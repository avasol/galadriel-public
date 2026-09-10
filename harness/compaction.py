"""Context compaction — summarize old tool results to keep history lean.

When a tool_result is about to be replaced with its summary, the verbatim
content is archived to the MemPalace first (via `palace.mine_batch_dir`).
Archive is fire-and-forget — a failure here must never break compaction.

Summarization rides the agent's own LLMProvider seam (harness/providers.py) —
it does NOT construct a private provider client. That means /compact never
requires external keys beyond what is dialed in; it asks whatever provider is
already live for a cheap, short completion.

To avoid running trims on expensive flagship brains (Opus, Fable, GPT-6,
Gemini Pro), compaction dynamically queries Palantír (`/v1/recommend?task=summarisation`)
for the lowest-cost active model under the active provider brand, with clean
static fallbacks if Palantír is offline. All provider adapters honour this
economy model passed to complete(model=...).
"""

import asyncio
import logging
from datetime import datetime

from . import palace

log = logging.getLogger("galadriel.compaction")

# Keep images in messages within the last N user turns. Beyond that, the visual
# context is usually moot and the base64 blob just burns tokens.
IMAGE_RETENTION_USER_TURNS = 3

# Tool results in the last N messages are kept verbatim. Older long ones get
# summarized.
TOOL_RESULT_FRESH_MESSAGES = 20

# Static fallback economy models per provider brand when Palantír is unreachable.
_STATIC_ECONOMY_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-3.5-flash-lite",
    "google": "gemini-3.5-flash-lite",
    "bedrock": "amazon.nova-micro-v1:0",
    "bedrock-nova": "amazon.nova-micro-v1:0",
}

_PALANTIR_PROVIDER_MAP = {
    "anthropic": "anthropic",
    "gemini": "google",
    "google": "google",
    "openai": "openai",
    "bedrock": "bedrock",
    "bedrock-nova": "bedrock",
}

_ECONOMY_MODEL_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SEC = 3600


def get_economy_summary_model(provider_name: str) -> str:
    """Resolve the lowest-cost model for conversation trims/summarization.
    Queries Palantír's recommendation engine (/v1/recommend?task=summarisation)
    with brand filtering, cached in-process for 1 hour, with graceful static
    fallback."""
    import time
    import urllib.request
    import json
    import os

    prov = (provider_name or "").lower()
    now = time.time()
    if prov in _ECONOMY_MODEL_CACHE:
        cached_ts, cached_mid = _ECONOMY_MODEL_CACHE[prov]
        if now - cached_ts < _CACHE_TTL_SEC:
            return cached_mid

    palantir_prov = _PALANTIR_PROVIDER_MAP.get(prov, prov)
    palantir_base = os.environ.get("PALANTIR_URL", "https://api.aedelgard.com/v1/models")
    # Base url might be .../v1/models -> resolve to /v1/recommend
    rec_url = palantir_base.replace("/v1/models", "/v1/recommend")
    if "/v1/recommend" not in rec_url:
        rec_url = "https://api.aedelgard.com/v1/recommend"
    url = f"{rec_url}?task=summarisation&providers={palantir_prov}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Aedelgard-Compaction/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            ranked = data.get("ranked", [])
            if ranked:
                chosen = ranked[0]["model_id"]
                _ECONOMY_MODEL_CACHE[prov] = (now, chosen)
                log.info(f"Compaction: resolved economy model via Palantír: {prov} -> {chosen}")
                return chosen
    except Exception as exc:
        log.debug(f"Compaction: Palantír recommendation failed for {prov}: {exc}")

    fallback = _STATIC_ECONOMY_MODELS.get(prov, "claude-haiku-4-5-20251001")
    _ECONOMY_MODEL_CACHE[prov] = (now, fallback)
    return fallback


async def _archive_to_palace(items: list[dict]) -> None:
    """Write verbatim pre-compaction tool_results to the palace.

    Each item: {"message_idx": int, "tool_use_id": str, "content": str}.
    Writes a timestamped batch dir on EBS, then delegates the actual mine
    to `palace.mine_batch_dir`. Silent on failure — compaction continues.
    """
    if not items:
        return

    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    batch_dir = palace._archive_root() / f"compaction_{ts}"
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        for item in items:
            fname = f"msg{item['message_idx']:04d}_{item['tool_use_id'][-12:]}.md"
            body = (
                f"# Pre-compaction tool_result archive\n\n"
                f"- archived: {ts}\n"
                f"- message_idx: {item['message_idx']}\n"
                f"- tool_use_id: {item['tool_use_id']}\n"
                f"- length: {len(item['content'])} chars\n\n"
                f"---\n\n"
                f"{item['content']}"
            )
            (batch_dir / fname).write_text(body, encoding="utf-8")
    except Exception as e:
        log.warning(f"Palace archive write failed: {e}")
        return

    ok = await palace.mine_batch_dir(batch_dir, agent="compaction")
    if ok:
        log.info(f"Palace archive: mined {len(items)} drawer(s) from {batch_dir}")


def _is_user_turn(msg: dict) -> bool:
    """A real user turn — not a tool_result wrapper, which is also role=user."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("text", "image"):
                return True
    return False


async def compact_conversation(messages: list, provider) -> dict:
    """Compress conversation history.

    - Images in messages older than the last IMAGE_RETENTION_USER_TURNS user
      turns are replaced with a text placeholder.
    - Long tool_result blocks older than the last TOOL_RESULT_FRESH_MESSAGES
      are summarized via `provider.complete()` — the agent's own LLMProvider
      seam, NOT a private client. `provider` is the caller's live instance
      (e.g. `agent.provider`), so summarization always rides whatever brain
      is actually dialed in.
    """
    user_turn_idx = [i for i, m in enumerate(messages) if _is_user_turn(m)]
    if len(user_turn_idx) > IMAGE_RETENTION_USER_TURNS:
        image_retain_from = user_turn_idx[-IMAGE_RETENTION_USER_TURNS]
    else:
        image_retain_from = 0

    summarize_before = max(0, len(messages) - TOOL_RESULT_FRESH_MESSAGES)

    if image_retain_from == 0 and summarize_before == 0:
        return {
            "compacted_messages": messages,
            "tokens_before": 0,
            "tokens_after": 0,
            "compression_ratio": 1.0,
            "summaries_created": 0,
            "images_removed": 0,
        }

    provider_name = getattr(provider, "name", "anthropic")
    summarize_model = get_economy_summary_model(provider_name)
    summaries_created = 0
    images_removed = 0
    compacted = []
    archive_items: list[dict] = []  # verbatim tool_results to file in the palace

    for i, msg in enumerate(messages):
        strip_images_here = i < image_retain_from
        summarize_here = i < summarize_before

        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            new_content = []
            for block in msg["content"]:
                if (
                    strip_images_here
                    and isinstance(block, dict)
                    and block.get("type") == "image"
                ):
                    new_content.append({
                        "type": "text",
                        "text": "[image removed — context compacted]",
                    })
                    images_removed += 1
                    continue

                if (
                    summarize_here
                    and isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and len(block.get("content", "")) > 3000
                ):
                    result_text = block["content"]
                    archive_items.append({
                        "message_idx": i,
                        "tool_use_id": block.get("tool_use_id", "unknown"),
                        "content": result_text,
                    })
                    try:
                        summary_response = await provider.complete(
                            model=summarize_model,
                            max_tokens=150,
                            system="",
                            tools=[],
                            messages=[
                                {
                                    "role": "user",
                                    "content": (
                                        f"Summarize this tool output in 1-2 sentences. "
                                        f"Preserve critical details (errors, file paths, counts). "
                                        f"Discard verbose scaffolding:\n\n{result_text}"
                                    ),
                                }
                            ],
                        )
                        summary = summary_response.content[0].text
                        new_content.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("tool_use_id"),
                                "content": f"[SUMMARIZED] {summary}",
                            }
                        )
                        summaries_created += 1
                        log.info(
                            f"Summarized tool result: {len(result_text)} → {len(summary)} chars"
                        )
                    except Exception as e:
                        log.warning(f"Could not summarize tool result: {e}, keeping original")
                        new_content.append(block)
                else:
                    new_content.append(block)

            compacted.append({**msg, "content": new_content})
        else:
            compacted.append(msg)

    # Rough token estimate: 4 chars ≈ 1 token
    tokens_before = sum(len(str(m.get("content", ""))) // 4 for m in messages)
    tokens_after = sum(len(str(m.get("content", ""))) // 4 for m in compacted)
    ratio = tokens_after / tokens_before if tokens_before > 0 else 1.0

    # Fire-and-forget archive of the verbatim pre-compaction tool_results.
    # /compact latency stays unchanged; palace mining happens in the background.
    if archive_items:
        asyncio.create_task(_archive_to_palace(archive_items))

    return {
        "compacted_messages": compacted,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "compression_ratio": ratio,
        "summaries_created": summaries_created,
        "images_removed": images_removed,
        "archive_queued": len(archive_items),
    }
