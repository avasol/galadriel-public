"""Acceptance tests (planner-written, protected): ONE RESET — every path that starts a fresh thread clears the
same per-thread state, through one function.

Why: clear_history, /new and rollover each kept their own pop-list; /new missed
_frozen_system, so a fresh thread woke under a stale frozen system prompt
(old clock, old daily log), and _last_warn_tier survived every reset. These tests make a missed item impossible:
  1. every reset path routes through _reset_channel_state;
  2. _reset_channel_state clears every name in _THREAD_SCOPED_STATE, for that
     channel only;
  3. every per-channel dict initialised in __init__ is either thread-scoped or
     on the explicit keep-list — a new dict cannot be added silently.
"""
import asyncio
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import GaladrielAgent  # noqa: E402

# Survive a reset on purpose (see the comment above _THREAD_SCOPED_STATE).
KEEP = {"_last_turn_end", "_thinking_modes", "_summarized_display"}


def _agent():
    a = GaladrielAgent.__new__(GaladrielAgent)
    for name in GaladrielAgent._THREAD_SCOPED_STATE:
        setattr(a, name, {"chan": "stale", "other": "keep"})
    a.conversations = {"chan": [{"role": "user", "content": "hi"}]}
    a.journal = MagicMock()
    return a


def _assert_cleared(a):
    for name in GaladrielAgent._THREAD_SCOPED_STATE:
        d = getattr(a, name)
        assert "chan" not in d, f"{name} survived the reset"
        assert d.get("other") == "keep", f"{name}: another channel was touched"


def test_clear_history_clears_all_thread_state():
    a = _agent()
    a.clear_history("chan")
    _assert_cleared(a)


def test_new_clears_all_thread_state(monkeypatch):
    a = _agent()
    import harness.palace as palace

    async def _noop(*_a, **_k):
        return None
    monkeypatch.setattr(palace, "archive_conversation", _noop)
    assert asyncio.run(a.pop_and_archive_history("chan")) == 1
    _assert_cleared(a)


def test_every_reset_path_routes_through_one_function():
    for fn in (GaladrielAgent.clear_history, GaladrielAgent.pop_and_archive_history,
               GaladrielAgent._maybe_rollover):
        src = inspect.getsource(fn)
        assert "_reset_channel_state(" in src, f"{fn.__name__} bypasses _reset_channel_state"
        assert "_frozen_system.pop" not in src, f"{fn.__name__} keeps its own pop-list"


def test_no_per_channel_dict_escapes_the_classification():
    init = inspect.getsource(GaladrielAgent.__init__)
    dicts = set(re.findall(r"self\.(_\w+)\s*(?::\s*dict[^=]*)?=\s*\{\}", init))
    unclassified = dicts - set(GaladrielAgent._THREAD_SCOPED_STATE) - KEEP
    assert not unclassified, (
        f"new per-channel dict(s) {sorted(unclassified)}: add to _THREAD_SCOPED_STATE "
        f"(dies with the thread) or to KEEP here (survives it) — decide, don't drift")
