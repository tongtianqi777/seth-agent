"""Volunteering groups: their messages go to their own calendar, not the school calendar."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from conftest import CHAT, TZ
from seth_agent.agent import Agent
from seth_agent.api import create_app
from seth_agent.cli import main
from seth_agent.config import Config
from seth_agent.extractors.calendar import CalendarExtractor, CalendarOps, EventOp
from seth_agent.extractors.volunteering import VolunteeringExtractor
from seth_agent.groups import VolunteeringGroups, parse_groups
from seth_agent.memory.store import MemoryStore

PE = "Preschool P.E. Teachers"
PE_CHAT = "pe@g.us"
GROUPS = VolunteeringGroups([PE])


class Scripted:
    """Mixin: replaces the Claude call with a function of the batch and records the prompts."""

    def __init__(self, store, source, respond):
        super().__init__(store, source, TZ, llm=object(), volunteering=GROUPS)  # type: ignore[arg-type]
        self.respond = respond
        self.batches: list[list[str]] = []
        self.prompts: list[str] = []

    def extract(self, batch):
        self.batches.append([m.message_id for m in batch])
        self.prompts.append(self.build_prompt(batch))
        return self.respond(batch)


class ScriptedSchool(Scripted, CalendarExtractor):
    pass


class ScriptedVolunteering(Scripted, VolunteeringExtractor):
    pass


def add_op(title, date, source, **kw):
    return EventOp(op="add", title=title, date_start=date, precision="day", source_message_ids=[source], **kw)


def make_agent(store, source, school=None, volunteering=None):
    none = lambda batch: CalendarOps(operations=[])  # noqa: E731
    school_ext = ScriptedSchool(store, source, school or none)
    vol_ext = ScriptedVolunteering(store, source, volunteering or none)
    return Agent(store, source, [school_ext, vol_ext]), school_ext, vol_ext


def test_group_names_match_loosely():
    groups = VolunteeringGroups(["Preschool P.E. Teachers", "  "])
    assert groups.calendar_for("preschool  p.e. teachers ") == PE
    assert groups.calendar_for("Preschool") is None and groups.calendar_for("") is None
    assert list(groups) == [PE] and len(groups) == 1


def test_parse_groups():
    assert parse_groups("A=x, y; B ;; =oops") == {"A": "x, y", "B": ""}
    assert parse_groups("") == {}


def test_prompt_gives_claude_the_usual_routine_and_the_sender(feed, source, store):
    feed.add("V1", "It's me this week", sent_at="2026-10-12T17:00:00Z", chat_jid=PE_CHAT, group=PE, sender="Alex Doe")
    feed.add("S1", "It's me this week", sent_at="2026-10-12T17:00:00Z", chat_jid="g1@g.us", group="Preschool")
    groups = VolunteeringGroups({PE: "The preschool P.E. class is usually on Fridays at 11:30am."})
    school = ScriptedSchool(store, source, lambda b: CalendarOps(operations=[]))
    vol = ScriptedVolunteering(store, source, lambda b: CalendarOps(operations=[]))
    school.volunteering = vol.volunteering = groups
    Agent(store, source, [school, vol]).poll_once()

    assert "The group's usual routine: The preschool P.E. class is usually on Fridays at 11:30am." in vol.prompts[0]
    assert "from=Alex Doe" in vol.prompts[0] and 'WhatsApp group "Preschool P.E. Teachers"' in vol.prompts[0]
    assert "usual routine" not in school.prompts[0]  # ordinary groups get no routine line
    assert "Reading who is on duty" in vol.system_prompt and "Reading who is on duty" not in school.system_prompt


def test_each_group_goes_to_one_extractor(feed, source, store):
    feed.add("S1", "Picnic on Oct 24", sent_at="2026-10-12T10:00:00Z", chat_jid="g1@g.us", group="Preschool")
    feed.add("V1", "I can help Thursday 9-10", sent_at="2026-10-12T10:01:00Z", chat_jid=PE_CHAT, group=PE)
    feed.add("V2", "Bring water bottles", sent_at="2026-10-12T10:02:00Z", chat_jid=PE_CHAT, group="preschool p.e. teachers")
    agent, school, vol = make_agent(store, source)

    r = agent.poll_once()
    assert school.batches == [["S1"]]
    assert vol.batches == [["V1", "V2"]]
    assert (r.seen, r.skipped, r.extracted) == (3, 3, 3)  # each extractor skipped what belongs to the other
    assert "volunteering calendar" in vol.system_prompt and "school-community calendar" in school.system_prompt


def test_events_land_on_the_right_calendar(feed, source, store):
    feed.add("S1", "Picnic on Oct 24", chat_jid="g1@g.us", group="Preschool")
    feed.add("V1", "I can help Thursday 9-10", chat_jid=PE_CHAT, group=PE)
    agent, _, _ = make_agent(
        store,
        source,
        school=lambda b: CalendarOps(operations=[add_op("Picnic", "2026-10-24", "S1")]),
        volunteering=lambda b: CalendarOps(operations=[add_op("P.E. volunteers", "2026-10-15", "V1", time="09:00-10:00")]),
    )
    agent.poll_once()

    assert [e["title"] for e in store.list_events(category="school")] == ["Picnic"]
    [vol] = store.list_events(category="volunteering", calendar=PE)
    assert (vol["title"], vol["category"], vol["calendar"], vol["time"]) == ("P.E. volunteers", "volunteering", PE, "09:00-10:00")
    assert store.calendar_names("volunteering") == [PE]


def test_each_extractor_only_sees_its_own_calendar(feed, source, store):
    with store.transaction() as conn:
        store.add_event(conn, {"title": "Picnic", "date_start": "2026-10-24", "precision": "day"}, [(CHAT, "x")])
        store.add_event(
            conn, {"title": "Other class helpers", "date_start": "2026-10-16", "precision": "day"}, [(CHAT, "x")],
            category="volunteering", calendar="Some Other Group",
        )
    feed.add("V1", "Thursday works for me", sent_at="2026-10-12T10:01:00Z", chat_jid=PE_CHAT, group=PE)
    feed.add("V2", "Great, see you then", sent_at="2026-10-12T10:02:00Z", chat_jid=PE_CHAT, group=PE)
    agent, _, vol = make_agent(store, source, volunteering=lambda b: CalendarOps(operations=[add_op("P.E. volunteers", "2026-10-15", "V1")]))
    agent.poll_once()
    assert "Picnic" not in vol.prompts[0] and "Other class helpers" not in vol.prompts[0]

    feed.add("V3", "Also bring a hat", sent_at="2026-10-12T10:03:00Z", chat_jid=PE_CHAT, group=PE)
    agent.poll_once()
    assert "id=3 | 2026-10-15 (day) | P.E. volunteers" in vol.prompts[1]
    assert "Other class helpers" not in vol.prompts[1]


def test_update_or_cancel_of_another_calendars_event_is_ignored(feed, source, store):
    with store.transaction() as conn:
        school_id = store.add_event(conn, {"title": "Picnic", "date_start": "2026-10-24", "precision": "day"}, [(CHAT, "x")])
    feed.add("V1", "Thursday is off", chat_jid=PE_CHAT, group=PE)

    def respond(batch):
        return CalendarOps(operations=[
            EventOp(op="cancel", event_id=school_id, source_message_ids=["V1"]),
            EventOp(op="update", event_id=school_id, title="Hijacked", source_message_ids=["V1"]),
        ])

    agent, _, _ = make_agent(store, source, volunteering=respond)
    assert agent.poll_once().changes == 0
    [picnic] = store.list_events()
    assert (picnic["title"], picnic["status"]) == ("Picnic", "active")


def test_database_from_before_volunteering_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, details TEXT, date_start TEXT NOT NULL,
                date_end TEXT NOT NULL, precision TEXT NOT NULL, time TEXT,
                status TEXT NOT NULL DEFAULT 'active', confidence REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO events (title, date_start, date_end, precision, created_at, updated_at)
                VALUES ('School closed', '2026-10-19', '2026-10-19', 'day', 'then', 'then');
        """)
    store = MemoryStore(path)
    MemoryStore(path)  # opening it again is harmless
    [event] = store.list_events(category="school")
    assert (event["title"], event["category"], event["calendar"]) == ("School closed", "school", "School")
    assert store.list_events(category="volunteering") == []


def test_api_serves_volunteering_calendars(feed, source, store):
    feed.add("V1", "I can help Thursday", chat_jid=PE_CHAT, group=PE)
    with store.transaction() as conn:
        store.add_event(conn, {"title": "Picnic", "date_start": "2026-10-24", "precision": "day"}, [(PE_CHAT, "V1")])
        store.add_event(
            conn, {"title": "P.E. volunteers", "date_start": "2026-10-15", "precision": "day"}, [(PE_CHAT, "V1")],
            category="volunteering", calendar=PE,
        )
    client = TestClient(create_app(store, source, TZ, GROUPS))

    assert client.get("/volunteering").json() == {"calendars": [PE]}
    school = client.get("/calendar", params={"from": "2026-10-01"}).json()
    assert [e["title"] for e in school["events"]] == ["Picnic"] and "group" not in school
    vol = client.get("/calendar", params={"from": "2026-10-01", "group": "preschool p.e. teachers"}).json()
    assert vol["group"] == PE and [e["title"] for e in vol["events"]] == ["P.E. volunteers"]
    assert client.get("/calendar", params={"group": "Nope"}).status_code == 404
    assert client.get("/events/2").json()["calendar"] == PE
    health = client.get("/health").json()
    assert (health["active_events"], health["active_volunteering_events"]) == (1, 1)


def test_api_lists_configured_group_with_no_events_first(source, store):
    with store.transaction() as conn:
        store.add_event(
            conn, {"title": "Old", "date_start": "2026-10-15", "precision": "day"}, [(CHAT, "x")],
            category="volunteering", calendar="Retired group",
        )
    client = TestClient(create_app(store, source, TZ, GROUPS))
    assert client.get("/volunteering").json() == {"calendars": [PE, "Retired group"]}
    assert client.get("/calendar", params={"group": PE}).json()["events"] == []


def test_config_reads_volunteering_groups(monkeypatch):
    monkeypatch.delenv("SETH_VOLUNTEERING_GROUPS", raising=False)
    assert list(Config.from_env().volunteering) == [PE]  # the default
    assert "Fridays at 11:30" in Config.from_env().volunteering.routine_for(PE)
    monkeypatch.setenv("SETH_VOLUNTEERING_GROUPS", "Library Helpers = Tuesdays at 9am, before school; Field Day Crew ;")
    groups = Config.from_env().volunteering
    assert list(groups) == ["Library Helpers", "Field Day Crew"]
    assert groups.routine_for("library helpers") == "Tuesdays at 9am, before school"
    assert groups.routine_for("Field Day Crew") == ""
    monkeypatch.setenv("SETH_VOLUNTEERING_GROUPS", "")
    assert len(Config.from_env().volunteering) == 0


def test_cli_prints_a_volunteering_calendar(store, monkeypatch, capsys):
    with store.transaction() as conn:
        store.add_event(conn, {"title": "Picnic", "date_start": "2026-10-24", "precision": "day"}, [(CHAT, "x")])
        store.add_event(
            conn, {"title": "P.E. volunteers", "date_start": "2026-10-15", "precision": "day"}, [(CHAT, "x")],
            category="volunteering", calendar=PE,
        )
    monkeypatch.setenv("SETH_MEMORY_DB", str(store.path))
    monkeypatch.setenv("SETH_TZ", "America/Los_Angeles")
    monkeypatch.delenv("SETH_VOLUNTEERING_GROUPS", raising=False)

    main(["calendar", "--from", "2026-10-01"])
    assert "Picnic" in capsys.readouterr().out
    main(["calendar", "--from", "2026-10-01", "--group", "preschool p.e. teachers"])
    out = capsys.readouterr().out
    assert "P.E. volunteers" in out and "Picnic" not in out
