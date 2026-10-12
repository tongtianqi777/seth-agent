"""Agent loop and calendar extractor, with Claude replaced by scripted responses."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from conftest import TZ
from seth_agent.agent import HIGH_WATER_KEY, Agent
from seth_agent.extractors.base import TransientError
from seth_agent.extractors.calendar import CalendarExtractor, CalendarOps, EventOp
from seth_agent.source import Message


class ScriptedCalendar(CalendarExtractor):
    """CalendarExtractor whose Claude call is replaced by a function of the batch."""

    def __init__(self, store, source, respond: Callable[[list[Message]], CalendarOps]):
        super().__init__(store, source, TZ, client=object())  # type: ignore[arg-type]
        self.respond = respond
        self.batches: list[list[str]] = []
        self.prompts: list[str] = []

    def extract(self, batch):
        self.batches.append([m.message_id for m in batch])
        self.prompts.append(self.build_prompt(batch))
        return self.respond(batch)


def no_ops(batch):
    return CalendarOps(operations=[])


def make_agent(store, source, respond=no_ops):
    ext = ScriptedCalendar(store, source, respond)
    return Agent(store, source, [ext]), ext


def test_filters_media_only_and_emoji(source_db, source, store):
    source_db.add("IMG", None, message_type="image", media_file_count=1, sent_at="2026-10-12T10:00:00Z")
    source_db.add("EMO", "👍🎉", sent_at="2026-10-12T10:01:00Z")
    source_db.add("OTH", None, message_type="other", sent_at="2026-10-12T10:02:00Z")
    source_db.add("TXT", "Dad's flight is on 10/18", sent_at="2026-10-12T10:03:00Z")
    source_db.add("CAP", "Party next Sat!", message_type="image", media_file_count=1, sent_at="2026-10-12T10:04:00Z")
    agent, ext = make_agent(store, source)

    r = agent.poll_once()
    assert ext.batches == [["TXT", "CAP"]]
    assert (r.seen, r.skipped, r.extracted) == (5, 3, 2)


def test_adds_events_and_is_idempotent(source_db, source, store):
    source_db.add("TXT", "Dad's flight is on 10/18")

    def respond(batch):
        return CalendarOps(operations=[EventOp(
            op="add", title="Dad's flight", date_start="2026-10-18", precision="day", source_message_ids=["TXT"],
        )])

    agent, ext = make_agent(store, source, respond)
    agent.poll_once()
    agent.poll_once()
    assert len(ext.batches) == 1
    [event] = store.list_events()
    assert event["title"] == "Dad's flight"
    assert event["sources"][0]["message_id"] == "TXT"
    assert store.get_state(HIGH_WATER_KEY) == "2026-10-12T16:00:00Z"


def test_album_caption_arriving_later_is_reprocessed(source_db, source, store):
    source_db.add("ALB", None, message_type="album", media_file_count=2)
    agent, ext = make_agent(store, source)
    agent.poll_once()
    assert ext.batches == []

    source_db.set_body("ALB", "Recital is on Nov 3")
    agent.poll_once()
    assert ext.batches == [["ALB"]]


def test_update_uses_existing_calendar_in_prompt(source_db, source, store):
    source_db.add("A", "Dad's flight is on 10/18", sent_at="2026-10-12T10:00:00Z")

    def respond(batch):
        if batch[0].message_id == "A":
            return CalendarOps(operations=[EventOp(
                op="add", title="Dad's flight", date_start="2026-10-18", precision="day", source_message_ids=["A"])])
        return CalendarOps(operations=[EventOp(
            op="update", event_id=1, date_start="2026-10-20", source_message_ids=["E"], reason="edited")])

    agent, ext = make_agent(store, source, respond)
    agent.poll_once()
    source_db.add("E", "Dad's flight is on 10/20", sent_at="2026-10-12T10:05:00Z", message_type="edit", edit_target_id="A")
    agent.poll_once()

    assert "id=1 | 2026-10-18 (day) | Dad's flight" in ext.prompts[1]
    assert "original text: \"Dad's flight is on 10/18\"" in ext.prompts[1]
    assert "<earlier_messages>" in ext.prompts[1]
    [event] = store.list_events()
    assert event["date_start"] == "2026-10-20"


def test_invalid_operation_does_not_drop_batch(source_db, source, store):
    source_db.add("A", "stuff")

    def respond(batch):
        return CalendarOps(operations=[
            EventOp(op="cancel", event_id=42, source_message_ids=["A"]),
            EventOp(op="add", title="Real", date_start="2026-10-20", precision="day", source_message_ids=["A"]),
        ])

    agent, _ = make_agent(store, source, respond)
    r = agent.poll_once()
    assert r.changes == 1
    assert [e["title"] for e in store.list_events()] == ["Real"]


def test_transient_error_retries_and_holds_high_water(source_db, source, store):
    source_db.add("A", "Dad's flight is on 10/18")
    calls = {"n": 0}

    def respond(batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("rate limited")
        return CalendarOps(operations=[])

    agent, ext = make_agent(store, source, respond)
    r = agent.poll_once()
    assert r.transient_error
    assert store.get_state(HIGH_WATER_KEY) is None
    agent.poll_once()
    assert ext.batches == [["A"], ["A"]]


def test_permanent_error_is_recorded_and_skipped(source_db, source, store):
    from seth_agent.extractors.base import PermanentError

    source_db.add("A", "Dad's flight is on 10/18")

    def respond(batch):
        raise PermanentError("refusal")

    agent, ext = make_agent(store, source, respond)
    r = agent.poll_once()
    agent.poll_once()
    assert r.failed == 1 and len(ext.batches) == 1
    assert store.last_error()["error"] == "refusal"


@pytest.mark.parametrize("body,expected", [("ok", True), ("🙂", False), ("", False), ("10/18", True)])
def test_wants(store, source, body, expected):
    ext = ScriptedCalendar(store, source, no_ops)
    from datetime import datetime, timezone

    msg = Message("c", "m", "s", datetime.now(timezone.utc), "", "text", body)
    assert ext.wants(msg) is expected
