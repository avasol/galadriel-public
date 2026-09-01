"""GALADRIEL_NO_PALACE=1 — the benchmark control-arm confound fix (2026-09-01).

The G1 LongMemEval benchmark's control arm sets GALADRIEL_NO_PALACE=1 to run
a genuine amnesiac session: no palace tools are advertised (see
tools.visible_tool_definitions()). But the STABLE PROMPT — SOUL.md,
TOOLS.md, any config/*.md — was still teaching the agent to reach for
`palace_search` / `palace_add_drawer` / etc. as its very first instinct
("Be resourceful before asking... Your memory palace is your first stop").
A tool-less agent told to use a tool that doesn't exist is not a clean
"no memory" baseline — it's a confused one, and every number the benchmark
produces downstream of that confound is untrustworthy.

This guard: in no-palace mode, the stable prompt must carry ZERO palace
references (dedicated sections, stray sentences, or bare tool-call syntax).
In normal mode, nothing changes — this is a mode-gated strip, not a content
cut.

Uses the REPO'S REAL config/ dir (not a synthetic tmp fixture) for the
content-bearing assertions, because the confound this guards against was
found live in this repo's own SOUL.md and TOOLS.md — a synthetic fixture
would not have caught it and would not prove the fix holds against drift.
"""

import os

from harness.memory import MemoryManager


def _real_mm():
    return MemoryManager(config_dir="config", memory_dir="memory")


def _isolated_mm(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "memory").mkdir(exist_ok=True)
    return MemoryManager(config_dir=str(tmp_path / "config"),
                         memory_dir=str(tmp_path / "memory"))


def _with_no_palace(value, fn):
    prev = os.environ.get("GALADRIEL_NO_PALACE")
    os.environ["GALADRIEL_NO_PALACE"] = value
    try:
        return fn()
    finally:
        if prev is None:
            os.environ.pop("GALADRIEL_NO_PALACE", None)
        else:
            os.environ["GALADRIEL_NO_PALACE"] = prev


def test_no_palace_mode_strips_all_palace_instruction():
    mm = _real_mm()
    text = _with_no_palace("1", mm.build_stable_text)

    assert "Palace Protocol" not in text
    assert "palace_search" not in text
    assert "palace_add_drawer" not in text
    assert "palace_" not in text  # no bare tool-call syntax survives either


def test_no_palace_mode_preserves_surrounding_content():
    # The strip must be surgical: SOUL.md's Core Truths section has other,
    # non-palace bullets either side of the palace-referencing one — they
    # must survive untouched.
    mm = _real_mm()
    text = _with_no_palace("1", mm.build_stable_text)

    assert "Have opinions" in text
    assert "Earn trust through competence" in text
    assert "Be resourceful before asking" in text
    # the resourcefulness advice itself should still read as a coherent
    # sentence once the palace clause is excised, not a dangling fragment.
    assert "Read the file. Check the context. Search for it." in text


def test_normal_mode_is_untouched():
    mm = _real_mm()
    text = _with_no_palace("0", mm.build_stable_text)

    assert "Palace Protocol" in text
    assert "palace_search" in text


def test_no_palace_mode_also_strips_dynamic_block_hints(tmp_path):
    # The active-project banner and wake-up injection both instruct the
    # model to call palace_search — same confound, different block.
    mm = _isolated_mm(tmp_path)
    (tmp_path / "config" / "active_vision.txt").write_text("aedelgard")
    (tmp_path / "config" / "visions").mkdir(exist_ok=True)
    (tmp_path / "config" / "visions" / "aedelgard.md").write_text("test vision")

    text = _with_no_palace("1", mm.build_dynamic_text)
    assert "palace_search" not in text

    text_on = _with_no_palace("0", mm.build_dynamic_text)
    assert "palace_search" in text_on
