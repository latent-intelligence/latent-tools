# Verbatim copy of latent-records at b7ca93c, below this header:
#   src/latent_records/serve.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""A store over MCP: search, an exact check, a row by id, and what is there.

Thin over `Store`, read-only, and one ranking per call, as everywhere else. The
server is built here and run by `latent-records serve`; ``mcp`` is the ``serve``
extra and is imported only when a server is built. Nothing but the protocol may
reach stdout, so diagnostics go through logging (stderr). The tool contract is
`docs/formats/records-tools.md`.

**Keys go under the store's id.** The server's MCP name is the store's ``store_id``,
and every key a reply names reads ``<store_id>:<key>``, so a client can tell a key
from an ordinary word. What a request echoed, and the id a refusal names, stay bare:
neither was returned. Arguments take a key bare or with the prefix.

**HTTP requires a token,** read from the environment and compared in constant time on
every request. HTTP without one is refused unless the operator passes
``--insecure-local`` and the host is loopback.
"""

from __future__ import annotations

import hmac
import inspect
import json
import logging
import threading
from datetime import datetime
from typing import Any, cast

from .backends.base import Eq
from .embedders.base import load_embedder
from .errors import FormatError
from .search import MODES, PRESENCE_TABLES, Hit, Mode
from .store import Store

log = logging.getLogger(__name__)

MAX_LIMIT = 50

#: Records listed as resources, by id. A store may hold thousands; the listing says
#: how many it left out, and search, contains and get reach every one.
RESOURCES_MAX = 500

#: A resource description's cap: the record's opening text, as a search lead.
DESCRIPTION_MAX = 280

#: Hosts only this machine can reach: the one place HTTP may run without a token.
LOOPBACK = ("127.0.0.1", "localhost", "::1")

MODELS = threading.Lock()

INSTRUCTIONS = """\
Records, their text chunks, figures and tables, searchable by meaning and by words.
- `describe` first: which spaces (embedding models) cover which targets.
- `search` ranks one target in one space; hybrid fuses meaning and words by rank. A hit
  with no `fts_rank` matched on meaning alone: it may be a near-miss, not a mention.
- `contains` answers whether a phrase occurs at all, exactly; use it before claiming
  the corpus says, or does not say, something.
- `get` returns a row in full; a chunk lists its neighbours for reading on.
Cite a chunk by its `section` and record title: its pages are its chapter's range.
Keys are named {store_id}:<id>; cite them by that name."""


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


class Keys:
    """How this store names its keys in replies, and reads them back in arguments."""

    def __init__(self, store_id: str) -> None:
        self.prefix = f"{store_id}:"

    def name(self, key: str | None) -> str | None:
        return None if key is None else self.prefix + key

    def names(self, keys: Any) -> list[str]:
        return [self.prefix + k for k in keys]

    def read(self, key: str) -> str:
        return key.removeprefix(self.prefix)


def store_id_of(store: Store) -> str:
    """The id a server names its keys under. A store without one is refused: a
    folder's name would do, until the folder was renamed."""
    if not store.store_id:
        raise FormatError(
            f"{store.root}/store.json names no store_id; add store_id to the "
            f"deployment and run it again"
        )
    return store.store_id


def hit_dict(hit: Hit, keys: Keys) -> dict[str, Any]:
    return {
        "id": keys.name(hit.id),
        "record_id": keys.name(hit.record_id),
        "title": hit.title,
        "section": list(hit.section),
        "pages": {
            "start": hit.page_start,
            "end": hit.page_end,
            "scope": hit.page_scope,
        },
        "text": hit.text,
        "score": round(hit.score, 6),
        "score_kind": hit.score_kind,
        "vector_rank": hit.vector_rank,
        "fts_rank": hit.fts_rank,
        "space": hit.space,
        "media_path": hit.media_path,
        "refs": keys.names(hit.refs),
        "referenced_by": keys.names(hit.referenced_by),
    }


def describe(store: Store) -> dict[str, Any]:
    status = store.status()
    return {
        "store_id": store.store_id,
        "title": status.title,
        "generated": status.generated,
        "rows": status.tables,
        "spaces": {
            name: {
                "embedder": entry.get("embedder"),
                "model": entry.get("model"),
                "targets": sorted(entry.get("targets") or {}),
                "query_modalities": entry.get("query_modalities", []),
            }
            for name, entry in sorted(store.spaces.items())
        },
        "modes": list(MODES),
        "contains_tables": list(PRESENCE_TABLES),
    }


def _described(store: Store, listed: int, total: int) -> dict[str, Any]:
    return {**describe(store), "resources": {"listed": listed, "records": total}}


def get(store: Store, item_id: str, keys: Keys) -> dict[str, Any] | None:
    row = store.get(item_id)
    if row is None:
        return None
    found = _plain(row)
    if "::chunk::" in item_id and row.get("seq") is not None:
        siblings = {
            r["seq"]: r["id"]
            for r in store.backend.read(
                "chunks", columns=["id", "seq"], where=Eq("record_id", row["record_id"])
            ).to_pylist()
        }
        found["previous"] = keys.name(siblings.get(row["seq"] - 1))
        found["next"] = keys.name(siblings.get(row["seq"] + 1))
    for column in ("id", "record_id"):
        if column in found:
            found[column] = keys.name(found[column])
    if found.get("refs") is not None:
        found["refs"] = keys.names(found["refs"])
    return dict(found)


def _cut(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1] + "…"


def record_text(store: Store, record_id: str) -> str:
    """A record's text: its chunks in order, each chapter's overlap kept once.

    A chunk is an exact span of its chapter, so where one starts before the last
    ended, only the rest of it is new. Spans one character apart were split at a line
    break, inside a table or a block, and are joined by one; any wider gap was blank.
    """
    rows = store.backend.read(
        "chunks",
        columns=["seq", "chapter", "text", "char_start", "char_end"],
        where=Eq("record_id", record_id),
    ).to_pylist()
    parts: list[str] = []
    chapter, end = None, 0
    for row in sorted(rows, key=lambda r: r["seq"]):
        start, text = int(row["char_start"]), str(row["text"])
        if row["chapter"] != chapter:
            chapter, end = row["chapter"], 0
        if start < end:
            text = text[end - start :]
        elif parts:
            parts.append("\n" if start - end == 1 else "\n\n")
        parts.append(text)
        end = max(end, int(row["char_end"]))
    return "".join(parts)


def listing(store: Store) -> tuple[list[tuple[str, str, str]], int]:
    """The records a server lists, as (id, title, description), and how many there
    are in all. Bounded by `RESOURCES_MAX`, by id."""
    if "records" not in store.backend.table_names():
        return [], 0
    records = sorted(
        store.backend.read("records", columns=["record_id", "title"]).to_pylist(),
        key=lambda r: r["record_id"],
    )
    listed = records[:RESOURCES_MAX]
    leads = {
        r["record_id"]: " ".join(str(r["text"]).split())
        for r in store.backend.read(
            "chunks", columns=["record_id", "text"], where=Eq("seq", 0)
        ).to_pylist()
    }
    return [
        (
            r["record_id"],
            str(r["title"] or r["record_id"]),
            _cut(leads.get(r["record_id"], ""), DESCRIPTION_MAX),
        )
        for r in listed
    ], len(records)


def warm(store: Store) -> threading.Thread:
    """Load every space's embedder in the background, so the server answers at once
    and the first query finds its model loaded, or waits only for what is left."""

    def load() -> None:
        for name, entry in store.spaces.items():
            log.info("loading the embedder for space %s", name)
            try:
                with MODELS:
                    load_embedder(entry)
            except Exception as exc:
                log.warning("space %s: %s", name, exc)

    thread = threading.Thread(target=load, name="warm", daemon=True)
    thread.start()
    return thread


def build_server(store: Store) -> Any:
    """An MCP server over one store: its tools, each annotated read-only, and its
    records as resources, so a client can list and read them with no model choosing
    a tool. A resource's URI is the bare record id; the tools print it after the
    store's id, which is also the server's name.

    An argument no tool declares is refused, not dropped: the SDK's own validation
    discards extras, so a misspelled filter would otherwise return results the caller
    believes were filtered."""
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.resources import FunctionResource
    from mcp.types import ToolAnnotations

    store_id = store_id_of(store)
    keys = Keys(store_id)
    records, total = listing(store)
    instructions = f"{store.title or 'A latent-records store'}.\n" + (
        INSTRUCTIONS.format(store_id=store_id)
    )
    if len(records) < total:
        instructions += (
            f"\nResources list {len(records)} of {total} records, by id; "
            f"search, contains and get reach every one."
        )
    declared: dict[str, set[str]] = {}

    class Server(MCPServer):
        async def call_tool(
            self, name: str, arguments: dict[str, Any], context: Any = None
        ) -> Any:
            if name in declared and (
                unknown := sorted(set(arguments) - declared[name])
            ):
                takes = ", ".join(sorted(declared[name])) or "no arguments"
                # %r: an argument name is caller data, and a refusal is one line.
                names = ", ".join(repr(u) for u in unknown)
                raise ToolError(f"{name} has no {names}; it takes {takes}")
            return await super().call_tool(name, arguments, context)

    server = Server(store_id, instructions=instructions)
    for record_id, title, description in records:
        server.add_resource(
            FunctionResource.from_function(
                _reader(store, record_id),
                uri=record_id,
                name=title,
                description=description,
                mime_type="text/markdown",
            )
        )
    # Open-world, as `latent-wiki serve` declares: a store may sit on object storage, so
    # a read can leave the machine.
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    def guarded(call: Any) -> Any:
        try:
            return call()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(str(exc) or type(exc).__name__) from exc

    @server.tool(name="describe", annotations=read_only)
    def describe_tool() -> str:
        """The store: row counts, and which spaces cover which targets."""
        return json.dumps(guarded(lambda: _described(store, len(records), total)))

    @server.tool(name="search", annotations=read_only)
    def search_tool(
        query: str,
        target: str = "chunks",
        space: str | None = None,
        mode: str = "hybrid",
        limit: int = 10,
        record_id: str | None = None,
        expand_refs: bool = False,
    ) -> str:
        """Rank one target (chunks, tables, figure_captions, figure_images) in one
        space by vector, fts or hybrid, 1 to 50 hits. Name a space when several cover
        the target (see describe); figure_images take mode "vector" only."""
        if mode not in MODES:
            raise ToolError(f"mode must be one of {list(MODES)}")
        if not 1 <= limit <= MAX_LIMIT:
            raise ToolError(f"limit must be between 1 and {MAX_LIMIT}")

        def ranked() -> list[Hit]:
            with MODELS:
                return store.search(
                    query,
                    target=target,
                    space=space or None,
                    mode=cast(Mode, mode),
                    limit=limit,
                    record_id=keys.read(record_id) if record_id else None,
                    expand_refs=expand_refs,
                )

        hits = guarded(ranked)
        return json.dumps(
            {
                "hits": [hit_dict(h, keys) for h in hits],
                "truncated": len(hits) == limit,
            }
        )

    @server.tool(name="contains", annotations=read_only)
    def contains_tool(
        phrase: str, record_id: str | None = None, tables: list[str] | None = None
    ) -> str:
        """Whether a phrase occurs, exactly (whole words, in order, any case), and
        where. total 0 means it does not occur."""
        found = guarded(
            lambda: store.contains(
                phrase,
                tables=tables or PRESENCE_TABLES,
                record_id=keys.read(record_id) if record_id else None,
            )
        )
        return json.dumps(
            {
                "phrase": found.phrase,
                "total": found.total,
                "by_record": {keys.prefix + r: n for r, n in found.by_record.items()},
                "ids": keys.names(found.ids),
                "truncated": found.truncated,
            }
        )

    @server.tool(name="get", annotations=read_only)
    def get_tool(id: str) -> str:
        """One row in full by id: a record, chunk, figure or table. A chunk also
        names its previous and next chunk."""
        bare = keys.read(id)
        row = guarded(lambda: get(store, bare, keys))
        if row is None:
            raise ToolError(f"no row {bare!r}")
        return json.dumps(row)

    for tool in (describe_tool, search_tool, contains_tool, get_tool):
        declared[tool.__name__.removesuffix("_tool")] = set(
            inspect.signature(tool).parameters
        )
    return server


def _reader(store: Store, record_id: str) -> Any:
    def read() -> str:
        return record_text(store, record_id)

    return read


def client_config(
    store: Store,
    *,
    argv: list[str],
    transport: str,
    host: str,
    port: int,
    token_env: str,
    token: bool,
) -> dict[str, Any]:
    """The `mcpServers` block a client pastes in, for a store that has just opened.

    `argv` is the stdio command, its paths resolved so a client starting elsewhere
    finds them. HTTP carries the token as `${VAR}`, never its value, whenever one is
    set, since the server then checks it; none at all when it runs without one.
    """
    entry: dict[str, Any]
    if transport == "http":
        dial = f"[{host}]" if ":" in host else host
        entry = {"type": "http", "url": f"http://{dial}:{port}/mcp"}
        if token:
            entry["headers"] = {"Authorization": f"Bearer ${{{token_env}}}"}
    else:
        entry = {"command": argv[0], "args": argv[1:]}
    return {"mcpServers": {f"records-{store_id_of(store)}": entry}}


class BearerCheck:
    """ASGI middleware: refuse any request whose bearer token is not the one set.

    Everything but the lifespan protocol is checked, so a websocket route mounted
    later is no way around the token. The scheme name is case-insensitive
    (RFC 7235); the token is compared in constant time.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        scheme, _, given = headers.get(b"authorization", b"").partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(given, self._token):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"www-authenticate", b"Bearer")],
            }
        )
        await send({"type": "http.response.body", "body": b"unauthorized"})


def refuse_http(
    token: str | None, variable: str, host: str, insecure_local: bool
) -> str:
    """Why HTTP may not start as asked, or "" when it may. Checked before opening."""
    if token or (insecure_local and host in LOOPBACK):
        return ""
    return (
        f"refusing HTTP without a token (${variable} is not set). Set one, or pass "
        f"--insecure-local with a loopback host for a local test."
    )


async def run(
    server: Any, *, transport: str, host: str, port: int, token: str | None
) -> None:
    """Serve until the client goes away (stdio) or the process is stopped (HTTP)."""
    if transport == "stdio":
        await server.run_stdio_async()
        return
    import uvicorn

    app = server.streamable_http_app(stateless_http=True, host=host)
    config = uvicorn.Config(
        BearerCheck(app, token) if token else app,
        host=host,
        port=port,
        log_level="warning",
    )
    await uvicorn.Server(config).serve()
