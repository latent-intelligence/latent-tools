# Verbatim copy of latent-records at e9eb62a, below this header:
#   src/latent_records/schema.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The Arrow schemas of the store's tables — part of the published format.

Every table carries the format stamp in its schema metadata, so a Lance table copied
out of its store still says what it is. Required columns are listed in
`docs/contracts/store.md`; adding an optional column is not a breaking change.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

from .formats import STORE

TIMESTAMP = pa.timestamp("us", tz="UTC")
BBOX = pa.list_(pa.float32(), 4)


def _stamp(extra: dict[str, str] | None = None) -> dict[bytes, bytes]:
    meta = {
        "latent-records.format": STORE.name,
        "latent-records.format_version": str(STORE.version),
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


@dataclass(frozen=True)
class ItemTable:
    """An item table: its schema, key, and the indexes a run keeps on it."""

    name: str
    schema: pa.Schema
    key: str
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
            bitmap=("record_id",),
            label_list=("refs",),
            fts="text",
            provenance=("record_id", "chapter", "section", "page_start", "page_end"),
        ),
        ItemTable(
            "figures",
            FIGURES,
            "id",
            bitmap=("record_id",),
            fts="text",
            media="media_path",
            provenance=("record_id", "key", "page", "bbox"),
        ),
        ItemTable(
            "tables",
            TABLES,
            "id",
            bitmap=("record_id",),
            fts="text",
            provenance=("record_id", "key", "page", "bbox"),
        ),
    )
}

TARGET_SOURCES: dict[str, tuple[str, str]] = {
    "chunks": ("chunks", "text"),
    "tables": ("tables", "text"),
    "figure_captions": ("figures", "text"),
    "figure_images": ("figures", "media_path"),
}


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
