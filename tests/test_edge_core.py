"""EDGE core: the pure parts of the desk widget's server side.

The Edge token, signed file links, turn tracking, the push outbox and the
widget trace (Edge Protocol v1.1, docs/EDGE.md). No Flask here.
"""
import base64
import json

import pytest

from harness import edge


# ── token ────────────────────────────────────────────────────────────

def test_new_token_is_long_random_urlsafe():
    a, b = edge.new_token(), edge.new_token()
    assert a != b
    assert len(a) >= 40
    assert all(c.isalnum() or c in "-_" for c in a)


@pytest.mark.parametrize("expected,supplied,ok", [
    ("abc" * 15, "abc" * 15, True),
    ("abc" * 15, "abd" * 15, False),
    ("abc" * 15, "", False),
    ("", "", False),            # no token configured: nothing matches
    ("", "anything", False),
    (None, None, False),
])
def test_token_ok(expected, supplied, ok):
    assert edge.token_ok(expected, supplied) is ok


# ── signed file links ────────────────────────────────────────────────

TOK = "t" * 43


def test_signed_link_round_trip():
    s = edge.sign_file(TOK, "sunrise.png", now=1000)
    assert "/" not in s and "?" not in s and TOK not in s
    assert edge.verify_file(TOK, s, now=1000 + 3600) == "sunrise.png"


def test_signed_link_expires_after_a_day():
    s = edge.sign_file(TOK, "a.png", now=1000)
    assert edge.verify_file(TOK, s, now=1000 + 86400 - 1) == "a.png"
    assert edge.verify_file(TOK, s, now=1000 + 86400 + 1) is None


def test_signed_link_tampered_or_other_token_refused():
    s = edge.sign_file(TOK, "a.png", now=1000)
    payload, mac = s.rsplit(".", 1)
    forged = base64.urlsafe_b64encode(json.dumps({"name": "b.png", "exp": 999999}).encode()).decode().rstrip("=")
    assert edge.verify_file(TOK, forged + "." + mac, now=1000) is None
    assert edge.verify_file(TOK, payload + "." + mac[:-2] + "AA", now=1000) is None
    assert edge.verify_file("u" * 43, s, now=1000) is None      # token replaced -> links void
    assert edge.verify_file("", s, now=1000) is None


@pytest.mark.parametrize("junk", ["", ".", "abc", "a.b.c.d", "%%%.%%%", "x" * 5000])
def test_signed_link_junk_refused(junk):
    assert edge.verify_file(TOK, junk, now=1000) is None


@pytest.mark.parametrize("bad", ["../x.png", "a/b.png", "a\\b.png", "", ".hidden", "..", "x" * 300])
def test_sign_refuses_path_parts(bad):
    with pytest.raises(ValueError):
        edge.sign_file(TOK, bad, now=1000)


# ── turn tracking (PROTOCOL v1.1 §3.9) ───────────────────────────────

def test_turns_view_none_before_first_turn():
    assert edge.TurnTracker().view() is None


def test_turns_begin_end_ack():
    t = edge.TurnTracker()
    a = t.begin()
    b = t.begin()
    assert b == a + 1
    v = t.view()
    assert v["latest"] == b and v["done"] == [] and v["unacked"] == []
    assert isinstance(v["epoch"], str) and len(v["epoch"]) >= 8
    assert t.ack(a) is False                     # still running
    t.end(a)
    assert t.view()["done"] == [a] and t.view()["unacked"] == [a]
    assert t.ack(a) is True
    assert t.view()["unacked"] == []
    assert t.ack(999) is False                   # unknown


def test_turns_keep_last_twenty_and_epochs_differ():
    t = edge.TurnTracker()
    for _ in range(30):
        t.end(t.begin())
    v = t.view()
    assert len(v["done"]) == 20 and v["done"][-1] == 30
    assert set(v["unacked"]) <= set(v["done"])
    assert edge.TurnTracker().epoch != t.epoch


# ── outbox ───────────────────────────────────────────────────────────

def test_outbox_fifo_with_titles_and_bound():
    o = edge.Outbox(limit=3)
    assert o.pop() is None
    o.push("one", title="Morning")
    o.push("two")
    assert o.pop() == {"type": "message", "message": "one", "title": "Morning"}
    assert o.pop() == {"type": "message", "message": "two", "title": ""}
    for i in range(5):
        o.push(str(i))
    assert [o.pop()["message"] for _ in range(3)] == ["2", "3", "4"]   # oldest dropped
    assert o.pop() is None


# ── trace (PROTOCOL v1.1 §3.10) ──────────────────────────────────────

def test_clean_trace_bounds_and_strips():
    data = {"boot": "b" * 100, "version": "1.3.44" * 10, "events": [
        {"ev": "turn", "id": 9, "ts": 1, "ok": True, "x": None},
        {"ev": "s", "msg": "m" * 1000, "nested": {"a": 1}, "list": [1], "recv": "spoof"},
        "not a dict",
        {("k" * 50): 1, "ev": "long key dropped"},
    ] + [{"ev": str(i)} for i in range(200)]}
    lines = edge.clean_trace(data, recv="2026-10-08T12:00:00")
    assert len(lines) <= 100
    rows = [json.loads(l) for l in lines]
    assert rows[0]["ev"] == "turn" and rows[0]["id"] == 9 and rows[0]["ok"] is True
    assert rows[0]["recv"] == "2026-10-08T12:00:00"
    assert len(rows[0]["boot"]) <= 40 and len(rows[0]["version"]) <= 20
    assert len(rows[1]["msg"]) <= 300
    assert "nested" not in rows[1] and "list" not in rows[1]
    assert rows[1]["recv"] == "2026-10-08T12:00:00"        # the widget cannot spoof it
    assert all(len(k) <= 40 for r in rows for k in r)
    assert all(len(r) <= 15 for r in rows)


def test_clean_trace_rejects_non_list():
    with pytest.raises(ValueError):
        edge.clean_trace({"events": "nope"}, recv="x")


def test_write_trace_caps_the_day(tmp_path):
    assert edge.write_trace(tmp_path, ['{"a":1}'], day="2026-10-08") == 1
    f = tmp_path / "2026-10-08.jsonl"
    assert f.read_text(encoding="utf-8") == '{"a":1}\n'
    f.write_bytes(b"x" * (5 * 1024 * 1024))
    assert edge.write_trace(tmp_path, ['{"a":2}'], day="2026-10-08") == 0


# ── the token file (this engine has no keyring) ──────────────────────

def test_token_file_round_trip_private(tmp_path, monkeypatch):
    import os, stat
    monkeypatch.delenv("EDGE_TOKEN", raising=False)
    f = tmp_path / "edge_token"
    assert edge.load_token(f) is None
    tok = edge.new_token()
    edge.save_token(f, tok)
    assert edge.load_token(f) == tok
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    tok2 = edge.new_token()
    edge.save_token(f, tok2)                 # replacing overwrites
    assert edge.load_token(f) == tok2


def test_env_token_wins(tmp_path, monkeypatch):
    f = tmp_path / "edge_token"
    edge.save_token(f, "from-file-" + "x" * 30)
    monkeypatch.setenv("EDGE_TOKEN", "from-env-" + "y" * 30)
    assert edge.load_token(f) == "from-env-" + "y" * 30


def test_blank_or_short_token_is_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGE_TOKEN", raising=False)
    f = tmp_path / "edge_token"
    f.write_text("  \n", encoding="utf-8")
    assert edge.load_token(f) is None
    f.write_text("short", encoding="utf-8")
    assert edge.load_token(f) is None        # under 24 chars is refused
    monkeypatch.setenv("EDGE_TOKEN", "short")
    assert edge.load_token(f) is None
