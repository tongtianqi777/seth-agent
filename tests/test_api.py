from fastapi.testclient import TestClient

from conftest import CHAT, TZ
from seth_agent.api import create_app


def test_calendar_and_event_endpoints(source_db, source, store):
    source_db.add("A", "Dad's flight is on 10/18")
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
