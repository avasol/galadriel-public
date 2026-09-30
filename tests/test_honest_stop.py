"""THE HONEST STOP (2026-09-30): a stop_reason the loop cannot continue from.

Regression: at 16:29:30 on 2026-09-30 a `refusal` stop mid write_file fell
through `while True`, re-called the API at once, and the half-emitted tool_use
was patched as "interrupted" -- the mind was never told it was a refusal.
Contract: refusal (or any unknown reason) -> no tool runs, no automatic retry,
history kept, the real reason named. Providers map their own safety/limit
reasons onto `refusal` / `max_tokens` instead of folding them into end_turn.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.agent import _render_honest_stop  # noqa: E402
from harness.providers import GeminiProvider  # noqa: E402
from test_max_tokens_recovery import _mk_agent, _run, _max_tokens_response  # noqa: E402
from test_openai_provider import _FakeAsyncClient, _FakeHTTPResponse, _complete, _provider  # noqa: E402


def _stopped(reason, tool_name="write_file", text=""):
    r = MagicMock(); r.stop_reason = reason
    blocks = []
    if text:
        t = MagicMock(); t.type = "text"; t.text = text
        t.model_dump = lambda **k: {"type": "text", "text": text}
        blocks.append(t)
    b = MagicMock(); b.type = "tool_use"; b.name = tool_name; b.id = "tu_1"; b.input = {}
    b.model_dump = lambda **k: {"type": "tool_use", "id": "tu_1", "name": tool_name, "input": {}}
    blocks.append(b)
    r.content = blocks
    return r


# ── the message ──────────────────────────────────────────────────────────────
def test_render_names_refusal_tool_and_keeps_partial():
    s = _render_honest_stop("refusal", "write_file", "half a thought")
    assert s.startswith("half a thought")
    assert "refusal" in s and "`write_file`" in s
    assert "not retry" in s and "intact" in s
    assert "while writing" not in _render_honest_stop("refusal")
    assert "'weird'" in _render_honest_stop("weird")


# ── the loop ─────────────────────────────────────────────────────────────────
def test_refusal_stops_once_runs_nothing_keeps_history():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[_stopped("refusal", text="Writing it now.")])
    agent._execute_tool = AsyncMock(side_effect=AssertionError("a refused call must never run"))
    before = len(agent.conversations[ch])

    res = _run(agent, ch)

    assert agent.provider.complete.call_count == 1          # no silent re-call
    assert "refusal" in res and "write_file" in res and res.startswith("Writing it now.")
    after = agent.conversations[ch]
    assert len(after) >= before
    last = after[-1]
    assert last["role"] == "assistant"
    assert all(b.get("type") == "text" for b in last["content"])   # no half tool_use left
    assert "refusal" in last["content"][0]["text"]
    agent.journal.append.assert_called()


def test_unknown_stop_reason_is_not_looped():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[_stopped("something_new")])
    res = _run(agent, ch)
    assert agent.provider.complete.call_count == 1
    assert "'something_new'" in res


def test_refusal_after_max_tokens_strips_the_nudge():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[_max_tokens_response(), _stopped("refusal")])
    _run(agent, ch)
    assert agent.provider.complete.call_count == 2
    nudges = [m for m in agent.conversations[ch] if m["role"] == "user"
              and isinstance(m["content"], str) and "[SYSTEM:OUTPUT-LIMIT]" in m["content"]]
    assert nudges == []


# ── the providers ────────────────────────────────────────────────────────────
def test_openai_content_filter_is_refusal_even_with_a_tool_call():
    resp = _FakeHTTPResponse(200, {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "run_shell", "arguments": "{}"}}]},
            "finish_reason": "content_filter"}],
        "usage": {}})
    assert _complete(_provider(resp)).stop_reason == "refusal"


def _gemini(payload):
    p = GeminiProvider(api_key="test-key")
    p._client = _FakeAsyncClient(_FakeHTTPResponse(200, payload))
    return asyncio.run(p.complete(model="gemini-2.5-flash", max_tokens=256,
                                  system=[{"type": "text", "text": "be brief"}],
                                  tools=[], messages=[{"role": "user", "content": "hi"}]))


def _cand(reason, parts=None):
    return {"candidates": [{"content": {"parts": parts or [{"text": "partial"}]},
                            "finishReason": reason}], "usageMetadata": {}}


def test_gemini_max_tokens_is_reported():
    assert _gemini(_cand("MAX_TOKENS")).stop_reason == "max_tokens"


def test_gemini_safety_is_refusal_and_beats_a_function_call():
    assert _gemini(_cand("SAFETY")).stop_reason == "refusal"
    fc = [{"functionCall": {"name": "run_shell", "args": {}}}]
    assert _gemini(_cand("SAFETY", fc)).stop_reason == "refusal"
    assert _gemini(_cand("STOP", fc)).stop_reason == "tool_use"


def test_gemini_blocked_prompt_is_refusal():
    out = _gemini({"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {}})
    assert out.stop_reason == "refusal"


def test_gemini_normal_stop_unchanged():
    assert _gemini(_cand("STOP")).stop_reason == "end_turn"
