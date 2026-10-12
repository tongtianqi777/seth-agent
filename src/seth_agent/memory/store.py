"""The agent's own memory database (data/memory.db)."""

from __future__ import annotations

import calendar
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any

from ..source import Message

PRECISIONS = ("day", "range", "week", "month", "year")
EVENT_FIELDS = ("title", "details", "date_start", "date_end", "precision", "time", "confidence")


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_span(precision: str, start: date, end: date | None) -> tuple[date, date]:
    """Expand a fuzzy date into the inclusive [start, end] span it covers."""
    if precision == "day":
        return start, start
    if precision == "week":
        return start, end or start + timedelta(days=6)
    if precision == "month":
        last = calendar.monthrange(start.year, start.month)[1]
        return start.replace(day=1), start.replace(day=last)
    if precision == "year":
        return date(start.year, 1, 1), date(start.year, 12, 31)
    end = end or start
    return (start, end) if end >= start else (end, start)


def event_label(event: dict[str, Any]) -> str:
    """A human-readable key for the calendar view: 2026-10-19, Nov 2026, 2026, ..."""
    start = date.fromisoformat(event["date_start"])
    match event["precision"]:
        case "day":
            return event["date_start"]
        case "week":
            return f"Week of {event['date_start']}"
        case "month":
            return start.strftime("%b %Y")
        case "year":
            return str(start.year)
        case _:
            return f"{event['date_start']}..{event['date_end']}"


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = resources.files(__package__).joinpath("schema.sql").read_text()
        with self.transaction() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(schema)

    # -- connections -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # One short-lived connection per operation keeps this safe to use from both the
        # poll thread and the API's worker threads.
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # -- agent bookkeeping ---------------------------------------------------

    def get_state(self, key: str) -> str | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT value FROM agent_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
        sql = "INSERT INTO agent_state (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        if conn is not None:
            conn.execute(sql, (key, value))
            return
        with self.transaction() as c:
            c.execute(sql, (key, value))

    def unprocessed(self, extractor: str, messages: Iterable[Message]) -> list[Message]:
        """The messages this extractor hasn't handled yet, or whose body changed since."""
        with self.transaction() as conn:
            seen = {
                (r["chat_jid"], r["message_id"]): r["body_hash"]
                for r in conn.execute(
                    "SELECT chat_jid, message_id, body_hash FROM processed_messages WHERE extractor = ?",
                    (extractor,),
                )
            }
        return [m for m in messages if seen.get(m.key) != m.body_hash]

    def mark_processed(
        self, conn: sqlite3.Connection, extractor: str, messages: Iterable[Message], outcome: str
    ) -> None:
        conn.executemany(
            """INSERT INTO processed_messages (extractor, chat_jid, message_id, body_hash, outcome, processed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (extractor, chat_jid, message_id) DO UPDATE SET
                   body_hash = excluded.body_hash, outcome = excluded.outcome, processed_at = excluded.processed_at""",
            [(extractor, m.chat_jid, m.message_id, m.body_hash, outcome, now_ts()) for m in messages],
        )

    def record_error(
        self, conn: sqlite3.Connection, extractor: str, messages: Iterable[Message], error: str
    ) -> None:
        conn.execute(
            "INSERT INTO extraction_errors (extractor, message_ids, error, at) VALUES (?, ?, ?, ?)",
            (extractor, json.dumps([list(m.key) for m in messages]), error, now_ts()),
        )

    def last_error(self) -> dict[str, Any] | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM extraction_errors ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # -- calendar: writes ------------------------------------------------------

    def add_event(
        self,
        conn: sqlite3.Connection,
        fields: dict[str, Any],
        sources: Iterable[tuple[str, str]],
        reason: str | None = None,
    ) -> int:
        fields = _normalize_fields(fields)
        ts = now_ts()
        cur = conn.execute(
            """INSERT INTO events (title, details, date_start, date_end, precision, time, confidence, created_at, updated_at)
               VALUES (:title, :details, :date_start, :date_end, :precision, :time, :confidence, :ts, :ts)""",
            {**{f: fields.get(f) for f in EVENT_FIELDS}, "ts": ts},
        )
        event_id = cur.lastrowid
        assert event_id is not None
        self._add_sources(conn, event_id, sources, "created")
        self._history(conn, event_id, "add", None, self._row(conn, event_id), reason)
        return event_id

    def update_event(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        changes: dict[str, Any],
        sources: Iterable[tuple[str, str]],
        reason: str | None = None,
    ) -> None:
        before = self._row(conn, event_id)
        if before is None:
            raise KeyError(f"event {event_id} not found")
        merged = {f: before[f] for f in EVENT_FIELDS}
        merged.update({k: v for k, v in changes.items() if k in EVENT_FIELDS and v is not None})
        # A new start date with no explicit end means the old end no longer applies.
        if changes.get("date_start") and not changes.get("date_end"):
            merged["date_end"] = None
        merged = _normalize_fields(merged)
        conn.execute(
            """UPDATE events SET title = :title, details = :details, date_start = :date_start, date_end = :date_end,
                   precision = :precision, time = :time, confidence = :confidence, status = 'active', updated_at = :ts
               WHERE id = :id""",
            {**merged, "ts": now_ts(), "id": event_id},
        )
        self._add_sources(conn, event_id, sources, "updated")
        self._history(conn, event_id, "update", before, self._row(conn, event_id), reason)

    def cancel_event(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        sources: Iterable[tuple[str, str]],
        reason: str | None = None,
    ) -> None:
        before = self._row(conn, event_id)
        if before is None:
            raise KeyError(f"event {event_id} not found")
        conn.execute(
            "UPDATE events SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_ts(), event_id)
        )
        self._add_sources(conn, event_id, sources, "cancelled")
        self._history(conn, event_id, "cancel", before, self._row(conn, event_id), reason)

    def _add_sources(self, conn, event_id: int, sources: Iterable[tuple[str, str]], role: str) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO event_sources (event_id, chat_jid, message_id, role) VALUES (?, ?, ?, ?)",
            [(event_id, chat_jid, message_id, role) for chat_jid, message_id in sources],
        )

    def _history(self, conn, event_id: int, op: str, before, after, reason: str | None) -> None:
        conn.execute(
            "INSERT INTO event_history (event_id, op, before_json, after_json, reason, at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, op, json.dumps(before) if before else None, json.dumps(after) if after else None, reason, now_ts()),
        )

    def _row(self, conn, event_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    # -- calendar: reads -------------------------------------------------------

    def list_events(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        """Events whose span overlaps [date_from, date_to], ordered by start date."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if not include_cancelled:
            sql += " AND status = 'active'"
        if date_from:
            sql += " AND date_end >= ?"
            params.append(date_from.isoformat())
        if date_to:
            sql += " AND date_start <= ?"
            params.append(date_to.isoformat())
        sql += " ORDER BY date_start, date_end, id"
        with self.transaction() as conn:
            events = [dict(r) for r in conn.execute(sql, params)]
            self._attach_sources(conn, events)
        for e in events:
            e["label"] = event_label(e)
        return events

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self.transaction() as conn:
            event = self._row(conn, event_id)
            if event is None:
                return None
            self._attach_sources(conn, [event])
            event["history"] = [
                {
                    **dict(r),
                    "before_json": json.loads(r["before_json"]) if r["before_json"] else None,
                    "after_json": json.loads(r["after_json"]) if r["after_json"] else None,
                }
                for r in conn.execute(
                    "SELECT op, before_json, after_json, reason, at FROM event_history WHERE event_id = ? ORDER BY id",
                    (event_id,),
                )
            ]
        event["label"] = event_label(event)
        return event

    def events_for_messages(self, keys: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        keys = list(keys)
        if not keys:
            return []
        where = " OR ".join("(s.chat_jid = ? AND s.message_id = ?)" for _ in keys)
        with self.transaction() as conn:
            events = [
                dict(r)
                for r in conn.execute(
                    f"SELECT DISTINCT e.* FROM events e JOIN event_sources s ON s.event_id = e.id WHERE {where}",
                    [v for k in keys for v in k],
                )
            ]
            self._attach_sources(conn, events)
        return events

    def _attach_sources(self, conn, events: list[dict[str, Any]]) -> None:
        for e in events:
            e["sources"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT chat_jid, message_id, role FROM event_sources WHERE event_id = ? ORDER BY rowid",
                    (e["id"],),
                )
            ]


def _normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    precision = out.get("precision") or "day"
    if precision not in PRECISIONS:
        raise ValueError(f"unknown precision {precision!r}")
    if not out.get("title"):
        raise ValueError("event needs a title")
    start = date.fromisoformat(out["date_start"])
    end = date.fromisoformat(out["date_end"]) if out.get("date_end") else None
    start, end = normalize_span(precision, start, end)
    out.update(precision=precision, date_start=start.isoformat(), date_end=end.isoformat())
    return out
