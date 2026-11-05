"""A store and its context stamped with their new `wiki.*` names read as the old
ones do: latent-wiki renames its formats, and every reader takes both first."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from latent_wiki_mcp import formats
from latent_wiki_mcp.reader import WikiReader

FORMATS = Path(__file__).resolve().parent / "fixtures" / "formats"


def renamed(tmp_path: Path) -> tuple[Path, Path]:
    wiki, context = tmp_path / "wiki", tmp_path / "context"
    shutil.copytree(FORMATS / "wiki-store", wiki)
    shutil.copytree(FORMATS / "context-store", context)
    for path in [*wiki.rglob("*"), *context.rglob("*")]:
        if path.suffix in (".json", ".yaml") and path.is_file():
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace('"latent-wiki.', '"wiki.').replace(
                    "format: latent-wiki.", "format: wiki."
                ),
                encoding="utf-8",
                newline="\n",
            )
    assert "latent-wiki." not in (wiki / "manifest.json").read_text(encoding="utf-8")
    return wiki, context


@pytest.mark.parametrize("name", ["store", "context"])
def test_each_format_reads_both_names(name: str) -> None:
    kind = getattr(formats, name.upper())
    assert kind.matches({"format": f"latent-wiki.{name}", "format_version": 1})
    assert kind.matches({"format": f"wiki.{name}", "format_version": 1})


def test_a_store_and_context_stamped_with_the_new_names_are_served(
    tmp_path: Path,
) -> None:
    wiki, context = renamed(tmp_path)
    reader = WikiReader(str(wiki), context=str(context))
    assert reader.overview()
    assert reader._check_context() == ""
    assert reader.search("limpet")
