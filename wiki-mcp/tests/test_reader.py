"""The copied reader's fixes, pinned here as the store's own tests pin them."""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from latent_wiki_mcp import _fts
from latent_wiki_mcp.errors import Refused
from latent_wiki_mcp.reader import WikiReader

from .conftest import FORMATS


def _copied(tmp_path: Path) -> tuple[Path, Path]:
    wiki, context = tmp_path / "wiki", tmp_path / "context"
    shutil.copytree(FORMATS / "wiki-store", wiki)
    shutil.copytree(FORMATS / "context-store", context)
    return wiki, context


def _unstamp(context: Path) -> None:
    descriptor = context / "context-store.yaml"
    descriptor.write_text(
        descriptor.read_text().replace("format: latent-wiki.context\n", "")
    )


def _add_pages(wiki: Path, pages: dict[str, dict[str, Any]]) -> None:
    manifest = wiki / "manifest.json"
    data = json.loads(manifest.read_text())
    for key, frontmatter in pages.items():
        entry = {"key": key, "file": f"{key}.md", "lead": f"Page {key}."}
        data["pages"].append({**entry, "frontmatter": {"key": key, **frontmatter}})
        (wiki / "pages" / f"{key}.md").write_text(
            f"---\nkey: {key}\n---\n\nPage {key}.\n"
        )
    data["page_count"] = len(data["pages"])
    manifest.write_text(json.dumps(data))


def test_neighbors_ignore_links_that_are_not_a_list(tmp_path: Path) -> None:
    """A `links:` written as a string is not read one character at a time."""
    wiki, context = _copied(tmp_path)
    _add_pages(
        wiki,
        {
            "a": {"type": "concept", "links": "bc"},
            "b": {"type": "concept"},
            "c": {"type": "concept"},
        },
    )
    reader = WikiReader(str(wiki), context=str(context))
    assert reader.neighbors("a") == {"out": [], "in": []}
    assert reader.neighbors("b")["in"] == []


def test_raw_refuses_an_unstamped_context(tmp_path: Path) -> None:
    wiki, context = _copied(tmp_path)
    _unstamp(context)
    reader = WikiReader(str(wiki), context=str(context))
    for key in ("tide-pool", "kelpmoor-ch01-pools"):
        with pytest.raises(Refused, match="latent-wiki.context") as refused:
            reader.raw(key)
        assert "Count the limpets." not in str(refused.value)


def test_raw_checks_the_stamp_before_reading_an_entry(tmp_path: Path) -> None:
    wiki, context = _copied(tmp_path)
    (context / "context-store.yaml").unlink()
    reader = WikiReader(str(wiki), context=str(context))
    with pytest.raises(Refused, match="not published") as refused:
        reader.raw("kelpmoor-ch01-pools")
    assert "Count the limpets." not in str(refused.value)
    with pytest.raises(Refused, match="not published"):
        reader.raw("no-such-entry")


def test_raw_refuses_a_declared_source_in_an_unstamped_context(tmp_path: Path) -> None:
    """A `sources[].resource` that resolves into the context store is guarded too."""
    wiki, context = _copied(tmp_path)
    (context / "raw" / "shore-notes.md").write_text("the context copy")
    _add_pages(
        wiki,
        {
            "notes": {
                "type": "concept",
                "sources": [{"resource": "corpus:raw/shore-notes.md"}],
            }
        },
    )
    assert "the context copy" in WikiReader(str(wiki), context=str(context)).raw(
        "notes"
    )
    (context / "context-store.yaml").unlink()
    with pytest.raises(Refused, match="not published") as refused:
        WikiReader(str(wiki), context=str(context)).raw("notes")
    assert "the context copy" not in str(refused.value)


def test_raw_still_reads_a_source_in_the_wiki_store_without_a_stamp(
    tmp_path: Path,
) -> None:
    """The stamp guards the context store; a source in the wiki store needs none."""
    wiki, context = _copied(tmp_path)
    (context / "context-store.yaml").unlink()
    (wiki / "raw").mkdir()
    (wiki / "raw" / "shore-notes.md").write_text("the wiki store's copy")
    _add_pages(
        wiki,
        {"notes": {"type": "concept", "sources": [{"resource": "raw/shore-notes.md"}]}},
    )
    answer = WikiReader(str(wiki), context=str(context)).raw("notes")
    assert "the wiki store's copy" in answer


def test_raw_reads_the_paired_context_once_it_is_stamped(tmp_path: Path) -> None:
    wiki, context = _copied(tmp_path)
    reader = WikiReader(str(wiki), context=str(context))
    for key in ("tide-pool", "kelpmoor-ch01-pools"):
        assert "Count the limpets." in reader.raw(key)


def test_one_index_ranks_from_many_threads_at_once() -> None:
    """A server runs each tool call on a worker thread, so two searches of one store
    can rank at the same moment; each must get the answer it gets alone."""
    rows = [
        _fts.Row(f"page-{i}", {"title": f"tide pool {i} limpets"}) for i in range(50)
    ]
    index = _fts.Index(rows)
    queries = ["tide", "pool limpets", "limpets", "tide pool"] * 100
    alone = {q: index.rank(q) for q in set(queries)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        together = list(pool.map(index.rank, queries))
    assert together == [alone[q] for q in queries]


def test_one_reader_searches_from_many_threads_at_once(tmp_path: Path) -> None:
    """The same, through the store's own search tool, as a server calls it."""
    wiki, context = _copied(tmp_path)
    _add_pages(
        wiki,
        {
            f"pool-{i}": {"type": "concept", "title": f"Tide pool {i} limpets"}
            for i in range(50)
        },
    )
    reader = WikiReader(str(wiki), context=str(context))
    calls = [{"query": q} for q in ("tide", "pool limpets", "limpets", "tide pool")]
    calls *= 100
    alone = {c["query"]: reader.call("wiki_search", c) for c in calls}
    with ThreadPoolExecutor(max_workers=8) as pool:
        together = list(pool.map(lambda c: reader.call("wiki_search", c), calls))
    assert together == [alone[c["query"]] for c in calls]
