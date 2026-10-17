"""Free-form flyer design: the model writes the page, the program supplies every fact.

Each flyer gets its own look. The model writes one self-contained HTML page (CSS and inline SVG drawings) for the
1080 x 1350 canvas, but it never types the event's words or loads a picture itself. It writes tokens such as
{{TITLE}}, {{DATE}}, {{LOGO}} and {{PHOTO_1}}, and this module swaps in the exact text and the real image files. So
creativity can't misspell a date, redraw the logo or invent a detail, and a page that breaks the school's rules is
sent back with the problems listed, or refused.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, Field

from ..llm import LLM, Image
from .render import check_page
from .spec import FlyerSpec
from .template import data_uri, format_date, format_times

log = logging.getLogger(__name__)

MAX_REVISIONS = 3  # how many times a page that breaks the rules is sent back

IDEAS = [
    "Giant poster type: the title fills the width, the date sits in a bold round badge, shapes in brand colors tumble behind them.",
    "An invitation card: a big rounded card on a colored background, a dotted tear-off edge, the date as a ticket stub.",
    "Scrapbook: photos as tilted prints with tape corners, hand-drawn doodles (stars, arrows, underlines) around the words.",
    "Split page: a tall navy panel with the details in cream on one side, the pictures and artwork on the other.",
    "Party bunting: a garland of triangle flags in SVG across the top, balloons at the sides, the words centered below.",
    "Calendar page: the day number as a huge hero numeral (use DAY), the month above it and the weekday below, like a tear-off calendar.",
    "An arched window: a tall arch frames the main photo or a drawn scene, with the title standing on a ribbon under it.",
    "Diagonal bands: bold slanted stripes in navy, green and gold cross the page and the title rides one of them.",
    "Magazine cover: the main photo full-bleed with a navy gradient at the bottom, the title in big cream letters over it.",
    "Chalkboard: a navy board with a cream border, the words in cream and gold as if written in chalk, with simple SVG doodles.",
    "Sunburst: rays fan out from a central badge holding the date, the title arching over it.",
    "Storybook: soft SVG hills, a tree and a path across the bottom, a big sky with the title, small birds and butterflies.",
    "Photo wall: a grid of photos in different sizes like a gallery wall, the title and date on a banner across it.",
    "Fresh and simple: lots of cream space, one strong color block, one big photo, very clean type, a single gold accent.",
]

SYSTEM = """\
You are the designer for the flyers of Seth Christian Education Institute, a Christian school. Each flyer you design should have its own character and suit its event: be creative, and never fall back on a stock layout. Warm, welcoming and wholesome, like a good picture book or a well-made school poster, and easy to read at a glance on a phone.

You write ONE self-contained HTML page for a canvas of exactly 1080 x 1350 px (it is saved as a PNG and shared in WhatsApp). Put everything on that canvas. Use CSS and inline <svg> for all decoration: shapes, patterns, ribbons, bunting, balloons, hills, trees, books, stars, doodles. Draw your own artwork in SVG; be bold with it, layer it, and keep it in the brand palette.

THE ART KIT
The school has a kit of hand-drawn motifs in its palette (bunting, balloons, sun, hills, tree, schoolhouse, flowers, butterflies, crayons, leaves, ribbons, sparkles, squiggle, clouds, books). They are listed with each flyer as {{MOTIF_...}} picture tokens and are optional, but they are the easiest way to make a page feel finished and hand-made. Compose a scene from several: place, scale, overlap and flip them (transform: scaleX(-1)), and let some run off the edges. Put them behind the words. Combine them with your own inline SVG shapes. Pick the ones that suit the event (leaves for autumn, crayons for picture day, bunting and balloons for a party, hills and a tree for a spring day, books for classes).

HOW WORDS AND PICTURES WORK
You do not type the event's words or load pictures. You write tokens, and the program replaces each with the real thing:
- Text tokens give the exact words: {{TITLE}}, {{SUBTITLE}}, {{DATE}}, {{TIME}}, {{LOCATION}}, {{ADDRESS}}, {{DETAILS}}, {{SLOGAN}}, plus {{WEEKDAY}}, {{MONTH}}, {{DAY}}, {{YEAR}} if you want pieces of the date. Use a token as text content, e.g. <h1>{{TITLE}}</h1>.
- Picture tokens give image files: {{LOGO}}, {{LOGO_DARK}}, {{CREST}}, {{BACKGROUND}}, {{PHOTO_1}}, {{PHOTO_2}}, ... and the school's art kit, {{MOTIF_BUNTING}}, {{MOTIF_HILLS}}, ... Use them as <img src="{{LOGO}}"> or in CSS as url({{BACKGROUND}}).
- Only the tokens listed for this flyer are available. You MUST use every listed text token at least once (except the pieces of the date, which are optional) and every listed photo token at least once. Use each text token as it comes; don't split it up or reword it.
- Do not write any other words on the page. No invented taglines, offers, prices, RSVP lines or details. The only extra words allowed are the small labels "When" and "Where". Symbols and doodles are fine.

THE SCHOOL'S BRAND (the program already defines these CSS variables, use them: var(--navy) var(--blue) var(--green) var(--gold) var(--cream) var(--background) var(--text) var(--font))
- Colors: navy #002a46 (headlines), blue #003e5d, green #739e30, gold #f1a618 (accents), cream #f0e9e0, background #f7f4ee, text #10283a. Use only these colors, plus white. For tints use color-mix(in srgb, var(--green) 40%, white) or rgba() of them. No other hues.
- Font: font-family: var(--font) (system fonts only). Use heavy weights (700-900) for headlines. Body text at least 28px, the slogan at least 22px, nothing under 20px.
- The logo: always the real image through a token, never redrawn. {{LOGO}} is for light backgrounds, {{LOGO_DARK}} for dark ones (navy, photos). Never stretch, recolor, crop, rotate, put effects or a shadow on it. Show it clearly, at least 320px wide, with clear space around it. {{CREST}} is the shield alone, for small spaces.
- The slogan must appear on every flyer, small and quiet, near the bottom, on a plain background where it is easy to read. Use {{SLOGAN}} (one line of text with "•" between its three parts), or, to control where it breaks, use all three of {{SLOGAN_1}} {{SLOGAN_2}} {{SLOGAN_3}} in order in separate elements with a "•" (drawn with CSS content) between them. Never reword or drop a part.
- {{ADDRESS}}, when it is listed, is the school's address and must be shown clearly.
- Photos (listed as {{PHOTO_n}}) are real photos of the school's children. Show every photo listed, recognizable and unaltered: object-fit: cover, a face-friendly crop such as object-position: 50% 30%, no heavy filters, no opacity or blending, nothing covering faces. Frames, borders, rounded corners, tilt and shadows are fine.
- EVERY flyer has a background of artwork, never a bare color. {{BACKGROUND}} is a ready-made full-page scene for this event (portrait 1080x1350: sky, hills, motifs from the art kit); put it behind everything with background-size: cover, or build your own scene from several art-kit motifs and your own SVG (at least three motifs, or a substantial SVG drawing). If the person supplied their own background picture, {{BACKGROUND}} is that picture and you must use it. Keep text readable over any background with a solid or soft cream panel behind the words.

HOW TO BUILD IT SO NOTHING COLLIDES
- Put all the words in normal flow inside ONE full-size container (width 1080px, height 1350px, display: flex or grid, flex-direction: column). Size the text so it fits with room to spare. Do NOT position text absolutely, and don't stack text blocks on each other with negative margins or transforms.
- Decorations (SVG shapes, bunting, balloons, hills, stars, doodles, photos as tilted prints) may be absolutely positioned, but put them behind or beside the words (z-index below the text) and keep them away from the text. Photos may sit in the flow or beside it; give them real sizes (at least 220px wide each).
- Plan the vertical budget before you write: logo about 160-250px tall, title about 150-300px, date and time about 150-220px, place and details about 150-250px, photos about 250-350px, slogan about 70-90px. They must add up to under 1350px with gaps.

PAGE RULES
- A complete HTML document with its own <style>. No <script>, no external files, fonts or URLs: only tokens, inline SVG and CSS. Set the page's content to fit within 1080 x 1350: nothing may run off the edges or overlap other text.
- Text must have strong contrast with whatever is behind it. On photos or art use a solid or gradient panel behind the words.
- Hierarchy: the title is the biggest thing, then the date and time, then the place, then the rest. Everything the reader needs must be easy to find.

Reply with a single JSON object: {"html": "<the complete page>"}.
"""


class FlyerDesign(BaseModel):
    html: str = Field(description="The complete HTML page, with tokens like {{TITLE}} where the program fills in the real text and pictures")


class DesignError(Exception):
    """The model couldn't produce a page that follows the rules. The flyer falls back to a fixed layout."""


@dataclass
class Assets:
    """Everything a design may use: exact text by token, and images by token."""

    text: dict[str, str]
    images: dict[str, Image]
    notes: dict[str, str]  # a short description of each image, for the designer
    palette: tuple[str, ...] = ()  # the school's colors, as hex
    background_required: bool = False  # the person chose the background picture, so the page must use it


def build_assets(
    spec: FlyerSpec,
    brand_dir: Path,
    brand: dict,
    background: Image,
    photos: Sequence[Image] = (),
    background_required: bool = False,
) -> Assets:
    text: dict[str, str] = {"TITLE": spec.title, "SLOGAN": brand["slogan"]["text"]}
    for i, part in enumerate(brand["slogan"]["parts"], 1):
        text[f"SLOGAN_{i}"] = part
    if spec.subtitle:
        text["SUBTITLE"] = spec.subtitle
    if spec.event_date:
        d = spec.event_date
        text.update(DATE=format_date(d), WEEKDAY=f"{d:%A}", MONTH=f"{d:%B}", DAY=str(d.day), YEAR=str(d.year))
    times = format_times(spec.start_time, spec.end_time)
    if times:
        text["TIME"] = times
    if spec.location:
        text["LOCATION"] = spec.location
    address = brand["address"]["text"] if spec.at_school else None
    if address and not (spec.location and "hollenbeck" in spec.location.casefold()):
        text["ADDRESS"] = address
    if spec.details:
        text["DETAILS"] = spec.details

    def logo(key: str) -> Image:
        return Image("image/png", (brand_dir / brand["logos"][key]["file"]).read_bytes())

    images = {"LOGO": logo("primary"), "LOGO_DARK": logo("on_dark"), "CREST": logo("crest")}
    notes = {
        "LOGO": "the full logo, wide (about 2.2:1), for light backgrounds",
        "LOGO_DARK": "the full logo in light colors, wide (about 2.2:1), for dark backgrounds",
        "CREST": "the shield alone, slightly taller than wide",
    }
    images["BACKGROUND"] = background
    notes["BACKGROUND"] = "the background for this flyer, portrait 1080x1350" + (
        " (the person's own picture: you must use it)" if background_required else " (a ready-made scene; you may use it or build your own)"
    )
    for name, motif in brand.get("motifs", {}).items():
        token = "MOTIF_" + name.upper().replace("-", "_")
        images[token] = Image("image/svg+xml", (brand_dir / motif["file"]).read_bytes())
        notes[token] = f"art kit, {motif['width']}x{motif['height']}px: {motif['use']}"
    for i, photo in enumerate(photos, 1):
        images[f"PHOTO_{i}"] = photo
        notes[f"PHOTO_{i}"] = f"a photo, {orientation(photo)}"
    return Assets(text, images, notes, tuple(brand["colors"].values()), background_required)


def orientation(image: Image) -> str:
    try:
        import io

        from PIL import Image as PILImage

        w, h = PILImage.open(io.BytesIO(image.data)).size
        shape = "landscape" if w > h * 1.1 else "portrait" if h > w * 1.1 else "square"
        return f"{shape}, {w}x{h}"
    except Exception:
        return "orientation unknown"


def pick_ideas(rng: random.Random | None = None, count: int = 2) -> list[str]:
    return (rng or random.Random()).sample(IDEAS, count)


def describe(assets: Assets) -> str:
    lines = ["Tokens available for this flyer, with what each holds:"]
    for token, value in assets.text.items():
        lines.append(f"- {{{{{token}}}}} (text): {value}")
    for token, note in assets.notes.items():
        lines.append(f"- {{{{{token}}}}} (picture): {note}")
    return "\n".join(lines)


def user_prompt(spec: FlyerSpec, assets: Assets, ideas: Sequence[str], request: str, extra_idea: str | None = None) -> str:
    starts = list(ideas) + ([extra_idea] if extra_idea else [])
    return (
        f"Design the flyer for: {request.strip()}\n\n{describe(assets)}\n\n"
        + ("The person asked for this look: " + extra_idea + "\n\n" if extra_idea else "")
        + "Starting points for the look (adapt, combine or ignore; the page must still suit this event):\n"
        + "\n".join(f"- {i}" for i in ideas)
        + "\n\nMake it distinctive. Put real effort into the SVG artwork and the typography."
    )


# ---- checking the page the model wrote ----

TOKEN = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
FORBIDDEN_TAGS = re.compile(r"<\s*(script|iframe|object|embed|link|base|form|audio|video)\b|<\s*meta\b[^>]*http-equiv", re.I)
LABELS = {"when", "where"}
DATE_PIECES = {"WEEKDAY", "MONTH", "DAY", "YEAR"}
SLOGAN_PARTS = {"SLOGAN_1", "SLOGAN_2", "SLOGAN_3"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "title"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "title") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.chunks.append(" ".join(data.split()))


def problems_with(html: str, assets: Assets) -> list[str]:
    """What is wrong with a designed page before it is drawn: tokens, forbidden things, invented words."""
    problems: list[str] = []
    used = set(TOKEN.findall(html))
    known = set(assets.text) | set(assets.images)
    for token in sorted(used - known):
        problems.append(f"{{{{{token}}}}} isn't available for this flyer; use only the tokens listed")
    if not ({"SLOGAN"} <= used or SLOGAN_PARTS <= used):
        problems.append("the school's slogan is missing: use {{SLOGAN}}, or all three of {{SLOGAN_1}} {{SLOGAN_2}} {{SLOGAN_3}} (with a • between them)")
    for token in sorted(set(assets.text) - used - DATE_PIECES - SLOGAN_PARTS - {"SLOGAN"}):
        problems.append(f"the page never uses {{{{{token}}}}}; every listed text token must appear")
    for token in sorted(set(assets.images) - used):
        if token in ("LOGO", "LOGO_DARK", "CREST", "BACKGROUND") or token.startswith("MOTIF_"):
            continue
        problems.append(f"the page never uses {{{{{token}}}}}; every listed picture must appear")
    if assets.background_required and "BACKGROUND" not in used:
        problems.append("the person chose the background picture: use {{BACKGROUND}} behind everything")
    elif not has_background(html, used):
        problems.append(
            "the flyer has no background artwork: put {{BACKGROUND}} behind everything, or build a scene from at least three "
            "art-kit motifs ({{MOTIF_...}}) or a substantial inline SVG drawing"
        )
    if not {"LOGO", "LOGO_DARK"} & used:
        problems.append("the school's logo is missing: use {{LOGO}} (light background) or {{LOGO_DARK}} (dark background)")
    if FORBIDDEN_TAGS.search(html):
        problems.append("the page uses a tag that isn't allowed (script, iframe, object, embed, link, base, form, audio, video)")
    bare = html.replace("http://www.w3.org/2000/svg", "").replace("http://www.w3.org/1999/xlink", "")
    if re.search(r"(https?:)?//[A-Za-z0-9]", bare) or re.search(r"@import", bare, re.I):
        problems.append("the page reaches for an outside address or file; use only tokens, CSS and inline SVG")
    for value in re.findall(r"""(?:src|href)\s*=\s*["']?([^"'\s>]+)""", bare, re.I) + re.findall(r"""url\(\s*["']?([^)"']+)""", bare, re.I):
        if not value.startswith(("{{", "#", "data:")):
            problems.append(f"the address {value[:40]!r} isn't allowed; pictures come only from tokens")
            break
    allowed_colors = {_hex6(c) for c in assets.palette} | {"#ffffff", "#000000"}
    stray = sorted({c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", bare) if len(c) in (4, 7, 9) and _hex6(c) not in allowed_colors})
    if stray:
        problems.append(
            f"these colors aren't the school's: {', '.join(stray[:5])}. Use only var(--navy) var(--blue) var(--green) var(--gold) var(--cream) var(--background) var(--text), white, "
            "and tints made with color-mix() or rgba() of them"
        )
    named = re.search(r"(?:fill|stroke|color|background(?:-color)?|border(?:-[a-z]+)?)\s*[:=]\s*[\"']?\s*(red|blue|yellow|orange|purple|pink|brown|gray|grey|teal|cyan|magenta|lime|maroon|violet|indigo|crimson|gold|navy|green)\b", bare, re.I)
    if named:
        problems.append(f"the color name {named.group(1)!r} isn't allowed; use the school's colors through var(--...) or their hex values")
    for family in re.findall(r"font-family\s*:\s*([^;}\"]+)", html, re.I):
        if "var(--font)" not in family and family.strip().lower() not in ("inherit", "initial", "unset"):
            problems.append("use font-family: var(--font) for all text (the school's fonts); no other font families")
            break
    # Words: everything printed must come from the tokens (or be a small label or a symbol).
    printed = _printed_text(TOKEN.sub(lambda m: "\x00" + m.group(1) + "\x00", html))
    values = [v.casefold() for v in assets.text.values()]
    for chunk in printed:
        for part in re.split(r"\x00", chunk):
            part = part.strip()
            part = part.strip(" :.,;-")
            if not part or part in assets.text or re.fullmatch(r"[\W\d_]*", part):
                continue
            if part.casefold() in LABELS or any(part.casefold() in v for v in values):
                continue
            problems.append(f"the page prints the words {part[:60]!r}, which aren't from the tokens; remove them")
    return problems


def has_background(html: str, used: set[str]) -> bool:
    """Whether a page has background artwork: the ready-made scene, several art-kit motifs, or a real SVG drawing."""
    if "BACKGROUND" in used or sum(t.startswith("MOTIF_") for t in used) >= 3:
        return True
    shapes = re.findall(r"<(?:path|circle|ellipse|rect|polygon|polyline|line)\b", html, re.I)
    return len(shapes) >= 12


def _hex6(color: str) -> str:
    """A hex color as six lowercase digits (short forms expanded, any alpha dropped), for comparing."""
    h = color.lower().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h[:6]


def _printed_text(html: str) -> list[str]:
    parser = _Text()
    parser.feed(html)
    return parser.chunks


def assemble(html: str, assets: Assets, brand: dict) -> str:
    """The finished page: the model's HTML with the real text and pictures in, and the brand's base styles around it."""
    colors = brand["colors"]
    variables = "".join(f"--{k}: {v}; " for k, v in colors.items())
    base = (
        f'<meta charset="utf-8"><style>:root {{ {variables}--font: {brand["fonts"]["stack"]}; }} '
        "*, *::before, *::after { box-sizing: border-box; } body { margin: 0; }</style>"
    )
    final = (
        "html, body { width: 1080px; height: 1350px; margin: 0; overflow: hidden; } "
        "body { position: relative; font-family: var(--font); }"
    )

    def swap(match: re.Match) -> str:
        token = match.group(1)
        if token in assets.text:
            return escape(assets.text[token], quote=True)
        image = assets.images[token]
        return data_uri(image.data, image.media_type) + "#" + ("logo" if token.startswith(("LOGO", "CREST")) else "photo" if token.startswith("PHOTO") else "motif" if token.startswith("MOTIF") else "art")

    page = TOKEN.sub(swap, html)
    if re.search(r"<head[^>]*>", page, re.I):
        page = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + base, page, count=1, flags=re.I)
    else:
        page = re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + "<head>" + base + "</head>", page, count=1, flags=re.I) if re.search(r"<html", page, re.I) else f"<!doctype html><html><head>{base}</head><body>{page}</body></html>"
    return re.sub(r"</head>", lambda m: f"<style>{final}</style></head>", page, count=1, flags=re.I)


def review(html: str, assets: Assets, brand: dict, chrome: str | None = None) -> tuple[str | None, list[str]]:
    """Checks a designed page. Returns (the finished page, []) if it follows the rules, else (None, what is wrong)."""
    problems = problems_with(html, assets)
    if problems:
        return None, problems
    page = assemble(html, assets, brand)
    problems = check_page(page, chrome)
    return (None, problems) if problems else (page, [])


def finish_design(html: str, assets: Assets, brand: dict, chrome: str | None = None) -> str:
    """A page written by hand (or by another model): the same checks, and an error rather than a retry."""
    page, problems = review(html, assets, brand, chrome)
    if page is None:
        raise DesignError("; ".join(problems[:8]))
    return page


def design_flyer(
    llm: LLM,
    spec: FlyerSpec,
    request: str,
    assets: Assets,
    brand: dict,
    ideas: Sequence[str],
    extra_idea: str | None = None,
    chrome: str | None = None,
    effort: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """The HTML of a designed flyer, checked and ready to draw. Raises DesignError if the model can't get it right."""
    prompt = user_prompt(spec, assets, ideas, request, extra_idea)
    problems: list[str] = []
    for attempt in range(MAX_REVISIONS + 1):
        design = llm.parse(SYSTEM, prompt, FlyerDesign, effort=effort, max_tokens=max_tokens)
        log.debug("designer attempt %d: %d chars of HTML", attempt + 1, len(design.html))
        page, problems = review(design.html, assets, brand, chrome)
        if page is not None:
            return page
        log.info("designer attempt %d had problems: %s", attempt + 1, "; ".join(problems[:6]))
        prompt = (
            user_prompt(spec, assets, ideas, request, extra_idea)
            + "\n\nYour previous page had these problems. Fix them and keep what was good. Here is the page:\n"
            + design.html
            + "\n\nProblems (remove any words that aren't tokens; do not add new words; keep text in normal flow):\n"
            + "\n".join(f"- {p}" for p in problems[:12])
        )
    raise DesignError("; ".join(problems[:6]))
