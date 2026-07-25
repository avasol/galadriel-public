"""Compaction — provider seam guard.

/compact used to build its own AsyncAnthropic client directly (bypassing
harness/providers.py entirely), which meant a body dialed to Gemini or
Bedrock would hard-crash on /compact if ANTHROPIC_API_KEY wasn't set — a
feature unrelated to the brain the customer is actually paying to use.

Fixed: compact_conversation() takes the agent's own `provider` (an
LLMProvider instance) and calls .complete() through it, same as the main
hot path. This test guards two invariants:
  1. Default path (provider.name == "anthropic") still asks for Haiku
     explicitly — zero cost regression on the box that matters today.
  2. A non-Anthropic provider is asked with model="n/a" (ignored by those
     providers' complete()) and NEVER touches ANTHROPIC_API_KEY / os.environ
     at all — the actual bug being fixed.

The palace archive side-effect (fire-and-forget `asyncio.create_task`) is
stubbed out here — it touches the real MemPalace (ChromaDB on EBS), which
can block on the live service's lock. Compaction's own contract is that
archive failures never propagate, so stubbing it is faithful, not evasive.

Run: python -m pytest tests/test_compaction_provider_seam.py -q
(no network, no API spend — providers are fake stubs.)
"""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import harness.compaction as compaction_mod  # noqa: E402
from harness.compaction import compact_conversation  # noqa: E402

# Never touch the real palace (ChromaDB on EBS) from this test.
compaction_mod.palace.mine_batch_dir = AsyncMock(return_value=True)
compaction_mod.palace._archive_root = lambda: Path("/tmp")


def _long_tool_result_messages():
    """A history with one old (summarizable) long tool_result and enough bulk
    to clear TOOL_RESULT_FRESH_MESSAGES so it's in the summarize window."""
    long_text = "x" * 5000
    old_block = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": long_text},
        ],
    }
    filler = [{"role": "user", "content": "hi"} for _ in range(25)]
    return [old_block] + filler


class FakeAnthropicProvider:
    name = "anthropic"

    def __init__(self):
        self.calls = []

    async def complete(self, *, model, max_tokens, system, tools, messages, thinking=None):
        self.calls.append({"model": model, "system": system, "tools": tools})
        block = SimpleNamespace(text="a short summary")
        return SimpleNamespace(content=[block])


class FakeGeminiProvider:
    name = "gemini"

    def __init__(self):
        self.calls = []

    async def complete(self, *, model, max_tokens, system, tools, messages, thinking=None):
        # Real GeminiProvider ignores `model` entirely and uses its own
        # self.default_model — the stub just records what it was asked.
        self.calls.append({"model": model})
        block = SimpleNamespace(text="a short summary")
        return SimpleNamespace(content=[block])


async def _run_and_settle(messages, provider):
    result = await compact_conversation(messages, provider)
    # Let the fire-and-forget archive task (now a stubbed no-op) actually
    # run to completion instead of being cancelled mid-flight by loop close.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return result


def test_anthropic_provider_gets_haiku_explicitly():
    provider = FakeAnthropicProvider()
    messages = _long_tool_result_messages()
    result = asyncio.run(_run_and_settle(messages, provider))

    assert result["summaries_created"] == 1
    assert len(provider.calls) == 1
    assert provider.calls[0]["model"] == "claude-haiku-4-5-20251001"


def test_non_anthropic_provider_never_touches_anthropic_key():
    # The actual bug: this must work with ZERO Anthropic credential present.
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        provider = FakeGeminiProvider()
        messages = _long_tool_result_messages()
        result = asyncio.run(_run_and_settle(messages, provider))

        assert result["summaries_created"] == 1
        assert len(provider.calls) == 1
        # model is a placeholder the real GeminiProvider ignores — the point
        # is no os.environ["ANTHROPIC_API_KEY"] lookup or client construction
        # ever happens for a non-anthropic provider.
        assert provider.calls[0]["model"] == "n/a"
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_no_summarization_needed_is_a_no_op():
    provider = FakeAnthropicProvider()
    messages = [{"role": "user", "content": "hi"}]
    result = asyncio.run(compact_conversation(messages, provider))

    assert result["summaries_created"] == 0
    assert provider.calls == []


if __name__ == "__main__":
    test_anthropic_provider_gets_haiku_explicitly()
    test_non_anthropic_provider_never_touches_anthropic_key()
    test_no_summarization_needed_is_a_no_op()
    print("OK")
