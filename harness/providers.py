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

    ── ONE KEY, the killer onboarding ──────────────────────────────────────
    The aedk (the Aedelgard registration key) is the ONLY secret the user pastes.
    A device token is short-lived (~1h TTL, fingerprint-bound) and must be minted
    from the aedk via POST /v1/sessions and SILENTLY RE-MINTED when it expires.
    This provider owns that whole lifecycle, so "paste your key once" is true,
    not hollow: the body keeps thinking for as long as the aedk is valid.

    Config (env), two ways:
      Preferred (true one-key):
        AEDELGARD_BROKER_URL  — broker base, e.g. https://hq.aedelgard.com
        AEDELGARD_AEDK        — the registration key (aedk…); provider self-mints
        AEDELGARD_DEVICE_FINGERPRINT — optional; a stable per-install fingerprint
                                       is derived + persisted if unset
      Legacy/advanced (pre-minted token, no self-mint):
        AEDELGARD_DEVICE_TOKEN  — a device token you minted yourself
        AEDELGARD_DEVICE_FINGERPRINT — the fingerprint bound into that token

    Honest cost (named, not hidden): relaying puts Aedelgard in the in-flight
    inference path — the broker sees the prompt in transit while forwarding it.
    Blind at rest; not operator-blind in-flight. The BYO-key path (AnthropicProvider
    et al.) avoids this by talking to the model directly. This is the documented
    trade of one-key convenience."""

    name = "aedelgard"

    def __init__(self, *, broker_url: str | None = None,
                 device_token: str | None = None,
                 device_fingerprint: str | None = None,
                 aedk: str | None = None):
        import httpx
        self.broker_url = (broker_url or os.environ.get("AEDELGARD_BROKER_URL", "")).rstrip("/")
        self.aedk = aedk or os.environ.get("AEDELGARD_AEDK", "")
        self.device_fingerprint = (
            device_fingerprint
            or os.environ.get("AEDELGARD_DEVICE_FINGERPRINT", "")
            or self._stable_fingerprint()
        )
        # A pre-minted token (legacy/advanced) short-circuits self-minting.
        self.device_token = device_token or os.environ.get("AEDELGARD_DEVICE_TOKEN", "")
        if not self.broker_url:
            raise RuntimeError(
                "AedelgardProvider needs AEDELGARD_BROKER_URL. Or set "
                "AGENT_PROVIDER=anthropic to use a direct key."
            )
        if not self.device_token and not self.aedk:
            raise RuntimeError(
                "AedelgardProvider needs either AEDELGARD_AEDK (one-key: the "
                "provider mints + refreshes device tokens for you) or a "
                "pre-minted AEDELGARD_DEVICE_TOKEN. Or AGENT_PROVIDER=anthropic."
            )
        self._httpx = httpx
        # NOTE: do NOT cache an httpx.AsyncClient on self here. The Tower serves
        # each chat request on its own short-lived asyncio event loop, which is
        # closed when the request ends. An AsyncClient binds to the loop alive at
        # construction time, so a cached client raises "Event loop is closed" on
        # the next request. We open a fresh client per complete() call instead,
        # which always binds to the current running loop.

    @staticmethod
    def _stable_fingerprint() -> str:
        """A stable per-install device fingerprint. Persisted under the body's
        data dir so the same machine re-mints against the same fingerprint
        (the broker binds tokens to it). Falls back to a host-derived hash."""
        import hashlib
        from pathlib import Path
        # Honour the native body's data dir if present; else a dotfile in HOME.
        base = os.environ.get("AEDELGARD_DATA_DIR") or os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else (Path.home() / ".aedelgard")
        try:
            root.mkdir(parents=True, exist_ok=True)
            fp_file = root / "device_fingerprint"
            if fp_file.exists():
                return fp_file.read_text().strip()
            import uuid
            fp = hashlib.sha256(f"{uuid.getnode()}:{uuid.uuid4()}".encode()).hexdigest()[:32]
            fp_file.write_text(fp)
            try:
                fp_file.chmod(0o600)
            except OSError:
                pass
            return fp
        except OSError:
            import uuid
            return hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:32]

    async def _mint_device_token(self, client) -> None:
        """Exchange the aedk for a fresh device token via POST /v1/sessions.
        Raises if the aedk is invalid/revoked — same opaque 401 the broker
        gives (no oracle). Uses the caller's live (current-loop) httpx client."""
        if not self.aedk:
            raise RuntimeError(
                "Aedelgard device token expired and no AEDELGARD_AEDK is set to "
                "re-mint it. Re-paste your Aedelgard key."
            )
        r = await client.post(
            f"{self.broker_url}/v1/sessions",
            json={"registration_key": self.aedk,
                  "device_fingerprint": self.device_fingerprint},
        )
        if r.status_code != 200:
            detail = ""
            try:
                detail = r.json().get("error", "")
            except Exception:
                detail = r.text[:160]
            raise RuntimeError(
                f"Aedelgard session mint HTTP {r.status_code}: {detail} "
                "(check your AEDELGARD_AEDK)."
            )
        self.device_token = r.json().get("device_token", "")
        if not self.device_token:
            raise RuntimeError("Aedelgard /v1/sessions returned no device_token")

    async def complete(self, *, model, max_tokens, system, tools, messages):
        from anthropic.types import Message
        # Open a fresh client bound to the CURRENT event loop (see __init__ note).
        async with self._httpx.AsyncClient(
            timeout=self._httpx.Timeout(120.0, connect=10.0)
        ) as client:
            # Ensure we hold a token (mint from aedk on first use).
            if not self.device_token:
                await self._mint_device_token(client)
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "tools": tools,
                "messages": messages,
            }

            async def _post():
                headers = {"Authorization": f"Bearer {self.device_token}"}
                if self.device_fingerprint:
                    headers["X-Device-Fingerprint"] = self.device_fingerprint
                return await client.post(
                    f"{self.broker_url}/v1/relay/complete", json=payload, headers=headers,
                )

            r = await _post()
            # A 401 means the short-TTL token expired (or was revoked). If we own
            # an aedk, silently re-mint ONCE and retry — what makes one-key real.
            if r.status_code == 401 and self.aedk:
                await self._mint_device_token(client)
                r = await _post()
            # Consume the response while the client is still open.
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

# ── Gemini — a second BYO brain, direct to Google (we are not on the wire) ─────
#
# The thesis made visible across a genuinely different vendor: swap the brain,
# keep the mind. Talks to Google's generateContent REST API directly via httpx
# (no new SDK dependency — consistent with AedelgardProvider). BYO key, so the
# body is operator-blind by construction on this path: plaintext goes from the
# user's machine straight to Google; Aedelgard is not on the wire.
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
    "aedelgard": AedelgardProvider,
    "bedrock-nova": BedrockNovaProvider,
}


# ── Boot-time credential requirements per provider ───────────────────────────
#
# The two-path privacy switch made real: which credential the body needs depends
# entirely on which brain it talks to. main.py uses this to gate boot honestly —
# an Aedelgard-key-only body must NOT be blocked for lacking an ANTHROPIC_API_KEY
# it deliberately does not have; a local-model body needs no cloud key at all.
#
# Returns (env_vars_any_of, human_hint). env_vars_any_of is a tuple — boot is OK
# if ANY one is set. Empty tuple = no credential required (e.g. a local model).

_PROVIDER_REQUIREMENTS = {
    "anthropic":    (("ANTHROPIC_API_KEY",),
                     "your own Claude key — you talk to the model directly; "
                     "we are not on the wire (operator-blind by construction)."),
    "aedelgard":    (("AEDELGARD_AEDK", "AEDELGARD_DEVICE_TOKEN"),
                     "your Aedelgard key (aedk) — the body mints + refreshes "
                     "device tokens for you; one key, no sk-ant-."),
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
    if cls is GeminiProvider:
        # The agent passes the ANTHROPIC key positionally; Gemini must NOT use
        # it. Always self-read GEMINI_API_KEY / GOOGLE_API_KEY from env instead.
        return GeminiProvider()
    return cls()
