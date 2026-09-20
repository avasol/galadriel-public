"""THE SCAR TISSUE — regression tests.

Covers: parse/promote/cap/retire on a temp SCARS.md, wound-class detection
with mocked neighbours (no chroma), the compass bearing stamp, and the
memory.py stable-block wiring (scars ride beneath the soul, excluded from
the extras section).
"""

import os
import textwrap
from pathlib import Path

import pytest

from harness import scars
from harness.memory import MemoryManager

SEED = textwrap.dedent("""\
    # SCAR TISSUE — test header

    doctrine text the parser must ignore.

    <!-- scars:begin -->
    <!-- scars:end -->
    """)


@pytest.fixture
def scar_env(tmp_path, monkeypatch):
    f = tmp_path / "SCARS.md"
    f.write_text(SEED, encoding="utf-8")
    mem = tmp_path / "memory"
    monkeypatch.setenv("SCARS_FILE", str(f))
    monkeypatch.setenv("MEMORY_DIR", str(mem))
    return f, mem


# ── parse / promote / retire / cap ───────────────────────────────────

def test_empty_file_lists_nothing(scar_env):
    assert scars.list_scars() == []
    assert "0/12" in scars.render_list()


def test_promote_appends_with_id_and_date(scar_env):
    f, mem = scar_env
    msg = scars.promote("Before trusting exit codes → verify the artifact. (3 incidents: 2026-07-04)")
    assert "Promoted S001" in msg
    got = scars.list_scars()
    assert len(got) == 1
    assert got[0]["id"] == "S001"
    assert "exit codes" in got[0]["text"]
    # audibility: daily-log line written
    logs = list(mem.glob("*.md"))
    assert logs and "SCAR PROMOTED [S001]" in logs[0].read_text()


def test_ids_increment_and_survive_gaps(scar_env):
    scars.promote("gate one")
    scars.promote("gate two")
    scars.retire("S001", "test")
    scars.promote("gate three")
    ids = [s["id"] for s in scars.list_scars()]
    assert ids == ["S002", "S003"]  # no reuse of retired S001


def test_promote_refuses_at_cap(scar_env, monkeypatch):
    monkeypatch.setattr(scars, "MAX_SCARS", 2)
    scars.promote("one")
    scars.promote("two")
    msg = scars.promote("three")
    assert msg.startswith("REFUSED")
    assert "equilibrium" in msg
    assert len(scars.list_scars()) == 2


def test_retire_removes_and_returns_text(scar_env):
    f, mem = scar_env
    scars.promote("a gate to retire")
    msg = scars.retire("S001", "graduated to code")
    assert "Retired S001" in msg and "a gate to retire" in msg
    assert scars.list_scars() == []
    assert "SCAR RETIRED [S001]" in list(mem.glob("*.md"))[0].read_text()


def test_retire_unknown_id_fails_cleanly(scar_env):
    assert scars.retire("S999", "x").startswith("FAILED")


def test_promote_without_markers_fails(scar_env):
    f, _ = scar_env
    f.write_text("# no markers here\n", encoding="utf-8")
    assert scars.promote("gate").startswith("FAILED")


# ── wound-class detection (mocked neighbours) ────────────────────────

CORR = "- origin: correction\n\n---\nwound body text about exit codes lying"
OBS = "- origin: observation\n\n---\nunrelated observation"


def test_scan_fires_on_third_strike(scar_env, monkeypatch):
    monkeypatch.setattr(scars, "_raw_neighbors", lambda c, n=12: [
        (CORR, 0.30, {"source_file": "a.md"}),
        (CORR, 0.40, {"source_file": "b.md"}),
    ])
    notice = scars.scan_wound_class("exit code lied again during signing")
    assert notice and "WOUND CLASS" in notice and "bin/scar promote" in notice


def test_scan_silent_below_need(scar_env, monkeypatch):
    monkeypatch.setattr(scars, "_raw_neighbors", lambda c, n=12: [
        (CORR, 0.30, {"source_file": "a.md"}),
    ])
    assert scars.scan_wound_class("x") is None


def test_scan_ignores_far_nonactive_and_noncorrection(scar_env, monkeypatch):
    monkeypatch.setattr(scars, "_raw_neighbors", lambda c, n=12: [
        (CORR, 0.80, {"source_file": "far.md"}),                                  # too far
        (OBS, 0.20, {"source_file": "obs.md"}),                                   # not a correction
        (CORR, 0.20, {"source_file": "old.md", "lifecycle_status": "superseded"}),  # not active
    ])
    assert scars.scan_wound_class("x") is None


def test_scan_dedupes_by_source_file(scar_env, monkeypatch):
    monkeypatch.setattr(scars, "_raw_neighbors", lambda c, n=12: [
        (CORR, 0.30, {"source_file": "same.md"}),
        (CORR, 0.35, {"source_file": "same.md"}),  # chunk of the same drawer
    ])
    assert scars.scan_wound_class("x") is None  # one drawer, not two


def test_scan_never_raises(scar_env, monkeypatch):
    def boom(c, n=12):
        raise RuntimeError("chroma down")
    monkeypatch.setattr(scars, "_raw_neighbors", boom)
    assert scars.scan_wound_class("x") is None


# ── memory.py stable-block wiring ────────────────────────────────────

def _mgr(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "SOUL.md").write_text("# SOUL\nthe soul text", encoding="utf-8")
    (cfg / "SCARS.md").write_text(SEED.replace("test header", "live header"), encoding="utf-8")
    (cfg / "EXTRA.md").write_text("extra project context", encoding="utf-8")
    mem = tmp_path / "memory"
    return MemoryManager(config_dir=str(cfg), memory_dir=str(mem))


def test_scars_ride_stable_block_beneath_soul(tmp_path):
    text = _mgr(tmp_path).build_stable_text()
    assert "SCAR TISSUE" in text
    assert text.index("the soul text") < text.index("SCAR TISSUE")


def test_scars_not_duplicated_in_extras(tmp_path):
    text = _mgr(tmp_path).build_stable_text()
    assert text.count("SCAR TISSUE") == 1
    assert "## SCARS.md" not in text  # not picked up as a Project Context extra
    assert "extra project context" in text  # extras still load


def test_missing_scars_file_is_harmless(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "SOUL.md").write_text("soul", encoding="utf-8")
    m = MemoryManager(config_dir=str(cfg), memory_dir=str(tmp_path / "m"))
    assert "SCAR TISSUE" not in m.build_stable_text()


# ── the compass binding (scars are filed under the heading) ──────────

def test_promote_stamps_bearing_when_compass_set(scar_env, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "compass.json").write_text('{"focused": "aedelgard", "ambient": [], "dormant": []}')
    monkeypatch.setenv("GALADRIEL_CONFIG_DIR", str(cfg))
    scars.promote("Before X -> check Y. (3 incidents)")
    got = scars.list_scars()
    assert got[0]["bearing"] == "aedelgard"
    assert "| bearing: aedelgard]" in scars._read()
    assert "🧭 aedelgard" in scars.render_list()


def test_promote_without_compass_omits_bearing(scar_env, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()  # no compass.json → no bearing
    monkeypatch.setenv("GALADRIEL_CONFIG_DIR", str(cfg))
    scars.promote("Before X -> check Y.")
    got = scars.list_scars()
    assert got[0]["bearing"] == ""
    assert "bearing:" not in scars._read()


def test_legacy_bearingless_lines_still_parse(scar_env):
    p = scars._scars_path()
    body = p.read_text(encoding="utf-8")
    legacy = "- [S007 | promoted 2026-07-01] Old wound, old format."
    p.write_text(body.replace(scars.END, legacy + "\n" + scars.END), encoding="utf-8")
    got = scars.list_scars()
    assert got[-1]["id"] == "S007"
    assert got[-1]["bearing"] == ""
    assert got[-1]["text"] == "Old wound, old format."
