from fastapi.testclient import TestClient

from conftest import CHAT, TZ
from seth_agent.api import create_app


def test_calendar_and_event_endpoints(feed, source, store):
    feed.add("A", "Dad's flight is on 10/18")
    with store.transaction() as conn:
        store.add_event(conn, {"title": "Dad's flight", "date_start": "2026-10-18", "precision": "day"}, [(CHAT, "A")])
        store.add_event(conn, {"title": "Workout", "date_start": "2026-11-01", "precision": "month"}, [(CHAT, "A")])
    client = TestClient(create_app(store, source, TZ))

    r = client.get("/calendar", params={"from": "2026-10-01", "to": "2026-12-31"}).json()
    assert list(r["days"]) == ["2026-10-18", "Nov 2026"]
    assert r["days"]["2026-10-18"][0]["title"] == "Dad's flight"

    r = client.get("/calendar", params={"from": "2026-10-19", "to": "2026-10-31"}).json()
    assert r["days"] == {}

    e = client.get("/events/1").json()
    assert e["sources"][0]["message"]["body"] == "Dad's flight is on 10/18"
    assert client.get("/events/99").status_code == 404
    assert client.get("/health").json()["active_events"] >= 0


def test_dashboard_is_served_alongside_the_api(source, store):
    client = TestClient(create_app(store, source, TZ))
    page = client.get("/")
    assert page.status_code == 200 and "School calendar" in page.text and "Volunteering" in page.text
    # Status is the first tab, so it is the one that opens by default, and it has a Metrics section.
    assert page.text.index('id="tab-status"') < page.text.index('id="tab-calendar"')
    assert 'id="metrics-title">Metrics<' in page.text
    assert client.get("/seth-crest.png").status_code == 200
    assert client.get("/calendar").json()["today"]
    assert client.get("/health").status_code == 200


def test_metrics_with_no_photo_search(source, store):
    client = TestClient(create_app(store, source, TZ))
    m = client.get("/metrics").json()
    assert m["started_at"].endswith("Z")
    assert m["agent"] == {"extractors": {}, "failed_batches": 0, "events": {}}
    assert m["photos"]["enabled"] is False and m["photos"]["model"] is None
    assert m["photos"]["indexed"] == 0 and m["photos"]["by_group"] == []
    assert m["photos"]["searches"] == {"count": 0, "avg_ms": None, "last_ms": None, "last_at": None}


def test_metrics_count_events_and_what_each_extractor_did(feed, source, store):
    feed.add("A", "Dad's flight is on 10/18")
    msgs = source.fetch_new(None).messages
    with store.transaction() as conn:
        store.add_event(conn, {"title": "Flight", "date_start": "2026-10-18", "precision": "day"}, [(CHAT, "A")])
        store.add_event(conn, {"title": "Gone", "date_start": "2026-10-19", "precision": "day"}, [(CHAT, "A")])
        store.cancel_event(conn, 2, [(CHAT, "A")], "called off")
        store.mark_processed(conn, "calendar", msgs, "extracted")
        store.mark_processed(conn, "volunteering", msgs, "skipped")
    m = TestClient(create_app(store, source, TZ)).get("/metrics").json()
    assert m["agent"]["extractors"] == {"calendar": {"extracted": 1}, "volunteering": {"skipped": 1}}
    assert m["agent"]["events"] == {"school": {"active": 1, "cancelled": 1}}
