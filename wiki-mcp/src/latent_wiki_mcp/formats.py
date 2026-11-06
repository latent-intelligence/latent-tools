# Verbatim copy of kb-wiki at 7d13913, below this header:
#   src/kb_wiki/formats.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The formats this package publishes — names and versions, in one place.

Each is specified in `docs/contracts/` with a JSON Schema in `schemas/`. A reader
refuses a version it does not know and a missing stamp; this
module is what the writers stamp and what this package's own readers check against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Format:
    name: str
    version: int
    also: str = ""
    """The format's other name, read but never written: through a rename, every
    reader accepts both before any writer stamps the new one."""

    def stamp(self) -> dict[str, Any]:
        return {"format": self.name, "format_version": self.version}

    def matches(self, data: dict[str, Any]) -> bool:
        names = (self.name, self.also) if self.also else (self.name,)
        return data.get("format") in names and data.get("format_version") == (
            self.version
        )

    def found(self, data: dict[str, Any]) -> str:
        """What a document claims to be, for an error message."""
        return f"{data.get('format')!r} v{data.get('format_version')!r}"


#: A wiki store: `manifest.json`, `pages/`, `wiki.yaml`. Stamped in the manifest.
STORE = Format("wiki.store", 1, also="latent-wiki.store")

#: A context store, stamped in its `context-store.yaml` descriptor.
CONTEXT = Format("wiki.context", 1, also="latent-wiki.context")

#: One imported document's record, `raw/<doc_id>/work.json`.
WORK = Format("wiki.work", 1, also="latent-wiki.work")

#: The import ledger, `imports.json`.
IMPORTS = Format("wiki.imports", 1, also="latent-wiki.imports")

#: The documents removed from a context store, `removed.json`: each one's sha256, when
#: and why. A document listed there is not imported again unless readmitted.
REMOVED = Format("wiki.removed", 1, also="latent-wiki.removed")

#: What `kb-wiki validate --json` and `kb-wiki lint --json` print: one store's
#: findings. The two commands share one engine and one report, so one format.
FINDINGS = Format("wiki.findings", 1, also="latent-wiki.findings")

#: What `kb-wiki verbatim --json` prints: the paraphrase guard's rows.
VERBATIM = Format("wiki.verbatim", 1, also="latent-wiki.verbatim")
