"""seth-agent command line: run | once | calendar | photos."""

from __future__ import annotations

import argparse
import logging
import threading
from datetime import date, datetime

from .config import Config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="seth-agent")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="poll seth-feed continuously and serve the HTTP API")
    sub.add_parser("once", help="process everything pending, then exit")
    cal = sub.add_parser("calendar", help="print the calendar")
    cal.add_argument("--from", dest="date_from", type=date.fromisoformat)
    cal.add_argument("--to", dest="date_to", type=date.fromisoformat)
    cal.add_argument("--all", action="store_true", help="include cancelled events")
    cal.add_argument("--group", help="print this volunteering group's calendar instead of the school calendar")
    photos = sub.add_parser("photos", help="find photos by describing them")
    photos.add_argument("query", help="what the photos show, e.g. 'children on a soccer field'")
    photos.add_argument("--group", help="only photos from this group (its exact name)")
    photos.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = Config.from_env()

    if args.command == "calendar":
        _print_calendar(config, args.date_from, args.date_to, args.all, args.group)
        return

    if args.command == "photos":
        _search_photos(config, args.query, args.group, args.limit)
        return

    from .agent import build_agent

    agent = build_agent(config)
    if args.command == "once":
        r = agent.poll_once()
        print(
            f"{r.seen} candidates: {r.extracted} extracted, {r.skipped} skipped, {r.failed} failed, "
            f"{r.changes} memory changes"
        )
        if r.transient_error:
            print(f"transient error (will retry next time): {r.transient_error}")
        return

    import uvicorn

    from .api import create_app

    stop = threading.Event()
    poller = threading.Thread(target=agent.run_forever, args=(config.poll_seconds, stop), name="poller", daemon=True)
    poller.start()
    try:
        uvicorn.run(
            create_app(agent.store, agent.source, config.tz, config.volunteering, agent.embedder),
            host=config.api_host,
            port=config.api_port,
            access_log=args.verbose,  # per-request dashboard lines are noise unless -v
        )
    finally:
        stop.set()
        poller.join(timeout=5)


def _search_photos(config: Config, query: str, group: str | None, limit: int) -> None:
    from .embeddings import build_embedder
    from .media_search import GroupNotFound, search_media
    from .memory.store import MemoryStore
    from .source import MessageSource, SourceError

    embedder = build_embedder(config)
    if embedder is None:
        raise SystemExit("photo search is off: install it with `pip install -e '.[media]'` (and leave SETH_MEDIA_INDEX on)")
    try:
        found = search_media(MemoryStore(config.memory_db), MessageSource(config.feed_url), embedder, query, group, limit)
    except (GroupNotFound, SourceError) as e:
        raise SystemExit(str(e))
    if not found["results"]:
        print(f"(no photos; {found['searched']} searched)")
        return
    for r in found["results"]:
        print(f"{r['score']:.3f}  #{r['file_id']}  {r['sent_at']}  {r['group']}  {r['caption'] or ''}".rstrip())
        print(f"       {config.feed_url.rstrip('/')}/media/{r['file_id']}")


def _print_calendar(
    config: Config, date_from: date | None, date_to: date | None, include_cancelled: bool, group: str | None
) -> None:
    from .memory.store import SCHOOL, VOLUNTEERING, MemoryStore

    store = MemoryStore(config.memory_db)
    start = date_from or datetime.now(config.tz).date()
    if group is None:
        events = store.list_events(start, date_to, include_cancelled, category=SCHOOL)
    else:
        known = list(config.volunteering) + store.calendar_names(VOLUNTEERING)
        name = next((n for n in known if n.casefold() == group.strip().casefold()), None)
        if name is None:
            raise SystemExit(f"no volunteering group {group!r}; known: {', '.join(dict.fromkeys(known)) or 'none'}")
        events = store.list_events(start, date_to, include_cancelled, category=VOLUNTEERING, calendar=name)
    if not events:
        print("(no events)")
        return
    width = max(len(e["label"]) for e in events)
    for e in events:
        extra = f" @ {e['time']}" if e["time"] else ""
        status = " [cancelled]" if e["status"] == "cancelled" else ""
        print(f"{e['label']:<{width}}  #{e['id']} {e['title']}{extra}{status}")
        if e["details"]:
            print(f"{'':<{width}}     {e['details']}")


if __name__ == "__main__":
    main()
