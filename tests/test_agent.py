"""Agent loop and calendar extractor, with Claude replaced by scripted responses."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from conftest import TZ
from seth_agent.agent import CURSOR_KEY, Agent
from seth_agent.extractors.base import TransientError
from seth_agent.extractors.calendar import CalendarExtractor, CalendarOps, EventOp
from seth_agent.source import Message


class ScriptedCalendar(CalendarExtractor):
    """CalendarExtractor whose Claude call is replaced by a function of the batch."""

    def __init__(self, store, source, respond: Callable[[list[Message]], CalendarOps]):
        super().__init__(store, source, TZ, llm=object())  # type: ignore[arg-type]
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


def test_filters_media_only_and_emoji(feed, source, store):
    feed.add("IMG", None, message_type="image", media_file_count=1, sent_at="2026-10-12T10:00:00Z")
    feed.add("EMO", "👍🎉", sent_at="2026-10-12T10:01:00Z")
    feed.add("OTH", None, message_type="other", sent_at="2026-10-12T10:02:00Z")
    feed.add("TXT", "Dad's flight is on 10/18", sent_at="2026-10-12T10:03:00Z")
    feed.add("OTX", "Picnic is Saturday", message_type="other", sent_at="2026-10-12T10:05:00Z")
    feed.add("CAP", "Party next Sat!", message_type="image", media_file_count=1, sent_at="2026-10-12T10:04:00Z")
    agent, ext = make_agent(store, source)

    r = agent.poll_once()
    assert ext.batches == [["TXT", "CAP", "OTX"]]
    assert (r.seen, r.skipped, r.extracted) == (6, 3, 3)


def test_adds_events_and_is_idempotent(feed, source, store):
    feed.add("TXT", "Dad's flight is on 10/18")

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
    assert store.get_state(CURSOR_KEY) == "1"


def test_reads_every_page_and_resumes_from_the_cursor(feed, source, store):
    source.page_size = 2
    for i in range(5):
        feed.add(f"M{i}", f"Party on Oct {20 + i}", sent_at=f"2026-10-12T1{i}:00:00Z")
    agent, ext = make_agent(store, source)

    r = agent.poll_once()
    assert r.seen == 5 and [len(b) for b in ext.batches] == [2, 2, 1]
    assert store.get_state(CURSOR_KEY) == "5"

    feed.requests.clear()
    agent.poll_once()
    assert feed.requests == ["/messages/new?limit=2&since=5"]


def test_groups_are_batched_separately_and_named_in_prompt(feed, source, store):
    feed.add("A1", "Picnic on Oct 24", sent_at="2026-10-12T10:00:00Z", chat_jid="g1@g.us", group="Preschool", sender="Melissa")
    feed.add("B1", "Meeting on Oct 25", sent_at="2026-10-12T10:01:00Z", chat_jid="g2@g.us", group="Yearbook Committee")
    feed.add("A2", "Bring a dish", sent_at="2026-10-12T10:02:00Z", chat_jid="g1@g.us", group="Preschool")
    agent, ext = make_agent(store, source)
    agent.poll_once()
    assert ext.batches == [["A1", "A2"], ["B1"]]
    assert 'WhatsApp group "Preschool"' in ext.prompts[0]
    assert "from=Melissa" in ext.prompts[0]
    assert 'WhatsApp group "Yearbook Committee"' in ext.prompts[1]


def test_feed_down_is_transient_and_recovers(feed, source, store):
    feed.add("A", "Dad's flight is on 10/18")
    agent, ext = make_agent(store, source)
    feed.down = True
    r = agent.poll_once()
    assert r.transient_error and "seth-feed" in r.transient_error
    assert store.get_state("agent.last_poll_at") and store.get_state(CURSOR_KEY) is None

    feed.down = False
    agent.poll_once()
    assert ext.batches == [["A"]]


def test_album_caption_arriving_later_is_reprocessed(feed, source, store):
    feed.add("ALB", None, message_type="album", media_file_count=2)
    agent, ext = make_agent(store, source)
    agent.poll_once()
    assert ext.batches == []

    feed.set_body("ALB", "Recital is on Nov 3")
    agent.poll_once()
    assert ext.batches == [["ALB"]]


def test_update_uses_existing_calendar_in_prompt(feed, source, store):
    feed.add("A", "Dad's flight is on 10/18", sent_at="2026-10-12T10:00:00Z")

    def respond(batch):
        if batch[0].message_id == "A":
            return CalendarOps(operations=[EventOp(
                op="add", title="Dad's flight", date_start="2026-10-18", precision="day", source_message_ids=["A"])])
        return CalendarOps(operations=[EventOp(
            op="update", event_id=1, date_start="2026-10-20", source_message_ids=["E"], reason="edited")])

    agent, ext = make_agent(store, source, respond)
    agent.poll_once()
    feed.add("E", "Dad's flight is on 10/20", sent_at="2026-10-12T10:05:00Z", message_type="edit", edit_target_id="A")
    agent.poll_once()

    assert "id=1 | 2026-10-18 (day) | Dad's flight" in ext.prompts[1]
    assert "original text: \"Dad's flight is on 10/18\"" in ext.prompts[1]
    assert "<earlier_messages>" in ext.prompts[1]
    [event] = store.list_events()
    assert event["date_start"] == "2026-10-20"


def test_invalid_operation_does_not_drop_batch(feed, source, store):
    feed.add("A", "stuff")

    def respond(batch):
        return CalendarOps(operations=[
            EventOp(op="cancel", event_id=42, source_message_ids=["A"]),
            EventOp(op="add", title="Real", date_start="2026-10-20", precision="day", source_message_ids=["A"]),
        ])

    agent, _ = make_agent(store, source, respond)
    r = agent.poll_once()
    assert r.changes == 1
    assert [e["title"] for e in store.list_events()] == ["Real"]


def test_transient_error_retries_and_holds_high_water(feed, source, store):
    feed.add("A", "Dad's flight is on 10/18")
    calls = {"n": 0}

    def respond(batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("rate limited")
        return CalendarOps(operations=[])

    agent, ext = make_agent(store, source, respond)
    r = agent.poll_once()
    assert r.transient_error
    assert store.get_state(CURSOR_KEY) is None
    agent.poll_once()
    assert ext.batches == [["A"], ["A"]]


def test_permanent_error_is_recorded_and_skipped(feed, source, store):
    from seth_agent.extractors.base import PermanentError

    feed.add("A", "Dad's flight is on 10/18")

    def respond(batch):
        raise PermanentError("refusal")

    agent, ext = make_agent(store, source, respond)
    r = agent.poll_once()
    agent.poll_once()
    assert r.failed == 1 and len(ext.batches) == 1
    assert store.last_error()["error"] == "refusal"


@pytest.mark.parametrize("body,expected", [("ok", True), ("🙂", False), ("", False), ("10/18", True), ("下周一放假", True), ("好", True), ("！！👍", False)])
def test_wants(store, source, body, expected):
    ext = ScriptedCalendar(store, source, no_ops)
    from datetime import datetime, timezone

    msg = Message("c", "m", "s", datetime.now(timezone.utc), "", "text", body)
    assert ext.wants(msg) is expected


def test_chinese_messages_reach_claude_unescaped(feed, source, store):
    feed.add("C1", "下周一学校放假", sent_at="2026-10-12T17:00:00Z")
    ext = ScriptedCalendar(store, source, no_ops)
    Agent(store, source, [ext]).poll_once()
    assert "下周一学校放假" in ext.prompts[0] and "\\u" not in ext.prompts[0]
    assert "下周" in ext.system_prompt
