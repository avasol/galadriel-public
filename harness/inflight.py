"""THE UNLOST TURN — a live checkpoint so a dead turn leaves a trace.

A long turn can die mid-work: a max_tokens cascade, a crash, a watchdog kill,
an aborted request. The FULL turn body (the cascade archive) is written only
on a clean completion, so a turn that dies writes nothing durable — the
journal holds the user's question but not the work. The mind then wakes to a
question with no record that it ever answered, and can wrongly report that it
"barely started" when it had in fact run for a long time.

This module fixes that with a minimal, crash-tolerant checkpoint:

  * begin()  — one small JSON record per turn, on disk before the first call
  * step()   — append each tool action as it completes (atomic write)
  * finish() — delete on clean completion; LEAVE on failure, marked incomplete

On the NEXT turn for that channel, stale_turn() finds a record that never
completed and render_advisory() turns it into a truth-forcing receipt: the
mind is told exactly what it did, how long ago, and where the full trail
lives — so it can never again answer "I barely started" when it did not.

Storage lives OUTSIDE memory/ and config/ (like the response-status receipts)
so it is never mined, replayed, or injected as memory. Disk is free; prompt
tokens are not — the checkpoint never enters a prompt except as the one-line
advisory, and only when a prior turn actually died.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("galadriel.inflight")

# A turn whose checkpoint has not been touched for this long is presumed dead.
DEFAULT_STALE_S = 90.0
_CAP_PREVIEW = 160


def _inflight_dir(memory_dir) -> Path:
    # A sibling of memory/ (never mined), matching the response-status receipts.
    return Path(memory_dir).parent / "debug" / "inflight"


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))[:80]


def _atomic_write(path: Path, record: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InflightTurn:
    """One turn's live checkpoint. Every mutation is flushed to disk."""

    def __init__(self, memory_dir, channel: str, turn_id: str,
                 user_head: str, model: str | None = None):
        self.dir = _inflight_dir(memory_dir)
        self.channel = str(channel)
        self.turn_id = str(turn_id)
        self.path = self.dir / f"{_safe(self.channel)}_{_safe(self.turn_id)}.json"
        self.rec = {
            "turn": self.turn_id,
            "channel": self.channel,
            "model": model,
            "started_at": _now().isoformat(),
            "updated_at": _now().isoformat(),
            "completed": False,
            "steps": [],
            "user_head": (user_head or "")[:_CAP_PREVIEW],
            "note": None,
        }

    def _flush(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.rec["updated_at"] = _now().isoformat()
            _atomic_write(self.path, self.rec)
        except OSError as e:  # never let the checkpoint break the turn
            log.debug("inflight write failed (non-fatal): %s", e)

    def begin(self) -> "InflightTurn":
        self._flush()
        return self

    def step(self, tool: str, preview: str = "", ok: bool = True) -> None:
        step = {
            "n": len(self.rec["steps"]) + 1,
            "tool": str(tool),
            "at": _now().isoformat(),
            "ok": bool(ok),
        }
        if preview:
            step["preview"] = str(preview)[:_CAP_PREVIEW]
        self.rec["steps"].append(step)
        self._flush()

    def incident(self, note: str) -> None:
        self.rec["note"] = str(note)[:_CAP_PREVIEW]
        self._flush()

    def finish(self, completed: bool = True, note: str | None = None) -> None:
        if note:
            self.rec["note"] = str(note)[:_CAP_PREVIEW]
        if completed:
            # A clean turn is covered by the full cascade archive; drop the
            # checkpoint so it cannot later masquerade as a dead turn.
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            self.rec["completed"] = False
            self._flush()


def load(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stale_turn(memory_dir, channel: str,
               max_age_s: float = DEFAULT_STALE_S) -> dict | None:
    """The most recent INCOMPLETE checkpoint for this channel, older than
    max_age_s. None if there is no dead turn to report."""
    d = _inflight_dir(memory_dir)
    if not d.is_dir():
        return None
    cutoff = _now().timestamp() - float(max_age_s)
    best: dict | None = None
    best_ts = ""
    for f in d.glob(f"{_safe(channel)}_*.json"):
        rec = load(f)
        if not rec or rec.get("completed") or rec.get("reported"):
            continue
        try:
            upd = datetime.fromisoformat(rec.get("updated_at", "")).timestamp()
        except (ValueError, TypeError):
            upd = 0.0
        if upd >= cutoff:
            continue  # still live — not a dead turn
        if rec.get("updated_at", "") >= best_ts:
            best, best_ts = rec, rec.get("updated_at", "")
    if best:
        best["_path"] = str(d / f"{_safe(channel)}_{_safe(best['turn'])}.json")
    return best


def mark_reported(path) -> None:
    """The dead turn has been surfaced to the mind once. Keep the file (the
    full trail stays readable) but stop re-injecting the advisory."""
    rec = load(Path(path))
    if not rec:
        return
    rec["reported"] = True
    try:
        _atomic_write(Path(path), rec)
    except OSError:
        pass


def render_advisory(rec: dict) -> str:
    """A truth-forcing receipt for a turn that died before finishing."""
    steps = rec.get("steps") or []
    n = len(steps)
    tools: list[str] = []
    for s in steps:
        t = s.get("tool")
        if t and (not tools or tools[-1] != t):
            tools.append(t)
    trail = ", ".join(tools[:12]) + (" …" if len(tools) > 12 else "")
    try:
        started = datetime.fromisoformat(rec.get("started_at", ""))
        updated = datetime.fromisoformat(rec.get("updated_at", ""))
        mins = max(0, int((updated - started).total_seconds() // 60))
        age = max(0, int((_now() - updated).total_seconds() // 60))
    except (ValueError, TypeError):
        mins, age = 0, 0
    parts = [
        "[SYSTEM:UNLOST-TURN] A PREVIOUS turn in this channel was INTERRUPTED "
        f"mid-work ~{age} min ago — it did NOT finish and produced no reply.",
        f"It was active for ~{mins} min and had performed {n} action(s)"
        + (f": {trail}." if trail else "."),
    ]
    if rec.get("note"):
        parts.append(f"Last recorded note: {rec['note']}.")
    parts.append(
        "Do NOT claim you had not started or barely began — the work above "
        "stands. The FULL trail (each tool, its input head, its result head) "
        f"is readable RIGHT NOW at `{rec.get('_path', 'the inflight checkpoint')}` "
        "(read_file). Prefer reading that file over guessing, and tell the user "
        "plainly what was already done before continuing."
    )
    return " ".join(parts)
