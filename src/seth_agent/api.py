"""Local HTTP JSON API over the memory."""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from .agent import CURSOR_KEY
from .memory.store import MemoryStore
from .source import MessageSource, SourceError


DASHBOARD_DIR = Path(__file__).parent / "dashboard"


def create_app(store: MemoryStore, source: MessageSource, tz: tzinfo) -> FastAPI:
    app = FastAPI(title="seth-agent memory")

    @app.get("/calendar")
    def calendar(
        date_from: date | None = Query(None, alias="from", description="YYYY-MM-DD; default today"),
        date_to: date | None = Query(None, alias="to", description="YYYY-MM-DD; default no limit"),
        include_cancelled: bool = False,
    ):
        today = datetime.now(tz).date()
        date_from = date_from or today
        events = store.list_events(date_from, date_to, include_cancelled)
        days: dict[str, list[dict]] = {}
        for e in events:
            days.setdefault(e["label"], []).append(_summary(e))
        return {
            "today": today.isoformat(),
            "from": date_from.isoformat(),
            "to": date_to.isoformat() if date_to else None,
            "days": days,
            "events": events,
        }

    @app.get("/events/{event_id}")
    def event(event_id: int):
        e = store.get_event(event_id)
        if e is None:
            raise HTTPException(404, "event not found")
        try:
            messages = {m.key: m for m in source.get_messages([(s["chat_jid"], s["message_id"]) for s in e["sources"]])}
        except SourceError as err:
            raise HTTPException(502, str(err)) from err
        for s in e["sources"]:
            m = messages.get((s["chat_jid"], s["message_id"]))
            s["message"] = (
                {"group": m.group, "sender": m.sender, "sent_at": m.sent_at.isoformat(), "type": m.message_type, "body": m.body}
                if m
                else None
            )
        return e

    @app.get("/health")
    def health():
        return {
            "last_poll_at": store.get_state("agent.last_poll_at"),
            "source_cursor": store.get_state(CURSOR_KEY),
            "last_transient_error": store.get_state("agent.last_transient_error"),
            "last_extraction_error": store.last_error(),
            "active_events": len(store.list_events()),
        }

    # The dashboard page. Mounted last so the API routes above take precedence.
    app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
    return app


def _summary(e: dict) -> dict:
    return {k: e[k] for k in ("id", "title", "details", "time", "status", "precision", "date_start", "date_end")}
