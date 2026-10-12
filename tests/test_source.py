from datetime import datetime, timezone

from conftest import CHAT


def test_fetch_candidates_with_reread_window(source_db, source):
    source_db.add("A", "first", sent_at="2026-10-12T10:00:00Z")
    source_db.add("B", "second", sent_at="2026-10-12T11:00:00Z")
    source_db.add("C", "third", sent_at="2026-10-12T12:00:00Z")

    assert [m.message_id for m in source.fetch_candidates(None)] == ["A", "B", "C"]
    # 10:55 is inside the 10 minute window behind 11:05, 10:00 is not.
    assert [m.message_id for m in source.fetch_candidates("2026-10-12T11:05:00Z")] == ["B", "C"]


def test_edit_and_reply_join_original_text(source_db, source):
    source_db.add("A", "Dad's flight is on 10/18", sent_at="2026-10-12T10:00:00Z")
    source_db.add("E", "Dad's flight is on 10/19", sent_at="2026-10-12T10:05:00Z", message_type="edit", edit_target_id="A")
    source_db.add("R", "cancelled now", sent_at="2026-10-12T10:06:00Z", quoted_id="A")
    msgs = {m.message_id: m for m in source.fetch_candidates(None)}
    assert msgs["E"].edit_target_body == "Dad's flight is on 10/18"
    assert msgs["R"].quoted_body == "Dad's flight is on 10/18"
    assert msgs["A"].sent_at == datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)


def test_fetch_context(source_db, source):
    for i in range(5):
        source_db.add(f"M{i}", f"msg {i}", sent_at=f"2026-10-12T1{i}:00:00Z")
    ctx = source.fetch_context(CHAT, datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc), limit=2)
    assert [m.message_id for m in ctx] == ["M2", "M3"]


def test_get_messages(source_db, source):
    source_db.add("A", "hi")
    assert [m.body for m in source.get_messages([(CHAT, "A"), (CHAT, "missing")])] == ["hi"]


def test_opens_wal_db_without_shm(source_db, source):
    # With no -wal/-shm files, mode=ro can't open a WAL database; the immutable fallback must kick in.
    source_db.add("A", "hi")
    for suffix in ("-wal", "-shm"):
        p = source_db.path.with_name(source_db.path.name + suffix)
        if p.exists():
            p.unlink()
    assert [m.message_id for m in source.fetch_candidates(None)] == ["A"]
