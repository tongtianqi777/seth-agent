"""The extractor interface.

An extractor turns batches of new source messages into memory updates. The agent loop
owns batching, bookkeeping and transactions; an extractor only decides which messages
it cares about, how to extract from them, and how to write the result.

To add a new kind of memory (photos, personal updates, ...): subclass Extractor, give it
its own tables in memory/schema.sql and its own API routes, and register it in agent.build_agent.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any

from ..source import Message


class TransientError(Exception):
    """Extraction failed for a reason that may go away (rate limit, network, outage).
    The batch stays unprocessed and is retried on a later poll."""


class PermanentError(Exception):
    """Extraction failed for a reason retrying won't fix (bad request, refusal, bad output).
    The batch is recorded in extraction_errors and marked failed so the loop moves on."""


class Extractor(ABC):
    name: str
    batch_size: int = 20

    @abstractmethod
    def wants(self, msg: Message) -> bool:
        """Cheap pre-filter. Messages this returns False for are marked skipped."""

    def fingerprint(self, msg: Message) -> str:
        """What decides that a message needs handling again. By default that is its text changing; an extractor
        that reads something else (e.g. the files of an album that is still growing) can override this."""
        return msg.body_hash

    @abstractmethod
    def extract(self, batch: list[Message]) -> Any:
        """Do the expensive work (e.g. call Claude). Must not write to memory.
        Raise TransientError or PermanentError on failure."""

    @abstractmethod
    def apply(self, conn: sqlite3.Connection, batch: list[Message], result: Any) -> int:
        """Write the result inside the agent's transaction. Returns the number of changes made."""
