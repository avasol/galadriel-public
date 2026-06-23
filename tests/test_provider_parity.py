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


# ─── Bedrock Nova — tool parity (the next brick after the text-path proof) ────
#
# Portability was proven for the TEXT path (commit 6e6f885). The remaining brick
# named on aedelgard.com/architecture: tool-use translation for non-Claude
# brains, so an alternate model runs the full tool cascade — not just answers in
# text. These tests pin the translation contract in BOTH directions:
#   1. Anthropic tools  -> Bedrock Converse toolConfig (outbound)
#   2. Converse toolUse -> Anthropic-shaped tool_use blocks (inbound), so the
#      agent loop (response.stop_reason=="tool_use"; block.name/.input/.id)
#      walks an alternate brain's tool call UNCHANGED.
#   3. Anthropic tool_result -> Converse toolResult (history round-trip).
# boto3 is mocked — no AWS spend.

import importlib  # noqa: E402


def _mock_boto3_into(monkeymodule):
    """Insert a fake boto3 so BedrockNovaProvider.__init__ doesn't touch AWS."""
    fake = types.ModuleType("boto3")
    fake.client = MagicMock(return_value=MagicMock(name="bedrock_runtime"))
    sys.modules["boto3"] = fake
    return fake


def _nova_provider():
    _mock_boto3_into(None)
    import harness.providers as P
    importlib.reload(P)
    return P, P.BedrockNovaProvider(model_id="eu.amazon.nova-micro-v1:0",
                                    region="eu-north-1")


def test_nova_tools_translate_to_converse_toolconfig():
    """Anthropic {name,description,input_schema} -> Converse toolSpec.inputSchema.json."""
    P, prov = _nova_provider()
    cfg = P._anthropic_tools_to_converse(TOOLS)
    assert "tools" in cfg
    spec = cfg["tools"][0]["toolSpec"]
    assert spec["name"] == "t1"
    assert spec["description"] == "d"
    assert spec["inputSchema"]["json"] == {"type": "object"}
    # cache_control is an Anthropic-only marker — must NOT leak into Converse.
    assert "cache_control" not in cfg["tools"][1]["toolSpec"]


def test_nova_converse_tooluse_maps_to_anthropic_tool_use():
    """A Converse stopReason=tool_use response must surface as an
    Anthropic-shaped response: stop_reason=='tool_use' and content blocks with
    .type/.name/.input/.id — exactly what the agent loop reads."""
    P, prov = _nova_provider()
    converse_resp = {
        "output": {"message": {"content": [
            {"text": "let me check that"},
            {"toolUse": {"toolUseId": "tu_1", "name": "run_shell",
                         "input": {"command": "ls"}}},
        ]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }
    prov.client.converse = MagicMock(return_value=converse_resp)
    resp = asyncio.run(prov.complete(
        model="m", max_tokens=100, system="SOUL", tools=TOOLS,
        messages=[{"role": "user", "content": "run ls"}],
    ))
    assert resp.stop_reason == "tool_use"
    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    b = tool_blocks[0]
    assert b.name == "run_shell"
    assert b.input == {"command": "ls"}
    assert b.id == "tu_1"
    # serialization parity: the agent stores blocks via .model_dump
    assert b.model_dump() == {
        "type": "tool_use", "id": "tu_1",
        "name": "run_shell", "input": {"command": "ls"},
    }


def test_nova_tool_result_history_maps_to_converse():
    """An Anthropic tool_result user message (what the agent appends after
    running a tool) must map to a Converse toolResult content block so the
    cascade can continue on the next complete() call."""
    P, prov = _nova_provider()
    messages = [
        {"role": "user", "content": "run ls"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "run_shell",
             "input": {"command": "ls"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "file.txt"}]},
    ]
    out = P._anthropic_messages_to_bedrock(messages)
    # last message carries a toolResult bound to the same id
    tr = out[-1]["content"][0]["toolResult"]
    assert tr["toolUseId"] == "tu_1"
    assert tr["content"][0]["text"] == "file.txt"
    # the assistant tool_use turn maps to a Converse toolUse block
    tu = out[1]["content"][0]["toolUse"]
    assert tu["toolUseId"] == "tu_1"
    assert tu["name"] == "run_shell"
    assert tu["input"] == {"command": "ls"}
