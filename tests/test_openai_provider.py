"""THE THIRD BRAIN — offline contract tests for the OpenAI dialect.

Everything here runs without a network: httpx is replaced by a fake client.
The sharp edges under guard:
  * tool-call IDs round-trip VERBATIM (orphan-repair depends on it),
  * malformed argument JSON degrades to {"_raw": ...}, never a crash,
  * 401 -> ProviderAuthError (terminal), other statuses carry .status_code
    for _is_fallback_worthy,
  * usage normalisation: cached_tokens -> cache_read, input excludes the
    cached share, cache_write = 0 (automatic caching has no billed write),
  * prompt_cache_key pins the stable prefix (api.openai.com only),
  * a caller-named OpenAI model is honoured; an Anthropic id is not,
  * reasoning_effort is gated to reasoning families on api.openai.com,
  * the fallback ladder accepts and forwards `thinking=` (regression).
"""
import asyncio
import json

import pytest

from harness.providers import (
    FallbackProvider,
    LocalProvider,
    OpenAIProvider,
    ProviderAuthError,
    _Rung,
    _anthropic_messages_to_openai,
    _anthropic_tools_to_openai,
    _is_fallback_worthy,
)


# ── translation: tools ───────────────────────────────────────────────────

def test_tools_translation_strips_cache_control():
    tools = [{"name": "run_shell", "description": "shell",
              "input_schema": {"type": "object",
                               "properties": {"command": {"type": "string"}}},
              "cache_control": {"type": "ephemeral"}}]
    out = _anthropic_tools_to_openai(tools)
    assert out == [{"type": "function", "function": {
        "name": "run_shell", "description": "shell",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}}}}}]


def test_tools_translation_skips_malformed_entries():
    assert _anthropic_tools_to_openai([{"description": "no name"}, "junk", None]) == []


# ── translation: messages ────────────────────────────────────────────────

def test_plain_string_messages_pass_through():
    out = _anthropic_messages_to_openai([{"role": "user", "content": "hi"}])
    assert out == [{"role": "user", "content": "hi"}]


def test_tool_use_and_result_ids_round_trip_verbatim():
    msgs = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "running"},
            {"type": "tool_use", "id": "toolu_ABC", "name": "run_shell",
             "input": {"command": "ls"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_ABC",
             "content": "file.txt"},
            {"type": "text", "text": "thanks"}]},
    ]
    out = _anthropic_messages_to_openai(msgs)
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == "running"
    assert out[0]["tool_calls"][0]["id"] == "toolu_ABC"
    assert json.loads(out[0]["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}
    # tool result precedes the user's text, as its own 'tool' message
    assert out[1] == {"role": "tool", "tool_call_id": "toolu_ABC", "content": "file.txt"}
    assert out[2] == {"role": "user", "content": "thanks"}


def test_tool_result_block_content_flattens_to_text():
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "text", "text": "a"},
            {"type": "image", "source": {"type": "base64", "data": "x"}},
            {"type": "text", "text": "b"}]}]}]
    out = _anthropic_messages_to_openai(msgs)
    assert out[0]["role"] == "tool"
    assert out[0]["content"].startswith("a\n[image omitted")
    assert out[0]["content"].endswith("\nb")


def test_image_blocks_become_data_uris():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "AAAA"}}]}]
    out = _anthropic_messages_to_openai(msgs)
    parts = out[0]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_assistant_tool_use_only_has_null_content():
    msgs = [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t9", "name": "f", "input": {}}]}]
    out = _anthropic_messages_to_openai(msgs)
    assert out[0]["content"] is None
    assert out[0]["tool_calls"][0]["id"] == "t9"


# ── the fake wire ────────────────────────────────────────────────────────

class _FakeHTTPResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


class _FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.last_url = None
        self.last_json = None
        self.last_headers = None

    async def post(self, url, json=None, headers=None):
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        return self.response

    async def get(self, url, headers=None):
        self.last_url = url
        self.last_headers = headers
        return self.response


def _provider(response, **kw):
    kw.setdefault("api_key", "sk-test")
    kw.setdefault("model", "test-model")
    p = OpenAIProvider(**kw)
    p._client = _FakeAsyncClient(response)
    return p


def _ok(text="ok"):
    return _FakeHTTPResponse(200, {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}})


def _complete(p, **kw):
    kw.setdefault("model", "claude-sonnet-5")
    kw.setdefault("max_tokens", 512)
    kw.setdefault("system", [{"type": "text", "text": "be brief"}])
    kw.setdefault("tools", [])
    kw.setdefault("messages", [{"role": "user", "content": "hi"}])
    return asyncio.run(p.complete(**kw))


# ── complete(): the shim ─────────────────────────────────────────────────

def test_text_response_shim():
    p = _provider(_FakeHTTPResponse(200, {
        "choices": [{"message": {"content": "hello there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}}))
    out = _complete(p)
    assert out.stop_reason == "end_turn"
    assert out.content[0].type == "text"
    assert out.content[0].text == "hello there"
    assert p.usage(out) == {"input": 10, "cache_read": 0, "cache_write": 0, "output": 3}
    assert p._client.last_json["messages"][0] == {"role": "system", "content": "be brief"}
    assert p._client.last_headers["Authorization"] == "Bearer sk-test"


def test_tool_call_response_and_cached_tokens_split_from_input():
    p = _provider(_FakeHTTPResponse(200, {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "call_XYZ", "type": "function",
             "function": {"name": "run_shell", "arguments": "{\"command\": \"ls\"}"}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 9,
                  "prompt_tokens_details": {"cached_tokens": 80}}}))
    out = _complete(p)
    assert out.stop_reason == "tool_use"
    blk = out.content[0]
    assert blk.type == "tool_use"
    assert blk.id == "call_XYZ"                # verbatim, feeds tool_result
    assert blk.input == {"command": "ls"}
    # prompt_tokens (100) includes the 80 cached: report 20 fresh + 80 read,
    # the Anthropic-shaped split the cost panel expects.
    assert p.usage(out) == {"input": 20, "cache_read": 80, "cache_write": 0, "output": 9}


def test_malformed_arguments_degrade_not_crash():
    out = _complete(_provider(_FakeHTTPResponse(200, {
        "choices": [{"message": {"tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "f", "arguments": "{not json"}}]},
            "finish_reason": "tool_calls"}],
        "usage": {}})))
    assert out.content[0].input == {"_raw": "{not json"}


def test_length_finish_reason_maps_to_max_tokens():
    out = _complete(_provider(_FakeHTTPResponse(200, {
        "choices": [{"message": {"content": "trunc"}, "finish_reason": "length"}],
        "usage": {}})))
    assert out.stop_reason == "max_tokens"


def test_401_raises_provider_auth_error_and_is_not_fallback_worthy():
    with pytest.raises(ProviderAuthError) as ei:
        _complete(_provider(_FakeHTTPResponse(401, text="no")))
    assert _is_fallback_worthy(ei.value) is False


def test_5xx_carries_status_code_for_the_ladder():
    with pytest.raises(RuntimeError) as ei:
        _complete(_provider(_FakeHTTPResponse(503, text="down")))
    assert getattr(ei.value, "status_code", None) == 503
    assert _is_fallback_worthy(ei.value) is True


# ── model selection: honour ours, ignore theirs ──────────────────────────

def test_anthropic_id_falls_back_to_default_model():
    p = _provider(_ok(), model="gpt-4o-mini")
    _complete(p, model="claude-sonnet-5")
    assert p._client.last_json["model"] == "gpt-4o-mini"


def test_caller_named_openai_model_is_honoured():
    p = _provider(_ok(), model="gpt-4o-mini")
    _complete(p, model="gpt-5.6-terra")
    assert p._client.last_json["model"] == "gpt-5.6-terra"


def test_compatible_server_honours_any_caller_model():
    p = _provider(_ok(), base_url="http://localhost:11434/v1", model="llama3")
    _complete(p, model="qwen2.5-coder")
    assert p._client.last_json["model"] == "qwen2.5-coder"


# ── automatic caching hints ──────────────────────────────────────────────

def test_prompt_cache_key_is_stable_and_openai_only():
    p = _provider(_ok())
    _complete(p)
    k1 = p._client.last_json["prompt_cache_key"]
    _complete(p)
    assert p._client.last_json["prompt_cache_key"] == k1
    _complete(p, system=[{"type": "text", "text": "different soul"}])
    assert p._client.last_json["prompt_cache_key"] != k1

    q = _provider(_ok(), base_url="http://localhost:11434/v1", model="llama3")
    _complete(q)
    assert "prompt_cache_key" not in q._client.last_json


def test_max_tokens_param_by_endpoint():
    p = _provider(_ok())
    _complete(p)
    assert "max_completion_tokens" in p._client.last_json
    q = _provider(_ok(), base_url="https://openrouter.ai/api/v1", model="x")
    _complete(q)
    assert "max_tokens" in q._client.last_json


# ── reasoning_effort, gated honestly ─────────────────────────────────────

def test_reasoning_effort_sent_for_reasoning_families():
    p = _provider(_ok(), model="gpt-5.6-sol")
    _complete(p, thinking={"type": "enabled", "budget_tokens": 16000})
    assert p._client.last_json["reasoning_effort"] == "high"


def test_tools_plus_reasoning_family_sends_effort_none():
    # Live finding 2026-09-07: chat.completions refuses function tools on the
    # reasoning families unless reasoning_effort is explicitly "none".
    p = _provider(_ok(), model="gpt-5.6-luna")
    tools = [{"name": "f", "description": "d", "input_schema": {"type": "object"}}]
    _complete(p, tools=tools, thinking={"type": "enabled", "budget_tokens": 16000})
    assert p._client.last_json["reasoning_effort"] == "none"
    _complete(p, tools=tools, thinking=None)
    assert p._client.last_json["reasoning_effort"] == "none"
    # a non-reasoning model with tools: field stays absent
    q = _provider(_ok(), model="gpt-4o-mini")
    _complete(q, tools=tools, thinking={"type": "enabled", "budget_tokens": 16000})
    assert "reasoning_effort" not in q._client.last_json


def test_unknown_model_learns_no_reasoning_with_tools_from_the_400():
    class _Wire(_FakeAsyncClient):
        def __init__(self):
            super().__init__(None)
            self.calls = []

        async def post(self, url, json=None, headers=None):
            self.calls.append(dict(json))
            if json.get("reasoning_effort") != "none":
                return _FakeHTTPResponse(400, text="Function tools with reasoning_effort "
                                                    "are not supported for gpt-9-nova")
            return _ok()

    p = OpenAIProvider(api_key="sk-test", model="gpt-9-nova")
    p._client = _Wire()
    tools = [{"name": "f", "description": "d", "input_schema": {"type": "object"}}]
    # "gpt-9" is not in the prefix table -> first call sends no field, the API
    # says no, the provider learns and retries with "none" in the same turn
    out = _complete(p, tools=tools, thinking={"type": "enabled", "budget_tokens": 16000})
    assert out.stop_reason == "end_turn"
    assert [c.get("reasoning_effort") for c in p._client.calls] == [None, "none"]
    assert "gpt-9-nova" in p._no_reasoning_with_tools
    # second turn: remembered, one call only
    p._client.calls.clear()
    _complete(p, tools=tools, thinking={"type": "enabled", "budget_tokens": 16000})
    assert [c.get("reasoning_effort") for c in p._client.calls] == ["none"]


def test_reasoning_effort_uses_the_model_actually_sent():
    p = _provider(_ok(), model="gpt-4o-mini")
    _complete(p, model="o4-mini", thinking={"type": "enabled", "budget_tokens": 1000})
    assert p._client.last_json["reasoning_effort"] == "low"


def test_reasoning_effort_absent_when_thinking_falsy_or_non_reasoning():
    p = _provider(_ok(), model="gpt-5.6-sol")
    _complete(p, thinking=None)
    assert "reasoning_effort" not in p._client.last_json
    q = _provider(_ok(), model="gpt-4o-mini")
    _complete(q, thinking={"type": "enabled", "budget_tokens": 16000})
    assert "reasoning_effort" not in q._client.last_json


def test_reasoning_effort_never_sent_to_compatible_servers():
    p = _provider(_ok(), base_url="https://api.groq.com/openai/v1", model="o3-ish")
    _complete(p, thinking={"type": "enabled", "budget_tokens": 16000})
    assert "reasoning_effort" not in p._client.last_json


def test_effort_from_budget_tiers():
    f = OpenAIProvider._effort_from_budget
    assert f(None) is None and f(0) is None
    assert f(1024) == "low" and f(2048) == "low"
    assert f(4096) == "medium" and f(8192) == "medium"
    assert f(8193) == "high"


# ── list_models: the /model dial ─────────────────────────────────────────

def test_list_models_filters_non_brains_on_openai_com():
    p = _provider(_FakeHTTPResponse(200, {"data": [
        {"id": "gpt-5.6-terra", "created": 1800000000},
        {"id": "text-embedding-3-large", "created": 1700000000},
        {"id": "gpt-4o-mini-tts", "created": 1750000000},
        {"id": "o4-mini", "created": 1790000000},
        {"id": "whisper-1", "created": 1600000000},
        {"id": "gpt-image-1", "created": 1760000000},
    ]}))
    out = asyncio.run(p.list_models())
    assert [m["id"] for m in out] == ["gpt-5.6-terra", "o4-mini"]
    assert p._client.last_url.endswith("/models")


def test_list_models_unfiltered_on_compatible_servers():
    p = _provider(_FakeHTTPResponse(200, {"data": [{"id": "llama3"}, {"id": "qwen2.5"}]}),
                  base_url="http://localhost:11434/v1", model="llama3")
    assert {m["id"] for m in asyncio.run(p.list_models())} == {"llama3", "qwen2.5"}


def test_list_models_401_is_terminal():
    p = _provider(_FakeHTTPResponse(401, text="no"))
    with pytest.raises(ProviderAuthError):
        asyncio.run(p.list_models())


# ── construction contracts ───────────────────────────────────────────────

def test_openai_default_base_needs_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        OpenAIProvider()


def test_local_is_keyless_but_needs_model(monkeypatch):
    for v in ("OPENAI_API_KEY", "OPENAI_MODEL", "LOCAL_MODEL", "LOCAL_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RuntimeError):
        LocalProvider()
    monkeypatch.setenv("LOCAL_MODEL", "llama3")
    p = LocalProvider()
    assert p.base_url == "http://localhost:11434/v1"
    assert p.default_model == "llama3"
    assert p._max_tokens_param() == "max_tokens"


# ── the ladder forwards thinking (regression) ────────────────────────────

def test_fallback_ladder_accepts_and_forwards_thinking():
    seen = {}

    class P:
        name = "p"

        async def complete(self, **kw):
            seen.update(kw)
            return "raw"

        def usage(self, raw):
            return {}

    fp = FallbackProvider([_Rung("p", lambda: P(), None)])
    th = {"type": "enabled", "budget_tokens": 1024}
    raw = asyncio.run(fp.complete(model="m", max_tokens=1, system=[], tools=[],
                                  messages=[], thinking=th))
    assert raw == "raw"
    assert seen["thinking"] == th
