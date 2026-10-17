"""The pages of a slideshow: a title card, a page for each photo, and a closing card, all in the school's branding.

Like a flyer, every word on a page is put there by code from what the person typed and from the brand kit, so the
slogan and address are always exact. Chrome draws each page to a 1920 x 1080 PNG; the video step animates them.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from string import Template

from ..flyer.template import data_uri
from ..llm import Image

WIDTH, HEIGHT = 1920, 1080

STYLE = Template(
    """
  :root { --navy: ${navy}; --blue: ${blue}; --green: ${green}; --gold: ${gold}; --cream: ${cream}; --background: ${background}; --text: ${text}; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 1920px; height: 1080px; overflow: hidden; }
  body { font-family: ${font_stack}; color: var(--text); background: var(--background); }
  .page { position: relative; width: 1920px; height: 1080px; display: flex; flex-direction: column; overflow: hidden; }
  .page > * { position: relative; z-index: 2; }
  .slogan {
    flex: none; padding: 20px 80px 22px; text-align: center; font-size: 27px; font-weight: 600; line-height: 1.3;
    letter-spacing: 0.01em; color: var(--navy); background: var(--background);
  }
  .slogan span { white-space: nowrap; }
  .slogan span:not(:last-child)::after { content: " \\2022 "; color: var(--gold); }
  .stripe { display: flex; height: 22px; flex: none; }
  .stripe div { flex: 1; }
  .center { flex: 1; min-height: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 0 160px; }
  .center img.logo { display: block; width: 560px; height: auto; margin-bottom: 44px; }
  h1 { font-weight: 800; line-height: 1.05; letter-spacing: -0.01em; text-wrap: balance; overflow-wrap: anywhere; color: var(--navy); }
  .rule { width: 170px; height: 12px; border-radius: 6px; background: var(--gold); margin: 36px 0 30px; }
  .sub { font-size: 50px; line-height: 1.25; color: var(--blue); max-width: 1300px; text-wrap: balance; }
  .motif { position: absolute; z-index: 1; }
  /* The closing card is navy, so its words are cream and the stripe sits at the bottom. */
  .dark { background: var(--navy); }
  .dark h1, .dark .sub { color: var(--cream); }
  .dark .sub { font-size: 44px; }
  .dark .slogan { background: var(--navy); color: var(--cream); }
  .dark .address { margin-top: 34px; font-size: 36px; color: var(--gold); font-weight: 600; }
  /* A photo page: the photo itself, whole, over a soft blur of itself so any shape of photo fills the frame. */
  .photo-page { background: var(--navy); }
  .backdrop { position: absolute !important; inset: -60px; z-index: 0 !important; width: calc(100% + 120px); height: calc(100% + 120px); object-fit: cover; filter: blur(46px) brightness(0.62) saturate(1.1); }
  .stage { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; padding: 80px 90px 36px; }
  .stage img { max-width: 100%; max-height: 100%; border-radius: 14px; border: 10px solid var(--background); box-shadow: 0 14px 44px rgba(0, 20, 34, 0.55); }
"""
)


def _brand(brand_dir: Path) -> dict:
    return json.loads((brand_dir / "brand.json").read_text())


def _page(brand: dict, body: str, title: str) -> str:
    style = STYLE.substitute(**brand["colors"], font_stack=brand["fonts"]["stack"])
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title>'
        f"<style>{style}</style></head><body>{body}</body></html>"
    )


def _slogan(brand: dict) -> str:
    parts = "".join(f"<span>{escape(p)}</span> " for p in brand["slogan"]["parts"])
    return f'<div class="slogan">{parts}</div><div class="stripe"><div style="background: var(--navy)"></div><div style="background: var(--green)"></div><div style="background: var(--gold)"></div></div>'


def _motif(brand: dict, brand_dir: Path, name: str, style: str) -> str:
    m = brand["motifs"][name]
    src = data_uri((brand_dir / m["file"]).read_bytes(), "image/svg+xml")
    return f'<img class="motif" src="{src}" style="{style}" alt="">'


def _logo(brand: dict, brand_dir: Path, which: str) -> str:
    src = data_uri((brand_dir / brand["logos"][which]["file"]).read_bytes())
    return f'<img class="logo" src="{src}" alt="{escape(brand["name"], quote=True)}">'


def title_size(title: str) -> int:
    n = len(title)
    return 132 if n <= 16 else 108 if n <= 28 else 88 if n <= 44 else 70


def title_card(brand_dir: Path, title: str, subtitle: str | None = None) -> str:
    brand = _brand(brand_dir)
    body = (
        '<div class="page">'
        + _motif(brand, brand_dir, "bunting", "top: -6px; left: 0; width: 760px")
        + _motif(brand, brand_dir, "bunting", "top: -6px; right: 0; width: 760px; transform: scaleX(-1)")
        + _motif(brand, brand_dir, "flowers", "left: 0; bottom: 44px; width: 1920px; opacity: 0.9")
        + '<div class="center">'
        + _logo(brand, brand_dir, "primary")
        + f'<h1 style="font-size: {title_size(title)}px">{escape(title)}</h1>'
        + '<div class="rule"></div>'
        + (f'<p class="sub">{escape(subtitle)}</p>' if subtitle else "")
        + "</div>"
        + _slogan(brand)
        + "</div>"
    )
    return _page(brand, body, title)


def photo_page(brand_dir: Path, photo: Image, title: str) -> str:
    brand = _brand(brand_dir)
    src = data_uri(photo.data, photo.media_type)
    body = (
        '<div class="page photo-page">'
        f'<img class="backdrop" src="{src}" alt="">'
        f'<div class="stage"><img src="{src}" alt=""></div>'
        + _slogan(brand)
        + "</div>"
    )
    return _page(brand, body, title)


def end_card(brand_dir: Path, text: str | None = None, address: bool = False) -> str:
    """The closing card: the logo for dark backgrounds, the person's closing line if any, and the school's address
    when the event was at the school."""
    brand = _brand(brand_dir)
    body = (
        '<div class="page dark">'
        + _motif(brand, brand_dir, "sparkles", "right: 60px; top: 30px; width: 380px; opacity: 0.8")
        + '<div class="center">'
        + _logo(brand, brand_dir, "on_dark")
        + (f'<h1 style="font-size: {title_size(text)}px">{escape(text)}</h1>' if text else "")
        + (f'<p class="address">{escape(brand["address"]["text"])}</p>' if address else "")
        + "</div>"
        + _slogan(brand)
        + "</div>"
    )
    return _page(brand, body, text or brand["name"])
