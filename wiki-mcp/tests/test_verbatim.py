"""The copied files are the store's, byte for byte, below their two-line header, at the
commit the header names.

Runs where `LATENT_WIKI_SRC` names a latent-wiki checkout's `src/latent_wiki`; skipped
elsewhere. This is how drift is seen on the machine that syncs the copy: the header's
commit is read from that checkout's history, so a stale header fails even when the
working tree happens to match.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "src" / "latent_wiki_mcp"
VERBATIM = ("reader.py", "_fts.py", "views.py", "formats.py", "serve.py")
HEADER_LINES = 3  # two comment lines and a blank one


@pytest.fixture
def source() -> Path:
    where = os.environ.get("LATENT_WIKI_SRC")
    if not where:
        pytest.skip("LATENT_WIKI_SRC is not set")
    return Path(where)


def _at_commit(source: Path, commit: str, name: str) -> str:
    shown = subprocess.run(
        ["git", "-C", str(source), "show", f"{commit}:src/latent_wiki/{name}"],
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0, shown.stderr
    return shown.stdout


@pytest.mark.parametrize("name", VERBATIM)
def test_copy_is_byte_identical_below_its_header(source: Path, name: str) -> None:
    copied = (HERE / name).read_text().split("\n", HEADER_LINES)
    assert copied[0].startswith("# Verbatim copy of latent-wiki")
    assert copied[1].startswith("# sync it from the store")
    assert copied[2] == ""
    commit = copied[0].split(" at ")[1].split(".")[0]
    assert copied[3] == _at_commit(source, commit, name)


def test_the_header_names_the_commit_the_changelog_names() -> None:
    changelog = (HERE.parents[1] / "CHANGELOG.md").read_text()
    for name in VERBATIM:
        first = (HERE / name).read_text().splitlines()[0]
        commit = first.split(" at ")[1].split(".")[0]
        assert commit in changelog, f"{name} is from {commit}, not in CHANGELOG.md"
