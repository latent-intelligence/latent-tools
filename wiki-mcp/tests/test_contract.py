"""The tools answer latent-wiki's `wiki-tools` contract: these names, these arguments,
in this order, and a refusal is one line with `isError` set, no footer and no path. The
table here is the contract as this member knows it; a change in the copied reader that
moves it fails here first."""

from __future__ import annotations

import pytest
from latent_wiki_mcp.reader import TOOLS, WikiReader
from latent_wiki_mcp.serve import build_server

pytestmark = pytest.mark.anyio

CONTRACT: list[tuple[str, set[str], list[str]]] = [
    ("wiki_overview", set(), []),
    ("wiki_index", {"type", "part_of", "offset", "limit"}, []),
    (
        "wiki_search",
        {
            "query",
            "type",
            "tag",
            "part_of",
            "status",
            "where",
            "include_retired",
            "include_drafts",
            "limit",
        },
        ["query"],
    ),  # fmt: skip
    ("wiki_get", {"key", "section", "offset"}, ["key"]),
    ("wiki_neighbors", {"key", "offset"}, ["key"]),
    ("wiki_trace", {"key"}, ["key"]),
    ("wiki_evidence", {"key", "entry", "offset"}, ["key"]),
    ("wiki_raw", {"key", "offset", "max_chars"}, ["key"]),
    (
        "wiki_view",
        {"root_type", "parent", "order", "fields", "root", "depth", "include_retired"},
        ["root_type", "parent"],
    ),
]


def test_the_tools_are_the_contract_s() -> None:
    declared = [
        (name, set(schema.get("properties") or {}), list(schema.get("required") or []))
        for name, _, schema in TOOLS
    ]
    assert declared == CONTRACT


def test_every_tool_has_a_description() -> None:
    assert all(description.strip() for _, description, _ in TOOLS)


@pytest.mark.parametrize(
    ("name", "arguments", "said"),
    [
        ("wiki_get", {"key": "limpets"}, "no page 'limpets' in heronsgate"),
        ("wiki_neighbors", {"key": "limpets"}, "no page 'limpets' in heronsgate"),
        ("wiki_search", {"query": "limpets", "status": "actve"}, "no status 'actve'"),
        ("wiki_index", {"limit": 0}, "'limit' must be 1 or more"),
        ("wiki_neighbors", {"key": "tide-pool", "offset": -1}, "'offset'"),
    ],
)
async def test_a_refusal_is_one_line_with_is_error_and_no_footer(
    reader: WikiReader, name: str, arguments: dict[str, object], said: str
) -> None:
    from mcp import Client

    async with Client(build_server(reader)) as client:
        result = await client.call_tool(name, arguments)
    assert result.is_error
    [content] = result.content
    text = content.text  # type: ignore[union-attr]
    assert said in text
    assert "\n" not in text
    assert "cut:" not in text
    assert "/" not in text
