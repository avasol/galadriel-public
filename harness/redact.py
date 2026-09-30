"""redact.py — THE VEIL: secrets never enter conversation history.

A secret that enters a tool result does not stay there. It is faithfully
persisted by every downstream organ — history, journal, cascade archives,
prompt debug dumps, and the memory index — and re-sent to the model on every
later turn. One ``cat keys/…`` or ``git config -l`` is enough to seed copies
that outlive the key.

The fix sits at the single choke point: a tool result is redacted BEFORE it
is appended to the conversation, so the model never sees the secret (and so
cannot echo it), and every organ downstream inherits clean text without
having to know it was ever there.

Design rules
  * Pure functions, no I/O, no imports beyond stdlib. Safe to call anywhere.
  * Replacement is legible: ``<REDACTED:kind:fp6>`` where fp6 is the first
    six hex chars of sha256(secret). The SAME secret always yields the SAME
    marker, so a reader can still tell "this token here is that token there"
    without ever seeing it.
  * Patterns favour recall over precision for high-entropy provider formats
    (a redacted placeholder is harmless; a persisted live key is not), and
    stay conservative for the generic KEY=value rule (value must be ≥ 8
    chars, no whitespace, and the key name must say secret/token/password/
    api_key) so ordinary env dumps keep their shape.
  * Inbound user text (e.g. a key pasted into chat) can reuse
    ``redact_secrets`` unchanged — the other half of the same veil.
"""
from __future__ import annotations

import hashlib
import re

__all__ = ["redact_secrets", "redact_text", "find_secrets"]

# (kind, compiled regex, group index holding the secret)
_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), 0),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), 0),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), 0),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), 0),
    ("openai", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}"), 0),
    ("elevenlabs", re.compile(r"\bsk_[a-f0-9]{32,}\b"), 0),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), 0),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), 0),
    ("aws_secret", re.compile(
        r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})"), 1),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), 0),
    ("discord_bot", re.compile(r"\b[MN][A-Za-z\d]{23,}\.[\w\-]{6}\.[\w\-]{27,}\b"), 0),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), 0),
    ("stripe", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), 0),
    ("nebius", re.compile(r"\bv1\.[A-Za-z0-9_\-]{30,}"), 0),
    # Generic env-style assignment: NAME_WITH_SECRET_WORD = value  (value kept
    # opaque; the name survives so `cat .env` output stays readable).
    ("env_value", re.compile(
        r"(?im)^\s*(?:export\s+)?([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY)[A-Z0-9_]*)"
        r"\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?\s*$"), 2),
    # URL query-string tokens: ?token=…  &api_key=…
    ("url_token", re.compile(
        r"(?i)[?&](?:token|api_key|apikey|access_token|key)=([A-Za-z0-9_\-\.%]{16,})"), 1),
]

_PLACEHOLDER_HINTS = ("REDACTED", "xxxx", "XXXX", "your_", "YOUR_", "<", "…", "****", "changeme", "example")


def _fp(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8", "replace")).hexdigest()[:6]


def _is_placeholder(secret: str) -> bool:
    return any(h in secret for h in _PLACEHOLDER_HINTS)


def find_secrets(text: str) -> list[tuple[str, str]]:
    """Return [(kind, secret)] found in text, in scan order. No mutation."""
    found: list[tuple[str, str]] = []
    for kind, rx, grp in _PATTERNS:
        for m in rx.finditer(text):
            s = m.group(grp)
            if s and not _is_placeholder(s):
                found.append((kind, s))
    return found


def redact_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Redact every secret in `text`. Returns (clean_text, [(kind, fp6)])."""
    if not text or not isinstance(text, str):
        return text, []
    hits: list[tuple[str, str]] = []
    for kind, rx, grp in _PATTERNS:
        def _sub(m, kind=kind, grp=grp):
            s = m.group(grp)
            if not s or _is_placeholder(s):
                return m.group(0)
            fp = _fp(s)
            hits.append((kind, fp))
            marker = f"<REDACTED:{kind}:{fp}>"
            if grp == 0:
                return marker
            start, end = m.start(grp) - m.start(0), m.end(grp) - m.start(0)
            whole = m.group(0)
            return whole[:start] + marker + whole[end:]
        text = rx.sub(_sub, text)
    return text, hits


def redact_secrets(result):
    """Redact a tool result in whatever shape execute_tool returned it.

    * str                       → redacted str
    * list of content blocks    → text blocks redacted, others untouched
    * anything else             → returned as-is
    Returns (clean_result, hits) so the caller can log fingerprints.
    """
    if isinstance(result, str):
        return redact_text(result)
    if isinstance(result, list):
        all_hits: list[tuple[str, str]] = []
        out = []
        for b in result:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                clean, hits = redact_text(b["text"])
                all_hits += hits
                out.append({**b, "text": clean})
            else:
                out.append(b)
        return out, all_hits
    return result, []
