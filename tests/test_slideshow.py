"""Slideshow videos: the pages, the ffmpeg command, and (where Chrome and ffmpeg exist) a real video."""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from seth_agent.flyer.photos import Photo
from seth_agent.flyer.render import find_chrome
from seth_agent.llm import Image
from seth_agent.slideshow import VideoError, make_slideshow, slides
from seth_agent.slideshow.video import build_command, total_seconds

BRAND = Path(__file__).resolve().parent.parent / "brand"
SLOGAN = json.loads((BRAND / "brand.json").read_text())["slogan"]["parts"]
ADDRESS = json.loads((BRAND / "brand.json").read_text())["address"]["text"]


def jpeg(w=800, h=600):
    from PIL import Image as PILImage

    out = io.BytesIO()
    PILImage.new("RGB", (w, h), (120, 160, 90)).save(out, "JPEG")
    return Image("image/jpeg", out.getvalue())


def photo(n=1):
    return Photo(n, None, "Preschool", "2026-10-05T10:00:00", jpeg())


def test_every_page_carries_the_slogan():
    pages = [slides.title_card(BRAND, "Picnic"), slides.photo_page(BRAND, jpeg(), "Picnic"), slides.end_card(BRAND)]
    for html in pages:
        for part in SLOGAN:
            assert part.replace("&", "&amp;") in html


def test_words_from_the_person_are_escaped():
    html = slides.title_card(BRAND, "Tom & <b>Jerry</b>", "<script>x</script>")
    assert "Tom &amp; &lt;b&gt;Jerry&lt;/b&gt;" in html and "<script>" not in html
    assert "<b>" not in slides.end_card(BRAND, "<b>bye</b>")


def test_the_address_is_only_on_the_closing_card_when_asked_for():
    assert ADDRESS not in slides.end_card(BRAND)
    assert ADDRESS in slides.end_card(BRAND, address=True)
    assert ADDRESS not in slides.title_card(BRAND, "Picnic")


def test_the_closing_card_uses_the_logo_for_dark_backgrounds():
    brand = json.loads((BRAND / "brand.json").read_text())
    dark = slides.data_uri((BRAND / brand["logos"]["on_dark"]["file"]).read_bytes())
    light = slides.data_uri((BRAND / brand["logos"]["primary"]["file"]).read_bytes())
    assert dark in slides.end_card(BRAND) and light not in slides.end_card(BRAND)
    assert light in slides.title_card(BRAND, "Picnic")


def test_dissolves_overlap_so_the_video_is_shorter_than_the_slides_added_up():
    assert total_seconds([4, 5, 5, 4], fade=1) == 15
    assert total_seconds([4], fade=1) == 4


def test_the_command_dissolves_each_slide_into_the_next_at_the_right_time():
    cmd = build_command("ffmpeg", [Path("a.png"), Path("b.png"), Path("c.png")], [4, 5, 4], Path("o.mp4"), fade=1)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=1:offset=3.000" in graph  # a (4s) ends 1s into b's start
    assert "offset=7.000" in graph  # a + b - 2 fades
    assert cmd[cmd.index("-t", cmd.index("libx264")) + 1] == "11.000"
    assert "-stream_loop" not in cmd and "aac" not in cmd  # no music, no audio track


def test_music_is_looped_faded_and_cut_to_the_video():
    cmd = build_command("ffmpeg", [Path("a.png"), Path("b.png")], [4, 4], Path("o.mp4"), music=Path("m.mp3"), fade=1)
    assert cmd[cmd.index("-stream_loop") + 1] == "-1"
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[2:a]atrim=0:7.000" in graph and "afade=t=out:st=4.000" in graph
    assert "loudnorm" in graph and graph.index("loudnorm") < graph.index("afade")  # levelled, then faded
    assert "aac" in cmd and cmd[cmd.index("-ac") + 1] == "2"


def test_a_video_needs_slides():
    with pytest.raises(VideoError):
        build_command("ffmpeg", [], [], Path("o.mp4"))


def test_mistakes_are_refused_before_anything_is_drawn(tmp_path):
    out = tmp_path / "o.mp4"
    with pytest.raises(VideoError, match="title"):
        make_slideshow("  ", [photo()], out, BRAND)
    with pytest.raises(VideoError, match="at least one photo"):
        make_slideshow("Picnic", [], out, BRAND)
    with pytest.raises(VideoError, match="seconds"):
        make_slideshow("Picnic", [photo()], out, BRAND, seconds_per_photo=1)
    with pytest.raises(VideoError, match="music"):
        make_slideshow("Picnic", [photo()], out, BRAND, music=tmp_path / "missing.mp3")


def _have(tool):
    try:
        return bool(find_chrome() if tool == "chrome" else shutil.which("ffmpeg"))
    except Exception:
        return False


@pytest.mark.skipif(not (_have("chrome") and _have("ffmpeg")), reason="needs Chrome and ffmpeg")
def test_a_real_video(tmp_path):
    out = tmp_path / "show.mp4"
    result = make_slideshow("Picnic", [photo(1), photo(2)], out, BRAND, "Spring", "Thanks", True, 2)
    assert result.path == out and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name", "-of", "json", str(out)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"], stream["codec_name"]) == (1920, 1080, "h264")
    assert result.seconds == pytest.approx(4 + 2 + 2 + 4 - 3 * 0.8)
