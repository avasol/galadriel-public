"""EDGE core: the pure parts of the desk widget's server side.

The Edge token, signed file links, turn tracking, the push outbox and the
widget trace (Edge Protocol v1.1, see docs/EDGE.md). No Flask here;
tower/edge.py wires these into routes.
"""
from __future__ import annotations

import base64
import collections
import hmac
import json
import secrets
import threading
import uuid
from hashlib import sha256

_FILE_KEY_INFO = b"edge-file-v1"
_MAX_NAME = 200
_MAX_SIGNED = 2048
_MAX_EVENTS = 100
_MAX_KEYS = 12
_MAX_KEY_LEN = 40
_MAX_STR = 300
_TRACE_CAP = 5 * 1024 * 1024


def new_token() -> str:
    """A fresh 32-byte url-safe Edge token."""
    return secrets.token_urlsafe(32)


def token_ok(expected, supplied) -> bool:
    """Constant-time token comparison. Falsy/non-str on either side fails."""
    if not expected or not supplied:
        return False
    if not isinstance(expected, str) or not isinstance(supplied, str):
        return False
    return hmac.compare_digest(expected.encode("utf-8"),
                               supplied.encode("utf-8"))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _name_ok(name) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if len(name) > _MAX_NAME:
        return False
    if name.startswith("."):
        return False
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        return False
    return True


def sign_file(token: str, name: str, now, ttl: int = 86400) -> str:
    """Sign a bare filename into a short-lived `payload.mac` link."""
    if not _name_ok(name):
        raise ValueError(f"refused file name: {name!r}")
    payload = _b64e(json.dumps(
        {"name": name, "exp": int(now) + ttl},
        separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    key = hmac.new(token.encode("utf-8"), _FILE_KEY_INFO, sha256).digest()
    mac = _b64e(hmac.new(key, payload.encode("utf-8"), sha256).digest())
    return payload + "." + mac


def verify_file(token, signed, now):
    """Return the filename for a valid link, else None. Never raises."""
    try:
        if not token or not isinstance(signed, str):
            return None
        if len(signed) > _MAX_SIGNED or signed.count(".") != 1:
            return None
        payload, mac = signed.split(".", 1)
        key = hmac.new(token.encode("utf-8"), _FILE_KEY_INFO, sha256).digest()
        expected = _b64e(hmac.new(key, payload.encode("utf-8"), sha256).digest())
        if not hmac.compare_digest(expected, mac):
            return None
        data = json.loads(_b64d(payload).decode("utf-8"))
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        exp = data.get("exp")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            return None
        if exp < now:
            return None
        if not _name_ok(name):
            return None
        return name
    except Exception:
        return None


class TurnTracker:
    """Thread-safe turn bookkeeping for the widget's poll/ack loop."""

    def __init__(self) -> None:
        self.epoch = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._latest = 0
        self._done: list = []
        self._acked: set = set()

    def begin(self) -> int:
        with self._lock:
            self._latest += 1
            return self._latest

    def end(self, turn_id) -> None:
        with self._lock:
            if turn_id in self._done:
                return
            self._done.append(turn_id)
            if len(self._done) > 20:
                self._done = self._done[-20:]
                self._acked &= set(self._done)

    def ack(self, turn_id) -> bool:
        with self._lock:
            if turn_id in self._done:
                self._acked.add(turn_id)
                return True
            return False

    def view(self):
        with self._lock:
            if self._latest == 0:
                return None
            return {
                "latest": self._latest,
                "done": list(self._done),
                "unacked": [t for t in self._done if t not in self._acked],
                "epoch": self.epoch,
            }


class Outbox:
    """Thread-safe bounded FIFO of mind-initiated messages."""

    def __init__(self, limit: int = 50) -> None:
        self._lock = threading.Lock()
        self._items: collections.deque = collections.deque(maxlen=limit)

    def push(self, message, title: str = "") -> None:
        with self._lock:
            self._items.append({
                "type": "message",
                "message": str(message),
                "title": str(title or ""),
            })

    def pop(self):
        with self._lock:
            if not self._items:
                return None
            return self._items.popleft()


def clean_trace(data, recv) -> list:
    """Bound and sanitise a widget-side event log into JSON lines."""
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    boot = str(data.get("boot") or "")[:40]
    version = str(data.get("version") or "")[:20]
    excluded = ("recv", "boot", "version")
    lines = []
    for ev in events[:_MAX_EVENTS]:
        if not isinstance(ev, dict):
            continue
        row = {}
        for key, value in ev.items():
            if len(row) >= _MAX_KEYS:
                break
            if not isinstance(key, str) or len(key) > _MAX_KEY_LEN:
                continue
            if key in excluded:
                continue
            if isinstance(value, str):
                row[key] = value[:_MAX_STR]
            elif value is None or isinstance(value, (bool, int, float)):
                row[key] = value
        lines.append(json.dumps(
            {"recv": recv, "boot": boot, "version": version, **row},
            ensure_ascii=False))
    return lines


def write_trace(directory, lines, day) -> int:
    """Append trace lines to `day.jsonl`, capped at 5 MB per day."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (day + ".jsonl")
    if path.exists() and path.stat().st_size >= _TRACE_CAP:
        return 0
    if not lines:
        return 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(lines)


# ── the token file (this engine has no keyring) ──────────────────────

_MIN_TOKEN = 24


def load_token(path):
    """The Edge token: EDGE_TOKEN env wins, else the 0600 file. None if unset."""
    import os
    env = (os.environ.get("EDGE_TOKEN") or "").strip()
    if env:
        return env if len(env) >= _MIN_TOKEN else None
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError:
        return None
    text = text.strip()
    return text if len(text) >= _MIN_TOKEN else None


def save_token(path, tok) -> None:
    """Write the token to `path` with mode 0600, creating parent dirs."""
    import os
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, str(tok).encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(str(p), 0o600)
