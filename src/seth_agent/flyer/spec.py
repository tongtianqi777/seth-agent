"""Reading a flyer request into a FlyerSpec.

Dates and times stay structured (a date, a time) so the code that lays out the flyer writes them the same way every
time. The model never writes the date line itself.
"""

from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, Field, field_validator

from ..llm import LLM

# The longest each field may be. The layout in brand/templates is built to fit these.
MAX_TITLE, MAX_SUBTITLE, MAX_LOCATION, MAX_DETAILS = 60, 90, 90, 240


class FlyerSpec(BaseModel):
    title: str = Field(max_length=MAX_TITLE, description="The event's name, short, without the date, e.g. 'Open House'")
    subtitle: str | None = Field(None, max_length=MAX_SUBTITLE, description="A short line under the title, only if the request has one")
    event_date: date | None = Field(None, description="The day of the event, if the request gives one")
    start_time: time | None = Field(None, description="When it starts, 24-hour, if given")
    end_time: time | None = Field(None, description="When it ends, 24-hour, if given")
    location: str | None = Field(None, max_length=MAX_LOCATION, description="Where, only if the request says")
    at_school: bool = Field(False, description="True if people come to the school itself for this, so the school's address belongs on the flyer")
    details: str | None = Field(None, max_length=MAX_DETAILS, description="Other things worth printing (what to bring, who it is for), only from the request")

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v.strip()

    @field_validator("subtitle", "location", "details")
    @classmethod
    def _tidy(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v[:1].upper() + v[1:] if v else None


SYSTEM_PROMPT = """\
You prepare the wording of a flyer for Seth Christian Education Institute, a school. The user describes the event in a sentence or two. You read it into the fields of the flyer; a program lays the flyer out.

Rules:
- Use only what the request says. Never invent a date, time, place or detail. A field the request doesn't cover is null.
- title: the event's name, short and in title case, without the date or time ("Open House", "Spring Concert", "Picture Day"). Don't put the school's name in it; the logo already carries it.
- subtitle: only if the request has a tagline or short extra line. Otherwise null.
- Dates are US style: 11/7/2026 is November 7, 2026. A date without a year is the next time it comes round, on or after today. A weekday on its own ("this Friday", "Saturday") is the next one on or after today, and "next Friday" is the one after that. Today's date is given with each request.
- A holiday's name is not a date. If the request names a holiday and no date, leave event_date null unless you are certain of the holiday's date that year; a weekday given with it must match.
- Times: "10am - 12pm" is 10:00 and 12:00. A single time is the start time. "Noon" is 12:00. "10-12" for an open house or a school event means 10:00 to 12:00. Use 24-hour times.
- location: where it takes place, only if given.
- at_school: true when people are meant to come to the school for this. That is when the request says it is at the school, on campus or in a part of it ("in the gym", "in room 5"), or when it is the kind of event a school holds on its own grounds (open house, picture day, parent-teacher night, graduation, a school program or class) and the request names no other place. False when the request names another place (a church, a park, a museum), when it is online, or when nobody has to come anywhere (a closure, a deadline, a reminder). The school's address is added by the program; never write it in location.
- details: other things a reader should know (what to bring, who is invited, how to sign up), kept short and written the way the request has them. Don't repeat the date, time or place here.
- Keep the request's wording and spelling for names. Fix only obvious typos in capitalization.
"""


def extract_spec(llm: LLM, prompt: str, today: date) -> FlyerSpec:
    """Read a flyer request. Raises TransientError or PermanentError, like any extractor."""
    return llm.parse(
        SYSTEM_PROMPT,
        f"Today is {today:%A}, {today.isoformat()}.\n\nFlyer request:\n{prompt.strip()}",
        FlyerSpec,
    )
