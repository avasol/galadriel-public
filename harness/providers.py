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


_REGISTRY = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "local": LocalProvider,
    "aedelgard": AedelgardProvider,
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
