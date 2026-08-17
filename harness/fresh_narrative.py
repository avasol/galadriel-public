"""THE FRESH NARRATIVE — Phase 0 (SHADOW) + the local cascade archive.

Ported from the galadriel harness (2026-07-30, Lord Isildur's order:
"Rebuild and adoption"). Two duties, both at zero prompt expense
(cascades leave the CONTEXT, never EXISTENCE — disk is free, prompt
tokens are not):

1. archive_cascade() — every completed turn's full cascade (user message,
   tool_use, tool_result, assistant blocks) is saved VERBATIM to local
   disk: memory/cascades/YYYY-MM-DD.jsonl. It never enters a prompt
   unless deliberately summoned. Distinct from CascadeLedger (heads-only
   audit surface): this is the full body, the summonable archive.

2. shadow_observe() — Phase 0 measurement for the stateless architecture
   (SPEC_fresh_narrative.md in the galadriel repo). For each incoming
   message it builds the retrieval query the fresh narrative WOULD use,
   runs the palace search, and logs a comparison record: what stateless
   retrieval would have provided vs what the growing buffer actually
   provided. Fired on a daemon thread — zero latency, zero behavior
   change.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("galadriel.fresh_narrative")


def _est_tokens(obj) -> int:
    """Cheap token estimate: rendered chars / 4."""
    if isinstance(obj, str):
        return len(obj) // 4
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str)) // 4
    except Exception:
        return len(str(obj)) // 4


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        f.flush()


# ── 1. The cascade archive ─────────────────────────────────────────────


def archive_cascade(memory_dir, channel: str, turn_messages: list) -> None:
    """Persist one turn's full cascade verbatim. Non-fatal by design."""
    try:
        if not turn_messages:
            return
        ts = datetime.now(timezone.utc).isoformat()
        record = {
            "ts": ts,
            "channel": str(channel),
            "n_messages": len(turn_messages),
            "est_tokens": _est_tokens(turn_messages),
            "messages": turn_messages,
        }
        path = Path(memory_dir) / "cascades" / f"{ts[:10]}.jsonl"
        _append_jsonl(path, record)
    except Exception as e:  # never let archival break a live turn
        log.warning("cascade archive failed (non-fatal): %s", e)


# ── 2. Phase 0 shadow observation ──────────────────────────────────────


def _thread_hint(buffer_messages: list) -> str:
    """Naive Phase-0 stand-in for the session state token: the previous
    user message, truncated. Phase 1 replaces this with a model-authored
    thread state."""
    for m in reversed(buffer_messages[:-1] if buffer_messages else []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"][:300]
    return ""


def shadow_observe(memory_dir, channel: str, user_text: str,
                   buffer_messages: list) -> None:
    """Fire-and-forget Phase 0 measurement. Spawns a daemon thread so the
    live respond path pays nothing."""
    # Snapshot cheap stats NOW (the buffer mutates during the cascade).
    buffer_tokens = sum(_est_tokens(m.get("content", "")) for m in buffer_messages)
    buffer_count = len(buffer_messages)
    hint = _thread_hint(buffer_messages)

    def _run():
        try:
            from . import palace
            query = f"{user_text[:500]}\n{hint}".strip()
            if not query:
                return
            hits_md = palace.search(query, k=3)
            ts = datetime.now(timezone.utc).isoformat()
            record = {
                "ts": ts,
                "channel": str(channel),
                "query": query[:600],
                "est_fresh_tokens": _est_tokens(hits_md),
                "est_buffer_tokens": buffer_tokens,
                "buffer_messages": buffer_count,
                "hits_preview": (hits_md or "")[:1500],
            }
            path = (Path(memory_dir) / "fresh_narrative_shadow"
                    / f"{ts[:10]}.jsonl")
            _append_jsonl(path, record)
        except Exception as e:
            log.warning("shadow observe failed (non-fatal): %s", e)

    threading.Thread(target=_run, daemon=True, name="fresh-narrative-shadow").start()
