"""Slideshow videos from the dashboard: background jobs and the endpoints that start, follow and serve them."""

import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import TZ
from seth_agent.api import create_app
from seth_agent.extractors.base import TransientError
from seth_agent.flyer import FlyerSpec, PhotoError
from seth_agent.flyer.photos import Photo
from seth_agent.llm import Image
from seth_agent.music import MusicLibrary
from seth_agent.slideshow import Found, Slideshow, VideoError
from seth_agent.slideshow.jobs import FAILED, DONE, Busy, SlideshowJobs
from test_media import FakeEmbedder


def photo(n):
    return Photo(n, 0.3, "Preschool", f"2026-12-1{n}T10:00:00Z", Image("image/jpeg", b"x"))


def fake_make(prompt, out, music=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"not really a video")
    spec = FlyerSpec(title="Christmas Program", event_date=date(2026, 12, 12), at_school=True)
    found = Found(spec, ["children singing"], [photo(1), photo(2)], ("2026-12-11", "2026-12-15"), "celebratory")
    track = out.parent / "celebratory.wav" if music == "auto" else None
    note = "No music: nothing made yet" if music == "Spring Song" else None
    return found, Slideshow(out, [photo(1), photo(2)], 18.0, track, note)


def wait_for(jobs, job, status, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if job.status in status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job is still {job.status}")


@pytest.fixture
def jobs(tmp_path):
    return SlideshowJobs(fake_make, tmp_path / "videos")


def test_a_job_is_made_in_the_background_and_describes_the_result(jobs):
    job = jobs.submit("Christmas Program on 12/12 at school")
    assert job.as_dict()["video_url"] is None
    wait_for(jobs, job, {DONE})
    d = job.as_dict()
    assert d["title"] == "Christmas Program" and d["event_date"] == "2026-12-12" and d["at_school"] is True
    assert d["queries"] == ["children singing"] and d["window"] == ["2026-12-11", "2026-12-15"]
    assert [p["file_id"] for p in d["photos"]] == [1, 2] and d["seconds"] == 18.0
    assert d["video_url"] == f"/slideshows/{job.id}/video" and job.path.read_bytes() == b"not really a video"


@pytest.mark.parametrize(
    "error, text",
    [
        (PhotoError("none match"), "photos: none match"),
        (TransientError("down"), "couldn't reach the model, try again: down"),
        (VideoError("no ffmpeg"), "couldn't make the slideshow: no ffmpeg"),
        (OSError("disk full"), "couldn't read or write a file: disk full"),
        (RuntimeError("boom"), "something went wrong; the agent's log has the details"),
    ],
)
def test_a_failure_is_explained_and_does_not_stop_the_next_job(tmp_path, error, text):
    calls = []

    def make(prompt, out, music=None):
        calls.append(prompt)
        if len(calls) == 1:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"half")
            raise error
        return fake_make(prompt, out, music)

    jobs = SlideshowJobs(make, tmp_path)
    bad = wait_for(jobs, jobs.submit("one"), {FAILED})
    assert bad.error == text and bad.as_dict()["video_url"] is None and not (tmp_path / f"{bad.id}.mp4").exists()
    assert wait_for(jobs, jobs.submit("two"), {DONE}).status == DONE


def test_videos_are_made_one_at_a_time_and_too_many_waiting_is_refused(tmp_path):
    release, running = threading.Event(), []

    def make(prompt, out, music=None):
        running.append(prompt)
        assert release.wait(5)
        return fake_make(prompt, out, music)

    jobs = SlideshowJobs(make, tmp_path, max_waiting=2)
    first, second = jobs.submit("a"), jobs.submit("b")
    with pytest.raises(Busy):
        jobs.submit("c")
    wait_for(jobs, first, {"running"})
    assert running == ["a"] and second.status == "queued"  # b waits its turn
    release.set()
    wait_for(jobs, second, {DONE})
    assert jobs.submit("d")  # room again


def test_old_videos_are_deleted_with_their_jobs(tmp_path):
    jobs = SlideshowJobs(fake_make, tmp_path, keep=2)
    made = []
    for i in range(4):
        made.append(wait_for(jobs, jobs.submit(str(i)), {DONE}))
    time.sleep(0.1)
    assert [j.prompt for j in jobs.recent()] == ["3", "2"]
    assert not made[0].path.exists() and not made[1].path.exists() and made[3].path.exists()


@pytest.fixture
def tracks(tmp_path):
    library = MusicLibrary(tmp_path / "music", Path(__file__).resolve().parent.parent / "brand")
    library.directory.mkdir()
    for name in ("celebratory.wav", "Spring Song.mp3"):
        (library.directory / name).write_bytes(b"x")
    return library


@pytest.fixture
def client(store, source, jobs):
    return TestClient(create_app(store, source, TZ, embedder=FakeEmbedder(), slideshows=jobs))


def test_start_follow_play_and_download(client):
    r = client.post("/slideshows", json={"prompt": "  Christmas Program on 12/12 at school "})
    assert r.status_code == 202 and r.json()["status"] in ("queued", "running", "done")
    job_id = r.json()["id"]
    assert r.json()["prompt"] == "Christmas Program on 12/12 at school"
    end = time.time() + 5
    while client.get(f"/slideshows/{job_id}").json()["status"] != "done" and time.time() < end:
        time.sleep(0.01)
    listing = client.get("/slideshows").json()
    assert listing["enabled"] and [j["id"] for j in listing["jobs"]] == [job_id]

    played = client.get(f"/slideshows/{job_id}/video")
    assert played.status_code == 200 and played.headers["content-type"] == "video/mp4"
    assert played.content == b"not really a video" and "attachment" not in played.headers.get("content-disposition", "")
    saved = client.get(f"/slideshows/{job_id}/video", params={"download": "true"})
    assert saved.headers["content-disposition"] == 'attachment; filename="christmas-program.mp4"'


def test_a_video_that_isnt_ready_or_doesnt_exist(store, source, tmp_path):
    release = threading.Event()

    def slow(prompt, out, music=None):
        release.wait(5)
        return fake_make(prompt, out, music)

    client = TestClient(create_app(store, source, TZ, embedder=FakeEmbedder(), slideshows=SlideshowJobs(slow, tmp_path)))
    job_id = client.post("/slideshows", json={"prompt": "Picnic"}).json()["id"]
    assert client.get(f"/slideshows/{job_id}/video").status_code == 409
    assert client.get("/slideshows/nope").status_code == 404 and client.get("/slideshows/nope/video").status_code == 404
    release.set()


def test_the_requests_are_checked(client, store, source, tmp_path):
    assert client.post("/slideshows", json={"prompt": "   "}).status_code == 422
    assert client.post("/slideshows", json={"prompt": "x" * 301}).status_code == 422
    assert client.post("/slideshows", json={}).status_code == 422

    release = threading.Event()
    jobs = SlideshowJobs(lambda p, o, m=None: (release.wait(5), fake_make(p, o, m))[1], tmp_path, max_waiting=1)
    busy = TestClient(create_app(store, source, TZ, embedder=FakeEmbedder(), slideshows=jobs))
    assert busy.post("/slideshows", json={"prompt": "a"}).status_code == 202
    assert busy.post("/slideshows", json={"prompt": "b"}).status_code == 429
    release.set()


def test_it_says_when_slideshows_are_unavailable(store, source, jobs):
    off = TestClient(create_app(store, source, TZ, embedder=FakeEmbedder()))
    assert off.get("/slideshows").json()["enabled"] is False
    assert off.post("/slideshows", json={"prompt": "Picnic"}).status_code == 503

    no_search = TestClient(create_app(store, source, TZ, slideshows=jobs))
    listing = no_search.get("/slideshows").json()
    assert listing["enabled"] is False and "photo search" in listing["reason"]
    assert no_search.post("/slideshows", json={"prompt": "Picnic"}).status_code == 503


def test_the_dashboard_has_the_slideshow_form(client):
    page = client.get("/").text
    assert "Generate slideshow video" in page and 'id="show-prompt"' in page and "Download video" in page


def test_the_music_asked_for_is_passed_on_and_reported(store, source, tmp_path, tracks):
    jobs = SlideshowJobs(fake_make, tmp_path / "videos", music=tracks)
    client = TestClient(create_app(store, source, TZ, embedder=FakeEmbedder(), slideshows=jobs))
    listing = client.get("/slideshows").json()["music"]
    assert listing["tracks"] == ["Spring Song", "celebratory"] and set(listing["moods"]) == {"cheerful", "celebratory", "calm"}
    assert listing["generate"] is False  # this server was set up without a music generator

    def run(**body):
        r = client.post("/slideshows", json={"prompt": "Picnic", **body})
        assert r.status_code == 202, r.text
        return wait_for(jobs, jobs.get(r.json()["id"]), {DONE}).as_dict()

    auto = run(music="auto")
    assert auto["music"] == "auto" and auto["music_track"] == "celebratory"
    assert run()["music"] == "auto" and run(music="")["music"] == "auto"  # music is on unless asked to be off
    off = run(music="none")
    assert off["music"] == "none" and off["music_track"] is None
    named = run(music="spring song")
    assert named["music"] == "Spring Song"  # the track's own spelling
    assert named["music_note"] == "No music: nothing made yet"  # what the fake says about that one


def test_only_a_track_in_the_folder_can_be_asked_for(store, source, tmp_path, tracks):
    jobs = SlideshowJobs(fake_make, tmp_path / "videos", music=tracks)
    client = TestClient(create_app(store, source, TZ, embedder=FakeEmbedder(), slideshows=jobs))
    for bad in ("polka", "/etc/passwd", "../music/celebratory.wav", "celebratory.wav"):
        r = client.post("/slideshows", json={"prompt": "Picnic", "music": bad})
        assert r.status_code == 422 and "Spring Song, celebratory" in r.json()["detail"], bad
    assert client.get("/slideshows").json()["jobs"] == []  # nothing was started


def test_a_music_error_is_explained(tmp_path):
    from seth_agent.music import MusicError

    def make(prompt, out, music=None):
        raise MusicError("no track 'polka'")

    jobs = SlideshowJobs(make, tmp_path)
    assert wait_for(jobs, jobs.submit("x", "polka"), {FAILED}).error == "music: no track 'polka'"


def test_the_dashboard_has_the_music_choice(client):
    page = client.get("/").text
    assert 'id="show-music"' in page and "Automatic (suits the event)" in page and "No music" in page
