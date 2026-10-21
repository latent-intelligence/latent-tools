"""Fixtures: the committed fixture store, copied so a test may change its stamp."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from latent_records_mcp.store import Store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "deployments"
NAME = "brindlewick-hives"
STORE_ID = NAME


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """The fixture deployment's folder: declaration beside its built `store/`."""
    shutil.copytree(FIXTURE / NAME, tmp_path / NAME)
    return tmp_path / NAME


@pytest.fixture
def store(folder: Path) -> Store:
    return Store.open(folder / "store")
