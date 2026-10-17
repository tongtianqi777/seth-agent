"""Photos from the school's photo library on a flyer.

The model describes the kind of photo that suits the event ("children in a classroom"); the photo search finds the
best matches among the school's photos, a few that don't look alike; and the template lays them in a row. Nothing is
sent anywhere: the search runs on this machine, and the photos come from seth-feed.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Sequence

from pydantic import BaseModel, Field

from ..embeddings import Embedder
from ..llm import LLM, Image
from ..media_search import GroupNotFound, pick_diverse
from ..memory.store import MemoryStore
from ..source import MessageSource, SourceError
from .spec import FlyerSpec

MAX_SIDE = 900  # px; a photo is shown about 300 px wide, so this keeps the page small without looking soft


class PhotoError(Exception):
    """Photos couldn't be found or read. The flyer is still made, without them, unless the person chose the photos."""


@dataclass
class Photo:
    file_id: int
    score: float | None  # None for a photo the person picked by id
    group: str
    sent_at: str
    image: Image  # ready for the page: upright, and no bigger than it needs to be


class PhotoBrief(BaseModel):
    queries: list[str] = Field(
        max_length=3,
        description="One to three short descriptions of photos that would suit the flyer, or none if photos wouldn't suit it",
    )


BRIEF_SYSTEM = """\
You choose photos for a flyer for Seth Christian Education Institute, a school. You get the flyer's wording. Describe, in a few plain words each, the photos from the school's photo library that would suit it, the way someone would describe a photo they are looking for: "children in a classroom", "a teacher reading to students", "children playing outside", "children eating lunch together", "a class performing on stage".

- One to three descriptions, each a different kind of photo, so the flyer gets variety.
- Describe what is in the photo, not the event ("children in a classroom", not "open house").
- Photos only go on a flyer when they are appropriate. Ask for no photos (an empty list) for: a closure, a cancellation, a deadline or payment reminder, a weather or safety notice, illness, an online event, or anything sad or serious.
"""


def plan_photos(llm: LLM, spec: FlyerSpec) -> list[str]:
    wording = "\n".join(x for x in (spec.title, spec.subtitle, spec.details) if x)
    brief = llm.parse(BRIEF_SYSTEM, f"Flyer wording:\n{wording}", PhotoBrief)
    return [q.strip()[:100] for q in brief.queries if q.strip()][:3]


def prepare(data: bytes, max_side: int = MAX_SIDE) -> Image:
    """The photo as it goes on the page: turned upright (phones store the turn in the file), shrunk, as a JPEG."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    try:
        picture = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:
        raise PhotoError(f"a photo can't be read as a picture: {e}") from e
    picture.thumbnail((max_side, max_side))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=86)
    return Image("image/jpeg", out.getvalue())


class PhotoLibrary:
    """The school's indexed photos, for flyers. `group` limits where they come from (None: every group)."""

    def __init__(
        self,
        store: MemoryStore,
        source: MessageSource,
        embedder: Embedder | None,
        group: str | None = None,
        min_score: float = 0.25,
        max_side: int = MAX_SIDE,
    ):
        self.store, self.source, self.embedder = store, source, embedder
        self.group, self.min_score, self.max_side = group, min_score, max_side

    def find(
        self, queries: list[str], count: int, sent_from: str | None = None, sent_until: str | None = None
    ) -> list[Photo]:
        """Up to `count` photos for `queries`. Fewer, or none, if little in the library is a good match.
        `sent_from` and `sent_until` (ISO dates) keep only photos sent in that window."""
        if self.embedder is None:
            raise PhotoError("photo search is off: install it with `pip install -e '.[media]'` (and leave SETH_MEDIA_INDEX on)")
        try:
            # A few spares, in case seth-feed no longer has a file.
            found = pick_diverse(
                self.store, self.source, self.embedder, queries, self.group, count + 2, self.min_score,
                sent_from=sent_from, sent_until=sent_until,
            )
        except GroupNotFound as e:
            raise PhotoError(f"{e} (set SETH_FLYER_PHOTO_GROUP, or 'all')") from e
        except OSError as e:  # the photo model couldn't be downloaded or read
            raise PhotoError(f"photo model unavailable: {e}") from e
        photos: list[Photo] = []
        for r in found:
            if len(photos) == count:
                break
            try:
                data = self.source.fetch_media(r["file_id"])
            except SourceError as e:
                raise PhotoError(str(e)) from e
            if data is not None:
                photos.append(Photo(r["file_id"], r["score"], r["group"], r["sent_at"], prepare(data, self.max_side)))
        return photos

    def get(self, file_ids: Sequence[int]) -> list[Photo]:
        """The photos with these ids, in that order. Raises PhotoError if one isn't an indexed photo."""
        photos = []
        for file_id in file_ids:
            row = self.store.get_media(file_id)
            if row is None:
                raise PhotoError(f"no photo with id {file_id} in the photo library")
            try:
                data = self.source.fetch_media(file_id)
            except SourceError as e:
                raise PhotoError(str(e)) from e
            if data is None:
                raise PhotoError(f"photo {file_id} is no longer available from seth-feed")
            photos.append(Photo(file_id, None, row["group_name"], row["sent_at"], prepare(data, self.max_side)))
        return photos
