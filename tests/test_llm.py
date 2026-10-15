import json

import httpx
import pytest
from pydantic import BaseModel

from seth_agent.config import Config
from seth_agent.extractors.base import PermanentError, TransientError
from seth_agent.llm import AnthropicLLM, Image, LocalLLM, build_llm


class Answer(BaseModel):
    items: list[str]


def local(handler, **kw) -> tuple[LocalLLM, list[dict]]:
    seen: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped), base_url="http://local/v1")
    return LocalLLM("qwen", client=client, **kw), seen


def reply(content, finish="stop", status=200):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    return lambda request: httpx.Response(status, json=body)


def test_local_parses_and_sends_schema():
    llm, seen = local(reply('{"items": ["a", "b"]}'))
    assert llm.parse("sys", "hello", Answer).items == ["a", "b"]
    body = seen[0]
    assert body["model"] == "qwen" and body["temperature"] == 0
    assert body["response_format"]["json_schema"]["schema"]["properties"]["items"]
    assert body["messages"][0]["content"].startswith("sys") and "JSON schema" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "hello"}


def test_local_strips_reasoning_and_code_fences():
    llm, _ = local(reply('<think>hm\n{"items": []}</think>\n```json\n{"items": ["x"]}\n```'))
    assert llm.parse("s", "p", Answer).items == ["x"]


def test_local_sends_images():
    llm, seen = local(reply('{"items": []}'))
    llm.parse("s", "what is this", Answer, images=[Image("image/png", b"\x89PNG")])
    parts = seen[0]["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1] == {"type": "text", "text": "what is this"}


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_local_server_trouble_is_transient(status):
    llm, _ = local(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(TransientError):
        llm.parse("s", "p", Answer)


def test_local_server_down_is_transient():
    def down(request):
        raise httpx.ConnectError("refused")

    llm, _ = local(down)
    with pytest.raises(TransientError, match="unreachable"):
        llm.parse("s", "p", Answer)


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(400, text="bad request"),
        reply("not json"),
        reply('{"items": "wrong type"}'),
        reply('{"items": ["a"'),
        reply('{"items": ["a"]}', finish="length"),
        lambda r: httpx.Response(200, json={"oops": 1}),
    ],
)
def test_local_bad_output_is_permanent(handler):
    llm, _ = local(handler)
    with pytest.raises(PermanentError):
        llm.parse("s", "p", Answer)


def test_build_llm_picks_provider(monkeypatch):
    monkeypatch.setenv("SETH_PROVIDER", "local")
    monkeypatch.setenv("SETH_LOCAL_MODEL", "gemma")
    llm = build_llm(Config.from_env())
    assert isinstance(llm, LocalLLM) and llm.model == "gemma"

    monkeypatch.setenv("SETH_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(build_llm(Config.from_env()), AnthropicLLM)

    monkeypatch.setenv("SETH_PROVIDER", "bogus")
    with pytest.raises(SystemExit):
        Config.from_env()


def test_extractor_asks_the_llm(store, source):
    from datetime import datetime, timezone

    from conftest import CHAT, TZ
    from seth_agent.extractors.calendar import CalendarExtractor, CalendarOps
    from seth_agent.source import Message

    class Stub:
        def parse(self, system, prompt, schema, images=()):
            self.args = (system, prompt, schema)
            return CalendarOps(operations=[])

    stub = Stub()
    msg = Message(CHAT, "M1", "Alex", datetime(2026, 10, 12, 17, tzinfo=timezone.utc), "", "text", "Off on Monday")
    ext = CalendarExtractor(store, source, TZ, llm=stub)
    assert ext.extract([msg]) == CalendarOps(operations=[])
    assert stub.args[0] == ext.system_prompt and stub.args[2] is CalendarOps and "<new_messages>" in stub.args[1]
