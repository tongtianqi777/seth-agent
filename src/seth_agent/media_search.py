"""Finding indexed photos from a description, optionally within one group.

The group is a hard filter on where the photo was sent, never part of the description: "children at a table"
limited to Preschool means photos from Preschool, ranked by how well they match the description.
"""

from __future__ import annotations

import heapq
from array import array
from operator import mul
from typing import Any

from .embeddings import Embedder
from .memory.store import MemoryStore
from .source import Group, MessageSource


class GroupNotFound(Exception):
    def __init__(self, name: str, known: list[str]):
        super().__init__(f"no group {name!r}; known groups: {', '.join(known) or 'none'}")
        self.known = known


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def _dot(query: list[float], stored: bytes) -> float:
    vector = array("f")
    vector.frombytes(stored)
    return sum(map(mul, query, vector))


def resolve_group(source: MessageSource, name: str) -> Group:
    """The group a client means, by its exact name (ignoring case and extra spaces), media folder or JID.

    Only exact matches count. "Preschool" must not quietly turn into "Preschool P.E. Teachers" (or the other
    way round), because the group decides which children's photos come back.
    """
    groups = source.list_groups()
    wanted = _norm(name)
    for g in groups:
        if wanted in (_norm(g.name), _norm(g.media_folder), _norm(g.jid)):
            return g
    raise GroupNotFound(name, [g.name for g in groups])


def search_media(
    store: MemoryStore,
    source: MessageSource,
    embedder: Embedder,
    query: str,
    group: str | None = None,
    limit: int = 20,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Photos that best match `query`, best first. Raises GroupNotFound or SourceError."""
    found = resolve_group(source, group) if group else None
    rows = store.media_for_search(embedder.model, found.jid if found else None)
    results: list[dict[str, Any]] = []
    if rows:
        query_vector = embedder.embed_text(query)
        # Vectors are unit length, so the dot product is the cosine similarity. A plain loop is plenty for a
        # school's photos (tens of thousands of vectors rank in well under a second).
        scored = ((_dot(query_vector, r["embedding"]), r) for r in rows)
        for score, r in heapq.nlargest(limit, scored, key=lambda x: x[0]):
            if min_score is not None and score < min_score:
                break
            results.append({
                "file_id": r["file_id"],
                "score": round(score, 4),
                "group": r["group_name"],
                "chat_jid": r["chat_jid"],
                "message_id": r["message_id"],
                "sender": r["sender"],
                "sent_at": r["sent_at"],
                "caption": r["caption"],
                "mime": r["mime"],
                "url": f"/media/{r['file_id']}",
            })
    return {
        "query": query,
        "group": {"name": found.name, "jid": found.jid} if found else None,
        "searched": len(rows),
        "results": results,
    }
