"""Indexes the school's photos so they can be found by describing them (see media_search.py).

Each photo is downloaded from seth-feed and turned into an embedding by a local model. No text is written about
the photo and nothing leaves the machine.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Any

from ..embeddings import Embedder
from ..memory.store import MemoryStore, pack_vector
from ..source import MediaFile, Message, MessageSource, SourceError, format_ts
from .base import Extractor, PermanentError, TransientError

log = logging.getLogger(__name__)


class MediaExtractor(Extractor):
    name = "media"
    batch_size = 16  # messages per batch; each can be an album of photos

    def __init__(self, store: MemoryStore, source: MessageSource, embedder: Embedder):
        self.store = store
        self.source = source
        self.embedder = embedder

    def wants(self, msg: Message) -> bool:
        return any(f.is_photo for f in msg.files)

    def fingerprint(self, msg: Message) -> str:
        """An album arrives photo by photo, as the same message with more files each time. Its text doesn't
        change, so the files it holds are what say there is something new to index."""
        ids = ",".join(str(f.id) for f in msg.files if f.is_photo)
        return hashlib.sha256(ids.encode()).hexdigest()[:16]

    def extract(self, batch: list[Message]) -> list[dict[str, Any]]:
        todo = [(m, f) for m in batch for f in m.files if f.is_photo]
        done = self.store.indexed_media_ids(f.id for _, f in todo)
        rows: list[dict[str, Any]] = []
        # Photos still to embed, grouped by content, so a photo forwarded within the batch is embedded once.
        # The same message can also be in the batch twice (a new album, then the same album with more photos).
        fresh: dict[str, list[tuple[Message, MediaFile]]] = {}
        seen: set[int] = set()
        for m, f in todo:
            if f.id in done or f.id in seen:
                continue
            seen.add(f.id)
            # The same photo forwarded to another group has the same content, so reuse its embedding.
            known = f.sha256 and self.store.embedding_for_sha(f.sha256, self.embedder.model)
            if known:
                rows.append(self._row(m, f, known))
            else:
                fresh.setdefault(f.sha256 or f"file:{f.id}", []).append((m, f))

        downloaded: list[tuple[list[tuple[Message, MediaFile]], bytes]] = []
        for copies in fresh.values():
            for _, f in copies:  # normally the first works; a copy seth-feed lost isn't a reason to skip the photo
                try:
                    data = self.source.fetch_media(f.id)
                except SourceError as e:
                    raise TransientError(str(e)) from e
                if data is not None:
                    downloaded.append((copies, data))
                    break
                log.warning("photo %s is not available from seth-feed", f.id)

        if downloaded:
            try:
                vectors = self.embedder.embed_images([data for _, data in downloaded])
            except OSError as e:  # the model couldn't be downloaded or read; may be a network problem
                raise TransientError(f"photo model unavailable: {e}") from e
            except Exception as e:
                raise PermanentError(f"embedding failed: {e}") from e
            for (copies, _), vector in zip(downloaded, vectors):
                if vector is None:
                    log.warning("photo %s can't be read as a picture, skipping it", copies[0][1].id)
                    continue
                packed = pack_vector(vector)
                rows.extend(self._row(m, f, packed) for m, f in copies)
        return rows

    def apply(self, conn: sqlite3.Connection, batch: list[Message], result: list[dict[str, Any]]) -> int:
        return self.store.add_media(conn, result)

    def _row(self, m: Message, f: MediaFile, embedding: bytes) -> dict[str, Any]:
        return {
            "file_id": f.id,
            "chat_jid": m.chat_jid,
            "message_id": m.message_id,
            "group_name": m.group,
            "sender": m.sender,
            "sent_at": format_ts(m.sent_at),
            "mime": f.mime,
            "caption": f.caption or m.body,
            "sha256": f.sha256,
            "model": self.embedder.model,
            "embedding": embedding,
        }
