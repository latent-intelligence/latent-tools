# Verbatim copy of latent-records at e9eb62a, below this header:
#   src/latent_records/embedders/sentence_transformers.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Text models through sentence-transformers. Needs the ``text`` extra."""

from __future__ import annotations

import os
from collections.abc import Sequence

import numpy as np

from ..errors import EmbedderError
from .base import Download, hub_download, normalise

BATCH = 64

TOKEN_BUDGET = 16_384

MAX_PLAUSIBLE_LENGTH = 1_000_000

#: The weights a model loads, in the order the library looks for them.
WEIGHTS = ("model.safetensors", "pytorch_model.bin")


def download(model: str, revision: str | None) -> Download | None:
    """What loading `model` would fetch from the hub. A bare name is looked up under
    `sentence-transformers/`, as the library looks it up; a folder fetches nothing."""
    if os.path.isdir(model):
        return None
    repo = model if "/" in model else f"sentence-transformers/{model}"
    return hub_download(repo, revision, WEIGHTS)


class SentenceTransformerEmbedder:
    def __init__(
        self,
        model: str,
        *,
        revision: str | None,
        query_prefix: str,
        document_prefix: str,
        device: str,
    ):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model, revision=revision, device=device)
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self.id = f"sentence-transformers:{model}" + (
            f"@{revision}" if revision else ""
        )
        dimension = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self.dims = int(dimension() or 0)
        if self.dims <= 0:
            raise EmbedderError(f"{model} does not report its embedding dimension")
        length = self._model.max_seq_length or self._model.tokenizer.model_max_length
        if not length or length > MAX_PLAUSIBLE_LENGTH:
            raise EmbedderError(f"{model} does not report a usable sequence length")
        prefix = len(
            self._model.tokenizer.encode(document_prefix, add_special_tokens=False)
        )
        self._length = int(length)
        self.max_tokens = self._length - prefix

    def count_tokens(self, text: str) -> int:
        return len(self._model.tokenizer.encode(text, add_special_tokens=True))

    def embed_texts(self, texts: Sequence[str], *, query: bool = False) -> np.ndarray:
        """Longest first, in batches of at most ``TOKEN_BUDGET`` padded tokens, so a
        long-context model's memory stays bounded whatever the input lengths."""
        prefix = self._query_prefix if query else self._document_prefix
        inputs = [prefix + t for t in texts]
        lengths = [min(self.count_tokens(t), self._length) for t in inputs]
        order = sorted(range(len(inputs)), key=lambda i: -lengths[i])
        vectors = np.zeros((len(inputs), self.dims), dtype=np.float32)
        start = 0
        while start < len(order):
            size = max(1, min(BATCH, TOKEN_BUDGET // max(1, lengths[order[start]])))
            batch = order[start : start + size]
            vectors[batch] = self._model.encode(
                [inputs[i] for i in batch],
                batch_size=size,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            start += size
        return normalise(vectors)
