"""Read-only access to seth-ingestion's messages.db.

See /Users/tnpbot/github.com/seth-ingestion/docs/DATABASE.md for the schema. This module never writes to it.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Album captions can be filled in after a row was first written, without changing ingested_at.
# Re-reading a short window behind the high-water mark picks those up.
REREAD_WINDOW = timedelta(minutes=10)

_COLUMNS = """
    m.chat_jid, m.message_id, m.sender, m.sent_at, m.ingested_at, m.message_type, m.body,
    m.edit_target_id, m.quoted_id, m.media_file_count,
    orig.body AS edit_target_body, q.body AS quoted_body
"""
_JOINS = """
    LEFT JOIN messages orig ON m.message_type = 'edit'
        AND orig.chat_jid = m.chat_jid AND orig.message_id = m.edit_target_id
    LEFT JOIN messages q ON q.chat_jid = m.chat_jid AND q.message_id = m.quoted_id
"""


@dataclass(frozen=True)
class Message:
    chat_jid: str
    message_id: str
    sender: str
    sent_at: datetime  # aware, UTC
    ingested_at: str  # RFC3339 UTC string, as stored
    message_type: str
    body: str | None
    edit_target_id: str | None = None
    quoted_id: str | None = None
    media_file_count: int = 0
    edit_target_body: str | None = None
    quoted_body: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.chat_jid, self.message_id)

    @property
    def body_hash(self) -> str:
        return hashlib.sha256((self.body or "").encode()).hexdigest()[:16]


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        chat_jid=row["chat_jid"],
        message_id=row["message_id"],
        sender=row["sender"],
        sent_at=parse_ts(row["sent_at"]),
        ingested_at=row["ingested_at"],
        message_type=row["message_type"],
        body=row["body"],
        edit_target_id=row["edit_target_id"],
        quoted_id=row["quoted_id"],
        media_file_count=row["media_file_count"],
        edit_target_body=row["edit_target_body"],
        quoted_body=row["quoted_body"],
    )


class MessageSource:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            conn.execute("SELECT 1 FROM messages LIMIT 1")
        except sqlite3.OperationalError as e:
            if "unable to open" not in str(e):
                raise
            # A WAL database can't be opened read-only when its -shm file doesn't exist,
            # which is the case while seth-ingestion is stopped. immutable=1 skips WAL handling.
            # That is only safe because nothing is writing the file at that moment.
            log.warning("messages.db not openable read-only (%s); falling back to immutable mode", e)
            conn = sqlite3.connect(f"file:{self.path}?immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch_candidates(self, high_water: str | None) -> list[Message]:
        """Messages ingested at or after high_water minus the re-read window, oldest first."""
        sql = f"SELECT {_COLUMNS} FROM messages m {_JOINS}"
        params: list[str] = []
        if high_water:
            sql += " WHERE m.ingested_at >= ?"
            params.append(format_ts(parse_ts(high_water) - REREAD_WINDOW))
        sql += " ORDER BY m.sent_at, m.message_id"
        with closing(self._connect()) as conn:
            return [_row_to_message(r) for r in conn.execute(sql, params)]

    def fetch_context(self, chat_jid: str, before: datetime, limit: int = 15) -> list[Message]:
        """The `limit` messages in a chat sent just before `before`, oldest first."""
        sql = f"""SELECT {_COLUMNS} FROM messages m {_JOINS}
                  WHERE m.chat_jid = ? AND m.sent_at < ?
                  ORDER BY m.sent_at DESC LIMIT ?"""
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, (chat_jid, format_ts(before), limit)).fetchall()
        return [_row_to_message(r) for r in reversed(rows)]

    def get_messages(self, keys: list[tuple[str, str]]) -> list[Message]:
        if not keys:
            return []
        where = " OR ".join("(m.chat_jid = ? AND m.message_id = ?)" for _ in keys)
        params = [v for k in keys for v in k]
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM messages m {_JOINS} WHERE {where}", params)
            return [_row_to_message(r) for r in rows]
