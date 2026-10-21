"""Every member carries the same `mcp` pin, so the servers agree on the protocol."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = "mcp>=2.0,<3"


def members() -> list[Path]:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return [ROOT / m for m in root["tool"]["uv"]["workspace"]["members"]]


def test_every_member_pins_mcp_the_same_way() -> None:
    assert members(), "no workspace members"
    for member in members():
        project = tomllib.loads((member / "pyproject.toml").read_text())
        pins = [
            d
            for d in project["project"]["dependencies"]
            if re.match(r"mcp\s*[<>=!~]", d)
        ]
        assert pins == [PIN], f"{member.name} pins mcp as {pins}, not {PIN!r}"


def test_every_member_is_a_latent_distribution_with_a_matching_command() -> None:
    for member in members():
        project = tomllib.loads((member / "pyproject.toml").read_text())["project"]
        assert project["name"].startswith("latent-"), project["name"]
        assert list(project["scripts"]) == [project["name"]], project["scripts"]
