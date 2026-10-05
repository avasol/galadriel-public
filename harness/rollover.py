"""THE ROLLOVER — a fresh context when the prompt cache has gone cold.

Why: a long thread costs little while the prompt cache is warm (cache reads are
a fraction of input price), but the first message after the cache TTL lapses
re-writes the whole thread at a premium. When a message arrives after the cache
has gone cold AND the thread is large, re-caching the whole thread is the most
expensive call there is.

So: the thread is archived (palace) and replaced IN PLACE by a small CARRY:
  - a header naming where the rest lives (journal verbatim, palace archive),
  - an extractive, zero-API bridge of the older turns,
  - the last KEEP exchanges VERBATIM as text (user words + the final answer),
    so a one-word reply like "Proceed" still points at the offer it accepts.
Tool calls, tool results and image payloads are not carried; the journal keeps
them.

The carry is journaled as a `[rollover]` event (meta.carry), so a journal
replay can rebuild the same thread (this engine does not replay the journal
into live history at boot yet). Kill switch: GALADRIEL_ROLLOVER=0.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("galadriel.rollover")

IDLE_S = float(os.environ.get("GALADRIEL_ROLLOVER_IDLE_S", "300"))      # ephemeral cache TTL
MIN_TOKENS = int(os.environ.get("GALADRIEL_ROLLOVER_MIN_TOKENS", "60000"))
KEEP = int(os.environ.get("GALADRIEL_ROLLOVER_KEEP", "2"))
CARRY_MAX_CHARS = 8000
HEADER_TAG = "[SYSTEM:ROLLOVER]"
_SKIP_PREFIXES = (HEADER_TAG, "[Context bridge")


def enabled() -> bool:
    return os.environ.get("GALADRIEL_ROLLOVER", "1").strip().lower() not in ("0", "false", "off", "no")


def should_roll(tokens: int, idle_s: Optional[float], min_tokens: int = None, idle_threshold: float = None) -> bool:
    min_tokens = MIN_TOKENS if min_tokens is None else min_tokens
    idle_threshold = IDLE_S if idle_threshold is None else idle_threshold
    return idle_s is not None and idle_s >= idle_threshold and tokens >= min_tokens


def _is_tool_result_turn(msg: dict) -> bool:
    c = msg.get("content")
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def _text_of(content) -> str:
    if isinstance(content, str):
        return "" if content.startswith(_SKIP_PREFIXES) else content
    if not isinstance(content, list):
        return str(content or "")
    parts = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            s = b.get("text") or ""
            if not s.startswith(_SKIP_PREFIXES):
                parts.append(s)
        elif t in ("image", "image_url", "input_image"):
            parts.append("[image]")
    return "\n".join(p for p in parts if p)


def exchanges(messages: list) -> list[tuple[str, Optional[str]]]:
    """(user text, final answer text | None) per REAL user turn, in order."""
    out: list[list] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user" and not _is_tool_result_turn(m):
            u = _text_of(m.get("content"))
            if u.strip():
                out.append([u, None])
        elif m.get("role") == "assistant" and out:
            t = _text_of(m.get("content"))
            if t.strip():
                out[-1][1] = t   # the LAST text of the exchange is the answer the user saw
    return [(u, a) for u, a in out]


def _cap(s: str) -> str:
    if len(s) <= CARRY_MAX_CHARS:
        return s
    head, tail = CARRY_MAX_CHARS * 5 // 8, CARRY_MAX_CHARS * 3 // 8
    return s[:head] + "\n…[trimmed for the carry; the full text is in the journal]…\n" + s[-tail:]


def _veil(s: str) -> str:
    try:
        from .redact import redact_secrets
        r = redact_secrets(s)
        return r[0] if isinstance(r, tuple) else r
    except Exception:  # noqa: BLE001 — never break a turn; redact is best-effort here, inbound veil already ran
        return s


def _default_bridge(older: list) -> Optional[str]:
    try:
        from .bridge import build_bridge
        return build_bridge(older, max_sentences=6)
    except Exception:  # noqa: BLE001
        return None


def build_carry(messages: list, keep: int = None, bridge: Callable[[list], Optional[str]] = None,
                now: _dt.datetime = None) -> list:
    """The replacement thread: strictly alternating user/assistant text blocks, user first, assistant last."""
    keep = KEEP if keep is None else keep
    done = [(u, a) for u, a in exchanges(messages) if a is not None]
    if not done or keep < 1:
        return []
    kept = done[-keep:]
    # older = everything before the first kept user turn (for the bridge)
    first_u = kept[0][0]
    cut = 0
    for i, m in enumerate(messages):
        if m.get("role") == "user" and not _is_tool_result_turn(m) and _text_of(m.get("content")) == first_u:
            cut = i
    # the bridge sees CONVERSATION only (user words + answers), never tool payloads
    older = []
    for u, a in exchanges(messages[:cut]):
        older.append({"role": "user", "content": u})
        if a:
            older.append({"role": "assistant", "content": a})
    summary = (bridge or _default_bridge)(older) if older else None
    now = now or _dt.datetime.now()
    day = now.strftime("%Y-%m-%d")
    header = (
        f"{HEADER_TAG} {now.strftime('%Y-%m-%d %H:%M')}: the prompt cache had gone cold on a long thread, so "
        f"this conversation was rolled over to a fresh context to save cost. Nothing is lost: every word is in "
        f"the journal (memory/journal/{day}.jsonl and earlier days) and the thread was archived to the palace. "
        f"Before assuming anything about earlier work, palace_search or read the journal; the daily log in the "
        f"system prompt holds the milestones. Carried below: the last {len(kept)} exchange(s) verbatim "
        f"(tool calls and results are not carried)."
    )
    if summary:
        header += f"\n\nEarlier in the thread (extractive summary):\n{summary}"
    carry = []
    for idx, (u, a) in enumerate(kept):
        blocks = [{"type": "text", "text": header}] if idx == 0 else []
        blocks.append({"type": "text", "text": _veil(_cap(u))})
        carry.append({"role": "user", "content": blocks})
        carry.append({"role": "assistant", "content": [{"type": "text", "text": _veil(_cap(a))}]})
    return carry


def archive_async(channel_id: str, messages: list) -> None:
    """Archive the rolled-off thread to the palace without blocking the turn."""
    try:
        from . import palace
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    except Exception as e:  # noqa: BLE001
        log.warning(f"rollover archive unavailable: {e}")
        return

    async def _go():
        try:
            await palace.archive_conversation(channel_id, messages)
        except Exception as e:  # noqa: BLE001
            log.warning(f"rollover archive failed: {e}")
    loop.create_task(_go())


def daily_note(memory_dir, text: str) -> None:
    """Append a one-line note to the day's memory log. A mockish dir is refused."""
    try:
        from .pathguard import is_mockish_dir
        if is_mockish_dir(memory_dir):
            return
    except Exception:  # noqa: BLE001
        return
    try:
        p = Path(memory_dir) / f"{_dt.date.today().isoformat()}.md"
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n- **{_dt.datetime.now().strftime('%H:%M')}:** {text}\n")
    except Exception:  # noqa: BLE001
        pass
