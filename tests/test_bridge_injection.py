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


# ── THE FRESH-CASCADE TEST (added 2026-09-21) ─────────────────────────────
# Every bridge observed in production so far fired while recovering from
# earlier splat residue, so "works on a genuinely clean long cascade" was
# still unproven. This test supplies a clean 60-turn conversation.

def _fresh_conversation(n: int = 60) -> list:
    msgs = []
    for i in range(n):
        if i % 2 == 0:
            msgs.append({"role": "user", "content": (
                f"Turn {i}: I want to talk about the memory palace design and how "
                f"the compass heading scopes retrieval for project number {i}; "
                f"please consider the tradeoffs carefully.")})
        else:
            msgs.append({"role": "assistant", "content": (
                f"Turn {i}: Right — the palace stores verbatim drawers and the "
                f"temporal knowledge graph tracks relations over time. For case "
                f"{i} I would weigh recall latency against recall precision and "
                f"prefer scoped search over a wide cast.")})
    return msgs


def test_fresh_cascade_bridge_is_coherent():
    msgs = _fresh_conversation(60)
    b = build_bridge(msgs)
    if not b:
        assert b is None  # documented degrade when sumy is absent
        return
    assert isinstance(b, str) and b.strip(), "fresh-cascade bridge was empty"
    assert len(b) < len(_messages_to_text(msgs)), "bridge did not compress"
    assert "base64" not in b.lower()
    assert not b.strip().startswith("{"), "bridge looks like a JSON dump"
    assert any(w in b.lower() for w in ("palace", "memory", "compass", "retriev")), \
        f"bridge did not reference the subject matter: {b[:200]!r}"


def test_bridge_survives_rebridging():
    msgs = _fresh_conversation(40)
    b1 = build_bridge(msgs)
    if not b1:
        return
    with_bridge = inject_bridge(msgs, b1)
    b2 = build_bridge(with_bridge)
    assert b2 is None or isinstance(b2, str)
    if b2:
        assert b2.strip(), "re-bridge produced an empty string"


def test_input_image_variants_also_placeholder():
    for key in ("image_url", "input_image"):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "look at this picture of the architecture diagram"},
            {"type": key, "image_url": "data:image/png;base64," + "B" * 2048},
        ]}]
        txt = _messages_to_text(msgs)
        assert "B" * 2048 not in txt, f"{key} payload leaked into bridge text"
