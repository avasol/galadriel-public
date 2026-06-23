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


class AedelgardProvider:
    """The body thinks via the Aedelgard broker as its provider endpoint — one
    key (aedk), no sk-ant-. The 'killer' one-key onboarding.

    The body owns its soul + memory locally and assembles its OWN Anthropic-
    shaped request (system, tools, messages). This provider forwards that
    request verbatim to the broker's transparent completion relay
    (POST /v1/relay/complete), authenticating with the device token minted from
    the aedk. The broker injects the metered Claude key server-side and returns
    the raw Anthropic response, which we rebuild into a real anthropic.types
    Message — so the agent's tool cascade (stop_reason/.type/.name/.input/.id)
    walks a relayed turn IDENTICALLY to a direct Anthropic turn.

    Config (env):
      AEDELGARD_BROKER_URL    — broker base, e.g. https://hq.aedelgard.com
      AEDELGARD_DEVICE_TOKEN  — device token (from /v1/sessions, via the aedk)
      AEDELGARD_DEVICE_FINGERPRINT — the fingerprint bound into that token

    Honest cost (named, not hidden): relaying puts Aedelgard in the in-flight
    inference path — the broker sees the prompt in transit while forwarding it.
    Blind at rest; not operator-blind in-flight. The BYO-key path (AnthropicProvider
    et al.) avoids this by talking to the model directly. This is the documented
    trade of one-key convenience."""

    name = "aedelgard"

    def __init__(self, *, broker_url: str | None = None,
                 device_token: str | None = None,
                 device_fingerprint: str | None = None):
        import httpx
        self.broker_url = (broker_url or os.environ.get("AEDELGARD_BROKER_URL", "")).rstrip("/")
        self.device_token = device_token or os.environ.get("AEDELGARD_DEVICE_TOKEN", "")
        self.device_fingerprint = device_fingerprint or os.environ.get("AEDELGARD_DEVICE_FINGERPRINT", "")
        if not self.broker_url or not self.device_token:
            raise RuntimeError(
                "AedelgardProvider needs AEDELGARD_BROKER_URL and "
                "AEDELGARD_DEVICE_TOKEN (mint a device token from your aedk via "
                "/v1/sessions). Or set AGENT_PROVIDER=anthropic to use a direct key."
            )
        self._httpx = httpx
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    async def complete(self, *, model, max_tokens, system, tools, messages):
        from anthropic.types import Message
        headers = {"Authorization": f"Bearer {self.device_token}"}
        if self.device_fingerprint:
            headers["X-Device-Fingerprint"] = self.device_fingerprint
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        r = await self._client.post(
            f"{self.broker_url}/v1/relay/complete", json=payload, headers=headers,
        )
        if r.status_code != 200:
            detail = ""
            try:
                detail = r.json().get("error", "")
            except Exception:
                detail = r.text[:160]
            raise RuntimeError(f"Aedelgard relay HTTP {r.status_code}: {detail}")
        raw = r.json().get("response")
        if not raw:
            raise RuntimeError("Aedelgard relay returned no response payload")
        # Rebuild a real Anthropic Message so downstream is byte-for-byte the
        # same shape the AnthropicProvider returns.
        return Message.model_validate(raw)

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {
            "input": getattr(u, "input_tokens", 0),
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "output": getattr(u, "output_tokens", 0),
        }



# ── Bedrock Nova — portability proof, NOW WITH TOOL PARITY ────────────────────
#
# Move 2 "Done when": an alternate provider answering end-to-end with the agent's
# real memory + soul (system blocks) intact. First shipped as the TEXT path
# (commit 6e6f885); this version closes the named gap — full tool parity, so an
# alternate brain runs the whole tool cascade, not just text:
#   * Anthropic tool defs  -> Converse toolConfig   (_anthropic_tools_to_converse)
#   * Converse toolUse      -> Anthropic tool_use    (_NovaBlock(tool_use=...))
#   * Anthropic tool_result -> Converse toolResult   (_anthropic_messages_to_bedrock)
# The agent's cascade reader (stop_reason=="tool_use"; block.name/.input/.id) and
# the tools it wields — the official Aedelgard tool surface — are unchanged: the
# mind keeps its instruments; only the brain behind them swaps.

class _NovaBlock:
    """Anthropic-SDK-shaped content block so downstream serialization
    (_serialize_content via .model_dump) and the agent's tool-cascade reader
    (block.type/.name/.input/.id) keep working unchanged across an alternate
    brain. Carries either a text block or a tool_use block."""
    def __init__(self, *, text=None, tool_use=None):
        if tool_use is not None:
            self.type = "tool_use"
            self.id = tool_use["id"]
            self.name = tool_use["name"]
            self.input = tool_use["input"]
            self.text = None
        else:
            self.type = "text"
            self.text = text
    def model_dump(self, exclude_none=True):
        if self.type == "tool_use":
            return {"type": "tool_use", "id": self.id,
                    "name": self.name, "input": self.input}
        return {"type": "text", "text": self.text}


class _NovaUsage:
    def __init__(self, in_tok, out_tok):
        self.input_tokens = in_tok
        self.output_tokens = out_tok
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _NovaResponse:
    def __init__(self, blocks, stop_reason, in_tok, out_tok):
        self.content = blocks
        self.stop_reason = stop_reason
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


def _anthropic_tools_to_converse(tools):
    """Anthropic tool defs {name,description,input_schema} -> Bedrock Converse
    toolConfig. cache_control (an Anthropic-only caching marker) is dropped — it
    has no meaning to another brain (Discipline #2: don't smuggle one vendor's
    concepts into another)."""
    if not tools:
        return None
    specs = []
    for t in tools:
        spec = {
            "name": t["name"],
            "description": t.get("description", ""),
            "inputSchema": {"json": t.get("input_schema", {"type": "object"})},
        }
        specs.append({"toolSpec": spec})
    return {"tools": specs}


def _anthropic_messages_to_bedrock(messages):
    """Map Anthropic messages -> Bedrock Converse messages WITH tool parity.

    text        -> {"text": ...}
    tool_use    -> {"toolUse": {toolUseId, name, input}}     (assistant turn)
    tool_result -> {"toolResult": {toolUseId, content:[{text}]}}  (user turn)

    This is the brick that lets an alternate brain run the full tool cascade,
    not just answer in text. The agent appends Anthropic-shaped tool_result
    messages after running a tool; we round-trip them back into Converse here so
    the next complete() call continues the cascade."""
    out = []
    for m in messages:
        role = m["role"]
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": [{"text": content or "(empty)"}]})
            continue
        blocks = []
        for blk in content:
            if not isinstance(blk, dict):
                blocks.append({"text": str(blk)}); continue
            t = blk.get("type")
            if t == "text":
                txt = blk.get("text", "")
                if txt:
                    blocks.append({"text": txt})
            elif t == "tool_use":
                blocks.append({"toolUse": {
                    "toolUseId": blk.get("id"),
                    "name": blk.get("name"),
                    "input": blk.get("input", {}),
                }})
            elif t == "tool_result":
                c = blk.get("content")
                txt = c if isinstance(c, str) else str(c)
                blocks.append({"toolResult": {
                    "toolUseId": blk.get("tool_use_id"),
                    "content": [{"text": txt or "(empty)"}],
                }})
        if not blocks:
            blocks = [{"text": "(empty)"}]
        out.append({"role": role, "content": blocks})
    return out


class BedrockNovaProvider:
    """Amazon Bedrock Nova via the Converse API. Cheapest brain in the account —
    used to PROVE the mind is portable, now with full tool parity (see notes)."""

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
        tool_config = _anthropic_tools_to_converse(tools)
        if tool_config:
            kwargs["toolConfig"] = tool_config
        # boto3 is sync; run it off the event loop.
        resp = await asyncio.to_thread(self.client.converse, **kwargs)

        # Map the Converse response back into Anthropic-shaped blocks so the
        # agent's tool-cascade reader walks an alternate brain UNCHANGED.
        out_blocks = []
        for blk in resp["output"]["message"]["content"]:
            if "text" in blk:
                if blk["text"]:
                    out_blocks.append(_NovaBlock(text=blk["text"]))
            elif "toolUse" in blk:
                tu = blk["toolUse"]
                out_blocks.append(_NovaBlock(tool_use={
                    "id": tu["toolUseId"],
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                }))
        if not out_blocks:
            out_blocks = [_NovaBlock(text="")]

        # Converse stopReason "tool_use" -> Anthropic "tool_use"; everything
        # else folds to "end_turn" (the agent only branches on those two + the
        # Anthropic-side max_tokens it never receives from Nova).
        stop = "tool_use" if resp.get("stopReason") == "tool_use" else "end_turn"
        u = resp.get("usage", {})
        return _NovaResponse(out_blocks, stop,
                             u.get("inputTokens", 0), u.get("outputTokens", 0))

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
