"""Conversation bridge — local, zero-API-cost summarization for trim continuity.

When _trim_history drops messages from the front of a conversation, those
messages are lost forever. This module produces a short "bridge" summary —
a few sentences of what was discussed in the dropped slice — so the agent
retains continuity without burning API tokens on summarization.

Uses sumy LexRank (extractive summarization) — pure Python, no GPU, no API key,
pip-installable anywhere. The bridge is not beautiful poetry; it is factual
continuity at zero marginal cost.

Design constraint: must run on a laptop with no GPU and no model downloads.
sumy + nltk satisfies this. BOTH sumy and an optional secret-redactor are
treated as OPTIONAL: if either is absent the bridge degrades to a silent drop,
because a trim must never break a live turn.
"""

import logging
from typing import Optional

log = logging.getLogger("galadriel.bridge")

# Optional secret redactor. If the harness ships one, use it; otherwise the
# bridge still works (it summarizes text that was already in the context).
try:  # pragma: no cover - trivial import guard
    from .redact import redact_secrets as _redact_secrets
except Exception:  # noqa: BLE001
    _redact_secrets = None

# Lazy imports — sumy is only imported when bridging actually happens
_SUMY_LOADED = False
_PARSER = None
_LEX_RANK = None
_TOKENIZER = None


def _ensure_sumy() -> bool:
    """Lazy-load sumy on first use. Returns True if available."""
    global _SUMY_LOADED, _PARSER, _LEX_RANK, _TOKENIZER
    if _SUMY_LOADED:
        return _PARSER is not None

    try:
        from sumy.parsers.plaintext import PlaintextParser
        from sumy.nlp.tokenizers import Tokenizer
        from sumy.summarizers.lex_rank import LexRankSummarizer
        import nltk

        # Ensure punkt is available (silent)
        try:
            nltk.data.find("tokenizers/punkt_tab")
        except LookupError:
            nltk.download("punkt_tab", quiet=True)

        _PARSER = PlaintextParser
        _LEX_RANK = LexRankSummarizer
        _TOKENIZER = Tokenizer
        _SUMY_LOADED = True
        log.info("bridge: sumy LexRank loaded (local, zero-API)")
        return True
    except Exception as e:  # ImportError, LookupError, download failure
        log.warning(f"bridge: sumy unavailable ({e}) — trim bridges disabled")
        _SUMY_LOADED = True  # Don't retry
        return False


def _messages_to_text(messages: list) -> str:
    """Convert message list to plain text for sumy, with speaker labels.

    Images are NEVER inlined (a base64 blob would poison the summary); they
    become a short '[image]' placeholder. Tool results are truncated to a head.
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

    Returns a string like "💬 ..." or None when sumy is unavailable or the
    input is too short to summarize.
    """
    if not messages or len(messages) < 3:
        return None

    if not _ensure_sumy():
        return None

    text = _messages_to_text(messages)
    if len(text) < 200:
        return None

    try:
        parser = _PARSER.from_string(text, _TOKENIZER("english"))
        summarizer = _LEX_RANK()
        sentence_count = min(max_sentences, len(parser.document.sentences))
        if sentence_count < 1:
            return None

        extracted = summarizer(parser.document, sentence_count)
        if not extracted:
            return None

        # Sort extracted sentences by their original position for coherence
        sentences = sorted(
            extracted,
            key=lambda s: text.index(str(s)) if str(s) in text else 0,
        )
        bridge_lines = [str(s).strip() for s in sentences if str(s).strip()]
        if not bridge_lines:
            return None

        raw_bridge = "💬 " + " ".join(bridge_lines)
        if _redact_secrets is not None:
            try:
                redacted = _redact_secrets(raw_bridge)
                raw_bridge = redacted[0] if isinstance(redacted, tuple) else redacted
            except Exception:  # noqa: BLE001 - redaction must never break a trim
                pass
        log.info(
            f"bridge: built {len(bridge_lines)}-sentence bridge "
            f"({len(text)} chars → {len(raw_bridge)} chars)"
        )
        return raw_bridge

    except Exception as e:
        log.warning(f"bridge: summarization failed: {e}")
        return None


def inject_bridge(messages: list, bridge_text: str) -> list:
    """Prepend a bridge message to the conversation.

    The bridge is injected as a synthetic user message so the API accepts it
    (assistant → user → assistant alternation is preserved).

    SAFETY: returns the message list UNCHANGED if the input is malformed — a
    bad bridge must never corrupt a live thread. The caller assigns the result
    (it does NOT splice in place).
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
