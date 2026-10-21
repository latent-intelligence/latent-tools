"""Fixtures: the format samples with invented names, as a reader and as a deployment."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from latent_wiki_mcp.reader import WikiReader

FORMATS = Path(__file__).resolve().parent / "fixtures" / "formats"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def reader(tmp_path: Path) -> WikiReader:
    """The format fixture, with `count-limpets` placed under `tide-pool`."""
    shutil.copytree(FORMATS / "wiki-store", tmp_path / "wiki")
    shutil.copytree(FORMATS / "context-store", tmp_path / "context")
    manifest = tmp_path / "wiki" / "manifest.json"
    data = json.loads(manifest.read_text())
    for entry in data["pages"]:
        if entry["key"] == "count-limpets":
            entry["frontmatter"]["part_of"] = "tide-pool"
    manifest.write_text(json.dumps(data))
    return WikiReader(str(tmp_path / "wiki"), context=str(tmp_path / "context"))


@pytest.fixture
def deployment(tmp_path: Path) -> Path:
    """A deployment around the format fixtures: declaration, config, wiki, context."""
    root = tmp_path / "survey"
    shutil.copytree(FORMATS / "wiki-store", root / "wiki")
    shutil.copytree(FORMATS / "context-store", root / "context")
    shutil.copy(root / "wiki" / "wiki.yaml", root / "wiki.yaml")
    (root / "survey.yaml").write_text("schema_version: 1\nwiki: {config: wiki.yaml}\n")
    return root
