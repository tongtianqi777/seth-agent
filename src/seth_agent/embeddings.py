"""Turning photos and search text into vectors that can be compared, on this machine.

A CLIP-style model puts a photo and a sentence in the same space, so "children on a soccer field" lands close
to photos of children on a soccer field. Both are unit length, which makes their dot product the cosine
similarity. Nothing is sent anywhere: the model runs here (Apple GPU, CUDA or CPU).

The heavy libraries (torch, transformers, Pillow) are the optional `media` extra and are only imported when the
model is first used, so an install without them still runs the calendar.
"""

from __future__ import annotations

import io
import logging
import threading
from importlib.util import find_spec
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

MEDIA_EXTRA_PACKAGES = ("torch", "transformers", "PIL")


class Embedder(Protocol):
    model: str  # names the vector space: vectors from different models can't be compared

    def embed_images(self, images: Sequence[bytes]) -> list[list[float] | None]:
        """A unit-length vector for each image, or None for one that can't be read as a picture."""
        ...

    def embed_text(self, text: str) -> list[float]:
        """A unit-length vector for a search phrase."""
        ...


class ClipEmbedder:
    def __init__(self, model: str = "openai/clip-vit-base-patch16"):
        self.model = model
        self._loaded: tuple | None = None
        # The poller indexes photos while the API embeds search phrases, both with the same model.
        self._lock = threading.Lock()

    def _load(self) -> tuple:
        if self._loaded is None:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
            log.info("loading %s on %s (the first time downloads it)", self.model, device)
            model = CLIPModel.from_pretrained(self.model).to(device).eval()
            self._loaded = (torch, model, CLIPProcessor.from_pretrained(self.model), device)
        return self._loaded

    def embed_images(self, images: Sequence[bytes]) -> list[list[float] | None]:
        from PIL import Image, ImageOps

        decoded: dict[int, Image.Image] = {}
        for i, data in enumerate(images):
            try:
                decoded[i] = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
            except Exception as e:  # truncated, not a picture, or too large to be safe to open
                log.warning("skipping an image that can't be read: %s", e)
        out: list[list[float] | None] = [None] * len(images)
        if not decoded:
            return out
        with self._lock:
            torch, model, processor, device = self._load()
            inputs = processor(images=list(decoded.values()), return_tensors="pt").to(device)
            with torch.inference_mode():
                features = model.get_image_features(**inputs)
                features = torch.nn.functional.normalize(features, dim=-1)
            vectors = features.float().cpu().tolist()
        for i, vector in zip(decoded, vectors):
            out[i] = vector
        return out

    def embed_text(self, text: str) -> list[float]:
        with self._lock:
            torch, model, processor, device = self._load()
            inputs = processor(text=[text], padding=True, truncation=True, return_tensors="pt").to(device)
            with torch.inference_mode():
                features = model.get_text_features(**inputs)
                features = torch.nn.functional.normalize(features, dim=-1)
            return features[0].float().cpu().tolist()


def media_extra_installed() -> bool:
    return all(find_spec(p) is not None for p in MEDIA_EXTRA_PACKAGES)


def build_embedder(config: Config) -> Embedder | None:
    """The photo embedder, or None when photo search is turned off or the `media` extra isn't installed."""
    if not config.media_index:
        log.info("photo search is off (SETH_MEDIA_INDEX=0)")
        return None
    if not media_extra_installed():
        log.info("photo search is off: install it with `pip install -e '.[media]'`")
        return None
    return ClipEmbedder(config.media_model)
