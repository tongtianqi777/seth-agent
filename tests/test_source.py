from datetime import datetime, timezone

import httpx
import pytest

from conftest import CHAT, GROUP
from seth_agent.source import MessageSource, SourceError


def test_fetch_new_follows_cursor_and_pages(feed, source):
    source.page_size = 2
    for i, mid in enumerate("ABC"):
        feed.add(mid, f"msg {mid}", sent_at=f"2026-10-12T1{i}:00:00Z")

    page = source.fetch_new(None)
    assert [m.message_id for m in page.messages] == ["A", "B"] and page.has_more
    page = source.fetch_new(page.cursor)
    assert [m.message_id for m in page.messages] == ["C"] and not page.has_more
    assert source.fetch_new(page.cursor).messages == []


def test_messages_carry_group_and_sender(feed, source):
    feed.add("A", "hi", sender="Melissa Chen")
    [m] = source.fetch_new(None).messages
    assert (m.chat_jid, m.group, m.sender) == (CHAT, GROUP, "Melissa Chen")
    assert m.sent_at == datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)


def test_current_cursor_skips_history(feed, source):
    feed.add("A", "old")
    assert source.fetch_new(source.current_cursor()).messages == []


def test_edit_and_reply_join_original_text(feed, source):
    feed.add("A", "Dad's flight is on 10/18", sent_at="2026-10-12T10:00:00Z")
    feed.add("E", "Dad's flight is on 10/19", sent_at="2026-10-12T10:05:00Z", message_type="edit", edit_target_id="A")
    feed.add("R", "cancelled now", sent_at="2026-10-12T10:06:00Z", quoted_id="A")
    msgs = {m.message_id: m for m in source.fetch_new(None).messages}
    # The original row already shows its latest edit; the edit row carries the new text and the old one.
    assert msgs["A"].body == "Dad's flight is on 10/19"
    assert msgs["E"].body == "Dad's flight is on 10/19"
    assert msgs["E"].edit_target_body == "Dad's flight is on 10/18"
    assert msgs["R"].quoted_body == "Dad's flight is on 10/19"


def test_reply_to_message_outside_the_page_is_fetched(feed, source):
    feed.add("A", "Picnic is Saturday", sent_at="2026-10-12T10:00:00Z")
    feed.add("R", "what time?", sent_at="2026-10-12T10:06:00Z", quoted_id="A")
    cursor = "1"
    [r] = source.fetch_new(cursor).messages
    assert r.quoted_body == "Picnic is Saturday"


def test_reply_to_unknown_message_has_no_quoted_text(feed, source):
    feed.add("R", "what time?", quoted_id="GONE")
    [r] = source.fetch_new(None).messages
    assert r.quoted_id == "GONE" and r.quoted_body is None


def test_album_sent_again_with_caption(feed, source):
    feed.add("ALB", None, message_type="album", media_file_count=2)
    first = source.fetch_new(None)
    feed.set_body("ALB", "Recital is on Nov 3")
    [again] = source.fetch_new(first.cursor).messages
    assert again.message_id == "ALB" and again.body == "Recital is on Nov 3"


def test_fetch_context_is_same_group_oldest_first(feed, source):
    for i in range(5):
        feed.add(f"M{i}", f"msg {i}", sent_at=f"2026-10-12T1{i}:00:00Z")
    feed.add("OTHER", "elsewhere", sent_at="2026-10-12T13:30:00Z", chat_jid="120363000000000002@g.us", group="Acorn")
    feed.add("E", "msg 2 fixed", sent_at="2026-10-12T13:40:00Z", message_type="edit", edit_target_id="M2")
    ctx = source.fetch_context(CHAT, datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc), limit=2)
    assert [m.message_id for m in ctx] == ["M2", "M3"]
    assert ctx[0].body == "msg 2 fixed"


def test_get_messages(feed, source):
    feed.add("A", "hi")
    assert [m.body for m in source.get_messages([(CHAT, "A"), (CHAT, "missing")])] == ["hi"]
    assert source.get_messages([]) == []


def test_unreachable_feed_raises_source_error(feed, source):
    feed.down = True
    with pytest.raises(SourceError):
        source.fetch_new(None)


def test_error_status_raises_source_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"error": "db"})), base_url="http://feed")
    with pytest.raises(SourceError, match="500"):
        MessageSource("http://feed", client=client).fetch_new(None)
