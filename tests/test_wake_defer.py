"""Regression: arm_wake must NOT live-fire by default.

A wake is almost always armed immediately before a planned restart. The old
behaviour kicked the wake loop live on arming, so the wake fired ~8s later on
the process that was about to die, self-cleared, and the restarted instance
woke with no context. Deferring to the next scheduler start is the contract.
"""

import json
from unittest.mock import MagicMock, patch

from harness.scheduler import Scheduler


def _scheduler(tmp_path):
    s = Scheduler.__new__(Scheduler)
    s.pending_wake = None
    s.heartbeat_enabled = False
    s.heartbeat_interval = 10
    s.heartbeat_prompt = None
    s._state_path = tmp_path / "scheduler_state.json"
    s._wake_task = None
    s._loop = MagicMock()
    s._loop.is_running.return_value = True
    return s


def test_default_arm_is_deferred(tmp_path):
    s = _scheduler(tmp_path)
    with patch("harness.scheduler.asyncio.run_coroutine_threadsafe") as rct:
        s.arm_wake("[SYSTEM:WAKE:X] resume")
        rct.assert_not_called()
    assert s.pending_wake == "[SYSTEM:WAKE:X] resume"
    saved = json.loads(s._state_path.read_text())
    assert saved["pending_wake"] == "[SYSTEM:WAKE:X] resume"


def test_explicit_live_fires_in_process(tmp_path):
    s = _scheduler(tmp_path)
    with patch("harness.scheduler.asyncio.run_coroutine_threadsafe") as rct:
        s.arm_wake("[SYSTEM:WAKE:X] ping me shortly", live=True)
        rct.assert_called_once()
    assert s.pending_wake == "[SYSTEM:WAKE:X] ping me shortly"


def test_disarm_never_kicks(tmp_path):
    s = _scheduler(tmp_path)
    s.pending_wake = "old"
    with patch("harness.scheduler.asyncio.run_coroutine_threadsafe") as rct:
        s.arm_wake("", live=True)
        rct.assert_not_called()
    assert s.pending_wake is None
