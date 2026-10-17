"""Background music: the folder of tracks, the moods to make them for, and the loop MusicGen's output becomes."""

import io
import wave
from pathlib import Path

import pytest

from seth_agent.music import MusicChoice, MusicError, MusicLibrary

BRAND = Path(__file__).resolve().parent.parent / "brand"


class FakeGenerator:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, seconds=30):
        self.prompts.append(prompt)
        return f"take {len(self.prompts)}".encode()


@pytest.fixture
def library(tmp_path):
    return MusicLibrary(tmp_path / "music", BRAND)


def put(library, name, data=b"x"):
    library.directory.mkdir(parents=True, exist_ok=True)
    (library.directory / name).write_bytes(data)


def test_the_brand_kit_defines_the_moods():
    moods = MusicLibrary(Path("nowhere"), BRAND).moods()
    assert set(moods) == {"cheerful", "celebratory", "calm"} and all(moods.values())


def test_tracks_are_the_audio_files_in_the_folder(library):
    assert library.tracks() == []  # no folder yet
    for name in ("calm.wav", "Spring Song.MP3", "notes.txt", "cover.jpg"):
        put(library, name)
    assert library.tracks() == ["Spring Song", "calm"]
    assert library.path("spring song").name == "Spring Song.MP3" and library.path("calm.wav").name == "calm.wav"
    assert library.path("nope") is None


def test_picking_none_a_track_or_a_file(library, tmp_path):
    put(library, "calm.wav")
    assert library.pick(None).path is None and library.pick("none").path is None
    assert library.pick("Calm").path == library.directory / "calm.wav"
    with pytest.raises(MusicError, match="no track 'nope'.*calm"):
        library.pick("nope")
    elsewhere = tmp_path / "mine.mp3"
    elsewhere.write_bytes(b"x")
    assert library.pick(str(elsewhere), allow_paths=True).path == elsewhere
    with pytest.raises(MusicError):  # the dashboard may only name a track, never a file
        library.pick(str(elsewhere))


def test_auto_plays_the_track_for_the_mood(library):
    put(library, "celebratory.wav")
    put(library, "cheerful.wav")
    assert library.pick("auto", "celebratory").name == "celebratory"
    assert library.pick("auto", "nonsense").name == "cheerful"  # an unknown mood falls back to the default
    assert library.pick("auto", None).name == "cheerful"


def test_auto_without_the_track_plays_nothing_and_says_why(library):
    pick = library.pick("auto", "calm")
    assert pick.path is None and "'calm'" in pick.note and "seth-agent music generate" in pick.note


def test_generating_makes_a_track_per_mood_and_keeps_it(library):
    gen = FakeGenerator()
    path = library.generate(gen, "calm")
    assert path == library.directory / "calm.wav" and path.read_bytes() == b"take 1"
    assert "instrumental only, no vocals" in gen.prompts[0] and "peaceful" in gen.prompts[0]
    assert library.generate(gen, "calm") == path and len(gen.prompts) == 1  # reused, not made again
    library.generate(gen, "calm", force=True)
    assert path.read_bytes() == b"take 2"  # a new take replaces it
    assert not list(library.directory.glob("*.partial"))
    assert library.missing() == ["cheerful", "celebratory"]


def test_a_hand_added_track_of_the_same_name_counts_as_the_mood(library):
    put(library, "Calm.mp3", b"mine")
    assert library.missing() == ["cheerful", "celebratory"]
    library.generate(FakeGenerator(), "calm")  # kept
    assert (library.directory / "Calm.mp3").read_bytes() == b"mine"


def test_an_unknown_mood_is_refused(library):
    with pytest.raises(MusicError, match="no mood 'jazz'.*cheerful"):
        library.generate(FakeGenerator(), "jazz")


def test_moods_are_only_asked_for_when_the_person_said_auto(library):
    assert MusicChoice(library, "auto").moods() == library.moods()
    assert MusicChoice(library, " Auto ").moods() == library.moods()
    assert MusicChoice(library, "calm").moods() is None and MusicChoice(library, None).moods() is None


np = pytest.importorskip("numpy")


def test_a_loop_has_no_seam():
    from seth_agent.music.musicgen import make_loopable

    rate = 1000
    t = np.arange(10 * rate) / rate
    samples = np.sin(2 * np.pi * 3.125 * t).astype("float32")  # 31.25 cycles: repeating it as it is would jump from 1 to 0
    assert abs(samples[0] - samples[-1]) > 0.9
    loop = make_loopable(samples, rate, fade_seconds=1.0)
    assert len(loop) == 9 * rate  # the fade comes off the end
    assert abs(loop[0] - loop[-1]) < 0.1  # so the end runs straight into the start
    assert abs(np.diff(loop)).max() < 0.1  # and there is no step anywhere in it


def test_music_too_short_to_loop_is_refused():
    from seth_agent.music.musicgen import MusicGenError, make_loopable

    with pytest.raises(MusicGenError):
        make_loopable(np.zeros(100, dtype="float32"), 1000, 1.0)


def test_wav_bytes_are_a_valid_clipped_16_bit_file():
    from seth_agent.music.musicgen import to_wav

    data = to_wav(np.array([0.0, 0.5, 2.0, -2.0], dtype="float32"), 32000)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 32000, 4)
        frames = np.frombuffer(w.readframes(4), dtype="<i2")
    assert list(frames) == [0, 16383, 32767, -32767]
