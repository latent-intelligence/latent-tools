# Verbatim copy of latent-records at b7ca93c, below this header:
#   src/latent_records/embedders/windowed.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Embed long text through a model with a short context: slide, embed, average.

A text within the window is embedded as it is. A longer one is cut into word windows
of at most ``tokens`` model tokens, starting every ``stride`` tokens; each window is
embedded, and the vectors are averaged weighted by their token counts, then
normalised. Averaging many windows drifts toward a topic centroid, so keep chunks
modest when a space windows.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from .base import TextEmbedder, normalise


def _windows(embedder: TextEmbedder, text: str, tokens: int, stride: int) -> list[str]:
    words = text.split()
    if not words or embedder.count_tokens(text) <= tokens:
        return [text]
    ratio = max(1.0, embedder.count_tokens(text) / len(words))
    width = max(1, math.floor(tokens / ratio))
    step = max(1, math.floor(stride / ratio))
    windows: list[str] = []
    start = 0
    while True:
        size = width
        window = " ".join(words[start : start + size])
        while size > 1 and embedder.count_tokens(window) > tokens:
            size -= 1
            window = " ".join(words[start : start + size])
        windows.append(window)
        if start + size >= len(words):
            return windows
        start += min(step, size)


def embed_windowed(
    embedder: TextEmbedder,
    texts: Sequence[str],
    *,
    tokens: int,
    stride: int,
    query: bool = False,
) -> tuple[np.ndarray, list[int], list[bool]]:
    """One vector per text, how many windows went into each, and whether any window
    was still longer than the model takes (a single word can be)."""
    per_text = [_windows(embedder, t, tokens, stride) for t in texts]
    flat = [w for windows in per_text for w in windows]
    counts = [embedder.count_tokens(w) for w in flat]
    vectors = (
        embedder.embed_texts(flat, query=query)
        if flat
        else np.zeros((0, embedder.dims), dtype=np.float32)
    )
    out = np.zeros((len(texts), embedder.dims), dtype=np.float32)
    truncated: list[bool] = []
    position = 0
    for row, windows in enumerate(per_text):
        span = slice(position, position + len(windows))
        weights = np.array([max(1, c) for c in counts[span]], dtype=np.float32)
        out[row] = (vectors[span] * weights[:, None]).sum(axis=0) / weights.sum()
        truncated.append(any(c > embedder.max_tokens for c in counts[span]))
        position += len(windows)
    return normalise(out), [len(w) for w in per_text], truncated
