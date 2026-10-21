"""The one framework constant the reader needs.

Trimmed from latent-wiki's `core.py` (991549f): the frontmatter keys of the retired
two-file shape, which the reader skips a page for rather than misreading it.
"""

from __future__ import annotations

#: Keys of the page and config shape retired on 2026-10-12, which nothing reads. They
#: are named only so a store still carrying one is refused with the way out, rather
#: than with a bare unknown-key error.
RETIRED_PAGE_KEYS = frozenset(
    {
        "related",
        "extracted_links",
        "inferred_links",
        "raw_path",
        "origin_alt",
        "origin_method",
        "ingested_by",
        "log_ref",
    }
)
