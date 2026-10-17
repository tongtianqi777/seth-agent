"""An in-memory stand-in for seth-feed, behind an httpx client, so tests need no server.

It mimics the parts of /health, /messages, /messages/new and /messages/{chat_jid}/{message_id} that
seth-agent uses, as described in seth-ingestion's docs/CONSUMING.md.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import httpx

CHAT = "120363000000000001@g.us"
GROUP = "Preschool"


class FakeFeed:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.log: list[tuple[int, bool]] = []  # (seq, updated) in the order the feed announced them
        self.media: dict[int, bytes] = {}  # file id -> bytes served at /media/{id}
        self.down = False
        self.requests: list[str] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle), base_url="http://feed")

    # -- writing rows the way seth-ingestion would ----------------------------------------

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
        chat_jid: str = CHAT,
        group: str = GROUP,
        sender: str = "Alex Doe",
        mentions: list[dict[str, str]] | None = None,
    ) -> None:
        self.rows.append({
            "seq": len(self.rows) + 1, "chat_jid": chat_jid, "message_id": message_id, "group": group,
            "sender": sender, "sender_jid": "15555550123@s.whatsapp.net", "sender_phone": "15555550123",
            "sent_at": sent_at, "ingested_at": ingested_at or sent_at, "message_type": message_type,
            "body": body, "mentions": mentions or [], "edit_target_id": edit_target_id, "quoted_id": quoted_id,
            "media_file_count": media_file_count, "source": "live", "files": [],
        })
        self.log.append((len(self.rows), False))

    def add_file(
        self,
        message_id: str,
        file_id: int,
        data: bytes | None,
        sha256: str | None = None,
        media_type: str = "image",
        mime: str = "image/jpeg",
        chat_jid: str = CHAT,
    ) -> None:
        """A file arriving for a message: the message is announced again (updated=true), as for an album.
        `data=None` is a download that failed: the file is listed with no url."""
        row = self._row(chat_jid, message_id)
        if data is not None:
            self.media[file_id] = data
        row["files"].append({
            "id": file_id, "position": len(row["files"]), "media_type": media_type, "caption": None,
            "mime": mime, "sha256": sha256 or f"sha-{file_id}", "url": f"/media/{file_id}" if data is not None else None,
        })
        row["media_file_count"] = len(row["files"])
        self.log.append((row["seq"], True))

    def set_body(self, message_id: str, body: str) -> None:
        """An album's caption arriving later: the feed sends the message again with updated=true."""
        row = self._row(CHAT, message_id)
        row["body"] = body
        self.log.append((row["seq"], True))

    # -- the API ----------------------------------------------------------------------------

    def _row(self, chat_jid: str, message_id: str) -> dict[str, Any] | None:
        return next((r for r in self.rows if (r["chat_jid"], r["message_id"]) == (chat_jid, message_id)), None)

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        if row["message_type"] != "edit":
            edits = [r for r in self.rows if r["message_type"] == "edit"
                     and (r["chat_jid"], r["edit_target_id"]) == (row["chat_jid"], row["message_id"])]
            latest = max(edits, key=lambda r: r["sent_at"], default=None)
            out["current_body"] = latest["body"] if latest else row["body"]
            out["edited"] = latest is not None
        return out

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.raw_path.decode())
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path, q = request.url.path, dict(request.url.params)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "cursor": str(len(self.log))})
        if path == "/groups":
            names = {r["chat_jid"]: r["group"] for r in self.rows}
            return httpx.Response(200, json={"groups": [
                {"group_jid": jid, "name": name, "media_folder": name.lower().replace(" ", "-")}
                for jid, name in names.items()
            ]})
        if path.startswith("/media/"):
            data = self.media.get(int(path.rsplit("/", 1)[1]))
            if data is None:
                return httpx.Response(404, json={"error": "file not found"})
            return httpx.Response(200, content=data)
        if path == "/messages/new":
            start, limit = int(q.get("since", 0)), int(q.get("limit", 100))
            entries = self.log[start : start + limit]
            messages = []
            for seq, updated in entries:
                m = self._view(self.rows[seq - 1])
                messages.append({**m, "updated": True} if updated else m)
            end = start + len(entries)
            return httpx.Response(200, json={"cursor": str(end), "has_more": end < len(self.log), "messages": messages})
        if path == "/messages":
            rows = [r for r in self.rows if r["message_type"] != "edit"]
            if "group" in q:
                rows = [r for r in rows if q["group"] in (r["chat_jid"], r["group"])]
            if "to" in q:
                rows = [r for r in rows if r["sent_at"] < q["to"]]
            key = "sent_at" if q.get("sort") == "sent_at" else "seq"
            rows.sort(key=lambda r: r[key], reverse=q.get("order", "desc") == "desc")
            return httpx.Response(200, json={"messages": [self._view(r) for r in rows[: int(q.get("limit", 50))]]})
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "messages":
            row = self._row(unquote(parts[2]), unquote(parts[3]))
            if row is None:
                return httpx.Response(404, json={"error": "message not found"})
            return httpx.Response(200, json=self._view(row))
        return httpx.Response(404, json={"error": "not found"})
