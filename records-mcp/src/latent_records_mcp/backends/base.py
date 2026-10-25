# Verbatim copy of latent-records at e9eb62a, below this header:
#   src/latent_records/backends/base.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The storage seam: what the store needs from a backend, and nothing it does not.

LanceDB is the only backend. The Protocol is here so a second one is a new file, not
a rewrite — it states the operations in this package's terms (tables of Arrow rows,
structured filters), never in one engine's query language.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pyarrow as pa

Scalar = str | int | bool


@dataclass(frozen=True)
class Eq:
    column: str
    value: Scalar


@dataclass(frozen=True)
class In:
    column: str
    values: tuple[Scalar, ...]


@dataclass(frozen=True)
class Contains:
    """A list column holding any of the values."""

    column: str
    values: tuple[str, ...]


Filter = Eq | In | Contains


class Backend(Protocol):
    def table_names(self) -> list[str]: ...

    def ensure_table(self, name: str, schema: pa.Schema) -> None:
        """Create the table empty, or refuse one whose columns differ."""

    def schema(self, name: str) -> pa.Schema: ...

    def upsert(
        self, name: str, rows: pa.Table, *, key: str, scope: Filter | None = None
    ) -> None:
        """Insert or replace rows by key, then delete rows in scope not among them."""

    def delete(self, name: str, where: Filter) -> None: ...

    def read(
        self,
        name: str,
        *,
        columns: Sequence[str] | None = None,
        where: Filter | None = None,
    ) -> pa.Table: ...

    def vector_search(
        self, name: str, vector: np.ndarray, *, limit: int, where: Filter | None = None
    ) -> pa.Table:
        """Nearest rows by cosine distance, with a ``_distance`` column."""

    def text_search(
        self,
        name: str,
        query: str,
        *,
        column: str,
        limit: int,
        where: Filter | None = None,
    ) -> pa.Table:
        """Full-text matches, best first, with a ``_score`` column."""

    def phrase_search(
        self, name: str, phrase: str, *, column: str, where: Filter | None = None
    ) -> pa.Table:
        """Candidate rows for an exact check: those the full-text index finds the
        phrase in (it stems and folds case), or every row in scope when the index
        cannot answer phrases exactly."""

    def fts_current(self, name: str, column: str) -> bool:
        """Whether the column's full-text index is the one phrase checks rely on."""

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
        """Build whichever of these indexes are missing; refresh the rest."""

    def count(self, name: str, where: Filter | None = None) -> int: ...

    def version(self, name: str) -> int: ...

    def drop_table(self, name: str) -> None: ...
