"""Acceptance tests (planner-written, protected): the local cost ledger.

Estimated spend from the token usage already logged on every API call:
no admin key, no network, nothing leaves the machine.
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import cost_ledger as cl  # noqa: E402


def _use(tmp_path, monkeypatch):
    p = tmp_path / "cost_ledger.jsonl"
    monkeypatch.setattr(cl, "LEDGER", p)
    return p


def test_record_appends_one_jsonl_row(tmp_path, monkeypatch):
    p = _use(tmp_path, monkeypatch)
    cl.record("claude-sonnet-5", {"input": 10, "cache_read": 20, "cache_write": 30, "output": 40})
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "claude-sonnet-5"
    assert (r["input"], r["cache_read"], r["cache_write"], r["output"]) == (10, 20, 30, 40)
    assert "ts" in r


def test_record_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "LEDGER", tmp_path / "missing" / "dir" / "x.jsonl" if False else tmp_path)  # a directory
    cl.record("m", {"input": 1})  # must not raise


def test_longest_prefix_rate_wins():
    keys = [k for k in cl.PRICING if k != "default"]
    assert keys, "pricing table must have model rows"
    longest = max(keys, key=len)
    assert cl._rates(longest + "-2099") == cl.PRICING[longest]
    assert cl._rates("totally-unknown-model") == cl.PRICING["default"]


def test_summary_sums_today_and_month(tmp_path, monkeypatch):
    p = _use(tmp_path, monkeypatch)
    today = datetime.date(2030, 5, 20)
    rate = cl.PRICING["default"]
    rows = [
        {"ts": "2030-05-20T10:00:00", "model": "unknown-x", "input": 1_000_000, "cache_read": 0, "cache_write": 0, "output": 0},
        {"ts": "2030-05-02T10:00:00", "model": "unknown-x", "input": 0, "cache_read": 0, "cache_write": 0, "output": 1_000_000},
        {"ts": "2030-04-30T10:00:00", "model": "unknown-x", "input": 1_000_000, "cache_read": 0, "cache_write": 0, "output": 0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    s = cl.summary(today)
    assert s["calls_today"] == 1
    assert abs(s["day"] - rate["input"]) < 0.01
    assert abs(s["mtd"] - (rate["input"] + rate["output"])) < 0.01


def test_summary_without_ledger_is_zero(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    assert cl.summary(datetime.date(2030, 1, 1)) == {"day": 0.0, "mtd": 0.0, "calls_today": 0}


def test_report_line_is_a_string(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    assert isinstance(cl.report_line(), str)


def test_ledger_lives_under_memory():
    assert cl.LEDGER.parent.name == "memory"


def test_agent_records_every_call():
    src = (Path(__file__).resolve().parent.parent / "harness" / "agent.py").read_text()
    assert "cost_ledger.record(" in src
