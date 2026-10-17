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


def _result(r: dict[str, Any], score: float) -> dict[str, Any]:
    return {
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
    }


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
            results.append(_result(r, score))
    return {
        "query": query,
        "group": {"name": found.name, "jid": found.jid} if found else None,
        "searched": len(rows),
        "results": results,
    }


def pick_diverse(
    store: MemoryStore,
    source: MessageSource,
    embedder: Embedder,
    queries: list[str],
    group: str | None = None,
    count: int = 3,
    min_score: float | None = None,
    max_similarity: float = 0.92,
    sent_from: str | None = None,
    sent_until: str | None = None,
) -> list[dict[str, Any]]:
    """Up to `count` photos that match any of `queries`, best first, no two of them near-identical.

    A photo's score is its best score over the queries. Burst shots and the same photo sent to two groups look alike
    to the model, so a photo that is too much like one already picked is skipped. Fewer than `count` come back when
    little matches well enough; none when nothing does. `sent_from` and `sent_until` (ISO dates, both included) keep
    only photos sent on those days. Raises GroupNotFound.
    """
    found = resolve_group(source, group) if group else None
    rows = store.media_for_search(embedder.model, found.jid if found else None)
    if sent_from is not None:
        rows = [r for r in rows if r["sent_at"][:10] >= sent_from]
    if sent_until is not None:
        rows = [r for r in rows if r["sent_at"][:10] <= sent_until]
    if not rows or not queries:
        return []
    query_vectors = [embedder.embed_text(q) for q in queries]
    scored = []
    for r in rows:
        vector = array("f")
        vector.frombytes(r["embedding"])
        score = max(sum(map(mul, q, vector)) for q in query_vectors)
        if min_score is None or score >= min_score:
            scored.append((score, r, vector))
    scored.sort(key=lambda x: -x[0])
    picked: list[tuple[float, dict[str, Any], array]] = []
    for score, r, vector in scored:
        if len(picked) == count:
            break
        if any(sum(map(mul, vector, other)) > max_similarity for _, _, other in picked):
            continue
        picked.append((score, r, vector))
    return [_result(r, score) for score, r, _ in picked]
