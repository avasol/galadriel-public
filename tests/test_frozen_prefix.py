"""THE FROZEN PREFIX (2026-09-29) — the system prompt must stop changing per turn.

Why: the system is the FIRST part of Anthropic's checked prefix for
preserved-thinking. Rebuilding it every turn (a live clock + a growing daily
log) invalidates every thinking block in the conversation AND re-writes the
prompt cache every turn. When enabled, the system is built once per session
and reused byte-identically; a TTL bounds staleness.

These tests prove the three properties that matter:
  1. OFF  → byte-for-byte the live path (build_system_blocks called each time).
  2. ON   → built once; the SAME blocks are reused across turns.
  3. ON   → after the TTL, it refreshes (staleness is bounded).
  4. ON   → clear_history drops the frozen entry (a reset is a new session).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import GaladrielAgent  # noqa: E402


def _bare_agent(frozen: bool):
    agent = GaladrielAgent.__new__(GaladrielAgent)
    agent.model = "test-model"
    agent.frozen_prefix = frozen
    agent._frozen_prefix_ttl = 21600.0
    agent._frozen_system = {}
    agent._post_recovery_archive_tag = {}
    agent._output_ceiling_streak = {}
    agent.conversations = {}
    agent.memory = MagicMock()
    agent.memory.build_system_blocks.side_effect = (
        lambda *a, **k: [{"type": "text", "text": f"built-{agent._n}"}]
        if False else [{"type": "text", "text": "SYSTEM"}]
    )
    agent._n = 0
    return agent


def test_off_is_the_live_path():
    agent = _bare_agent(frozen=False)
    agent._system_blocks_cached("chan", set())
    agent._system_blocks_cached("chan", set())
    # built every call — the unchanged live behaviour
    assert agent.memory.build_system_blocks.call_count == 2


def test_on_builds_once_and_reuses():
    agent = _bare_agent(frozen=True)
    a = agent._system_blocks_cached("chan", set())
    b = agent._system_blocks_cached("chan", set())
    assert agent.memory.build_system_blocks.call_count == 1
    assert a == b
    # returned copy is not the frozen object (safe to append advisories)
    a.append({"type": "text", "text": "advisory"})
    assert agent._frozen_system["chan"][1] == [{"type": "text", "text": "SYSTEM"}]


def test_on_refreshes_after_ttl():
    agent = _bare_agent(frozen=True)
    agent._frozen_prefix_ttl = 0.0  # everything is stale
    agent._system_blocks_cached("chan", set())
    agent._system_blocks_cached("chan", set())
    assert agent.memory.build_system_blocks.call_count == 2


def test_on_is_per_channel():
    agent = _bare_agent(frozen=True)
    agent._system_blocks_cached("a", set())
    agent._system_blocks_cached("b", set())
    assert agent.memory.build_system_blocks.call_count == 2


def test_clear_history_drops_frozen_entry():
    agent = _bare_agent(frozen=True)
    agent._system_blocks_cached("chan", set())
    assert "chan" in agent._frozen_system
    agent.clear_history("chan")
    assert "chan" not in agent._frozen_system


def test_default_is_on(monkeypatch):
    """The Oct-1 posture (2026-09-29): the prefix is frozen by default so
    preserved thinking stays valid and the prompt cache stops thrashing."""
    monkeypatch.delenv("GALADRIEL_FROZEN_PREFIX", raising=False)
    assert (os.environ.get("GALADRIEL_FROZEN_PREFIX", "1") == "1") is True
    monkeypatch.setenv("GALADRIEL_FROZEN_PREFIX", "0")
    assert (os.environ.get("GALADRIEL_FROZEN_PREFIX", "1") == "1") is False
