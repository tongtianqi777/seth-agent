"""The school's background music for slideshow videos: a folder of tracks, and a few moods to make them for.

Music is made once and reused. `seth-agent music generate` has MusicGen (see musicgen.py) make one instrumental loop
for each mood in the brand kit (cheerful, celebratory, calm) and saves it in the music folder. A slideshow then plays
a track the person names, or, with "auto", the one for the mood the model reads from the event. Tracks the school
adds to the folder by hand (music it has the right to use) work the same way.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
NONE, AUTO = "none", "auto"

log = logging.getLogger(__name__)


class MusicError(Exception):
    """A track that isn't in the music folder, or music that couldn't be made."""


class Generator(Protocol):
    def generate(self, prompt: str, seconds: float = ...) -> bytes:
        """WAV bytes of a loop of music for `prompt`."""
        ...


@dataclass
class MusicPick:
    path: Path | None  # the track to play, or None for no music
    name: str | None = None
    note: str | None = None  # why there is no music when the person asked for some


class MusicLibrary:
    def __init__(self, directory: Path, brand_dir: Path):
        self.directory = Path(directory)
        brand = json.loads((Path(brand_dir) / "brand.json").read_text())["music"]
        self._moods: dict[str, dict] = brand["moods"]
        self.default_mood: str = brand["default_mood"]
        self.suffix: str = brand.get("prompt_suffix", "")

    def moods(self) -> dict[str, str]:
        """Each mood and what it is for."""
        return {name: m["use"] for name, m in self._moods.items()}

    def tracks(self) -> list[str]:
        """The names of the tracks in the folder (the file names without the extension), sorted."""
        if not self.directory.is_dir():
            return []
        return sorted(f.stem for f in self.directory.iterdir() if f.suffix.lower() in AUDIO_SUFFIXES and f.is_file())

    def path(self, name: str) -> Path | None:
        """The track called `name` (ignoring case and the file's extension), if the folder has one."""
        wanted = Path(name).stem.casefold() if Path(name).suffix.lower() in AUDIO_SUFFIXES else name.casefold()
        if not self.directory.is_dir():
            return None
        for f in sorted(self.directory.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES and f.stem.casefold() == wanted:
                return f
        return None

    def pick(self, choice: str | None, mood: str | None = None, allow_paths: bool = False) -> MusicPick:
        """The track for what the person chose: nothing, `auto` (the one for `mood`), a track's name or, if
        `allow_paths`, a file. Raises MusicError for a track that isn't there. With `auto` and no track made yet
        there is no music, and the note says why."""
        if not choice or choice.strip().casefold() == NONE:
            return MusicPick(None)
        choice = choice.strip()
        if choice.casefold() == AUTO:
            use = mood if mood in self._moods else self.default_mood
            found = self.path(use)
            if found:
                return MusicPick(found, use)
            return MusicPick(None, None, f"No music: this event suits {use!r} music, which hasn't been made yet (run `seth-agent music generate`).")
        if allow_paths and Path(choice).expanduser().is_file():
            file = Path(choice).expanduser()
            return MusicPick(file, file.stem)
        found = self.path(choice)
        if found is None:
            have = ", ".join(self.tracks()) or "none yet"
            raise MusicError(f"no track {choice!r} in {self.directory} (tracks: {have}; `seth-agent music generate` makes some)")
        return MusicPick(found, found.stem)

    def prompt(self, mood: str) -> str:
        return ", ".join(x for x in (self._moods[mood]["prompt"], self.suffix) if x)

    def missing(self) -> list[str]:
        have = {t.casefold() for t in self.tracks()}
        return [m for m in self._moods if m not in have]

    def generate(self, generator: Generator, mood: str, force: bool = False) -> Path:
        """Make the track for `mood` and save it in the folder as `<mood>.wav`. An existing one is kept unless
        `force`, which replaces it with a new take."""
        if mood not in self._moods:
            raise MusicError(f"no mood {mood!r}; the moods are {', '.join(self._moods)}")
        existing = self.path(mood)
        if existing and not force:
            return existing
        wav = generator.generate(self.prompt(mood))
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{mood}.wav"
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(wav)
        partial.replace(target)
        if existing and existing != target:
            existing.unlink()
        return target


@dataclass
class MusicChoice:
    """What the person asked for (nothing, `auto`, a track's name), with the library to find it in.

    With a `generator`, `auto` makes the music for the event's mood on the spot when there is none yet (and keeps it
    for next time), so music works without being made beforehand. Music is never worth losing a video over: if it
    can't be made, the video has no music and the note says why."""

    library: MusicLibrary
    choice: str | None
    allow_paths: bool = False  # the command line may name a file; the dashboard may only name a track
    generator: Generator | None = None

    def moods(self) -> dict[str, str] | None:
        """The moods to ask the model to pick from, when the person said `auto`."""
        return self.library.moods() if (self.choice or "").strip().casefold() == AUTO else None

    def pick(self, mood: str | None = None) -> MusicPick:
        pick = self.library.pick(self.choice, mood, self.allow_paths)
        if pick.path is None and pick.note and self.generator:  # auto, and this mood's track isn't made yet
            use = mood if mood in self.library.moods() else self.library.default_mood
            try:
                made = self.library.generate(self.generator, use)
            except Exception as e:  # the optional install is missing, the model can't load, the disk is full...
                log.warning("couldn't make %s music: %s", use, e)
                return MusicPick(None, None, f"No music: couldn't make {use!r} music ({e}).")
            return MusicPick(made, use)
        return pick
