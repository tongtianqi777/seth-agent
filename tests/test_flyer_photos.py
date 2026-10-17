"""Photos from the school's photo library on a flyer."""

import io
from datetime import date
from pathlib import Path

import pytest

import seth_agent.flyer as flyer_module
import seth_agent.flyer.photos as photos_module
from conftest import CHAT
from seth_agent.agent import Agent
from seth_agent.config import Config
from seth_agent.extractors.base import PermanentError
from seth_agent.extractors.media import MediaExtractor
from seth_agent.flyer import FlyerSpec, PhotoError, PhotoLibrary
from seth_agent.flyer import make_flyer
from seth_agent.flyer.photos import PhotoBrief, plan_photos
from seth_agent.llm import Image
from seth_agent.media_search import pick_diverse
from test_media import PE, PE_CHAT, FakeEmbedder, photo

BRAND = Path(__file__).resolve().parent.parent / "brand"
TODAY = date(2026, 10, 17)
SPEC = FlyerSpec(title="Picnic")


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def indexed(feed, store, source, embedder):
    """A library with three different Preschool photos, a copy of one, and one in another group."""
    photo(feed, "A", 1, b"soccer soccer field")
    photo(feed, "B", 2, b"table table lunch")
    photo(feed, "C", 3, b"eating eating at table")
    photo(feed, "D", 4, b"soccer soccer field")  # the same picture again
    photo(feed, "E", 5, b"playground", group=PE, chat_jid=PE_CHAT)
    Agent(store, source, [MediaExtractor(store, source, embedder)], embedder).poll_once()


@pytest.fixture
def library(indexed, store, source, embedder, monkeypatch):
    monkeypatch.setattr(photos_module, "prepare", lambda data: Image("image/jpeg", data))  # the fake photos aren't pictures
    return PhotoLibrary(store, source, embedder, group="Preschool", min_score=0.5)


def ids(found):
    return [r["file_id"] for r in found]


def test_the_best_matches_come_first_and_a_photo_is_not_picked_twice(indexed, store, source, embedder):
    found = pick_diverse(store, source, embedder, ["soccer"], "Preschool", count=3, min_score=0.0)
    assert ids(found)[0] in (1, 4) and len([i for i in ids(found) if i in (1, 4)]) == 1


def test_several_descriptions_give_variety(indexed, store, source, embedder):
    found = pick_diverse(store, source, embedder, ["soccer", "eating"], "Preschool", count=3, min_score=0.5)
    assert sorted(ids(found)) in ([1, 3], [3, 4])


def test_photos_that_dont_match_well_are_left_out(indexed, store, source, embedder):
    assert pick_diverse(store, source, embedder, ["chemistry"], "Preschool", min_score=0.5) == []
    assert pick_diverse(store, source, embedder, [], "Preschool") == []


def test_the_group_decides_where_photos_come_from(indexed, store, source, embedder):
    assert pick_diverse(store, source, embedder, ["playground"], "Preschool", min_score=0.5) == []
    assert ids(pick_diverse(store, source, embedder, ["playground"], PE, min_score=0.5)) == [5]
    assert ids(pick_diverse(store, source, embedder, ["playground"], None, min_score=0.5)) == [5]


def test_find_gives_photos_ready_for_the_page(library):
    found = library.find(["soccer", "table"], 2)
    assert len(found) == 2 and {p.group for p in found} == {"Preschool"}
    assert all(p.image.media_type == "image/jpeg" and p.score >= 0.5 for p in found)


def test_a_photo_seth_feed_has_lost_is_skipped(library, feed):
    del feed.media[1]
    del feed.media[4]  # both copies of the soccer picture
    assert [p.file_id for p in library.find(["soccer", "table"], 3)] == [2]


def test_find_says_why_when_it_cant_search(library, store, source):
    with pytest.raises(PhotoError, match="photo search is off"):
        PhotoLibrary(store, source, None, "Preschool").find(["x"], 3)
    with pytest.raises(PhotoError, match="SETH_FLYER_PHOTO_GROUP"):
        PhotoLibrary(store, source, library.embedder, "No Such Group").find(["x"], 3)


def test_get_returns_the_photos_asked_for_in_order(library):
    assert [p.file_id for p in library.get([3, 1])] == [3, 1]
    assert library.get([3])[0].score is None and library.get([3])[0].group == "Preschool"
    with pytest.raises(PhotoError, match="999"):
        library.get([1, 999])


class BriefLLM:
    def __init__(self, queries):
        self.queries, self.calls = queries, []

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        self.calls.append(prompt)
        return PhotoBrief(queries=self.queries) if schema is PhotoBrief else SPEC


def test_the_model_describes_the_photos_it_wants():
    llm = BriefLLM(["  children in a classroom ", "", "children eating lunch"])
    assert plan_photos(llm, SPEC) == ["children in a classroom", "children eating lunch"]
    assert "Picnic" in llm.calls[0]



@pytest.fixture
def pages(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(flyer_module, "render_png", lambda html, out, chrome=None: (seen.append(html), out.write_bytes(b"PNG")))
    return seen


def test_photos_go_on_the_flyer_and_are_reported(tmp_path, pages, library):
    flyer = make_flyer(BriefLLM(["soccer", "table"]), "x", tmp_path / "o.png", BRAND, TODAY, library=library, photo_count=2)
    assert len(flyer.photos) == 2 and flyer.photo_error is None
    assert 'class="page has-photos"' in pages[0] and pages[0].count('<div class="photos">') == 1
    assert pages[0].count('<img src="data:image/jpeg') == 2


def test_no_photos_when_nothing_in_the_library_matches_well(tmp_path, pages, library):
    flyer = make_flyer(BriefLLM(["chemistry lab"]), "x", tmp_path / "o.png", BRAND, TODAY, library=library)
    assert flyer.photos == [] and flyer.photo_error is None and flyer.path.exists()
    assert "has-photos" not in pages[0].split("<body>")[1]


def test_no_photos_when_the_model_says_they_wouldnt_suit(tmp_path, pages, library):
    flyer = make_flyer(BriefLLM([]), "x", tmp_path / "o.png", BRAND, TODAY, library=library)
    assert flyer.photos == [] and flyer.photo_error is None and "has-photos" not in pages[0].split("<body>")[1]


def test_no_photos_without_a_library_or_when_none_are_wanted(tmp_path, pages, library):
    assert make_flyer(BriefLLM(["soccer"]), "x", tmp_path / "a.png", BRAND, TODAY).photos == []
    assert make_flyer(BriefLLM(["soccer"]), "x", tmp_path / "b.png", BRAND, TODAY, library=library, photo_count=0).photos == []


def test_when_photos_cant_be_found_the_flyer_is_still_made(tmp_path, pages, indexed, store, source):
    off = PhotoLibrary(store, source, None, "Preschool")
    flyer = make_flyer(BriefLLM(["soccer"]), "x", tmp_path / "o.png", BRAND, TODAY, library=off)
    assert flyer.path.exists() and flyer.photos == [] and "photo search is off" in flyer.photo_error


def test_a_failing_model_doesnt_stop_the_flyer(tmp_path, pages, library):
    class Failing(BriefLLM):
        def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
            if schema is PhotoBrief:
                raise PermanentError("refusal")
            return SPEC

    flyer = make_flyer(Failing([]), "x", tmp_path / "o.png", BRAND, TODAY, library=library)
    assert flyer.path.exists() and flyer.photos == [] and "refusal" in flyer.photo_error


def test_photos_you_pick_are_used_and_a_wrong_id_is_an_error(tmp_path, pages, library):
    flyer = make_flyer(BriefLLM(["soccer"]), "x", tmp_path / "o.png", BRAND, TODAY, library=library, photo_ids=[3, 2])
    assert [p.file_id for p in flyer.photos] == [3, 2]
    with pytest.raises(PhotoError):
        make_flyer(BriefLLM([]), "x", tmp_path / "p.png", BRAND, TODAY, library=library, photo_ids=[999])


def test_a_phone_photo_is_turned_upright_and_made_smaller():
    PILImage = pytest.importorskip("PIL.Image")
    picture = PILImage.new("RGB", (3000, 2000), "navy")  # wide on disk...
    exif = picture.getexif()
    exif[0x0112] = 6  # ...but the phone says: turn it a quarter turn
    raw = io.BytesIO()
    picture.save(raw, "JPEG", exif=exif)
    out = photos_module.prepare(raw.getvalue())
    result = PILImage.open(io.BytesIO(out.data))
    assert out.media_type == "image/jpeg" and result.size[0] < result.size[1] and max(result.size) == 900
    with pytest.raises(PhotoError):
        photos_module.prepare(b"not a picture")


def test_flyer_photos_come_from_every_group_unless_limited(monkeypatch):
    monkeypatch.delenv("SETH_FLYER_PHOTO_GROUP", raising=False)
    assert Config.from_env().flyer_photo_group is None
    for value in ("all", "ALL", ""):
        monkeypatch.setenv("SETH_FLYER_PHOTO_GROUP", value)
        assert Config.from_env().flyer_photo_group is None
    monkeypatch.setenv("SETH_FLYER_PHOTO_GROUP", "Preschool")
    assert Config.from_env().flyer_photo_group == "Preschool"


# ---- what every flyer has, however it was made ----

DESIGNED = """<html><head><style>body { font-family: var(--font); }</style></head><body>
<div style="background: url({{BACKGROUND}}) center / cover"></div>
<img src="{{LOGO}}" alt=""><h1>{{TITLE}}</h1><img src="{{PHOTO_1}}" alt=""><img src="{{PHOTO_2}}" alt=""><img src="{{PHOTO_3}}" alt="">
<p>{{SLOGAN_1}}</p><p>{{SLOGAN_2}}</p><p>{{SLOGAN_3}}</p></body></html>"""


@pytest.mark.parametrize("how", ["event", "event-collage", "designed", "design-file"])
def test_every_flyer_has_the_brand_and_a_background_and_photos_when_they_suit(how, tmp_path, pages, library, monkeypatch):
    import seth_agent.flyer.design as design_module

    monkeypatch.setattr(design_module, "check_page", lambda page, chrome=None: [])

    class Designer(BriefLLM):
        def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
            from seth_agent.flyer.design import FlyerDesign

            return FlyerDesign(html=DESIGNED) if schema is FlyerDesign else super().parse(system, prompt, schema)

    kwargs = {"layout": how} if how.startswith("event") else {"layout": None}
    if how == "design-file":
        mine = tmp_path / "mine.html"
        mine.write_text(DESIGNED)
        kwargs["design_file"] = mine
    flyer = make_flyer(Designer(["soccer", "table", "eating"]), "x", tmp_path / "o.png", BRAND, TODAY, library=library, photo_count=3, **kwargs)
    html = pages[0]
    assert len(flyer.photos) == 3 and html.count("data:image/jpeg;base64,") == 3  # photos, because they suit
    assert "data:image/png;base64,iVBOR" in html  # the real logo file
    assert all(part in html.replace("&amp;", "&") for part in ("Christ-centered education", "Unity among family, school, & church", "Building Christian character"))
    assert "#002a46" in html  # the school's colors
    assert "data:image/svg+xml;base64," in html  # a background scene, with no image provider and no picture of your own
