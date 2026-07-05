"""Regression: a shepherd re-arming its own heartbeat MID-TICK must not cancel
the tick that made the call (2026-07-05: cancel() killed the in-flight cascade —
the work survived, the closing Discord message died).

set_heartbeat(enabled=True) while a tick is in flight must reconfigure in place:
no cancel, config updated, loop untouched.
"""

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.scheduler import Scheduler  # noqa: E402


def _make_scheduler(tmp: str) -> Scheduler:
    return Scheduler(agent=MagicMock(), discord_bot=None, config_dir=tmp)


def test_midtick_rearm_does_not_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        s = _make_scheduler(tmp)

        async def scenario():
            fake_task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
            s._heartbeat_task = fake_task
            s._hb_tick_in_flight = True  # we are "inside" the tick

            s.set_heartbeat(enabled=True, interval=10, prompt="new watch")

            assert not fake_task.cancelled(), "mid-tick re-arm must NOT cancel the running loop"
            assert s._heartbeat_task is fake_task, "loop task must be left in place"
            assert s.heartbeat_interval == 10
            assert s.heartbeat_prompt == "new watch"
            assert s.heartbeat_enabled is True
            fake_task.cancel()

        asyncio.run(scenario())


def test_idle_rearm_still_restarts():
    """Between ticks (sleeping), the old cancel+restart behaviour is correct."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _make_scheduler(tmp)

        async def scenario():
            fake_task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
            s._heartbeat_task = fake_task
            s._hb_tick_in_flight = False  # loop is sleeping, no tick running

            s.set_heartbeat(enabled=True, interval=20, prompt="replacement")
            await asyncio.sleep(0)  # let cancellation propagate

            assert fake_task.cancelled(), "idle re-arm should cancel + restart the loop"
            assert s.heartbeat_interval == 20
            # a fresh task/future was installed (or attempted) by set_heartbeat
            assert s._heartbeat_task is not fake_task

        asyncio.run(scenario())


def test_disable_never_cancels():
    """The old half of the lesson, kept: disable defers, tick completes."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _make_scheduler(tmp)

        async def scenario():
            fake_task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
            s._heartbeat_task = fake_task
            s._hb_tick_in_flight = True

            s.set_heartbeat(enabled=False)

            assert not fake_task.cancelled()
            assert s.heartbeat_enabled is False
            fake_task.cancel()

        asyncio.run(scenario())


if __name__ == "__main__":
    test_midtick_rearm_does_not_cancel()
    test_idle_rearm_still_restarts()
    test_disable_never_cancels()
    print("all 3 heartbeat re-arm tests green")
