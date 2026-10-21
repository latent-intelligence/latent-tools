# Verbatim copy of latent-records at b7ca93c, below this header:
#   src/latent_records/embedders/open_clip.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""CLIP through open_clip: text and images in one space. Needs the ``clip`` extra."""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any

import numpy as np

from ..errors import EmbedderError
from .base import normalise

BATCH = 64


class OpenClipEmbedder:
    def __init__(self, model: str, pretrained: str, device: str):
        import open_clip
        import torch

        self._torch = torch
        self._device = device
        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            model, pretrained=pretrained, device=device
        )
        self._model.eval()
        self._tokenizer = open_clip.get_tokenizer(model)
        self.id = f"open-clip:{model}/{pretrained}"
        with torch.no_grad():
            probe = self._model.encode_text(self._tokenizer(["probe"]).to(device))
        self.dims = int(probe.shape[1])
        self.max_tokens = int(getattr(self._tokenizer, "context_length", 77)) - 2

    def count_tokens(self, text: str) -> int:
        tokenizer: Any = self._tokenizer
        if hasattr(tokenizer, "encode"):
            return len(tokenizer.encode(text))
        return len(tokenizer.tokenizer.encode(text, add_special_tokens=False))

    def embed_texts(self, texts: Sequence[str], *, query: bool = False) -> np.ndarray:
        out = []
        with self._torch.no_grad():
            for i in range(0, len(texts), BATCH):
                tokens = self._tokenizer(list(texts[i : i + BATCH])).to(self._device)
                out.append(self._model.encode_text(tokens).float().cpu().numpy())
        return normalise(np.concatenate(out)) if out else np.zeros((0, self.dims))

    def embed_images(self, images: Sequence[bytes]) -> np.ndarray:
        from PIL import Image

        out = []
        with self._torch.no_grad():
            for i in range(0, len(images), BATCH):
                try:
                    pixels = [
                        self._preprocess(Image.open(io.BytesIO(data)).convert("RGB"))
                        for data in images[i : i + BATCH]
                    ]
                except (OSError, ValueError) as exc:
                    raise EmbedderError(f"not a readable image: {exc}") from exc
                batch = self._torch.stack(pixels).to(self._device)
                out.append(self._model.encode_image(batch).float().cpu().numpy())
        return normalise(np.concatenate(out)) if out else np.zeros((0, self.dims))
