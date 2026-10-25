"""The tools answer latent-records' `records-tools` contract: these names, these
arguments, a refusal of one line, and `serverInfo` naming the store and this package's
version. The table here is the contract as this member knows it."""

from __future__ import annotations

import pytest
from latent_records_mcp import __version__
from latent_records_mcp.serve import build_server
from latent_records_mcp.store import Store

from .conftest import STORE_ID

pytestmark = pytest.mark.anyio

CONTRACT: dict[str, tuple[set[str], list[str]]] = {
    "describe": (set(), []),
    "search": (
        {"query", "target", "space", "mode", "limit", "record_id", "expand_refs"},
        ["query"],
    ),
    "contains": ({"phrase", "record_id", "tables"}, ["phrase"]),
    "get": ({"id"}, ["id"]),
}


async def test_the_tools_are_the_contract_s(store: Store) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        tools = (await client.list_tools()).tools
    declared = {
        t.name: (
            set(t.input_schema.get("properties") or {}),
            list(t.input_schema.get("required") or []),
        )
        for t in tools
    }
    assert declared == CONTRACT
    assert all(t.description for t in tools)


async def test_server_info_names_the_store_and_this_package_s_version(
    store: Store,
) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        assert client.server_info.name == STORE_ID
        assert client.server_info.version == __version__


@pytest.mark.parametrize(
    ("name", "arguments", "said"),
    [
        ("search", {"query": "frame", "limit": "ten"}, "search: limit: "),
        (
            "search",
            {"query": "frame", "space": "hivewright", "record_id": "nope"},
            "no record 'nope' in",
        ),
        ("contains", {"phrase": "frame", "record_id": "nope"}, "no record 'nope' in"),
        ("search", {"query": "  "}, "give a query"),
    ],
)
async def test_a_refusal_is_one_line_with_is_error(
    store: Store, name: str, arguments: dict[str, object], said: str
) -> None:
    from mcp import Client

    async with Client(build_server(store)) as client:
        result = await client.call_tool(name, arguments)
    assert result.is_error
    [content] = result.content
    text = content.text  # type: ignore[union-attr]
    assert said in text
    assert "\n" not in text
