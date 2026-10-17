"""A background scene for every flyer, built from the school's art kit (brand/motifs).

Without an image provider there is still always artwork behind a flyer: this picks a scene to suit the event and the
season (autumn leaves for Thanksgiving, crayons for picture day, bunting and balloons for a party, spring flowers, a
sunny schoolyard otherwise) and arranges motifs into one full-page SVG, with the middle left calm for the words.
Generated artwork or a picture of your own replaces it.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from ..llm import Image
from .spec import FlyerSpec

W, H = 1080, 1350

# keywords in the flyer's wording that choose a scene; the first scene with a match wins
KEYWORDS = [
    ("crayons", ("picture day", "art", "craft", "paint", "draw", "class", "classroom", "school supplies", "book", "reading", "library")),
    ("autumn", ("thanksgiving", "harvest", "fall", "autumn", "halloween", "pumpkin", "turkey")),
    ("party", ("party", "picnic", "open house", "festival", "fair", "celebration", "carnival", "bbq", "barbecue", "graduation", "concert", "program", "dinner", "fellowship", "christmas", "easter")),
    ("spring", ("spring", "garden", "field day", "sports", "play", "plant", "flower")),
]

# (motif, x, y, width, mirrored, opacity) for each scene; 1080 x 1350, words go in the middle
SCENES: dict[str, list[tuple[str, int, int, int, bool, float]]] = {
    "party": [
        ("sun", 760, -160, 460, False, 1), ("hills", 0, 1050, 1080, False, 1), ("flowers", 0, 1110, 1080, False, 1),
        ("bunting", -30, -14, 430, False, 1), ("bunting", 650, -14, 430, True, 1), ("balloons", 905, 400, 160, False, 1),
        ("tree", -90, 880, 300, False, 1), ("butterflies", 40, 300, 130, False, 1), ("sparkles", 870, 760, 150, False, 0.9),
    ],
    "spring": [
        ("sun", -120, -150, 430, False, 1), ("clouds", 600, 40, 420, False, 1), ("hills", 0, 1040, 1080, False, 1),
        ("flowers", 0, 1100, 1080, False, 1), ("tree", 800, 840, 330, False, 1), ("butterflies", 60, 420, 150, False, 1),
        ("butterflies", 880, 520, 120, True, 1),
    ],
    "autumn": [
        ("leaves", -90, -40, 420, False, 1), ("leaves", 760, 20, 380, True, 0.95), ("leaves", 720, 960, 420, False, 0.95),
        ("hills", 0, 1090, 1080, False, 1), ("leaves", -110, 900, 360, True, 0.9), ("clouds", 380, 110, 360, False, 0.9),
    ],
    "crayons": [
        ("crayons", 0, 1040, 1080, False, 1), ("sparkles", -60, 20, 260, False, 1), ("sparkles", 860, 30, 240, True, 1),
        ("clouds", 330, 70, 380, False, 0.9), ("books", 830, 860, 230, False, 1), ("butterflies", 50, 640, 130, False, 1),
    ],
    "sunny": [
        ("sun", 760, -150, 440, False, 1), ("clouds", -40, 60, 380, False, 1), ("hills", 0, 1060, 1080, False, 1),
        ("flowers", 0, 1120, 1080, False, 1), ("schoolhouse", -50, 820, 300, False, 1), ("tree", 800, 870, 300, False, 1),
        ("butterflies", 880, 380, 120, False, 1),
    ],
}


def scene_for(spec: FlyerSpec) -> str:
    """The scene that suits this flyer: by its wording first, then by the season of its date."""
    words = " ".join(x for x in (spec.title, spec.subtitle, spec.details) if x).casefold()
    for scene, keys in KEYWORDS:
        if any(re.search(rf"\b{re.escape(k)}", words) for k in keys):
            return scene
    month = spec.event_date.month if spec.event_date else date.today().month
    return "autumn" if month in (10, 11) else "spring" if month in (3, 4, 5) else "sunny"


def _place(svg: str, x: int, y: int, width: int, mirrored: bool, opacity: float) -> str:
    """One motif, drawn into the scene at (x, y), `width` wide, as a nested svg."""
    m = re.search(r'<svg[^>]*viewBox="0 0 (\d+) (\d+)"[^>]*>(.*)</svg>\s*$', svg, re.S)
    vw, vh, inner = int(m.group(1)), int(m.group(2)), m.group(3)
    height = width * vh / vw
    nested = f'<svg x="0" y="0" width="{width}" height="{height:.0f}" viewBox="0 0 {vw} {vh}" overflow="visible">{inner}</svg>'
    move = f"translate({x + width} {y}) scale(-1 1)" if mirrored else f"translate({x} {y})"
    return f'<g transform="{move}" opacity="{opacity}">{nested}</g>'


def compose(spec: FlyerSpec, brand_dir: Path, scene: str | None = None) -> Image:
    """The background for this flyer: a full-page SVG scene made of the brand's own motifs."""
    brand = json.loads((brand_dir / "brand.json").read_text())
    colors = brand["colors"]
    parts = []
    for name, x, y, width, mirrored, opacity in SCENES[scene or scene_for(spec)]:
        motif = brand["motifs"][name]
        parts.append(_place((brand_dir / motif["file"]).read_text(), x, y, width, mirrored, opacity))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<defs><linearGradient id="bg-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{colors["cream"]}"/>'
        f'<stop offset="0.45" stop-color="{colors["background"]}"/></linearGradient></defs>'
        f'<rect width="{W}" height="{H}" fill="url(#bg-sky)"/>{"".join(parts)}</svg>'
    )
    return Image("image/svg+xml", svg.encode())
