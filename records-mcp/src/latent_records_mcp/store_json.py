# Verbatim copy of latent-records at e9eb62a, below this header:
#   src/latent_records/store_json.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""`store.json`: the stamp, and the map a reader needs without this package.

Written last in every run, so its presence means the tables beside it are complete. A
reader refuses a store whose stamp is missing or of a version it does not know.
Specified in `docs/contracts/store.md`, with a JSON Schema in `schemas/store.v1.json`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .deployment import NAME
from .errors import FormatError, StoreError
from .formats import STORE
from .schema import ITEM_TABLES

FILE = "store.json"
TABLE_NAME = re.compile(r"[a-z0-9_]+")


def read_store_json(root: Path) -> dict[str, Any]:
    path = root / FILE
    try:
        # Read, never `exists()` first: a filesystem's `exists()` may answer False
        # for missing credentials or a refused request, which would read as no store.
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FormatError(f"no {FILE} at {root}; not a latent-records store") from exc
    except Exception as exc:  # a remote read fails in whatever its client raises
        raise StoreError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FormatError(f"{path}: not JSON ({exc})") from exc
    if not isinstance(data, dict) or not STORE.matches(data):
        found = STORE.found(data) if isinstance(data, dict) else "not a mapping"
        raise FormatError(
            f"{path} is {found}; this package reads {STORE.name} v{STORE.version}"
        )
    store_id = data.get("store_id")
    if store_id is not None and not (
        isinstance(store_id, str) and NAME.match(store_id)
    ):
        raise FormatError(f"{path}: store_id {store_id!r} cannot name a key")
    for table in _table_names(data):
        if not isinstance(table, str) or not TABLE_NAME.fullmatch(table):
            raise FormatError(f"{path}: table {table!r} is not a table name in a store")
    return data


def _table_names(data: dict[str, Any]) -> list[Any]:
    tables = data.get("tables") or {}
    spaces = data.get("spaces") or {}
    if not isinstance(tables, dict) or not isinstance(spaces, dict):
        raise FormatError("store.json: tables and spaces must be mappings")
    names = [entry.get("table") for entry in tables.values()]
    names += [
        target.get("table")
        for space in spaces.values()
        for target in (space.get("targets") or {}).values()
    ]
    return names


def item_tables(counts: dict[str, int]) -> dict[str, Any]:
    """The item tables' entries, from the schema module and the current row counts."""
    out: dict[str, Any] = {}
    for name, table in ITEM_TABLES.items():
        entry: dict[str, Any] = {
            "table": name,
            "path": f"{name}.lance",
            "key": table.key,
            "rows": counts.get(name, 0),
        }
        if name != "records":
            entry["parent"] = "records"
            entry["text_column"] = "text"
            entry["fts"] = table.fts is not None
            entry["provenance"] = list(table.provenance)
        if table.media:
            entry["media_column"] = table.media
        out[name] = entry
    return out


def build_store_json(
    *, store_id: str, title: str, counts: dict[str, int], spaces: dict[str, Any]
) -> dict[str, Any]:
    return {
        **STORE.stamp(),
        "store_id": store_id,
        "title": title,
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "producer": {"package": "latent-records", "version": __version__},
        "backend": {"kind": "lancedb"},
        "tables": item_tables(counts),
        "spaces": spaces,
    }


def write_store_json(root: Path, data: dict[str, Any]) -> None:
    (root / FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
