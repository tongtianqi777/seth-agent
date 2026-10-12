from datetime import date

import pytest

from seth_agent.memory.store import event_label, normalize_span

SRC = [("chat", "m1")]


def test_normalize_span():
    assert normalize_span("day", date(2026, 10, 18), date(2026, 10, 20)) == (date(2026, 10, 18), date(2026, 10, 18))
    assert normalize_span("month", date(2026, 11, 15), None) == (date(2026, 11, 1), date(2026, 11, 30))
    assert normalize_span("year", date(2027, 5, 1), None) == (date(2027, 1, 1), date(2027, 12, 31))
    assert normalize_span("week", date(2026, 10, 19), None) == (date(2026, 10, 19), date(2026, 10, 25))
    assert normalize_span("range", date(2026, 10, 20), date(2026, 10, 18)) == (date(2026, 10, 18), date(2026, 10, 20))


def test_add_and_query_by_overlap(store):
    with store.transaction() as conn:
        store.add_event(conn, {"title": "School closed", "date_start": "2026-10-19", "precision": "day"}, SRC)
        store.add_event(conn, {"title": "Workout", "date_start": "2026-11-01", "precision": "month"}, SRC)
        store.add_event(conn, {"title": "Dad's flight", "date_start": "2026-10-18", "precision": "day"}, SRC)

    labels = [e["label"] for e in store.list_events()]
    assert labels == ["2026-10-18", "2026-10-19", "Nov 2026"]

    # A query for mid-November still finds the month-precision event.
    mid_nov = store.list_events(date(2026, 11, 10), date(2026, 11, 12))
    assert [e["title"] for e in mid_nov] == ["Workout"]
    assert [e["title"] for e in store.list_events(date(2026, 10, 19), date(2026, 10, 19))] == ["School closed"]


def test_update_and_cancel_keep_history(store):
    with store.transaction() as conn:
        eid = store.add_event(conn, {"title": "Dad's flight", "date_start": "2026-10-18", "precision": "day"}, SRC)
    with store.transaction() as conn:
        store.update_event(conn, eid, {"date_start": "2026-10-20", "title": None}, [("chat", "m2")], "moved")
    e = store.get_event(eid)
    assert (e["title"], e["date_start"], e["date_end"]) == ("Dad's flight", "2026-10-20", "2026-10-20")

    with store.transaction() as conn:
        store.cancel_event(conn, eid, [("chat", "m3")], "called off")
    assert store.list_events() == []
    e = store.get_event(eid)
    assert e["status"] == "cancelled"
    assert [h["op"] for h in e["history"]] == ["add", "update", "cancel"]
    assert [s["role"] for s in e["sources"]] == ["created", "updated", "cancelled"]
    assert len(store.list_events(include_cancelled=True)) == 1


def test_invalid_events_rejected(store):
    with store.transaction() as conn:
        with pytest.raises(ValueError):
            store.add_event(conn, {"title": "", "date_start": "2026-10-18"}, SRC)
        with pytest.raises(KeyError):
            store.update_event(conn, 999, {"title": "x"}, SRC)


def test_event_label_range():
    assert event_label({"date_start": "2026-10-18", "date_end": "2026-10-20", "precision": "range"}) == "2026-10-18..2026-10-20"
