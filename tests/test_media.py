"""Photo search: indexing photos from the feed, and finding them by description within a group."""

import pytest

from fastapi.testclient import TestClient

from conftest import CHAT, TZ
from seth_agent.agent import Agent
from seth_agent.api import create_app
from seth_agent.extractors.media import MediaExtractor
from seth_agent.memory.store import pack_vector

PE_CHAT = "120363000000000002@g.us"
PE = "Preschool P.E. Teachers"
WORDS = ("soccer", "table", "eating", "playground")


class FakeEmbedder:
    """Stands in for CLIP. A "photo" is bytes of words such as b"soccer field"; its vector counts the
    words it knows, so a query is closest to the photos that mention the same words."""

    model = "fake-clip"

    def __init__(self):
        self.embedded = 0  # photos that were actually embedded (not reused)

    def _vec(self, text: str) -> list[float]:
        v = [float(text.count(w)) for w in WORDS]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_images(self, images):
        self.embedded += len(images)
        return [None if not b.strip() else self._vec(b.decode()) for b in images]

    def embed_text(self, text):
        return self._vec(text)


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def agent(store, source, embedder):
    return Agent(store, source, [MediaExtractor(store, source, embedder)], embedder)


@pytest.fixture
def client(agent, store, source, embedder):
    return TestClient(create_app(store, source, TZ, embedder=embedder))


def photo(feed, message_id, file_id, data, group="Preschool", chat_jid=CHAT, **kw):
    feed.add(message_id, None, message_type="image", chat_jid=chat_jid, group=group)
    feed.add_file(message_id, file_id, data, chat_jid=chat_jid, **kw)


def search(client, **body):
    r = client.post("/media/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_group_is_a_filter_and_no_group_searches_everywhere(feed, agent, client):
    photo(feed, "A", 1, b"soccer soccer field", group="Preschool")
    photo(feed, "B", 2, b"soccer field", group=PE, chat_jid=PE_CHAT)
    photo(feed, "C", 3, b"table table", group="Preschool")
    photo(feed, "D", 4, b"eating eating at table", group=PE, chat_jid=PE_CHAT)
    agent.poll_once()

    r = search(client, query="children on a soccer field", group="Preschool")
    assert [x["file_id"] for x in r["results"]][0] == 1
    assert {x["group"] for x in r["results"]} == {"Preschool"}  # the P.E. photo of soccer is not included
    assert r["group"]["jid"] == CHAT and r["searched"] == 2

    r = search(client, query="children sitting at table", group="Preschool")
    assert r["results"][0]["file_id"] == 3

    r = search(client, query="children eating meals")  # no group: any group
    assert r["results"][0]["file_id"] == 4 and r["group"] is None and r["searched"] == 4


def test_group_must_match_exactly_ignoring_case_and_spaces(feed, agent, client):
    photo(feed, "A", 1, b"soccer", group="Preschool")
    photo(feed, "B", 2, b"soccer", group=PE, chat_jid=PE_CHAT)
    agent.poll_once()

    assert search(client, query="soccer", group="  preschool ")["group"]["name"] == "Preschool"
    assert search(client, query="soccer", group="preschool p.e.  teachers")["group"]["name"] == PE
    assert search(client, query="soccer", group=PE_CHAT)["group"]["name"] == PE  # the JID works too
    r = client.post("/media/search", json={"query": "soccer", "group": "Pre"})
    assert r.status_code == 404 and "Preschool" in r.json()["detail"]


def test_limit_and_min_score(feed, agent, client):
    for i in range(1, 6):
        photo(feed, f"M{i}", i, b"soccer" if i <= 2 else b"table")
    agent.poll_once()
    assert len(search(client, query="soccer", limit=3)["results"]) == 3
    # photos about something else score 0 against "soccer"
    assert [x["file_id"] for x in search(client, query="soccer", min_score=0.5)["results"]] == [1, 2]


def test_album_photos_that_arrive_later_are_indexed(feed, agent, client, embedder):
    feed.add("ALB", "Sports day", message_type="album")
    feed.add_file("ALB", 10, b"soccer")
    agent.poll_once()
    assert search(client, query="soccer")["searched"] == 1
    feed.add_file("ALB", 11, b"playground")  # the same message again, with a second photo
    agent.poll_once()
    r = search(client, query="playground")
    assert r["searched"] == 2 and r["results"][0]["file_id"] == 11
    assert embedder.embedded == 2  # the first photo wasn't embedded again
    assert r["results"][0]["caption"] == "Sports day"  # an album's caption comes from the message


def test_a_forwarded_photo_is_embedded_once_but_found_in_both_groups(feed, agent, client, embedder):
    photo(feed, "A", 1, b"soccer", group="Preschool", sha256="same")
    photo(feed, "B", 2, b"soccer", group=PE, chat_jid=PE_CHAT, sha256="same")
    agent.poll_once()
    assert embedder.embedded == 1
    assert [x["file_id"] for x in search(client, query="soccer", group=PE)["results"]] == [2]


def test_files_that_cannot_be_indexed_are_skipped_without_blocking_the_rest(feed, agent, client):
    photo(feed, "A", 1, None)  # the download failed in seth-ingestion: no url
    photo(feed, "B", 2, b"   ")  # not a picture
    photo(feed, "V", 3, b"soccer", media_type="video", mime="video/mp4")
    photo(feed, "S", 4, b"soccer", media_type="sticker", mime="image/webp")
    photo(feed, "OK", 5, b"soccer")
    feed.media.pop(5)  # in the feed's list, but gone by the time it is fetched
    photo(feed, "OK2", 6, b"soccer")
    r = agent.poll_once()
    assert r.failed == 0 and r.transient_error is None
    assert [x["file_id"] for x in search(client, query="soccer")["results"]] == [6]


def test_an_unreachable_feed_is_retried_not_lost(feed, agent, client):
    photo(feed, "A", 1, b"soccer")
    ext = agent.extractors[0]
    real_fetch = agent.source.fetch_media

    def down(file_id):
        feed.down = True
        return real_fetch(file_id)

    agent.source.fetch_media = down
    assert agent.poll_once().transient_error
    feed.down = False
    agent.source.fetch_media = real_fetch
    agent.poll_once()
    assert [x["file_id"] for x in search(client, query="soccer")["results"]] == [1]


def test_searching_with_nothing_indexed_returns_no_results(client, feed):
    feed.add("T", "hello")
    assert search(client, query="soccer")["results"] == []
    assert search(client, query="soccer", group="Preschool")["results"] == []


def test_blank_query_is_rejected(client):
    assert client.post("/media/search", json={"query": "   "}).status_code == 422
    assert client.post("/media/search", json={"query": "x", "limit": 0}).status_code == 422


def test_photo_search_is_503_when_off(source, store, feed):
    assert TestClient(create_app(store, source, TZ)).post("/media/search", json={"query": "x"}).status_code == 503


def test_indexed_photos_can_be_fetched_and_nothing_else(feed, agent, client):
    photo(feed, "A", 1, b"soccer")
    feed.media[99] = b"secret"  # exists in the feed but was never indexed
    agent.poll_once()
    r = client.get("/media/1")
    assert r.status_code == 200 and r.content == b"soccer" and r.headers["content-type"] == "image/jpeg"
    assert client.get("/media/99").status_code == 404
    assert client.get("/health").json()["indexed_photos"] == 1


def test_embeddings_from_another_model_are_not_searched(feed, agent, client, store):
    photo(feed, "A", 1, b"soccer")
    agent.poll_once()
    with store.transaction() as conn:
        conn.execute("UPDATE media_files SET model = 'older-model'")
    assert search(client, query="soccer")["results"] == []


def test_metrics_describe_the_photo_index_and_searches(feed, agent, client):
    photo(feed, "A", 1, b"soccer", sha256="same")
    photo(feed, "B", 2, b"soccer", group=PE, chat_jid=PE_CHAT, sha256="same")  # a forward
    photo(feed, "C", 3, b"table")
    agent.poll_once()
    search(client, query="soccer")
    search(client, query="table", group="Preschool")
    m = client.get("/metrics").json()["photos"]
    assert m["enabled"] and m["model"] == "fake-clip"
    assert (m["indexed"], m["unique_images"], m["searchable"]) == (3, 2, 3)
    assert m["embedding_bytes"] == 3 * 4 * len(WORDS)
    assert m["by_model"] == [{"model": "fake-clip", "photos": 3, "dimensions": len(WORDS)}]
    assert m["by_group"] == [{"group": "Preschool", "photos": 2}, {"group": PE, "photos": 1}]
    assert m["newest_sent_at"] and m["last_indexed_at"]
    assert m["searches"]["count"] == 2 and m["searches"]["avg_ms"] >= 0 and m["searches"]["last_at"]
    assert client.get("/metrics").json()["agent"]["extractors"]["media"] == {"extracted": 3}


def test_photos_from_another_model_are_indexed_but_not_searchable(feed, agent, client, store):
    photo(feed, "A", 1, b"soccer")
    agent.poll_once()
    with store.transaction() as conn:
        conn.execute("UPDATE media_files SET model = 'older-model'")
    m = client.get("/metrics").json()["photos"]
    assert (m["indexed"], m["searchable"]) == (1, 0)


def test_vectors_round_trip_through_the_store():
    from array import array

    v = [0.5, -0.25, 0.125]
    back = array("f")
    back.frombytes(pack_vector(v))
    assert back.tolist() == v
