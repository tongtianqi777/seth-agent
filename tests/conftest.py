from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from seth_agent.memory.store import MemoryStore
from seth_agent.source import MessageSource

# A copy of seth-ingestion's db/schema.sql, so tests run against the real source schema.
INGESTION_SCHEMA = Path(__file__).parent / "fixtures" / "ingestion_schema.sql"
CHAT = "15555550123@s.whatsapp.net"
TZ = ZoneInfo("America/Los_Angeles")


class SourceDB:
    """Writes rows the way seth-ingestion would."""

    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(INGESTION_SCHEMA.read_text())

    def add(
        self,
        message_id: str,
        body: str | None,
        sent_at: str = "2026-10-12T16:00:00Z",
        ingested_at: str | None = None,
        message_type: str = "text",
        edit_target_id: str | None = None,
        quoted_id: str | None = None,
        media_file_count: int = 0,
    ) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                """INSERT INTO messages (chat_jid, message_id, sender, sender_jid, sender_phone, sent_at, ingested_at,
                       message_type, body, edit_target_id, quoted_id, media_file_count, source)
                   VALUES (?, ?, 'Alex Doe', ?, '15555550123', ?, ?, ?, ?, ?, ?, ?, 'live')""",
                (CHAT, message_id, CHAT, sent_at, ingested_at or sent_at, message_type, body,
                 edit_target_id, quoted_id, media_file_count),
            )

    def set_body(self, message_id: str, body: str) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE messages SET body = ? WHERE message_id = ?", (body, message_id))


@pytest.fixture
def source_db(tmp_path) -> SourceDB:
    return SourceDB(tmp_path / "messages.db")


@pytest.fixture
def source(source_db) -> MessageSource:
    return MessageSource(source_db.path)


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")
