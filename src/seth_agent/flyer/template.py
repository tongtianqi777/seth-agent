"""Filling a flyer layout from brand/ with a FlyerSpec.

The layout (brand/templates/event.html) holds the look; brand/brand.json says which logo, colors and fonts it uses.
This module only fills in the words. Everything from the request is HTML-escaped.
"""

from __future__ import annotations

import base64
import json
from datetime import date, time
from html import escape
from pathlib import Path
from typing import Sequence
from string import Template

from ..llm import Image
from .spec import FlyerSpec

DEFAULT_TEMPLATE = "event"


def format_date(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def format_time(t: time) -> str:
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def format_times(start: time | None, end: time | None) -> str | None:
    if start and end:
        return f"{format_time(start)} – {format_time(end)}"
    if start:
        return format_time(start)
    if end:
        return f"Until {format_time(end)}"
    return None


def available_layouts(brand_dir: Path) -> list[str]:
    """The flyer layouts in the brand kit (the names of the html files in its templates folder)."""
    brand = json.loads((brand_dir / "brand.json").read_text())
    return sorted(f.stem for f in (brand_dir / brand.get("templates_dir", "templates")).glob("*.html"))


def title_size(title: str) -> int:
    """The headline's font size in px: the shorter the title, the bigger it can be."""
    n = len(title)
    return 150 if n <= 14 else 120 if n <= 24 else 96 if n <= 38 else 78


def data_uri(data: bytes, media_type: str = "image/png") -> str:
    return f"data:{media_type};base64," + base64.b64encode(data).decode()


def render_html(
    spec: FlyerSpec,
    brand_dir: Path,
    art: Image | None = None,
    photos: Sequence[Image] = (),
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """The flyer's page. `art`, if given, is a picture laid behind everything; `photos` go in a row near the bottom."""
    brand = json.loads((brand_dir / "brand.json").read_text())
    layout = Template((brand_dir / brand.get("templates_dir", "templates") / f"{template}.html").read_text())

    when_lines = []
    if spec.event_date:
        when_lines.append(f'<div class="date">{escape(format_date(spec.event_date))}</div>')
    times = format_times(spec.start_time, spec.end_time)
    if times:
        when_lines.append(f'<div class="time">{escape(times)}</div>')

    def block(html: str, present) -> str:
        return html if present else ""

    # The address comes from the brand kit, never from the model, so it is always exactly right.
    address = brand["address"]["text"] if spec.at_school else None
    if address and spec.location and "hollenbeck" in spec.location.casefold():
        address = None  # the request already says it
    where_lines = []
    if spec.location:
        where_lines.append(f'<div class="place">{escape(spec.location)}</div>')
    if address:
        where_lines.append(f'<div class="{"address" if spec.location else "place"}">{escape(address)}</div>')
    slogan = "".join(f"<span>{escape(part)}</span> " for part in brand["slogan"]["parts"])

    return layout.substitute(
        **brand["colors"],
        font_stack=brand["fonts"]["stack"],
        school_name=escape(brand["name"], quote=True),
        logo_src=data_uri((brand_dir / brand["logos"]["primary"]["file"]).read_bytes()),
        art_block=(
            f'<div class="art" style="background-image: url({data_uri(art.data, art.media_type)})"></div><div class="veil"></div>'
            if art
            else ""
        ),
        page_class=" has-photos" if photos else "",
        photos_block=(
            '<div class="photos">' + "".join(f'<img src="{data_uri(p.data, p.media_type)}" alt="">' for p in photos) + "</div>"
            if photos
            else ""
        ),
        title_text=escape(spec.title),
        title=escape(spec.title),
        title_size=title_size(spec.title),
        subtitle_block=block(f'<p class="subtitle">{escape(spec.subtitle or "")}</p>', spec.subtitle),
        when_block=block(f'<div class="when">{"".join(when_lines)}</div>', when_lines),
        where_block=block(f'<div class="where"><div class="label">Where</div>{"".join(where_lines)}</div>', where_lines),
        slogan_html=slogan,
        details_block=block(f'<p class="details">{escape(spec.details or "")}</p>', spec.details),
    )
