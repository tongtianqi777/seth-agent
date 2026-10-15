"""The poll loop: read new messages, hand them to each extractor, record what was done."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config
from .extractors.base import Extractor, PermanentError, TransientError
from .memory.store import MemoryStore, now_ts
from .source import Message, MessageSource, SourceError

log = logging.getLogger(__name__)

CURSOR_KEY = "source.cursor"
MAX_BACKOFF_SECONDS = 600


@dataclass
class PollResult:
    seen: int = 0
    skipped: int = 0
    extracted: int = 0
    failed: int = 0
    changes: int = 0
    transient_error: str | None = None
    per_extractor: dict[str, int] = field(default_factory=dict)


class Agent:
    def __init__(self, store: MemoryStore, source: MessageSource, extractors: list[Extractor]):
        self.store = store
        self.source = source
        self.extractors = extractors

    def poll_once(self) -> PollResult:
        result = PollResult()
        cursor = self.store.get_state(CURSOR_KEY)
        try:
            while True:
                page = self.source.fetch_new(cursor)
                result.seen += len(page.messages)
                for ext in self.extractors:
                    try:
                        self._run_extractor(ext, page.messages, result)
                    except TransientError as e:
                        result.transient_error = f"{ext.name}: {e}"
                        log.warning("transient error in %s, will retry: %s", ext.name, e)
                # Only move the cursor once every message in the page has been handled, so a page that
                # hit a transient error is fetched again next time.
                if result.transient_error:
                    break
                cursor = page.cursor
                self.store.set_state(CURSOR_KEY, cursor)
                if not page.has_more:
                    break
        except SourceError as e:
            result.transient_error = f"source: {e}"
            log.warning("seth-feed unavailable, will retry: %s", e)
        self.store.set_state("agent.last_poll_at", now_ts())
        if result.transient_error:
            self.store.set_state("agent.last_transient_error", f"{now_ts()} {result.transient_error}")
        return result

    def _run_extractor(self, ext: Extractor, messages: list[Message], result: PollResult) -> None:
        pending = self.store.unprocessed(ext.name, messages)
        if not pending:
            return
        wanted = [m for m in pending if ext.wants(m)]
        skipped = [m for m in pending if not ext.wants(m)]
        if skipped:
            with self.store.transaction() as conn:
                self.store.mark_processed(conn, ext.name, skipped, "skipped")
            result.skipped += len(skipped)

        # Batches stay within one chat so each prompt has a single conversation's context.
        by_chat: dict[str, list[Message]] = defaultdict(list)
        for m in wanted:
            by_chat[m.chat_jid].append(m)
        for chat_messages in by_chat.values():
            for i in range(0, len(chat_messages), ext.batch_size):
                batch = chat_messages[i : i + ext.batch_size]
                try:
                    out = ext.extract(batch)
                except PermanentError as e:
                    log.error("%s failed on %d messages: %s", ext.name, len(batch), e)
                    with self.store.transaction() as conn:
                        self.store.record_error(conn, ext.name, batch, str(e))
                        self.store.mark_processed(conn, ext.name, batch, "failed")
                    result.failed += len(batch)
                    continue
                with self.store.transaction() as conn:
                    changes = ext.apply(conn, batch, out)
                    self.store.mark_processed(conn, ext.name, batch, "extracted")
                result.extracted += len(batch)
                result.changes += changes
                result.per_extractor[ext.name] = result.per_extractor.get(ext.name, 0) + changes

    def run_forever(self, poll_seconds: float, stop: threading.Event) -> None:
        delay = poll_seconds
        while not stop.is_set():
            try:
                r = self.poll_once()
                if r.extracted or r.failed or r.changes:
                    log.info(
                        "poll: %d candidates, %d extracted, %d skipped, %d failed, %d memory changes",
                        r.seen, r.extracted, r.skipped, r.failed, r.changes,
                    )
                delay = min(delay * 2, MAX_BACKOFF_SECONDS) if r.transient_error else poll_seconds
            except Exception:
                log.exception("poll failed")
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)
            stop.wait(delay)


def build_agent(config: Config) -> Agent:
    from .extractors.calendar import CalendarExtractor
    from .extractors.volunteering import VolunteeringExtractor
    from .llm import build_llm

    store = MemoryStore(config.memory_db)
    source = MessageSource(config.feed_url)
    # Both read the same feed; each one only takes the groups that belong to its calendar.
    settings = dict(llm=build_llm(config), batch_size=config.batch_size, volunteering=config.volunteering)
    extractors: list[Extractor] = [
        CalendarExtractor(store, source, config.tz, **settings),
        VolunteeringExtractor(store, source, config.tz, **settings),
    ]
    return Agent(store, source, extractors)
