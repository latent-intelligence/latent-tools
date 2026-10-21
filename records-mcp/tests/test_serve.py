"""The adapter: the store's tools over MCP, read-only, keys under the store's id."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from latent_records_mcp import serve
from latent_records_mcp.errors import FormatError
from latent_records_mcp.serve import DESCRIPTION_MAX, BearerCheck, build_server
from latent_records_mcp.store import Store

from .conftest import STORE_ID

pytestmark = pytest.mark.anyio


async def _call(client: Any, name: str, **arguments: Any) -> Any:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


async def _text(client: Any, name: str, **arguments: Any) -> str:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    return str(result.content[0].text)


async def test_the_tools_are_read_only_and_described(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"describe", "search", "contains", "get"}
        for tool in tools.values():
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is True, tool.name
            assert tool.annotations.open_world_hint is True, tool.name
        described = await _call(client, "describe")
    assert described["rows"]["chunks"] > 0
    assert "chunks" in described["spaces"]["hivewright"]["targets"]
    assert described["store_id"] == STORE_ID


async def test_search_contains_and_get(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        hits = (
            await _call(
                client, "search", query="mouse guards", space="hivewright", limit=3
            )
        )["hits"]
        assert hits[0]["section"] and hits[0]["pages"]["scope"] == "chapter"
        assert {"vector_rank", "fts_rank"} <= set(hits[0])
        found = await _call(client, "contains", phrase="frame card")
        assert found["total"] == 4
        absent = await _call(client, "contains", phrase="opportunity mandate")
        assert absent["total"] == 0
        row = await _call(client, "get", id="comb-ledger-a::chunk::0001")
        assert row["previous"] == f"{STORE_ID}:comb-ledger-a::chunk::0000"
        assert row["next"] == f"{STORE_ID}:comb-ledger-a::chunk::0002"
        first = await _call(client, "get", id="comb-ledger-a::chunk::0000")
        assert first["previous"] is None


async def test_what_cannot_be_honoured_is_an_error(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        several = await client.call_tool("search", {"query": "comb"})
        assert several.is_error and "several spaces" in several.content[0].text
        mode = await client.call_tool("search", {"query": "comb", "mode": "fuzzy"})
        assert mode.is_error
        for limit in (0, 51):
            refused = await client.call_tool(
                "search", {"query": "comb", "space": "hivewright", "limit": limit}
            )
            assert refused.is_error and "between 1 and 50" in refused.content[0].text
        missing = await client.call_tool("get", {"id": "comb-ledger-a::chunk::9999"})
        assert missing.is_error
        assert missing.content[0].text.endswith(": no row 'comb-ledger-a::chunk::9999'")


async def test_a_full_search_reply_says_more_may_have_ranked(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        cut = await _call(client, "search", query="frame", space="hivewright", limit=1)
        whole = await _call(
            client, "search", query="mouse guards", mode="fts", limit=50
        )
    assert len(cut["hits"]) == 1 and cut["truncated"] is True
    assert 0 < len(whole["hits"]) < 50 and whole["truncated"] is False


async def test_a_quoted_phrase_is_the_phrase(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        found = await _call(client, "contains", phrase='"frame card"')
    assert found["total"] == 4


async def test_a_model_that_fails_to_load_says_why(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from latent_records_mcp.embedders import base
    from mcp import Client

    def offline(options: Any) -> Any:
        raise OSError("cannot reach the model hub")

    monkeypatch.setattr(base, "_LOADED", {})
    monkeypatch.setitem(base.KINDS, "hash", offline)
    async with Client(build_server(store)) as client:
        failed = await client.call_tool(
            "search", {"query": "comb", "space": "hivewright", "mode": "vector"}
        )
    assert failed.is_error and "cannot reach the model hub" in failed.content[0].text


# -- keys ---------------------------------------------------------------------


def _ids(store: Store) -> set[str]:
    ids: set[str] = set()
    for table, column in (
        ("records", "record_id"),
        ("chunks", "id"),
        ("figures", "id"),
        ("tables", "id"),
    ):
        ids |= {
            str(r[column])
            for r in store.backend.read(table, columns=[column]).to_pylist()
        }
    return ids


def _bare(text: str, ids: set[str]) -> list[str]:
    """Ids that appear in `text` without the store's prefix in front."""
    return sorted(
        i for i in ids if re.search(rf"(?<!{STORE_ID}:){re.escape(i)}(?![\w:-])", text)
    )


async def test_the_server_is_named_by_its_store_id(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        assert client.server_info.name == STORE_ID
        assert f"{STORE_ID}:<id>" in str(client.instructions)


async def test_no_reply_names_a_bare_key(store: Store) -> None:
    from mcp import Client

    ids = _ids(store)
    async with Client(build_server(store)) as client:
        replies = {
            "search": await _text(
                client,
                "search",
                query="frame card",
                space="hivewright",
                expand_refs=True,
            ),
            "figures": await _text(
                client,
                "search",
                query="comb",
                target="figure_captions",
                space="hivewright",
                expand_refs=True,
            ),
            "contains": await _text(client, "contains", phrase="frame card"),
            "chunk": await _text(client, "get", id="comb-ledger-a::chunk::0001"),
            "record": await _text(client, "get", id="comb-ledger-a"),
            "describe": await _text(client, "describe"),
        }
        named = await client.call_tool(
            "get", {"id": f"{STORE_ID}:comb-ledger-a::chunk::9999"}
        )
    for name, text in replies.items():
        assert _bare(text, ids) == [], name
    hits = json.loads(replies["search"])["hits"]
    assert any(h["refs"] for h in hits)
    assert all(r.startswith(f"{STORE_ID}:") for h in hits for r in h["refs"])
    found = json.loads(replies["contains"])
    assert found["phrase"] == "frame card"
    assert all(k.startswith(f"{STORE_ID}:") for k in found["by_record"])
    assert named.is_error and STORE_ID not in named.content[0].text


async def test_a_key_is_read_bare_or_as_replies_name_it(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        bare = await _text(client, "get", id="comb-ledger-a::chunk::0001")
        named = await _text(client, "get", id=f"{STORE_ID}:comb-ledger-a::chunk::0001")
        assert named == bare
        other = await client.call_tool("get", {"id": "other:comb-ledger-a"})
        assert other.is_error
        within = json.loads(
            await _text(
                client,
                "contains",
                phrase="frame card",
                record_id=f"{STORE_ID}:comb-ledger-a",
            )
        )
        assert within["total"] > 0
        assert set(within["by_record"]) == {f"{STORE_ID}:comb-ledger-a"}


def test_a_store_without_an_id_is_not_served(store: Store) -> None:
    stamp = store.root / "store.json"
    data = json.loads(stamp.read_text())
    del data["store_id"]
    stamp.write_text(json.dumps(data))
    with pytest.raises(FormatError, match="store_id"):
        build_server(Store.open(store.root))


# -- resources ----------------------------------------------------------------


async def test_each_record_is_a_resource(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        listed = (await client.list_resources()).resources
        read = await client.read_resource("comb-ledger-a")
    assert [str(r.uri) for r in listed] == ["comb-ledger-a", "hivewright-b"]
    titles = {
        r["record_id"]: r["title"] for r in store.backend.read("records").to_pylist()
    }
    assert {str(r.uri): r.name for r in listed} == titles
    assert all(r.description and len(r.description) <= DESCRIPTION_MAX for r in listed)
    assert all(r.mime_type == "text/markdown" for r in listed)
    text = read.contents[0].text  # type: ignore[union-attr]
    assert "frame card" in text and len(text.split()) > 50


async def test_the_listing_is_capped_and_says_what_it_cut(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp import Client

    monkeypatch.setattr(serve, "RESOURCES_MAX", 1)
    monkeypatch.setattr(serve, "DESCRIPTION_MAX", 20)
    async with Client(build_server(store)) as client:
        listed = (await client.list_resources()).resources
        described = await _call(client, "describe")
        instructions = str(client.instructions)
    assert [str(r.uri) for r in listed] == ["comb-ledger-a"]
    assert described["resources"] == {"listed": 1, "records": 2}
    assert "1 of 2 records" in instructions
    assert all((r.description or "").endswith("…") for r in listed)
    assert all(len(r.description or "") <= 20 for r in listed)


# -- what a tool declares -----------------------------------------------------

DECLARED = {
    "describe": ({}, "no arguments"),
    "search": (
        {"query": "comb", "space": "hivewright"},
        "expand_refs, limit, mode, query, record_id, space, target",
    ),
    "contains": ({"phrase": "frame card"}, "phrase, record_id, tables"),
    "get": ({"id": "comb-ledger-a"}, "id"),
}


@pytest.mark.parametrize("tool", sorted(DECLARED))
async def test_an_argument_no_tool_declares_is_refused(store: Store, tool: str) -> None:
    from mcp import Client

    arguments, takes = DECLARED[tool]
    async with Client(build_server(store)) as client:
        fine = await client.call_tool(tool, arguments)
        refused = await client.call_tool(
            tool, {**arguments, "recordid": f"{STORE_ID}:comb-ledger-a", "tag": "x"}
        )
    assert not fine.is_error, fine.content
    assert refused.is_error
    [content] = refused.content
    assert content.text == f"{tool} has no 'recordid', 'tag'; it takes {takes}"


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
