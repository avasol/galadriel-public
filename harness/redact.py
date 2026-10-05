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
import threading

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
        r"(?im)(?:^|[\s:])[ \t]*(?:export\s+)?([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|AEDK)[A-Z0-9_]*)"
        r"\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?\s*$"), 2),
    # The Aedelgard registration key, matched by its VALUE not its context, so
    # it is caught in EVERY shape — bare, as an env line, behind a
    # `path:line:` prefix. Added 2026-09-30 (a reviewer's finding): AEDELGARD_AEDK
    # carries none of the words in the env_value name list, so the one secret a
    # body most needs veiled slipped through every shape. `aedk_` is the normal
    # mint; `grk_` is admin-issued. The value is opaque (the broker hashes the
    # whole string), so length is the only guarantee we lean on.
    ("aedelgard_key", re.compile(r"\b(?:aedk|grk)_[A-Za-z0-9_\-]{16,}\b"), 0),
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


# ── THE VALUE REGISTRY (2026-09-30) ─────────────────────────────────────────
# The pattern set recognises secrets by SHAPE and by NAME. A secret echoed with
# NEITHER — a bare token inside code, a default argument, an env var expanded by
# the shell — is invisible to it, no matter how many patterns we add. The value
# is high-entropy but arbitrary; there is nothing in it to recognise.
#
# What we DO know, and the pattern engine cannot: the exact secret VALUES this
# process already holds. So we keep a process-memory registry of them and match
# by exact value as a second pass, closing the class the patterns cannot.
#
# Safety (see harness/VEIL_VALUE_REGISTRY.md): process memory ONLY, never
# written to disk, never emitted, never logged by value; minimum length 16 so
# short config values are never registered; empty registry is a no-op.
_MIN_REGISTERED_LEN = 16
_SECRET_NAME_HINT = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|AEDK)", re.I)
_registry_lock = threading.Lock()
_secret_registry: set[str] = set()


def register_secret(value: str) -> bool:
    """Add one exact secret value to the process registry. Never raises;
    ignores short or placeholder values. Returns True if newly registered."""
    try:
        if not value or not isinstance(value, str):
            return False
        v = value.strip()
        if len(v) < _MIN_REGISTERED_LEN or _is_placeholder(v):
            return False
        with _registry_lock:
            if v in _secret_registry:
                return False
            _secret_registry.add(v)
            return True
    except Exception:
        return False


def set_registry(values) -> int:
    """Replace the registry with `values`. Returns the count kept. Called at
    boot with the values read from .env + the unsealed keyring."""
    global _secret_registry
    kept = set()
    for v in values or ():
        try:
            if isinstance(v, str):
                vv = v.strip()
                if len(vv) >= _MIN_REGISTERED_LEN and not _is_placeholder(vv):
                    kept.add(vv)
        except Exception:
            continue
    with _registry_lock:
        _secret_registry = kept
    return len(kept)


def registry_size() -> int:
    with _registry_lock:
        return len(_secret_registry)


def collect_env_secrets(env_text: str) -> list:
    """Values from a .env whose NAME looks like a secret. Conservative: only
    secret-named lines, so an ordinary config value is never registered."""
    out = []
    try:
        for line in (env_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            name = name.strip()
            if name.startswith("export "):
                name = name[7:].strip()
            val = val.strip().strip("'\"")
            if name and _SECRET_NAME_HINT.search(name) and len(val) >= _MIN_REGISTERED_LEN:
                out.append(val)
    except Exception:
        pass
    return out


def collect_environ_secrets(environ) -> list:
    """Secret VALUES from a process environment mapping, by NAME hint. The
    process already holds the live secrets (ANTHROPIC_API_KEY, AEDELGARD_AEDK,
    tokens...), so this is the simplest true source for the registry — no file
    read, no path resolution, and it captures whatever the .env loaded."""
    out = []
    try:
        for k, v in (environ or {}).items():
            if not k or not isinstance(v, str):
                continue
            if _SECRET_NAME_HINT.search(str(k)) and len(v.strip()) >= _MIN_REGISTERED_LEN:
                out.append(v.strip())
    except Exception:
        pass
    return out


def _apply_registry(text: str, hits: list) -> str:
    """Second pass: replace every registered value, longest first, so a secret
    that is a substring of another is not half-replaced."""
    with _registry_lock:
        values = sorted(_secret_registry, key=len, reverse=True)
    for v in values:
        if v and v in text:
            fp = _fp(v)
            text = text.replace(v, f"<REDACTED:known:{fp}>")
            hits.append(("known", fp))
    return text


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
    # THE VALUE REGISTRY: exact-value second pass over the values this
    # process already holds. Empty registry => no-op.
    text = _apply_registry(text, hits)
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
