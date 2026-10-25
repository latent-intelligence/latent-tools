# Verbatim copy of latent-records at 5a75235, below this header:
#   src/latent_records/formats.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The formats this package publishes — names and versions, in one place.

Each is specified in `docs/contracts/` with a JSON Schema in `schemas/`. A reader
refuses a version it does not know and a missing stamp; this module is what the writer
stamps and what this package's own reader checks against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Format:
    name: str
    version: int

    def stamp(self) -> dict[str, Any]:
        return {"format": self.name, "format_version": self.version}

    def matches(self, data: dict[str, Any]) -> bool:
        return data.get("format") == self.name and data.get("format_version") == (
            self.version
        )

    def found(self, data: dict[str, Any]) -> str:
        """What a document claims to be, for an error message."""
        return f"{data.get('format')!r} v{data.get('format_version')!r}"


STORE = Format("latent-records.store", 1)
