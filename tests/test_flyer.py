import base64
import json
import stat
from datetime import date, time
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

import seth_agent.flyer as flyer_module
from seth_agent.config import Config
from seth_agent.extractors.base import PermanentError
from seth_agent.flyer import ArtError, FlyerSpec, extract_spec, render_html
from seth_agent.flyer import make_flyer
from seth_agent.flyer.art import ArtBrief, OpenAIImages, art_prompt, build_image_gen, plan_art
from seth_agent.llm import Image
from seth_agent.flyer.render import FlyerError, find_chrome, render_png
from seth_agent.flyer.template import format_date, format_time, format_times, title_size

BRAND = Path(__file__).resolve().parent.parent / "brand"



class ScriptedLLM:
    def __init__(self, spec: FlyerSpec):
        self.spec, self.calls = spec, []

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        self.calls.append((system, prompt, schema))
        return self.spec


OPEN_HOUSE = FlyerSpec(title="Open House", event_date=date(2026, 11, 7), start_time=time(10), end_time=time(12))


def fake_chrome(tmp_path: Path, body: str = 'printf PNG > "$shot"') -> str:
    """A stand-in browser that finds --screenshot=<path> in its arguments and runs `body`."""
    script = tmp_path / "chrome"
    script.write_text(f'#!/bin/sh\nfor a in "$@"; do case "$a" in --screenshot=*) shot="${{a#--screenshot=}}";; esac; done\n{body}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_dates_and_times_are_written_the_same_way_every_time():
    assert format_date(date(2026, 11, 7)) == "Saturday, November 7, 2026"
    assert format_time(time(0, 0)) == "12:00 AM"
    assert format_time(time(12, 30)) == "12:30 PM"
    assert format_time(time(15, 5)) == "3:05 PM"
    assert format_times(time(10), time(12)) == "10:00 AM – 12:00 PM"
    assert format_times(time(10), None) == "10:00 AM"
    assert format_times(None, time(12)) == "Until 12:00 PM"
    assert format_times(None, None) is None


def test_the_request_goes_to_the_model_with_todays_date():
    llm = ScriptedLLM(OPEN_HOUSE)
    spec = extract_spec(llm, "  Open House on 11/7/2026 at 10am - 12pm ", date(2026, 10, 17))
    assert spec is OPEN_HOUSE
    _, prompt, schema = llm.calls[0]
    assert schema is FlyerSpec
    assert "Saturday, 2026-10-17" in prompt and prompt.endswith("Open House on 11/7/2026 at 10am - 12pm")


def test_free_text_starts_with_a_capital():
    assert FlyerSpec(title="Picture Day", details="wear your school uniform").details == "Wear your school uniform"


def test_blank_optional_fields_become_none_and_a_blank_title_is_refused():
    spec = FlyerSpec(title=" Picnic ", subtitle="  ", location="", details=None)
    assert (spec.title, spec.subtitle, spec.location, spec.details) == ("Picnic", None, None, None)
    with pytest.raises(ValidationError):
        FlyerSpec(title="   ")
    with pytest.raises(ValidationError):
        FlyerSpec(title="x" * 61)


def test_the_flyer_carries_the_brand_and_the_exact_words():
    html = render_html(OPEN_HOUSE, BRAND)
    assert "Saturday, November 7, 2026" in html and "10:00 AM – 12:00 PM" in html
    assert "<h1>Open House</h1>" in html
    assert "#002a46" in html and "#f1a618" in html  # navy and gold, from brand.json
    assert "Avenir Next" in html
    assert (BRAND / "logos/logo-light.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "src=\"data:image/png;base64,iVBOR" in html  # the real logo file, not a redrawn one
    assert "${" not in html


def test_fields_the_request_didnt_have_leave_no_empty_blocks():
    html = render_html(FlyerSpec(title="Picture Day"), BRAND)
    for gone in ('class="when"', 'class="where"', 'class="subtitle"', 'class="details"'):
        assert f"<div {gone}" not in html and f"<p {gone}" not in html
    full = render_html(
        FlyerSpec(title="Picnic", subtitle="Sun", location="The park", details="Bring a blanket", event_date=date(2026, 5, 2)),
        BRAND,
    )
    for there in ("The park", "Bring a blanket", "Sun", "Saturday, May 2, 2026"):
        assert there in full


SLOGAN_PARTS = ["Christ-centered education", "Unity among family, school, &amp; church", "Building Christian character"]
ADDRESS = "1395 Hollenbeck Ave, Sunnyvale, CA 94087"


def test_every_flyer_carries_the_slogan_whatever_it_says():
    for spec in (FlyerSpec(title="Picture Day"), OPEN_HOUSE, FlyerSpec(title="Picnic", location="The park")):
        html = render_html(spec, BRAND)
        positions = [html.index(f"<span>{part}</span>") for part in SLOGAN_PARTS]
        assert positions == sorted(positions)


def test_the_school_address_is_printed_for_events_at_the_school():
    html = render_html(OPEN_HOUSE.model_copy(update={"at_school": True}), BRAND)
    assert ADDRESS in html and 'class="label">Where' in html
    assert ADDRESS not in render_html(OPEN_HOUSE, BRAND)  # not asked for: no address


def test_a_place_in_the_school_gets_the_address_under_it():
    html = render_html(FlyerSpec(title="Parent Night", location="The gym", at_school=True), BRAND)
    assert html.index("The gym") < html.index(ADDRESS)
    assert f'class="address">{ADDRESS}' in html and f'class="place">{ADDRESS}' not in html
    assert f'class="place">{ADDRESS}' in render_html(FlyerSpec(title="Open House", at_school=True), BRAND)


def test_the_address_is_not_printed_twice():
    spec = FlyerSpec(title="Open House", location="1395 Hollenbeck Ave", at_school=True)
    assert render_html(spec, BRAND).count("Hollenbeck") == 1


def test_a_place_elsewhere_has_no_school_address():
    assert "Hollenbeck" not in render_html(FlyerSpec(title="Picnic", location="Baylands Park"), BRAND)


def test_the_person_can_force_the_address_on_or_off(tmp_path, pages):
    off = make_flyer(ScriptedLLM(OPEN_HOUSE.model_copy(update={"at_school": True})), "x", tmp_path / "a.png", BRAND, date(2026, 10, 17), show_address=False)
    on = make_flyer(ScriptedLLM(OPEN_HOUSE), "x", tmp_path / "b.png", BRAND, date(2026, 10, 17), show_address=True)
    assert ADDRESS not in pages[0] and ADDRESS in pages[1]
    assert not off.spec.at_school and on.spec.at_school


def test_the_model_decides_by_default(tmp_path, pages):
    make_flyer(ScriptedLLM(OPEN_HOUSE.model_copy(update={"at_school": True})), "x", tmp_path / "a.png", BRAND, date(2026, 10, 17))
    assert ADDRESS in pages[0]


def test_text_from_the_request_is_escaped():
    html = render_html(FlyerSpec(title="<script>alert(1)</script> & Co", details='"quoted" <b>'), BRAND)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co" in html and "&lt;b&gt;" in html


def test_longer_titles_get_smaller_type():
    assert title_size("Open House") > title_size("Spring Family Picnic Day") > title_size("A" * 60)


def test_render_png_runs_the_browser_on_the_page_and_saves_the_picture(tmp_path):
    out = tmp_path / "deep" / "flyer.png"
    render_png("<html></html>", out, fake_chrome(tmp_path))
    assert out.read_bytes() == b"PNG"


def test_render_png_reports_a_browser_that_draws_nothing(tmp_path):
    with pytest.raises(FlyerError, match="didn't produce"):
        render_png("<html></html>", tmp_path / "x.png", fake_chrome(tmp_path, "exit 1"))


def test_a_missing_browser_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(FlyerError, match="SETH_CHROME"):
        find_chrome()
    with pytest.raises(FlyerError, match="SETH_CHROME"):
        find_chrome("/nope/chrome")


def test_make_flyer_end_to_end(tmp_path):
    flyer = make_flyer(
        ScriptedLLM(OPEN_HOUSE), "Open House", tmp_path / "o.png", BRAND, date(2026, 10, 17), fake_chrome(tmp_path)
    )
    assert flyer.spec is OPEN_HOUSE and flyer.path.read_bytes() == b"PNG"
    assert flyer.art_path is None and flyer.art_error is None


# ---- artwork ----

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fakepicture"
BRAND_ART = json.loads((BRAND / "brand.json").read_text())["art"]


class ArtLLM(ScriptedLLM):
    """Answers the flyer request with a spec, and the art request with a scene."""

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        self.calls.append((system, prompt, schema))
        return ArtBrief(scene="An open wooden door beside a big oak tree.") if schema is ArtBrief else self.spec


class FakeImages:
    def __init__(self, image=None, error=None):
        self.image, self.error, self.prompts = image or Image("image/png", PNG_BYTES), error, []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.image


@pytest.fixture
def pages(monkeypatch):
    """Captures the HTML each flyer is drawn from instead of starting a browser."""
    seen: list[str] = []
    monkeypatch.setattr(flyer_module, "render_png", lambda html, out, chrome=None: (seen.append(html), out.write_bytes(b"PNG")))
    return seen


def test_the_brand_has_art_direction():
    assert set(BRAND_ART) == {"style", "palette", "composition", "avoid"}
    assert all(BRAND_ART.values())


def test_the_art_prompt_is_the_scene_plus_the_brand_direction():
    prompt = art_prompt(ArtBrief(scene=" A kite over a hill. "), BRAND_ART)
    assert "Scene: A kite over a hill." in prompt
    for line in BRAND_ART.values():
        assert line in prompt


def test_only_the_wording_not_the_date_or_place_is_shared_to_plan_the_art():
    spec = FlyerSpec(title="Open House", subtitle="Meet our teachers", details="Bring a friend", location="12 Oak Lane", event_date=date(2026, 11, 7))
    llm = ArtLLM(spec)
    plan_art(llm, spec, BRAND_ART)
    _, sent, schema = llm.calls[0]
    assert schema is ArtBrief and "Open House" in sent and "Bring a friend" in sent
    assert "Oak Lane" not in sent and "2026" not in sent


def image_client(handler, **kw):
    seen: list[httpx.Request] = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped), base_url="http://images/v1")
    return OpenAIImages("sk-test", client=client, **kw), seen


def test_openai_images_asks_for_a_portrait_picture_and_decodes_it():
    images, seen = image_client(lambda r: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}))
    got = images.generate("a kite")
    assert got == Image("image/png", PNG_BYTES)
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/images/generations" and body["prompt"] == "a kite"
    assert body["model"] == "gpt-image-1" and body["size"] == "1024x1536" and body["quality"] == "high" and body["n"] == 1


def test_openai_images_follows_a_url_when_the_server_sends_one():
    def handler(request):
        if request.url.path.endswith("/generations"):
            return httpx.Response(200, json={"data": [{"url": "http://images/files/1.png"}]})
        return httpx.Response(200, content=PNG_BYTES)

    images, _ = image_client(handler)
    assert images.generate("x").data == PNG_BYTES


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": {"message": "content policy"}}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": [{}]}),
        httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"GIF89a").decode()}]}),
    ],
)
def test_openai_images_failures_are_art_errors(response):
    images, _ = image_client(lambda r: response)
    with pytest.raises(ArtError):
        images.generate("x")


def test_an_unreachable_image_server_is_an_art_error():
    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ArtError, match="reach"):
        image_client(down)[0].generate("x")


def config(monkeypatch, **env):
    for key in ("SETH_IMAGE_PROVIDER", "SETH_IMAGE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Config.from_env()


def test_artwork_is_off_unless_an_image_provider_is_chosen(monkeypatch):
    assert build_image_gen(config(monkeypatch, OPENAI_API_KEY="sk-x")) is None


def test_choosing_openai_needs_a_key(monkeypatch):
    with pytest.raises(ArtError, match="OPENAI_API_KEY"):
        build_image_gen(config(monkeypatch, SETH_IMAGE_PROVIDER="openai"))
    gen = build_image_gen(config(monkeypatch, SETH_IMAGE_PROVIDER="openai", OPENAI_API_KEY="sk-x", SETH_IMAGE_QUALITY="low"))
    assert isinstance(gen, OpenAIImages) and gen.quality == "low"


def test_a_misspelled_image_provider_is_refused(monkeypatch):
    with pytest.raises(SystemExit, match="SETH_IMAGE_PROVIDER"):
        config(monkeypatch, SETH_IMAGE_PROVIDER="dalle")


def test_generated_art_goes_behind_the_flyer_and_is_kept(tmp_path, pages):
    images = FakeImages()
    flyer = make_flyer(ArtLLM(OPEN_HOUSE), "Open House", tmp_path / "o.png", BRAND, date(2026, 10, 17), image_gen=images)
    assert flyer.art_path == tmp_path / "o-art.png" and flyer.art_path.read_bytes() == PNG_BYTES
    assert "An open wooden door" in images.prompts[0] and BRAND_ART["style"] in images.prompts[0]
    assert 'class="art"' in pages[0] and base64.b64encode(PNG_BYTES).decode() in pages[0]


def test_a_jpeg_from_the_image_server_is_saved_as_a_jpeg(tmp_path, pages):
    jpeg = Image("image/jpeg", b"\xff\xd8\xff\xe0jpeg")
    flyer = make_flyer(ArtLLM(OPEN_HOUSE), "x", tmp_path / "o.png", BRAND, date(2026, 10, 17), image_gen=FakeImages(jpeg))
    assert flyer.art_path.suffix == ".jpg" and "data:image/jpeg;base64," in pages[0]


@pytest.mark.parametrize("error", [ArtError("server down"), PermanentError("refusal")])
def test_when_the_art_fails_the_flyer_still_has_a_background(tmp_path, pages, error):
    flyer = make_flyer(ArtLLM(OPEN_HOUSE), "x", tmp_path / "o.png", BRAND, date(2026, 10, 17), image_gen=FakeImages(error=error))
    assert flyer.path.exists() and flyer.art_path is None and str(error) in flyer.art_error
    assert flyer.scene and 'class="art"' in pages[0] and "data:image/svg+xml;base64," in pages[0]  # the art-kit scene stands in


def test_your_own_picture_can_be_the_background_and_costs_nothing(tmp_path, pages):
    mine = tmp_path / "mine.png"
    mine.write_bytes(PNG_BYTES)
    images = FakeImages()
    flyer = make_flyer(ArtLLM(OPEN_HOUSE), "x", tmp_path / "o.png", BRAND, date(2026, 10, 17), image_gen=images, art_file=mine)
    assert images.prompts == [] and flyer.art_path == mine and 'class="art"' in pages[0]


def test_a_picture_you_gave_that_cant_be_used_is_an_error_not_a_quiet_omission(tmp_path, pages):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    with pytest.raises(ArtError):
        make_flyer(ArtLLM(OPEN_HOUSE), "x", tmp_path / "o.png", BRAND, date(2026, 10, 17), art_file=notes)
    with pytest.raises(OSError):
        make_flyer(ArtLLM(OPEN_HOUSE), "x", tmp_path / "o.png", BRAND, date(2026, 10, 17), art_file=tmp_path / "missing.png")


# ---- layouts ----


def test_the_brand_kit_has_the_standard_and_the_collage_layout():
    from seth_agent.flyer.template import available_layouts

    assert available_layouts(BRAND) == ["event", "event-collage"]


@pytest.mark.parametrize("layout", ["event", "event-collage"])
def test_every_layout_fills_in_completely(layout):
    photo = Image("image/jpeg", b"\xff\xd8\xff\xe0jpeg")
    spec = FlyerSpec(title="Open House", subtitle="Hi", event_date=date(2026, 11, 7), location="The gym", details="Come", at_school=True)
    html = render_html(spec, BRAND, Image("image/png", PNG_BYTES), [photo, photo], layout)
    assert "${" not in html and 'class="page has-photos"' in html and html.count('<img src="data:image/jpeg') == 2
    assert 'class="art"' in html and ADDRESS in html and "Building Christian character" in html


def test_a_flyer_can_use_another_layout(tmp_path, pages):
    make_flyer(ScriptedLLM(OPEN_HOUSE), "x", tmp_path / "o.png", BRAND, date(2026, 10, 17), layout="event-collage")
    assert ".photos img:nth-child(4n+1)" in pages[0]
    with pytest.raises(FileNotFoundError):
        render_html(OPEN_HOUSE, BRAND, template="nope")
