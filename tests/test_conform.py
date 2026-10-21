"""Both patterns give the same answers.

A member and the store's own `serve` command, each started over stdio on the same
fixture deployment, are given the same calls and must return identical replies:
footers, `cut:` lines and keys included. Runs under `just conform`, where both commands
are on PATH; each case is skipped when its store command is missing, so `just check`
never needs a store.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.conform]

ROOT = Path(__file__).resolve().parent.parent
FORMATS = ROOT / "wiki-mcp" / "tests" / "fixtures" / "formats"

WIKI_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("wiki_overview", {}),
    ("wiki_index", {}),
    ("wiki_search", {"query": "limpets"}),
    ("wiki_search", {"query": "pool", "type": "concept", "limit": 1}),
    ("wiki_get", {"key": "tide-pool"}),
    ("wiki_get", {"key": "heronsgate:count-limpets", "section": "Steps"}),
    ("wiki_neighbors", {"key": "tide-pool"}),
    ("wiki_trace", {"key": "count-limpets"}),
    ("wiki_evidence", {"key": "count-limpets"}),
    ("wiki_raw", {"key": "count-limpets", "max_chars": 400}),
    ("wiki_view", {"root_type": "concept", "parent": "part_of", "fields": ["aliases"]}),
    ("wiki_get", {"key": "no-such-page"}),
    ("wiki_search", {"query": "limpets", "tags": "shore"}),  # refused by both
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def wiki_deployment(tmp_path: Path) -> Path:
    root = tmp_path / "survey"
    shutil.copytree(FORMATS / "wiki-store", root / "wiki")
    shutil.copytree(FORMATS / "context-store", root / "context")
    shutil.copy(root / "wiki" / "wiki.yaml", root / "wiki.yaml")
    (root / "survey.yaml").write_text("schema_version: 1\nwiki: {config: wiki.yaml}\n")
    manifest = root / "wiki" / "manifest.json"
    data = json.loads(manifest.read_text())
    for entry in data["pages"]:
        if entry["key"] == "count-limpets":
            entry["frontmatter"]["part_of"] = "tide-pool"
    manifest.write_text(json.dumps(data))
    return root


async def _replies(
    command: list[str], calls: list[tuple[str, dict[str, Any]]]
) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command[0], args=command[1:])
    out: dict[str, Any] = {}
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        out["name"] = init.server_info.name
        out["instructions"] = init.instructions
        tools = (await session.list_tools()).tools
        out["tools"] = [(t.name, t.input_schema, t.description) for t in tools]
        out["resources"] = sorted(
            str(r.uri) for r in (await session.list_resources()).resources
        )
        for name, arguments in calls:
            result = await session.call_tool(name, arguments)
            texts = [getattr(c, "text", None) for c in result.content]
            out[f"{name} {json.dumps(arguments, sort_keys=True)}"] = (
                result.is_error,
                texts,
            )
    return out


def _store_command(name: str) -> list[str]:
    found = shutil.which(name)
    if not found:
        pytest.skip(
            f"{name} is not on PATH; `just conform` needs both servers installed"
        )
    return [found]


async def test_wiki_mcp_answers_as_latent_wiki_serve_does(
    wiki_deployment: Path,
) -> None:
    store = [
        *_store_command("latent-wiki"),
        "serve",
        "--deployment",
        str(wiki_deployment),
    ]
    tool = [
        sys.executable,
        "-m",
        "latent_wiki_mcp.cli",
        "--deployment",
        str(wiki_deployment),
    ]
    theirs = await _replies(store, WIKI_CALLS)
    ours = await _replies(tool, WIKI_CALLS)
    assert ours["name"] == theirs["name"] == "heronsgate"
    for key in theirs:
        assert ours[key] == theirs[key], key


def test_the_store_command_is_the_one_documented() -> None:
    """A smoke of the recipe itself: the store command exists and answers --help."""
    command = _store_command("latent-wiki")
    out = subprocess.run([*command, "serve", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "--deployment" in out.stdout
