"""palace_mine_guard — the mine that fails because another miner holds the
palace lock must WAIT and RETRY, never report a bare "mine failed"; anything
unrecovered must land in a durable queue the sweeper drains.

All offline: fake miners, tmp archive root, no mempalace binary.
"""
import asyncio
import os
from pathlib import Path

import pytest

from harness import palace_mine_guard as g


LOCK_MSG = ("mempalace: palace /p is held by PID {pid} (/venv/bin/mempalace --palace /p); "
            "wait for it to finish or stop the holder before retrying")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── classify_failure ──────────────────────────────────────────────────────────

def test_classify_ok():
    assert g.classify_failure(0, "", "").kind == "ok"


def test_classify_lock_extracts_pid_and_cmd():
    d = g.classify_failure(1, "", LOCK_MSG.format(pid=os.getpid()))
    assert d.kind == "lock"
    assert d.holder_pid == os.getpid()
    assert "mempalace" in (d.holder_cmd or "")
    assert d.holder_alive is True


def test_classify_lock_dead_holder():
    d = g.classify_failure(1, "", LOCK_MSG.format(pid=2_000_000_000))
    assert d.kind == "lock" and d.holder_alive is False


def test_classify_other_error():
    d = g.classify_failure(2, "", "Traceback: KeyError 'x'")
    assert d.kind == "error" and "KeyError" in d.detail
    assert "rc=2" in d.human()


# ── guarded_mine ─────────────────────────────────────────────────────────────

def test_guarded_mine_retries_after_external_holder_exits():
    """First attempt collides with a (dead) external holder; second succeeds.
    The caller sees ok, with attempts==2 — no 'mine failed' surfaces."""
    calls = []

    async def once():
        calls.append(1)
        if len(calls) == 1:
            return 1, "", LOCK_MSG.format(pid=2_000_000_000)
        return 0, "filed", ""

    d = run(g.guarded_mine(once, label="t", lock_wait_sec=5, poll_sec=0.01))
    assert d.kind == "ok" and d.attempts == 2


def test_guarded_mine_gives_up_after_max_attempts_with_cause():
    async def once():
        return 1, "", LOCK_MSG.format(pid=2_000_000_000)

    d = run(g.guarded_mine(once, label="t", max_attempts=2, lock_wait_sec=2, poll_sec=0.01))
    assert d.kind == "lock" and d.attempts == 2
    assert "palace lock held by PID 2000000000" in d.human()


def test_guarded_mine_timeout_is_classified_not_raised():
    async def once():
        raise asyncio.TimeoutError("180s")

    d = run(g.guarded_mine(once, label="t"))
    assert d.kind == "timeout" and "timeout" in d.human()


def test_guarded_mine_serialises_concurrent_callers():
    """Two guarded mines launched together must not overlap — the process
    gate is the fix for the harness racing itself."""
    active = {"n": 0, "max": 0}

    async def once():
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        await asyncio.sleep(0.02)
        active["n"] -= 1
        return 0, "", ""

    async def both():
        await asyncio.gather(g.guarded_mine(once), g.guarded_mine(once))

    run(both())
    assert active["max"] == 1


# ── queue + sweeper ──────────────────────────────────────────────────────────

def test_enqueue_dequeue_roundtrip(tmp_path: Path):
    bd = tmp_path / "agent_add_x"
    bd.mkdir()
    d = g.MineDiagnosis(kind="lock", holder_pid=1)
    depth = g.enqueue_failure(tmp_path, bd, d, agent="agent-add", wing="w", mode=None, extract=None)
    assert depth == 1
    q = g.load_queue(tmp_path)
    assert q[0]["batch_dir"] == str(bd) and q[0]["diag"]["kind"] == "lock"
    # re-failure updates, not duplicates, and counts
    g.enqueue_failure(tmp_path, bd, d, agent="agent-add", wing="w", mode=None, extract=None)
    q = g.load_queue(tmp_path)
    assert len(q) == 1 and q[0]["failures"] == 2
    g.dequeue(tmp_path, bd)
    assert g.load_queue(tmp_path) == []


def test_sweep_recovers_and_drops_vanished(tmp_path: Path):
    live = tmp_path / "agent_add_live"; live.mkdir()
    gone = tmp_path / "agent_add_gone"  # never created
    d = g.MineDiagnosis(kind="lock")
    g.enqueue_failure(tmp_path, live, d, agent="agent-add", wing="w", mode=None, extract=None)
    g.enqueue_failure(tmp_path, gone, d, agent="agent-add", wing="w", mode=None, extract=None)
    seen = []

    async def mine(bd, agent, wing, mode, extract):
        seen.append(bd)
        return True

    s = run(g.sweep_unmined(tmp_path, mine))
    assert s == {"queued": 2, "recovered": 1, "dropped": 1, "still_failing": 0, "quarantined": 0, "waiting": 0}
    assert seen == [live]
    assert g.load_queue(tmp_path) == []


def test_sweep_keeps_still_failing(tmp_path: Path):
    bd = tmp_path / "agent_add_stuck"; bd.mkdir()
    g.enqueue_failure(tmp_path, bd, g.MineDiagnosis(kind="error"), agent="a", wing="w", mode=None, extract=None)

    async def mine(*a):
        return False

    s = run(g.sweep_unmined(tmp_path, mine))
    assert s["still_failing"] == 1 and len(g.load_queue(tmp_path)) == 1


def test_doctor_reports_queue(tmp_path: Path):
    bd = tmp_path / "agent_add_q"; bd.mkdir()
    g.enqueue_failure(tmp_path, bd, g.MineDiagnosis(kind="lock", holder_pid=7), agent="a", wing="w", mode=None, extract=None)
    out = g.doctor(tmp_path)
    assert "unmined queue: 1 entry" in out and "agent_add_q" in out and "kind=lock" in out


# ── 2026-09-07: doomed mines must not be retried forever, and bulk
#    ingests must be refused before they hold the lock ──────────────────────

def _diag_timeout():
    return g.MineDiagnosis(kind="timeout", detail="180s")


def test_enqueue_backoff_grows_and_quarantines(tmp_path):
    bd = tmp_path / "batch"
    bd.mkdir()
    (bd / "a.md").write_text("x")
    for i in range(1, g.QUEUE_MAX_FAILURES + 1):
        g.enqueue_failure(tmp_path, bd, _diag_timeout(), agent="a", wing="w", mode=None, extract=None)
        e = g.load_queue(tmp_path)[0]
        assert e["failures"] == i
        assert e["next_retry"] > 0 if i > 1 else e["next_retry"] <= __import__("time").time()
        if i < g.QUEUE_MAX_FAILURES:
            assert e["quarantined"] is False
    assert g.load_queue(tmp_path)[0]["quarantined"] is True


def test_sweep_skips_waiting_and_quarantined(tmp_path):
    """A freshly failed entry is in backoff; a quarantined one is never tried.
    Neither reaches the miner."""
    a = tmp_path / "a"; a.mkdir(); (a / "x.md").write_text("x")
    q = tmp_path / "q"; q.mkdir(); (q / "x.md").write_text("x")
    g.enqueue_failure(tmp_path, a, _diag_timeout(), agent="a", wing="w", mode=None, extract=None)
    g.enqueue_failure(tmp_path, a, _diag_timeout(), agent="a", wing="w", mode=None, extract=None)  # 2nd → backoff
    for _ in range(g.QUEUE_MAX_FAILURES):
        g.enqueue_failure(tmp_path, q, _diag_timeout(), agent="a", wing="w", mode=None, extract=None)
    mined = []

    async def mine(bd, agent, wing, mode, extract):
        mined.append(bd.name)
        return True

    s = run(g.sweep_unmined(tmp_path, mine))
    assert mined == []
    assert s["waiting"] == 1 and s["quarantined"] == 1 and s["recovered"] == 0


def test_release_quarantine_makes_entry_due_again(tmp_path):
    q = tmp_path / "q"; q.mkdir(); (q / "x.md").write_text("x")
    for _ in range(g.QUEUE_MAX_FAILURES):
        g.enqueue_failure(tmp_path, q, _diag_timeout(), agent="a", wing="w", mode=None, extract=None)
    assert g.release_quarantine(tmp_path, q) is True
    mined = []

    async def mine(bd, agent, wing, mode, extract):
        mined.append(bd.name)
        return True

    s = run(g.sweep_unmined(tmp_path, mine))
    assert mined == ["q"] and s["recovered"] == 1
    assert g.load_queue(tmp_path) == []


def test_preflight_refuses_bulk_tree(tmp_path):
    bd = tmp_path / "memory"
    (bd / "journal").mkdir(parents=True)
    for i in range(g.MAX_BATCH_FILES + 1):
        (bd / "journal" / f"{i}.jsonl").write_text("{}")
    d = g.preflight(bd)
    assert d is not None and d.kind == "oversize"
    assert "files" in d.human()


def test_preflight_passes_a_filing(tmp_path):
    bd = tmp_path / "agent_add_x"
    bd.mkdir()
    (bd / "note.md").write_text("a thought")
    assert g.preflight(bd) is None


def test_doctor_shows_quarantine_and_cause(tmp_path):
    q = tmp_path / "q"; q.mkdir(); (q / "x.md").write_text("x")
    for _ in range(g.QUEUE_MAX_FAILURES):
        g.enqueue_failure(tmp_path, q, _diag_timeout(), agent="a", wing="w", mode=None, extract=None)
    out = g.doctor(tmp_path)
    assert "QUARANTINED" in out and "180s" in out
