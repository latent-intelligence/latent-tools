"""The copied files are the store's, byte for byte, below their three-line header, at
the commit the header names.

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
HEADER_LINES = 4  # three comment lines and a blank one


@pytest.fixture
def source() -> Path:
    where = os.environ.get("LATENT_WIKI_SRC")
    if not where:
        pytest.skip("LATENT_WIKI_SRC is not set")
    return Path(where)


def _at_commit(source: Path, commit: str, path: str) -> str:
    shown = subprocess.run(
        ["git", "-C", str(source), "show", f"{commit}:{path}"],
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0, shown.stderr
    return shown.stdout


@pytest.mark.parametrize("name", VERBATIM)
def test_copy_is_byte_identical_below_its_header(source: Path, name: str) -> None:
    copied = (HERE / name).read_text().split("\n", HEADER_LINES)
    assert copied[0].startswith("# Verbatim copy of latent-wiki at ")
    commit = copied[0].split(" at ")[1].split(",")[0]
    path = copied[1].removeprefix("#   ")
    assert path.endswith(f"/{name}")
    assert copied[2].startswith("# Do not edit")
    assert copied[3] == ""
    assert copied[4] == _at_commit(source, commit, path)


@pytest.mark.parametrize("name", VERBATIM)
def test_the_store_has_not_moved_past_the_header_commit(
    source: Path, name: str
) -> None:
    """The checkout's working file equals the file at the header's commit; otherwise
    the store moved on and the copy is due a sync."""
    first, path = (HERE / name).read_text().splitlines()[:2]
    commit = first.split(" at ")[1].split(",")[0]
    now = (source.parents[1] / path.removeprefix("#   ")).read_text()
    assert now == _at_commit(source, commit, path.removeprefix("#   ")), (
        f"the store moved past {commit} in {name}; sync the copy and its header"
    )


def test_the_header_names_the_commit_the_changelog_names() -> None:
    changelog = (HERE.parents[1] / "CHANGELOG.md").read_text()
    for name in VERBATIM:
        first = (HERE / name).read_text().splitlines()[0]
        commit = first.split(" at ")[1].split(",")[0]
        assert commit in changelog, f"{name} is from {commit}, not in CHANGELOG.md"
