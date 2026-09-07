"""stage_daily_logs — the goodnight mine touches ONLY memory/YYYY-MM-DD.md.

The 2026-09-07 wound: archive_daily_logs handed mempalace the whole memory/
tree (journal/, cascades/, prompt_trace/, cost_ledger.jsonl) — 944 files,
610k would-be drawers — which timed out every night and was retried 16 times.
"""
import os
import time
from pathlib import Path

from harness import palace


def _mk(memory: Path):
    memory.mkdir()
    (memory / "2026-09-06.md").write_text("- 09:00 a day")
    (memory / "2026-09-07.md").write_text("- 09:00 today")
    (memory / "MEMORY_notes.md").write_text("not a daily log")
    (memory / "cost_ledger.jsonl").write_text("{}\n" * 1000)
    (memory / "journal").mkdir()
    (memory / "journal" / "2026-09-07.jsonl").write_text("{}")
    (memory / "prompt_trace").mkdir()
    (memory / "prompt_trace" / "system_abc.txt").write_text("SYSTEM PROMPT " * 100)


def test_stage_copies_only_daily_md(tmp_path):
    memory = tmp_path / "memory"
    _mk(memory)
    stage = palace.stage_daily_logs(memory, tmp_path / "stage")
    assert stage is not None
    assert sorted(p.name for p in stage.iterdir()) == ["2026-09-06.md", "2026-09-07.md"]


def test_stage_is_idempotent_until_a_log_changes(tmp_path):
    memory = tmp_path / "memory"
    _mk(memory)
    st = tmp_path / "stage"
    assert palace.stage_daily_logs(memory, st) is not None
    assert palace.stage_daily_logs(memory, st) is None          # nothing new
    (memory / "2026-09-07.md").write_text("- 09:00 today\n- 10:00 more")
    os.utime(memory / "2026-09-07.md", (time.time() + 5, time.time() + 5))
    assert palace.stage_daily_logs(memory, st) is not None      # changed → restaged
    assert "10:00" in (st / "2026-09-07.md").read_text()


def test_stage_skips_ancient_logs(tmp_path):
    memory = tmp_path / "memory"
    _mk(memory)
    old = time.time() - 400 * 86400
    os.utime(memory / "2026-09-06.md", (old, old))
    stage = palace.stage_daily_logs(memory, tmp_path / "stage")
    assert [p.name for p in stage.iterdir()] == ["2026-09-07.md"]


def test_stage_passes_preflight_where_raw_tree_would_not(tmp_path):
    from harness import palace_mine_guard as g
    memory = tmp_path / "memory"
    _mk(memory)
    for i in range(g.MAX_BATCH_FILES + 1):
        (memory / "journal" / f"{i}.jsonl").write_text("{}")
    assert g.preflight(memory) is not None                       # raw tree: refused
    stage = palace.stage_daily_logs(memory, tmp_path / "stage")
    assert g.preflight(stage) is None                            # stage: fine
