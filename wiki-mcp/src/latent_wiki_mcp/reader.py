# Verbatim copy of latent-wiki at 991549f, below this header:
#   src/latent_wiki/reader.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""A published `latent-wiki` store, read for agents — local or `s3://`.

A published store is a versioned artifact::

    manifest.json    manifest_version, contract_version, store_id, pack, title,
                     description, generated, page_count,
                     pages: [{key, file, lead, frontmatter}]      one GET
    pages/<file>.md  bodies, fetched only when something asks     on demand

This reader reads only that, with `json` and `fsspec`. **An unpublished store is
refused**, not loaded through `Store`: publishing is what makes a store readable, so an
agent sees exactly what any other reader of the format would.

**The contract is a published format**, `latent-wiki.store` v1
(`docs/formats/wiki-store.md`), stamped in the manifest as `format`/`format_version`. A
store without the stamp, or with a version this build does not know, is refused loudly
rather than misread quietly. The paired context store is `latent-wiki.context` v1,
stamped in its `context-store.yaml`.

**This is the one implementation of the wiki tools** (`docs/formats/wiki-tools.md`);
`latent-wiki serve` exposes them over MCP. Two measured ranking decisions hold:

- **Filter before rank.** Superseded and deprecated pages are excluded by predicate, not
  down-weighted. A retired page and its replacement are near-identical by construction,
  so ranking cannot separate them — and the deprecated one sometimes wins.
- **Rank over the gist**, not the whole body, unless asked: key, title, aliases, tags,
  intent and lead, weighted in that order, through SQLite FTS5 (`_fts.py`).

**Disclosure is a ladder, and every rung is bounded**: overview → index or search →
get (a section, or a capped page) → neighbors and trace → evidence (the context-store
entries a page cites) → raw (the source text, paged). Each answer says where it came
from and what it left out.

**Field vocabulary is the default pattern** — `type`, `title`, `why`, `status`, `tags`,
`links`, `sources`, `as_of` — which is what the packs we ship write. A store with a
different vocabulary is a sidecar mapping away; the seam is `_Page`, which is the only
place frontmatter keys are named.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from upath import UPath

from . import _fts, formats, manifest, views
from .core import RETIRED_PAGE_KEYS
from .errors import StoreError

MANIFEST_NAME = manifest.MANIFEST_NAME

#: The manifest layout this build reads. A store declaring another version is refused —
#: silently misreading a newer artifact is the failure this exists to prevent.
MANIFEST_VERSION = manifest.MANIFEST_VERSION

#: The published formats this build reads. Anything else, or nothing, is refused.
STORE_FORMAT = (formats.STORE.name, formats.STORE.version)
CONTEXT_FORMAT = (formats.CONTEXT.name, formats.CONTEXT.version)

#: Caps on what one answer may carry. Each answer that hits one says so.
GET_MAX_CHARS = 8_000
RAW_MAX_CHARS = 12_000
INDEX_PAGE = 50
INDEX_MAX = 200
SEARCH_MAX = 50
NEIGHBORS_MAX = 40
EVIDENCE_MAX = 20
SMALL_TYPE = 12

#: A context-store entry key: one path segment, never a path.
_ENTRY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Lifecycle states search hides unless asked. A predicate, never a ranking penalty.
RETIRED = ("superseded", "deprecated")

#: Where bodies live, relative to the store root.
PAGES_DIR = manifest.PAGES_DIR

_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)

#: Why an unpublished store could not be read, by what is actually at the root. The
#: three cases have different fixes, and one message for all of them sent a deployment
#: with no local copy — the normal case for a store hosted on S3 — chasing a publish
#: step for a directory that was never there.
NO_STORE = (
    "{root} does not exist, so this machine has no copy of the store. Point at a "
    "published store — a path or s3:// URL containing {manifest}."
)
EMPTY_STORE = (
    "{root} is empty, so this machine has no copy of the store. If it is a synced "
    "folder the data has not arrived; otherwise point at a published store — a path "
    "or s3:// URL containing {manifest}."
)
#: A remote read the provider refused. S3 answers 403 for a wrong key, a policy without
#: `s3:GetObject`/`s3:ListBucket` on the prefix, and — without list permission — a key
#: that simply is not there, so the message names all three rather than guessing.
REFUSED = (
    "{path}: {error} — the credentials in the environment were refused. Check the key, "
    "the policy on this prefix, and that the bucket and path are the ones intended."
)
UNPUBLISHED_STORE = (
    "{root} has no {manifest}, so it is an unpublished store. Publish it "
    "(`latent-wiki publish <deployment>`), or point at a published copy."
)


def _as_path(target: str) -> Path:
    """A local path or any fsspec URI. `UPath` implements everything used here."""
    if "://" in target:
        return cast(Path, UPath(target))
    return Path(target).expanduser().resolve()


@dataclass
class _Page:
    """One page at the scan and gist rungs. **The only place frontmatter is named.**

    A store with a different field vocabulary is a mapping over this class, which is why
    every accessor below reads `raw` rather than being handed a value.
    """

    key: str
    raw: dict[str, Any]
    #: Where the body lives.
    path: Path
    lead: str = ""

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or self.key)

    @property
    def type(self) -> str:
        return str(self.raw.get("type") or "")

    @property
    def why(self) -> str:
        return str(self.raw.get("why") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "")

    @property
    def tags(self) -> list[str]:
        return [str(t) for t in (self.raw.get("tags") or [])]

    @property
    def links(self) -> list[dict[str, str]]:
        """`links:` entries, as `{to, rel, derivation}`. A bare string is a link too."""
        out: list[dict[str, str]] = []
        for entry in self.raw.get("links") or []:
            if isinstance(entry, str):
                out.append({"to": entry, "rel": "", "derivation": "inferred"})
            elif isinstance(entry, dict) and entry.get("to"):
                out.append(
                    {
                        "to": str(entry["to"]),
                        "rel": str(entry.get("rel") or ""),
                        "derivation": str(entry.get("derivation") or ""),
                    }
                )
        return out

    @property
    def sources(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self.raw.get("sources") or []:
            if isinstance(entry, str):
                entries.append({"resource": entry})
            elif isinstance(entry, dict):
                entries.append(dict(entry))
        return entries

    @property
    def aliases(self) -> list[str]:
        return [str(a) for a in (self.raw.get("aliases") or [])]

    @property
    def source_docs(self) -> list[str]:
        """Existence-trigger evidence: page keys or `<root>:<key>` references."""
        return [str(d) for d in (self.raw.get("source_docs") or [])]

    @property
    def evidence(self) -> list[str]:
        """The `source_docs` entries that name another store's entry, not a page."""
        return [d for d in self.source_docs if ":" in d and "://" not in d]

    @property
    def parent(self) -> str:
        return str(self.raw.get("part_of") or "")

    @property
    def draft(self) -> bool:
        return self.status == "draft"

    @property
    def inferred(self) -> bool:
        """A page the store marks as drawn by its author, not stated by a source."""
        return str(self.raw.get("derivation") or "") == "inferred"

    def resources(self) -> list[str]:
        """What a page derives from: declared sources, then cited evidence."""
        declared = [str(s["resource"]) for s in self.sources if s.get("resource")]
        return declared + [e for e in self.evidence if e not in declared]

    def flags(self) -> str:
        marks = [
            m for m, on in (("draft", self.draft), ("inferred", self.inferred)) if on
        ]
        return f" · {' · '.join(marks)}" if marks else ""

    def text(self) -> str:
        """The page as written, frontmatter and all. This is the fetch a manifest
        exists to defer."""
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError) as exc:
            raise StoreError(f"{self.key}: {exc}") from exc

    def body(self) -> str:
        """The page body, frontmatter stripped."""
        return _FRONTMATTER.sub("", self.text())


@dataclass
class Hit:
    """One search result: a ref, its gist, and what it matched on."""

    ref: str
    title: str
    kind: str
    lead: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class WikiReader:
    """One published `latent-wiki` store. One reader, one store — keys stay unambiguous.

    Answers name a page `<store_id>:<key>`, the same form a link into a peer store
    takes, so a page name never reads as an ordinary word. The reader knows no client:
    how a client labels this store is its own business, and a ref that carried that
    label would tie the answer's usefulness to the client getting the label right.
    Arguments take a key bare or with this store's id in front. Refusals and footers
    name it bare, so a page that was not returned is never named as one.
    """

    def __init__(
        self,
        target: str,
        *,
        context: str | None = None,
        index_body: bool = False,
    ) -> None:
        self.target = str(target)
        self.root = _as_path(self.target)
        #: Until the manifest is read, errors name the store by its last path segment.
        self.id = self.target.rstrip("/").rsplit("/", 1)[-1]
        #: The material pages derive from, for `wiki_raw`. A wiki and its context are a
        #: pair: the wiki carries the summaries, this carries what they summarise.
        self.context: Path | None = _as_path(str(context)) if context else None
        #: Index page bodies as well as gists. Off by default: over `s3://` it costs one
        #: GET per page, and the measured benefit is in the gist.
        self.index_body = index_body

        self.manifest: dict[str, Any] = {}
        self.pages: dict[str, _Page] = {}
        self._context_checked = False
        self._load()
        self.id = str(self.manifest.get("store_id") or "") or self.id
        self._index: _fts.Index | None = self._build_index()

    def _build_index(self) -> _fts.Index | None:
        """The FTS5 index over this store, or None where sqlite lacks FTS5."""
        if not _fts.available():
            return None
        rows = [
            _fts.Row(
                key,
                {
                    "title": page.title,
                    "aliases": " ".join(page.aliases),
                    "tags": " ".join(page.tags).replace("-", " "),
                    "why": page.why,
                    "lead": page.lead,
                    "body": page.body() if self.index_body else "",
                },
            )
            for key, page in self.pages.items()
        ]
        return _fts.Index(rows)

    # -- loading --------------------------------------------------------------

    def _load(self) -> None:
        data = self._read_manifest()
        if data is None:
            raise StoreError(
                self._unreadable().format(root=self.root, manifest=MANIFEST_NAME)
            )
        self.manifest = data
        self.pages = self._pages_from_manifest(data)

    def _read_manifest(self) -> dict[str, Any] | None:
        """The store's manifest, or None when it has none.

        A malformed or wrong-version manifest raises: a store that ships a broken
        artifact should say so, not degrade into something that half works.
        """
        path = self.root / MANIFEST_NAME
        if "://" in self.target:
            # Read, never pre-check: over fsspec `is_file()` answers False to a 403, a
            # missing bucket and a missing key alike, and the refusal that follows
            # got reported as "does not exist" — sending a client with the wrong key
            # to look for a directory. The read raises the real reason.
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise StoreError(REFUSED.format(path=path, error=exc)) from exc
        else:
            try:
                if not path.is_file():
                    return None
                text = path.read_text(encoding="utf-8")
            except OSError:
                return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError(
                f"{self.id}: {MANIFEST_NAME} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict) or "pages" not in data:
            raise StoreError(f"{self.id}: {MANIFEST_NAME} is not a manifest")
        declared = int(data.get("manifest_version", MANIFEST_VERSION))
        if declared != MANIFEST_VERSION:
            raise StoreError(
                f"{self.id}: {MANIFEST_NAME} declares manifest_version {declared}; "
                f"this build reads {MANIFEST_VERSION} — upgrade latent-wiki, or "
                f"regenerate the store with the tool that built it"
            )
        name, version = STORE_FORMAT
        if (data.get("format"), data.get("format_version")) != STORE_FORMAT:
            found = f"{data.get('format')!r} v{data.get('format_version')!r}"
            raise StoreError(
                f"{self.id}: {MANIFEST_NAME} is {found}; this build reads {name} "
                f"v{version}. An unstamped manifest predates the published format — "
                f"republish the store with the tool that built it"
            )
        return data

    def _pages_from_manifest(self, data: dict[str, Any]) -> dict[str, _Page]:
        pages_dir = self.root / PAGES_DIR
        pages: dict[str, _Page] = {}
        for entry in data.get("pages") or []:
            key = str(entry.get("key") or "")
            raw = entry.get("frontmatter") or {}
            if not key or not isinstance(raw, dict):
                continue
            if RETIRED_PAGE_KEYS & set(raw):
                # The retired shape: skipped, as `Store.load` skips it, so one old page
                # does not take the rest of the store with it; `latent-wiki validate`
                # names it.
                continue
            file = str(entry.get("file") or f"{key}.md")
            if _UNSAFE_RAW.search(file):
                raise StoreError(
                    f"{self.id}: page '{key}' names {file!r}, which is not inside "
                    f"{PAGES_DIR}/ — the manifest is not one `latent-wiki manifest` "
                    f"wrote"
                )
            pages[key] = _Page(
                key=key,
                raw=raw,
                lead=str(entry.get("lead") or ""),
                path=pages_dir / file,
            )
        return pages

    def _unreadable(self) -> str:
        """Which of the three unpublished cases this root is, as a message template.

        A remote root that cannot be listed is reported as missing: an unreachable
        `s3://` prefix and an absent directory are the same fact to the caller, and
        guessing which would be a guess about someone else's network.
        """
        try:
            if not self.root.exists():
                return NO_STORE
            if self.root.is_dir() and not any(self.root.iterdir()):
                return EMPTY_STORE
        except OSError:
            return NO_STORE
        return UNPUBLISHED_STORE

    # -- the contract ---------------------------------------------------------

    def search(self, query: str, *, limit: int = 10, **filters: Any) -> list[Hit]:
        """Rank by FTS5 over the gist fields. Filters run first and are absolute.

        Filters: `type`, `tag`, `part_of`, `status`, a `where` mapping over any
        frontmatter field (a value or a list of accepted values), `include_retired`
        (default off) and `include_drafts` (default on — a draft is shown, flagged).
        A query naming a page exactly — key, title or alias — puts that page first.
        """
        q = str(query).strip()
        if not q:
            return []
        keep = self._filter(filters)
        ranked = self._rank(q, keep)
        lowered = q.lower()
        exact = {
            key
            for key, page in self.pages.items()
            if lowered in {key, page.title.lower(), *(a.lower() for a in page.aliases)}
        }
        ordered = [r for r in ranked if r[0] in exact] + [
            r for r in ranked if r[0] not in exact
        ]
        hits: list[Hit] = []
        for key, score, matched_on in ordered:
            if key not in keep:
                continue
            page = self.pages[key]
            hits.append(
                Hit(
                    ref=key,
                    title=page.title,
                    kind=page.type or "?",
                    lead=page.lead[:280],
                    score=round(score, 3),
                    metadata={
                        "matched_on": list(matched_on)
                        or (["exact"] if key in exact else []),
                        "draft": page.draft,
                        "inferred": page.inferred,
                        "exact": key in exact,
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def _rank(
        self, query: str, allowed: set[str]
    ) -> list[tuple[str, float, tuple[str, ...]]]:
        """FTS5 where available; a substring count over the gist where it is not."""
        if self._index is not None:
            ranked = self._index.rank(query, allowed)
            return [(r.key, r.score, r.matched_on) for r in ranked]
        q = query.lower()
        scored = []
        for key, page in self.pages.items():
            haystack = f"{key} {page.title} {page.why} {page.lead}".lower()
            n = haystack.count(q)
            if n:
                scored.append(
                    (float(n + 5 * (q in key) + 3 * (q in page.title.lower())), key)
                )
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [(key, score, ()) for score, key in scored]

    def _filter(self, filters: dict[str, Any]) -> set[str]:
        """The keys a query may return, by predicate. Never a ranking penalty."""
        type_filter = str(filters.get("type") or filters.get("kind") or "")
        tag_filter = str(filters.get("tag") or "")
        parent = str(filters.get("part_of") or "")
        status = str(filters.get("status") or "")
        include_retired = bool(filters.get("include_retired"))
        include_drafts = filters.get("include_drafts", True) not in (False, "false", 0)
        where = filters.get("where") or {}
        if not isinstance(where, dict):
            raise StoreError("`where` must map a frontmatter field to a value or list")
        keep: set[str] = set()
        for key, page in self.pages.items():
            if type_filter and page.type != type_filter:
                continue
            if tag_filter and tag_filter not in page.tags:
                continue
            if parent and page.parent != parent:
                continue
            if status and page.status != status:
                continue
            if not status and not include_retired and page.status in RETIRED:
                continue
            if not include_drafts and page.draft:
                continue
            if not all(_field_matches(page.raw.get(f), v) for f, v in where.items()):
                continue
            keep.add(key)
        return keep

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Answer one tool. Synchronous on purpose: every read here blocks (fsspec,
        S3), and the MCP server runs a sync tool on a worker thread, so one slow read
        does not stall every other request."""
        arguments = {
            arg: self._own(value) if arg in _KEY_ARGS else value
            for arg, value in arguments.items()
        }
        key = str(arguments.get("key") or "")
        if name == "wiki_overview":
            return self.overview()
        if name == "wiki_index":
            text = self.index(
                type=str(arguments.get("type") or ""),
                part_of=str(arguments.get("part_of") or ""),
                offset=_clamp(arguments.get("offset"), 0, 0, None),
                limit=_clamp(arguments.get("limit"), INDEX_PAGE, 1, INDEX_MAX),
            )
            return _also_cut(text, _lowered(arguments.get("limit"), INDEX_MAX))
        if name == "wiki_search":
            query = str(arguments.get("query") or "")
            limit = _clamp(arguments.get("limit"), 10, 1, SEARCH_MAX)
            lowered = _lowered(arguments.get("limit"), SEARCH_MAX)
            hits = self.search(
                query,
                limit=limit + 1,
                type=arguments.get("type") or "",
                tag=arguments.get("tag") or "",
                part_of=arguments.get("part_of") or "",
                status=arguments.get("status") or "",
                where=arguments.get("where") or {},
                include_retired=bool(arguments.get("include_retired")),
                include_drafts=arguments.get("include_drafts", True),
            )
            if not hits:
                text = f"no matches for {query!r}" + self._footer("search", query)
                return _also_cut(text, lowered)
            more = len(hits) > limit
            hits = hits[:limit]
            lines = [
                f"[{h.kind}] {self.name(h.ref)} — {h.title}{self._flags(h)}\n"
                f"  {h.lead}\n"
                f"  matched on: {', '.join(h.metadata['matched_on']) or '—'}"
                for h in hits
            ]
            narrow = "filter to narrow" if lowered else "raise limit or filter"
            cut = f"first {limit}; {narrow}" if more else ""
            text = "\n\n".join(lines) + self._footer("search", query, cut=cut)
            return _also_cut(text, lowered)
        if name == "wiki_get":
            return self.get(
                key,
                section=str(arguments.get("section") or ""),
                offset=_clamp(arguments.get("offset"), 0, 0, None),
            )
        if name == "wiki_neighbors":
            return self._neighbors_text(key)
        if name == "wiki_trace":
            return self._trace_text(key)
        if name == "wiki_evidence":
            return self.evidence(
                key,
                entry=str(arguments.get("entry") or ""),
                offset=_clamp(arguments.get("offset"), 0, 0, None),
            )
        if name == "wiki_raw":
            return self.raw(
                key,
                _clamp(arguments.get("offset"), 0, 0, None),
                _clamp(arguments.get("max_chars"), RAW_MAX_CHARS, 1, RAW_MAX_CHARS),
            )
        if name == "wiki_view":
            return self.view(
                root_type=str(arguments.get("root_type") or ""),
                parent=str(arguments.get("parent") or ""),
                order=str(arguments.get("order") or ""),
                fields=[str(f) for f in arguments.get("fields") or []],
                root=str(arguments.get("root") or ""),
                depth=_clamp(arguments.get("depth"), 0, 0, None),
                include_retired=bool(arguments.get("include_retired")),
            )
        raise StoreError(f"{self.id} has no tool '{name}'")

    # -- the ladder -----------------------------------------------------------

    def overview(self) -> str:
        """The one routing rung: what this store holds and where to go next."""
        by_type: dict[str, list[_Page]] = {}
        for page in self.pages.values():
            by_type.setdefault(page.type or "?", []).append(page)
        drafts = sum(page.draft for page in self.pages.values())
        retired = sum(page.status in RETIRED for page in self.pages.values())
        lines = [
            str(self.manifest.get("title") or self.id),
            str(self.manifest.get("description") or "").strip(),
            "",
            f"{len(self.pages)} pages ({drafts} draft, {retired} retired):",
        ]
        for kind, pages in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            lines.append(f"  {kind}: {len(pages)}")
        children: dict[str, int] = {}
        for page in self.pages.values():
            if page.parent:
                children[page.parent] = children.get(page.parent, 0) + 1
        roots = sorted(
            (k for k in children if k in self.pages and not self.pages[k].parent),
            key=lambda k: self.pages[k].title,
        )
        if roots:
            lines += [
                "",
                "Structures (part_of trees) — wiki_index part_of=<key>, or a view:",
            ]
            lines += [
                f"  {self.name(k)} — {self.pages[k].title} "
                f"({_descendants(self.pages, k)} beneath)"
                for k in roots
            ]
        small = [kind for kind, pages in by_type.items() if len(pages) <= SMALL_TYPE]
        for kind in sorted(small):
            lines += ["", f"All {kind} pages:"]
            lines += [
                f"  {self.name(p.key)} — {p.title}{p.flags()}"
                for p in sorted(by_type[kind], key=lambda p: p.key)
            ]
        lines += [
            "",
            "Next: wiki_search for a topic, wiki_index type=<type> to list, "
            "wiki_get for a "
            "page, wiki_evidence for what a page rests on.",
        ]
        return "\n".join(lines) + self._footer("overview", "")

    def index(
        self,
        *,
        type: str = "",
        part_of: str = "",
        offset: int = 0,
        limit: int = INDEX_PAGE,
    ) -> str:
        """Every page as one line, paged: of one type, or one parent's children."""
        chosen = [
            p
            for p in self.pages.values()
            if (not type or p.type == type) and (not part_of or p.parent == part_of)
        ]
        pages = sorted(chosen, key=lambda p: (_step(p) if part_of else 0, p.key))
        chunk = pages[offset : offset + limit]
        lines = [
            f"[{p.type}] {self.name(p.key)} — {p.title}{p.flags()}\n  {p.why}"
            for p in chunk
        ]
        end = offset + len(chunk)
        cut = f"{end} of {len(pages)}; next offset={end}" if end < len(pages) else ""
        subject = part_of or type or "all"
        return "\n".join(lines) + self._footer("index", subject, cut=cut)

    def get(self, key: str, *, section: str = "", offset: int = 0) -> str:
        """One page: header and body, a named section of it, capped either way."""
        page = self.pages.get(key)
        if page is None:
            return f"no page '{key}' in {self.id}"
        body = page.body().strip()
        headings = [text for _, text in _headings(body)]
        if section:
            body = _section(body, section)
            if not body:
                return (
                    f"no section {section!r} in {key}; sections: "
                    f"{'; '.join(headings) or '(none)'}"
                )
        text = body[offset : offset + GET_MAX_CHARS]
        end = offset + len(text)
        header = [
            f"{self.name(key)} — {page.title}  [{page.type}{page.flags()}]",
            f"why: {page.why}",
        ]
        if page.parent:
            header.append(f"part of: {self.name(page.parent)}")
        if page.evidence:
            header.append(
                f"evidence: {len(page.evidence)} entries — "
                f"wiki_evidence key={self.name(key)}"
            )
        if headings and not section:
            header.append(f"sections: {'; '.join(headings)}")
        cut = ""
        if end < len(body):
            cut = f"{end} of {len(body)} chars; call again with offset={end}"
        return "\n".join(header) + "\n\n" + text + self._footer("get", key, cut=cut)

    def evidence(self, key: str, *, entry: str = "", offset: int = 0) -> str:
        """What a page rests on, from the paired context store.

        Without `entry`: one gist per cited entry — its title, intent and lead. With
        `entry`: that entry in full, paged. Either way, the next rung is `wiki_raw`.
        """
        page = self.pages.get(key)
        if page is None and not entry:
            return f"no page '{key}' in {self.id}"
        problem = self._check_context()
        if problem:
            return problem
        if entry:
            path = self._summary_path(entry)
            if path is None:
                return f"no context entry '{entry}' under {self.context}"
            loaded = _read_entry(path)
            if isinstance(loaded, str):
                return loaded
            meta, text = loaded
            chunk = text[offset : offset + RAW_MAX_CHARS]
            end = offset + len(chunk)
            cut = f"{end} of {len(text)}; offset={end}" if end < len(text) else ""
            return (
                f"{meta.get('title') or entry}\n{meta.get('source') or ''}\n\n{chunk}"
                + self._footer("evidence", _bare(entry), cut=cut)
                + f"\nNext: wiki_raw key={_bare(entry)} for the source text."
            )
        assert page is not None
        if not page.evidence:
            return f"{key} cites no context-store entries" + self._footer(
                "evidence", key
            )
        lines = []
        cited = page.evidence
        for ref in cited[:EVIDENCE_MAX]:
            path = self._summary_path(ref)
            if path is None:
                lines.append(f"- {ref}: not found in {self.context}")
                continue
            loaded = _read_entry(path)
            if isinstance(loaded, str):
                lines.append(f"- {ref}: {loaded}")
                continue
            meta, text = loaded
            lead = text.strip().split("\n\n", 1)[0][:280]
            lines.append(
                f"- {_bare(ref)} — {meta.get('title') or ''}\n"
                f"  {meta.get('source') or ''}\n"
                f"  why: {meta.get('why') or ''}\n  {lead}"
            )
        cut = (
            f"{EVIDENCE_MAX} of {len(cited)} entries; pass entry=<key> for the others"
            if len(cited) > EVIDENCE_MAX
            else ""
        )
        return (
            "\n".join(lines)
            + self._footer("evidence", key, cut=cut)
            + "\nNext: wiki_evidence entry=<key> for one in full, "
            "wiki_raw key=<key> for its source."
        )

    def raw(self, key: str, offset: int = 0, max_chars: int = RAW_MAX_CHARS) -> str:
        """Escalate to source text, paged — the last rung, and it must be bounded.

        `key` may be a page — its declared sources first, then the first cited
        evidence entry's `raw_path` — or a context-store entry, by key.
        """
        page = self.pages.get(key)
        candidates: list[tuple[str, Path | None]] = []
        if page is not None:
            for resource in page.resources():
                if resource in page.evidence:
                    candidates.append((resource, self._raw_of_entry(resource)))
                else:
                    candidates.append((resource, self._resolve_origin(resource)))
        elif self.context is not None:
            candidates.append((key, self._raw_of_entry(key)))
        else:
            return f"no page '{key}'"
        for label, path in candidates:
            if path is None:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, FileNotFoundError):
                continue
            chunk = text[offset : offset + max_chars]
            end = offset + len(chunk)
            head = f"{label}  [{offset}:{end} of {len(text)}]"
            cut = f"{end} of {len(text)}; call again with offset={end}"
            return f"{head}\n\n{chunk}" + self._footer(
                "raw", key, cut=cut if end < len(text) else ""
            )
        declared = ", ".join(page.resources()) if page else key
        if self.context is None:
            return (
                f"no source attached for '{key}' (declared: {declared or 'none'}). "
                f"Pair this wiki with its material: serve it from its deployment, "
                f"`latent-wiki serve --deployment <folder>`."
            )
        return (
            f"no readable source for '{key}' (declared: {declared or 'none'}) under "
            f"{self.context}. Remote or missing sources cannot be paged — this is not "
            f"an empty source."
        )

    def view(
        self,
        *,
        root_type: str,
        parent: str,
        order: str = "",
        fields: list[str] | None = None,
        root: str = "",
        depth: int = 0,
        include_retired: bool = False,
    ) -> str:
        """A tree over the pages: `root_type` at the top, children through `parent`.

        Retired pages are left out unless asked for, by the same predicate as search: a
        superseded step and its replacement would otherwise sit side by side.
        """
        if not root_type or not parent:
            return "wiki_view needs root_type and parent"
        shown = dict(self.pages)
        under_retired: list[str] = []
        if not include_retired:
            retired = {k for k, p in shown.items() if p.status in RETIRED}
            # A current page under a retired parent is neither an orphan (the parent
            # exists) nor placed (its parent is hidden): it is said as itself.
            under_retired = sorted(
                k
                for k, p in shown.items()
                if k not in retired and str(p.raw.get(parent) or "") in retired
            )
            shown = {
                k: p
                for k, p in shown.items()
                if k not in retired and k not in under_retired
            }
        tree = views.build(
            views.View(
                name=root_type,
                root_type=root_type,
                parent=parent,
                order=order,
                fields=tuple(fields or ()),
            ),
            {key: (page.title, page.raw) for key, page in shown.items()},
        )
        text, cut = views.render(tree, root, depth, named=self.name)
        if under_retired:
            names = under_retired[: views.MAX_NAMES]
            text += "\nunder a retired parent (include_retired to show): " + ", ".join(
                map(self.name, names)
            )
            if len(under_retired) > len(names):
                more = f"under a retired parent: {len(names)} of {len(under_retired)}"
                cut = f"{cut}; {more}" if cut else more
        return text + self._footer("view", root or root_type, cut=cut)

    # -- the graph ------------------------------------------------------------

    def neighbors(self, key: str) -> dict[str, list[tuple[str, str]]]:
        """Outbound and inbound edges, each with its derivation."""
        page = self.pages.get(key)
        if page is None:
            return {}
        out = [(link["to"], link["derivation"]) for link in page.links]
        inn = [
            (other, link["derivation"])
            for other, candidate in self.pages.items()
            for link in candidate.links
            if link["to"] == key
        ]
        return {"out": sorted(out), "in": sorted(inn)}

    def trace(self, key: str) -> dict[str, Any]:
        """Where a page came from and where it sits: evidence, lifecycle, parents."""
        page = self.pages.get(key)
        if page is None:
            return {}
        chain, seen, at = [], {key}, page.parent
        while at and at not in seen and at in self.pages:
            chain.append(at)
            seen.add(at)
            at = self.pages[at].parent
        return {
            "key": key,
            "sources": page.sources,
            "source_docs": page.source_docs,
            "part_of": " < ".join(chain),
            "children": sorted(k for k, p in self.pages.items() if p.parent == key),
            "provenance": page.raw.get("provenance") or {},
            "captured": str(page.raw.get("captured") or ""),
            "as_of": str(page.raw.get("as_of") or ""),
            "status": page.status,
            "supersedes": page.raw.get("supersedes") or [],
            "superseded_by": page.raw.get("superseded_by") or "",
        }

    def _neighbors_text(self, key: str) -> str:
        edges = self.neighbors(key)
        if not edges:
            return f"no page '{key}'"
        parts, cut = [], []
        for direction, arrow in (("out", "->"), ("in", "<-")):
            rows = edges[direction]
            shown = rows[:NEIGHBORS_MAX]
            body = "\n".join(
                f"  {arrow} {self.name(t)} — {self._title(t)}  ({d})" for t, d in shown
            )
            parts.append(f"{direction}:\n{body or '  (none)'}")
            if len(rows) > len(shown):
                cut.append(f"{direction} {len(shown)} of {len(rows)}")
        return "\n".join(parts) + self._footer("neighbors", key, cut=", ".join(cut))

    def _trace_text(self, key: str) -> str:
        trace = self.trace(key)
        if not trace:
            return "no page"
        named = {
            **trace,
            "key": self.name(key),
            "part_of": " < ".join(map(self.name, trace["part_of"].split(" < ")))
            if trace["part_of"]
            else "",
            "children": [self.name(k) for k in trace["children"]],
            "supersedes": [
                self.name(str(k))
                for k in (
                    [trace["supersedes"]]
                    if isinstance(trace["supersedes"], str)
                    else trace["supersedes"]
                )
            ],
            "superseded_by": self.name(str(trace["superseded_by"]))
            if trace["superseded_by"]
            else "",
        }
        return "\n".join(f"{k}: {v}" for k, v in named.items() if v) + self._footer(
            "trace", key
        )

    # -- internals ------------------------------------------------------------

    def name(self, key: str) -> str:
        """A page as answers name it: `<store_id>:<key>`. A link into a peer store
        already carries its store's id, and stays as it is."""
        return key if ":" in key else f"{self.id}:{key}"

    def _own(self, value: Any) -> Any:
        """A key with this store's id in front, as answers print it, made bare."""
        prefix = f"{self.id}:"
        if isinstance(value, str) and value.startswith(prefix):
            rest = value[len(prefix) :]
            if rest and not any(c.isspace() for c in rest):
                return rest
        return value

    def _check_context(self) -> str:
        """Why the paired context store cannot be read, or "" when it can."""
        if self.context is None:
            return (
                "no context store paired with this wiki — serve it from its "
                "deployment, `latent-wiki serve --deployment <folder>`, whose context "
                "store its pages cite."
            )
        if self._context_checked:
            return ""
        descriptor = self.context / "context-store.yaml"
        try:
            data = yaml.safe_load(descriptor.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return (
                f"{descriptor} is missing — the paired context store is not published"
            )
        except OSError as exc:
            return REFUSED.format(path=descriptor, error=exc)
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            return f"{descriptor} cannot be read as YAML: {exc}"
        found = (
            (data.get("format"), data.get("format_version"))
            if isinstance(data, dict)
            else None
        )
        if found != CONTEXT_FORMAT:
            name, version = CONTEXT_FORMAT
            return (
                f"{descriptor} declares {found}; this build reads {name} v{version} — "
                f"republish the context store with the tool that built it"
            )
        self._context_checked = True
        return ""

    def _summary_path(self, ref: str) -> Path | None:
        key = _bare(ref)
        # An entry key is one segment: a ref with a path in it is refused outright,
        # not stripped to whatever follows its last colon.
        if (
            self.context is None
            or "/" in ref
            or "\\" in ref
            or not _ENTRY_KEY.match(key)
            or ".." in key
        ):
            return None
        candidate = self.context / "summaries" / f"{key}.md"
        try:
            return candidate if candidate.is_file() else None
        except OSError:
            return None

    def _raw_of_entry(self, ref: str) -> Path | None:
        """A context entry's `raw_path`, resolved against the context root."""
        path = self._summary_path(ref)
        if path is None or self.context is None:
            return None
        loaded = _read_entry(path)
        if isinstance(loaded, str):
            return None
        raw_path = str(loaded[0].get("raw_path") or "")
        if not raw_path or _UNSAFE_RAW.search(raw_path):
            return None
        candidate = self.context / raw_path.rstrip("/")
        try:
            if candidate.is_file():
                return candidate
            if candidate.is_dir():
                files = sorted(f for f in candidate.iterdir() if f.suffix == ".md")
                return files[0] if files else None
        except OSError:
            return None
        return None

    def _resolve_origin(self, resource: str) -> Path | None:
        """Map a `sources[].resource` to something readable, or None.

        A resource is written `corpus:raw/paper.md` — a root name the store declares,
        then a path under it. The paired `context:` is that root: one location, named
        once in the project file, rather than a wiki config this client cannot see.
        """
        if not resource or "://" in resource:
            return None
        _, _, rest = resource.rpartition(":")
        if not rest or _UNSAFE_RAW.search(rest):
            return None
        candidates: list[Path] = []
        if self.context is not None:
            candidates += [self.context / rest, self.context / resource]
        candidates += [self.root / rest, self.root / PAGES_DIR / rest]
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:  # an unreadable mount is a miss, not a crash
                continue
        return None

    def _footer(self, rung: str, subject: str, *, cut: str = "") -> str:
        """Where an answer came from and what it left out — on every answer."""
        generated = str(self.manifest.get("generated") or "")
        line = (
            f"\n\n— {rung}"
            + (f" · {subject}" if subject else "")
            + (f" · {self.id} as of {generated}" if generated else f" · {self.id}")
        )
        return line + f"\n  cut: {cut or 'nothing'}"

    def _title(self, key: str) -> str:
        page = self.pages.get(key)
        return page.title if page else "?"

    @staticmethod
    def _flags(hit: Hit) -> str:
        marks = [m for m in ("draft", "inferred") if hit.metadata.get(m)]
        return f" · {' · '.join(marks)}" if marks else ""


#: A `raw_path` a reader can follow: relative, inside the context store.
_UNSAFE_RAW = re.compile(
    r"^[/\\~]|^[A-Za-z]:|^[A-Za-z][A-Za-z0-9+.-]*://|(^|[/\\])\.\.([/\\]|$)"
)


#: The arguments that name a page or an entry, read bare or as an answer names them.
_KEY_ARGS = frozenset({"key", "part_of", "root", "entry", "query"})


def _bare(ref: str) -> str:
    """`corpus:<key>` → `<key>`; a bare key stays as it is."""
    return ref.rsplit(":", 1)[-1]


def _read_entry(path: Path) -> tuple[dict[str, Any], str] | str:
    """A context entry's frontmatter and body, read once — or why it could not be."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return f"{path.name} is not UTF-8 text ({exc.reason})"
    except OSError as exc:
        return f"{path.name} could not be read: {exc}"
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        parsed = yaml.safe_load(match.group(0).strip().strip("-")) or {}
    except yaml.YAMLError as exc:
        return f"{path.name} has frontmatter that is not YAML: {exc}"
    return (parsed if isinstance(parsed, dict) else {}), text[match.end() :]


def _clamp(value: Any, default: int, low: int, high: int | None) -> int:
    """A caller's number, defaulted and held inside the bounds every rung promises."""
    try:
        number = int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        number = default
    number = max(low, number)
    return min(number, high) if high is not None else number


def _lowered(value: Any, cap: int) -> str:
    """What a footer says when a caller's limit was above the cap and lowered to it."""
    try:
        asked = int(value)
    except (TypeError, ValueError):
        return ""
    return f"limit lowered to {cap}, the cap" if asked > cap else ""


def _also_cut(text: str, note: str) -> str:
    """The answer with `note` added to its footer's `cut:` line."""
    if not note:
        return text
    head, _, cut = text.rpartition("\n  cut: ")
    return f"{head}\n  cut: {note if cut == 'nothing' else f'{cut}; {note}'}"


def _step(page: _Page) -> int:
    step = page.raw.get("step")
    return step if isinstance(step, int) else 10**6


_HEADING = re.compile(r"^(#{1,3}) +(.+?) *$")


def _headings(body: str) -> list[tuple[int, str]]:
    """`(line index, text)` of each `##`/`###` heading outside fenced code."""
    found, fenced = [], False
    for i, line in enumerate(body.splitlines()):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = _HEADING.match(line)
        if not fenced and m and len(m.group(1)) >= 2:
            found.append((i, m.group(2)))
    return found


def _section(body: str, name: str) -> str:
    """One `##`/`###` section, by heading text (case-insensitive prefix).

    It runs to the next heading of the same or a higher level — a `#` heading ends it
    too — and headings inside fenced code are text, not structure.
    """
    lines = body.splitlines()
    want = name.lower().strip()
    start, level, fenced = None, 0, False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = _HEADING.match(line)
        if fenced or not m:
            continue
        depth = len(m.group(1))
        if start is None:
            if depth >= 2 and m.group(2).lower().startswith(want):
                start, level = i, depth
        elif depth <= level:
            return "\n".join(lines[start:i]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else ""


def _descendants(pages: dict[str, _Page], key: str) -> int:
    kids = [k for k, p in pages.items() if p.parent == key]
    return len(kids) + sum(_descendants(pages, k) for k in kids)


def _field_matches(value: Any, wanted: Any) -> bool:
    """A frontmatter value against a `where` filter value or list of values."""
    accepted = {str(w) for w in wanted} if isinstance(wanted, list) else {str(wanted)}
    if isinstance(value, list):
        return any(str(v) in accepted for v in value)
    return str(value) in accepted if value is not None else False


_KEY_ONLY: dict[str, Any] = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
}

# Descriptions are prompts. Each one says what the tool cannot do as well as what it
# can, and where to go next — an overpromising description produces confident wrong
# answers, and a ladder nobody can see is not climbed. A parameter whose default is not
# the empty value declares it: over MCP an omitted argument arrives as that default,
# and `include_drafts: false` or `limit: 0` would quietly change the answer.
TOOLS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "wiki_overview",
        "Start here. What this wiki holds: page counts by type, the structures "
        "(part_of trees) in it, and every page of the small types. One screen.",
        {"type": "object", "properties": {}},
    ),
    (
        "wiki_index",
        "List pages one line each (title and intent), paged: of one type, or the "
        "children of one page in step order (part_of). Use to browse; use wiki_search "
        "to find something.",
        {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "part_of": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer", "default": INDEX_PAGE},
            },
        },
    ),
    (
        "wiki_search",
        "Full-text search over key, title, aliases, tags, intent and lead (stemmed, "
        "weighted in that order). Filters apply before ranking: type, tag, part_of, "
        "status, and `where` over any frontmatter field. Superseded pages are hidden "
        "unless include_retired; drafts are shown and flagged. Returns gists and what "
        "each matched on — use wiki_get for a page.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "type": {"type": "string"},
                "tag": {"type": "string"},
                "part_of": {"type": "string"},
                "status": {"type": "string"},
                "where": {"type": "object"},
                "include_retired": {"type": "boolean", "default": False},
                "include_drafts": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
    (
        "wiki_get",
        "One page: type, intent, parent, its section headings, then the body — capped, "
        "with an offset to continue. Pass `section` for one heading only.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "section": {"type": "string"},
                "offset": {"type": "integer"},
            },
            "required": ["key"],
        },
    ),
    (
        "wiki_neighbors",
        "What a page links to and what links back, with each edge's derivation "
        "(extracted, inferred or ambiguous). Capped.",
        _KEY_ONLY,
    ),
    (
        "wiki_trace",
        "Provenance and position: cited evidence, sources, authorship, lifecycle, the "
        "chain of parents (part_of) and the direct children.",
        _KEY_ONLY,
    ),
    (
        "wiki_evidence",
        "What a page rests on: one gist per context-store entry it cites (title, "
        "pages, intent, lead). Pass `entry` for one entry in full. Needs the wiki "
        "paired with its context store.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "entry": {"type": "string"},
                "offset": {"type": "integer"},
            },
            "required": ["key"],
        },
    ),
    (
        "wiki_raw",
        "The source text behind a page or a context entry, paged. The last rung: use "
        "when the page and its evidence do not answer, and quote from it.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "offset": {"type": "integer"},
                "max_chars": {"type": "integer", "default": RAW_MAX_CHARS},
            },
            "required": ["key"],
        },
    ),
    (
        "wiki_view",
        "A structure over the pages as an indented tree: pages of root_type at the "
        "top, children through the parent field, siblings in the order field, with "
        "the named fields carried on each node. Pass root= for one subtree and depth= "
        "to stop early. Superseded pages are hidden unless include_retired. Capped; "
        "says what it cut, and which pages no root reaches.",
        {
            "type": "object",
            "properties": {
                "root_type": {"type": "string"},
                "parent": {"type": "string"},
                "order": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "root": {"type": "string"},
                "depth": {"type": "integer"},
                "include_retired": {"type": "boolean", "default": False},
            },
            "required": ["root_type", "parent"],
        },
    ),
)
