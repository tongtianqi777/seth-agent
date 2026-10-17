"""Making background music on this machine with Meta's MusicGen.

A text prompt in, about half a minute of instrumental music out, made into a loop so a longer video can repeat it
without a click. Nothing is sent anywhere: the model runs here (Apple GPU, CUDA or CPU), after a one-time download.
The model's weights are licensed CC-BY-NC 4.0 (non-commercial use).

torch and transformers are the optional `media` extra and are only imported when the model is first used.
"""

from __future__ import annotations

import io
import logging
import threading
import wave

log = logging.getLogger(__name__)

DEFAULT_MODEL = "facebook/musicgen-small"
LOOP_FADE_SECONDS = 2.0
MAX_SECONDS = 30  # what MusicGen is trained for; longer drifts


class MusicGenError(Exception):
    """Music couldn't be made (the optional install is missing, the model couldn't load). Not about the request."""


def make_loopable(samples, rate: int, fade_seconds: float = LOOP_FADE_SECONDS):
    """`samples` (a numpy array of floats) as a loop: the last `fade_seconds` fade out into the start, so playing it
    over and over has no seam. It comes out `fade_seconds` shorter."""
    import numpy as np

    n = int(fade_seconds * rate)
    if len(samples) < 4 * n:
        raise MusicGenError("the music is too short to loop")
    ramp = np.linspace(0.0, 1.0, n, dtype=samples.dtype)
    loop = samples[:-n].copy()
    loop[:n] = samples[:n] * ramp + samples[-n:] * (1.0 - ramp)
    return loop


def to_wav(samples, rate: int) -> bytes:
    """16-bit mono WAV bytes from float samples between -1 and 1."""
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


class MusicGen:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._loaded: tuple | None = None
        self._lock = threading.Lock()

    def _load(self) -> tuple:
        if self._loaded is None:
            try:
                import torch
                from transformers import AutoProcessor, MusicgenConfig, MusicgenForConditionalGeneration
            except ImportError as e:
                raise MusicGenError("music needs the optional install: `pip install -e '.[media]'`") from e
            # transformers 4.57 points this class at the decoder's config, which refuses the model's own. Say it right.
            MusicgenForConditionalGeneration.config_class = MusicgenConfig
            device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
            log.info("loading %s on %s (the first time downloads it)", self.model, device)
            try:
                processor = AutoProcessor.from_pretrained(self.model)
                model = MusicgenForConditionalGeneration.from_pretrained(self.model).to(device).eval()
            except OSError as e:  # couldn't be downloaded or read
                raise MusicGenError(f"music model unavailable: {e}") from e
            self._loaded = (torch, model, processor, device)
        return self._loaded

    def generate(self, prompt: str, seconds: float = MAX_SECONDS) -> bytes:
        """A WAV loop of music for `prompt`, a little shorter than `seconds` (the loop's fade comes off the end)."""
        seconds = min(seconds, MAX_SECONDS)
        with self._lock:
            torch, model, processor, device = self._load()
            inputs = processor(text=[prompt], padding=True, return_tensors="pt").to(device)
            tokens = int(seconds * model.config.audio_encoder.frame_rate)
            with torch.inference_mode():
                audio = model.generate(**inputs, do_sample=True, guidance_scale=3.0, max_new_tokens=tokens)
            rate = model.config.audio_encoder.sampling_rate
            samples = audio[0, 0].float().cpu().numpy()
        return to_wav(make_loopable(samples, rate), rate)
