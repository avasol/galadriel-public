"""Tests for THE SUMMARIZED DISPLAY (2026-09-29).

The bug: on the current Claude family (opus-5, fable-5, sonnet-5) a thinking
block arrives with its text EMPTY unless the request asks for
``display: "summarized"``. Without the display field the thinking-bubble UI
has nothing to render — the toggle lights up but no reasoning ever shows.

These tests pin the param shapes and the per-model learning that drops the
display on an older model that rejects it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import GaladrielAgent  # noqa: E402


def _agent(budget=2048, model="claude-opus-5"):
    a = GaladrielAgent.__new__(GaladrielAgent)
    a.thinking_budget = budget
    a.model = model
    a._thinking_modes = {}
    a._summarized_display = {}
    return a


def test_display_summarized_is_requested_by_default():
    a = _agent()
    p = a._thinking_param()
    assert p["display"] == "summarized"
    assert p["type"] == "enabled"
    assert p["budget_tokens"] == 2048


def test_display_present_on_adaptive_dialect():
    a = _agent()
    a._thinking_modes["claude-opus-5"] = "adaptive"
    p = a._thinking_param()
    assert p == {"type": "adaptive", "display": "summarized"}


def test_no_thinking_when_budget_zero():
    a = _agent(budget=0)
    assert a._thinking_param() is None


def test_display_dropped_when_model_rejects_it():
    a = _agent()
    # simulate a 400 naming the display field
    err = Exception(
        '400 invalid_request_error: unexpected field "display" / '
        '"summarized" is not supported for this model'
    )
    # make it the only trigger: dialect must not match
    assert a._adapt_thinking_dialect(err) is True
    assert a._summarized_display["claude-opus-5"] is False
    # now the param omits display but KEEPS thinking
    p = a._thinking_param()
    assert "display" not in p
    assert p["type"] == "enabled"


def test_display_off_is_per_model():
    a = _agent()
    a._summarized_display["claude-opus-5"] = False
    a.model = "claude-fable-5"
    assert a._thinking_param()["display"] == "summarized"  # other model unaffected


def test_dialect_adaptation_still_works():
    a = _agent()
    err = Exception(
        '"thinking.type.enabled" is not supported for this model. '
        'Use "thinking.type.adaptive"'
    )
    assert a._adapt_thinking_dialect(err) is True
    assert a._thinking_modes["claude-opus-5"] == "adaptive"
    # display is preserved through a dialect change
    assert a._thinking_param()["display"] == "summarized"
