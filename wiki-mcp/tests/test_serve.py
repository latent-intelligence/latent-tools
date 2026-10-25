"""The adapter: the reader's tools over MCP, read-only, HTTP behind a token."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from latent_wiki_mcp.reader import TOOLS, WikiReader
from latent_wiki_mcp.serve import BearerCheck, build_server, instructions

from .conftest import FORMATS

pytestmark = pytest.mark.anyio


async def test_the_server_lists_every_tool_read_only(reader: WikiReader) -> None:
    from mcp import Client

    async with Client(build_server(reader)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert list(tools) == [name for name, _, _ in TOOLS]
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
    assert tools["wiki_view"].input_schema["required"] == ["root_type", "parent"]
    fields = tools["wiki_view"].input_schema["properties"]["fields"]
    assert fields["items"] == {"type": "string"}


async def test_every_current_page_is_a_resource_read_as_written(
    tmp_path: Path,
) -> None:
    """URIs are bare keys; a retired page is not listed; a read is the file itself."""
    from mcp import Client

    wiki = tmp_path / "wiki"
    shutil.copytree(FORMATS / "wiki-store", wiki)
    data = json.loads((wiki / "manifest.json").read_text())
    for entry in data["pages"]:
        if entry["key"] == "count-limpets":
            entry["frontmatter"]["status"] = "superseded"
    (wiki / "manifest.json").write_text(json.dumps(data))
    reader = WikiReader(str(wiki))
    async with Client(build_server(reader)) as client:
        listed = (await client.list_resources()).resources
        assert [str(r.uri) for r in listed] == ["tide-pool"]
        [content] = (await client.read_resource("tide-pool")).contents
    assert content.text == (wiki / "pages" / "tide-pool.md").read_text()  # type: ignore[union-attr]


async def test_an_argument_no_tool_declares_is_refused(reader: WikiReader) -> None:
    """Reject what you cannot honour: a misspelled filter must not look applied."""
    from mcp import Client

    async with Client(build_server(reader)) as client:
        result = await client.call_tool(
            "wiki_search", {"query": "limpets", "tags": "shore"}
        )
        fine = await client.call_tool("wiki_search", {"query": "limpets"})
    assert result.is_error
    [content] = result.content
    assert content.text == (  # type: ignore[union-attr]
        "wiki_search has no 'tags'; it takes include_drafts, include_retired, limit, "
        "part_of, query, status, tag, type, where"
    )
    assert not fine.is_error


async def test_an_omitted_argument_means_what_it_means_in_process(
    reader: WikiReader,
) -> None:
    from mcp import Client

    async with Client(build_server(reader)) as client:
        result = await client.call_tool("wiki_search", {"query": "pool"})
    over_mcp = result.content[0].text  # type: ignore[union-attr]
    assert over_mcp == reader.call("wiki_search", {"query": "pool"})
    assert "count-limpets" in over_mcp


async def test_view_tool_renders_the_tree(reader: WikiReader) -> None:
    from mcp import Client

    async with Client(build_server(reader)) as client:
        result = await client.call_tool(
            "wiki_view",
            {
                "root_type": "concept",
                "parent": "part_of",
                "order": "step",
                "fields": ["aliases"],
            },
        )
    text = result.content[0].text  # type: ignore[union-attr]
    assert "Tide pool (heronsgate:tide-pool)" in text
    assert "1. Count limpets (heronsgate:count-limpets) · draft" in text
    assert "cut: nothing" in text


def test_the_server_is_named_by_its_store_id(reader: WikiReader) -> None:
    server = build_server(reader)
    assert server.name == "heronsgate"
    text = instructions(reader)
    assert text.startswith(f"{reader.manifest['title']}: a wiki of 2 pages.")
    assert "named heronsgate:<key>" in text


def test_pages_are_named_by_the_store_and_echoes_stay_bare(reader: WikiReader) -> None:
    answer = reader.call("wiki_search", {"query": "limpets"})
    assert "[procedure] heronsgate:count-limpets — " in answer
    assert "— search · limpets · " in answer
    missing = reader.call("wiki_get", {"key": "limpets"})
    assert missing == "no page 'limpets' in heronsgate"
    bare = reader.call("wiki_get", {"key": "count-limpets"})
    assert reader.call("wiki_get", {"key": "heronsgate:count-limpets"}) == bare


# -- transport ----------------------------------------------------------------


async def _status(app: Any, headers: list[tuple[bytes, bytes]]) -> int:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}
    await app(scope, receive, send)
    return int(sent[0]["status"])


async def _ok(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def test_http_rejects_a_missing_or_wrong_bearer() -> None:
    app = BearerCheck(_ok, "s3cret")
    assert await _status(app, []) == 401
    assert await _status(app, [(b"authorization", b"Bearer wrong")]) == 401
    assert await _status(app, [(b"authorization", b"Bearer s3cret")]) == 200
    assert await _status(app, [(b"authorization", b"bearer s3cret")]) == 200


async def test_a_websocket_is_refused_too() -> None:
    app = BearerCheck(_ok, "s3cret")
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app({"type": "websocket", "headers": []}, None, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


def _second_store(tmp_path: Path) -> WikiReader:
    """The format fixture as another store: its own id, and periwinkles where the
    first counts limpets. `tide-pool` keeps its key in both, with different text."""
    root = tmp_path / "second"
    shutil.copytree(FORMATS / "wiki-store", root / "wiki")
    shutil.copytree(FORMATS / "context-store", root / "context")
    swaps = {
        "heronsgate": "saltmarsh",
        "Heronsgate": "Saltmarsh",
        "limpet": "periwinkle",
        "water left behind": "brine left behind",
    }
    for path in [p for p in root.rglob("*") if p.is_file()]:
        text = path.read_text()
        for old, new in swaps.items():
            text = text.replace(old, new)
        path.write_text(text)
    pages = root / "wiki" / "pages"
    (pages / "count-limpets.md").rename(pages / "count-periwinkles.md")
    return WikiReader(str(root / "wiki"), context=str(root / "context"))


async def test_two_stores_served_at_once_each_answer_from_their_own(
    reader: WikiReader, tmp_path: Path
) -> None:
    """Two servers in one process, both connected, called concurrently: each answers
    from its own pages and its own context store, under its own name, even for a key
    both stores share."""
    import anyio
    from mcp import Client

    second = _second_store(tmp_path)
    calls = [
        ("wiki_search", {"query": "limpets"}),
        ("wiki_search", {"query": "periwinkles"}),
        ("wiki_get", {"key": "tide-pool"}),
        ("wiki_evidence", {"key": "tide-pool"}),
        ("wiki_raw", {"key": "kelpmoor-ch01-pools"}),
    ]
    answers: dict[str, dict[str, str]] = {"heronsgate": {}, "saltmarsh": {}}
    resources: dict[str, set[str]] = {}
    pages: dict[str, str] = {}

    async with (
        Client(build_server(reader)) as first_client,
        Client(build_server(second)) as second_client,
    ):
        clients = {"heronsgate": first_client, "saltmarsh": second_client}
        assert {n: c.server_info.name for n, c in clients.items()} == {
            "heronsgate": "heronsgate",
            "saltmarsh": "saltmarsh",
        }

        async def ask(store: str, name: str, arguments: dict[str, Any]) -> None:
            result = await clients[store].call_tool(name, arguments)
            assert not result.is_error
            answers[store][f"{name} {arguments}"] = result.content[0].text

        async def browse(store: str) -> None:
            listed = await clients[store].list_resources()
            resources[store] = {str(r.uri) for r in listed.resources}
            read = await clients[store].read_resource("tide-pool")
            pages[store] = read.contents[0].text

        async with anyio.create_task_group() as group:
            for name, arguments in calls:
                for store in clients:
                    group.start_soon(ask, store, name, arguments)
            for store in clients:
                group.start_soon(browse, store)

    first, other = answers["heronsgate"], answers["saltmarsh"]
    for name, arguments in calls:
        asked = f"{name} {arguments}"
        assert first[asked] == reader.call(name, arguments)
        assert other[asked] == second.call(name, arguments)
    assert "heronsgate:count-limpets" in first["wiki_search {'query': 'limpets'}"]
    assert "saltmarsh" not in first["wiki_search {'query': 'limpets'}"]
    assert "count-periwinkles" not in first["wiki_search {'query': 'periwinkles'}"]
    assert (
        "saltmarsh:count-periwinkles" in other["wiki_search {'query': 'periwinkles'}"]
    )
    assert "count-limpets" not in other["wiki_search {'query': 'limpets'}"]
    assert "water left behind" in first["wiki_get {'key': 'tide-pool'}"]
    assert "brine left behind" in other["wiki_get {'key': 'tide-pool'}"]
    assert "limpet" in first["wiki_evidence {'key': 'tide-pool'}"]
    assert "periwinkle" in other["wiki_evidence {'key': 'tide-pool'}"]
    assert "limpet" not in other["wiki_evidence {'key': 'tide-pool'}"]
    assert "Count the limpets" in first["wiki_raw {'key': 'kelpmoor-ch01-pools'}"]
    assert "Count the periwinkles" in other["wiki_raw {'key': 'kelpmoor-ch01-pools'}"]
    assert resources == {
        "heronsgate": {"tide-pool", "count-limpets"},
        "saltmarsh": {"tide-pool", "count-periwinkles"},
    }
    assert "water left behind" in pages["heronsgate"]
    assert "brine left behind" in pages["saltmarsh"]
