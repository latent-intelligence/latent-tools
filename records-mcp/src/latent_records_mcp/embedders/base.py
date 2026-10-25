# Verbatim copy of latent-records at e9eb62a, below this header:
#   src/latent_records/embedders/base.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The embedding seam: what a space needs from a model, and how one is loaded.

An embedder takes text, images or both, and returns L2-normalised float32 rows. Kinds
are a small registry loaded lazily, so the base install never imports torch; a kind
not installed is an error naming the install line, under the name this package is
installed as.

A model fetched on first use is announced on the log before the fetch and after it,
with its size when the hub reports one: a first search can wait minutes for weights.

The device (``cpu``, ``cuda``, ``mps``) is a property of the machine, never of a
deployment: ``LATENT_RECORDS_DEVICE``, or the best available.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..errors import EmbedderError

log = logging.getLogger(__name__)

ENV_DEVICE = "LATENT_RECORDS_DEVICE"

#: The distribution this code is installed as, read from the package it sits in, so
#: an install line names the package a user would actually install.
DISTRIBUTION = __name__.split(".")[0].replace("_", "-")


@runtime_checkable
class TextEmbedder(Protocol):
    id: str
    dims: int
    max_tokens: int

    def count_tokens(self, text: str) -> int: ...

    def embed_texts(self, texts: Sequence[str], *, query: bool = False) -> np.ndarray:
        """(n, dims) float32, each row unit length."""


@runtime_checkable
class ImageEmbedder(Protocol):
    id: str
    dims: int

    def embed_images(self, images: Sequence[bytes]) -> np.ndarray:
        """(n, dims) float32, each row unit length."""


def normalise(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def device() -> str:
    chosen = os.environ.get(ENV_DEVICE)
    if chosen:
        return chosen
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _hash(options: Mapping[str, Any]) -> Any:
    from .hashing import HashEmbedder

    return HashEmbedder(int(options["dims"]))


def _open_clip(options: Mapping[str, Any]) -> Any:
    from .open_clip import OpenClipEmbedder

    return OpenClipEmbedder(str(options["model"]), str(options["pretrained"]), device())


def _sentence_transformers(options: Mapping[str, Any]) -> Any:
    from .sentence_transformers import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder(
        str(options["model"]),
        revision=options.get("revision"),
        query_prefix=str(options.get("query_prefix") or ""),
        document_prefix=str(options.get("document_prefix") or ""),
        device=device(),
    )


KINDS: dict[str, Callable[[Mapping[str, Any]], Any]] = {
    "hash": _hash,
    "open-clip": _open_clip,
    "sentence-transformers": _sentence_transformers,
}


@dataclass(frozen=True)
class Download:
    """What loading a model fetches first: its name, and its size in bytes if known."""

    name: str
    size: int | None = None


def hub_download(
    repo: str, revision: str | None, weights: Sequence[str]
) -> Download | None:
    """A Hugging Face repo that loading would fetch, sized by the first of `weights`
    it holds; None when it is cached, the hub is off, or `repo` is not a hub id."""
    # Imported by name: the package loads its submodules lazily, as attributes.
    try:
        from huggingface_hub import HfApi, constants, snapshot_download
    except ImportError:
        return None
    if constants.HF_HUB_OFFLINE:
        return None
    try:
        snapshot_download(repo, revision=revision, local_files_only=True)
        return None
    except FileNotFoundError:
        pass
    except Exception:
        return None
    try:
        info = HfApi().model_info(
            repo, revision=revision, files_metadata=True, timeout=10
        )
        sizes = {s.rfilename: s.size for s in info.siblings or ()}
    except Exception:
        sizes = {}
    return Download(repo, next((sizes[w] for w in weights if sizes.get(w)), None))


def _sentence_transformers_download(options: Mapping[str, Any]) -> Download | None:
    from .sentence_transformers import download

    return download(str(options["model"]), options.get("revision"))


def _open_clip_download(options: Mapping[str, Any]) -> Download | None:
    from .open_clip import download

    return download(str(options["model"]), str(options["pretrained"]))


#: What a kind fetches before it loads, when anything: a kind with none never does.
DOWNLOADS: dict[str, Callable[[Mapping[str, Any]], Download | None]] = {
    "open-clip": _open_clip_download,
    "sentence-transformers": _sentence_transformers_download,
}


def install_line(extra: str | None) -> str:
    """What to install for an extra, as this package is named."""
    return f"{DISTRIBUTION}[{extra}]"


def missing_install(recipe: Mapping[str, Any]) -> str | None:
    """The install line a recipe's kind needs and this install lacks, or None."""
    from ..deployment import EMBEDDERS

    kind = EMBEDDERS.get(str(recipe.get("embedder")))
    if kind is None or not kind.module or importlib.util.find_spec(kind.module):
        return None
    return install_line(kind.extra)


def _pending(recipe: Mapping[str, Any], options: Mapping[str, Any]) -> Download | None:
    """What loading would fetch first. A kind not installed fetches nothing, and a
    check that fails announces nothing: it never stops a model loading."""
    check = DOWNLOADS.get(str(recipe.get("embedder")))
    if check is None or missing_install(recipe) is not None:
        return None
    try:
        return check(options)
    except Exception as exc:
        log.debug("could not tell whether loading downloads: %s", exc)
        return None


def _size(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


_LOADED: dict[str, Any] = {}

_LOADING = threading.Lock()


def load_embedder(recipe: Mapping[str, Any]) -> Any:
    """The embedder a recipe names, loaded once per process.

    A recipe is a space's ``recipe()`` or a space entry from ``store.json``. Only the
    options its kind declares identify the model: a window, or a store's row counts,
    do not, so two spaces differing only in window share one loaded model.
    """
    kind = recipe.get("embedder")
    if kind not in KINDS:
        raise EmbedderError(f"unknown embedder {kind!r}; known: {sorted(KINDS)}")
    from ..deployment import EMBEDDERS

    declared = EMBEDDERS[str(kind)].required | EMBEDDERS[str(kind)].optional
    options = {k: v for k, v in recipe.items() if k in declared}
    key = json.dumps({"embedder": kind, **options}, sort_keys=True, default=str)
    if key in _LOADED:
        return _LOADED[key]
    with _LOADING:
        if key not in _LOADED:
            try:
                fetch = _pending(recipe, options)
                if fetch:
                    started = time.monotonic()
                    log.info(
                        "downloading %s%s before loading it",
                        fetch.name,
                        f" ({_size(fetch.size)})" if fetch.size else "",
                    )
                _LOADED[key] = KINDS[str(kind)](options)
                if fetch:
                    log.info(
                        "downloaded %s in %.0f s",
                        fetch.name,
                        time.monotonic() - started,
                    )
            except ImportError as exc:
                extra = EMBEDDERS[str(kind)].extra
                raise EmbedderError(
                    f"{kind} is not installed; install {install_line(extra)} ({exc})"
                ) from exc
            except EmbedderError:
                raise
            except Exception as exc:
                raise EmbedderError(
                    f"could not load {options.get('model', kind)}: {exc}"
                ) from exc
        return _LOADED[key]
