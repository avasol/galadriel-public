"""Conversation bridge — local, zero-API-cost summarization for trim continuity.

When history is trimmed from the front of a conversation, those messages leave
the working window for good. This module produces a short "bridge" summary — a
few sentences of what was discussed in the dropped slice — and injects it at the
head of the surviving conversation, so the mind keeps its continuity across the
cut without spending a single API token on summarizing.

IMPLEMENTATION NOTE (why this has no third-party dependencies)
--------------------------------------------------------------
An earlier version used sumy + nltk. That was replaced deliberately: nltk
requires `regex`, a COMPILED platform-specific extension, and the Mind bundle
shipped to bodies is a SINGLE platform-agnostic zip serving Windows, Linux and
macOS. A compiled dependency cannot ride that zip — it would work on one
platform and break on two, and carrying it in a signed Shell would mean buying a
code-sign for a summarizer.

So the summarizer is implemented in pure stdlib (harness/summarize.py): the same
LexRank algorithm, a few small functions, no compiled parts. The bridge is
portable by construction — it runs identically on every platform with nothing
beyond the standard library.

Design constraint: this must run on a user's laptop — no GPU, no model
downloads, no network. Pure stdlib satisfies that permanently.
"""

import logging
from typing import Optional

from .summarize import summarize as _summarize

log = logging.getLogger("galadriel.bridge")

# Optional secret redactor. If the harness ships one, use it; otherwise the
# bridge still works (it summarizes text that was already in the context).
try:  # pragma: no cover - trivial import guard
    from .redact import redact_secrets as _redact_secrets
except Exception:  # noqa: BLE001
    _redact_secrets = None

# Minimum text worth summarizing. Below this the bridge would be noise.
_MIN_TEXT = 200


def _messages_to_text(messages: list) -> str:
    """Convert a message list to plain text for the summarizer, with speakers.

    Images are NEVER inlined except as a short '[image]' placeholder — a base64
    blob would poison the summary AND cost an absurd number of tokens. Tool
    results are truncated to a head so a large payload never dominates.
    Non-dict entries are skipped rather than raising (defensive: a stray string
    from an older bug must not break a bridge).
    """
    lines = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        role = msg.get("role", "")

        if isinstance(content, list):
            texts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    texts.append(block.get("text", ""))
                elif block_type == "tool_result":
                    result = block.get("content", "")
                    if not isinstance(result, str):
                        result = str(result)
                    if len(result) > 200:
                        result = result[:200] + "..."
                    texts.append(f"[tool result: {result}]")
                elif block_type == "tool_use":
                    texts.append(f"[tool call: {block.get('name', '')}]")
                elif block_type in ("image", "image_url", "input_image"):
                    # Never include image data (base64 blobs, URLs) in the bridge
                    texts.append("[image]")
            content = " ".join(texts)
        elif not isinstance(content, str):
            content = str(content)

        content = content.strip()
        if not content or len(content) < 10:
            continue

        speaker = "User" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")

    return "\n".join(lines)


def build_bridge(messages: list, max_sentences: int = 5) -> Optional[str]:
    """Build a continuity bridge from a list of messages about to be dropped.

    Returns a string like "💬 ..." or None when the slice is too short to
    summarize usefully. Never raises — a trim must never break a live turn.
    """
    if not messages or len(messages) < 3:
        return None

    text = _messages_to_text(messages)
    if len(text) < _MIN_TEXT:
        return None

    try:
        summary = _summarize(text, max_sentences)
        if not summary:
            return None

        bridge = f"💬 {summary}"
        if _redact_secrets is not None:
            try:
                redacted = _redact_secrets(bridge)
                bridge = redacted[0] if isinstance(redacted, tuple) else redacted
            except Exception:  # noqa: BLE001 - redaction must never break a trim
                pass
        log.info(f"bridge: built bridge ({len(text)} chars → {len(bridge)} chars)")
        return bridge
    except Exception as e:  # noqa: BLE001 — summarization must never break a trim
        log.warning(f"bridge: summarization failed: {e}")
        return None


def inject_bridge(messages: list, bridge_text: str) -> list:
    """Prepend a bridge message to the conversation.

    The bridge is injected as a synthetic user message (a text block) so the
    API accepts it and the user→assistant alternation is preserved.

    SAFETY: returns the message list UNCHANGED if either argument is malformed
    — a bad bridge must never corrupt a live thread. The caller assigns the
    result; this function never splices in place (splicing a str is exactly the
    bug class this guards against).
    """
    if not bridge_text or not isinstance(bridge_text, str):
        return messages
    if not isinstance(messages, list):
        return messages

    bridge_msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    f"[Context bridge — summary of earlier conversation now "
                    f"outside the context window]\n\n{bridge_text}"
                ),
            }
        ],
    }
    return [bridge_msg] + messages
