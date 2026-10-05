"""THE REFLEX ARC Phases 1 & 2 — ledger→mine digest + armoury index.

Pure file-level tests: tmp dirs, no network, no live service.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness.reflex_arc import (  # noqa: E402
    append_ledger_digest,
    armoury_index_text,
    ledger_digest,
    parse_command_headers,
)


def _write_command(bin_dir: Path, name: str, stale: str = "no",
                   reach: str = "local") -> Path:
    path = bin_dir / name
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"# REFLEX-ARC name:       {name}\n"
        f"# REFLEX-ARC born_of:    test discipline (2026-07-15)\n"
        f"# REFLEX-ARC reach:      {reach}\n"
        f"# REFLEX-ARC success:    artifact exists; exit code irrelevant\n"
        f"# REFLEX-ARC rollback:   n/a\n"
        f"# REFLEX-ARC stale:      {stale}\n"
        "echo hi\n"
    )
    path.chmod(0o755)
    return path


# ── Phase 2: header parsing + armoury index ─────────────────────────

class TestParseCommandHeaders:
    def test_parses_declared_fields(self, tmp_path):
        _write_command(tmp_path, "demo-cmd", reach="cloud")
        cmds = parse_command_headers(tmp_path)
        assert len(cmds) == 1
        c = cmds[0]
        assert c["name"] == "demo-cmd"
        assert c["reach"] == "cloud"
        assert c["stale"] == "no"
        assert "test discipline" in c["born_of"]

    def test_ignores_files_without_name_header(self, tmp_path):
        (tmp_path / "_ledger.sh").write_text("#!/bin/bash\n# helper, no header\n")
        (tmp_path / "README.md").write_text("# docs\n")
        assert parse_command_headers(tmp_path) == []

    def test_missing_dir_is_empty(self, tmp_path):
        assert parse_command_headers(tmp_path / "nope") == []

    def test_non_executable_template_not_a_phantom_command(self, tmp_path):
        # bin/README.md carries the header template in a code block; a
        # non-executable file must never parse as a command.
        readme = tmp_path / "README.md"
        readme.write_text(
            "# docs\n# REFLEX-ARC name:       <command-name>\n"
            "# REFLEX-ARC reach:      local | cloud\n"
        )
        assert parse_command_headers(tmp_path) == []


class TestArmouryIndex:
    def test_index_lists_commands(self, tmp_path):
        _write_command(tmp_path, "alpha")
        _write_command(tmp_path, "beta", reach="cloud")
        text = armoury_index_text(tmp_path)
        assert "# The Armoury" in text
        assert "`alpha` (local)" in text
        assert "`beta` (cloud)" in text
        assert "success: artifact exists" in text  # first clause only
        assert "exit code irrelevant" not in text

    def test_stale_flagged(self, tmp_path):
        _write_command(tmp_path, "old-cmd", stale="yes")
        text = armoury_index_text(tmp_path)
        assert "STALE" in text

    def test_empty_dir_yields_empty_string(self, tmp_path):
        assert armoury_index_text(tmp_path) == ""


class TestStablePrefixIntegration:
    def test_armoury_appears_in_stable_text(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_command(bin_dir, "gamma")
        monkeypatch.setenv("GALADRIEL_BIN_DIR", str(bin_dir))
        from harness.memory import MemoryManager
        mm = MemoryManager(config_dir=str(tmp_path / "config"),
                           memory_dir=str(tmp_path / "memory"))
        text = mm.build_stable_text()
        assert "# The Armoury" in text
        assert "`gamma`" in text

    def test_missing_bin_dir_never_breaks_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GALADRIEL_BIN_DIR", str(tmp_path / "absent"))
        from harness.memory import MemoryManager
        mm = MemoryManager(config_dir=str(tmp_path / "config"),
                           memory_dir=str(tmp_path / "memory"))
        text = mm.build_stable_text()
        assert "# The Armoury" not in text
        assert text  # prompt still assembles


# ── Phase 1: ledger digest → daily log ──────────────────────────────

def _write_ledger(ledger_dir: Path, cmd: str, records: list[dict]):
    ledger_dir.mkdir(parents=True, exist_ok=True)
    with (ledger_dir / f"{cmd}.jsonl").open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestLedgerDigest:
    def test_digest_filters_by_day(self, tmp_path):
        _write_ledger(tmp_path, "safe-restart", [
            {"ts": "2026-07-14T12:00:00+02:00", "command": "safe-restart",
             "outcome": "ok", "note": "yesterday"},
            {"ts": "2026-07-15T19:02:00+02:00", "command": "safe-restart",
             "outcome": "refused", "note": "pending_wake not armed"},
        ])
        digest = ledger_digest(tmp_path, date(2026, 7, 15))
        assert "refused" in digest
        assert "yesterday" not in digest
        assert "19:02 safe-restart" in digest

    def test_malformed_lines_skipped(self, tmp_path):
        ledger_dir = tmp_path
        ledger_dir.mkdir(exist_ok=True)
        (ledger_dir / "bad.jsonl").write_text(
            "not json at all\n"
            '{"ts": "2026-07-15T10:00:00+02:00", "command": "bad", "outcome": "ok", "note": ""}\n'
        )
        digest = ledger_digest(ledger_dir, date(2026, 7, 15))
        assert "bad → ok" in digest

    def test_no_records_empty(self, tmp_path):
        assert ledger_digest(tmp_path, date(2026, 7, 15)) == ""
        assert ledger_digest(tmp_path / "absent", date(2026, 7, 15)) == ""

    def test_overflow_capped(self, tmp_path):
        _write_ledger(tmp_path, "busy", [
            {"ts": f"2026-07-15T10:{i:02d}:00+02:00", "command": "busy",
             "outcome": "ok", "note": ""}
            for i in range(25)
        ])
        digest = ledger_digest(tmp_path, date(2026, 7, 15))
        assert "and 5 more record(s)" in digest


class TestAppendLedgerDigest:
    def test_appends_to_daily_log(self, tmp_path):
        memory_dir = tmp_path / "memory"
        ledger_dir = memory_dir / "command_ledger"
        _write_ledger(ledger_dir, "safe-restart", [
            {"ts": "2026-07-15T19:06:00+02:00", "command": "safe-restart",
             "outcome": "initiated", "note": "detached restart in 20s"},
        ])
        assert append_ledger_digest(memory_dir, ledger_dir, date(2026, 7, 15))
        content = (memory_dir / "2026-07-15.md").read_text()
        assert "REFLEX ARC LEDGER" in content
        assert "safe-restart → initiated" in content

    def test_idempotent_per_day(self, tmp_path):
        memory_dir = tmp_path / "memory"
        ledger_dir = memory_dir / "command_ledger"
        _write_ledger(ledger_dir, "x", [
            {"ts": "2026-07-15T09:00:00+02:00", "command": "x",
             "outcome": "ok", "note": ""},
        ])
        assert append_ledger_digest(memory_dir, ledger_dir, date(2026, 7, 15))
        assert not append_ledger_digest(memory_dir, ledger_dir, date(2026, 7, 15))
        content = (memory_dir / "2026-07-15.md").read_text()
        assert content.count("REFLEX ARC LEDGER") == 1

    def test_quiet_day_writes_nothing(self, tmp_path):
        memory_dir = tmp_path / "memory"
        assert not append_ledger_digest(
            memory_dir, memory_dir / "command_ledger", date(2026, 7, 15))
        assert not (memory_dir / "2026-07-15.md").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
