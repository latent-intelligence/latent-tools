"""The tools answer latent-records' `records-tools` contract: these names, these
arguments. The table here is the contract as this member knows it."""

from __future__ import annotations

import pytest
from latent_records_mcp.serve import build_server
from latent_records_mcp.store import Store

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
