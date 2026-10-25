# Verbatim copy of latent-records at e9eb62a, below this header:
#   src/latent_records/search.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Search one target: by vector, by full text, or both fused by rank.

**One ranking per target and space.** Distances from two tables — two spaces, or text
against images in one CLIP space — are not on one scale, so nothing here merges them.
Hybrid search fuses a vector ranking and a full-text ranking over the *same* rows by
reciprocal rank (RRF), which uses positions only, never scores.

Everything a search needs comes from `store.json`: which spaces cover a target, the
recipe to embed the query with, and the tables to read. A store built elsewhere is
searched the same way as one built here.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from .backends.base import Backend, Contains, Eq, Filter, In
from .deployment import TARGETS
from .embedders.base import ImageEmbedder, TextEmbedder, load_embedder
from .embedders.windowed import embed_windowed
from .errors import StoreError
from .schema import TARGET_SOURCES

Mode = Literal["vector", "fts", "hybrid"]
MODES: tuple[str, ...] = ("vector", "fts", "hybrid")

RRF_K = 60

HYBRID_DEPTH = 4


@dataclass(frozen=True)
class Hit:
    """One result, with the provenance to cite it."""

    id: str
    target: str
    space: str | None
    mode: str
    score: float
    score_kind: str
    record_id: str
    title: str | None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    chapter: str | None = None
    section: tuple[str, ...] = ()
    media_path: str | None = None
    refs: tuple[str, ...] = ()
    referenced_by: tuple[str, ...] = ()
    vector_rank: int | None = None
    fts_rank: int | None = None

    @property
    def page_scope(self) -> str:
        """``chapter`` when the pages are the chunk's chapter range, else ``exact``."""
        return "chapter" if self.target == "chunks" else "exact"


PRESENCE_TABLES: tuple[str, ...] = ("chunks", "figures", "tables")

PRESENCE_IDS = 50


@dataclass(frozen=True)
class Presence:
    """Where a phrase occurs, exactly. ``total == 0`` means it does not."""

    phrase: str
    total: int
    by_record: dict[str, int] = field(default_factory=dict)
    ids: tuple[str, ...] = ()
    truncated: bool = False


QUOTES = "\"'\u2018\u2019\u201c\u201d`"

DASHES = "\\-\u2010-\u2015\u2212"

GAP = rf"(?:[ \t{DASHES}]++\n?+[ \t{DASHES}]*+|\n[ \t{DASHES}]*+)"


def exact_text(text: str) -> str:
    """Text as an exact check reads it: NFKC (ligatures, widths, composed accents),
    soft hyphens removed."""
    return unicodedata.normalize("NFKC", text).replace("\u00ad", "")


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """The phrase's words in order, whole, any case, joined by spaces, dashes or one
    line break. A word ending in a digit does not match inside a longer number."""
    split = re.split(rf"[\s{DASHES}]+", exact_text(phrase))
    words = [w.strip(QUOTES) for w in split if w.strip(QUOTES)]
    if not words:
        raise StoreError("give a phrase to look for")
    body = GAP.join(re.escape(w) for w in words)
    return re.compile(rf"(?<!\w)(?<!\d\.){body}(?!\w)(?!\.\d)", re.IGNORECASE)


def _confirmed(rows: Any, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    return [
        (r["record_id"], r["id"])
        for r in rows.to_pylist()
        if pattern.search(exact_text(r["text"] or ""))
    ]


def contains(
    backend: Backend,
    phrase: str,
    *,
    tables: Sequence[str] = PRESENCE_TABLES,
    record_id: str | None = None,
) -> Presence:
    """Rows whose text holds the phrase. The full-text index proposes candidates,
    stemmed and folded; only rows whose text matches the phrase exactly count. The
    index reads raw text, so it can miss a spelling that normalises to the phrase
    (a ligature, a soft hyphen): absence is therefore confirmed by a scan before it
    is reported."""
    pattern = phrase_pattern(phrase)
    unknown = sorted(set(tables) - set(PRESENCE_TABLES))
    if unknown:
        raise StoreError(f"{unknown} are not among {list(PRESENCE_TABLES)}")
    names = [n for n in tables if n in set(backend.table_names())]
    where = _scope(record_id)
    matched = [
        hit
        for name in names
        for hit in _confirmed(
            backend.phrase_search(name, phrase, column="text", where=where), pattern
        )
    ]
    if not matched:
        matched = [
            hit
            for name in names
            if backend.fts_current(name, "text")
            for hit in _confirmed(
                backend.read(name, columns=["id", "record_id", "text"], where=where),
                pattern,
            )
        ]
    by_record: dict[str, int] = {}
    for rid, _ in matched:
        by_record[rid] = by_record.get(rid, 0) + 1
    ids = sorted(i for _, i in matched)
    return Presence(
        phrase=phrase,
        total=len(matched),
        by_record=dict(sorted(by_record.items())),
        ids=tuple(ids[:PRESENCE_IDS]),
        truncated=len(ids) > PRESENCE_IDS,
    )


def choose_space(spaces: dict[str, Any], target: str, space: str | None) -> str:
    covering = sorted(
        n for n, s in spaces.items() if target in (s.get("targets") or {})
    )
    if space is not None:
        if space not in covering:
            raise StoreError(
                f"space {space!r} has no vectors for {target}; "
                f"spaces that do: {covering or 'none'}"
            )
        return space
    if len(covering) == 1:
        return covering[0]
    if not covering:
        raise StoreError(
            f"no space has vectors for {target}; search it with mode `fts`"
        )
    raise StoreError(
        f"several spaces cover {target}: {covering}; name one with `space`"
    )


def embed_query(entry: dict[str, Any], query: str | bytes) -> np.ndarray:
    """A query vector made the way the space made its rows."""
    embedder = load_embedder(entry)
    if isinstance(query, bytes):
        if "image" not in entry.get("query_modalities", []) or not isinstance(
            embedder, ImageEmbedder
        ):
            raise StoreError("this space cannot embed an image query")
        return np.asarray(embedder.embed_images([query])[0])
    if not isinstance(embedder, TextEmbedder):
        raise StoreError("this space cannot embed a text query")
    window = entry.get("window")
    if window:
        vectors, _, _ = embed_windowed(
            embedder,
            [query],
            tokens=int(window["tokens"]),
            stride=int(window["stride"]),
            query=True,
        )
        return np.asarray(vectors[0])
    return np.asarray(embedder.embed_texts([query], query=True)[0])


def _scope(record_id: str | None) -> Filter | None:
    return Eq("record_id", record_id) if record_id else None


def _vector(
    backend: Backend,
    entry: dict[str, Any],
    target: str,
    query: str | bytes,
    limit: int,
    record_id: str | None,
) -> list[tuple[str, float]]:
    table = entry["targets"][target]["table"]
    vector = embed_query(entry, query)

    def fetch(depth: int) -> list[tuple[str, float]]:
        rows = backend.vector_search(
            table, vector, limit=depth, where=_scope(record_id)
        ).to_pylist()
        return [(r["id"], 1.0 - float(r["_distance"])) for r in rows]

    return _ranked(fetch, limit)


def _fts(
    backend: Backend, source: str, query: str, limit: int, record_id: str | None
) -> list[tuple[str, float]]:
    def fetch(depth: int) -> list[tuple[str, float]]:
        rows = backend.text_search(
            source, query, column="text", limit=depth, where=_scope(record_id)
        ).to_pylist()
        return [(r["id"], float(r["_score"])) for r in rows]

    return _ranked(fetch, limit)


def _ranked(
    fetch: Callable[[int], list[tuple[str, float]]], limit: int
) -> list[tuple[str, float]]:
    """Best first, ties by id. The backend orders and cuts tied rows as it likes, so
    rows are fetched past `limit` until the tie at the cut is whole: one extra row as
    a rule, the whole tied group when many rows tie there (repeated boilerplate)."""
    depth = limit
    while True:
        rows = sorted(fetch(depth + 1), key=lambda kv: (-kv[1], kv[0]))
        if len(rows) <= depth or rows[depth][1] != rows[limit - 1][1]:
            return rows[:limit]
        depth *= 2


def _rrf(*rankings: list[tuple[str, float]]) -> list[tuple[str, float]]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, (item, _) in enumerate(ranking, 1):
            fused[item] = fused.get(item, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))


def search(
    backend: Backend,
    spaces: dict[str, Any],
    query: str | bytes,
    *,
    target: str = "chunks",
    space: str | None = None,
    mode: Mode = "hybrid",
    limit: int = 10,
    record_id: str | None = None,
    expand_refs: bool = False,
) -> list[Hit]:
    if target not in TARGETS:
        raise StoreError(f"target {target!r} is not one of {sorted(TARGETS)}")
    if mode not in MODES:
        raise StoreError(f"mode {mode!r} is not one of {list(MODES)}")
    if limit < 1:
        raise StoreError("limit must be at least 1")
    image_target = TARGETS[target][0] == "image"
    if mode != "vector" and (image_target or isinstance(query, bytes)):
        raise StoreError(
            f"{mode} search needs text on both sides; use mode `vector` for images"
        )
    source = TARGET_SOURCES[target][0]

    chosen: str | None = None
    vector: list[tuple[str, float]] = []
    lexical: list[tuple[str, float]] = []
    if mode == "fts":
        if space is not None:
            raise StoreError("full-text search uses no space; drop `space`")
        ranking = lexical = _fts(backend, source, str(query), limit, record_id)
        kind = "bm25"
    else:
        chosen = choose_space(spaces, target, space)
        entry = spaces[chosen]
        depth = limit * HYBRID_DEPTH if mode == "hybrid" else limit
        ranking = vector = _vector(backend, entry, target, query, depth, record_id)
        kind = "cosine_similarity"
        if mode == "hybrid":
            lexical = _fts(backend, source, str(query), depth, record_id)
            ranking = _rrf(vector, lexical)
            kind = "rrf"
    ranks = (_positions(vector), _positions(lexical))
    hits = _hits(
        backend, ranking, source, target, chosen, mode, kind, expand_refs, ranks
    )
    return hits[:limit]


def _positions(ranking: list[tuple[str, float]]) -> dict[str, int]:
    return {item: rank for rank, (item, _) in enumerate(ranking, 1)}


def _hits(
    backend: Backend,
    ranking: Sequence[tuple[str, float]],
    source: str,
    target: str,
    space: str | None,
    mode: str,
    kind: str,
    expand_refs: bool,
    ranks: tuple[dict[str, int], dict[str, int]],
) -> list[Hit]:
    if not ranking:
        return []
    ids = tuple(i for i, _ in ranking)
    rows = {r["id"]: r for r in backend.read(source, where=In("id", ids)).to_pylist()}
    record_ids = tuple(sorted({r["record_id"] for r in rows.values()}))
    titles = {
        r["record_id"]: r["title"]
        for r in backend.read(
            "records", columns=["record_id", "title"], where=In("record_id", record_ids)
        ).to_pylist()
    }
    cited: dict[str, list[str]] = {}
    if expand_refs and source != "chunks":
        for chunk in backend.read(
            "chunks", columns=["id", "refs"], where=Contains("refs", ids)
        ).to_pylist():
            for ref in chunk["refs"] or []:
                cited.setdefault(ref, []).append(chunk["id"])

    hits = []
    for item, score in ranking:
        row = rows.get(item)
        if row is None:
            continue
        page = row.get("page")
        hits.append(
            Hit(
                id=item,
                target=target,
                space=space,
                mode=mode,
                score=score,
                score_kind=kind,
                record_id=row["record_id"],
                title=titles.get(row["record_id"]),
                text=row["text"],
                page_start=row.get("page_start", page),
                page_end=row.get("page_end", page),
                chapter=row.get("chapter"),
                section=tuple(row.get("section") or ()),
                media_path=row.get("media_path"),
                refs=tuple(row.get("refs") or ()) if expand_refs else (),
                referenced_by=tuple(sorted(cited.get(item, ()))),
                vector_rank=ranks[0].get(item),
                fts_rank=ranks[1].get(item),
            )
        )
    return hits
