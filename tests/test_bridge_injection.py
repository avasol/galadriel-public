"""Regression: trim-bridge injection must never splice a string into history.

The bug this pins: `messages[:0] = bridge` where bridge is a str iterates the
string character-by-character, injecting hundreds of single-char entries. Every
dict-assuming reader (bridge, tower history, palace archive) then dies on
'str' object has no attribute 'get'.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness.bridge import build_bridge, inject_bridge, _messages_to_text


def _sample():
    return [
        {"role": "user", "content": "hello there this is a test message number one about a thing"},
        {"role": "assistant", "content": "sure thing here is a longer reply about the subject matter at hand"},
        {"role": "user", "content": "and another question that goes on for a while to build enough text to summarize"},
    ]


def test_build_bridge_returns_str():
    b = build_bridge(_sample())
    assert b is None or isinstance(b, str)


def test_inject_produces_single_dict_message():
    msgs = _sample()
    b = build_bridge(msgs)
    if not b:
        return  # sumy unavailable — nothing to assert
    out = inject_bridge(msgs, b)
    # exactly one message prepended, and it is a DICT
    assert len(out) == len(msgs) + 1, f"expected +1 message, got +{len(out)-len(msgs)}"
    assert isinstance(out[0], dict), "bridge entry must be a dict, not a str"
    assert out[0]["role"] == "user"
    # every entry remains a dict — no character-splatter
    assert all(isinstance(m, dict) for m in out), "history contains non-dict entries"


def test_inject_rejects_non_str():
    msgs = _sample()
    assert inject_bridge(msgs, None) == msgs
    assert inject_bridge(msgs, ["not", "a", "str"]) == msgs


def test_readers_survive_bare_strings():
    # defense-in-depth: a stray str in the list must not raise
    poisoned = [{"role": "user", "content": "valid message that is long enough here"}]
    poisoned.insert(0, "s")  # simulate a splatted character
    txt = _messages_to_text(poisoned)  # must not raise
    assert isinstance(txt, str)


def test_messages_to_text_never_inlines_images():
    """A base64 blob must never reach the summarizer — it would poison the
    bridge with a wall of encoded bytes. Images become '[image]'."""
    msgs = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "data": "ABCDEF" * 100}},
        {"type": "text", "text": "here is a screenshot of the error"}]}]
    txt = _messages_to_text(msgs)
    assert "ABCDEF" not in txt
    assert "[image]" in txt
