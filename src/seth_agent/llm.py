"""The language model behind the extractors: Claude through the Anthropic API, or a model on this machine.

An extractor builds a prompt and a Pydantic schema, and asks an `LLM` to fill in the schema. It doesn't know
which provider answers. Both providers raise TransientError or PermanentError the way extractors are expected to.

Images (for extractors that read photos) go in `images`. They are passed through to the model as they are.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Protocol, Sequence, TypeVar

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

from .extractors.base import PermanentError, TransientError

T = TypeVar("T", bound=BaseModel)

# Configuration problems (401/403/404) count as transient on purpose: a mistake in the setup shouldn't mark
# every message as failed. The batch waits until it is fixed.
TRANSIENT_STATUS = {401, 403, 404, 408, 409, 429}


@dataclass(frozen=True)
class Image:
    media_type: str  # e.g. "image/jpeg"
    data: bytes

    def base64(self) -> str:
        return base64.b64encode(self.data).decode()


class LLM(Protocol):
    def parse(
        self,
        system: str,
        prompt: str,
        schema: type[T],
        images: Sequence[Image] = (),
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> T:
        """Answer `prompt` with an instance of `schema`. Raises TransientError or PermanentError.

        `effort` (none, low, medium, high) says how hard to think for this call, and `max_tokens` how much it may write
        (thinking included); None uses the configured defaults."""
        ...


class AnthropicLLM:
    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        client: anthropic.Anthropic | None = None,
    ):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def parse(
        self,
        system: str,
        prompt: str,
        schema: type[T],
        images: Sequence[Image] = (),
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> T:
        content: str | list[dict] = prompt
        if images:
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.base64()}}
                for i in images
            ] + [{"type": "text", "text": prompt}]
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=max_tokens or 16000,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
                output_config={"effort": {"none": "low"}.get(effort, effort) if effort else self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.APIConnectionError, anthropic.CredentialsError) as e:
            raise TransientError(str(e)) from e
        except TypeError as e:
            # The SDK raises TypeError when no credentials are configured at all.
            if "authentication" not in str(e):
                raise
            raise TransientError("no Anthropic credentials: set ANTHROPIC_API_KEY") from e
        except anthropic.APIStatusError as e:
            if e.status_code in TRANSIENT_STATUS or e.status_code >= 500:
                raise TransientError(f"{e.status_code}: {e.message}") from e
            raise PermanentError(f"{e.status_code}: {e.message}") from e

        if response.stop_reason == "refusal":
            raise PermanentError(f"refusal: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise PermanentError("hit max_tokens before finishing")
        if response.parsed_output is None:
            raise PermanentError("no parsed output")
        return response.parsed_output


class LocalLLM:
    """A model served on this machine through an OpenAI-compatible API (Ollama, LM Studio, mlx-lm, llama.cpp).

    The schema is sent as `response_format` so the server constrains the output to it, and it is repeated in the
    system prompt for servers that ignore `response_format`. The reply is validated either way.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key: str | None = None,
        timeout: float = 600.0,
        max_tokens: int = 16000,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # A local model can take minutes on a full batch, so wait much longer than a web API would.
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def parse(
        self,
        system: str,
        prompt: str,
        schema: type[T],
        images: Sequence[Image] = (),
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> T:
        json_schema = schema.model_json_schema()
        system = (
            f"{system}\n\nReply with a single JSON object that matches this JSON schema, and nothing else:\n"
            f"{json.dumps(json_schema)}"
        )
        content: str | list[dict] = prompt
        if images:
            content = [
                {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.base64()}"}}
                for i in images
            ] + [{"type": "text", "text": prompt}]
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": max_tokens or self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema, "strict": True},
            },
        }
        if effort:  # "none" turns a reasoning model's thinking off, which a long page can't afford
            body["reasoning_effort"] = effort
        try:
            r = self.client.post("/chat/completions", json=body)
        except httpx.TransportError as e:
            raise TransientError(f"local model server unreachable ({self.client.base_url}): {e!r}") from e
        if r.status_code in TRANSIENT_STATUS or r.status_code >= 500:
            raise TransientError(f"{r.status_code}: {r.text[:300]} (is model {self.model!r} pulled and loaded?)")
        if r.status_code >= 400:
            raise PermanentError(f"{r.status_code}: {r.text[:300]}")

        try:
            choice = r.json()["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise PermanentError(f"unexpected response from the local model server: {r.text[:300]}") from e
        if choice.get("finish_reason") == "length":
            raise PermanentError("hit max_tokens before finishing")
        try:
            return schema.model_validate_json(_json_text(text))
        except ValidationError as e:
            raise PermanentError(f"the local model's output didn't match the schema: {e.errors()[:3]}") from e


def _json_text(text: str) -> str:
    """The JSON object in a model reply, without a reasoning block or a code fence around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return fenced.group(1) if fenced else text


def build_llm(config) -> LLM:
    if config.provider == "local":
        return LocalLLM(
            config.local_model,
            base_url=config.local_url,
            api_key=config.local_api_key,
            timeout=config.local_timeout,
            max_tokens=config.local_max_tokens,
        )
    return AnthropicLLM(config.model, config.effort)
