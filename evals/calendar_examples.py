"""Live check of the calendar extractor against Claude, using the examples from the spec.

Costs a few API calls. Run with:  ANTHROPIC_API_KEY=... .venv/bin/python evals/calendar_examples.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from seth_agent.agent import Agent
from seth_agent.extractors.calendar import CalendarExtractor
from seth_agent.memory.store import MemoryStore
from seth_agent.source import MessageSource

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fakefeed import FakeFeed  # noqa: E402  (the in-memory seth-feed used by the tests)

TZ = ZoneInfo("America/Los_Angeles")

# Sent Monday 2026-10-12, mid-morning Pacific time.
ROUND_1 = [
    ("M1", "Hello"),
    ("M2", "The school is off on Monday"),
    ("M3", "My workout session has been rescheduled to Nov"),
    ("M4", "Dad’s flight is on 10/18"),
    ("M5", "I’m outside waiting for you"),
    ("M6", "Will you be able to make it?"),
]
ROUND_2 = [("M7", "Oh wait, dad's flight got moved to 10/20")]


def add(feed: FakeFeed, rows, start_minute: int) -> None:
    for i, (mid, body) in enumerate(rows):
        feed.add(mid, body, sent_at=f"2026-10-12T17:{start_minute + i:02d}:00Z", sender="Alex Doe")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    feed = FakeFeed()
    store, source = MemoryStore(tmp / "memory.db"), MessageSource("http://feed", client=feed.client)
    agent = Agent(store, source, [CalendarExtractor(store, source, TZ)])

    add(feed, ROUND_1, 0)
    agent.poll_once()
    add(feed, ROUND_2, 30)
    agent.poll_once()

    events = store.list_events(include_cancelled=True)
    for e in events:
        print(f"  #{e['id']} {e['label']:<12} {e['status']:<9} {e['title']}  (sources: {[s['message_id'] for s in e['sources']]})")

    active = [e for e in events if e["status"] == "active"]
    by_source = {s["message_id"]: e for e in active for s in e["sources"]}
    checks = {
        "school off -> 2026-10-19": by_source.get("M2", {}).get("date_start") == "2026-10-19",
        "workout -> Nov 2026 (month)": by_source.get("M3", {}).get("label") == "Nov 2026",
        "dad's flight moved -> 2026-10-20": by_source.get("M4", {}).get("date_start") == "2026-10-20",
        "flight updated, not duplicated": sum("flight" in e["title"].lower() for e in active) == 1,
        "no events from M1/M5/M6": not any(m in by_source for m in ("M1", "M5", "M6")),
        "exactly 3 active events": len(active) == 3,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
