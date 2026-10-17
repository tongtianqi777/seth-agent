"""seth-agent command line: run | once | calendar | photos | flyer | slideshow | music."""

from __future__ import annotations

import argparse
import logging
import threading
from datetime import date, datetime
from pathlib import Path

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
    flyer = sub.add_parser("flyer", help="make a flyer in the school's branding from a sentence")
    flyer.add_argument("prompt", help="what the flyer is for, e.g. 'Open House on 11/7/2026 at 10am - 12pm'")
    flyer.add_argument("-o", "--output", type=Path, help="where to save the PNG; default data/flyers/")
    art = flyer.add_mutually_exclusive_group()
    art.add_argument("--no-art", action="store_true", help="no artwork, even if an image provider is set up")
    art.add_argument("--art-file", type=Path, help="use this picture as the background instead of generating one")
    photos = flyer.add_mutually_exclusive_group()
    photos.add_argument("--no-photos", action="store_true", help="no photos on this flyer")
    photos.add_argument(
        "--photo", dest="photo_ids", type=int, action="append", default=[], metavar="ID",
        help="use this photo from the library (a file id; repeat for more) instead of searching",
    )  # fmt: skip
    flyer.add_argument("--photos", type=int, default=3, choices=range(1, 7), metavar="N", help="how many photos to look for (1-6, default 3)")
    flyer.add_argument("--photo-group", help="take photos from this group ('all' for every group); default SETH_FLYER_PHOTO_GROUP")
    flyer.add_argument("--layout", help="use a fixed page design from brand/templates ('event', 'event-collage') instead of letting the model design the page")
    flyer.add_argument("--design-file", type=Path, help="use a page you designed (HTML with tokens like {{TITLE}}; see src/seth_agent/flyer/design.py)")
    flyer.add_argument("--idea", help="steer the design, e.g. 'like a vintage poster' or 'lots of balloons'")
    address = flyer.add_mutually_exclusive_group()
    address.add_argument("--address", dest="address", action="store_true", default=None, help="print the school's address")
    address.add_argument("--no-address", dest="address", action="store_false", help="leave the school's address off")
    show = sub.add_parser("slideshow", help="make a slideshow video, in the school's branding, from photos you choose or find")
    show.add_argument("title", help="with --photo, the video's title, e.g. 'Christmas Program 2026'; with --find, the event in a sentence, e.g. 'Christmas Program on 12/12/2026 at school'")
    chosen = show.add_mutually_exclusive_group(required=True)
    chosen.add_argument("--photo", dest="photo_ids", type=int, action="append", metavar="ID", help="a photo to include (its id from the dashboard's Media tab); repeat for each, in the order to show them")
    chosen.add_argument("--find", action="store_true", help="find the event's photos in the library from the sentence, around the date it gives")
    show.add_argument("--count", type=int, default=12, choices=range(2, 41), metavar="N", help="with --find, how many photos to look for (2-40, default 12)")
    show.add_argument("--photo-group", help="with --find, take photos from this group ('all' for every group); default SETH_FLYER_PHOTO_GROUP")
    show.add_argument("-o", "--output", type=Path, help="where to save the MP4; default data/slideshows/")
    show.add_argument("--subtitle", help="a line under the title, e.g. the date: 'December 12, 2026' (with --find, the date from the sentence is used if you don't give one)")
    show.add_argument("--end-text", help="a closing line on the last card, e.g. 'Thank you for a wonderful year!'")
    address = show.add_mutually_exclusive_group()
    address.add_argument("--address", dest="address", action="store_true", default=None, help="print the school's address on the last card (with --find, that is decided from the sentence unless you say)")
    address.add_argument("--no-address", dest="address", action="store_false", help="leave the school's address off")
    show.add_argument("--seconds", type=float, default=5.0, help="seconds each photo is shown (2-20, default 5)")
    sound = show.add_mutually_exclusive_group()
    sound.add_argument("--music", help="music under the video: 'auto' (the default with --find) for music that suits the event, made on this computer the first time it is needed; the name of a track in the music folder (see `seth-agent music list`); or the path to an audio file. With --photo there is no music unless you name it")
    sound.add_argument("--no-music", action="store_true", help="no music, even with --find")
    music = sub.add_parser("music", help="make background music for slideshow videos, or list what there is")
    music.add_argument("action", choices=["generate", "list"], help="generate: make a track for each mood that has none yet (on this machine, the first time downloads the model); list: show the tracks and moods")
    music.add_argument("moods", nargs="*", help="with generate, only these moods (default: every mood without a track)")
    music.add_argument("--force", action="store_true", help="with generate, make a new take of a mood that already has a track, replacing it")
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

    if args.command == "flyer":
        _make_flyer(config, args)
        return

    if args.command == "slideshow":
        _make_slideshow(config, args)
        return

    if args.command == "music":
        _music(config, args)
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
            create_app(agent.store, agent.source, config.tz, config.volunteering, agent.embedder, _slideshow_jobs(config, agent)),
            host=config.api_host,
            port=config.api_port,
            access_log=args.verbose,  # per-request dashboard lines are noise unless -v
        )
    finally:
        stop.set()
        poller.join(timeout=5)


def _make_flyer(config: Config, args: argparse.Namespace) -> None:
    import re

    from .extractors.base import PermanentError, TransientError
    from .flyer import ArtError, FlyerError, PhotoError, make_flyer
    from .flyer.design import DesignError
    from .flyer.art import build_image_gen
    from .flyer.template import available_layouts, format_date, format_times
    from .llm import build_llm

    prompt, output, art_file = args.prompt, args.output, args.art_file
    if args.layout and args.layout not in available_layouts(config.brand_dir):
        raise SystemExit(f"no layout {args.layout!r}; choose one of: {', '.join(available_layouts(config.brand_dir))}")
    today = datetime.now(config.tz).date()
    if output is None:
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "flyer"
        output = config.flyer_dir / f"{today.isoformat()}-{slug}.png"
    try:
        image_gen = None if args.no_art or art_file else build_image_gen(config)
        library = None if args.no_photos else _photo_library(config, args.photo_group, searching=not args.photo_ids)
        flyer = make_flyer(
            build_llm(config), prompt, output, config.brand_dir, today, config.chrome, image_gen, art_file,
            args.address, library, args.photos, args.photo_ids, args.layout, args.idea, None, config.design_effort, args.design_file, config.design_max_tokens,
        )  # fmt: skip
    except ArtError as e:
        raise SystemExit(f"artwork: {e}")
    except PhotoError as e:
        raise SystemExit(f"photos: {e}")
    except DesignError as e:
        raise SystemExit(f"the design breaks the rules: {e}")
    except OSError as e:
        raise SystemExit(f"couldn't read or write a file: {e}")
    except TransientError as e:
        raise SystemExit(f"couldn't reach the model, try again: {e}")
    except (PermanentError, FlyerError) as e:
        raise SystemExit(f"couldn't make the flyer: {e}")
    # Say what was understood, so a misread date or time is caught before the flyer is shared.
    spec = flyer.spec
    read = [spec.title, spec.subtitle, spec.event_date and format_date(spec.event_date)]
    read += [format_times(spec.start_time, spec.end_time), spec.location, spec.at_school and "school address", spec.details]
    print("Read from your request: " + " | ".join(x for x in read if x))
    if flyer.photo_error:
        print(f"No photos this time ({flyer.photo_error}).")
    elif not flyer.photos and not args.no_photos:
        print("No photos: none suit this flyer (or nothing in the library matches well enough).")
    for p in flyer.photos:
        how = f"score {p.score:.2f}" if p.score is not None else "chosen by you"
        print(f"Photo #{p.file_id}: {p.group}, {p.sent_at[:10]} ({how})")
    if flyer.photos and not args.photo_ids:
        print("  Don't like one? Swap one with --photo ID (find ids in the dashboard's Media tab), or use --no-photos.")
    if flyer.design_error:
        print(f"The model's design wasn't used ({flyer.design_error}); the standard layout took its place.")
    elif flyer.design == "hand-made":
        print("Used your design file.")
    elif flyer.design == "creative":
        print("Designed from scratch. Looks it was pointed at: " + " / ".join(i.split(":")[0] for i in flyer.ideas))
    if flyer.art_error:
        print(f"No artwork this time ({flyer.art_error}). The flyer is plain; try again for artwork.")
    elif flyer.art_path and not art_file:
        print(f"Artwork: {flyer.art_path}  (reuse it with --art-file)")
    print(flyer.path)


def _slideshow_jobs(config: Config, agent):
    """The dashboard's slideshow videos: made in the background, one at a time, from a sentence about the event."""
    from .flyer import PhotoLibrary
    from .llm import build_llm
    from .music import MusicChoice, MusicLibrary
    from .music.musicgen import MusicGen
    from .slideshow import make_from_request
    from .slideshow.jobs import SlideshowJobs

    llm = build_llm(config)
    library = PhotoLibrary(
        agent.store, agent.source, agent.embedder, config.flyer_photo_group, config.flyer_photo_min_score, max_side=1920
    )

    tracks = MusicLibrary(config.music_dir, config.brand_dir)
    generator = MusicGen(config.music_model)  # loads on first use

    def make(prompt: str, out: Path, music: str | None):
        today = datetime.now(config.tz).date()
        choice = MusicChoice(tracks, music, generator=generator)
        return make_from_request(llm, library, prompt, config.brand_dir, today, out, music=choice, chrome=config.chrome, ffmpeg=config.ffmpeg)

    return SlideshowJobs(make, config.slideshow_dir, music=tracks, can_generate_music=True)


def _make_slideshow(config: Config, args: argparse.Namespace) -> None:
    import re

    from .extractors.base import PermanentError, TransientError
    from .flyer import FlyerError, PhotoError
    from .flyer.template import format_date
    from .llm import build_llm
    from .music import AUTO, NONE, MusicChoice, MusicError, MusicLibrary
    from .music.musicgen import MusicGen
    from .slideshow import VideoError, make_from_request, make_slideshow

    today = datetime.now(config.tz).date()
    found = None
    wanted = NONE if args.no_music else args.music or (AUTO if args.find else NONE)  # music is on with --find unless turned off
    music = MusicChoice(MusicLibrary(config.music_dir, config.brand_dir), wanted, True, MusicGen(config.music_model))
    if music.moods() and not args.find:
        raise SystemExit("--music auto reads the mood from the event, so it needs --find; name a track instead (`seth-agent music list`)")

    def default_output(title: str) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "slideshow"
        return args.output or config.slideshow_dir / f"{today.isoformat()}-{slug}.mp4"

    try:
        # Video frames are bigger than a flyer's photos, so the photos are kept at full screen size.
        library = _photo_library(config, args.photo_group, searching=args.find, max_side=1920)
        if args.find:
            found, show = make_from_request(
                build_llm(config), library, args.title, config.brand_dir, today, default_output, args.count,
                args.subtitle, args.end_text, args.address, args.seconds, music, config.chrome, config.ffmpeg,
            )  # fmt: skip
        else:
            track = music.pick()
            show = make_slideshow(
                args.title, library.get(args.photo_ids), default_output(args.title), config.brand_dir, args.subtitle,
                args.end_text, bool(args.address), args.seconds, track.path, config.chrome, config.ffmpeg,
            )  # fmt: skip
    except MusicError as e:
        raise SystemExit(f"music: {e}")
    except PhotoError as e:
        raise SystemExit(f"photos: {e}")
    except OSError as e:
        raise SystemExit(f"couldn't read or write a file: {e}")
    except TransientError as e:
        raise SystemExit(f"couldn't reach the model, try again: {e}")
    except (PermanentError, VideoError, FlyerError) as e:
        raise SystemExit(f"couldn't make the slideshow: {e}")
    if found:  # say what was understood, so a misread date or a wrong search is caught before the video is shared
        spec = found.spec
        read = [spec.title, spec.event_date and format_date(spec.event_date), spec.at_school and "school address"]
        print("Read from your request: " + " | ".join(x for x in read if x))
        print("Looked for: " + " / ".join(found.queries))
        if found.window:
            print(f"Photos sent {found.window[0]} to {found.window[1]}.")
        if len(found.photos) < args.count:
            print(f"Only {len(found.photos)} of the {args.count} you asked for matched well enough.")
    for p in show.photos:
        how = f", score {p.score:.2f}" if p.score is not None else ""
        print(f"Photo #{p.file_id}: {p.group}, {p.sent_at[:10]}{how}")
    if found:
        print("  Don't like one? Leave it out by listing the photos you want with --photo ID, in order.")
    if show.music:
        print(f"Music: {show.music.stem}" + (f" (the {found.mood} mood suits this event)" if found and found.mood else ""))
    elif show.music_note:
        print(show.music_note)
    print(f"{len(show.photos)} photos, {show.seconds:.0f} seconds. Watch it before you share it: the photos show children.")
    print(show.path)


def _music(config: Config, args: argparse.Namespace) -> None:
    from .music import MusicError, MusicLibrary
    from .music.musicgen import MusicGen, MusicGenError

    library = MusicLibrary(config.music_dir, config.brand_dir)
    if args.action == "list":
        tracks = library.tracks()
        print(f"Tracks in {config.music_dir}: " + (", ".join(tracks) if tracks else "none yet"))
        for mood, use in library.moods().items():
            print(f"  {mood}: {use}" + ("" if mood in library.missing() else "  [made]"))
        if library.missing():
            print("Make the missing moods with `seth-agent music generate`.")
        return
    moods = args.moods or (list(library.moods()) if args.force else library.missing())
    if not moods:
        print("Every mood already has a track. Use --force to make a new take of one.")
        return
    generator = MusicGen(config.music_model)
    for mood in moods:
        print(f"Making {mood} music (under a minute; the first time also downloads the model)...", flush=True)
        try:
            path = library.generate(generator, mood, args.force)
        except (MusicError, MusicGenError) as e:
            raise SystemExit(f"music: {e}")
        print(f"  {path}")
    print("Listen to them before the school uses them. `seth-agent slideshow ... --music auto` picks one to suit the event.")


def _photo_library(config: Config, group: str | None, searching: bool, max_side: int | None = None):
    from .embeddings import build_embedder
    from .flyer import PhotoLibrary
    from .memory.store import MemoryStore
    from .source import MessageSource

    if group is None:
        group = config.flyer_photo_group
    elif group.casefold() == "all":
        group = None
    return PhotoLibrary(
        MemoryStore(config.memory_db),
        MessageSource(config.feed_url),
        build_embedder(config) if searching else None,
        group,
        config.flyer_photo_min_score,
        **({"max_side": max_side} if max_side else {}),
    )


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
