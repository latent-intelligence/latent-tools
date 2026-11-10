"""The copied files are the store's, byte for byte, below their three-line header, at
the commit the header names.

Runs where `KB_STORE_SRC` names a kb-store checkout, or its `src/kb_store`; skipped
elsewhere. This is how drift is seen on the machine that syncs the copy.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "src" / "latent_records_mcp"
VERBATIM = (
    "serve.py",
    "search.py",
    "schema.py",
    "formats.py",
    "store_json.py",
    "locations.py",
    "errors.py",
    "variables.py",
    "backends/base.py",
    "backends/lance.py",
    "embedders/base.py",
    "embedders/windowed.py",
    "embedders/hashing.py",
    "embedders/open_clip.py",
    "embedders/sentence_transformers.py",
)
HEADER_LINES = 4  # three comment lines and a blank one


@pytest.fixture
def source() -> Path:
    where = os.environ.get("KB_STORE_SRC")
    if not where:
        pytest.skip("KB_STORE_SRC is not set")
    path = Path(where)
    return path / "src" / "kb_store" if (path / "src").is_dir() else path


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
    assert copied[0].startswith("# Verbatim copy of kb-store at ")
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
