"""Turning slide pictures into a video with ffmpeg.

Each slide drifts in slowly (a gentle zoom) and dissolves into the next. A music file, if given, plays under it,
fades in and out and stops with the last slide. ffmpeg is called directly, as Chrome is for flyers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

FPS = 30
FADE = 0.8  # seconds each dissolve takes
ZOOM = 0.04  # how much each slide grows over its time on screen


class VideoError(Exception):
    """The video couldn't be made (no ffmpeg, ffmpeg failed). Not about the request itself."""


def find_ffmpeg(configured: str | None = None) -> str:
    if configured:
        found = shutil.which(configured)
        if not found:
            raise VideoError(f"SETH_FFMPEG={configured!r} is not an executable")
        return found
    found = shutil.which("ffmpeg")
    if not found:
        raise VideoError("ffmpeg not found: install it (on a Mac, `brew install ffmpeg`), or set SETH_FFMPEG to its path")
    return found


def total_seconds(durations: Sequence[float], fade: float = FADE) -> float:
    """How long the video runs: the slides end to end, less the overlap of each dissolve."""
    return sum(durations) - fade * (len(durations) - 1)


def build_command(
    ffmpeg: str,
    slides: Sequence[Path],
    durations: Sequence[float],
    out: Path,
    music: Path | None = None,
    size: tuple[int, int] = (1920, 1080),
    fade: float = FADE,
) -> list[str]:
    """The ffmpeg command that makes the video. `slides[i]` is shown for `durations[i]` seconds."""
    if not slides or len(slides) != len(durations):
        raise VideoError("a video needs at least one slide, and a time for each")
    w, h = size
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for path, d in zip(slides, durations):
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(path)]
    parts = []
    for i, d in enumerate(durations):
        frames = max(2, round(d * FPS))
        # Drawn at twice the size before zooming, so the slow zoom doesn't stutter. The zoom is anchored at the bottom
        # edge, so the slogan and stripe stay in view and only the top margin drifts out.
        parts.append(
            f"[{i}:v]scale={w * 2}:{h * 2},zoompan=z='1+{ZOOM}*on/{frames}':x='iw/2-iw/zoom/2':y='ih-ih/zoom'"
            f":d=1:s={w}x{h}:fps={FPS},setsar=1,format=yuv420p[v{i}]"
        )
    last, elapsed = "v0", durations[0]
    for i in range(1, len(slides)):
        offset = elapsed - fade
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset:.3f}[x{i}]")
        last, elapsed = f"x{i}", elapsed + durations[i] - fade
    length = total_seconds(durations, fade)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        parts.append(
            f"[{len(slides)}:a]atrim=0:{length:.3f},loudnorm=I=-20:TP=-2:LRA=7,afade=t=in:d=2,afade=t=out:st={max(0.0, length - 3):.3f}:d=3[a]"
        )
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]"]
    if music:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", "-t", f"{length:.3f}", str(out)]
    return cmd


def make_video(
    slides: Sequence[Path],
    durations: Sequence[float],
    out: Path,
    ffmpeg: str | None = None,
    music: Path | None = None,
    size: tuple[int, int] = (1920, 1080),
) -> None:
    binary = find_ffmpeg(ffmpeg)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_command(binary, slides, durations, out, music, size)
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
    except subprocess.TimeoutExpired as e:
        raise VideoError("ffmpeg took more than 30 minutes to make the video") from e
    except OSError as e:
        raise VideoError(f"couldn't start {binary}: {e}") from e
    if done.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        raise VideoError(f"ffmpeg failed: {(done.stderr or '').strip()[-600:] or 'no message'}")
