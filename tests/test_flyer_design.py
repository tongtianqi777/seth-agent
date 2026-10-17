"""Free-form flyer design: the model writes the page, the program checks it and supplies every fact."""

import json
import random
import stat
from datetime import date, time
from pathlib import Path

import pytest

import seth_agent.flyer as flyer_module
import seth_agent.flyer.design as design_module
from seth_agent.extractors.base import PermanentError
from seth_agent.flyer import FlyerSpec
from seth_agent.flyer import make_flyer
from seth_agent.flyer.design import (
    FlyerDesign,
    DesignError,
    assemble,
    build_assets,
    design_flyer,
    finish_design,
    pick_ideas,
    problems_with,
)
from seth_agent.flyer.render import FlyerError, check_page
from seth_agent.llm import Image



BRAND_DIR = Path(__file__).resolve().parent.parent / "brand"
BRAND = json.loads((BRAND_DIR / "brand.json").read_text())
SPEC = FlyerSpec(
    title="Open House", event_date=date(2026, 11, 7), start_time=time(10), end_time=time(12), location="The gym",
    details="Come and see", at_school=True,
)
PHOTO = Image("image/jpeg", b"\xff\xd8\xff\xe0jpeg")
ART = Image("image/png", b"\x89PNG\r\n\x1a\nart")
BG = Image("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg' width='1080' height='1350'/>")

GOOD = """<!doctype html><html><head><title>x</title><style>
body { font-family: var(--font); } .p { width: 1080px; height: 1350px; display: flex; flex-direction: column; }
</style></head><body><div class="p">
<div style="background: url({{BACKGROUND}}) center / cover"></div><img src="{{LOGO}}" alt=""><h1>{{TITLE}}</h1><p>{{DATE}} {{TIME}}</p><p>Where: {{LOCATION}}</p><p>{{ADDRESS}}</p><p>{{DETAILS}}</p>
<img src="{{PHOTO_1}}" alt=""><img src="{{MOTIF_BUNTING}}" alt=""><p>{{SLOGAN}}</p></div></body></html>"""


NO_PHOTO = GOOD.replace('<img src="{{PHOTO_1}}" alt="">', "")


@pytest.fixture
def assets():
    return build_assets(SPEC, BRAND_DIR, BRAND, BG, [PHOTO])


def test_the_tokens_hold_the_exact_words(assets):
    assert assets.text["DATE"] == "Saturday, November 7, 2026" and assets.text["TIME"] == "10:00 AM – 12:00 PM"
    assert (assets.text["WEEKDAY"], assets.text["MONTH"], assets.text["DAY"], assets.text["YEAR"]) == ("Saturday", "November", "7", "2026")
    assert assets.text["ADDRESS"] == "1395 Hollenbeck Ave, Sunnyvale, CA 94087"
    assert assets.text["SLOGAN"] == BRAND["slogan"]["text"] and assets.text["SLOGAN_2"] == "Unity among family, school, & church"
    assert {"LOGO", "LOGO_DARK", "CREST", "PHOTO_1", "MOTIF_BUNTING", "MOTIF_RIBBON_NAVY"} <= set(assets.images)
    assert assets.images["BACKGROUND"] is BG and "PHOTO_2" not in assets.images


def test_tokens_only_exist_for_what_the_flyer_has():
    plain = build_assets(FlyerSpec(title="Picnic"), BRAND_DIR, BRAND, BG)
    assert set(plain.text) == {"TITLE", "SLOGAN", "SLOGAN_1", "SLOGAN_2", "SLOGAN_3"}
    elsewhere = build_assets(FlyerSpec(title="Picnic", location="Baylands Park"), BRAND_DIR, BRAND, BG)
    assert "ADDRESS" not in elsewhere.text and elsewhere.text["LOCATION"] == "Baylands Park"
    chosen = build_assets(FlyerSpec(title="x"), BRAND_DIR, BRAND, ART, background_required=True)
    assert chosen.images["BACKGROUND"] is ART and "you must use it" in chosen.notes["BACKGROUND"]


def test_every_flyer_needs_background_artwork(assets):
    bare = GOOD.replace('<div style="background: url({{BACKGROUND}}) center / cover"></div>', "").replace('<img src="{{MOTIF_BUNTING}}" alt="">', "")
    assert any("no background artwork" in p for p in problems_with(bare, assets))
    three = bare + "".join(f'<img src="{{{{MOTIF_{m}}}}}">' for m in ("HILLS", "TREE", "SUN"))
    assert problems_with(three, assets) == []  # a scene of art-kit motifs counts
    two = bare + '<img src="{{MOTIF_HILLS}}"><img src="{{MOTIF_SUN}}">'
    assert any("no background artwork" in p for p in problems_with(two, assets))
    drawing = bare + "<svg>" + "<circle r='3'/>" * 12 + "</svg>"
    assert problems_with(drawing, assets) == []  # so does a real SVG drawing


def test_a_background_the_person_chose_must_be_used(tmp_path):
    chosen = build_assets(SPEC, BRAND_DIR, BRAND, ART, [PHOTO], background_required=True)
    scene = GOOD.replace("{{BACKGROUND}}", "{{MOTIF_HILLS}}") + '<img src="{{MOTIF_SUN}}"><img src="{{MOTIF_TREE}}">'
    assert any("chose the background" in p for p in problems_with(scene, chosen))
    assert problems_with(GOOD, chosen) == []


def test_a_page_that_follows_the_rules_has_no_problems(assets):
    assert problems_with(GOOD, assets) == []


def test_every_listed_token_must_be_used(assets):
    problems = problems_with(GOOD.replace("<p>{{ADDRESS}}</p>", "").replace("<img src=\"{{PHOTO_1}}\" alt=\"\">", ""), assets)
    assert any("ADDRESS" in p for p in problems) and any("PHOTO_1" in p for p in problems)


def test_the_logo_and_the_slogan_are_required(assets):
    problems = problems_with(GOOD.replace('<img src="{{LOGO}}" alt="">', "").replace("<p>{{SLOGAN}}</p>", ""), assets)
    assert any("logo" in p for p in problems) and any("slogan" in p for p in problems)
    split = GOOD.replace("<p>{{SLOGAN}}</p>", "<p>{{SLOGAN_1}} • {{SLOGAN_2}}</p><p>{{SLOGAN_3}}</p>")
    assert problems_with(split, assets) == []
    assert any("slogan" in p for p in problems_with(GOOD.replace("<p>{{SLOGAN}}</p>", "<p>{{SLOGAN_1}}</p>"), assets))


def test_unknown_tokens_are_refused(assets):
    assert any("PHOTO_2" in p for p in problems_with(GOOD + "<img src='{{PHOTO_2}}'>", assets))
    assert any("ART" in p for p in problems_with(GOOD + "<div style='background: url({{ART}})'></div>", assets))


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "<iframe src='x'></iframe>",
        "<link rel='stylesheet' href='x.css'>",
        "<meta http-equiv='refresh' content='0'>",
        "<img src='https://example.com/a.png'>",
        "<div style='background: url(http://example.com/a.png)'></div>",
        "<style>@import url(x.css);</style>",
        "<img src='photo.jpg'>",
    ],
)
def test_scripts_and_outside_files_are_refused(assets, bad):
    assert problems_with(GOOD + bad, assets), bad


def test_inline_svg_and_a_charset_are_fine(assets):
    page = GOOD.replace("<head>", '<head><meta charset="utf-8">') + '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'
    assert problems_with(page, assets) == []


def test_words_the_model_invents_are_refused(assets):
    problems = problems_with(GOOD + "<p>Free admission and pizza!</p>", assets)
    assert any("Free admission" in p for p in problems)
    assert problems_with(GOOD + "<p>Where:</p><p>When</p><p>•</p><p>2026</p>", assets) == []  # labels, symbols, numbers


@pytest.mark.parametrize("color", ["#ff0000", "#abc", "#12345678", "red", "rebeccapurple;fill:blue"])
def test_colors_outside_the_palette_are_refused(assets, color):
    style = f"fill: {color}" if color[0] != "#" else f"color: {color}"
    assert any("color" in p for p in problems_with(GOOD + f"<div style='{style}'>x</div>".replace("x</div>", "</div>"), assets)), color


def test_the_schools_colors_are_fine_in_any_spelling(assets):
    page = GOOD + (
        "<div style='color:#002A46; background:#fff; border-color:#F1A618'></div>"
        "<svg><path fill='#739e30' stroke='#000'/><circle fill='rgba(241,166,24,.4)'/></svg>"
        "<div style='background: color-mix(in srgb, var(--green) 40%, white); color: var(--cream)'></div>"
        "<div style='background:#00000000'></div>"
    )
    assert problems_with(page, assets) == []


def test_only_the_schools_font_may_be_used(assets):
    assert any("font" in p for p in problems_with(GOOD.replace("var(--font)", "'Comic Sans MS', cursive"), assets))
    assert problems_with(GOOD.replace("var(--font)", "inherit"), assets) == []


def test_assembling_fills_in_real_text_and_pictures(assets):
    page = assemble(GOOD, assets, BRAND)
    assert "{{" not in page and "<h1>Open House</h1>" in page and "Saturday, November 7, 2026" in page
    assert "10:00 AM – 12:00 PM" in page and "1395 Hollenbeck Ave, Sunnyvale, CA 94087" in page
    assert 'src="data:image/png;base64,' in page and "#logo" in page and "data:image/svg+xml;base64," in page and "#photo" in page
    assert "--navy: #002a46" in page and "overflow: hidden" in page and page.count("<head>") == 1


def test_text_is_escaped_when_filled_in():
    assets = build_assets(FlyerSpec(title="<b>Tom & Jerry</b>"), BRAND_DIR, BRAND, BG)
    page = assemble("<html><head></head><body><h1>{{TITLE}}</h1><p>{{SLOGAN}}</p><img src='{{LOGO}}'></body></html>", assets, BRAND)
    assert "&lt;b&gt;Tom &amp; Jerry&lt;/b&gt;" in page and "<b>Tom" not in page


def test_a_page_without_a_head_or_html_tag_is_still_wrapped(assets):
    page = assemble("<h1>{{TITLE}}</h1>", assets, BRAND)
    assert page.startswith("<!doctype html>") and "--navy" in page and "<h1>Open House</h1>" in page


class DesignerLLM:
    """Writes the pages it is given, one per call, and remembers what it was asked."""

    def __init__(self, *pages):
        self.pages, self.prompts, self.calls = list(pages), [], []

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        self.prompts.append(prompt)
        self.calls.append((effort, max_tokens))
        return FlyerDesign(html=self.pages.pop(0))


@pytest.fixture
def chrome_says(monkeypatch):
    """Replaces the browser check with scripted answers (a list of problems per page drawn)."""
    answers: list[list[str]] = []
    monkeypatch.setattr(design_module, "check_page", lambda page, chrome=None: answers.pop(0))
    return answers


def test_a_good_page_is_accepted_at_once(assets, chrome_says):
    chrome_says.append([])
    llm = DesignerLLM(GOOD)
    page = design_flyer(llm, SPEC, "Open House", assets, BRAND, ["a poster"], effort="none", max_tokens=1234)
    assert "Saturday, November 7, 2026" in page and llm.calls == [("none", 1234)]
    assert "a poster" in llm.prompts[0] and "{{MOTIF_BUNTING}}" in llm.prompts[0] and "Open House" in llm.prompts[0]


def test_a_page_with_problems_is_sent_back_with_them_listed(assets, chrome_says):
    chrome_says.extend([['the text "x" runs off the page'], []])
    llm = DesignerLLM(GOOD, GOOD)
    design_flyer(llm, SPEC, "Open House", assets, BRAND, [])
    assert len(llm.prompts) == 2 and 'runs off the page' in llm.prompts[1] and "{{TITLE}}" in llm.prompts[1]  # the old page is quoted too
    bad = GOOD + "<p>Hello there!</p>"
    chrome_says.append([])
    llm = DesignerLLM(bad, GOOD)
    design_flyer(llm, SPEC, "Open House", assets, BRAND, [])
    assert "Hello there" in llm.prompts[1]  # problems found before drawing are sent back too


def test_a_model_that_cant_follow_the_rules_is_given_up_on(assets, chrome_says):
    llm = DesignerLLM(*[GOOD + "<script>1</script>"] * (design_module.MAX_REVISIONS + 1))
    with pytest.raises(DesignError, match="tag that isn't allowed"):
        design_flyer(llm, SPEC, "x", assets, BRAND, [])
    assert len(llm.prompts) == design_module.MAX_REVISIONS + 1


def test_a_hand_made_page_gets_the_same_checks_and_no_retries(assets, chrome_says):
    chrome_says.append([])
    assert "Open House" in finish_design(GOOD, assets, BRAND)
    with pytest.raises(DesignError, match="never uses"):
        finish_design(GOOD.replace("<p>{{DETAILS}}</p>", ""), assets, BRAND)
    chrome_says.append(["the logo is too small"])
    with pytest.raises(DesignError, match="logo is too small"):
        finish_design(GOOD, assets, BRAND)


def test_each_flyer_is_pointed_at_different_looks():
    assert len(set(map(tuple, (pick_ideas(random.Random(i)) for i in range(20))))) > 5
    assert pick_ideas(random.Random(1)) == pick_ideas(random.Random(1))
    first, second = pick_ideas(random.Random(3))
    assert first != second


class SpecThenDesign:
    """Answers the flyer request with a spec, then plays designer."""

    def __init__(self, *pages):
        self.designer = DesignerLLM(*pages)

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        return SPEC if schema is FlyerSpec else self.designer.parse(system, prompt, schema, images, effort, max_tokens)


@pytest.fixture
def drawn(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(flyer_module, "render_png", lambda html, out, chrome=None: (seen.append(html), out.write_bytes(b"PNG")))
    return seen


def test_with_no_layout_the_model_designs_the_flyer(tmp_path, drawn, chrome_says):
    chrome_says.append([])
    llm = SpecThenDesign(NO_PHOTO)
    flyer = make_flyer(
        llm, "Open House", tmp_path / "o.png", BRAND_DIR, date(2026, 10, 17), layout=None, idea="like a vintage poster",
        rng=random.Random(2),
    )
    assert flyer.design == "creative" and flyer.design_error is None and len(flyer.ideas) == 2
    assert "<h1>Open House</h1>" in drawn[0] and "like a vintage poster" in llm.designer.prompts[0]


def test_when_the_design_fails_the_standard_layout_takes_over(tmp_path, drawn, chrome_says):
    llm = SpecThenDesign(*["<p>no tokens at all</p>"] * (design_module.MAX_REVISIONS + 1))
    flyer = make_flyer(llm, "Open House", tmp_path / "o.png", BRAND_DIR, date(2026, 10, 17), layout=None)
    assert flyer.design == "event" and "never uses" in flyer.design_error and flyer.path.exists()
    assert 'class="page' in drawn[0] and "Saturday, November 7, 2026" in drawn[0]


def test_a_failing_model_also_falls_back(tmp_path, drawn):
    class Failing(SpecThenDesign):
        def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
            if schema is FlyerSpec:
                return SPEC
            raise PermanentError("hit max_tokens before finishing")

    flyer = make_flyer(Failing(), "x", tmp_path / "o.png", BRAND_DIR, date(2026, 10, 17), layout=None)
    assert flyer.design == "event" and "max_tokens" in flyer.design_error


def test_a_design_file_is_used_or_refused(tmp_path, drawn, chrome_says):
    mine = tmp_path / "mine.html"
    mine.write_text(NO_PHOTO)
    chrome_says.append([])
    flyer = make_flyer(SpecThenDesign(), "x", tmp_path / "o.png", BRAND_DIR, date(2026, 10, 17), design_file=mine)
    assert flyer.design == "hand-made" and "<h1>Open House</h1>" in drawn[0]
    mine.write_text("<p>nothing</p>")
    with pytest.raises(DesignError):
        make_flyer(SpecThenDesign(), "x", tmp_path / "p.png", BRAND_DIR, date(2026, 10, 17), design_file=mine)


def test_fixed_layouts_are_still_available(tmp_path, drawn):
    flyer = make_flyer(SpecThenDesign(), "x", tmp_path / "o.png", BRAND_DIR, date(2026, 10, 17), layout="event-collage")
    assert flyer.design == "event-collage" and flyer.design_error is None


def fake_chrome(tmp_path: Path, dom: str) -> str:
    script = tmp_path / "chrome"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{dom}\nEOF\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_the_browser_check_reads_the_report_the_page_wrote(tmp_path):
    dom = '<html data-seth-report="[&quot;the logo is too small&quot;, &quot;the logo is too small&quot;, &quot;a photo is too small&quot;]"></html>'
    assert check_page("<html><body></body></html>", fake_chrome(tmp_path, dom)) == ["the logo is too small", "a photo is too small"]
    assert check_page("<html><body></body></html>", fake_chrome(tmp_path, '<html data-seth-report="[]"></html>')) == []
    assert "didn't finish" in check_page("<html></html>", fake_chrome(tmp_path, "<html></html>"))[0]


def test_the_browser_check_needs_a_browser(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(FlyerError):
        check_page("<html></html>")
