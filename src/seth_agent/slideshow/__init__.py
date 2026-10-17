"""Slideshow videos in the school's branding: chosen photos in, an MP4 out.

The person either says which photos (by id, from the photo library) or describes the event and lets the agent find
them (see find.py). Either way code lays out the title card, a page for each photo and a closing card from the brand
kit, so the title, the slogan and the address are always exactly what they should be. Chrome draws the pages and
ffmpeg makes the video.
"""

from __future__ import annotations

import tempfile
from typing import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from ..flyer.photos import Photo, PhotoLibrary
from ..flyer.render import render_png
from ..flyer.template import format_date
from ..llm import LLM
from . import slides
from .find import Found, find_photos
from .video import FADE, VideoError, make_video, total_seconds

__all__ = ["Found", "Slideshow", "VideoError", "find_photos", "make_from_request", "make_slideshow"]

TITLE_SECONDS = 4.0
END_SECONDS = 4.0
MIN_PHOTO_SECONDS, MAX_PHOTO_SECONDS = 2.0, 20.0


@dataclass
class Slideshow:
    path: Path
    photos: list[Photo]
    seconds: float  # how long the video runs


def make_slideshow(
    title: str,
    photos: Sequence[Photo],
    out: Path,
    brand_dir: Path,
    subtitle: str | None = None,
    end_text: str | None = None,
    show_address: bool = False,
    seconds_per_photo: float = 5.0,
    music: Path | None = None,
    chrome: str | None = None,
    ffmpeg: str | None = None,
) -> Slideshow:
    """Make the video of `photos`, in order, and save it to `out` as an MP4.

    It opens with a title card (`title`, and `subtitle` under it, such as the date) and ends with a closing card
    (`end_text`, and the school's address if `show_address`). Every page carries the school's slogan. `music` is any
    audio file ffmpeg can read; it is faded in and out and cut to the video's length.
    """
    title = title.strip()
    if not title:
        raise VideoError("the slideshow needs a title")
    if not photos:
        raise VideoError("the slideshow needs at least one photo")
    if not MIN_PHOTO_SECONDS <= seconds_per_photo <= MAX_PHOTO_SECONDS:
        raise VideoError(f"each photo can be shown for {MIN_PHOTO_SECONDS:g} to {MAX_PHOTO_SECONDS:g} seconds")
    if music is not None and not Path(music).is_file():
        raise VideoError(f"no music file at {music}")
    pages = [slides.title_card(brand_dir, title, (subtitle or "").strip() or None)]
    pages += [slides.photo_page(brand_dir, p.image, title) for p in photos]
    pages.append(slides.end_card(brand_dir, (end_text or "").strip() or None, show_address))
    durations = [TITLE_SECONDS] + [seconds_per_photo] * len(photos) + [END_SECONDS]
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="seth-slideshow-") as tmp:
        pictures = [Path(tmp) / f"slide-{i:03d}.png" for i in range(len(pages))]
        size = (slides.WIDTH, slides.HEIGHT)
        with ThreadPoolExecutor(max_workers=4) as pool:  # each page is its own Chrome, so a few at a time
            list(pool.map(lambda job: render_png(job[0], job[1], chrome, size), zip(pages, pictures)))
        make_video(pictures, durations, out, ffmpeg, music, size)
    return Slideshow(out, list(photos), total_seconds(durations, FADE))


def make_from_request(
    llm: LLM,
    library: PhotoLibrary,
    request: str,
    brand_dir: Path,
    today: date,
    out: Path | Callable[[str], Path],
    count: int = 12,
    subtitle: str | None = None,
    end_text: str | None = None,
    address: bool | None = None,
    seconds_per_photo: float = 5.0,
    music: Path | None = None,
    chrome: str | None = None,
    ffmpeg: str | None = None,
) -> tuple[Found, Slideshow]:
    """Read `request`, find the event's photos and make the video.

    The title comes from the request. The date line under it is the event's date unless `subtitle` says otherwise, and
    the closing card carries the school's address when the request is for something at the school (`address`
    overrides that). `out` is where to save the MP4, or a function that picks the path from the title.
    """
    found = find_photos(llm, library, request, today, count)
    spec = found.spec
    if subtitle is None and spec.event_date:
        subtitle = format_date(spec.event_date)
    path = out(spec.title) if callable(out) else out
    show = make_slideshow(
        spec.title, found.photos, path, brand_dir, subtitle, end_text,
        spec.at_school if address is None else address, seconds_per_photo, music, chrome, ffmpeg,
    )  # fmt: skip
    return found, show
