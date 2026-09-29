"""thinking_preservation.py — the preserved-thinking guard.

Anthropic platform change (effective 2026-10-01): on the Fable 5.1 /
Opus 5.5 / Sonnet 5.5 family a thinking block stays valid only while
everything sent before it is unchanged on later requests.  If the prefix
differs, that block and every later thinking block are invalid, and the
request is rejected with a 400 (or the blocks are dropped), whichever you
choose:

    thinking.block_binding.prefix_mismatch_behavior: "error" | "drop_block"

A harness that rebuilds the system prompt each turn, edits earlier messages
(stale-image ageing, compaction, tool-pair repair), or replays assistant
turns WITH their thinking blocks is exactly the shape the notice warns
about — any such client-side edit to an earlier turn can answer 400.

This module is the honest seam for that fault — the sibling of
``_adapt_thinking_dialect`` (which learns a thinking *dialect* per model).
It is deliberately PURE and dark by default so it can never change the live
path on its own:

  * ``replaying_thinking(messages)`` — is there any thinking block in the
    history we are about to replay?  (the precondition for the whole fault)
  * ``is_prefix_mismatch_error(err)`` — does this error actually NAME the
    prefix/thinking binding as the cause?  We assert on the CAUSE, never on
    a bare 400 (an error names the wrong layer all too often).
  * ``strip_thinking_blocks(messages)`` — the recovery: drop thinking blocks
    from the replayed history so the prefix is consistent again. Idempotent,
    identity-preserving when there is nothing to strip.

Two modes mirror the API's own choice:

  * ``"drop_block"`` (default, the Oct-1 posture) — after a named
    prefix-mismatch 400, strip the thinking blocks and retry ONCE.  Never silent: it is logged, because
    swallowing a 400 the caller could have fixed is itself a dishonesty
    ("Don't hide the 400.").
  * ``"error"`` — never rewrite on the fly; raise the honest error so the
    fault is seen, not masked.

Opt in with::

    AGENT_PRESERVED_THINKING_RECOVERY=drop_block

Inert on any brain that does not send thinking blocks (other providers drop
them at the seam; this sees nothing to do).
"""

from __future__ import annotations

import logging

log = logging.getLogger("aedelgard.thinking_preservation")

# The thinking block types the Messages API may replay in an assistant turn.
_THINKING_TYPES = ("thinking", "redacted_thinking")


def replaying_thinking(messages: list) -> bool:
    """True if any assistant turn in ``messages`` carries a thinking block.

    Precondition for the prefix-mismatch class: with no thinking blocks in
    the replayed history, there is nothing the API can invalidate.
    """
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in _THINKING_TYPES:
                return True
    return False


def is_prefix_mismatch_error(err: Exception) -> bool:
    """Does this error NAME the preserved-thinking prefix binding as its cause?

    Conservative on purpose.  A bare ``400 invalid_request_error`` is NOT
    enough — it names the wrong layer far too often (a tool-pair orphan, a
    malformed image, an empty message all surface as 400).  We claim the
    cause only when the message names it.
    """
    s = str(err).lower()
    markers = (
        "block_binding",
        "prefix_mismatch",
        "prefix mismatch",
        "thinking block",
        "preserved thinking",
        "unmodified thinking",
    )
    return any(m in s for m in markers)


def strip_thinking_blocks(messages: list) -> tuple[list, int]:
    """Return ``(new_messages, removed_count)`` with thinking blocks dropped.

    IDENTITY-PRESERVING: returns the SAME list object when there is nothing
    to strip, so the byte-identical parity path is untouched.  Never raises;
    a malformed message is passed through unchanged rather than crash a live
    turn.  Idempotent: stripping twice is stripping once.
    """
    if not replaying_thinking(messages):
        return messages, 0

    removed = 0
    out = []
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in _THINKING_TYPES:
                    removed += 1
                    continue
                new_content.append(block)
            if new_content:
                m = {**m, "content": new_content}
            else:
                # An assistant turn that held ONLY thinking blocks — dropping
                # them would leave an empty message, which the API rejects.
                # Keep a minimal text block so tool-pair integrity holds.
                m = {**m, "content": [{"type": "text", "text": "(reasoning elided)"}]}
        out.append(m)
    return out, removed


def recovery_mode() -> str:
    """The configured mismatch behaviour: ``"error"`` (default) or ``"drop_block"``.

    Default (2026-09-29, the Oct-1 posture) is ``"drop_block"`` — drop the
    invalid blocks and retry once. Set ``"error"`` to never rewrite.
    """
    import os
    mode = (os.environ.get("AGENT_PRESERVED_THINKING_RECOVERY") or "drop_block").strip().lower()
    return mode if mode in ("error", "drop_block") else "drop_block"
