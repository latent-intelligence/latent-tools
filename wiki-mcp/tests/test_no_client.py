"""Package code names no deployment: the fixture's invented names never reach `src/`."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
FIXTURE_NAMES = ("heronsgate", "kelpmoor", "tide-pool", "count-limpets", "survey")


def test_src_names_no_fixture() -> None:
    hits = [
        f"{path.relative_to(SRC)}: {name}"
        for path in SRC.rglob("*.py")
        for name in FIXTURE_NAMES
        if name in path.read_text()
    ]
    assert hits == []
