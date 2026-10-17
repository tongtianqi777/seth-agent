"""Artwork behind a flyer: a short scene from the model, wrapped in the brand's art direction, sent to an image model.

The image model only ever paints a background with no writing on it. The words, the date and the logo are put on top
by the template, so a flyer is never wrong because a picture misspelled something.

An image provider is anything with `generate(prompt)`. OpenAIImages speaks the OpenAI images API, which several
other servers copy, so `SETH_IMAGE_URL` can point at one of those instead.
"""

from __future__ import annotations

import base64
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from ..llm import LLM, Image
from .spec import FlyerSpec


class ArtError(Exception):
    """The artwork couldn't be made. The flyer is still made, without it."""


class ImageGen(Protocol):
    def generate(self, prompt: str) -> Image:
        """Paint `prompt`. Raises ArtError."""
        ...


class ArtBrief(BaseModel):
    scene: str = Field(
        max_length=300,
        description="One or two sentences describing the picture: the objects and setting, nothing about text or layout",
    )


BRIEF_SYSTEM = """\
You art-direct the background picture for a flyer for Seth Christian Education Institute, a Christian school. You get the flyer's wording. Describe the picture that should decorate it, as a short scene for an illustrator.

- Pick things that suit the event (an open house: an open door, books, a schoolyard tree; a picnic: a blanket, fruit, a kite; picture day: a camera, a paintbrush and crayons).
- Objects, plants, buildings and landscapes are best. No people's faces. Don't put words, letters or numbers in the scene.
- Keep it simple, a few elements. The page already has the logo, headline and details; the picture is only decoration.
- Don't describe style, colors or layout. The art direction is added separately.
"""


def art_prompt(brief: ArtBrief, art: dict) -> str:
    """The text sent to the image model: the scene, then the brand's art direction, word for word."""
    return (
        f"A background illustration for a school flyer, portrait orientation. Scene: {brief.scene.strip()}\n\n"
        f"Style: {art['style']}\nPalette: {art['palette']}\nComposition: {art['composition']}\nAvoid: {art['avoid']}"
    )


def plan_art(llm: LLM, spec: FlyerSpec, art: dict) -> str:
    """The prompt for this flyer's artwork. Only the event's name and its extras are shared with the model."""
    wording = "\n".join(x for x in (spec.title, spec.subtitle, spec.details) if x)
    return art_prompt(llm.parse(BRIEF_SYSTEM, f"Flyer wording:\n{wording}", ArtBrief), art)


class OpenAIImages:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-image-1",
        base_url: str = "https://api.openai.com/v1",
        quality: str = "high",
        size: str = "1024x1536",
        timeout: float = 300.0,
        client: httpx.Client | None = None,
    ):
        self.model, self.quality, self.size = model, quality, size
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )

    def generate(self, prompt: str) -> Image:
        body = {"model": self.model, "prompt": prompt, "n": 1, "size": self.size, "quality": self.quality}
        try:
            r = self.client.post("/images/generations", json=body)
            if r.status_code >= 400:
                raise ArtError(f"image server answered {r.status_code}: {r.text[:300]}")
            item = r.json()["data"][0]
            if item.get("b64_json"):
                data = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                data = self.client.get(item["url"]).raise_for_status().content
            else:
                raise ArtError("image server sent no picture")
        except httpx.HTTPError as e:
            raise ArtError(f"couldn't reach the image server: {e!r}") from e
        except (KeyError, IndexError, ValueError) as e:
            raise ArtError(f"unexpected answer from the image server: {e!r}") from e
        return Image(sniff_media_type(data), data)


def sniff_media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ArtError("the image server's picture isn't a PNG, JPEG or WebP")


def build_image_gen(config) -> ImageGen | None:
    """The configured image provider, or None when artwork is off. Raises ArtError if it can't be set up."""
    if config.image_provider == "none":
        return None
    if not config.image_api_key:
        raise ArtError("no image API key: set OPENAI_API_KEY (or SETH_IMAGE_API_KEY), or SETH_IMAGE_PROVIDER=none")
    return OpenAIImages(
        config.image_api_key, config.image_model, config.image_url, config.image_quality, config.image_size
    )
