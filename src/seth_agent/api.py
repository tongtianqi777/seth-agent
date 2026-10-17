"""Local HTTP JSON API over the memory."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agent import CURSOR_KEY
from .embeddings import Embedder
from .groups import VolunteeringGroups
from .media_search import GroupNotFound, search_media
from .memory.store import SCHOOL, VOLUNTEERING, MemoryStore
from .source import MessageSource, SourceError


DASHBOARD_DIR = Path(__file__).parent / "dashboard"


class MediaQuery(BaseModel):
    query: str = Field(description="What the photos show, in plain words, e.g. 'children on a soccer field'")
    group: str | None = Field(None, description="Only photos from this group (its exact name); default every group")
    limit: int = Field(20, ge=1, le=100)
    min_score: float | None = Field(None, description="Drop photos that match less well than this (cosine, -1 to 1)")

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be blank")
        return v.strip()


def create_app(
    store: MemoryStore,
    source: MessageSource,
    tz: tzinfo,
    volunteering: VolunteeringGroups | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    app = FastAPI(title="seth-agent memory")
    volunteering = volunteering or VolunteeringGroups()
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Photo searches answered since the API started. Endpoints run in worker threads, hence the lock.
    searches = {"count": 0, "total_ms": 0.0, "last_ms": None, "last_at": None}
    searches_lock = threading.Lock()

    def volunteering_calendars() -> list[str]:
        """The configured volunteering groups, in order, then any others that still have events."""
        names = list(volunteering)
        return names + [n for n in store.calendar_names(VOLUNTEERING) if n not in names]

    @app.get("/calendar")
    def calendar(
        date_from: date | None = Query(None, alias="from", description="YYYY-MM-DD; default today"),
        date_to: date | None = Query(None, alias="to", description="YYYY-MM-DD; default no limit"),
        include_cancelled: bool = False,
        group: str | None = Query(None, description="A volunteering group's name; default the school calendar"),
    ):
        today = datetime.now(tz).date()
        date_from = date_from or today
        if group is None:
            events = store.list_events(date_from, date_to, include_cancelled, category=SCHOOL)
        else:
            name = next((n for n in volunteering_calendars() if n.casefold() == group.strip().casefold()), None)
            if name is None:
                raise HTTPException(404, "no such volunteering group")
            events = store.list_events(date_from, date_to, include_cancelled, category=VOLUNTEERING, calendar=name)
        days: dict[str, list[dict]] = {}
        for e in events:
            days.setdefault(e["label"], []).append(_summary(e))
        return {
            **({} if group is None else {"group": name}),
            "today": today.isoformat(),
            "from": date_from.isoformat(),
            "to": date_to.isoformat() if date_to else None,
            "days": days,
            "events": events,
        }

    @app.get("/volunteering")
    def volunteering_groups():
        return {"calendars": volunteering_calendars()}

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

    @app.post("/media/search")
    def media_search(q: MediaQuery):
        if embedder is None:
            raise HTTPException(503, "photo search is off: install it with `pip install -e '.[media]'`")
        began = time.perf_counter()
        try:
            found = search_media(store, source, embedder, q.query, q.group, q.limit, q.min_score)
            elapsed_ms = (time.perf_counter() - began) * 1000
            with searches_lock:
                searches["count"] += 1
                searches["total_ms"] += elapsed_ms
                searches["last_ms"] = round(elapsed_ms, 1)
                searches["last_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return found
        except GroupNotFound as err:
            raise HTTPException(404, str(err)) from err
        except SourceError as err:
            raise HTTPException(502, str(err)) from err

    @app.get("/media/{file_id}")
    def media_file(file_id: int):
        """An indexed photo's bytes, fetched from seth-feed. Only photos in the index are served."""
        photo = store.get_media(file_id)
        if photo is None:
            raise HTTPException(404, "photo not found")
        try:
            data = source.fetch_media(file_id)
        except SourceError as err:
            raise HTTPException(502, str(err)) from err
        if data is None:
            raise HTTPException(404, "seth-feed no longer has this photo")
        return Response(data, media_type=photo["mime"])

    @app.get("/metrics")
    def metrics():
        m = store.metrics()
        photos = m["photos"]
        with searches_lock:
            count = searches["count"]
            search_stats = {
                "count": count,
                "avg_ms": round(searches["total_ms"] / count, 1) if count else None,
                "last_ms": searches["last_ms"],
                "last_at": searches["last_at"],
            }
        model = embedder.model if embedder else None
        return {
            "started_at": started_at,
            "agent": {
                "extractors": m["extractors"],
                "failed_batches": m["failed_batches"],
                "events": m["events"],
            },
            "photos": {
                "enabled": embedder is not None,
                "model": model,
                # Photos indexed by the model in use. Photos made by another model are kept but not searched.
                "searchable": next((x["photos"] for x in photos["by_model"] if x["model"] == model), 0),
                "indexed": photos["indexed"],
                "unique_images": photos["unique_images"],
                "embedding_bytes": photos["embedding_bytes"],
                "oldest_sent_at": photos["oldest_sent_at"],
                "newest_sent_at": photos["newest_sent_at"],
                "last_indexed_at": photos["last_indexed_at"],
                "by_model": photos["by_model"],
                "by_group": photos["by_group"],
                "searches": search_stats,
            },
        }

    @app.get("/health")
    def health():
        return {
            "last_poll_at": store.get_state("agent.last_poll_at"),
            "source_cursor": store.get_state(CURSOR_KEY),
            "last_transient_error": store.get_state("agent.last_transient_error"),
            "last_extraction_error": store.last_error(),
            "active_events": len(store.list_events(category=SCHOOL)),
            "active_volunteering_events": len(store.list_events(category=VOLUNTEERING)),
            "indexed_photos": store.count_media(),
        }

    # The dashboard page. Mounted last so the API routes above take precedence.
    app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
    return app


def _summary(e: dict) -> dict:
    return {k: e[k] for k in ("id", "title", "details", "time", "status", "precision", "date_start", "date_end")}
