"""THE EAGER WAKE — a loading wake must not be silent.

A one-shot wake whose agent turn runs for minutes is, from the far side,
indistinguishable from a wake that never came back. So the wake speaks an
immediate one-liner the instant the cycle is confirmed clean, BEFORE the agent
turn runs; the turn's own report lands as a SECOND message.

Contract under test:
  1. Ordering — the eager line is delivered BEFORE the agent turn begins.
  2. Two-message contract — the eager line AND the turn's report both send.
  3. No false completion — the eager line never claims the work is done.
  4. Topic slug surfaces when the prompt carries one, and is absent otherwise.
  5. Non-fatal — a failed eager send must not abort the turn.
  6. Clear-after-success — pending_wake is cleared only after the turn runs.
"""

import asyncio
from unittest.mock import MagicMock

from harness.scheduler import Scheduler, WAKE_GRACE_SECONDS


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _scheduler():
    s = Scheduler.__new__(Scheduler)
    s.pending_wake = None
    s._state_path = MagicMock()
    return s


def test_topic_slug_extracted():
    assert Scheduler._wake_topic("[SYSTEM:WAKE:MAC-PARITY] go") == "MAC-PARITY"
    assert Scheduler._wake_topic("[SYSTEM:WAKE: EAGER-WAKE ] x") == "EAGER-WAKE"
    assert Scheduler._wake_topic("no marker here") == ""


def test_eager_line_never_claims_completion():
    s = _scheduler()
    line = s._render_wake_eager("[SYSTEM:WAKE:X] resume")
    low = line.lower()
    # It may say "soon" / "follows", never that the work is DONE.
    assert "done" not in low
    assert "complete" not in low
    assert "finished" not in low
    assert "follows" in low


def test_eager_line_surfaces_topic():
    s = _scheduler()
    assert "Resume: X." in s._render_wake_eager("[SYSTEM:WAKE:X] resume")
    assert "Resume:" not in s._render_wake_eager("plain prompt, no slug")


def test_eager_delivered_before_agent_turn(monkeypatch):
    """The ordering is the whole point: the eager line lands BEFORE the turn."""
    calls = []

    async def _fake_discord(message, channel_id=None):
        calls.append(("eager", channel_id))

    async def _fake_turn(prompt, channel_id):
        calls.append(("turn", channel_id))
        return True

    async def _no_sleep(*a, **k):
        return None

    s = _scheduler()
    s.pending_wake = "[SYSTEM:WAKE:ORDERING] go"
    monkeypatch.setattr(s, "_send_to_discord", _fake_discord)
    monkeypatch.setattr(s, "_send_agent_message", _fake_turn)
    monkeypatch.setattr(s, "_save_state", lambda: None)
    monkeypatch.setattr("harness.scheduler.asyncio.sleep", _no_sleep)

    run(s._wake_loop())

    assert [c[0] for c in calls] == ["eager", "turn"]
    assert calls[0][1] == "wake"
    assert calls[1][1] == "wake"


def test_failed_eager_is_non_fatal_and_turn_still_runs(monkeypatch):
    calls = []

    async def _boom(message, channel_id=None):
        calls.append("eager-failed")
        raise RuntimeError("discord down")

    async def _fake_turn(prompt, channel_id):
        calls.append("turn")
        return True

    async def _no_sleep(*a, **k):
        return None

    s = _scheduler()
    s.pending_wake = "[SYSTEM:WAKE:X] go"
    monkeypatch.setattr(s, "_send_to_discord", _boom)
    monkeypatch.setattr(s, "_send_agent_message", _fake_turn)
    monkeypatch.setattr(s, "_save_state", lambda: None)
    monkeypatch.setattr("harness.scheduler.asyncio.sleep", _no_sleep)

    run(s._wake_loop())
    assert "turn" in calls  # a missing greeting must not cost the real report


def test_wake_cleared_only_after_success(monkeypatch):
    s = _scheduler()
    sent = []

    async def _fake_discord(message, channel_id=None):
        sent.append(message)

    async def _fake_turn(prompt, channel_id):
        # The wake must still be armed WHILE the turn runs — it clears after.
        assert s.pending_wake == "[SYSTEM:WAKE:X] go"
        return True

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(s, "_send_to_discord", _fake_discord)
    monkeypatch.setattr(s, "_send_agent_message", _fake_turn)
    monkeypatch.setattr(s, "_save_state", lambda: None)
    monkeypatch.setattr("harness.scheduler.asyncio.sleep", _no_sleep)

    s.pending_wake = "[SYSTEM:WAKE:X] go"
    run(s._wake_loop())

    assert s.pending_wake is None
    assert len(sent) == 1  # the single eager line


def test_grace_constant_is_eight():
    assert WAKE_GRACE_SECONDS == 8
