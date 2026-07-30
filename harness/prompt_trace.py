"""THE GLASS PROMPT — full visibility into every prompt as it enters the model.

Lord Isildur's order (2026-07-30): "enough debugging in the aedelgard body
that we can thoroughly evaluate the prompts as they enter and are executed."

Every provider call is traced to memory/prompt_trace/YYYY-MM-DD.jsonl (UTC):

  - The SYSTEM prompt: per-block char/token sizes, plus the full verbatim
    text deduplicated into memory/prompt_trace/blobs/system_<sha12>.txt —
    written once per unique system prompt, referenced by hash thereafter.
    (System changes rarely within a day; dedup keeps the trace lean while
    keeping every byte inspectable.)
  - The MESSAGES: per-message role, token estimate, 200-char head, and a
    content hash. Verbatim message bodies are NOT duplicated here — the
    journal and the cascade archive already hold them verbatim; the trace
    holds the *shape* of what entered the context window.
  - The RESPONSE: stop_reason and the provider's own usage numbers
    (input, cache_read, cache_write, output) — the ground truth against
    which our estimates are judged.

Zero prompt expense. Non-fatal by design. One writer (the agent loop),
one reader (the Tower /api/debug/prompts, lazily).
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("harness.prompt_trace")

_HEAD = 200  # chars of each message kept inline


def _est_tokens(obj) -> int:
    if isinstance(obj, str):
        return len(obj) // 4
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str)) // 4
    except Exception:
        return len(str(obj)) // 4


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _render_content(content) -> str:
    """Flatten a message's content (str or block list) to a single string
    for hashing/heads."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content)


def _usage_dict(response) -> dict:
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", None),
        "cache_read": getattr(u, "cache_read_input_tokens", None),
        "cache_write": getattr(u, "cache_creation_input_tokens", None),
        "output_tokens": getattr(u, "output_tokens", None),
    }


def trace_call(memory_dir, *, channel: str, turn_id: str, seq: int,
               model: str, system_blocks: list, messages: list,
               response=None) -> None:
    """Record one provider call. Call AFTER the response so usage numbers
    ride along. Non-fatal: a trace failure never touches a live turn."""
    try:
        trace_dir = Path(memory_dir) / "prompt_trace"
        blobs = trace_dir / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)

        # System: dedup the verbatim text by hash.
        sys_texts = [
            (b.get("text", "") if isinstance(b, dict) else str(b))
            for b in (system_blocks or [])
        ]
        sys_full = "\n\n\u241e\n\n".join(sys_texts)  # ␞ block separator
        sys_hash = _sha12(sys_full)
        blob_path = blobs / f"system_{sys_hash}.txt"
        if not blob_path.exists():
            blob_path.write_text(sys_full, encoding="utf-8")
        sys_meta = [
            {"i": i, "chars": len(t), "est_tokens": len(t) // 4,
             "cached": bool(isinstance(b, dict) and b.get("cache_control"))}
            for i, (b, t) in enumerate(zip(system_blocks or [], sys_texts))
        ]

        # Messages: shape, not bodies (journal + cascade archive hold those).
        msg_meta = []
        for m in messages or []:
            rendered = _render_content(m.get("content", ""))
            msg_meta.append({
                "role": m.get("role"),
                "est_tokens": len(rendered) // 4,
                "head": rendered[:_HEAD],
                "sha12": _sha12(rendered),
            })

        ts = datetime.now(timezone.utc).isoformat()
        record = {
            "ts": ts,
            "channel": str(channel),
            "turn": turn_id,
            "seq": seq,
            "model": model,
            "system_hash": sys_hash,
            "system_blocks": sys_meta,
            "system_est_tokens": len(sys_full) // 4,
            "n_messages": len(msg_meta),
            "messages_est_tokens": sum(m["est_tokens"] for m in msg_meta),
            "messages": msg_meta,
            "stop_reason": getattr(response, "stop_reason", None),
            "usage": _usage_dict(response),
        }
        day_path = trace_dir / f"{ts[:10]}.jsonl"
        with open(day_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception as e:
        log.warning("prompt trace failed (non-fatal): %s", e)


def read_recent(memory_dir, n: int = 20) -> list:
    """Last n trace records, newest first. Reads today + yesterday (UTC)."""
    from datetime import timedelta
    trace_dir = Path(memory_dir) / "prompt_trace"
    now = datetime.now(timezone.utc)
    records = []
    for day in (now - timedelta(days=1), now):
        p = trace_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            continue
    return records[-n:][::-1]


def read_system_blob(memory_dir, sys_hash: str) -> str | None:
    """Fetch the verbatim system prompt for a given trace hash."""
    if not sys_hash.isalnum():
        return None
    p = Path(memory_dir) / "prompt_trace" / "blobs" / f"system_{sys_hash}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None
