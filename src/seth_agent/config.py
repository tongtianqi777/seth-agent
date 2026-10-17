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
    local_max_tokens: int
    batch_size: int
    api_host: str
    api_port: int
    tz: tzinfo
    volunteering: VolunteeringGroups
    media_index: bool
    media_model: str
    brand_dir: Path
    flyer_dir: Path
    chrome: str | None
    image_provider: str
    image_model: str
    image_url: str
    image_quality: str
    image_size: str
    image_api_key: str | None
    flyer_photo_group: str | None
    flyer_photo_min_score: float
    design_effort: str | None
    design_max_tokens: int

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        # "Name=usual routine; Other Name". Setting it replaces the default; set it empty for none.
        groups = env("SETH_VOLUNTEERING_GROUPS")
        volunteering = DEFAULT_VOLUNTEERING_GROUPS if groups is None else parse_groups(groups)
        provider = env("SETH_PROVIDER", "anthropic").strip().lower()
        if provider not in ("anthropic", "local"):
            raise SystemExit(f"SETH_PROVIDER must be 'anthropic' or 'local', not {provider!r}")
        image_provider = env("SETH_IMAGE_PROVIDER", "none").strip().lower()
        if image_provider not in ("none", "openai"):
            raise SystemExit(f"SETH_IMAGE_PROVIDER must be 'none' or 'openai', not {image_provider!r}")
        # The group flyer photos come from: every group by default; a group's exact name limits it to that one.
        photo_group = env("SETH_FLYER_PHOTO_GROUP", "all").strip()
        flyer_photo_group = None if photo_group.casefold() in ("", "all") else photo_group
        return cls(
            feed_url=env("SETH_FEED_URL", DEFAULT_FEED_URL),
            memory_db=Path(env("SETH_MEMORY_DB", str(REPO_ROOT / "data" / "memory.db"))),
            poll_seconds=float(env("SETH_POLL_SECONDS", "15")),
            provider=provider,
            model=env("SETH_MODEL", "claude-opus-5-5"),
            effort=env("SETH_EFFORT", "medium"),
            local_url=env("SETH_LOCAL_URL", "http://127.0.0.1:11434/v1"),
            local_model=env("SETH_LOCAL_MODEL", "gemma4:31b"),
            local_api_key=env("SETH_LOCAL_API_KEY") or None,
            local_timeout=float(env("SETH_LOCAL_TIMEOUT", "600")),
            local_max_tokens=int(env("SETH_LOCAL_MAX_TOKENS", "16000")),
            batch_size=int(env("SETH_BATCH_SIZE", "20")),
            api_host=env("SETH_API_HOST", "127.0.0.1"),
            api_port=int(env("SETH_API_PORT", "8765")),
            tz=local_tz(),
            volunteering=VolunteeringGroups(volunteering),
            media_index=env("SETH_MEDIA_INDEX", "1").strip().lower() not in ("0", "false", "off", "no", ""),
            media_model=env("SETH_MEDIA_MODEL", "openai/clip-vit-base-patch16"),
            brand_dir=Path(env("SETH_BRAND_DIR", str(REPO_ROOT / "brand"))),
            flyer_dir=Path(env("SETH_FLYER_DIR", str(REPO_ROOT / "data" / "flyers"))),
            chrome=env("SETH_CHROME") or None,
            image_provider=image_provider,
            image_model=env("SETH_IMAGE_MODEL", "gpt-image-1"),
            image_url=env("SETH_IMAGE_URL", "https://api.openai.com/v1"),
            image_quality=env("SETH_IMAGE_QUALITY", "high"),
            image_size=env("SETH_IMAGE_SIZE", "1024x1536"),
            image_api_key=env("SETH_IMAGE_API_KEY") or env("OPENAI_API_KEY") or None,
            flyer_photo_group=flyer_photo_group,
            flyer_photo_min_score=float(env("SETH_FLYER_PHOTO_MIN_SCORE", "0.25")),
            design_effort=env("SETH_DESIGN_EFFORT") or ("low" if provider == "local" else None),
            design_max_tokens=int(env("SETH_DESIGN_MAX_TOKENS", "40000" if provider == "local" else "24000")),
        )
