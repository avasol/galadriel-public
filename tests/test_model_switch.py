"""The brain dial — /model switch regression tests.

Covers the pure mechanics: atomic AGENT_MODEL persistence in .env and the
live set_model() state re-derivation. No network, no Discord — the agent is
built bare via object.__new__ so __init__'s client wiring never runs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.agent import GaladrielAgent, _resolve_context_window  # noqa: E402


def _bare_agent(tmp_path):
    a = object.__new__(GaladrielAgent)
    a.model = "claude-old-1"
    a.working_dir = str(tmp_path)
    return a


def test_persist_replaces_only_agent_model_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-secret\nAGENT_MODEL=claude-old-1\nAGENT_MAX_TOKENS=8192\n")
    a = _bare_agent(tmp_path)
    assert a._persist_model_env("claude-new-9") is True
    lines = env.read_text().splitlines()
    assert "AGENT_MODEL=claude-new-9" in lines
    assert "ANTHROPIC_API_KEY=sk-ant-secret" in lines  # untouched
    assert "AGENT_MAX_TOKENS=8192" in lines            # untouched
    assert len(lines) == 3                             # no duplicates appended


def test_persist_appends_when_line_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=x\n")
    a = _bare_agent(tmp_path)
    assert a._persist_model_env("claude-new-9") is True
    assert "AGENT_MODEL=claude-new-9" in env.read_text().splitlines()


def test_persist_failure_returns_false_not_raise(tmp_path):
    a = _bare_agent(tmp_path / "nonexistent-dir")
    assert a._persist_model_env("claude-new-9") is False


def test_set_model_rederives_state_and_persists(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENT_MODEL=claude-old-1\n")
    a = _bare_agent(tmp_path)
    res = a.set_model("claude-new-9")
    assert res["old"] == "claude-old-1"
    assert res["new"] == "claude-new-9"
    assert res["persisted"] is True
    assert a.model == "claude-new-9"
    assert os.environ.get("AGENT_MODEL") == "claude-new-9"
    assert a.context_window == _resolve_context_window("claude-new-9")
    assert a.history_token_budget > 0
    assert "AGENT_MODEL=claude-new-9" in env.read_text()


def test_set_model_unknown_id_degrades_to_default_window(tmp_path):
    (tmp_path / ".env").write_text("AGENT_MODEL=claude-old-1\n")
    os.environ.pop("AGENT_CONTEXT_WINDOW", None)
    a = _bare_agent(tmp_path)
    a.set_model("some-brain-nobody-has-heard-of")
    assert a.context_window == _resolve_context_window("some-brain-nobody-has-heard-of")
    assert a.context_window > 0
