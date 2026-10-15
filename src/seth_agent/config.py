"""Runtime settings, read from environment variables (CLI flags override some of them)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from .groups import DEFAULT_VOLUNTEERING_GROUPS, VolunteeringGroups, parse_groups

DEFAULT_FEED_URL = "http://127.0.0.1:8080"
REPO_ROOT = Path(__file__).resolve().parents[2]


def local_tz() -> tzinfo:
    """SETH_TZ if set (e.g. America/Los_Angeles), otherwise the system timezone."""
    name = os.environ.get("SETH_TZ")
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


@dataclass
class Config:
    feed_url: str
    memory_db: Path
    poll_seconds: float
    provider: str
    model: str
    effort: str
    local_url: str
    local_model: str
    local_api_key: str | None
    local_timeout: float
    batch_size: int
    api_host: str
    api_port: int
    tz: tzinfo
    volunteering: VolunteeringGroups

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        # "Name=usual routine; Other Name". Setting it replaces the default; set it empty for none.
        groups = env("SETH_VOLUNTEERING_GROUPS")
        volunteering = DEFAULT_VOLUNTEERING_GROUPS if groups is None else parse_groups(groups)
        provider = env("SETH_PROVIDER", "anthropic").strip().lower()
        if provider not in ("anthropic", "local"):
            raise SystemExit(f"SETH_PROVIDER must be 'anthropic' or 'local', not {provider!r}")
        return cls(
            feed_url=env("SETH_FEED_URL", DEFAULT_FEED_URL),
            memory_db=Path(env("SETH_MEMORY_DB", str(REPO_ROOT / "data" / "memory.db"))),
            poll_seconds=float(env("SETH_POLL_SECONDS", "15")),
            provider=provider,
            model=env("SETH_MODEL", "claude-opus-5-5"),
            effort=env("SETH_EFFORT", "medium"),
            local_url=env("SETH_LOCAL_URL", "http://127.0.0.1:11434/v1"),
            local_model=env("SETH_LOCAL_MODEL", "qwen3:30b-a3b"),
            local_api_key=env("SETH_LOCAL_API_KEY") or None,
            local_timeout=float(env("SETH_LOCAL_TIMEOUT", "600")),
            batch_size=int(env("SETH_BATCH_SIZE", "20")),
            api_host=env("SETH_API_HOST", "127.0.0.1"),
            api_port=int(env("SETH_API_PORT", "8765")),
            tz=local_tz(),
            volunteering=VolunteeringGroups(volunteering),
        )
