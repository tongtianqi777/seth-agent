from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from fakefeed import CHAT, GROUP, FakeFeed
from seth_agent.memory.store import MemoryStore
from seth_agent.source import MessageSource

__all__ = ["CHAT", "GROUP", "TZ"]

TZ = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def feed() -> FakeFeed:
    return FakeFeed()


@pytest.fixture
def source(feed) -> MessageSource:
    return MessageSource("http://feed", client=feed.client)


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")
