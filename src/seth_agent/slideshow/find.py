"""Choosing a slideshow's photos from a sentence about the event.

The model reads the sentence the way it does for a flyer (the event's name, its date, whether it is at the school)
and describes the photos an album of that event would hold. Code does the rest: it searches the school's photo
library, keeps photos sent around the event's date when the sentence has one, and puts them in the order they were
sent, so the video follows the day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from ..flyer.photos import Photo, PhotoError, PhotoLibrary
from ..flyer.spec import FlyerSpec, extract_spec
from ..llm import LLM

# Photos of an event are sent that day and in the next few days; a day's worth before covers setting up (and a
# timezone's difference in what "that day" is).
DAYS_BEFORE, DAYS_AFTER = 1, 3
MAX_QUERIES = 5


class PhotoBrief(BaseModel):
    queries: list[str] = Field(
        max_length=MAX_QUERIES,
        description="Three to five short descriptions of photos an album of this event would hold, each a different kind of photo",
    )
    mood: str | None = Field(None, description="The mood of music that suits the event, one of the moods listed, only when asked for")


BRIEF_SYSTEM = """\
You choose photos for a slideshow video of an event at Seth Christian Education Institute, a school. You get what the event is. Describe, in a few plain words each, the photos from the school's photo library that an album of this event would hold, the way someone would describe a photo they are looking for: "children singing on a stage", "a teacher with students", "families gathered in a room", "children sitting at tables eating", "a class performing".

- Three to five descriptions, each a different kind of photo, so the video has variety.
- Describe what is in the photo, not the event's name ("children singing on a stage", not "Christmas program").
- Only what would plausibly be photographed at such an event. Don't invent details the request doesn't suggest.
"""


MOOD_SYSTEM = """
Also choose the mood of background music for the video, as `mood`: exactly one of these, whichever suits the event best.
{moods}
"""


@dataclass
class Found:
    spec: FlyerSpec
    queries: list[str]
    photos: list[Photo]  # in the order they were sent
    window: tuple[str, str] | None  # the days they were searched in, when the sentence had a date
    mood: str | None = None  # the music mood that suits it, when the person asked for music to match


def plan_queries(llm: LLM, spec: FlyerSpec, request: str, moods: dict[str, str] | None = None) -> tuple[list[str], str | None]:
    """What to look for in the photos, and (when `moods` is given) which of them suits the event's music."""
    system = BRIEF_SYSTEM + (MOOD_SYSTEM.format(moods="\n".join(f"- {m}: {use}" for m, use in moods.items())) if moods else "")
    brief = llm.parse(system, f"Event: {spec.title}\nThe request, in the person's words: {request.strip()}", PhotoBrief)
    mood = brief.mood.strip().casefold() if brief.mood else None
    return [q.strip()[:100] for q in brief.queries if q.strip()][:MAX_QUERIES], mood if moods and mood in moods else None


def event_window(event_date: date) -> tuple[str, str]:
    return (event_date - timedelta(days=DAYS_BEFORE)).isoformat(), (event_date + timedelta(days=DAYS_AFTER)).isoformat()


def find_photos(
    llm: LLM, library: PhotoLibrary, request: str, today: date, count: int = 12, moods: dict[str, str] | None = None
) -> Found:
    """Read `request` and find up to `count` photos for it. Raises PhotoError when there are none to show."""
    spec = extract_spec(llm, request, today)
    queries, mood = plan_queries(llm, spec, request, moods)
    if not queries:
        raise PhotoError("the model didn't say what to look for; try describing the event more")
    window = event_window(spec.event_date) if spec.event_date else None
    photos = library.find(queries, count, *(window or (None, None)))
    if not photos:
        where = f" sent between {window[0]} and {window[1]}" if window else ""
        hint = " (leave the date out of the request to look through every photo, or choose photos with --photo)" if window else ""
        raise PhotoError(f"no photos{where} match {spec.title!r} well enough{hint}")
    return Found(spec, queries, sorted(photos, key=lambda p: p.sent_at), window, mood)
