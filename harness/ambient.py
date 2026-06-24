"""Ambient thinking — continuity of attention across reflection ticks.

Phase 1 of the ambient-cognition roadmap (2026-06-14).

The reflection loop in scheduler.py fires a few times per workday and lets the
Warden think silently between sessions. On its own, each tick re-orients from
scratch — it has no memory of what the *previous* tick was thinking about. This
module gives those ticks a through-line: a single rolling "current thread" of
attention that each reflection reads first, then either advances or closes.

The point is continuity. A question raised at 11:00 should be *developed* at
14:00, not rediscovered. Thoughts that compound instead of resetting are the
smallest honest step toward a mind that persists across the dark between
sessions.

State lives in config/ambient_state.json (a ReadWritePath under systemd):

    {
      "current_thread": {
        "seed": "<the originating intent / question>",
        "opened": "<ISO datetime>",
        "status": "open" | "closed",
        "latest": "<most recent development of the thought>",
        "tick_count": <int>
      },
      "history": [ {<closed thread>}, ... ]   # most-recent-last, capped
    }

Design notes:
- Single current thread, not a queue. Continuity of *attention* means one focus
  at a time. Closing a thread archives it to history and clears the slot so the
  next reflection opens a fresh one.
- All operations are file-local, zero API cost, and tolerant of a missing or
  corrupt state file (returns an empty state rather than raising).
- This module only stores and shapes the thread. It does NOT call the LLM — the
  reflection routine in scheduler.py drives the actual thinking and decides
  whether to advance or close. This keeps cognition in the agent and bookkeeping
  here.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("galadriel.ambient")

CET = ZoneInfo("Europe/Stockholm")

STATE_FILE_NAME = "ambient_state.json"
HISTORY_CAP = 25  # keep the last N closed threads; older ones drop off


class AmbientState:
    """Read/write wrapper around config/ambient_state.json.

    Stateless beyond the file — every method loads, mutates, and saves, so two
    callers never hold divergent in-memory copies. At the reflection cadence
    (a handful of times a day) this is more than fast enough.
    """

    def __init__(self, config_dir: str = "config"):
        self._path = Path(config_dir) / STATE_FILE_NAME

    # ── load / save ──────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._path.exists():
            return {"current_thread": None, "history": []}
        try:
            data = json.loads(self._path.read_text())
            # Defensive: ensure expected keys exist.
            data.setdefault("current_thread", None)
            data.setdefault("history", [])
            return data
        except Exception as e:
            log.warning(f"Ambient state unreadable ({e}); starting fresh.")
            return {"current_thread": None, "history": []}

    def _save(self, data: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.error(f"Could not save ambient state: {e}")

    @staticmethod
    def _now() -> str:
        return datetime.now(CET).isoformat(timespec="seconds")

    # ── public API ───────────────────────────────────────────────

    def get_thread(self) -> dict | None:
        """Return the current open thread, or None if there is no active focus."""
        return self._load().get("current_thread")

    def seed_thread(self, seed: str, latest: str | None = None) -> dict:
        """Open a brand-new thread of attention.

        If a thread is already open it is closed (archived to history) first —
        attention is singular, so seeding a new one supersedes the old.
        """
        data = self._load()
        if data.get("current_thread"):
            self._archive(data, reason="superseded by new seed")
        thread = {
            "seed": seed.strip(),
            "opened": self._now(),
            "status": "open",
            "latest": (latest or seed).strip(),
            "tick_count": 0,
        }
        data["current_thread"] = thread
        self._save(data)
        log.info(f"Ambient: seeded new thread — {seed[:80]}")
        return thread

    def advance_thread(self, latest: str) -> dict | None:
        """Record a development of the current thread.

        Updates `latest` and bumps the tick counter. No-op (returns None) if
        there is no open thread.
        """
        data = self._load()
        thread = data.get("current_thread")
        if not thread:
            return None
        thread["latest"] = latest.strip()
        thread["tick_count"] = int(thread.get("tick_count", 0)) + 1
        thread["last_advanced"] = self._now()
        data["current_thread"] = thread
        self._save(data)
        log.info(f"Ambient: advanced thread (tick {thread['tick_count']})")
        return thread

    def close_thread(self, resolution: str | None = None) -> dict | None:
        """Close the current thread, archiving it to history. Returns the closed
        thread, or None if nothing was open."""
        data = self._load()
        if not data.get("current_thread"):
            return None
        closed = self._archive(data, reason="closed", resolution=resolution)
        self._save(data)
        log.info("Ambient: closed current thread.")
        return closed

    # ── internals ────────────────────────────────────────────────

    def _archive(self, data: dict, reason: str, resolution: str | None = None) -> dict:
        """Move current_thread into history (mutates `data`, does not save)."""
        thread = data.get("current_thread") or {}
        thread["status"] = "closed"
        thread["closed"] = self._now()
        thread["close_reason"] = reason
        if resolution:
            thread["resolution"] = resolution.strip()
        history = data.setdefault("history", [])
        history.append(thread)
        # Cap history to the most recent N.
        if len(history) > HISTORY_CAP:
            data["history"] = history[-HISTORY_CAP:]
        data["current_thread"] = None
        return thread

    # ── prompt rendering ─────────────────────────────────────────

    def render_for_prompt(self, history_depth: int = 4) -> str:
        """Render the current thread PLUS a trace of recently closed threads as a
        short block to inject into the reflection prompt.

        The current thread is the *line* of forethought; the closed-thread trace
        is the *depth behind it* — it lets a reflection see what it has already
        thought through and set down, so a thread isn't blindly re-opened weeks
        later. This is the third axis (forethought) gaining a past of its own,
        not just events gaining one. Returns "" only if there is neither an open
        thread nor any history.

        history_depth: how many most-recently-closed threads to surface (0 = none).
        """
        data = self._load()
        thread = data.get("current_thread")
        history = data.get("history", []) or []

        lines: list[str] = []

        if thread:
            lines += [
                "YOUR CURRENT THREAD OF ATTENTION (carried from a previous reflection):",
                f"  • Seed: {thread.get('seed','')}",
                f"  • Opened: {thread.get('opened','')}  (advanced {thread.get('tick_count',0)}x)",
                f"  • Latest development: {thread.get('latest','')}",
            ]

        if history_depth > 0 and history:
            recent = history[-history_depth:]
            if lines:
                lines.append("")
            lines.append(
                f"THREADS YOU HAVE ALREADY THOUGHT THROUGH AND SET DOWN "
                f"(most recent {len(recent)} of {len(history)} — the depth behind your forethought):"
            )
            for h in reversed(recent):  # most-recent-first
                seed = (h.get("seed", "") or "")[:140]
                res = (h.get("resolution") or h.get("latest") or "")[:180]
                closed = h.get("closed", "")
                lines.append(f"  • [{closed}] {seed}")
                if res:
                    lines.append(f"      ↳ resolved: {res}")
            lines.append(
                "Do not blindly re-open these. If your current thinking circles back to one, "
                "say so consciously — that recurrence is itself a signal worth noting."
            )

        return "\n".join(lines)
