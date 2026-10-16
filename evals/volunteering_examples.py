"""Live check of the volunteering extractor against Claude: "it's me this week" style messages.

Costs a few API calls. Run with:  ANTHROPIC_API_KEY=... .venv/bin/python evals/volunteering_examples.py
To try a local model instead:  SETH_PROVIDER=local SETH_LOCAL_MODEL=gemma4:31b .venv/bin/python evals/volunteering_examples.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from seth_agent.agent import Agent
from seth_agent.config import Config
from seth_agent.extractors.volunteering import VolunteeringExtractor
from seth_agent.llm import build_llm
from seth_agent.memory.store import MemoryStore
from seth_agent.source import MessageSource

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fakefeed import FakeFeed  # noqa: E402  (the in-memory seth-feed used by the tests)

TZ = ZoneInfo("America/Los_Angeles")
GROUP, CHAT = "Preschool P.E. Teachers", "pe@g.us"

# (id, sender, text), sent Monday 2026-10-12, mid-morning Pacific time. The usual class is Fridays at 11:30.
ROUND_1 = [
    ("V1", "Melissa Chen", "Hi everyone, thanks for signing up!"),
    ("V2", "Sarah Lopez", "It's me this week"),
    ("V3", "Melissa Chen", "Great. Who has next week?"),
    ("V4", "Tom Reyes", "It's Mia's mom next week"),
    ("V5", "Melissa Chen", "Thank you all!"),
]
ROUND_2 = [("V6", "Sarah Lopez", "Sorry, it's actually Tom this week, we have a family thing")]


def add(feed: FakeFeed, rows, start_minute: int) -> None:
    for i, (mid, sender, body) in enumerate(rows):
        feed.add(mid, body, sent_at=f"2026-10-12T17:{start_minute + i:02d}:00Z", chat_jid=CHAT, group=GROUP, sender=sender)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    feed = FakeFeed()
    store, source = MemoryStore(tmp / "memory.db"), MessageSource("http://feed", client=feed.client)
    volunteering = Config.from_env().volunteering  # the real defaults, including the usual routine
    agent = Agent(store, source, [VolunteeringExtractor(store, source, TZ, llm=build_llm(Config.from_env()), volunteering=volunteering)])

    add(feed, ROUND_1, 0)
    agent.poll_once()
    first = store.list_events(category="volunteering", calendar=GROUP)
    add(feed, ROUND_2, 30)
    agent.poll_once()

    events = store.list_events(include_cancelled=True)
    for e in events:
        print(f"  #{e['id']} {e['label']:<12} {e['time'] or '-':<6} {e['status']:<9} {e['title']} | {e['details']}")

    active = [e for e in events if e["status"] == "active"]
    by_date = {e["date_start"]: e for e in active}
    this_week, next_week = by_date.get("2026-10-16", {}), by_date.get("2026-10-23", {})
    checks = {
        "first round: this week's class is Fri 2026-10-16 at 11:30 with Sarah": any(
            e["date_start"] == "2026-10-16" and e["time"] and e["time"].startswith("11:30") and "Sarah" in e["title"]
            for e in first
        ),
        "next week's class is Fri 2026-10-23 with Mia's mom": "Mia" in next_week.get("title", ""),
        "this week moved to Tom, not duplicated": "Tom" in this_week.get("title", "") and "Sarah" not in this_week.get("title", ""),
        "exactly 2 active events, all on the P.E. calendar": len(active) == 2 and all(e["calendar"] == GROUP for e in active),
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
