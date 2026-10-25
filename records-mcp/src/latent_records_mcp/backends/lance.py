# Verbatim copy of latent-records at 5a75235, below this header:
#   src/latent_records/backends/lance.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The LanceDB backend. The only module that writes LanceDB SQL.

Filters arrive as `Eq` / `In` / `Contains` and become SQL here, through one literal
encoder, so a value can never become syntax. Column names are checked against the
table's schema before they reach a query.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import lancedb
import numpy as np
import pyarrow as pa
from lancedb.index import FTS, Bitmap, BTree, IvfPq, LabelList
from lancedb.query import PhraseQuery

from ..errors import StoreError
from .base import Contains, Eq, Filter, In, Scalar

VECTOR_INDEX_MIN_ROWS = 50_000

COLUMN = re.compile(r"[a-z_][a-z0-9_]*")

REFINE_FACTOR = 10

STAMP = b"latent-records.format_version"


def _literal(value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise StoreError(f"cannot filter on {value!r}")


def _column(name: str, schema: pa.Schema) -> str:
    if not COLUMN.fullmatch(name) or name not in schema.names:
        raise StoreError(f"no column {name!r} to filter on")
    return name


EXACT_FTS = FTS(with_position=True, remove_stop_words=False, max_token_length=None)


def _fts_name(column: str) -> str:
    """An index built as ``EXACT_FTS`` carries this name, so any other full-text
    index is recognised: phrase checks scan instead, and the next run replaces it."""
    return f"{column}_fts_exact"


def to_sql(where: Filter, schema: pa.Schema) -> str:
    column = _column(where.column, schema)
    if isinstance(where, Eq):
        return f"{column} = {_literal(where.value)}"
    if not where.values:
        return "false"
    values = ", ".join(_literal(v) for v in where.values)
    if isinstance(where, In):
        return f"{column} IN ({values})"
    if isinstance(where, Contains):
        return f"array_has_any({column}, [{values}])"
    raise StoreError(f"unknown filter {where!r}")


def _on_disk(uri: str) -> str | None:
    """The local path a store's URI names, or None for an object store. A ``file://``
    path is read literally, as LanceDB and fsspec read it: ``%20`` is not a space."""
    if "://" not in uri:
        return uri
    if not uri.startswith("file://"):
        return None
    parsed = urlparse(uri)
    if parsed.netloc:
        raise StoreError(f"{uri}: a file:// URI with a host is not a path on this host")
    return parsed.path


class LanceBackend:
    def __init__(self, uri: str, storage_options: dict[str, str] | None = None):
        self.uri = uri
        self._storage_options = storage_options
        self._connection: Any = None
        self._tables: dict[str, Any] = {}

    @property
    def _db(self) -> Any:
        """Connected on first use: connecting creates a local directory, and a plan
        must write nothing."""
        if self._connection is None:
            self._connection = lancedb.connect(
                self.uri, storage_options=self._storage_options
            )
        return self._connection

    def _absent(self) -> bool:
        """A store on this host's disk that is not there yet, where connecting would
        make its directory. An object store has no directory to make."""
        path = _on_disk(self.uri)
        return path is not None and not os.path.isdir(path)

    def _table(self, name: str) -> Any:
        if name not in self._tables:
            if name not in self.table_names():
                raise StoreError(f"no table {name!r} in {self.uri}")
            self._tables[name] = self._db.open_table(name)
        return self._tables[name]

    def table_names(self) -> list[str]:
        if self._absent():
            return []
        names: list[str] = []
        token: str | None = None
        while True:
            page = self._db.list_tables(page_token=token)
            names.extend(page.tables)
            token = page.page_token
            if not token:
                return sorted(names)

    def ensure_table(self, name: str, schema: pa.Schema) -> None:
        if name in self.table_names():
            existing = self._table(name).schema
            if (existing.metadata or {}).get(STAMP) != (schema.metadata or {}).get(
                STAMP
            ):
                raise StoreError(
                    f"table {name!r} is stamped "
                    f"{(existing.metadata or {}).get(STAMP)!r}; this package writes "
                    f"{(schema.metadata or {}).get(STAMP)!r}"
                )
            if not existing.remove_metadata().equals(schema.remove_metadata()):
                raise StoreError(
                    f"table {name!r} has columns {existing.names}, expected "
                    f"{schema.names}; the store was written by another version"
                )
            return
        self._tables[name] = self._db.create_table(name, schema=schema)

    def schema(self, name: str) -> pa.Schema:
        return self._table(name).schema

    def upsert(
        self, name: str, rows: pa.Table, *, key: str, scope: Filter | None = None
    ) -> None:
        table = self._table(name)
        if rows.num_rows == 0:
            if scope is not None:
                table.delete(to_sql(scope, table.schema))
            return
        merge = (
            table.merge_insert(key)
            .when_matched_update_all()
            .when_not_matched_insert_all()
        )
        if scope is not None:
            merge = merge.when_not_matched_by_source_delete(to_sql(scope, table.schema))
        merge.execute(rows.cast(table.schema))

    def delete(self, name: str, where: Filter) -> None:
        table = self._table(name)
        table.delete(to_sql(where, table.schema))

    def read(
        self,
        name: str,
        *,
        columns: Sequence[str] | None = None,
        where: Filter | None = None,
    ) -> pa.Table:
        table = self._table(name)
        query = table.search()
        if where is not None:
            query = query.where(to_sql(where, table.schema))
        if columns is not None:
            query = query.select([_column(c, table.schema) for c in columns])
        result: pa.Table = query.limit(None).to_arrow()
        return result

    def vector_search(
        self, name: str, vector: np.ndarray, *, limit: int, where: Filter | None = None
    ) -> pa.Table:
        table = self._table(name)
        query = (
            table.search(vector.astype(np.float32))
            .distance_type("cosine")
            .refine_factor(REFINE_FACTOR)
        )
        if where is not None:
            query = query.where(to_sql(where, table.schema))
        result: pa.Table = query.limit(limit).to_arrow()
        return result

    @staticmethod
    def _require_fts(name: str, table: Any, column: str) -> None:
        if not any(
            i.index_type == "FTS" and i.columns == [column]
            for i in table.list_indices()
        ):
            raise StoreError(f"{name}.{column} has no full-text index")

    def text_search(
        self,
        name: str,
        query: str,
        *,
        column: str,
        limit: int,
        where: Filter | None = None,
    ) -> pa.Table:
        table = self._table(name)
        column = _column(column, table.schema)
        if table.count_rows() == 0:
            return pa.table(
                {
                    **{f.name: pa.array([], f.type) for f in table.schema},
                    "_score": pa.array([], pa.float32()),
                }
            )
        self._require_fts(name, table, column)
        search = table.search(query, query_type="fts", fts_columns=column)
        if where is not None:
            search = search.where(to_sql(where, table.schema))
        result: pa.Table = search.limit(limit).to_arrow()
        return result

    def phrase_search(
        self, name: str, phrase: str, *, column: str, where: Filter | None = None
    ) -> pa.Table:
        table = self._table(name)
        column = _column(column, table.schema)
        rows = table.count_rows()
        if rows == 0 or not phrase.split():
            return table.schema.empty_table()
        if self.fts_current(name, column):
            search = table.search(
                PhraseQuery(phrase, column, slop=0, document_granularity=None)
            )
        else:
            search = table.search()
        if where is not None:
            search = search.where(to_sql(where, table.schema))
        result: pa.Table = search.limit(rows).to_arrow()
        return result

    def fts_current(self, name: str, column: str) -> bool:
        return any(
            i.index_type == "FTS" and i.name == _fts_name(column)
            for i in self._table(name).list_indices()
        )

    def ensure_indexes(
        self,
        name: str,
        *,
        btree: Sequence[str] = (),
        bitmap: Sequence[str] = (),
        label_list: Sequence[str] = (),
        fts: str | None = None,
        vector: str | None = None,
    ) -> None:
        table = self._table(name)
        rows = table.count_rows()
        if rows == 0:
            return
        indices = table.list_indices()
        if fts:
            for old in indices:
                stale = old.index_type == "FTS" and old.name != _fts_name(fts)
                if stale and old.columns == [fts]:
                    table.drop_index(old.name)
            indices = table.list_indices()
        have = {i.columns[0] for i in indices}
        wanted: list[tuple[str, Any]] = [
            *((c, BTree()) for c in btree),
            *((c, Bitmap()) for c in bitmap),
            *((c, LabelList()) for c in label_list),
        ]
        if fts:
            wanted.append((fts, EXACT_FTS))
        if vector and rows >= VECTOR_INDEX_MIN_ROWS:
            wanted.append((vector, IvfPq(distance_type="cosine")))
        for column, config in wanted:
            if column not in have:
                table.create_index(
                    _column(column, table.schema),
                    config=config,
                    name=_fts_name(column) if isinstance(config, FTS) else None,
                )
        table.optimize()

    def count(self, name: str, where: Filter | None = None) -> int:
        table = self._table(name)
        if where is None:
            return int(table.count_rows())
        return int(table.count_rows(to_sql(where, table.schema)))

    def version(self, name: str) -> int:
        return int(self._table(name).version)

    def drop_table(self, name: str) -> None:
        self._tables.pop(name, None)
        if name in self.table_names():
            self._db.drop_table(name)
