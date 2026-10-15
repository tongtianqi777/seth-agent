"""Read-only access to the school's WhatsApp messages through seth-feed, seth-ingestion's HTTP API.

See /Users/tnpbot/github.com/seth-ingestion/docs/CONSUMING.md for the API. This module only issues GET requests.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

PAGE_SIZE = 500  # the most /messages/new returns per call


class SourceError(Exception):
    """seth-feed is unreachable or answered with an error. Retrying later may work."""


@dataclass(frozen=True)
class Message:
    chat_jid: str  # the group's JID
    message_id: str
    sender: str
    sent_at: datetime  # aware, UTC
    ingested_at: str  # RFC3339 UTC string, as given
    message_type: str
    body: str | None  # latest text (edits applied); for an `edit` row, the new text
    edit_target_id: str | None = None
    quoted_id: str | None = None
    media_file_count: int = 0
    edit_target_body: str | None = None
    quoted_body: str | None = None
    group: str = ""  # the group's name

    @property
    def key(self) -> tuple[str, str]:
        return (self.chat_jid, self.message_id)

    @property
    def body_hash(self) -> str:
        return hashlib.sha256((self.body or "").encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Page:
    messages: list[Message]
    cursor: str
    has_more: bool


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(raw: dict[str, Any]) -> str | None:
    """The text people see: the latest edit for ordinary messages, the new text for an edit row."""
    if raw["message_type"] == "edit":
        return raw.get("body")
    return raw.get("current_body", raw.get("body"))


class MessageSource:
    def __init__(self, base_url: str, client: httpx.Client | None = None, page_size: int = PAGE_SIZE):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.client = client or httpx.Client(base_url=self.base_url, timeout=30)

    def _get(self, path: str, params: dict[str, Any] | None = None, allow_404: bool = False) -> dict[str, Any] | None:
        try:
            resp = self.client.get(path, params=params)
        except httpx.HTTPError as e:
            raise SourceError(f"seth-feed unreachable at {self.base_url}: {e}") from e
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code != 200:
            raise SourceError(f"seth-feed {path} returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # -- reading ------------------------------------------------------------------

    def current_cursor(self) -> str:
        """The cursor for "everything so far"."""
        return self._get("/health")["cursor"]  # type: ignore[index]

    def fetch_new(self, cursor: str | None) -> Page:
        """Up to one page of messages recorded after `cursor` (None = from the start), oldest first.

        Edits arrive as their own `edit` rows, and an album that gained photos comes again, so a
        message can appear more than once over time.
        """
        params: dict[str, Any] = {"limit": self.page_size}
        if cursor:
            params["since"] = cursor
        data = self._get("/messages/new", params)
        assert data is not None
        # Arrival order isn't sending order (history can arrive after newer live messages); sort is stable.
        messages = sorted(self._to_messages(data["messages"]), key=lambda m: m.sent_at)
        return Page(messages, data["cursor"], data["has_more"])

    def fetch_context(self, chat_jid: str, before: datetime, limit: int = 15) -> list[Message]:
        """The `limit` messages in a group sent just before `before`, oldest first."""
        params = {"group": chat_jid, "to": format_ts(before), "sort": "sent_at", "order": "desc", "limit": limit}
        data = self._get("/messages", params)
        assert data is not None
        return self._to_messages(list(reversed(data["messages"])))

    def get_messages(self, keys: list[tuple[str, str]]) -> list[Message]:
        """The messages with these (chat_jid, message_id) keys. Ones that don't exist are left out."""
        raws = [r for r in (self._get_raw(k) for k in dict.fromkeys(keys)) if r]
        return self._to_messages(raws)

    def _get_raw(self, key: tuple[str, str]) -> dict[str, Any] | None:
        chat_jid, message_id = key
        return self._get(f"/messages/{quote(chat_jid, safe='')}/{quote(message_id, safe='')}", allow_404=True)

    # -- conversion -------------------------------------------------------------------

    def _to_messages(self, raws: list[dict[str, Any]]) -> list[Message]:
        """Builds Messages, filling in the text an edit changed and the text a reply quotes."""
        known = {(r["chat_jid"], r["message_id"]): r for r in raws}
        fetched: dict[tuple[str, str], dict[str, Any] | None] = {}

        def lookup(chat_jid: str, message_id: str | None) -> dict[str, Any] | None:
            if not message_id:
                return None
            key = (chat_jid, message_id)
            if key in known:
                return known[key]
            if key not in fetched:
                fetched[key] = self._get_raw(key)
            return fetched[key]

        out = []
        for r in raws:
            original = lookup(r["chat_jid"], r.get("edit_target_id")) if r["message_type"] == "edit" else None
            quoted = lookup(r["chat_jid"], r.get("quoted_id"))
            out.append(
                Message(
                    chat_jid=r["chat_jid"],
                    message_id=r["message_id"],
                    sender=r.get("sender") or r.get("sender_name") or "",
                    sent_at=parse_ts(r["sent_at"]),
                    ingested_at=r["ingested_at"],
                    message_type=r["message_type"],
                    body=_text(r),
                    edit_target_id=r.get("edit_target_id"),
                    quoted_id=r.get("quoted_id"),
                    media_file_count=r.get("media_file_count", 0),
                    # The original as first sent: its current_body already includes this very edit.
                    edit_target_body=original.get("body") if original else None,
                    quoted_body=_text(quoted) if quoted else None,
                    group=r.get("group") or "",
                )
            )
        return out
