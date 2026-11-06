# Verbatim copy of kb-store at fe6e28c, below this header:
#   src/kb_store/schema.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The Arrow schemas of the store's tables — part of the published format.

Every table carries the format stamp in its schema metadata, so a Lance table copied
out of its store still says what it is. Required columns are listed in
`docs/contracts/store.md`; adding an optional column is not a breaking change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pyarrow as pa

from .formats import STORE

TIMESTAMP = pa.timestamp("us", tz="UTC")
BBOX = pa.list_(pa.float32(), 4)


def _stamp(extra: dict[str, str] | None = None) -> dict[bytes, bytes]:
    meta = {
        "records.format": STORE.name,
        "records.format_version": str(STORE.version),
        **(extra or {}),
    }
    return {k.encode(): v.encode() for k, v in meta.items()}


RECORDS = pa.schema(
    [
        pa.field("record_id", pa.string(), nullable=False),
        pa.field("input", pa.string(), nullable=False),
        pa.field("title", pa.string()),
        pa.field("source_sha256", pa.string(), nullable=False),
        pa.field("pages", pa.int32()),
        pa.field(
            "chapters",
            pa.list_(
                pa.struct(
                    [
                        pa.field("file", pa.string()),
                        pa.field("page_start", pa.int32()),
                        pa.field("page_end", pa.int32()),
                    ]
                )
            ),
        ),
        pa.field("outline_json", pa.large_string()),
        pa.field("extraction_json", pa.large_string()),
        pa.field("ingest_digest", pa.string(), nullable=False),
        pa.field("ingested_at", TIMESTAMP, nullable=False),
    ],
    metadata=_stamp(),
)

CHUNKS = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("record_id", pa.string(), nullable=False),
        pa.field("seq", pa.int32(), nullable=False),
        pa.field("chapter", pa.string(), nullable=False),
        pa.field("section", pa.list_(pa.string())),
        pa.field("text", pa.large_string(), nullable=False),
        pa.field("tokens", pa.int32()),
        pa.field("char_start", pa.int32()),
        pa.field("char_end", pa.int32()),
        pa.field("page_start", pa.int32()),
        pa.field("page_end", pa.int32()),
        pa.field("refs", pa.list_(pa.string())),
        pa.field("content_sha256", pa.string(), nullable=False),
    ],
    metadata=_stamp(),
)

FIGURES = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("record_id", pa.string(), nullable=False),
        pa.field("key", pa.string(), nullable=False),
        pa.field("page", pa.int32()),
        pa.field("bbox", BBOX),
        pa.field("number", pa.string()),
        pa.field("caption", pa.string()),
        pa.field("caption_kind", pa.string()),
        pa.field("text", pa.large_string(), nullable=False),
        pa.field("media_path", pa.string()),
        pa.field("media_sha256", pa.string()),
        pa.field("media_bytes", pa.int64()),
        pa.field("content_sha256", pa.string(), nullable=False),
    ],
    metadata=_stamp(),
)

TABLES = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("record_id", pa.string(), nullable=False),
        pa.field("key", pa.string(), nullable=False),
        pa.field("page", pa.int32()),
        pa.field("bbox", BBOX),
        pa.field("number", pa.string()),
        pa.field("caption", pa.string()),
        pa.field("caption_kind", pa.string()),
        pa.field("markdown", pa.large_string()),
        pa.field("grid_json", pa.large_string()),
        pa.field("ruled_agrees", pa.bool_()),
        pa.field("text", pa.large_string(), nullable=False),
        pa.field("content_sha256", pa.string(), nullable=False),
    ],
    metadata=_stamp(),
)


SETTINGS = pa.schema(
    [
        pa.field("key", pa.string(), nullable=False),
        pa.field("value", pa.large_string(), nullable=False),
    ],
    metadata=_stamp(),
)
"""The store's own settings, one row keyed ``store``: what it was built with."""


EQUATIONS = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("record_id", pa.string(), nullable=False),
        pa.field("key", pa.string(), nullable=False),
        pa.field("page", pa.int32()),
        pa.field("bbox", BBOX),
        pa.field("latex", pa.large_string(), nullable=False),
        pa.field("display", pa.bool_()),
        pa.field("text", pa.large_string(), nullable=False),
        pa.field("content_sha256", pa.string(), nullable=False),
    ],
    metadata=_stamp(),
)


@dataclass(frozen=True)
class ItemTable:
    """An item table: its schema, key, and the indexes a run keeps on it."""

    name: str
    schema: pa.Schema
    key: str
    infix: str | None = None
    """The word between a record id and a part's key in a part id: ``chunk``."""
    bitmap: tuple[str, ...] = ()
    label_list: tuple[str, ...] = ()
    fts: str | None = None
    media: str | None = None
    provenance: tuple[str, ...] = ()


ITEM_TABLES: dict[str, ItemTable] = {
    t.name: t
    for t in (
        ItemTable("records", RECORDS, "record_id"),
        ItemTable(
            "chunks",
            CHUNKS,
            "id",
            infix="chunk",
            bitmap=("record_id",),
            label_list=("refs",),
            fts="text",
            provenance=("record_id", "chapter", "section", "page_start", "page_end"),
        ),
        ItemTable(
            "figures",
            FIGURES,
            "id",
            infix="figure",
            bitmap=("record_id",),
            fts="text",
            media="media_path",
            provenance=("record_id", "key", "page", "bbox"),
        ),
        ItemTable(
            "tables",
            TABLES,
            "id",
            infix="table",
            bitmap=("record_id",),
            fts="text",
            provenance=("record_id", "key", "page", "bbox"),
        ),
        ItemTable(
            "equations",
            EQUATIONS,
            "id",
            infix="equation",
            bitmap=("record_id",),
            fts="text",
            provenance=("record_id", "key", "page", "bbox"),
        ),
    )
}


@dataclass(frozen=True)
class Target:
    """What a space can embed: one column of one table, in one modality."""

    name: str
    modality: Literal["text", "image"]
    table: str
    column: str
    hash_column: str
    artifact: str
    """The input artifact the target's rows come from."""


TARGETS: dict[str, Target] = {
    t.name: t
    for t in (
        Target("chunks", "text", "chunks", "text", "content_sha256", "text"),
        Target("tables", "text", "tables", "text", "content_sha256", "tables"),
        Target(
            "figure_captions", "text", "figures", "text", "content_sha256", "figures"
        ),
        Target(
            "figure_images", "image", "figures", "media_path", "media_sha256", "figures"
        ),
        Target("equations", "text", "equations", "text", "content_sha256", "equations"),
    )
}


def table_of(item_id: str) -> str | None:
    """The table an id names: ``records`` for a record id, a part's table for a part
    id (``<record>::<infix>::<key>``), None for anything else."""
    pieces = item_id.split("::")
    if len(pieces) == 1:
        return "records"
    if len(pieces) == 3:
        for name, table in ITEM_TABLES.items():
            if table.infix == pieces[1]:
                return name
    return None


def part_tables() -> tuple[str, ...]:
    """Every table of a record's parts: all but ``records``, in declared order."""
    return tuple(n for n, t in ITEM_TABLES.items() if t.infix)


def presence_tables() -> tuple[str, ...]:
    """The part tables an exact-phrase search reads: those with full text."""
    return tuple(n for n in part_tables() if ITEM_TABLES[n].fts)


def vector_schema(dims: int, meta: dict[str, str]) -> pa.Schema:
    """One (target, space) vector table. ``meta`` carries the space's recipe."""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("record_id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dims), nullable=False),
            pa.field("content_sha256", pa.string(), nullable=False),
            pa.field("windows", pa.int16(), nullable=False),
            pa.field("truncated", pa.bool_(), nullable=False),
        ],
        metadata=_stamp(meta),
    )
