# Verbatim copy of latent-records at b7ca93c, below this header:
#   src/latent_records/embedders/base.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""The embedding seam: what a space needs from a model, and how one is loaded.

An embedder takes text, images or both, and returns L2-normalised float32 rows. Kinds
are a small registry loaded lazily, so the base install never imports torch; a kind
not installed is an error naming the extra to add.

The device (``cpu``, ``cuda``, ``mps``) is a property of the machine, never of a
deployment: ``LATENT_RECORDS_DEVICE``, or the best available.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..errors import EmbedderError

ENV_DEVICE = "LATENT_RECORDS_DEVICE"


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
                _LOADED[key] = KINDS[str(kind)](options)
            except ImportError as exc:
                extra = EMBEDDERS[str(kind)].extra
                raise EmbedderError(
                    f"{kind} is not installed; install the {extra!r} extra ({exc})"
                ) from exc
            except EmbedderError:
                raise
            except Exception as exc:
                raise EmbedderError(
                    f"could not load {options.get('model', kind)}: {exc}"
                ) from exc
        return _LOADED[key]
