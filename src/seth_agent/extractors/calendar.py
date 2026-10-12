"""Finds future events in messages and keeps the calendar memory up to date."""

from __future__ import annotations

import logging
import sqlite3
import unicodedata
from datetime import timedelta, tzinfo
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from ..memory.store import MemoryStore
from ..source import Message, MessageSource
from .base import Extractor, PermanentError, TransientError

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You maintain a personal calendar for the user. You read WhatsApp messages that one of the user's contacts sent them, and record events that will happen in the future, so the user always has an accurate picture of what is coming up.

Record an event when a message states or implies something that will happen at an identifiable future day or timeframe: appointments, trips, flights, school closures, deadlines, parties, rescheduled sessions, visits, and so on. "Future" is relative to when the message was sent, not to today.

Do not record anything for:
- greetings, chit-chat, reactions ("Hello", "lol", "ok")
- what is happening right now ("I'm outside waiting for you", "on my way")
- open questions that don't establish a plan ("Will you be able to make it?"). A question that confirms a plan already in place can update that event.
- events that were already over when the message was sent
- vague intentions with no timeframe at all ("we should get dinner sometime")

Resolving dates (use each message's own local sent date):
- A bare weekday ("on Monday") means the next such day strictly after the sent date, unless the message says "today". "This Friday" sent on a Wednesday is two days later.
- "Tomorrow", "next week", "in two weeks", "end of the month" are relative to the sent date.
- A month without a year ("in Nov") is the next occurrence; the current month counts.
- Numeric dates are US-style month/day ("10/18" is October 18).
- If you only know a rough timeframe, use the matching precision: "week" (date_start = Monday of that week), "month" (any day in the month, e.g. the 1st), "year", or "range" with explicit date_start and date_end.

Keeping the calendar consistent:
- You are shown the current calendar. If a message reschedules, adds detail to, or confirms an existing event, emit an "update" with that event_id instead of a new "add". If it calls an event off, emit a "cancel".
- An edited message replaces the original text. Adjust or cancel the events that came from the original so the calendar reflects the edited text.
- Never add an event that duplicates one already on the calendar.

Writing events:
- title: short and specific, from the user's point of view, naming who it involves ("Dad's flight", "School closed", "Workout session (rescheduled)").
- details: one sentence with any useful specifics from the messages (place, flight number, who said it). Optional.
- time: the local time of day if known ("15:30", "morning"), else null.
- source_message_ids: the ids of the new messages that support this operation.
- confidence: 0.0-1.0, how sure you are that this is a real future event with the right date.

Earlier messages are shown only as context and have already been processed; never emit operations based on them alone. Most messages contain no event. Returning an empty list of operations is normal.

Examples (assume each was sent Monday 2026-10-12):
- "The school is off on Monday" -> add "School closed", 2026-10-19, precision day
- "My workout session has been rescheduled to Nov" -> add "Workout session (rescheduled)", 2026-11-01, precision month (or update the existing workout event if there is one)
- "Dad's flight is on 10/18" -> add "Dad's flight", 2026-10-18, precision day
- "Hello" / "I'm outside waiting for you" / "Will you be able to make it?" -> no operations
"""


class EventOp(BaseModel):
    op: Literal["add", "update", "cancel"]
    event_id: int | None = Field(None, description="Existing event id. Required for update and cancel; null for add.")
    title: str | None = None
    details: str | None = None
    date_start: str | None = Field(None, description="YYYY-MM-DD")
    date_end: str | None = Field(None, description="YYYY-MM-DD, inclusive. Only needed for precision 'range'.")
    precision: Literal["day", "range", "week", "month", "year"] | None = None
    time: str | None = None
    source_message_ids: list[str]
    confidence: float | None = None
    reason: str | None = Field(None, description="Why this operation follows from the messages.")


class CalendarOps(BaseModel):
    operations: list[EventOp]


TRANSIENT_STATUS = {401, 403, 404, 408, 409, 429}


class CalendarExtractor(Extractor):
    name = "calendar"

    def __init__(
        self,
        store: MemoryStore,
        source: MessageSource,
        tz: tzinfo,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        batch_size: int = 20,
        client: anthropic.Anthropic | None = None,
    ):
        self.store = store
        self.source = source
        self.tz = tz
        self.model = model
        self.effort = effort
        self.batch_size = batch_size
        self.client = client or anthropic.Anthropic()

    # -- filtering ------------------------------------------------------------

    def wants(self, msg: Message) -> bool:
        if msg.message_type == "other" or not msg.body:
            return False
        # Skip bodies that are only emoji, punctuation and whitespace.
        return any(unicodedata.category(ch)[0] in "LN" for ch in msg.body)

    # -- extraction -------------------------------------------------------------

    def extract(self, batch: list[Message]) -> CalendarOps:
        prompt = self.build_prompt(batch)
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_format=CalendarOps,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.APIConnectionError, anthropic.CredentialsError) as e:
            raise TransientError(str(e)) from e
        except TypeError as e:
            # The SDK raises TypeError when no credentials are configured at all.
            if "authentication" not in str(e):
                raise
            raise TransientError("no Anthropic credentials: set ANTHROPIC_API_KEY") from e
        except anthropic.APIStatusError as e:
            if e.status_code in TRANSIENT_STATUS or e.status_code >= 500:
                raise TransientError(f"{e.status_code}: {e.message}") from e
            raise PermanentError(f"{e.status_code}: {e.message}") from e

        if response.stop_reason == "refusal":
            raise PermanentError(f"refusal: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise PermanentError("hit max_tokens before finishing")
        if response.parsed_output is None:
            raise PermanentError("no parsed output")
        return response.parsed_output

    def build_prompt(self, batch: list[Message]) -> str:
        first = batch[0]
        context = self.source.fetch_context(first.chat_jid, first.sent_at)
        earliest = min(m.sent_at.astimezone(self.tz).date() for m in batch)
        events = {e["id"]: e for e in self.store.list_events(date_from=earliest - timedelta(days=7))}
        # Events tied to messages being edited or replied to, however old.
        related = [(m.chat_jid, t) for m in batch for t in (m.edit_target_id, m.quoted_id) if t]
        for e in self.store.events_for_messages(related):
            if e["status"] == "active":
                events.setdefault(e["id"], e)

        parts = [f"The messages are from {first.sender or 'a contact'} to the user."]
        parts.append("<calendar>")
        if events:
            for e in sorted(events.values(), key=lambda e: (e["date_start"], e["id"])):
                parts.append(_format_event(e))
        else:
            parts.append("(empty)")
        parts.append("</calendar>")
        if context:
            parts.append("<earlier_messages>")
            parts.extend(self._format_message(m) for m in context)
            parts.append("</earlier_messages>")
        parts.append("<new_messages>")
        parts.extend(self._format_message(m) for m in batch)
        parts.append("</new_messages>")
        return "\n".join(parts)

    def _format_message(self, m: Message) -> str:
        local = m.sent_at.astimezone(self.tz)
        stamp = local.strftime("%a %Y-%m-%d %H:%M %Z")
        line = f"[{stamp}] id={m.message_id} type={m.message_type}: {m.body or f'[{m.message_type} without caption]'}"
        if m.message_type == "edit":
            line += f"\n    (edit of message {m.edit_target_id}; original text: {m.edit_target_body!r})"
        elif m.quoted_id:
            line += f"\n    (reply to message {m.quoted_id}: {m.quoted_body!r})"
        return line

    # -- applying -------------------------------------------------------------

    def apply(self, conn: sqlite3.Connection, batch: list[Message], result: CalendarOps) -> int:
        by_id = {m.message_id: m.key for m in batch}
        applied = 0
        for op in result.operations:
            sources = [by_id[i] for i in op.source_message_ids if i in by_id] or [m.key for m in batch]
            fields = op.model_dump(include={"title", "details", "date_start", "date_end", "precision", "time", "confidence"})
            try:
                if op.op == "add":
                    if not (op.title and op.date_start):
                        raise ValueError("add needs title and date_start")
                    event_id = self.store.add_event(conn, fields, sources, op.reason)
                    log.info("added event %s: %s %s", event_id, op.date_start, op.title)
                elif op.event_id is None:
                    raise ValueError(f"{op.op} needs event_id")
                elif op.op == "update":
                    self.store.update_event(conn, op.event_id, fields, sources, op.reason)
                    log.info("updated event %s: %s", op.event_id, op.reason)
                else:
                    self.store.cancel_event(conn, op.event_id, sources, op.reason)
                    log.info("cancelled event %s: %s", op.event_id, op.reason)
                applied += 1
            except (ValueError, KeyError) as e:
                # One bad operation shouldn't discard the rest of the batch.
                log.warning("skipping invalid operation %s: %s", op.model_dump(), e)
        return applied


def _format_event(e: dict) -> str:
    span = e["date_start"] if e["date_start"] == e["date_end"] else f"{e['date_start']}..{e['date_end']}"
    parts = [f"id={e['id']}", f"{span} ({e['precision']})", e["title"]]
    if e.get("time"):
        parts.append(f"time: {e['time']}")
    if e.get("details"):
        parts.append(e["details"])
    sources = ",".join(s["message_id"] for s in e.get("sources", []))
    if sources:
        parts.append(f"from messages: {sources}")
    return " | ".join(parts)

