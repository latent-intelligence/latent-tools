# Verbatim copy of kb-store at fe6e28c, below this header:
#   src/kb_store/locations.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Where a store or an input lives: a local folder or a URI, always a `UPath`.

Every location is made here. `Path("s3://b/k")` collapses to a local, relative
`s3:/b/k`; a `UPath` keeps the URI, and is a real `Path` when it is local, so one type
covers both, and a type checker refuses a call that works only on local disk
(`os.walk`, `open(str(p))`) instead of letting it fail in a bucket.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from upath import UPath


def location(
    value: str | Path | UPath, storage_options: Mapping[str, str] | None = None
) -> UPath:
    """A local path, made absolute with ``~`` expanded, or a URI opened with
    `storage_options`. Raises ValueError for a scheme fsspec does not know."""
    if isinstance(value, UPath):
        return value
    text = str(value)
    if "://" in text:
        return UPath(text, **(storage_options or {}))
    return UPath(Path(text).expanduser().resolve())
