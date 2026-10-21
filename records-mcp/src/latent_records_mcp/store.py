"""The Store, read-only: the reader's view of a `latent-records.store` v1 store.

Trimmed from latent-records' `store.py` (b7ca93c) to what a server needs: `Store.open`
on a stamped `store.json`, the spaces it describes, status, search, contains and get.
Nothing here plans or runs a build; that is the store package's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from upath import UPath

from .backends.base import Backend, Eq
from .backends.lance import STAMP as TABLE_STAMP
from .backends.lance import LanceBackend
from .errors import FormatError, StoreError
from .formats import STORE
from .schema import ITEM_TABLES
from .search import PRESENCE_TABLES, Hit, Mode, Presence, contains, search
from .store_json import read_store_json


def as_store_path(uri: str | Path) -> Path:
    text = str(uri)
    if "://" in text:
        return cast(Path, UPath(text))
    return Path(text).expanduser().resolve()


@dataclass(frozen=True)
class StoreStatus:
    title: str
    generated: str | None
    tables: dict[str, int] = field(default_factory=dict)
    spaces: dict[str, Any] = field(default_factory=dict)


def _check_table_stamps(backend: Backend, manifest: dict[str, Any]) -> None:
    """Every table a store lists carries this format's version, or it is refused."""
    names = [t["table"] for t in manifest["tables"].values()]
    names += [
        t["table"]
        for space in manifest["spaces"].values()
        for t in space["targets"].values()
    ]
    present = set(backend.table_names())
    for name in names:
        if name not in present:
            raise FormatError(f"store.json lists {name!r}, which is not in the store")
        stamp = (backend.schema(name).metadata or {}).get(TABLE_STAMP)
        if stamp != str(STORE.version).encode():
            raise FormatError(
                f"table {name!r} is stamped {stamp!r}; this package reads "
                f"{STORE.name} v{STORE.version}"
            )


class Store:
    def __init__(
        self, root: Path, backend: Backend, *, store_id: str = "", title: str = ""
    ):
        self.root = root
        self.backend = backend
        self.store_id = store_id
        self.title = title
        self._spaces: dict[str, Any] | None = None

    @classmethod
    def open(
        cls, uri: str | Path, *, storage_options: dict[str, str] | None = None
    ) -> Store:
        """A store someone built, read through its ``store.json`` alone."""
        root = as_store_path(uri)
        manifest = read_store_json(root)
        backend = LanceBackend(str(root), storage_options)
        _check_table_stamps(backend, manifest)
        return cls(
            root,
            backend,
            store_id=str(manifest.get("store_id") or ""),
            title=str(manifest.get("title") or ""),
        )

    @property
    def spaces(self) -> dict[str, Any]:
        """The spaces `store.json` describes, read once."""
        if self._spaces is None:
            self._spaces = dict(read_store_json(self.root).get("spaces") or {})
        return self._spaces

    def _counts(self) -> dict[str, int]:
        names = set(self.backend.table_names())
        return {n: self.backend.count(n) for n in ITEM_TABLES if n in names}

    def status(self) -> StoreStatus:
        """Row counts and space coverage. Never writes."""
        try:
            manifest = read_store_json(self.root)
        except FormatError:
            manifest = {}
        return StoreStatus(
            title=self.title,
            generated=manifest.get("generated"),
            tables=self._counts(),
            spaces=manifest.get("spaces") or {},
        )

    def search(
        self,
        query: str | bytes,
        *,
        target: str = "chunks",
        space: str | None = None,
        mode: Mode = "hybrid",
        limit: int = 10,
        record_id: str | None = None,
        expand_refs: bool = False,
    ) -> list[Hit]:
        """One ranking for one target. See `search.py` for what is never merged."""
        return search(
            self.backend,
            self.spaces,
            query,
            target=target,
            space=space,
            mode=mode,
            limit=limit,
            record_id=record_id,
            expand_refs=expand_refs,
        )

    def contains(
        self,
        phrase: str,
        *,
        tables: Sequence[str] = PRESENCE_TABLES,
        record_id: str | None = None,
    ) -> Presence:
        """Where the phrase occurs, exactly; absence is an answer."""
        return contains(self.backend, phrase, tables=tables, record_id=record_id)

    def get(self, item_id: str) -> dict[str, Any] | None:
        """One row by id: a record id, or a chunk, figure or table id."""
        kinds = {"chunk": "chunks", "figure": "figures", "table": "tables"}
        parts = item_id.split("::")
        if len(parts) == 3 and parts[1] in kinds:
            name, key = kinds[parts[1]], "id"
        elif len(parts) == 1:
            name, key = "records", "record_id"
        else:
            raise StoreError(f"{item_id!r} is not a record, chunk, figure or table id")
        if name not in self.backend.table_names():
            return None
        rows = self.backend.read(name, where=Eq(key, item_id)).to_pylist()
        return rows[0] if rows else None
