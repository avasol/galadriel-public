"""THE WHOLE CUT (2026-09-30) — parity tests for the max_tokens recovery.

Regression contract: a `stop_reason == "max_tokens"` (an OUTPUT ceiling — the
answer ran long) must NOT destroy conversation history. We drop the truncated
reply, KEEP history, nudge the model to smaller steps, escalate the ceiling
once, and retry. Trim/reset stays reserved for REAL overflow (413).
"""
import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.agent import (
    GaladrielAgent,
    _render_max_tokens_advisory,
    _truncated_tool_name,
    _resolve_max_tokens_ceiling,
    _strip_output_limit_nudges,
)


def test_advisory_names_the_tool_and_ceiling():
    s = _render_max_tokens_advisory(8192, "write_file")
    assert "[SYSTEM:OUTPUT-LIMIT]" in s
    assert "8192" in s and "write_file" in s and "INTACT" in s
    assert "while emitting" not in _render_max_tokens_advisory(8192, "")


def test_truncated_tool_name():
    blk = MagicMock(); blk.type = "tool_use"; blk.name = "write_file"
    blk2 = MagicMock(); blk2.type = "text"; blk2.text = "hi"
    assert _truncated_tool_name([blk2, blk]) == "write_file"
    assert _truncated_tool_name([blk2]) == ""
    assert _truncated_tool_name(None) == ""


def test_ceiling_default_and_env_override():
    os.environ.pop("AGENT_MAX_TOKENS_CEILING", None)
    assert _resolve_max_tokens_ceiling("m") == 32_000
    os.environ["AGENT_MAX_TOKENS_CEILING"] = "16000"
    try:
        assert _resolve_max_tokens_ceiling("m") == 16_000
    finally:
        os.environ.pop("AGENT_MAX_TOKENS_CEILING", None)


def _mk_agent(history_len=60, max_tokens=1000):
    agent = GaladrielAgent.__new__(GaladrielAgent)
    agent.conversations = {}
    agent.journal = MagicMock()
    agent.journal.append = MagicMock()
    agent.memory = MagicMock()
    agent.memory.build_system_blocks.return_value = []
    agent.memory.config_dir = MagicMock()
    agent.memory.memory_dir = "/tmp/nonexistent_mt_test"
    agent.model = "test-model"
    agent.max_tokens = max_tokens
    agent.tools = []
    agent.thinking_budget = 0
    agent.context_warning_callback = None
    agent.context_window = 200000
    agent.history_token_budget = 150000
    agent.history_max_messages = 1000
    agent._output_ceiling_streak = {}
    agent._post_recovery_archive_tag = {}
    agent._thinking_param = lambda: None
    agent._log_usage = lambda resp: None
    agent._maybe_warn_context = AsyncMock()
    agent._maybe_warn_output_ceiling = AsyncMock()
    agent._channel_locks = {}
    agent.show_thinking = "off"
    agent.turn_thinking = []
    agent.frozen_prefix = False
    agent._frozen_system = {}
    agent.progress_callback = None
    agent.last_usage = {}
    agent.last_trim_count = 0
    ch = "test_chan"
    agent.conversations[ch] = [{"role": "user", "content": f"msg {i}"} for i in range(history_len)]
    return agent, ch


def _max_tokens_response(tool_name="write_file"):
    r = MagicMock(); r.stop_reason = "max_tokens"
    b = MagicMock(); b.type = "tool_use"; b.name = tool_name
    b.model_dump = lambda **k: {"type": "tool_use", "name": tool_name, "input": {}}
    r.content = [b]; return r


def _end_turn_response(text="Recovered response"):
    r = MagicMock(); r.stop_reason = "end_turn"
    b = MagicMock(); b.model_dump = lambda **k: {"type": "text", "text": text}
    r.content = [b]; return r


def _run(agent, ch, msg="latest question"):
    import harness.palace as _pal
    fn = lambda a, m, c: a.respond(m, channel_id=c)
    with patch.object(_pal, "archive_conversation", new=AsyncMock(return_value=None)):
        return asyncio.run(fn(agent, msg, ch))


def test_max_tokens_keeps_history_and_recovers():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[
        _max_tokens_response("write_file"), _end_turn_response("Recovered response")])
    before = len(agent.conversations[ch])
    res = _run(agent, ch)
    assert res == "Recovered response"
    assert agent.provider.complete.call_count == 2
    after = agent.conversations[ch]
    assert len(after) >= before, f"history shrank {before} -> {len(after)}"
    assert len(after) > 55
    # nudge is transient scaffolding — stripped on success
    nudges = [m for m in after if m["role"] == "user"
              and isinstance(m["content"], str) and "[SYSTEM:OUTPUT-LIMIT]" in m["content"]]
    assert nudges == []
    assert agent.max_tokens > 1000


def test_escalation_respects_the_ceiling():
    os.environ["AGENT_MAX_TOKENS_CEILING"] = "1500"
    try:
        agent, ch = _mk_agent(60, 1000)
        agent.provider = MagicMock()
        agent.provider.complete = AsyncMock(side_effect=[
            _max_tokens_response(), _end_turn_response()])
        _run(agent, ch)
        assert agent.max_tokens == 1500
    finally:
        os.environ.pop("AGENT_MAX_TOKENS_CEILING", None)


def test_escalates_only_once():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[
        _max_tokens_response(), _max_tokens_response(), _end_turn_response()])
    _run(agent, ch)
    assert agent.provider.complete.call_count == 3
    assert agent.max_tokens == min(32_000, max(2000, 1000 + 4096))
    assert len(agent.conversations[ch]) > 55


def test_nudge_stripped_on_success():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[
        _max_tokens_response(), _end_turn_response()])
    _run(agent, ch)
    leftovers = [m for m in agent.conversations[ch]
                 if isinstance(m.get("content"), str)
                 and "[SYSTEM:OUTPUT-LIMIT]" in m["content"]]
    assert leftovers == []


def test_giveup_keeps_history_no_hard_reset():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[
        _max_tokens_response(), _max_tokens_response(), _max_tokens_response()])
    before = len(agent.conversations[ch])
    res = _run(agent, ch)
    assert agent.provider.complete.call_count == 3
    assert len(agent.conversations[ch]) >= before - 1
    assert len(agent.conversations[ch]) > 55
    assert "intact" in res.lower()


def test_strip_output_limit_nudges_helper():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "[SYSTEM:OUTPUT-LIMIT] cut off"},
        {"role": "user", "content": "[SYSTEM:OUTPUT-LIMIT] cut off again"},
    ]
    assert _strip_output_limit_nudges(msgs) == 2
    assert all("OUTPUT-LIMIT" not in str(m.get("content")) for m in msgs)


def test_no_false_archive_advisory():
    agent, ch = _mk_agent(60, 1000)
    agent.provider = MagicMock()
    agent.provider.complete = AsyncMock(side_effect=[
        _max_tokens_response(), _end_turn_response()])
    _run(agent, ch)
    assert agent._post_recovery_archive_tag.get(ch) is None
