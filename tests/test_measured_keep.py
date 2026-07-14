"""THE MEASURED KEEP (v0.20.0) — token-budget conversation retention.

Guards the fix for the token-blind trim: the routine per-turn call used to
cap history at 100 MESSAGES, so one tool-heavy coding cascade (20-40
messages) shrank the working window to ~3 turns while 98% of a 1M-token
context sat unused — same-day content silently left the window.

Now the routine path trims on an estimated TOKEN budget
(AGENT_HISTORY_TOKEN_BUDGET, default 150k, clamped to 70% of the context
window), with a high message ceiling (AGENT_HISTORY_MAX_MESSAGES, default
1000) as a memory bound. The max_tokens recovery cascade still passes an
explicit max_messages (50/20) and keeps its decisive count-mode shrink.

No network, no provider — a bare agent shell exercising _trim_history. Run:
    python -m pytest tests/test_measured_keep.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import (  # noqa: E402
    GaladrielAgent,
    _estimate_msg_tokens,
    _resolve_history_token_budget,
)


def _bare_agent(budget: int = 1000, hard_cap: int = 1000) -> GaladrielAgent:
    """Agent shell with only the attrs _trim_history touches — no provider,
    no network, no palace."""
    a = object.__new__(GaladrielAgent)
    a.history_token_budget = budget
    a.history_max_messages = hard_cap
    a._post_recovery_archive_tag = {}
    return a


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


# ── token mode (routine per-turn path) ──────────────────────────────


def test_under_budget_untouched():
    a = _bare_agent(budget=10_000)
    msgs = [_user("hello"), _assistant("hi"), _user("how are you?")]
    before = list(msgs)
    a._trim_history(msgs)
    assert msgs == before


def test_over_budget_trims_from_front_to_safe_boundary():
    a = _bare_agent(budget=200)  # ~800 chars of JSON
    filler = "x" * 400  # ~100 est. tokens per message
    msgs = [
        _user(filler), _assistant(filler),
        _user(filler), _assistant(filler),
        _user("the latest question"),
    ]
    a._trim_history(msgs)
    # Something was cut, the tail survived, and we start at a plain user msg.
    assert 0 < len(msgs) < 5
    assert msgs[-1]["content"] == "the latest question"
    assert msgs[0]["role"] == "user"
    # And the kept suffix fits the budget.
    assert sum(_estimate_msg_tokens(m) for m in msgs) <= a.history_token_budget


def test_many_messages_within_budget_not_count_trimmed():
    """THE WOUND ITSELF: >100 small messages must NOT be trimmed while the
    token budget has room — count is no longer the driver."""
    a = _bare_agent(budget=150_000, hard_cap=1000)
    msgs = []
    for i in range(150):  # would have been cut to 100 by the old cap
        msgs.append(_user(f"question {i}"))
        msgs.append(_assistant(f"answer {i}"))
    n = len(msgs)
    a._trim_history(msgs)
    assert len(msgs) == n, "small messages within token budget were trimmed"


def test_hard_message_ceiling_still_bounds_memory():
    a = _bare_agent(budget=10_000_000, hard_cap=50)
    msgs = [_user(f"m{i}") for i in range(120)]
    a._trim_history(msgs)
    assert len(msgs) <= 50


def test_never_trims_to_zero():
    a = _bare_agent(budget=1)  # absurdly small budget
    msgs = [_user("x" * 4000)]
    a._trim_history(msgs)
    assert len(msgs) >= 1


# ── legacy count mode (max_tokens recovery cascade) ─────────────────


def test_explicit_max_messages_keeps_count_semantics():
    a = _bare_agent(budget=10_000_000)  # budget must not interfere
    msgs = []
    for i in range(30):
        msgs.append(_user(f"q{i}"))
        msgs.append(_assistant(f"a{i}"))
    a._trim_history(msgs, max_messages=20)
    assert len(msgs) <= 20
    assert msgs[0]["role"] == "user"


def test_explicit_max_messages_under_limit_untouched():
    a = _bare_agent()
    msgs = [_user("a"), _assistant("b")]
    a._trim_history(msgs, max_messages=50)
    assert len(msgs) == 2


# ── tool-pair safety in token mode ──────────────────────────────────


def test_token_trim_does_not_orphan_tool_results():
    a = _bare_agent(budget=150)
    filler = "y" * 300
    tool_use = {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "run_shell", "input": {"command": filler}}]}
    tool_result = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": filler}]}
    msgs = [
        _user(filler),
        tool_use,
        tool_result,
        _user("latest"),
    ]
    a._trim_history(msgs)
    # Must not START with a tool_result or an assistant message.
    first = msgs[0]
    assert first["role"] == "user"
    content = first["content"]
    if isinstance(content, list):
        assert not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )


# ── budget resolver ─────────────────────────────────────────────────


def test_budget_default_and_clamps(monkeypatch):
    monkeypatch.delenv("AGENT_HISTORY_TOKEN_BUDGET", raising=False)
    assert _resolve_history_token_budget(1_000_000) == 150_000
    # Clamped to 70% of a small window…
    assert _resolve_history_token_budget(100_000) == 70_000
    # …but never below the floor.
    assert _resolve_history_token_budget(10_000) == 8_192


def test_budget_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_HISTORY_TOKEN_BUDGET", "300000")
    assert _resolve_history_token_budget(1_000_000) == 300_000
    # Env asks for more than the window allows → clamped.
    assert _resolve_history_token_budget(200_000) == 140_000


def test_estimate_never_raises_on_junk():
    class Weird:
        pass
    assert _estimate_msg_tokens({"role": "user", "content": Weird()}) >= 1
    assert _estimate_msg_tokens(None) >= 1
