"""Acceptance tests (planner-written, protected): THE ROLLOVER.

When a message arrives after the prompt-cache TTL has lapsed AND the live thread is
large, re-caching the whole thread is the most expensive call there is. Instead the
thread is archived and replaced in place by a small carry: a header naming where the
rest lives, a zero-API bridge of older turns, and the last exchanges verbatim.
"""
import inspect
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import rollover as ro  # noqa: E402
from harness.journal import ConversationJournal  # noqa: E402


def U(t):
    return {"role": "user", "content": t}


def A(t):
    return {"role": "assistant", "content": [{"type": "text", "text": t}]}


def TU(name="run_shell"):
    return {"role": "assistant", "content": [{"type": "text", "text": "checking"},
                                              {"type": "tool_use", "id": "t1", "name": name, "input": {}}]}


def TR(out="x" * 50):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": out}]}


def _thread():
    return [
        U("Fix the weekly report please, the totals are wrong"),
        TU(), TR("SECRET-FREE LONG TOOL OUTPUT " * 400),
        A("The report is fixed: totals now sum the right column."),
        U("What did you say earlier about the importer and the tools it lacks?"),
        TU(), TR(),
        A("Two improvements: a search tool and a per-job ledger. Shall I build them?"),
        U("Proceed"),
        TU(), TR(), TU(), TR(),
        A("Built and proven: abc1234. Should the importer also get a dry-run flag?"),
    ]


def test_exchanges_pair_real_user_turns_with_the_final_answer():
    ex = ro.exchanges(_thread())
    assert [u for u, _ in ex][-1] == "Proceed"
    assert ex[-1][1].startswith("Built and proven")
    assert len(ex) == 3  # tool_result turns are not exchanges


def test_carry_shape_is_valid_for_the_api():
    carry = ro.build_carry(_thread(), keep=2)
    roles = [m["role"] for m in carry]
    assert roles[0] == "user" and roles[-1] == "assistant"
    assert all(a != b for a, b in zip(roles, roles[1:]))      # strict alternation
    for m in carry:                                            # S001: a block list of text, nothing else
        assert isinstance(m["content"], list)
        assert all(b["type"] == "text" and isinstance(b["text"], str) for b in m["content"])


def test_carry_keeps_the_last_exchanges_verbatim_and_drops_tool_noise():
    carry = ro.build_carry(_thread(), keep=2)
    text = json.dumps(carry)
    assert "Proceed" in text and "Built and proven: abc1234" in text
    assert "Shall I build them?" in text
    assert "SECRET-FREE LONG TOOL OUTPUT" not in text
    assert "tool_use" not in text and "tool_result" not in text
    assert "[SYSTEM:ROLLOVER]" in carry[0]["content"][0]["text"]


def test_carry_header_names_where_the_rest_lives():
    head = ro.build_carry(_thread(), keep=1)[0]["content"][0]["text"]
    assert "journal" in head and "palace_search" in head


def test_carry_includes_a_bridge_of_older_turns_when_given():
    carry = ro.build_carry(_thread(), keep=1, bridge=lambda msgs: "💬 the report was fixed earlier")
    assert "the report was fixed earlier" in carry[0]["content"][0]["text"]


def test_carry_caps_huge_messages_and_veils_secrets():
    big = "a" * 50_000
    th = [U("key sk-ant-api03-" + "B" * 90), A(big)]
    carry = ro.build_carry(th, keep=1)
    s = json.dumps(carry)
    assert "BBBBBBBBBB" not in s
    assert len(carry[-1]["content"][0]["text"]) <= ro.CARRY_MAX_CHARS + 200


def test_images_become_placeholders():
    th = [{"role": "user", "content": [{"type": "text", "text": "look"},
                                       {"type": "image", "source": {"type": "base64", "data": "Q" * 5000}}]},
          A("I see it")]
    s = json.dumps(ro.build_carry(th, keep=1))
    assert "QQQQ" not in s and "[image]" in s


def test_no_completed_exchange_means_no_carry():
    assert ro.build_carry([U("hello")], keep=2) == []
    assert ro.build_carry([], keep=2) == []


# ── the trigger ─────────────────────────────────────────────────────────────
def test_should_roll_needs_both_cold_cache_and_size():
    assert ro.should_roll(tokens=80_000, idle_s=400, min_tokens=60_000, idle_threshold=300)
    assert not ro.should_roll(tokens=80_000, idle_s=200, min_tokens=60_000, idle_threshold=300)   # warm
    assert not ro.should_roll(tokens=20_000, idle_s=4000, min_tokens=60_000, idle_threshold=300)  # small
    assert not ro.should_roll(tokens=80_000, idle_s=None, min_tokens=60_000, idle_threshold=300)  # unknown


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "0")
    assert ro.enabled() is False
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "1")
    assert ro.enabled() is True




# ── the agent ───────────────────────────────────────────────────────────────
def _agent(tmp_path):
    from harness.agent import GaladrielAgent as Agent
    a = Agent.__new__(Agent)
    a.conversations = {}
    a.journal = ConversationJournal(tmp_path)
    a._frozen_system = {}
    a._last_turn_end = {}
    a.memory = MagicMock()
    a.memory.memory_dir = tmp_path
    return a


def test_agent_rolls_over_a_cold_large_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "1")
    monkeypatch.setattr(ro, "archive_async", lambda ch, msgs: None)
    monkeypatch.setattr(ro, "MIN_TOKENS", 100)
    a = _agent(tmp_path)
    msgs = a._get_messages("ch")
    msgs.extend(_thread())
    a._frozen_system["ch"] = (0.0, ["stale"])
    a._last_turn_end["ch"] = 0.0            # long cold
    info = a._maybe_rollover("ch")
    assert info and info["carried_exchanges"] == 2
    live = a.conversations["ch"]
    assert live is msgs                      # mutated IN PLACE
    assert live[0]["content"][0]["text"].startswith("[SYSTEM:ROLLOVER]")
    assert live[-1]["role"] == "assistant"
    assert "ch" not in a._frozen_system      # system block rebuilt fresh next call
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ev = [i for i in a.journal.read_day(day) if i["role"] == "event"]
    assert ev and ev[-1]["content"].startswith("[rollover]") and ev[-1]["meta"]["carry"]


def test_agent_does_not_roll_a_warm_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "1")
    monkeypatch.setattr(ro, "MIN_TOKENS", 100)
    a = _agent(tmp_path)
    a._get_messages("ch").extend(_thread())
    a._last_turn_end["ch"] = time.time()
    assert a._maybe_rollover("ch") is None
    assert len(a.conversations["ch"]) == len(_thread())


def test_agent_without_a_previous_turn_never_rolls(tmp_path, monkeypatch):
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "1")
    monkeypatch.setattr(ro, "MIN_TOKENS", 100)
    a = _agent(tmp_path)
    a._get_messages("ch").extend(_thread())
    assert a._maybe_rollover("ch") is None


def test_kill_switch_stops_the_agent_rollover(tmp_path, monkeypatch):
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "0")
    monkeypatch.setattr(ro, "MIN_TOKENS", 100)
    a = _agent(tmp_path)
    a._get_messages("ch").extend(_thread())
    a._last_turn_end["ch"] = 0.0
    assert a._maybe_rollover("ch") is None


def test_rollover_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GALADRIEL_ROLLOVER", "1")
    monkeypatch.setattr(ro, "MIN_TOKENS", 100)
    monkeypatch.setattr(ro, "build_carry", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    a = _agent(tmp_path)
    a._get_messages("ch").extend(_thread())
    a._last_turn_end["ch"] = 0.0
    assert a._maybe_rollover("ch") is None
    assert len(a.conversations["ch"]) == len(_thread())


def test_stamp_turn_end_records_wall_clock(tmp_path):
    a = _agent(tmp_path)
    del a._last_turn_end                     # works on agents built without __init__
    a._stamp_turn_end("ch")
    assert abs(a._last_turn_end["ch"] - time.time()) < 5


def test_respond_rolls_before_appending_and_stamps_in_finally():
    from harness.agent import GaladrielAgent as Agent
    src = inspect.getsource(Agent.respond)
    assert "_maybe_rollover(" in src and "_stamp_turn_end(" in src
    assert "finally" in src
    i_roll = src.index("_maybe_rollover(")
    i_append = src.find('"role": "user"')
    assert i_append == -1 or i_roll < i_append


def test_daily_note_refuses_a_mock_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ro.daily_note(MagicMock().memory_dir, "x")
    assert list(tmp_path.iterdir()) == []
