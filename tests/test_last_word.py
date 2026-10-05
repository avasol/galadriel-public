"""THE LAST WORD — termination-as-persistence. Sealed 2026-07-15.

Covers the covenant: manual/deliberate (wake armed) == no persist;
otherwise == persist by default, with a recovery wake outside the
nightly stop window.
"""

import signal
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from harness.last_word import (
    _should_arm_wake,
    compose_last_word,
    compose_wake_prompt,
    install,
)


def _scheduler(pending_wake=None, hb=False, interval=10, prompt=None):
    s = MagicMock()
    s.pending_wake = pending_wake
    s.heartbeat_enabled = hb
    s.heartbeat_interval = interval
    s.heartbeat_prompt = prompt
    return s


def _fire(agent, scheduler, sig=signal.SIGTERM):
    """Install handlers, capture them, fire once, restore originals."""
    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        install(agent, scheduler)
        handler = signal.getsignal(sig)
        # Neutralize the re-kill tail: patch os.kill via monkey substitution
        import harness.last_word as lw
        killed = {}
        real_kill = lw.os.kill
        lw.os.kill = lambda pid, s: killed.setdefault("sig", s)
        try:
            if sig == signal.SIGINT:
                with pytest.raises(KeyboardInterrupt):
                    handler(sig, None)
            else:
                handler(sig, None)
        finally:
            lw.os.kill = real_kill
        return killed
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)


# ── the window rule ──────────────────────────────────────────────

def test_daytime_arms_wake():
    assert _should_arm_wake(datetime(2026, 7, 15, 14, 30))


def test_no_quiet_window_by_default_always_arms(monkeypatch):
    monkeypatch.delenv("GALADRIEL_NIGHTLY_STOP_HOUR", raising=False)
    assert _should_arm_wake(datetime(2026, 7, 15, 23, 30))


def test_configured_nightly_window_does_not_arm(monkeypatch):
    monkeypatch.setenv("GALADRIEL_NIGHTLY_STOP_HOUR", "22")
    assert _should_arm_wake(datetime(2026, 7, 15, 21, 59))
    assert not _should_arm_wake(datetime(2026, 7, 15, 22, 0))
    assert not _should_arm_wake(datetime(2026, 7, 15, 23, 1))


def test_invalid_window_is_ignored(monkeypatch):
    monkeypatch.setenv("GALADRIEL_NIGHTLY_STOP_HOUR", "late")
    assert _should_arm_wake(datetime(2026, 7, 15, 23, 30))


# ── composition ──────────────────────────────────────────────────

def test_last_word_names_signal_and_journal():
    text = compose_last_word("SIGTERM", _scheduler(), datetime(2026, 7, 15, 14, 0))
    assert "SIGTERM" in text
    assert "journal/2026-07-15.jsonl" in text
    assert "Recovery wake ARMED" in text


def test_last_word_nightly_notes_no_wake(monkeypatch):
    monkeypatch.setenv("GALADRIEL_NIGHTLY_STOP_HOUR", "22")
    text = compose_last_word("SIGINT", _scheduler(), datetime(2026, 7, 15, 23, 0))
    assert "no wake armed" in text


def test_last_word_reports_heartbeat_topic():
    s = _scheduler(hb=True, interval=20, prompt="[SYSTEM:HEARTBEAT:BUILD_X] watch it")
    text = compose_last_word("SIGTERM", s, datetime(2026, 7, 15, 14, 0))
    assert "20m" in text and "SYSTEM:HEARTBEAT:BUILD_X" in text


def test_wake_prompt_is_self_contained():
    p = compose_wake_prompt("SIGTERM", datetime(2026, 7, 15, 14, 0))
    assert p.startswith("[SYSTEM:WAKE:UNPLANNED-SHUTDOWN]")
    assert "memory/2026-07-15.md" in p
    assert "palace_diary_read" in p


# ── the covenant, end to end ─────────────────────────────────────

def test_deliberate_shutdown_no_persist():
    """Manual == no: armed wake → nothing written, wake untouched."""
    agent = MagicMock()
    sched = _scheduler(pending_wake="[SYSTEM:WAKE:PLANNED] resume X")
    _fire(agent, sched)
    agent.memory.append_daily_log.assert_not_called()
    sched._save_state.assert_not_called()
    assert sched.pending_wake == "[SYSTEM:WAKE:PLANNED] resume X"


def test_unplanned_daytime_persists_and_arms(monkeypatch):
    import harness.last_word as lw
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 15, 14, 0)
    monkeypatch.setattr(lw, "datetime", _FakeDT)
    agent = MagicMock()
    sched = _scheduler(pending_wake=None)
    _fire(agent, sched)
    agent.memory.append_daily_log.assert_called_once()
    assert "LAST WORD (SIGTERM)" in agent.memory.append_daily_log.call_args[0][0]
    assert sched.pending_wake and "UNPLANNED-SHUTDOWN" in sched.pending_wake
    sched._save_state.assert_called_once()


def test_unplanned_nightly_persists_without_wake(monkeypatch):
    monkeypatch.setenv("GALADRIEL_NIGHTLY_STOP_HOUR", "22")
    import harness.last_word as lw
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 15, 23, 1)
    monkeypatch.setattr(lw, "datetime", _FakeDT)
    agent = MagicMock()
    sched = _scheduler(pending_wake=None)
    _fire(agent, sched)
    agent.memory.append_daily_log.assert_called_once()
    assert sched.pending_wake is None
    sched._save_state.assert_not_called()


def test_sigint_reraises_keyboard_interrupt():
    """discord.py's graceful close path must still run."""
    agent = MagicMock()
    sched = _scheduler(pending_wake="armed")
    _fire(agent, sched, sig=signal.SIGINT)  # raises inside; asserted there


def test_persist_failure_never_blocks_death(monkeypatch):
    """A broken persist must not swallow the shutdown."""
    agent = MagicMock()
    agent.memory.append_daily_log.side_effect = OSError("disk gone")
    sched = _scheduler(pending_wake=None)
    _fire(agent, sched)  # must not raise anything but complete the death path


def test_double_fire_persists_once(monkeypatch):
    import harness.last_word as lw
    agent = MagicMock()
    sched = _scheduler(pending_wake=None)
    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        install(agent, sched)
        handler = signal.getsignal(signal.SIGTERM)
        real_kill = lw.os.kill
        lw.os.kill = lambda pid, s: None
        try:
            handler(signal.SIGTERM, None)
            signal.signal(signal.SIGTERM, handler)  # simulate second delivery
            handler(signal.SIGTERM, None)
        finally:
            lw.os.kill = real_kill
        assert agent.memory.append_daily_log.call_count == 1
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)
