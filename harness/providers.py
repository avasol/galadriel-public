"""The Provider Seam — separate the mind from the brain power.

The agent's mind (soul + memory) is persistent,
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

import logging
import os
import time
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("galadriel.providers")


# Normalised usage shape — matches what _log_usage stores in agent.last_usage,
# so the /status cost panel is provider-relative, not Anthropic-shaped.
Usage = dict  # {"input": int, "cache_read": int, "cache_write": int, "output": int}


@runtime_checkable
class LLMProvider(Protocol):
    """The seam the agent calls instead of a raw vendor client."""

    async def complete(self, *, model: str, max_tokens: int,
                       system: Any, tools: Any, messages: Any,
                       thinking: Any = None) -> Any:
        """Run one completion. Returns a response object exposing at least
        `.usage`, `.content`, `.stop_reason` in the Anthropic SDK shape (the
        agent's downstream code reads those). For Anthropic this is the raw SDK
        response; alternate providers return a compatible shim."""
        ...

    def usage(self, raw: Any) -> Usage:
        """Normalise a raw response's token usage to the common Usage dict."""
        ...


_FOREIGN_TOOL_USE_KEYS = ("thought_signature",)


def _strip_foreign_keys(messages):
    """Remove keys another brain left on tool_use blocks (Gemini's
    thought_signature) before an Anthropic call — the Messages API rejects
    unknown fields. IDENTITY-PRESERVING: returns the very same list object when
    there is nothing to strip, so the byte-identical parity path is untouched."""
    dirty = False
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" \
                        and any(k in b for k in _FOREIGN_TOOL_USE_KEYS):
                    dirty = True
                    break
        if dirty:
            break
    if not dirty:
        return messages
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            nc = []
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    b = {k: v for k, v in b.items() if k not in _FOREIGN_TOOL_USE_KEYS}
                nc.append(b)
            m = {**m, "content": nc}
        out.append(m)
    return out


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

    async def complete(self, *, model, max_tokens, system, tools, messages,
                       thinking=None):
        # IDENTICAL to the original agent.py:555 call. cache_control markers on
        # system[0] / tools[-1] / messages[-1] are passed through untouched —
        # they are attached upstream and ARE the caching contract.
        # `thinking` (THE MIRROR, ported home from the body v0.14.1) is
        # additive: absent -> byte-identical call, the parity contract holds.
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=_strip_foreign_keys(messages),
        )
        if thinking:
            kwargs["thinking"] = thinking
        return await self.client.messages.create(**kwargs)

    def stream_complete(self, *, model, max_tokens, system, tools, messages,
                        thinking=None):
        """Return a context-manager that streams text deltas.
        Mirrors complete() exactly — same kwargs, same cache_control pass-through.
        Returns the SDK stream context manager; caller does 'async with ... as s'.
        """
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=_strip_foreign_keys(messages),
        )
        if thinking:
            kwargs["thinking"] = thinking
        return self.client.messages.stream(**kwargs)

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {
            "input": getattr(u, "input_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "output": getattr(u, "output_tokens", 0) or 0,
        }

    async def list_models(self) -> list[dict]:
        """Models THIS key may use, per the provider's own registry
        (GET /v1/models — no hardcoded catalogue to go stale). Newest first.

        Optional capability: the picker (/model) probes for this method with
        hasattr(); providers without it simply don't offer a listing yet.
        """
        page = await self.client.models.list(limit=50)
        return [
            {
                "id": m.id,
                "display_name": getattr(m, "display_name", None) or m.id,
                "created_at": str(getattr(m, "created_at", "") or ""),
            }
            for m in page.data
        ]


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


class ProviderAuthError(RuntimeError):
    """A TERMINAL credential failure: the brain-key was rejected by its
    issuer. Deterministic — the same key fails the same way on every retry
    until the key itself changes. Ported from aedelgard-body (v0.7.2 THE
    HONEST DOOR) alongside OpenAIProvider, 2026-08-26 (the Council build)."""


def _anthropic_tools_to_openai(tools):
    """Anthropic tool defs -> OpenAI function tools. cache_control markers are
    an Anthropic caching contract and are stripped here."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict) or "name" not in t:
            continue
        out.append({"type": "function", "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object"}),
        }})
    return out


def _flatten_tool_result_content(content):
    """tool_result content (str | [blocks]) -> plain text for a 'tool' message."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif isinstance(b, dict) and b.get("type") == "image":
            parts.append("[image omitted — this brain's API cannot see images inside tool results; switch to an Anthropic brain to use look()]")
    return "\n".join(parts)


def _anthropic_messages_to_openai(messages):
    """Anthropic messages -> chat.completions messages.

    The sharp edge (SPEC): tool-call IDs round-trip VERBATIM. The agent echoes
    the id from tool_use into tool_result; minting or rewriting ids here would
    desynchronise the orphan-repair passes."""
    import json as _json
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        texts, images, tool_calls, tool_msgs = [], [], [], []
        for b in content or []:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                if b.get("text"):
                    texts.append(b["text"])
            elif btype == "tool_use":
                tool_calls.append({
                    "id": b.get("id"),
                    "type": "function",
                    "function": {"name": b.get("name"),
                                 "arguments": _json.dumps(b.get("input") or {})},
                })
            elif btype == "tool_result":
                tool_msgs.append({
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id"),
                    "content": _flatten_tool_result_content(b.get("content")) or "",
                })
            elif btype == "image":
                src = b.get("source", {}) or {}
                if src.get("type") == "base64":
                    images.append({
                        "type": "image_url",
                        "image_url": {"url": "data:%s;base64,%s" % (
                            src.get("media_type", "image/png"),
                            src.get("data", ""))},
                    })
        if role == "assistant":
            msg = {"role": "assistant",
                   "content": "\n".join(texts) if texts else None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if msg["content"] is not None or tool_calls:
                out.append(msg)
        else:
            # user turn: tool results answer the assistant's preceding
            # tool_calls, so they go FIRST as their own 'tool' messages.
            out.extend(tool_msgs)
            if images:
                parts = [{"type": "text", "text": t} for t in texts]
                out.append({"role": "user", "content": parts + images})
            elif texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _anthropic_tools_to_responses(tools):
    """Anthropic tool defs -> OpenAI /v1/responses flat function tools."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict) or "name" not in t:
            continue
        out.append({
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object"}),
        })
    return out


def _anthropic_messages_to_responses_input(messages):
    """Anthropic messages -> OpenAI /v1/responses input items.

    Converts tool_use to function_call items and tool_result to
    function_call_output items with call_ids preserved verbatim."""
    import json as _json
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        texts = []
        for b in content or []:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                if b.get("text"):
                    texts.append(b["text"])
            elif btype == "tool_use":
                out.append({
                    "type": "function_call",
                    "call_id": b.get("id"),
                    "name": b.get("name"),
                    "arguments": _json.dumps(b.get("input") or {}),
                })
            elif btype == "tool_result":
                out.append({
                    "type": "function_call_output",
                    "call_id": b.get("tool_use_id"),
                    "output": _flatten_tool_result_content(b.get("content")) or "",
                })
        if texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return out


class OpenAIProvider:
    """chat.completions against a configurable base_url. Default =
    api.openai.com (BYO OpenAI key, direct — we are not on the wire). The
    same class speaks to the whole OpenAI-compatible ecosystem (Groq,
    OpenRouter, DeepSeek, vLLM, Ollama) via OPENAI_BASE_URL — see
    LocalProvider for the keyless offline case.

    Caching on this brain is AUTOMATIC: OpenAI caches any identical prompt
    prefix >= 1024 tokens with no markup; cached tokens are billed as read
    hits and there is no cache-write charge. Two things the seam does to
    make hits likely: the system prompt (soul + memory) always goes first and
    byte-identical, and `prompt_cache_key` pins a stable routing key derived
    from that prefix so consecutive turns land on the same cache shard."""

    name = "openai"
    _default_base = "https://api.openai.com/v1"

    # Model ids this brain will honour when the CALLER names one (the /model
    # dial, a fallback rung, a cross-provider pick). Anything else — e.g. the
    # agent's Anthropic default id — falls back to default_model.
    _OWN_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
    # reasoning_effort is accepted only by reasoning-capable families on
    # api.openai.com; other models and compatible servers 400 on the field.
    _REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-6")

    def __init__(self, *, api_key: str | None = None,
                 base_url: str | None = None, model: str | None = None):
        import httpx
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or self._default_base).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        if not self.api_key and self._is_openai_com():
            raise RuntimeError(
                "OpenAIProvider needs OPENAI_API_KEY. Or set AGENT_PROVIDER "
                "to another brain."
            )
        self.default_model = model or os.environ.get("OPENAI_MODEL") or (
            "gpt-4o-mini" if self._is_openai_com() else "")
        if not self.default_model:
            raise RuntimeError(
                "No model named for endpoint %s — set OPENAI_MODEL to the "
                "model tag your server hosts." % self.base_url)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(630.0, connect=10.0))
        # Learned per model, from the API's own 400: ids whose chat.completions
        # dialect refuses reasoning + function tools together. The known
        # families are pre-seeded by rule (see _reasoning_field); this set
        # catches the ones the rule does not yet know.
        self._no_reasoning_with_tools: set[str] = set()
        # Models whose chat.completions endpoint cannot handle function tools
        # with reasoning (e.g. gpt-6-astra); routed cleanly to /v1/responses.
        self._use_responses_endpoint: set[str] = set()

    def _is_openai_com(self) -> bool:
        return "api.openai.com" in self.base_url

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        return h

    def _max_tokens_param(self):
        # api.openai.com's newer models demand max_completion_tokens; the
        # wider compatible ecosystem (Ollama, Groq, OpenRouter, ...) speaks
        # the original max_tokens. Choose by endpoint, not by hope.
        return "max_completion_tokens" if self._is_openai_com() else "max_tokens"

    def _pick_model(self, requested: str | None) -> str:
        """Honour a caller-named model when it is one of ours (so the /model
        dial and fallback rungs work); otherwise use the configured default."""
        r = (requested or "").lower()
        if r and (not self._is_openai_com()
                  or r.startswith(self._OWN_MODEL_PREFIXES)):
            return requested
        return self.default_model

    def _supports_reasoning_effort(self, model: str | None = None) -> bool:
        if not self._is_openai_com():
            return False
        m = (model or self.default_model or "").lower()
        return m.startswith(self._REASONING_MODEL_PREFIXES)

    def _reasoning_field(self, model: str, has_tools: bool, thinking) -> str | None:
        """What to put in `reasoning_effort`, if anything.

        Live finding (2026-09-07, api.openai.com): on the current reasoning
        families, /v1/chat/completions REFUSES function tools unless
        reasoning_effort is explicitly "none" — even the default (field
        absent) is a 400. Reasoning *with* tools lives on /v1/responses, a
        different dialect this provider does not yet speak. So: tools present
        -> "none" (the cascade works, no hidden reasoning); no tools ->
        translate the agent's thinking budget into a tier. No false parity:
        a Claude-style thinking budget is honoured here only on tool-less
        turns, and that limitation is named in the README."""
        if has_tools and model.lower() in self._no_reasoning_with_tools:
            return "none"          # learned from this model's own 400
        if not self._supports_reasoning_effort(model):
            return None
        if has_tools:
            return "none"          # known reasoning family: rule, not guess
        if thinking:
            budget = thinking.get("budget_tokens") if isinstance(thinking, dict) else None
            return self._effort_from_budget(budget)
        return None

    @staticmethod
    def _effort_from_budget(budget_tokens) -> str | None:
        """Translate the agent's continuous thinking budget (Claude/Gemini
        dialect) into OpenAI's discrete tiers. No budget -> no opinion (let
        the model default)."""
        if not budget_tokens:
            return None
        if budget_tokens <= 2048:
            return "low"
        if budget_tokens <= 8192:
            return "medium"
        return "high"

    @staticmethod
    def _cache_key(sys_text: str) -> str:
        import hashlib
        return "galadriel-" + hashlib.sha256(sys_text.encode("utf-8")).hexdigest()[:24]

    async def _complete_responses(self, *, oai_model, max_tokens, sys_text, tools, messages, thinking):
        """Responses API dialect (/v1/responses). Supports reasoning AND function tools
        natively on reasoning families (e.g. gpt-6-astra)."""
        import json as _json
        input_items = _anthropic_messages_to_responses_input(messages)
        body = {
            "model": oai_model,
            "input": input_items,
            "max_output_tokens": max_tokens,
        }
        if sys_text:
            body["instructions"] = sys_text
            if self._is_openai_com():
                body["prompt_cache_key"] = self._cache_key(sys_text)
        resp_tools = _anthropic_tools_to_responses(tools)
        if resp_tools:
            body["tools"] = resp_tools
        effort = self._reasoning_field(oai_model, False, thinking)
        if effort and effort != "none":
            body["reasoning"] = {"effort": effort}

        r = await self._client.post(self.base_url + "/responses",
                                    json=body, headers=self._headers())
        if r.status_code == 401:
            raise ProviderAuthError(
                self.name + ": the endpoint rejected the key (HTTP 401). Check OPENAI_API_KEY.")
        if r.status_code != 200:
            exc = RuntimeError("%s HTTP %s: %s" % (self.name, r.status_code, r.text[:200]))
            exc.status_code = r.status_code
            raise exc
        data = r.json()

        out_blocks = []
        for item in data.get("output", []) or []:
            itype = item.get("type")
            if itype == "function_call":
                fn_name = item.get("name")
                raw_args = item.get("arguments") or "{}"
                try:
                    args = _json.loads(raw_args)
                    if not isinstance(args, dict):
                        args = {"_raw": raw_args}
                except (ValueError, TypeError):
                    args = {"_raw": raw_args}
                out_blocks.append(_NovaBlock(tool_use={
                    "id": item.get("call_id") or item.get("id") or ("call_%d" % len(out_blocks)),
                    "name": fn_name,
                    "input": args,
                }))
            elif itype == "message":
                for c in item.get("content", []) or []:
                    if c.get("type") == "output_text":
                        out_blocks.append(_NovaBlock(text=c.get("text", "")))

        if not out_blocks:
            out_blocks = [_NovaBlock(text="")]

        finish = "end_turn"
        if any(getattr(b, "type", None) == "tool_use" for b in out_blocks):
            finish = "tool_use"

        u = data.get("usage", {}) or {}
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
        prompt = u.get("input_tokens", 0) or 0
        resp = _NovaResponse(out_blocks, finish,
                             max(prompt - cached, 0),
                             u.get("output_tokens", 0) or 0)
        resp.usage.cache_read_input_tokens = cached
        return resp


    async def complete(self, *, model, max_tokens, system, tools, messages,
                       thinking=None):
        import json as _json
        oai_model = self._pick_model(model)
        sys_text = _anthropic_system_to_text(system)
        if self._is_openai_com() and (
            "astra" in oai_model.lower() or oai_model.lower() in self._use_responses_endpoint
        ):
            return await self._complete_responses(
                oai_model=oai_model, max_tokens=max_tokens, sys_text=sys_text,
                tools=tools, messages=messages, thinking=thinking)
        oai_messages = []
        if sys_text:
            oai_messages.append({"role": "system", "content": sys_text})
        oai_messages.extend(_anthropic_messages_to_openai(messages))
        body = {"model": oai_model, "messages": oai_messages,
                self._max_tokens_param(): max_tokens}
        oai_tools = _anthropic_tools_to_openai(tools)
        if oai_tools:
            body["tools"] = oai_tools
        effort = self._reasoning_field(oai_model, bool(oai_tools), thinking)
        if effort:
            body["reasoning_effort"] = effort
        if sys_text and self._is_openai_com():
            body["prompt_cache_key"] = self._cache_key(sys_text)

        r = await self._client.post(self.base_url + "/chat/completions",
                                    json=body, headers=self._headers())
        if r.status_code == 400 and oai_tools and "responses" in r.text.lower():
            # API told us: function tools with reasoning require /v1/responses.
            self._use_responses_endpoint.add(oai_model.lower())
            return await self._complete_responses(
                oai_model=oai_model, max_tokens=max_tokens, sys_text=sys_text,
                tools=tools, messages=messages, thinking=thinking)
        if (r.status_code == 400 and oai_tools and "reasoning_effort" in r.text
                and body.get("reasoning_effort") != "none"):
            # The API corrected us once: this model will not reason and call
            # tools in the same chat.completions request. Remember, retry.
            self._no_reasoning_with_tools.add(oai_model.lower())
            body["reasoning_effort"] = "none"
            r = await self._client.post(self.base_url + "/chat/completions",
                                        json=body, headers=self._headers())
        if r.status_code == 401:
            # TERMINAL: the key is provably dead — not a rung to step past.
            raise ProviderAuthError(
                self.name + ": the endpoint rejected the key (HTTP 401). "
                "Check OPENAI_API_KEY.")
        if r.status_code != 200:
            exc = RuntimeError("%s HTTP %s: %s" % (
                self.name, r.status_code, r.text[:200]))
            exc.status_code = r.status_code   # feeds _is_fallback_worthy
            raise exc
        data = r.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        out_blocks = []
        if msg.get("content"):
            out_blocks.append(_NovaBlock(text=msg["content"]))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            try:
                args = _json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {"_raw": fn.get("arguments")}
            except (ValueError, TypeError):
                # Compatible servers sometimes emit broken argument JSON. The
                # cascade degrades to an is_error tool result; it must not
                # die here.
                args = {"_raw": fn.get("arguments")}
            out_blocks.append(_NovaBlock(tool_use={
                "id": tc.get("id") or "oai_call_%d" % len(out_blocks),
                "name": fn.get("name"), "input": args}))
        if not out_blocks:
            out_blocks = [_NovaBlock(text="")]

        finish = {"tool_calls": "tool_use", "length": "max_tokens"}.get(
            choice.get("finish_reason"), "end_turn")
        if any(getattr(b, "type", None) == "tool_use" for b in out_blocks):
            finish = "tool_use"

        u = data.get("usage", {}) or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        prompt = u.get("prompt_tokens", 0) or 0
        # Honest accounting: OpenAI's prompt_tokens INCLUDES the cached share.
        # Report uncached input + cache_read separately, the way the Anthropic
        # path does, so /status and the cost panel compare like with like.
        resp = _NovaResponse(out_blocks, finish,
                             max(prompt - cached, 0),
                             u.get("completion_tokens", 0) or 0)
        resp.usage.cache_read_input_tokens = cached
        return resp

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {"input": u.input_tokens,
                "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                "cache_write": 0,   # automatic caching: no billed write
                "output": u.output_tokens}

    async def list_models(self) -> list[dict]:
        """Models THIS key may use, per GET /v1/models — powers the /model
        dial. On api.openai.com the catalogue is filtered to chat-capable
        families (embeddings, audio, image and moderation ids are not brains);
        compatible servers return whatever they host, unfiltered."""
        r = await self._client.get(self.base_url + "/models", headers=self._headers())
        if r.status_code == 401:
            raise ProviderAuthError(self.name + ": the endpoint rejected the key (HTTP 401).")
        r.raise_for_status()
        data = r.json().get("data", []) or []
        out = []
        for m in data:
            mid = m.get("id") or ""
            if not mid:
                continue
            if self._is_openai_com():
                low = mid.lower()
                if not low.startswith(self._OWN_MODEL_PREFIXES):
                    continue
                if any(x in low for x in ("embedding", "audio", "realtime", "tts",
                                          "transcribe", "image", "moderation",
                                          "search", "instruct")):
                    continue
            out.append({"id": mid,
                        "display_name": mid,
                        "created_at": str(m.get("created") or "")})
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out


class LocalProvider(OpenAIProvider):
    """The keyless local brain: Ollama / LM Studio / vLLM / llama.cpp on your
    own machine via their OpenAI-compatible endpoint. No cloud credential, no
    per-token rent — and operator-blind in the absolute sense: the prompts
    never leave the hardware. Point OPENAI_BASE_URL at the server (default
    Ollama) and name the hosted tag in LOCAL_MODEL or OPENAI_MODEL."""

    name = "local"
    _default_base = "http://localhost:11434/v1"

    def __init__(self, *, api_key=None, base_url=None, model=None):
        super().__init__(
            api_key=api_key or os.environ.get("OPENAI_API_KEY") or "local",
            base_url=base_url or os.environ.get("LOCAL_BASE_URL"),
            model=model or os.environ.get("LOCAL_MODEL"))


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
            # Gemini 3.x: every functionCall part carries a thoughtSignature
            # that MUST be replayed verbatim on the next turn, or the API
            # rejects the whole request (400, found live 2026-09-04). It rides
            # in the stored history as an extra key on the tool_use dict; the
            # Anthropic path strips it (see _strip_foreign_keys).
            self.thought_signature = tool_use.get("thought_signature")
        else:
            self.type = "text"
            self.text = text
            self.thought_signature = None
    def model_dump(self, exclude_none=True):
        if self.type == "tool_use":
            d = {"type": "tool_use", "id": self.id,
                 "name": self.name, "input": self.input}
            if self.thought_signature:
                d["thought_signature"] = self.thought_signature
            return d
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

    async def complete(self, *, model, max_tokens, system, tools, messages,
                       thinking=None):
        # `thinking` is accepted but not translated in v1 — no false parity.
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

# ── Gemini — a second BYO brain, direct to Google (we are not on the wire) ─────
#
# The thesis made visible across a genuinely different vendor: swap the brain,
# keep the mind. Talks to Google's generateContent REST API directly via httpx
# (no new SDK dependency). BYO key, so the
# body is operator-blind by construction on this path: plaintext goes from the
# user's machine straight to Google; we are not on the wire.
#
# Mapping (same discipline as the Bedrock-Nova brick — full tool parity):
#   system blocks      -> system_instruction (flattened text; the mind, intact)
#   anthropic tools    -> tools[].function_declarations[]
#   role "assistant"   -> "model"; "user" stays "user"
#   text               -> {"text": ...}
#   tool_use           -> {"functionCall": {name, args}}        (model turn)
#   tool_result        -> {"functionResponse": {name, response}} (user turn)
# Gemini's functionResponse needs the function NAME, but Anthropic tool_result
# carries only tool_use_id — so we first index id->name from prior tool_use
# blocks, then resolve each tool_result against it.

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _anthropic_tools_to_gemini(tools):
    """Anthropic tool defs -> Gemini function_declarations. cache_control and
    other Anthropic-only keys are dropped (Discipline #2)."""
    if not tools:
        return None
    decls = []
    for t in tools:
        schema = dict(t.get("input_schema", {"type": "object"}))
        decls.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": schema,
        })
    return [{"function_declarations": decls}]


def _index_tool_names(messages):
    """Map tool_use id -> tool name across all assistant messages, so a later
    tool_result (which only has the id) can be turned into a Gemini
    functionResponse (which needs the name)."""
    id_to_name = {}
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                id_to_name[blk.get("id")] = blk.get("name")
    return id_to_name


# Gemini 3 accepts this documented dummy signature for functionCall parts that
# were NOT produced by Gemini (e.g. history minted by Claude before a brain
# swap) — without it the API 400s on the first replayed Claude tool call.
_GEMINI_DUMMY_SIGNATURE = "skip_thought_signature_validator"


def _gemini_image_part(blk):
    """Anthropic image block {source:{type:base64,media_type,data}} -> Gemini inlineData."""
    src = blk.get("source") or {}
    if src.get("type") == "base64" and src.get("data"):
        return {"inlineData": {"mimeType": src.get("media_type", "image/png"),
                               "data": src["data"]}}
    return None


def _anthropic_messages_to_gemini(messages):
    """Map Anthropic messages -> Gemini contents WITH tool parity.
    Vision-aware (2026-09-04): image blocks become inlineData parts instead of
    a str()'d base64 dump; tool_result LIST content is joined as text, its
    images lifted out as sibling parts; thinking blocks are dropped (they are
    another brain's private reasoning, never replayed across vendors)."""
    id_to_name = _index_tool_names(messages)
    out = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": role, "parts": [{"text": content or " "}]})
            continue
        parts = []
        for blk in content:
            if not isinstance(blk, dict):
                parts.append({"text": str(blk)}); continue
            t = blk.get("type")
            if t == "text":
                txt = blk.get("text", "")
                if txt:
                    parts.append({"text": txt})
            elif t == "image":
                ip = _gemini_image_part(blk)
                if ip:
                    parts.append(ip)
            elif t in ("thinking", "redacted_thinking"):
                continue
            elif t == "tool_use":
                fc = {"functionCall": {
                    "name": blk.get("name"),
                    "args": blk.get("input", {}) or {},
                }}
                fc["thoughtSignature"] = blk.get("thought_signature") or _GEMINI_DUMMY_SIGNATURE
                parts.append(fc)
            elif t == "tool_result":
                c = blk.get("content")
                images = []
                if isinstance(c, list):
                    texts = []
                    for sub in c:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            texts.append(sub.get("text", ""))
                        elif isinstance(sub, dict) and sub.get("type") == "image":
                            ip = _gemini_image_part(sub)
                            if ip:
                                images.append(ip)
                                texts.append("[image attached below]")
                        else:
                            texts.append(str(sub))
                    txt = "\n".join(x for x in texts if x)
                else:
                    txt = c if isinstance(c, str) else ("" if c is None else str(c))
                name = id_to_name.get(blk.get("tool_use_id"), "unknown_tool")
                parts.append({"functionResponse": {
                    "name": name,
                    "response": {"result": txt or "(empty)"},
                }})
                parts.extend(images)
        if not parts:
            parts = [{"text": " "}]
        out.append({"role": role, "parts": parts})
    return out


class GeminiProvider:
    """Google Gemini via generateContent REST. BYO key, direct to Google —
    operator-blind by construction (we are not on the wire). Proves the mind is
    portable across a second, genuinely different vendor."""

    name = "gemini"

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        import httpx
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY")
                        or os.environ.get("GOOGLE_API_KEY") or "")
        if not self.api_key:
            raise RuntimeError(
                "GeminiProvider needs GEMINI_API_KEY (or GOOGLE_API_KEY). "
                "Or set AGENT_PROVIDER=anthropic to use a Claude key."
            )
        self.default_model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        # Gemini 2.5 models "think" by default, burning the output budget on
        # hidden reasoning. GEMINI_THINKING_BUDGET controls it: 0 disables it
        # (fast, cheap — the default here), -1 lets the model decide.
        self.thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", "0"))
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        # Adaptive, not hardcoded: some 2.5-class models (found live 2026-08-27
        # testing the cross-provider /supreme pick — gemini-2.5-pro) REJECT
        # budget 0 ("this model only works in thinking mode"); others (flash)
        # require it be disableable for cheap fast calls. Rather than hand-list
        # which models mandate thinking, learn it the same way agent.py's
        # Anthropic path already learns per-model thinking dialects: the API
        # corrects us once via its own error, we remember for next time.
        self._requires_thinking: set[str] = set()
        
        # Stateful explicit cache mapping (hash -> Google cachedContent name)
        self._cache_map: dict[str, str] = {}
        self._last_cache_write = False

    async def _post(self, gem_model: str, body: dict) -> "httpx.Response":
        url = f"{_GEMINI_API_BASE}/models/{gem_model}:generateContent"
        return await self._client.post(
            url, json=body, headers={"x-goog-api-key": self.api_key},
        )

    async def _get_or_create_cache(self, gem_model: str, stable_text: str, gem_tools: list | None) -> str | None:
        if not stable_text or len(stable_text) < 4000:
            return None
            
        import hashlib
        import json
        raw = stable_text + (json.dumps(gem_tools, sort_keys=True) if gem_tools else "")
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        
        if h in self._cache_map:
            return self._cache_map[h]
            
        url = f"{_GEMINI_API_BASE}/cachedContents"
        payload = {
            "model": f"models/{gem_model}",
            "systemInstruction": {"parts": [{"text": stable_text}]},
            "ttl": "3600s",
        }
        if gem_tools:
            payload["tools"] = gem_tools
            
        r = await self._client.post(
            url, json=payload, headers={"x-goog-api-key": self.api_key},
            timeout=30.0
        )
        if r.status_code == 200:
            name = r.json()["name"]
            self._cache_map[h] = name
            self._last_cache_write = True
            log.info(f"Gemini cached context created: {name} for {gem_model}")
            return name
        elif r.status_code == 400 and "too small" in r.text.lower():
            return None
        else:
            log.warning(f"Gemini cache creation failed {r.status_code}: {r.text[:200]}")
            return None

    async def complete(self, *, model, max_tokens, system, tools, messages,
                       thinking=None):
        # `thinking` is accepted but not translated in v1 — no false parity.
        # `model` is normally the Anthropic model id from the agent's own
        # session config — Gemini ignores that and uses its configured
        # default, since the mind doesn't care which brain answers. But a
        # caller that DOES know it wants a specific Gemini model (Palantír's
        # cross-provider /supreme pick, 2026-08-27) passes a real Gemini id
        # here — honour it rather than silently substituting the default.
        gem_model = model if model and "gemini" in model.lower() else self.default_model
        gen_cfg = {"maxOutputTokens": max_tokens}
        # Only 2.5 models accept thinkingConfig; harmless to send, but gate to
        # avoid surprising older models. Budget 0 = no hidden reasoning spend,
        # UNLESS this model has already told us it mandates thinking — then
        # omit thinkingConfig entirely and let Gemini use its own default.
        if "2.5" in gem_model and gem_model not in self._requires_thinking:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
            
        gem_tools = _anthropic_tools_to_gemini(tools)
        
        stable_text = ""
        dynamic_text = ""
        if isinstance(system, list):
            for b in system:
                if b.get("cache_control"):
                    stable_text += b.get("text", "") + "\n\n"
                else:
                    dynamic_text += b.get("text", "") + "\n\n"
            stable_text = stable_text.strip()
            dynamic_text = dynamic_text.strip()
        else:
            dynamic_text = _anthropic_system_to_text(system)
            
        cache_name = await self._get_or_create_cache(gem_model, stable_text, gem_tools) if stable_text else None
        
        gem_messages = _anthropic_messages_to_gemini(messages)
        if dynamic_text:
            injection = f"--- SYSTEM CONTEXT (DYNAMIC) ---\n{dynamic_text}\n\n"
            if gem_messages and gem_messages[0]["role"] == "user":
                gem_messages[0]["parts"].insert(0, {"text": injection})
            else:
                gem_messages.insert(0, {"role": "user", "parts": [{"text": injection}]})
                
        body = {
            "contents": gem_messages,
            "generationConfig": gen_cfg,
        }
        
        if cache_name:
            body["cachedContent"] = cache_name
        else:
            full_sys = (stable_text + "\n\n" + dynamic_text).strip() if stable_text else dynamic_text
            if full_sys:
                body["systemInstruction"] = {"parts": [{"text": full_sys}]}
            if gem_tools:
                body["tools"] = gem_tools

        r = await self._post(gem_model, body)
        
        # Auto-heal cache misses (TTL expired on Google's side)
        if r.status_code in (400, 404) and "cache" in r.text.lower() and cache_name:
            log.warning(f"Gemini cached content {cache_name} invalid, retrying without cache.")
            for k, v in list(self._cache_map.items()):
                if v == cache_name:
                    del self._cache_map[k]
            body.pop("cachedContent", None)
            if stable_text:
                body["systemInstruction"] = {"parts": [{"text": stable_text}]}
            if gem_tools:
                body["tools"] = gem_tools
            r = await self._post(gem_model, body)

        if r.status_code == 400 and "thinking mode" in r.text.lower() and "thinkingConfig" in body.get("generationConfig", {}):
            # Learn it and retry once, this call, without forcing a budget.
            self._requires_thinking.add(gem_model)
            body["generationConfig"].pop("thinkingConfig", None)
            log.info(f"Gemini model {gem_model} mandates thinking — retrying without a forced budget.")
            r = await self._post(gem_model, body)
            
        if r.status_code != 200:
            detail = r.text[:200]
            exc = RuntimeError(f"Gemini HTTP {r.status_code}: {detail}")
            exc.status_code = r.status_code
            raise exc
            
        data = r.json()

        cands = data.get("candidates") or []
        out_blocks = []
        finish = "end_turn"
        if cands:
            cand = cands[0]
            for part in cand.get("content", {}).get("parts", []):
                if part.get("thought"):
                    continue  # Gemini's own reasoning summary — not an answer block
                if "text" in part and part["text"]:
                    out_blocks.append(_NovaBlock(text=part["text"]))
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    out_blocks.append(_NovaBlock(tool_use={
                        "id": f"gem_{fc.get('name','tool')}_{len(out_blocks)}",
                        "name": fc.get("name"),
                        "input": fc.get("args", {}) or {},
                        "thought_signature": part.get("thoughtSignature"),
                    }))
        if not out_blocks:
            out_blocks = [_NovaBlock(text="")]
        # If any tool_use block is present, the agent must run the cascade.
        if any(getattr(b, "type", None) == "tool_use" for b in out_blocks):
            finish = "tool_use"

        um = data.get("usageMetadata", {})
        
        cache_read = um.get("cachedContentTokenCount", 0)
        cache_write = 0
        if self._last_cache_write and cache_read > 0:
            cache_write = cache_read
            self._last_cache_write = False
            
        # thoughtsTokenCount is billed as OUTPUT by Google but reported apart
        # from candidatesTokenCount — fold it in or the ledger undercounts
        # every thinking model (Gemini 3.x thinks by default).
        resp = _NovaResponse(
            out_blocks, finish,
            um.get("promptTokenCount", 0),
            (um.get("candidatesTokenCount", 0) or 0) + (um.get("thoughtsTokenCount", 0) or 0),
        )
        resp.usage.cache_read_input_tokens = cache_read
        resp.usage.cache_creation_input_tokens = cache_write
        return resp

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {"input": u.input_tokens, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "output": u.output_tokens}



# ── Fallback ladder ───────────────────────────────────────────────────────────
# HTTP statuses that mean "this brain is unavailable right now" — closed model
# (404), revoked access (403), throttle (429), provider incident (5xx / 529).
# NOT here on purpose: 400 (malformed request — will fail on every rung) and
# 401 (bad credential — falling back would mask a key problem, not fix it).
_FALLBACK_WORTHY_STATUS = {403, 404, 408, 409, 429, 500, 502, 503, 504, 529}

# Exception modules we treat as vendor-side when no HTTP status is attached
# (connection refused, DNS, TLS, stream death). A TypeError raised by OUR own
# shim code lives in 'builtins' and must surface as a bug, never a downgrade.
_VENDOR_MODULES = ("anthropic", "httpx", "httpcore", "google", "botocore",
                   "aiohttp", "urllib3", "requests")


def _is_fallback_worthy(exc: Exception) -> bool:
    """Decide whether an error means 'try the next rung' (True) or 'this is a
    bug/config problem that follows you down every rung' (False)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _FALLBACK_WORTHY_STATUS
    if isinstance(exc, (ConnectionError, OSError, TimeoutError,
                        NotImplementedError)):
        return True
    mod = type(exc).__module__ or ""
    return mod.split(".")[0] in _VENDOR_MODULES


class _Rung:
    """One step of the ladder: a lazily-built provider + optional model pin."""

    def __init__(self, label: str, factory, model: str | None):
        self.label = label
        self.factory = factory      # () -> LLMProvider, called at most once
        self.model = model          # None = use the caller's model (rung 0)
        self.provider = None
        self.dead = False           # construction failed (e.g. missing key)

    def get(self):
        if self.provider is None and not self.dead:
            try:
                self.provider = self.factory()
            except Exception as e:
                self.dead = True
                log.warning("Fallback rung %s cannot be built: %s", self.label, e)
        return self.provider


class FallbackProvider:
    """The ladder. Wraps an ordered chain of providers; if the active brain is
    unavailable (closed model, revoked access, provider outage), the call steps
    down to the next rung — at the price of one cold cache-write on the new
    model. Demotion is sticky so every later call starts at the working rung;
    the primary is re-probed after `retry_primary_s` so a reopened door is
    found without a restart. With AGENT_MODEL_FALLBACKS unset this class is
    never constructed and the hot path is byte-identical to before."""

    name = "fallback"

    def __init__(self, rungs: list, retry_primary_s: float = 3600.0):
        self._rungs = rungs
        self._active = 0
        self._demoted_at: float | None = None
        self._last_used = None     # provider that produced the last raw response
        self.retry_primary_s = retry_primary_s

    @property
    def active_label(self) -> str:
        return self._rungs[self._active].label

    async def complete(self, *, model, max_tokens, system, tools, messages,
                       thinking=None):
        start = self._active
        if start != 0 and self._demoted_at is not None and \
                time.monotonic() - self._demoted_at >= self.retry_primary_s:
            log.info("Fallback: probe window elapsed — re-trying primary %s",
                     self._rungs[0].label)
            start = 0
        last_exc: Exception | None = None
        for i in range(start, len(self._rungs)):
            rung = self._rungs[i]
            provider = rung.get()
            if provider is None:
                continue
            try:
                raw = await provider.complete(
                    model=rung.model or model, max_tokens=max_tokens,
                    system=system, tools=tools, messages=messages,
                    thinking=thinking)
            except Exception as e:
                if not _is_fallback_worthy(e):
                    raise
                log.warning("Rung %s unavailable (%s: %s) — stepping down",
                            rung.label, type(e).__name__, e)
                last_exc = e
                continue
            self._last_used = provider
            if i != self._active:
                if i == 0:
                    log.warning("Fallback: primary %s RECOVERED — promoting back",
                                rung.label)
                    self._demoted_at = None
                else:
                    log.warning("Fallback: DOWNGRADED to %s (was %s). One cold "
                                "cache-write, then warm again.",
                                rung.label, self._rungs[self._active].label)
                    self._demoted_at = time.monotonic()
                self._active = i
            return raw
        raise last_exc if last_exc is not None else RuntimeError(
            "FallbackProvider: no usable rungs")

    def usage(self, raw) -> Usage:
        provider = self._last_used or self._rungs[0].get()
        return provider.usage(raw)


def _parse_fallback_chain(spec: str, primary_provider_name: str) -> list:
    """'claude-opus-4-8, gemini:gemini-3.1-pro' -> [(provider, model), ...].
    A bare model name means 'same provider as the primary, lesser model'."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            pname, mname = part.split(":", 1)
            out.append((pname.strip().lower(), mname.strip()))
        else:
            out.append((primary_provider_name, part))
    return out

_REGISTRY = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "local": LocalProvider,
    "bedrock-nova": BedrockNovaProvider,
}


# ── Boot-time credential requirements per provider ───────────────────────────
#
# The two-path privacy switch made real: which credential the body needs depends
# entirely on which brain it talks to. main.py uses this to gate boot honestly —
# a body using a non-Claude brain must NOT be blocked for lacking an ANTHROPIC_API_KEY
# it deliberately does not have; a local-model body needs no cloud key at all.
#
# Returns (env_vars_any_of, human_hint). env_vars_any_of is a tuple — boot is OK
# if ANY one is set. Empty tuple = no credential required (e.g. a local model).

_PROVIDER_REQUIREMENTS = {
    "anthropic":    (("ANTHROPIC_API_KEY",),
                     "your own Claude key — you talk to the model directly; "
                     "we are not on the wire (operator-blind by construction)."),
    "gemini":       (("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                     "your own Gemini key — direct to Google; we are not on the wire."),
    "openai":       (("OPENAI_API_KEY",),
                     "your own OpenAI key — direct to OpenAI; we are not on the wire."),
    "bedrock-nova": ((),  # uses the host's AWS credentials/role
                     "AWS credentials on the host (role or env)."),
    "local":        ((),  # offline model, no cloud credential
                     "nothing — a local model runs offline on your own machine."),
}


def provider_requirements(provider_name: str | None = None) -> tuple[tuple, str]:
    """Return (env_vars_any_of, hint) for the selected provider. Used by main.py
    to gate boot per the chosen brain, so the body is never blocked for lacking a
    credential it intentionally does not carry."""
    name = (provider_name or os.environ.get("AGENT_PROVIDER") or "anthropic").lower()
    return _PROVIDER_REQUIREMENTS.get(name, (("ANTHROPIC_API_KEY",),
        f"a credential for provider {name!r}."))


def _make_single(name: str, *, anthropic_client=None,
                 api_key: str | None = None) -> LLMProvider:
    """Build a single (non-ladder) provider by name. Internal — callers outside
    this module should use make_provider()."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown AGENT_PROVIDER={name!r}. Known: {', '.join(_REGISTRY)}"
        )
    if cls is AnthropicProvider:
        return AnthropicProvider(client=anthropic_client, api_key=api_key)
    if cls is GeminiProvider:
        return GeminiProvider()
    return cls()


def make_provider(provider_name: str | None = None, *,
                  anthropic_client=None, api_key: str | None = None) -> LLMProvider:
    """Select a provider. Defaults to 'anthropic' so the live hot path is
    unchanged unless AGENT_PROVIDER is explicitly set to something else.
    If AGENT_MODEL_FALLBACKS is set, wraps the primary in a FallbackProvider
    ladder so closed/throttled models step down gracefully."""
    name = (provider_name or os.environ.get("AGENT_PROVIDER") or "anthropic").lower()
    base = _make_single(name, anthropic_client=anthropic_client, api_key=api_key)

    chain = (os.environ.get("AGENT_MODEL_FALLBACKS") or "").strip()
    if not chain:
        return base   # parity path — byte-identical to the pre-ladder code

    rungs = [_Rung(f"{base.name}:<primary>", lambda b=base: b, None)]
    for pname, mname in _parse_fallback_chain(chain, base.name):
        rungs.append(_Rung(
            f"{pname}:{mname}",
            lambda p=pname: _make_single(p, api_key=api_key),
            mname))
    retry_s = float(os.environ.get("AGENT_FALLBACK_RETRY_PRIMARY_S", "3600"))
    return FallbackProvider(rungs, retry_primary_s=retry_s)
