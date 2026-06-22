"""Provider seam — parity guard.

The provider seam (Move 2: "separate the mind from the brain power") wraps the
single model call site (agent.py:555) behind an LLMProvider interface. The
AnthropicProvider MUST be a byte-identical wrapper of the original direct call:
same kwargs to messages.create, same response passed straight through. Caching
is a *paid promise* — if these kwargs drift, cache_read/cache_write can silently
regress. This test is the guard.

Run: python -m pytest tests/test_provider_parity.py -q
(no network, no API spend — the Anthropic client is mocked.)
"""
import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Make `harness` importable without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.providers import AnthropicProvider, make_provider  # noqa: E402


# A fixed transcript — the thing whose request shape must never drift.
SYSTEM_BLOCKS = [
    {"type": "text", "text": "SOUL", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "dynamic timestamp"},
]
TOOLS = [
    {"name": "t1", "description": "d", "input_schema": {"type": "object"}},
    {"name": "t2", "description": "d", "input_schema": {"type": "object"},
     "cache_control": {"type": "ephemeral"}},
]
MESSAGES = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    {"role": "user", "content": [{"type": "text", "text": "again",
                                  "cache_control": {"type": "ephemeral"}}]},
]


def _make_mock_client():
    """A stand-in AsyncAnthropic whose messages.create records its kwargs."""
    client = MagicMock()
    sentinel_response = MagicMock(name="sdk_response")
    client.messages.create = AsyncMock(return_value=sentinel_response)
    return client, sentinel_response


def test_anthropic_provider_passes_identical_kwargs():
    """AnthropicProvider.complete must forward the exact kwargs the original
    direct `self.client.messages.create(...)` call used, and return the raw
    response object unchanged."""
    client, sentinel = _make_mock_client()
    provider = AnthropicProvider(client=client)

    result = asyncio.run(provider.complete(
        model="claude-opus-4-8",
        max_tokens=8192,
        system=SYSTEM_BLOCKS,
        tools=TOOLS,
        messages=MESSAGES,
    ))

    # 1. Response passed straight through — no wrapping, no mutation.
    assert result is sentinel, "AnthropicProvider must return the raw SDK response unchanged"

    # 2. Exactly one call, with exactly the original kwargs (and no extras).
    client.messages.create.assert_awaited_once()
    _, kwargs = client.messages.create.call_args
    assert kwargs == {
        "model": "claude-opus-4-8",
        "max_tokens": 8192,
        "system": SYSTEM_BLOCKS,
        "tools": TOOLS,
        "messages": MESSAGES,
    }, f"kwargs drift would break Claude caching — got {kwargs}"


def test_cache_control_markers_preserved():
    """The provider must not strip or move cache_control markers — they ARE
    the caching contract. Verify they survive the pass-through verbatim."""
    client, _ = _make_mock_client()
    provider = AnthropicProvider(client=client)

    asyncio.run(provider.complete(
        model="m", max_tokens=1,
        system=SYSTEM_BLOCKS, tools=TOOLS, messages=MESSAGES,
    ))
    _, kwargs = client.messages.create.call_args
    # system[0], tools[-1], messages[-1].content[-1] all keep cache_control.
    assert kwargs["system"][0].get("cache_control") == {"type": "ephemeral"}
    assert kwargs["tools"][-1].get("cache_control") == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][-1].get("cache_control") == {"type": "ephemeral"}


def test_make_provider_defaults_to_anthropic():
    """Selection must default to anthropic so the live hot path is unchanged
    unless AGENT_PROVIDER is explicitly set."""
    client, _ = _make_mock_client()
    provider = make_provider(provider_name=None, anthropic_client=client)
    assert isinstance(provider, AnthropicProvider)


def test_usage_normalisation_is_identity_for_anthropic():
    """AnthropicProvider.usage(raw) must surface the same fields _log_usage
    reads today, so the cost panel does not regress."""
    client, _ = _make_mock_client()
    provider = AnthropicProvider(client=client)

    raw = types.SimpleNamespace(usage=types.SimpleNamespace(
        input_tokens=50,
        cache_read_input_tokens=5380,
        cache_creation_input_tokens=220,
        output_tokens=200,
    ))
    u = provider.usage(raw)
    assert u == {"input": 50, "cache_read": 5380, "cache_write": 220, "output": 200}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
