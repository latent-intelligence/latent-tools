"""The tools answer latent-wiki's `wiki-tools` contract: these names, these arguments,
in this order. The table here is the contract as this member knows it; a change in the
copied reader that moves it fails here first."""

from __future__ import annotations

from latent_wiki_mcp.reader import TOOLS

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
    ("wiki_neighbors", {"key"}, ["key"]),
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
