# Verbatim copy of latent-wiki at 991549f, below this header:
#   src/latent_wiki/serve.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""`latent-wiki serve` — one published wiki store over MCP, read-only, stdio or HTTP.

The adapter beside the store (house rule: stores own their capability). It adds no
capability of its own: every tool is `WikiReader`'s, declared once in its tool table and
dispatched by its `call`, and this module adapts that pair to MCP. The tool contract is
`docs/formats/wiki-tools.md`.

Kept from the first `latent-wiki serve`, because use validated them:

- **Read-only by construction.** No tool writes. Authoring stays with `latent-wiki`,
  where validate / links --close / index run.
- **One server, one store.** The client chooses which servers to connect to, so keys
  from two stores never share a namespace.
- **Location is configured, not assumed.** The store is the wiki of the deployment whose
  folder `--deployment` names, a local path or any URI `UPath` opens; a remote store
  reads through `manifest.json`.

**HTTP requires a token,** read from the environment, compared in constant time on every
request. HTTP without one is refused unless the operator passes `--insecure-local` and
the host is loopback.

`mcp` is the `serve` extra and is imported only when a server is built. Nothing but the
protocol may reach stdout on stdio; diagnostics go to stderr.
"""

from __future__ import annotations

import hmac
import inspect
from typing import Any

from .reader import RETIRED, TOOLS, WikiReader

#: A resource description's cap — a search lead's, so listing every page stays bounded.
DESCRIPTION_MAX = 280

#: Hosts that only this machine can reach — the one place HTTP may run without a token.
LOOPBACK = ("127.0.0.1", "localhost", "::1")

#: JSON Schema's scalar types, as Python annotations. The server infers a tool's schema
#: from a function signature, so this is how the reader's declared schema is honoured
#: rather than re-derived.
_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

#: What an optional parameter becomes when the schema names no default. Empty rather
#: than `None`: the reader reads these with `str(...)` / `int(... or default)`.
_EMPTY: dict[type, Any] = {str: "", int: 0, float: 0.0, bool: False, list: [], dict: {}}


def _signature(schema: dict[str, Any]) -> inspect.Signature:
    """A call signature expressing `schema`.

    Required properties become required keyword arguments; the rest take a default, so
    the generated MCP schema marks exactly what the reader marked.
    """
    properties: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parameters = []
    for name, prop in properties.items():
        base = _TYPES.get(str(prop.get("type") or "string"), str)
        annotation: Any = base
        if base is list and (prop.get("items") or {}).get("type") == "string":
            annotation = list[str]
        if name in required:
            default: Any = inspect.Parameter.empty
        else:
            default = prop.get("default", _EMPTY.get(base))
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=annotation,
                default=default,
            )
        )
    return inspect.Signature(parameters)


def _handler(reader: WikiReader, name: str, description: str, schema: Any) -> Any:
    """One MCP tool, delegating to the reader's own dispatcher.

    Sync, so the server runs it on a worker thread: the reader's reads block.
    """

    def run(**arguments: Any) -> str:
        return str(reader.call(name, arguments))

    run.__name__ = name
    run.__doc__ = description
    run.__signature__ = _signature(schema)  # type: ignore[attr-defined]
    return run


def instructions(reader: WikiReader) -> str:
    """What a client reads first: what the store is, and how to climb the ladder."""
    title = str(reader.manifest.get("title") or reader.id)
    about = str(reader.manifest.get("description") or "").strip()
    return "\n".join(
        [
            f"{title}: a wiki of {len(reader.pages)} pages."
            + (f" {about}" if about else ""),
            "",
            "Start with wiki_overview; find with wiki_search (filters: type, tag, "
            "part_of, where); read with wiki_get (section= for one part); see what a "
            "page rests on with wiki_evidence; read source text with wiki_raw only "
            "when that does not answer; draw a structure with wiki_view. Pages are "
            f"named {reader.id}:<key>: cite them by that name, and evidence entries "
            "by key. Pages marked draft rest on one source; pages marked inferred are "
            "the author's pattern, not a source's statement.",
        ]
    )


def _page(reader: WikiReader, key: str) -> Any:
    """One page as a resource read: the file as written, frontmatter and all, uncapped.

    A resource is "give me this page"; a tool is "answer this question". Keeping them
    apart is what lets a client fetch a page whole without a model choosing a tool.
    """

    def read() -> str:
        return reader.pages[key].text()

    return read


def build_server(reader: WikiReader) -> Any:
    """An MCP server exposing the reader's tools, each annotated read-only, and every
    current page as a resource.

    The resources are what let a client list and read pages without a model choosing a
    tool: MCP's own list/read operations, which a client may treat as search and fetch.
    Each URI is the page's bare key; the tools print it after the store's id, which is
    also the server's name. The server knows no client, so nothing it says depends on
    what a client calls it. Descriptions are
    capped like a search lead. Retired
    pages are left out of the list by the same predicate as search; `wiki_get` still
    reads them.

    An argument no tool declares is refused, not dropped: the SDK's own validation
    discards extras, so a misspelled filter would otherwise return results the caller
    believes were filtered.
    """
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.resources import FunctionResource
    from mcp.types import ToolAnnotations

    declared = {name: set(schema.get("properties") or {}) for name, _, schema in TOOLS}

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

    server = Server(reader.id, instructions=instructions(reader))
    # Declared, not implied: a client that gates on effects reads this to decide what
    # needs approval, and an absent annotation is correctly read as "assume it writes".
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)
    for name, description, schema in TOOLS:
        server.add_tool(
            _handler(reader, name, description, schema),
            name=name,
            description=description,
            annotations=annotations,
        )
    for key, page in reader.pages.items():
        if page.status in RETIRED:
            continue
        server.add_resource(
            FunctionResource.from_function(
                _page(reader, key),
                uri=key,
                name=page.title,
                description=" — ".join(t for t in (page.why, page.lead) if t)[
                    :DESCRIPTION_MAX
                ],
                mime_type="text/markdown",
            )
        )
    return server


def client_config(
    reader: WikiReader,
    *,
    argv: list[str],
    transport: str,
    host: str,
    port: int,
    token_env: str,
    token: bool,
) -> dict[str, Any]:
    """The `mcpServers` block a client pastes in, for a store that has just loaded.

    Loaded first, so a block you paste is one that starts. `argv` is the stdio command,
    with paths already resolved so a client starting elsewhere finds them. HTTP carries
    the token as `${VAR}`, never its value, whenever one is set, since the server
    then checks it; none at all when it runs without one.
    """
    entry: dict[str, Any]
    if transport == "http":
        dial = f"[{host}]" if ":" in host else host
        entry = {"type": "http", "url": f"http://{dial}:{port}/mcp"}
        if token:
            entry["headers"] = {"Authorization": f"Bearer ${{{token_env}}}"}
    else:
        entry = {"command": argv[0], "args": argv[1:]}
    return {"mcpServers": {f"wiki-{reader.id}": entry}}


class BearerCheck:
    """ASGI middleware: refuse any request whose bearer token is not the one set.

    Everything but the lifespan protocol is checked — a websocket route mounted later
    must not be the way around the token. The scheme name is case-insensitive
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


def http_app(server: Any, token: str | None, host: str) -> Any:
    app = server.streamable_http_app(stateless_http=True, host=host)
    return BearerCheck(app, token) if token else app


def refuse_http(
    token: str | None, variable: str, host: str, insecure_local: bool
) -> str:
    """Why HTTP may not start as asked, or "" when it may. Checked before loading."""
    if token or (insecure_local and host in LOOPBACK):
        return ""
    return (
        f"refusing HTTP without a token (${variable} is not set). Set one, or pass "
        f"--insecure-local with a loopback host for a local test."
    )


async def run(
    reader: WikiReader, *, transport: str, host: str, port: int, token: str | None
) -> None:
    """Serve until the client goes away (stdio) or the process is stopped (HTTP)."""
    server = build_server(reader)
    if transport == "stdio":
        await server.run_stdio_async()
        return
    import uvicorn

    config = uvicorn.Config(
        http_app(server, token, host), host=host, port=port, log_level="warning"
    )
    await uvicorn.Server(config).serve()
