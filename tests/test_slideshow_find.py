"""Finding a slideshow's photos from a sentence: what the model is asked, the date window, and the order."""

from datetime import date

import pytest

import seth_agent.flyer.photos as photos_module
from conftest import CHAT
from seth_agent.agent import Agent
from seth_agent.extractors.media import MediaExtractor
from seth_agent.flyer import FlyerSpec, PhotoError, PhotoLibrary
from seth_agent.llm import Image
from seth_agent.media_search import pick_diverse
from seth_agent.slideshow.find import PhotoBrief, event_window, find_photos
from test_media import FakeEmbedder

TODAY = date(2026, 10, 17)


@pytest.fixture
def embedder():
    return FakeEmbedder()


def add_photo(feed, message_id, file_id, data, sent_at):
    feed.add(message_id, None, message_type="image", sent_at=sent_at)
    feed.add_file(message_id, file_id, data)


@pytest.fixture
def library(feed, store, source, embedder, monkeypatch):
    """Soccer photos on three different days (the event is on the 12th), a lunch photo on the 12th."""
    add_photo(feed, "A", 1, b"soccer soccer soccer", "2026-12-14T10:00:00Z")  # two days after, sent first
    add_photo(feed, "B", 2, b"soccer soccer playground", "2026-12-12T09:00:00Z")
    add_photo(feed, "C", 3, b"table table eating", "2026-12-12T12:00:00Z")
    add_photo(feed, "D", 4, b"soccer soccer table", "2025-12-12T09:00:00Z")  # a year earlier
    add_photo(feed, "E", 5, b"soccer soccer eating", "2026-12-20T09:00:00Z")  # a week later
    Agent(store, source, [MediaExtractor(store, source, embedder)], embedder).poll_once()
    monkeypatch.setattr(photos_module, "prepare", lambda data, max_side=0: Image("image/jpeg", data))
    return PhotoLibrary(store, source, embedder, group="Preschool", min_score=0.5)


class FindLLM:
    def __init__(self, spec, queries):
        self.spec, self.queries, self.calls = spec, queries, []

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        self.calls.append((schema, prompt))
        return PhotoBrief(queries=self.queries) if schema is PhotoBrief else self.spec


def ids(found):
    return [p.file_id for p in found.photos]


def test_the_window_runs_from_the_day_before_to_a_few_days_after():
    assert event_window(date(2026, 12, 12)) == ("2026-12-11", "2026-12-15")


def test_the_date_in_the_sentence_limits_which_photos_are_looked_through(feed, store, source, embedder, library):
    everything = pick_diverse(store, source, embedder, ["soccer"], "Preschool", 10, 0.5)
    assert {r["file_id"] for r in everything} >= {1, 2, 4, 5}
    near = pick_diverse(store, source, embedder, ["soccer"], "Preschool", 10, 0.5, sent_from="2026-12-11", sent_until="2026-12-15")
    assert {r["file_id"] for r in near} == {1, 2}  # not last year's, not the week after


def test_photos_come_in_the_order_they_were_sent(library):
    spec = FlyerSpec(title="Christmas Program", event_date=date(2026, 12, 12), at_school=True)
    found = find_photos(FindLLM(spec, ["soccer", "table"]), library, "Christmas Program on 12/12", TODAY, 5)
    assert ids(found) == [2, 3, 1]  # the 12th (morning, noon), then the 14th, though photo 1 matched first
    assert found.window == ("2026-12-11", "2026-12-15") and found.spec.at_school


def test_with_no_date_every_photo_is_looked_through(library):
    spec = FlyerSpec(title="Soccer")
    found = find_photos(FindLLM(spec, ["soccer"]), library, "Soccer", TODAY, 10)
    assert found.window is None and set(ids(found)) == {1, 2, 4, 5}


def test_the_model_is_given_the_persons_words(library):
    llm = FindLLM(FlyerSpec(title="Picnic"), ["soccer"])
    find_photos(llm, library, "Spring picnic at the park", TODAY)
    brief = [p for schema, p in llm.calls if schema is PhotoBrief][0]
    assert "Picnic" in brief and "Spring picnic at the park" in brief


def test_nothing_in_the_window_is_an_error_that_says_what_to_try(library):
    spec = FlyerSpec(title="Chemistry Fair", event_date=date(2026, 12, 12))
    with pytest.raises(PhotoError, match="2026-12-11 and 2026-12-15.*leave the date out"):
        find_photos(FindLLM(spec, ["chemistry lab"]), library, "Chemistry Fair 12/12", TODAY)


def test_a_model_that_describes_nothing_is_an_error(library):
    with pytest.raises(PhotoError, match="what to look for"):
        find_photos(FindLLM(FlyerSpec(title="Picnic"), ["", "  "]), library, "Picnic", TODAY)


@pytest.fixture
def run(monkeypatch, library, tmp_path):
    """Runs `seth-agent slideshow ...` with the model faked, and returns what make_slideshow was asked to make."""
    import seth_agent.cli as cli
    import seth_agent.llm as llm_module
    import seth_agent.slideshow as slideshow_module
    from seth_agent.slideshow import Slideshow

    made = {}
    spec = FlyerSpec(title="Christmas Program", event_date=date(2026, 12, 12), at_school=True)
    monkeypatch.setattr(llm_module, "build_llm", lambda config: FindLLM(spec, ["soccer", "table"]))
    monkeypatch.setattr(cli, "_photo_library", lambda config, group, searching, max_side=None: library)

    def fake_make(title, photos, out, brand_dir, subtitle, end_text, address, *rest):
        made.update(title=title, ids=[p.file_id for p in photos], subtitle=subtitle, address=address, out=out)
        return Slideshow(out, photos, 20.0)

    monkeypatch.setattr(slideshow_module, "make_slideshow", fake_make)
    monkeypatch.setenv("SETH_SLIDESHOW_DIR", str(tmp_path))

    def go(*args):
        cli.main(["slideshow", *args])
        return made

    return go


def test_find_uses_the_sentence_for_the_title_date_and_address(run, capsys):
    made = run("Christmas Program on 12/12 at school", "--find")
    assert made["title"] == "Christmas Program" and made["ids"] == [2, 3, 1]
    assert made["subtitle"] == "Saturday, December 12, 2026" and made["address"] is True
    assert made["out"].name == "2026-10-17-christmas-program.mp4"
    out = capsys.readouterr().out
    assert "Read from your request: Christmas Program | Saturday, December 12, 2026 | school address" in out
    assert "Looked for: soccer / table" in out and "Photos sent 2026-12-11 to 2026-12-15" in out


def test_what_the_person_says_beats_the_models_reading(run):
    made = run("Christmas Program on 12/12", "--find", "--subtitle", "Our best yet", "--no-address")
    assert made["subtitle"] == "Our best yet" and made["address"] is False


def test_chosen_photos_use_the_title_as_typed_and_no_model(run):
    made = run("My Title", "--photo", "3", "--photo", "2")
    assert made["title"] == "My Title" and made["ids"] == [3, 2] and made["subtitle"] is None and made["address"] is False
