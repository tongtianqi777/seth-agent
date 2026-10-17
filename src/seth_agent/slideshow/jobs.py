"""Slideshow videos made in the background, for the dashboard.

A video takes a minute or more (the model, Chrome for every page, then ffmpeg), far too long to answer a web request.
So a request becomes a job: it is queued, made one at a time (each one keeps Chrome and ffmpeg busy), and the page
asks how it is getting on until the video is ready to watch or download. Jobs live in memory and the newest few are
kept; a video's file is deleted with its job.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..extractors.base import PermanentError, TransientError
from ..flyer import FlyerError, PhotoError
from . import Found, Slideshow, VideoError

log = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

Maker = Callable[[str, Path], tuple[Found, Slideshow]]  # (the request, where to save the MP4)


class Busy(Exception):
    """Too many videos are waiting already."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(e: Exception) -> str:
    """What went wrong, in words for the person who asked."""
    if isinstance(e, PhotoError):
        return f"photos: {e}"
    if isinstance(e, TransientError):
        return f"couldn't reach the model, try again: {e}"
    if isinstance(e, (PermanentError, VideoError, FlyerError)):
        return f"couldn't make the slideshow: {e}"
    if isinstance(e, OSError):
        return f"couldn't read or write a file: {e}"
    return "something went wrong; the agent's log has the details"


@dataclass
class Job:
    id: str
    prompt: str
    created_at: str = field(default_factory=now)
    status: str = QUEUED
    finished_at: str | None = None
    found: Found | None = None
    show: Slideshow | None = None
    error: str | None = None
    path: Path | None = None
    number: int = 0  # the order jobs were asked for in

    def as_dict(self) -> dict:
        f, show = self.found, self.show
        spec = f.spec if f else None
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "title": spec.title if spec else None,
            "event_date": spec.event_date.isoformat() if spec and spec.event_date else None,
            "at_school": spec.at_school if spec else None,
            "queries": f.queries if f else [],
            "window": list(f.window) if f and f.window else None,
            "photos": [{"file_id": p.file_id, "group": p.group, "sent_at": p.sent_at} for p in f.photos] if f else [],
            "seconds": round(show.seconds, 1) if show else None,
            "video_url": f"/slideshows/{self.id}/video" if self.status == DONE else None,
        }


class SlideshowJobs:
    def __init__(self, make: Maker, directory: Path, keep: int = 10, max_waiting: int = 3):
        self.make, self.directory, self.keep, self.max_waiting = make, Path(directory), keep, max_waiting
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[Job] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._count = 0

    def submit(self, prompt: str) -> Job:
        """Queue a video. Raises Busy when `max_waiting` are already waiting or being made."""
        with self._lock:
            if sum(j.status in (QUEUED, RUNNING) for j in self._jobs.values()) >= self.max_waiting:
                raise Busy(f"{self.max_waiting} videos are already being made; wait for one to finish")
            self._count += 1
            job = Job(uuid.uuid4().hex[:12], prompt, number=self._count)
            self._jobs[job.id] = job
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name="slideshows", daemon=True)
                self._worker.start()
        self._queue.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self) -> list[Job]:
        """Newest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.number, reverse=True)

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            with self._lock:
                job.status = RUNNING
            path = self.directory / f"{job.id}.mp4"
            try:
                found, show = self.make(job.prompt, path)
            except Exception as e:  # a job must never take the worker down
                path.unlink(missing_ok=True)  # no half-made video
                if not isinstance(e, (PhotoError, TransientError, PermanentError, VideoError, FlyerError, OSError)):
                    log.exception("slideshow %s failed", job.id)
                with self._lock:
                    job.status, job.error, job.finished_at = FAILED, describe(e), now()
            else:
                with self._lock:
                    job.found, job.show, job.path = found, show, show.path
                    job.status, job.finished_at = DONE, now()
            self._prune()

    def _prune(self) -> None:
        with self._lock:
            finished = sorted((j for j in self._jobs.values() if j.status in (DONE, FAILED)), key=lambda j: j.number)
            old = finished[: max(0, len(finished) - self.keep)]
            for j in old:
                del self._jobs[j.id]
        for j in old:
            if j.path:
                j.path.unlink(missing_ok=True)
