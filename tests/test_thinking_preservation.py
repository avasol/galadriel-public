"""Tests for the preserved-thinking guard (Anthropic 2026-10-01 change).

The mechanism is dark by default: with no thinking blocks in history (every
non-Anthropic brain, and any Anthropic brain with thinking off) it must be a
no-op that returns the SAME object — the parity contract.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.thinking_preservation import (  # noqa: E402
    replaying_thinking,
    is_prefix_mismatch_error,
    strip_thinking_blocks,
    recovery_mode,
)


def _assistant_with_thinking(text="done"):
    return {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "let me reason", "signature": "sig"},
        {"type": "text", "text": text},
    ]}


def test_no_thinking_is_a_noop_and_identity_preserving():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}]
    assert replaying_thinking(msgs) is False
    out, removed = strip_thinking_blocks(msgs)
    assert removed == 0
    assert out is msgs  # SAME object — byte-identical parity path untouched


def test_detects_replayed_thinking():
    assert replaying_thinking([_assistant_with_thinking()]) is True
    assert replaying_thinking(
        [{"role": "assistant",
          "content": [{"type": "redacted_thinking", "data": "x"}]}]) is True


def test_strip_removes_thinking_but_keeps_text_and_tools():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "r", "signature": "s"},
            {"type": "tool_use", "id": "t1", "name": "run_shell", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        ]},
    ]
    out, removed = strip_thinking_blocks(msgs)
    assert removed == 1
    # tool_use survived → tool-pair integrity preserved
    kinds = [b["type"] for b in out[1]["content"]]
    assert kinds == ["tool_use"]
    # original untouched (shallow copy on the changed message)
    assert msgs[1]["content"][0]["type"] == "thinking"


def test_strip_is_idempotent():
    msgs = [_assistant_with_thinking()]
    once, n1 = strip_thinking_blocks(msgs)
    twice, n2 = strip_thinking_blocks(once)
    assert n1 == 1 and n2 == 0
    assert twice is once


def test_thinking_only_turn_keeps_a_minimal_block():
    msgs = [{"role": "assistant",
             "content": [{"type": "thinking", "thinking": "r", "signature": "s"}]}]
    out, removed = strip_thinking_blocks(msgs)
    assert removed == 1
    # never emits an empty content list (the API rejects it)
    assert out[0]["content"] and out[0]["content"][0]["type"] == "text"


def test_prefix_mismatch_named_only_by_cause():
    # names the cause → True
    assert is_prefix_mismatch_error(
        Exception("400 invalid_request_error: thinking.block_binding.prefix_mismatch"))
    assert is_prefix_mismatch_error(
        Exception("the prefix must match the thinking block that was produced"))
    # a BARE 400 that does NOT name it → False (don't claim the wrong layer)
    assert not is_prefix_mismatch_error(
        Exception("400 invalid_request_error: messages.1.content: field required"))
    assert not is_prefix_mismatch_error(
        Exception("unexpected tool_use_id found in tool_result blocks"))


def test_recovery_mode_defaults_to_error(monkeypatch):
    monkeypatch.delenv("AGENT_PRESERVED_THINKING_RECOVERY", raising=False)
    assert recovery_mode() == "error"
    monkeypatch.setenv("AGENT_PRESERVED_THINKING_RECOVERY", "drop_block")
    assert recovery_mode() == "drop_block"
    monkeypatch.setenv("AGENT_PRESERVED_THINKING_RECOVERY", "nonsense")
    assert recovery_mode() == "error"  # never trust a typo into a rewrite path
