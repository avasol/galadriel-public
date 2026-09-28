"""THE UNLOST TURN + THE HOLLOW END — a dead turn leaves a trace.

A long turn can die mid-work — a max_tokens cascade, a crash, a hollow end.
The full turn body (the cascade archive) is written only on a clean
completion, so a turn that dies writes nothing durable: the mind wakes to a
question with no record it ever answered, and can wrongly report that it
"barely started" when it ran for a long time.

Two fixes, tested here:

1. THE UNLOST TURN — a crash-tolerant checkpoint (harness/inflight.py) opened
   before the first provider call, stepped on every tool action, deleted only
   on clean completion. A dead turn leaves its trail on disk; the next turn
   in that channel gets a truth-forcing receipt naming the on-disk path.
2. THE HOLLOW END — end_turn with no text after real work is surfaced as a
   gap ("my turn closed without a word"), never as "nothing to add".
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "harness" / "agent.py").read_text(encoding="utf-8")


# ── the checkpoint module ─────────────────────────────────────────────────

def test_inflight_opens_steps_and_finishes(tmp_path):
    from harness.inflight import InflightTurn, load
    f = InflightTurn(str(tmp_path / "memory"), "chan", "turn1", "do a thing",
                     model="m").begin()
    f.step("run_shell", "ls -la", ok=True)
    f.step("read_file", "x.py", ok=True)
    rec = load(f.path)
    assert rec and rec["completed"] is False and len(rec["steps"]) == 2
    f.finish(completed=True)
    assert load(f.path) is None            # clean turn leaves no checkpoint


def test_incomplete_turn_is_found_then_reported(tmp_path):
    from harness.inflight import InflightTurn, stale_turn, render_advisory, mark_reported
    mem = str(tmp_path / "memory")
    f = InflightTurn(mem, "chan", "dead1", "build the deck", model="m").begin()
    f.step("run_shell", "extract slides", ok=True)
    # not stale yet — the checkpoint was just touched
    assert stale_turn(mem, "chan", max_age_s=60) is None
    rec = stale_turn(mem, "chan", max_age_s=0)   # force: older than 0s
    assert rec and rec["turn"] == "dead1"
    adv = render_advisory(rec)
    # The receipt must force the truth: no "barely started", name the trail.
    assert "UNLOST-TURN" in adv
    assert "INTERRUPTED" in adv and "did NOT finish" in adv
    assert "Do NOT claim you had not started" in adv
    assert "run_shell" in adv and rec["_path"] in adv
    mark_reported(rec["_path"])
    assert stale_turn(mem, "chan", max_age_s=0) is None   # self-silences


def test_a_dead_turn_survives_a_crash(tmp_path):
    """The point: no finish() call, yet the file persists — like a real crash."""
    from harness.inflight import InflightTurn, stale_turn
    mem = str(tmp_path / "memory")
    f = InflightTurn(mem, "chan", "crashed", "long job", model="m").begin()
    f.step("run_shell", "part 1", ok=True)
    f.step("generate_image", "part 2", ok=True)
    del f                                 # simulate process death — no finish()
    rec = stale_turn(mem, "chan", max_age_s=0)
    assert rec is not None and len(rec["steps"]) == 2


def test_finish_incomplete_marks_not_completed(tmp_path):
    """An aborted turn (cascade give-up) stays reportable."""
    from harness.inflight import InflightTurn, stale_turn
    mem = str(tmp_path / "memory")
    f = InflightTurn(mem, "chan", "aborted", "a job", model="m").begin()
    f.step("run_shell", "step", ok=True)
    f.finish(completed=False, note="max_tokens cascade (gave up after 3)")
    rec = stale_turn(mem, "chan", max_age_s=0)
    assert rec and rec["completed"] is False
    assert "max_tokens cascade" in rec["note"]


def test_checkpoint_lives_outside_memory(tmp_path):
    """Never mined: the checkpoint dir is a sibling of memory/, not inside it."""
    from harness.inflight import InflightTurn
    mem = tmp_path / "memory"
    f = InflightTurn(str(mem), "chan", "t", "x", model="m").begin()
    assert (tmp_path / "debug" / "inflight").is_dir()
    assert mem not in f.path.parents


# ── the agent wiring ──────────────────────────────────────────────────────

def test_agent_opens_and_closes_the_checkpoint():
    assert "from . import inflight as _inflight" in SRC
    assert "InflightTurn(" in SRC and ".begin()" in SRC
    assert "_flight.step(" in SRC
    assert "_flight.finish(completed=True)" in SRC        # clean completion
    assert "max_tokens cascade (gave up after 3)" in SRC  # cascade give-up


def test_agent_injects_the_dead_turn_advisory():
    assert "stale_turn(" in SRC and "render_advisory(" in SRC
    assert "mark_reported(" in SRC


def test_hollow_end_is_named_not_silenced():
    # end_turn with no text after real work must NOT return "" ("nothing to add").
    assert "_hollow = (not final_text) and _tool_actions > 0" in SRC
    assert "closed without a word" in SRC
    assert "_tool_actions" in SRC


def test_hollow_end_records_an_incident():
    # The hollow close must ALSO leave a checkpoint incident, not just a message.
    assert "hollow end_turn" in SRC
