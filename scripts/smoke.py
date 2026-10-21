"""The clean-environment test: install one member from this checkout into a fresh uv
tool environment, as a colleague would from the git URL, then talk to it over stdio.

    uv run python scripts/smoke.py wiki-mcp

Uses the same `git+file://…#subdirectory=<member>` mechanism the install spike proved,
so it needs no token and runs in CI. Installs into a temporary UV_TOOL_DIR; nothing on
the machine changes. Exits non-zero on the first failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Smoke:
    deployment: Callable[[Path], Path]
    """Makes a deployment folder to serve, under the directory given."""
    call: tuple[str, dict[str, Any]]
    """A tool call whose reply must contain `expect`."""
    expect: str


def _wiki(into: Path) -> Path:
    formats = ROOT / "wiki-mcp" / "tests" / "fixtures" / "formats"
    root = into / "survey"
    shutil.copytree(formats / "wiki-store", root / "wiki")
    shutil.copytree(formats / "context-store", root / "context")
    (root / "survey.yaml").write_text("schema_version: 1\nwiki: {config: wiki.yaml}\n")
    return root


SMOKE: dict[str, Smoke] = {
    "wiki-mcp": Smoke(
        _wiki, ("wiki_search", {"query": "limpets"}), "heronsgate:count-limpets"
    ),
}


def install(member: str, tool_dir: Path, bin_dir: Path) -> Path:
    project = tomllib.loads((ROOT / member / "pyproject.toml").read_text())["project"]
    name = str(project["name"])
    url = f"git+file://{ROOT}#subdirectory={member}"
    env = {**os.environ, "UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(bin_dir)}
    print(f"uv tool install {url}")
    subprocess.run(["uv", "tool", "install", url], check=True, env=env)
    command = bin_dir / name
    if not command.exists():
        sys.exit(f"{command} was not installed; the entry point is not {name}")
    return command


async def handshake(command: Path, folder: Path, smoke: Smoke) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(command), args=["--deployment", str(folder)]
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        name, arguments = smoke.call
        result = await session.call_tool(name, arguments)
    text = str(getattr(result.content[0], "text", ""))
    print(f"server {init.server_info.name}: {len(tools)} tools; {name} answered")
    if smoke.expect not in text:
        sys.exit(f"{name} did not name {smoke.expect!r}:\n{text}")


def main(member: str) -> None:
    if member not in SMOKE:
        sys.exit(f"no smoke for {member!r}; known: {sorted(SMOKE)}")
    smoke = SMOKE[member]
    dirty = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", member],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        print(f"note: {member} has uncommitted changes; a git install sees HEAD")
    with tempfile.TemporaryDirectory() as tmp:
        where = Path(tmp)
        command = install(member, where / "tools", where / "bin")
        out = subprocess.run(
            [command, "--deployment", str(smoke.deployment(where)), "--print-config"],
            capture_output=True,
            text=True,
            check=True,
        )
        block = json.loads(out.stdout)["mcpServers"]
        print(f"--print-config: {list(block)}")
        asyncio.run(handshake(command, smoke.deployment(where / "again"), smoke))
    print(f"smoke {member}: ok")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
