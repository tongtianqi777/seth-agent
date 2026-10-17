"""Every flyer has a background: a scene from the school's art kit unless there is other artwork."""

import base64
from datetime import date
from pathlib import Path

import pytest

import seth_agent.flyer as flyer_module
from seth_agent.flyer import FlyerSpec, make_flyer
from seth_agent.flyer.background import SCENES, compose, scene_for
from seth_agent.llm import Image

BRAND = Path(__file__).resolve().parent.parent / "brand"
TODAY = date(2026, 10, 17)


def test_the_scene_suits_the_wording():
    for title, scene in [
        ("Picture Day", "crayons"), ("Thanksgiving Dinner", "autumn"), ("Spring Picnic", "party"),
        ("Open House", "party"), ("Art Show", "crayons"), ("Field Day", "spring"),
    ]:
        assert scene_for(FlyerSpec(title=title, event_date=date(2026, 6, 1))) == scene, title


def test_otherwise_the_scene_follows_the_season():
    spec = lambda m: FlyerSpec(title="Parent Meeting", event_date=date(2026, m, 5))
    assert scene_for(spec(11)) == "autumn" and scene_for(spec(4)) == "spring" and scene_for(spec(7)) == "sunny"


def test_words_must_start_a_word_to_count():
    assert scene_for(FlyerSpec(title="Carpool Update", event_date=date(2026, 7, 1))) == "sunny"  # "art" isn't in "Carpool"... but "car" starts nothing
    assert scene_for(FlyerSpec(title="Heartfelt Thanks", event_date=date(2026, 7, 1))) == "sunny"  # "art" inside "Heartfelt"


@pytest.mark.parametrize("scene", sorted(SCENES))
def test_every_scene_is_a_full_page_svg_of_art_kit_motifs(scene):
    image = compose(FlyerSpec(title="x"), BRAND, scene)
    svg = image.data.decode()
    assert image.media_type == "image/svg+xml" and 'viewBox="0 0 1080 1350"' in svg and svg.count("<svg") > 4
    assert "bg-sky" in svg and "#f7f4ee" in svg  # the brand's own sky color


class Spec:
    def __init__(self, spec):
        self.spec = spec

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        return self.spec


@pytest.fixture
def pages(monkeypatch):
    seen = []
    monkeypatch.setattr(flyer_module, "render_png", lambda html, out, chrome=None: (seen.append(html), out.write_bytes(b"PNG")))
    return seen


def test_a_flyer_with_nothing_else_gets_a_scene(tmp_path, pages):
    flyer = make_flyer(Spec(FlyerSpec(title="Picture Day")), "x", tmp_path / "o.png", BRAND, TODAY)
    assert flyer.scene == "crayons" and 'class="art"' in pages[0] and "data:image/svg+xml;base64," in pages[0]
    assert flyer.art_path is None  # a scene isn't a saved picture


def test_your_own_picture_replaces_the_scene(tmp_path, pages):
    mine = tmp_path / "mine.png"
    mine.write_bytes(b"\x89PNG\r\n\x1a\nmine")
    flyer = make_flyer(Spec(FlyerSpec(title="Picture Day")), "x", tmp_path / "o.png", BRAND, TODAY, art_file=mine)
    assert flyer.scene is None and "data:image/svg+xml" not in pages[0] and base64.b64encode(mine.read_bytes()).decode() in pages[0]


def test_when_generated_art_fails_the_scene_stands_in(tmp_path, pages):
    from seth_agent.flyer import ArtError

    class Down:
        def generate(self, prompt):
            raise ArtError("server down")

    class Both(Spec):
        def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
            from seth_agent.flyer.art import ArtBrief

            return ArtBrief(scene="a door") if schema is ArtBrief else self.spec

    flyer = make_flyer(Both(FlyerSpec(title="Open House")), "x", tmp_path / "o.png", BRAND, TODAY, image_gen=Down())
    assert flyer.art_error == "server down" and flyer.scene == "party" and "data:image/svg+xml;base64," in pages[0]


@pytest.mark.parametrize("layout", ["event", "event-collage"])
def test_both_fixed_layouts_show_the_scene(layout, tmp_path, pages):
    make_flyer(Spec(FlyerSpec(title="Open House")), "x", tmp_path / "o.png", BRAND, TODAY, layout=layout)
    assert 'class="art"' in pages[0] and 'class="veil"' in pages[0]
