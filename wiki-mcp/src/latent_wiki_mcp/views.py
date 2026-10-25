# Verbatim copy of latent-wiki at 28db78d, below this header:
#   src/latent_wiki/views.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Views — a generic tree over a store's frontmatter, named by the caller.

The engine knows how to draw a tree; the caller says what the tree is made of: which
page type sits at the top, which field points at a parent, which orders siblings, and
which fields ride along. No field name lives here, so a second store with a different
vocabulary is a different `wiki_view` call, not a code change.

A view is a rung beside search, not a replacement for it: it answers "what are the
steps of X, in order, and who does them" in one call, where search answers "which page
is about X".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class View:
    """A tree: pages of `root_type` at the top, children linked by the `parent` field,
    siblings ordered by `order`, with `fields` carried into each node."""

    name: str
    root_type: str
    parent: str
    order: str = ""
    fields: tuple[str, ...] = ()


#: Lines one rendered view may carry before it says what it cut.
MAX_LINES = 400

#: Keys one trailing list (orphans, unreached, empty roots) may name before it counts.
MAX_NAMES = 50


@dataclass
class Node:
    key: str
    title: str
    fields: dict[str, Any] = field(default_factory=dict)
    order: Any = None
    status: str = ""
    children: list[Node] = field(default_factory=list)


@dataclass
class Tree:
    view: View
    roots: list[Node]
    #: Pages naming a parent that is not a page.
    orphans: list[str]
    #: Pages with a parent that no root reaches — under a page of another type, or in a
    #: parent cycle. Reported so a view never silently leaves pages out.
    unreached: list[str] = field(default_factory=list)

    @property
    def empty_roots(self) -> list[str]:
        return [r.key for r in self.roots if not r.children]


def build(view: View, pages: dict[str, tuple[str, dict[str, Any]]]) -> Tree:
    """A tree from `key -> (title, frontmatter)`. Orphans are reported, not dropped."""
    nodes = {
        key: Node(
            key=key,
            title=title,
            fields={f: raw[f] for f in view.fields if raw.get(f) not in (None, "", [])},
            order=raw.get(view.order) if view.order else None,
            status=str(raw.get("status") or ""),
        )
        for key, (title, raw) in pages.items()
    }
    orphans: list[str] = []
    for key, (_, raw) in pages.items():
        parent = raw.get(view.parent)
        if not parent:
            continue
        if str(parent) in nodes:
            nodes[str(parent)].children.append(nodes[key])
        else:
            orphans.append(key)

    roots = [
        nodes[key]
        for key, (_, raw) in pages.items()
        if str(raw.get("type") or "") == view.root_type and not raw.get(view.parent)
    ]
    roots.sort(key=lambda n: n.title)
    reached: set[str] = set()

    def visit(node: Node) -> None:
        if node.key in reached:  # a cycle reached from a root stops here
            return
        reached.add(node.key)
        node.children.sort(key=_sibling_order)
        for child in node.children:
            visit(child)

    for root in roots:
        visit(root)
    unreached = sorted(
        key
        for key, (_, raw) in pages.items()
        if raw.get(view.parent) and key not in reached and key not in orphans
    )
    return Tree(view=view, roots=roots, orphans=sorted(orphans), unreached=unreached)


def _sibling_order(node: Node) -> tuple[int, Any, str]:
    """Integer orders first, in order; then any other order as text; then none."""
    if isinstance(node.order, int) and not isinstance(node.order, bool):
        return (0, node.order, node.title)
    if node.order not in (None, ""):
        return (1, str(node.order), node.title)
    return (2, "", node.title)


def render(
    tree: Tree,
    root: str = "",
    depth: int = 0,
    named: Callable[[str], str] = str,
) -> tuple[str, str]:
    """The tree as indented text — one root by key, or all of them — and what was cut.
    `named` is how a page's key is printed.

    Capped twice: the tree at `MAX_LINES`, and each trailing list at `MAX_NAMES`, so a
    wrong `root_type` over a large store cannot return every key in one line.
    """
    chosen = [r for r in tree.roots if not root or r.key == root]
    if root and not chosen:
        found = _find(tree.roots, root)
        chosen = [found] if found else []
    if root and not chosen:
        return f"no node '{root}' in view '{tree.view.name}'", ""
    lines: list[str] = []

    seen: set[str] = set()

    def walk(node: Node, level: int) -> None:
        if node.key in seen:
            lines.append(f"{'  ' * level}- … {named(node.key)} (cycle)")
            return
        seen.add(node.key)
        step = f"{node.order}. " if isinstance(node.order, int) else ""
        draft = " · draft" if node.status == "draft" else ""
        lines.append(f"{'  ' * level}- {step}{node.title} ({named(node.key)}){draft}")
        for name, value in node.fields.items():
            shown = ", ".join(map(str, value)) if isinstance(value, list) else value
            lines.append(f"{'  ' * (level + 1)}{name}: {shown}")
        if depth and level + 1 >= depth:
            if node.children:
                lines.append(f"{'  ' * (level + 1)}… {len(node.children)} beneath")
            return
        for child in node.children:
            walk(child, level + 1)

    for node in chosen:
        walk(node, 0)
    cuts = []
    if len(lines) > MAX_LINES:
        cuts.append(f"{MAX_LINES} of {len(lines)} lines; pass root=<key> or depth=<n>")
        lines = lines[:MAX_LINES]
    tail = [f"\n{len(chosen)} root(s)"]
    for label, names in (
        ("named but not broken down", [] if root else tree.empty_roots),
        ("orphans (parent not a page)", tree.orphans),
        ("not reached from any root", [] if root else tree.unreached),
    ):
        if not names:
            continue
        more = len(names) - MAX_NAMES
        tail.append(
            f"{label}: {', '.join(map(named, names[:MAX_NAMES]))}"
            + (f" … and {more} more" if more > 0 else "")
        )
        if more > 0:
            cuts.append(f"{label}: {MAX_NAMES} of {len(names)} named")
    return "\n".join(lines) + "\n".join(tail), "; ".join(cuts)


def _find(nodes: list[Node], key: str) -> Node | None:
    for node in nodes:
        if node.key == key:
            return node
        if found := _find(node.children, key):
            return found
    return None
