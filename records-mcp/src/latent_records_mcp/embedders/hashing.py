# Verbatim copy of latent-records at 5a75235, below this header:
#   src/latent_records/embedders/hashing.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""A deterministic embedder with no dependencies — for smoke tests only.

Text vectors are signed word hashes, so texts sharing words are near each other and
retrieval on a fixture is predictable. Image vectors are seeded by the bytes. Nothing
here means anything; it exists so the whole pipeline runs without model weights.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import numpy as np

from .base import normalise

WORD = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    max_tokens = 512

    def __init__(self, dims: int):
        self.dims = dims
        self.id = f"hash:{dims}"

    def count_tokens(self, text: str) -> int:
        return len(WORD.findall(text.lower()))

    def _word(self, word: str) -> tuple[int, float]:
        digest = hashlib.sha256(word.encode()).digest()
        return int.from_bytes(digest[:4], "big") % self.dims, (
            1.0 if digest[4] & 1 else -1.0
        )

    def embed_texts(self, texts: Sequence[str], *, query: bool = False) -> np.ndarray:
        out = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            words = WORD.findall(text.lower())
            if not words:
                out[row, 0] = 1.0
            for word in words:
                index, sign = self._word(word)
                out[row, index] += sign
        return normalise(out)

    def embed_images(self, images: Sequence[bytes]) -> np.ndarray:
        out = np.zeros((len(images), self.dims), dtype=np.float32)
        for row, data in enumerate(images):
            seed = int.from_bytes(hashlib.sha256(data).digest()[:8], "big")
            out[row] = np.random.default_rng(seed).standard_normal(self.dims)
        return normalise(out)
