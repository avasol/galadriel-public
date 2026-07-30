"""THE FRESH NARRATIVE (Phase 0) + THE GLASS PROMPT — under test.

archive_cascade: verbatim turn cascades land in memory/cascades/<UTC-day>.jsonl,
non-fatal on garbage. prompt_trace: every provider call traced with system
blob dedup, message shape, and usage ground truth; read paths tolerate
missing days; blob fetch rejects path funny-business.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from harness.fresh_narrative import archive_cascade, _thread_hint
from harness.prompt_trace import (trace_call, read_recent, read_system_blob,
                                  _sha12)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── cascade archive ──────────────────────────────────────────────────────

def test_archive_cascade_writes_verbatim(tmp_path):
    msgs = [
        {"role": "user", "content": "deploy the thing"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "run_shell",
             "input": {"command": "ls"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    archive_cascade(tmp_path, "tower", msgs)
    day_file = tmp_path / "cascades" / f"{_today()}.jsonl"
    assert day_file.exists()
    rec = json.loads(day_file.read_text().strip())
    assert rec["channel"] == "tower"
    assert rec["n_messages"] == 4
    assert rec["messages"] == msgs  # verbatim, bodies included


def test_archive_cascade_empty_is_noop(tmp_path):
    archive_cascade(tmp_path, "tower", [])
    assert not (tmp_path / "cascades").exists()


def test_archive_cascade_never_raises(tmp_path):
    # An unserializable object must not break a live turn (default=str).
    archive_cascade(tmp_path, "tower", [{"role": "user", "content": object()}])


def test_thread_hint_finds_previous_user_message():
    msgs = [
        {"role": "user", "content": "the earlier topic"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "carry on"},
    ]
    assert _thread_hint(msgs) == "the earlier topic"
    assert _thread_hint([]) == ""


# ── the glass prompt ─────────────────────────────────────────────────────

def _fake_response():
    return SimpleNamespace(
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=12, cache_read_input_tokens=4800,
                              cache_creation_input_tokens=0, output_tokens=99),
    )


def _trace_once(tmp_path, seq=1):
    system = [
        {"type": "text", "text": "SOUL " * 100,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "daily log"},
    ]
    msgs = [{"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
    trace_call(tmp_path, channel="tower", turn_id="turn1", seq=seq,
               model="claude-test", system_blocks=system, messages=msgs,
               response=_fake_response())
    return system


def test_trace_call_writes_record_with_usage(tmp_path):
    _trace_once(tmp_path)
    day_file = tmp_path / "prompt_trace" / f"{_today()}.jsonl"
    rec = json.loads(day_file.read_text().strip())
    assert rec["turn"] == "turn1" and rec["seq"] == 1
    assert rec["model"] == "claude-test"
    assert rec["usage"]["cache_read"] == 4800
    assert rec["stop_reason"] == "end_turn"
    assert rec["n_messages"] == 2
    assert rec["messages"][0]["role"] == "user"
    assert rec["messages"][0]["head"] == "hello"
    assert rec["system_blocks"][0]["cached"] is True
    assert rec["system_blocks"][1]["cached"] is False


def test_trace_call_dedups_system_blob(tmp_path):
    _trace_once(tmp_path, seq=1)
    _trace_once(tmp_path, seq=2)
    blobs = list((tmp_path / "prompt_trace" / "blobs").glob("system_*.txt"))
    assert len(blobs) == 1  # same system → one blob
    day_file = tmp_path / "prompt_trace" / f"{_today()}.jsonl"
    lines = day_file.read_text().strip().splitlines()
    assert len(lines) == 2  # but two records


def test_trace_call_never_raises(tmp_path):
    trace_call(tmp_path, channel="t", turn_id="x", seq=1, model="m",
               system_blocks=None, messages=None, response=None)


def test_read_recent_newest_first(tmp_path):
    _trace_once(tmp_path, seq=1)
    _trace_once(tmp_path, seq=2)
    recs = read_recent(tmp_path, n=10)
    assert len(recs) == 2
    assert recs[0]["seq"] == 2  # newest first


def test_read_recent_missing_days(tmp_path):
    assert read_recent(tmp_path, n=5) == []


def test_read_system_blob_roundtrip(tmp_path):
    system = _trace_once(tmp_path)
    full = "\n\n\u241e\n\n".join(b["text"] for b in system)
    blob = read_system_blob(tmp_path, _sha12(full))
    assert blob is not None and "SOUL" in blob


def test_read_system_blob_rejects_traversal(tmp_path):
    assert read_system_blob(tmp_path, "../../etc/passwd") is None
    assert read_system_blob(tmp_path, "nonexistent000") is None
