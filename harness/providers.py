"""The Provider Seam — separate the mind from the brain power.

Move 2 of the Aedelgard thesis. The agent's mind (soul + memory) is persistent,
portable, and ours to keep. The brain power (the model) is rented and swappable.
Everything in this harness is already model-blind EXCEPT the single call site
where it talks to the model. This module is that seam.

Design contract (do not break — caching is a paid promise):
  * AnthropicProvider is a BYTE-IDENTICAL wrapper of the original direct
    `client.messages.create(...)` call. It forwards the exact kwargs and returns
    the raw SDK response object unchanged, so every downstream consumer
    (_log_usage, _maybe_warn_context, _maybe_warn_output_ceiling, content
    serialization) sees precisely what it saw before the seam existed.
  * Selection is via AGENT_PROVIDER (default "anthropic"). The live hot path is
    unchanged unless explicitly switched.
  * Alternate providers normalise their response so the agent's downstream code
    keeps working; until one is wired end-to-end they raise NotImplementedError
    rather than pretend.

Guarded by tests/test_provider_parity.py.
"""
from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable


# Normalised usage shape — matches what _log_usage stores in agent.last_usage,
# so the /status cost panel is provider-relative, not Anthropic-shaped.
Usage = dict  # {"input": int, "cache_read": int, "cache_write": int, "output": int}


@runtime_checkable
class LLMProvider(Protocol):
    """The seam the agent calls instead of a raw vendor client."""

    async def complete(self, *, model: str, max_tokens: int,
                       system: Any, tools: Any, messages: Any) -> Any:
        """Run one completion. Returns a response object exposing at least
        `.usage`, `.content`, `.stop_reason` in the Anthropic SDK shape (the
        agent's downstream code reads those). For Anthropic this is the raw SDK
        response; alternate providers return a compatible shim."""
        ...

    def usage(self, raw: Any) -> Usage:
        """Normalise a raw response's token usage to the common Usage dict."""
        ...


class AnthropicProvider:
    """Verbatim wrapper of the original Claude call. Zero behavioural change."""

    name = "anthropic"

    def __init__(self, client=None, api_key: str | None = None):
        if client is not None:
            self.client = client
        else:
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(
                api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
            )

    async def complete(self, *, model, max_tokens, system, tools, messages):
        # IDENTICAL to the original agent.py:555 call. cache_control markers on
        # system[0] / tools[-1] / messages[-1] are passed through untouched —
        # they are attached upstream and ARE the caching contract.
        return await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {
            "input": getattr(u, "input_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "output": getattr(u, "output_tokens", 0) or 0,
        }


class _NotYetWired:
    """Base for providers whose wiring is roadmap, not code. Fails honestly
    instead of pretending to work — Discipline #2 (name the gap; don't imply
    it's closed)."""

    name = "unwired"

    async def complete(self, **_):
        raise NotImplementedError(
            f"The {self.name!r} provider is on the roadmap but not yet wired. "
            f"Only 'anthropic' is live today. Set AGENT_PROVIDER=anthropic."
        )

    def usage(self, raw) -> Usage:
        raise NotImplementedError


class GeminiProvider(_NotYetWired):
    """google-genai; context caching where it pays. Roadmap."""
    name = "gemini"


class OpenAIProvider(_NotYetWired):
    """chat.completions; automatic prompt caching. Roadmap."""
    name = "openai"


class LocalProvider(_NotYetWired):
    """Ollama / llama.cpp OpenAI-compatible. mark_cache is a no-op (context is
    free). The body's killer offline case. Roadmap."""
    name = "local"


class AedelgardProvider(_NotYetWired):
    """The body thinks via the Aedelgard broker as its provider endpoint — one
    key (aedk), no sk-ant-. The 'killer' one-key onboarding. Roadmap; depends on
    the broker exposing a completion relay. See ROADMAP_aedelgard 'THE BODY'."""
    name = "aedelgard"



# ── Bedrock Nova — portability proof (an alternate brain, mind intact) ────────
#
# This is the "Done when" item of the seam: an alternate provider answering a
# turn end-to-end with the agent's real memory + soul (the system blocks) intact.
# Scope is deliberately the TEXT path — it proves the mind is portable across
# brains. Tool-use translation to Nova's schema is a later brick (named, not
# implied — Discipline #2). With tools present and no tool support here, Nova
# simply answers in text; that is enough to prove portability.

class _NovaBlock:
    """Anthropic-SDK-shaped content block so downstream serialization
    (_serialize_content via .model_dump) and text extraction keep working."""
    def __init__(self, text):
        self.type = "text"
        self.text = text
    def model_dump(self, exclude_none=True):
        return {"type": "text", "text": self.text}


class _NovaUsage:
    def __init__(self, in_tok, out_tok):
        self.input_tokens = in_tok
        self.output_tokens = out_tok
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _NovaResponse:
    def __init__(self, text, in_tok, out_tok):
        self.content = [_NovaBlock(text)]
        self.stop_reason = "end_turn"
        self.usage = _NovaUsage(in_tok, out_tok)


def _anthropic_system_to_text(system):
    """Flatten Anthropic system blocks (list of {type,text,...}) to one string.
    This carries the SOUL + memory prefix verbatim into Nova — the mind, intact."""
    if isinstance(system, str):
        return system
    parts = []
    for b in system or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n\n".join(parts)


def _anthropic_messages_to_bedrock(messages):
    """Map Anthropic messages -> Bedrock Converse messages, text-only. Tool_use /
    tool_result blocks are flattened to readable text so a tool cascade history
    still gives Nova context (full tool parity is a later brick)."""
    out = []
    for m in messages:
        role = m["role"]
        content = m["content"]
        if isinstance(content, str):
            text = content
        else:
            chunks = []
            for blk in content:
                if not isinstance(blk, dict):
                    chunks.append(str(blk)); continue
                t = blk.get("type")
                if t == "text":
                    chunks.append(blk.get("text", ""))
                elif t == "tool_use":
                    chunks.append(f"[called tool {blk.get('name')} with {blk.get('input')}]")
                elif t == "tool_result":
                    c = blk.get("content")
                    chunks.append(f"[tool result: {c if isinstance(c,str) else c}]")
            text = "\n".join(x for x in chunks if x)
        if not text:
            text = "(empty)"
        out.append({"role": role, "content": [{"text": text}]})
    return out


class BedrockNovaProvider:
    """Amazon Bedrock Nova via the Converse API. Cheapest brain in the account —
    used to PROVE the mind is portable. Text path only (see notes above)."""

    name = "bedrock-nova"

    def __init__(self, model_id=None, region=None):
        import boto3
        self.model_id = model_id or os.environ.get(
            "BEDROCK_MODEL_ID", "eu.amazon.nova-micro-v1:0")
        self.region = region or os.environ.get("AWS_REGION", "eu-north-1")
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

    async def complete(self, *, model, max_tokens, system, tools, messages):
        import asyncio
        sys_text = _anthropic_system_to_text(system)
        bedrock_msgs = _anthropic_messages_to_bedrock(messages)
        kwargs = dict(
            modelId=self.model_id,
            messages=bedrock_msgs,
            inferenceConfig={"maxTokens": min(max_tokens, 4096), "temperature": 0.7},
        )
        if sys_text:
            kwargs["system"] = [{"text": sys_text}]
        # boto3 is sync; run it off the event loop.
        resp = await asyncio.to_thread(self.client.converse, **kwargs)
        text = resp["output"]["message"]["content"][0]["text"]
        u = resp.get("usage", {})
        return _NovaResponse(text, u.get("inputTokens", 0), u.get("outputTokens", 0))

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {"input": u.input_tokens, "cache_read": 0,
                "cache_write": 0, "output": u.output_tokens}

_REGISTRY = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "local": LocalProvider,
    "aedelgard": AedelgardProvider,
    "bedrock-nova": BedrockNovaProvider,
}


def make_provider(provider_name: str | None = None, *,
                  anthropic_client=None, api_key: str | None = None) -> LLMProvider:
    """Select a provider. Defaults to 'anthropic' so the live hot path is
    unchanged unless AGENT_PROVIDER is explicitly set to something else."""
    name = (provider_name or os.environ.get("AGENT_PROVIDER") or "anthropic").lower()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown AGENT_PROVIDER={name!r}. Known: {', '.join(_REGISTRY)}"
        )
    if cls is AnthropicProvider:
        return AnthropicProvider(client=anthropic_client, api_key=api_key)
    return cls()
