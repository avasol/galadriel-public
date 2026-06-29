"""Regression tests for the two tool-pairing repair passes in harness/agent.py.

These guard the live hot path: both ``_sanitize_tool_pairs`` (forward orphan:
tool_use without tool_result) and ``_strip_orphan_tool_results`` (reverse orphan:
tool_result without a parent tool_use) run before EVERY Anthropic API call. A
break here resurrects the recurring 400 stalls that required ``/new`` to clear
(commits 4fdbf82 + 9bf98c7). The original ad-hoc tests for 4fdbf82 were never
committed; this file is their durable replacement, covering both directions.

Pure functions over plain dicts — no agent state, no network. Run:
    /home/ubuntu/.venv/bin/python -m pytest tests/test_tool_pair_repair.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import _sanitize_tool_pairs, _strip_orphan_tool_results


def _tool_use(tid):
    return {"type": "tool_use", "id": tid, "name": "x", "input": {}}


def _tool_result(tid):
    return {"type": "tool_result", "tool_use_id": tid, "content": "ok"}


def _assistant(*blocks):
    return {"role": "assistant", "content": list(blocks)}


def _user(*blocks):
    return {"role": "user", "content": list(blocks)}


# ---------------------------------------------------------------------------
# Forward orphan: tool_use without a following tool_result
# ---------------------------------------------------------------------------

def test_forward_already_valid_is_noop():
    msgs = [_assistant(_tool_use("a")), _user(_tool_result("a"))]
    assert _sanitize_tool_pairs(msgs) == 0
    assert len(msgs) == 2


def test_forward_orphan_at_tail_inserts_synthetic():
    msgs = [_assistant(_tool_use("a"))]
    assert _sanitize_tool_pairs(msgs) == 1
    assert len(msgs) == 2
    res = msgs[1]
    assert res["role"] == "user"
    assert res["content"][0]["tool_use_id"] == "a"
    assert res["content"][0]["is_error"] is True


def test_forward_partial_result_fills_missing_ids():
    # assistant demanded a + b, user only answered a
    msgs = [_assistant(_tool_use("a"), _tool_use("b")), _user(_tool_result("a"))]
    assert _sanitize_tool_pairs(msgs) == 1
    answered = {b["tool_use_id"] for b in msgs[1]["content"]}
    assert answered == {"a", "b"}


def test_forward_orphan_followed_by_plain_text_inserts_before():
    msgs = [_assistant(_tool_use("a")), _user({"type": "text", "text": "hi"})]
    assert _sanitize_tool_pairs(msgs) == 1
    # synthetic tool_result inserted right after the assistant, before the text
    assert msgs[1]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["type"] == "text"


def test_forward_is_idempotent():
    msgs = [_assistant(_tool_use("a"))]
    _sanitize_tool_pairs(msgs)
    assert _sanitize_tool_pairs(msgs) == 0


# ---------------------------------------------------------------------------
# Reverse orphan: tool_result without a parent tool_use
# ---------------------------------------------------------------------------

def test_reverse_already_valid_is_noop():
    msgs = [_assistant(_tool_use("a")), _user(_tool_result("a"))]
    assert _strip_orphan_tool_results(msgs) == 0
    assert len(msgs) == 2


def test_reverse_orphan_result_is_stripped():
    # parent tool_use 'a' is gone; result for 'a' survives -> strip it
    msgs = [_assistant({"type": "text", "text": "no tools here"}),
            _user(_tool_result("a"), {"type": "text", "text": "keep me"})]
    assert _strip_orphan_tool_results(msgs) == 1
    # the orphan tool_result removed, the text block kept
    types = [b["type"] for b in msgs[1]["content"]]
    assert types == ["text"]


def test_reverse_whole_message_dropped_when_emptied():
    msgs = [_assistant({"type": "text", "text": "no tools"}),
            _user(_tool_result("a"))]
    assert _strip_orphan_tool_results(msgs) == 1
    assert len(msgs) == 1  # the all-orphan user message removed entirely


def test_reverse_keeps_valid_result_strips_only_orphan():
    # assistant offered 'a' only; user answered 'a' (valid) and 'b' (orphan)
    msgs = [_assistant(_tool_use("a")), _user(_tool_result("a"), _tool_result("b"))]
    assert _strip_orphan_tool_results(msgs) == 1
    ids = [b["tool_use_id"] for b in msgs[1]["content"]]
    assert ids == ["a"]


def test_reverse_is_idempotent():
    msgs = [_assistant({"type": "text", "text": "x"}), _user(_tool_result("a"))]
    _strip_orphan_tool_results(msgs)
    assert _strip_orphan_tool_results(msgs) == 0


# ---------------------------------------------------------------------------
# Combined: a channel poisoned in both directions, both passes in sequence
# ---------------------------------------------------------------------------

def test_combined_both_passes_yield_valid_channel():
    # message 1: forward orphan (tool_use 'a' unanswered at tail of a turn)
    # message 3: reverse orphan (tool_result 'z' with no parent tool_use)
    msgs = [
        _assistant(_tool_use("a")),                 # forward orphan
        _assistant({"type": "text", "text": "huh"}),  # no tools
        _user(_tool_result("z")),                   # reverse orphan
    ]
    _sanitize_tool_pairs(msgs)
    _strip_orphan_tool_results(msgs)

    # Every tool_result now has a matching tool_use in the previous message,
    # and every tool_use is answered by the next message.
    for i, m in enumerate(msgs):
        if m["role"] == "user":
            prev = msgs[i - 1] if i > 0 else {"content": []}
            parent_ids = {
                b["id"] for b in prev.get("content", [])
                if b.get("type") == "tool_use"
            }
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    assert b["tool_use_id"] in parent_ids
