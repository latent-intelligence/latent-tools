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


def test_readme_install_lines_name_each_members_current_release() -> None:
    readmes = [ROOT / "README.md", *(m / "README.md" for m in members())]
    for member in members():
        version = tomllib.loads((member / "pyproject.toml").read_text())["project"][
            "version"
        ]
        for readme in readmes:
            tags = re.findall(rf"{member.name}-v(\d+(?:\.\d+)*)", readme.read_text())
            stale = [t for t in tags if t != version]
            assert not stale, (
                f"{readme.relative_to(ROOT)} installs {member.name} at {stale}, "
                f"but its pyproject.toml says {version}"
            )


def test_every_members_version_is_the_same_in_its_package() -> None:
    """`--version` reports `__version__`, so it must say what pyproject.toml says."""
    for member in members():
        version = tomllib.loads((member / "pyproject.toml").read_text())["project"][
            "version"
        ]
        inits = list((member / "src").glob("*/__init__.py"))
        assert len(inits) == 1, inits
        found = re.search(r'__version__ = "([^"]+)"', inits[0].read_text())
        assert found and found.group(1) == version, (
            f"{inits[0].relative_to(ROOT)} says {found and found.group(1)}, "
            f"but pyproject.toml says {version}"
        )
