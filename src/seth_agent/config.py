"""Runtime settings, read from environment variables (CLI flags override some of them)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_SOURCE_DB = "/Users/tnpbot/github.com/seth-ingestion/data/messages.db"
REPO_ROOT = Path(__file__).resolve().parents[2]


def local_tz() -> tzinfo:
    """SETH_TZ if set (e.g. America/Los_Angeles), otherwise the system timezone."""
    name = os.environ.get("SETH_TZ")
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


@dataclass
class Config:
    source_db: Path
    memory_db: Path
    poll_seconds: float
    model: str
    effort: str
    batch_size: int
    api_host: str
    api_port: int
    tz: tzinfo

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            source_db=Path(env("SETH_SOURCE_DB", DEFAULT_SOURCE_DB)),
            memory_db=Path(env("SETH_MEMORY_DB", str(REPO_ROOT / "data" / "memory.db"))),
            poll_seconds=float(env("SETH_POLL_SECONDS", "15")),
            model=env("SETH_MODEL", "claude-opus-5-5"),
            effort=env("SETH_EFFORT", "medium"),
            batch_size=int(env("SETH_BATCH_SIZE", "20")),
            api_host=env("SETH_API_HOST", "127.0.0.1"),
            api_port=int(env("SETH_API_PORT", "8765")),
            tz=local_tz(),
        )
