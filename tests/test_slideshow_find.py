"""Finding a slideshow's photos from a sentence: what the model is asked, the date window, and the order."""

from datetime import date
from pathlib import Path

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
BRAND = Path(__file__).resolve().parent.parent / "brand"


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
    def __init__(self, spec, queries, mood=None):
        self.spec, self.queries, self.mood, self.calls = spec, queries, mood, []

    def parse(self, system, prompt, schema, images=(), effort=None, max_tokens=None):
        self.calls.append((schema, prompt, system))
        return PhotoBrief(queries=self.queries, mood=self.mood) if schema is PhotoBrief else self.spec


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
    brief = [p for schema, p, _ in llm.calls if schema is PhotoBrief][0]
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
    import seth_agent.music.musicgen as musicgen_module
    import seth_agent.slideshow as slideshow_module
    from seth_agent.slideshow import Slideshow

    made = {}
    spec = FlyerSpec(title="Christmas Program", event_date=date(2026, 12, 12), at_school=True)
    monkeypatch.setattr(llm_module, "build_llm", lambda config: FindLLM(spec, ["soccer", "table"]))
    monkeypatch.setattr(cli, "_photo_library", lambda config, group, searching, max_side=None: library)

    def fake_make(title, photos, out, brand_dir, subtitle, end_text, address, *rest):
        made.update(title=title, ids=[p.file_id for p in photos], subtitle=subtitle, address=address, out=out, music=rest[1])
        return Slideshow(out, photos, 20.0)

    monkeypatch.setattr(slideshow_module, "make_slideshow", fake_make)
    monkeypatch.setenv("SETH_SLIDESHOW_DIR", str(tmp_path))
    monkeypatch.setenv("SETH_MUSIC_DIR", str(tmp_path / "music"))

    class FakeMusicGen:  # never the real model
        def __init__(self, model):
            pass

        def generate(self, prompt, seconds=30):
            return b"generated"

    monkeypatch.setattr(musicgen_module, "MusicGen", FakeMusicGen)

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


MOODS = {"cheerful": "Everyday school life.", "calm": "Quiet, reflective videos."}


def test_the_mood_is_only_asked_for_when_music_should_match(library):
    spec = FlyerSpec(title="Picnic")
    asked = FindLLM(spec, ["soccer"], mood="calm")
    found = find_photos(asked, library, "Picnic", TODAY, moods=MOODS)
    assert found.mood == "calm"
    system = [sys for schema, _, sys in asked.calls if schema is PhotoBrief][0]
    assert "- cheerful: Everyday school life." in system and "- calm: Quiet, reflective videos." in system

    quiet = FindLLM(spec, ["soccer"], mood="calm")  # a model that offers one unasked is not believed
    found = find_photos(quiet, library, "Picnic", TODAY)
    assert found.mood is None
    assert "mood" not in [sys for schema, _, sys in quiet.calls if schema is PhotoBrief][0]


def test_a_mood_the_model_made_up_is_dropped(library):
    spec = FlyerSpec(title="Picnic")
    assert find_photos(FindLLM(spec, ["soccer"], mood="Calm "), library, "Picnic", TODAY, moods=MOODS).mood == "calm"
    assert find_photos(FindLLM(spec, ["soccer"], mood="jazzy"), library, "Picnic", TODAY, moods=MOODS).mood is None


@pytest.fixture
def music(tmp_path):
    from seth_agent.music import MusicChoice, MusicLibrary

    tracks = MusicLibrary(tmp_path / "music", BRAND)
    tracks.directory.mkdir()
    for name in ("cheerful.wav", "calm.wav"):
        (tracks.directory / name).write_bytes(b"x")
    return lambda choice: MusicChoice(tracks, choice)


@pytest.fixture
def made(monkeypatch):
    """make_from_request with the video itself faked: records what it was asked to make."""
    import seth_agent.slideshow as slideshow_module

    seen = {}

    def fake(title, photos, out, brand_dir, subtitle, end_text, address, seconds, music, chrome, ffmpeg):
        seen["music"] = music
        return slideshow_module.Slideshow(out, list(photos), 10.0, music)

    monkeypatch.setattr(slideshow_module, "make_slideshow", fake)
    return seen


def make(llm, library, tmp_path, music):
    from seth_agent.slideshow import make_from_request

    return make_from_request(llm, library, "Picnic", BRAND, TODAY, tmp_path / "o.mp4", music=music)


def test_auto_music_follows_the_mood_the_model_reads(library, made, music, tmp_path):
    llm = FindLLM(FlyerSpec(title="Picnic"), ["soccer"], mood="calm")
    found, show = make(llm, library, tmp_path, music("auto"))
    assert found.mood == "calm" and show.music.name == "calm.wav" and made["music"] == show.music
    assert show.music_note is None


def test_a_named_track_plays_and_the_model_is_not_asked_for_a_mood(library, made, music, tmp_path):
    llm = FindLLM(FlyerSpec(title="Picnic"), ["soccer"], mood="calm")
    _, show = make(llm, library, tmp_path, music("cheerful"))
    assert show.music.name == "cheerful.wav"
    assert "mood" not in [sys for schema, _, sys in llm.calls if schema is PhotoBrief][0]


def test_auto_without_the_track_still_makes_the_video_and_says_why(library, made, tmp_path):
    from seth_agent.music import MusicChoice, MusicLibrary

    empty = MusicChoice(MusicLibrary(tmp_path / "none", BRAND), "auto")
    _, show = make(FindLLM(FlyerSpec(title="Picnic"), ["soccer"], mood="calm"), library, tmp_path, empty)
    assert show.music is None and "'calm'" in show.music_note


def test_a_wrong_track_name_fails_before_any_photos_are_looked_for(library, made, music, tmp_path):
    from seth_agent.music import MusicError

    llm = FindLLM(FlyerSpec(title="Picnic"), ["soccer"])
    with pytest.raises(MusicError, match="no track 'polka'"):
        make(llm, library, tmp_path, music("polka"))
    assert llm.calls == []  # the model was never asked


@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    folder = tmp_path / "music"
    folder.mkdir()
    (folder / "calm.wav").write_bytes(b"x")
    monkeypatch.setenv("SETH_MUSIC_DIR", str(folder))
    return folder


def test_a_chosen_track_plays_under_photos_you_picked(run, music_dir):
    assert run("My Title", "--photo", "3", "--music", "calm")["music"] == music_dir / "calm.wav"
    assert run("My Title", "--photo", "3")["music"] is None


def test_auto_music_needs_the_event_to_read_the_mood_from(run, music_dir):
    with pytest.raises(SystemExit, match="needs --find"):
        run("My Title", "--photo", "3", "--music", "auto")


def test_a_wrong_track_is_reported(run, music_dir):
    with pytest.raises(SystemExit, match="music: no track 'polka'"):
        run("My Title", "--photo", "3", "--music", "polka")


def test_the_music_command_makes_the_missing_moods_and_lists_them(music_dir, monkeypatch, capsys):
    import seth_agent.cli as cli
    import seth_agent.music.musicgen as musicgen

    class Fake:
        def __init__(self, model):
            self.model = model

        def generate(self, prompt, seconds=30):
            return b"made"

    monkeypatch.setattr(musicgen, "MusicGen", Fake)
    cli.main(["music", "generate"])
    assert sorted(f.name for f in music_dir.iterdir()) == ["calm.wav", "celebratory.wav", "cheerful.wav"]
    assert (music_dir / "calm.wav").read_bytes() == b"x"  # the one that was there is kept
    capsys.readouterr()
    cli.main(["music", "generate"])
    assert "Every mood already has a track" in capsys.readouterr().out
    cli.main(["music", "generate", "calm", "--force"])
    assert (music_dir / "calm.wav").read_bytes() == b"made"
    cli.main(["music", "list"])
    assert "Tracks in" in capsys.readouterr().out


def test_with_find_music_is_on_unless_turned_off(run, tmp_path):
    made = run("Christmas Program on 12/12 at school", "--find")
    assert made["music"] == tmp_path / "music" / "cheerful.wav"  # made on the spot (the fake model read no mood), then kept
    assert (tmp_path / "music" / "cheerful.wav").read_bytes() == b"generated"
    assert run("Christmas Program on 12/12 at school", "--find", "--no-music")["music"] is None
    assert run("Christmas Program on 12/12", "--find", "--music", "none")["music"] is None


def test_music_is_off_with_chosen_photos_unless_asked_for(run, tmp_path):
    assert run("My Title", "--photo", "3")["music"] is None
    with pytest.raises(SystemExit):  # one or the other
        run("My Title", "--photo", "3", "--music", "calm", "--no-music")


class Counting:
    def __init__(self, fail=None):
        self.prompts, self.fail = [], fail

    def generate(self, prompt, seconds=30):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return b"made"


def test_auto_makes_the_mood_music_once_and_reuses_it(tmp_path):
    from seth_agent.music import MusicChoice, MusicLibrary

    gen = Counting()
    library = MusicLibrary(tmp_path / "music", BRAND)
    first = MusicChoice(library, "auto", generator=gen).pick("calm")
    again = MusicChoice(library, "auto", generator=gen).pick("calm")
    assert first.path == again.path == library.directory / "calm.wav" and len(gen.prompts) == 1


def test_music_that_cant_be_made_never_loses_the_video(tmp_path):
    from seth_agent.music import MusicChoice, MusicLibrary

    pick = MusicChoice(MusicLibrary(tmp_path / "music", BRAND), "auto", generator=Counting(RuntimeError("no GPU"))).pick("calm")
    assert pick.path is None and "couldn't make 'calm' music (no GPU)" in pick.note


def test_only_auto_ever_makes_music(tmp_path):
    from seth_agent.music import MusicChoice, MusicError, MusicLibrary

    gen, library = Counting(), MusicLibrary(tmp_path / "music", BRAND)
    assert MusicChoice(library, "none", generator=gen).pick("calm").path is None
    with pytest.raises(MusicError):
        MusicChoice(library, "polka", generator=gen).pick("calm")
    assert gen.prompts == []
