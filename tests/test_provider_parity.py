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
import os
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


# ─── Aedelgard relay — the body's one-key brain socket (Rung A) ───────────────
#
# The body thinks via the broker's transparent completion relay with ONLY an
# aedk-derived device token — no sk-ant- on the user's machine. These tests pin
# the contract: the provider forwards the body's own Anthropic-shaped request
# verbatim with the right auth, and rebuilds the raw relay response into a real
# anthropic.types.Message so the agent's tool cascade walks it identically to a
# direct Anthropic turn. httpx is mocked — no network.

def _aedelgard_provider_with_capture():
    """Build an AedelgardProvider whose httpx POST is captured, returning a
    canned relay response (a tool_use turn)."""
    from harness.providers import AedelgardProvider
    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"response": {
                "id": "msg_relay_1", "type": "message", "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "tu_9", "name": "run_shell",
                     "input": {"command": "date"}},
                ],
                "stop_reason": "tool_use", "stop_sequence": None,
                "usage": {"input_tokens": 42, "output_tokens": 7,
                          "cache_read_input_tokens": 1000,
                          "cache_creation_input_tokens": 0},
            }}

    async def _post(url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    prov = AedelgardProvider(
        broker_url="https://hq.aedelgard.test/",
        device_token="dev-token-abc",
        device_fingerprint="fp-xyz",
    )
    prov._client.post = _post  # type: ignore
    return prov, captured


def test_aedelgard_provider_forwards_request_and_auth():
    """The provider must POST the body's own {model,max_tokens,system,tools,
    messages} verbatim to /v1/relay/complete, with the device token + fingerprint
    as auth — the 'one key' contract."""
    prov, captured = _aedelgard_provider_with_capture()
    asyncio.run(prov.complete(
        model="claude-sonnet-4-20250514", max_tokens=4096,
        system=SYSTEM_BLOCKS, tools=TOOLS, messages=MESSAGES,
    ))
    assert captured["url"] == "https://hq.aedelgard.test/v1/relay/complete"
    assert captured["headers"]["Authorization"] == "Bearer dev-token-abc"
    assert captured["headers"]["X-Device-Fingerprint"] == "fp-xyz"
    body = captured["json"]
    assert body["model"] == "claude-sonnet-4-20250514"
    assert body["max_tokens"] == 4096
    assert body["system"] == SYSTEM_BLOCKS
    assert body["tools"] == TOOLS
    assert body["messages"] == MESSAGES


def test_aedelgard_provider_rebuilds_anthropic_tool_use():
    """The relay's raw JSON must rebuild into a real anthropic.types.Message so
    the agent loop reads stop_reason/.type/.name/.input/.id unchanged."""
    prov, _ = _aedelgard_provider_with_capture()
    resp = asyncio.run(prov.complete(
        model="m", max_tokens=10, system=SYSTEM_BLOCKS, tools=TOOLS, messages=MESSAGES,
    ))
    assert resp.stop_reason == "tool_use"
    tu = [b for b in resp.content if b.type == "tool_use"]
    assert len(tu) == 1
    assert tu[0].name == "run_shell"
    assert tu[0].input == {"command": "date"}
    assert tu[0].id == "tu_9"
    # usage() normalises identically to AnthropicProvider (cost panel parity)
    u = prov.usage(resp)
    assert u == {"input": 42, "cache_read": 1000, "cache_write": 0, "output": 7}


def test_aedelgard_provider_requires_config():
    """No broker URL / device token -> honest RuntimeError, not silent failure."""
    from harness.providers import AedelgardProvider
    import pytest as _pytest
    # clear any ambient env
    for k in ("AEDELGARD_BROKER_URL", "AEDELGARD_DEVICE_TOKEN"):
        os.environ.pop(k, None)
    with _pytest.raises(RuntimeError):
        AedelgardProvider()


# ─── The two-path switch: provider-aware boot requirements ────────────────────
#
# The architecture page promises a binary switch: which key the body carries
# decides whether we are on the wire. The body must therefore boot on the
# credential the SELECTED brain needs — and must NOT demand an ANTHROPIC_API_KEY
# from an Aedelgard-key-only or local-model body. These pin that contract.

def test_provider_requirements_anthropic_needs_claude_key():
    from harness.providers import provider_requirements
    needed, hint = provider_requirements("anthropic")
    assert "ANTHROPIC_API_KEY" in needed
    assert "operator-blind" in hint


def test_provider_requirements_aedelgard_needs_device_token_not_claude():
    from harness.providers import provider_requirements
    needed, _ = provider_requirements("aedelgard")
    assert needed == ("AEDELGARD_DEVICE_TOKEN",)
    assert "ANTHROPIC_API_KEY" not in needed  # one key, no sk-ant-


def test_provider_requirements_local_needs_nothing():
    from harness.providers import provider_requirements
    needed, hint = provider_requirements("local")
    assert needed == ()  # offline model, no cloud credential
    assert "offline" in hint


def test_provider_requirements_gemini_accepts_either_google_var():
    from harness.providers import provider_requirements
    needed, _ = provider_requirements("gemini")
    assert "GEMINI_API_KEY" in needed and "GOOGLE_API_KEY" in needed


def test_provider_requirements_reads_env_default(monkeypatch):
    from harness.providers import provider_requirements
    monkeypatch.setenv("AGENT_PROVIDER", "aedelgard")
    needed, _ = provider_requirements()  # no arg -> read env
    assert needed == ("AEDELGARD_DEVICE_TOKEN",)


# ─── Gemini provider: a second BYO brain, full tool parity (no network) ───────
#
# These pin the Anthropic<->Gemini translation. The mind (system blocks) must
# survive, tools must round-trip, and a tool_result must resolve back to its
# function NAME (Gemini needs the name; Anthropic only carries the id).

def test_gemini_tools_mapping():
    from harness.providers import _anthropic_tools_to_gemini
    tools = [{"name": "run_shell", "description": "run a command",
              "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
              "cache_control": {"type": "ephemeral"}}]
    out = _anthropic_tools_to_gemini(tools)
    assert out[0]["function_declarations"][0]["name"] == "run_shell"
    # cache_control must NOT leak into Gemini's schema
    assert "cache_control" not in out[0]["function_declarations"][0]


def test_gemini_messages_roundtrip_tool_cascade():
    from harness.providers import _anthropic_messages_to_gemini
    messages = [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "run_shell", "input": {"cmd": "ls"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "file.txt"},
        ]},
    ]
    out = _anthropic_messages_to_gemini(messages)
    # roles: user, model, user
    assert [c["role"] for c in out] == ["user", "model", "user"]
    # tool_use -> functionCall
    assert out[1]["parts"][0]["functionCall"]["name"] == "run_shell"
    # tool_result -> functionResponse WITH the resolved name (not the id)
    fr = out[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "run_shell"
    assert "file.txt" in fr["response"]["result"]


def test_gemini_system_blocks_flatten_to_instruction():
    from harness.providers import _anthropic_system_to_text
    system = [{"type": "text", "text": "You are Galadriel."},
              {"type": "text", "text": "Memory: the mind persists."}]
    txt = _anthropic_system_to_text(system)
    assert "Galadriel" in txt and "mind persists" in txt


def test_gemini_provider_requires_a_google_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from harness.providers import GeminiProvider
    import pytest
    with pytest.raises(RuntimeError):
        GeminiProvider()


def test_gemini_in_registry_and_no_longer_stub(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    from harness.providers import make_provider, GeminiProvider
    p = make_provider("gemini")
    assert isinstance(p, GeminiProvider)
    assert p.name == "gemini"
