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


class OpenAIProvider(_NotYetWired):
    """chat.completions; automatic prompt caching. Roadmap."""
    name = "openai"


class LocalProvider(_NotYetWired):
    """Ollama / llama.cpp OpenAI-compatible. mark_cache is a no-op (context is
    free). The body's killer offline case. Roadmap."""
    name = "local"


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


def _anthropic_messages_to_gemini(messages):
    """Map Anthropic messages -> Gemini contents WITH tool parity."""
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
            elif t == "tool_use":
                parts.append({"functionCall": {
                    "name": blk.get("name"),
                    "args": blk.get("input", {}) or {},
                }})
            elif t == "tool_result":
                c = blk.get("content")
                txt = c if isinstance(c, str) else str(c)
                name = id_to_name.get(blk.get("tool_use_id"), "unknown_tool")
                parts.append({"functionResponse": {
                    "name": name,
                    "response": {"result": txt or "(empty)"},
                }})
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

    async def complete(self, *, model, max_tokens, system, tools, messages):
        # `model` is the Anthropic model id from the agent; Gemini ignores it and
        # uses its own. The mind doesn't care which brain answers.
        gem_model = self.default_model
        gen_cfg = {"maxOutputTokens": max_tokens}
        # Only 2.5 models accept thinkingConfig; harmless to send, but gate to
        # avoid surprising older models. Budget 0 = no hidden reasoning spend.
        if "2.5" in gem_model:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        body = {
            "contents": _anthropic_messages_to_gemini(messages),
            "generationConfig": gen_cfg,
        }
        sys_text = _anthropic_system_to_text(system)
        if sys_text:
            body["systemInstruction"] = {"parts": [{"text": sys_text}]}
        gem_tools = _anthropic_tools_to_gemini(tools)
        if gem_tools:
            body["tools"] = gem_tools

        url = f"{_GEMINI_API_BASE}/models/{gem_model}:generateContent"
        r = await self._client.post(
            url, json=body, headers={"x-goog-api-key": self.api_key},
        )
        if r.status_code != 200:
            detail = r.text[:200]
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {detail}")
        data = r.json()

        cands = data.get("candidates") or []
        out_blocks = []
        finish = "end_turn"
        if cands:
            cand = cands[0]
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part and part["text"]:
                    out_blocks.append(_NovaBlock(text=part["text"]))
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    out_blocks.append(_NovaBlock(tool_use={
                        "id": f"gem_{fc.get('name','tool')}_{len(out_blocks)}",
                        "name": fc.get("name"),
                        "input": fc.get("args", {}) or {},
                    }))
        if not out_blocks:
            out_blocks = [_NovaBlock(text="")]
        # If any tool_use block is present, the agent must run the cascade.
        if any(getattr(b, "type", None) == "tool_use" for b in out_blocks):
            finish = "tool_use"

        um = data.get("usageMetadata", {})
        return _NovaResponse(
            out_blocks, finish,
            um.get("promptTokenCount", 0),
            um.get("candidatesTokenCount", 0),
        )

    def usage(self, raw) -> Usage:
        u = raw.usage
        return {"input": u.input_tokens, "cache_read": 0,
                "cache_write": 0, "output": u.output_tokens}


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


log = logging.getLogger("galadriel.providers")

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

    async def complete(self, *, model, max_tokens, system, tools, messages):
        start = self._active
        if start != 0 and self._demoted_at is not None and                 time.monotonic() - self._demoted_at >= self.retry_primary_s:
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
                    system=system, tools=tools, messages=messages)
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


def provider_requirements(provider_name: str | None = None) -> tuple[tuple, str]:
    """Return (env_vars_any_of, hint) for the selected provider. Used by main.py
    to gate boot per the chosen brain, so the body is never blocked for lacking a
    credential it intentionally does not carry."""
    name = (provider_name or os.environ.get("AGENT_PROVIDER") or "anthropic").lower()
    return _PROVIDER_REQUIREMENTS.get(name, (("ANTHROPIC_API_KEY",),
        f"a credential for provider {name!r}."))


def make_provider(provider_name: str | None = None, *,
                  anthropic_client=None, api_key: str | None = None) -> LLMProvider:
    """Select a provider. Defaults to 'anthropic' so the live hot path is
    unchanged unless AGENT_PROVIDER is explicitly set to something else."""
    name = (provider_name or os.environ.get("AGENT_PROVIDER") or "anthropic").lower()
    base = _make_single(name, anthropic_client=anthropic_client, api_key=api_key)

    chain = (os.environ.get("AGENT_MODEL_FALLBACKS") or "").strip()
    if not chain:
        return base   # the parity path — byte-identical to the pre-ladder code

    rungs = [_Rung(f"{base.name}:<primary>", lambda b=base: b, None)]
    for pname, mname in _parse_fallback_chain(chain, base.name):
        # NB: rungs build via _make_single, never make_provider — re-entering
        # the chain logic here would wrap ladders in ladders.
        rungs.append(_Rung(
            f"{pname}:{mname}",
            lambda p=pname: _make_single(p, api_key=api_key),
            mname))
    retry_s = float(os.environ.get("AGENT_FALLBACK_RETRY_PRIMARY_S", "3600"))
    return FallbackProvider(rungs, retry_primary_s=retry_s)


def _make_single(name: str, *, anthropic_client=None,
                 api_key: str | None = None) -> LLMProvider:
    """Construct exactly one provider — the pre-ladder selection logic,
    verbatim. Used for the primary and for each fallback rung."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown AGENT_PROVIDER={name!r}. Known: {', '.join(_REGISTRY)}"
        )
    if cls is AnthropicProvider:
        return AnthropicProvider(client=anthropic_client, api_key=api_key)
    if cls is GeminiProvider:
        # The agent passes the ANTHROPIC key positionally; Gemini must NOT use
        # it. Always self-read GEMINI_API_KEY / GOOGLE_API_KEY from env instead.
        return GeminiProvider()
    return cls()
