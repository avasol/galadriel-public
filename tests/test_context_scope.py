"""Compass layer 2 — per-heading config scoping via config/context_scope.json.

The README documented this since June; the loader did not implement it until
2026-09-06 (found by an outside audit: "the loader reads every configuration
file unconditionally and the string appears nowhere"). This test is the guard
that the documentation and the code now say the same thing.

Rules:
  - file NOT in the manifest → always loaded (back-compat)
  - file with []            → never loaded (palace/disk only)
  - file with ["x", "y"]    → loaded only while the active vision is x or y
  - missing/invalid manifest → everything loads, as before
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.memory import MemoryManager  # noqa: E402


def _mm(tmp_path, active: str | None, manifest=None):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "SOUL.md").write_text("soul", encoding="utf-8")
    (cfg / "MEMORY.md").write_text("memory", encoding="utf-8")
    (cfg / "ALWAYS.md").write_text("always-content", encoding="utf-8")
    (cfg / "ROADMAP_x.md").write_text("x-only-content", encoding="utf-8")
    (cfg / "SHARED.md").write_text("shared-x-y-content", encoding="utf-8")
    (cfg / "ARCHIVE.md").write_text("archived-content", encoding="utf-8")
    if manifest is not None:
        (cfg / "context_scope.json").write_text(json.dumps(manifest), encoding="utf-8")
    if active:
        (cfg / "active_vision.txt").write_text(active, encoding="utf-8")
    (tmp_path / "memory").mkdir(exist_ok=True)
    return MemoryManager(config_dir=str(cfg), memory_dir=str(tmp_path / "memory"))


MANIFEST = {
    "_comment": "ignored",
    "ROADMAP_x.md": ["x"],
    "SHARED.md": ["x", "y"],
    "ARCHIVE.md": [],
}


def test_scoped_files_follow_the_active_heading(tmp_path):
    out = _mm(tmp_path, "x", MANIFEST)._load_extra_context_files()
    assert "always-content" in out
    assert "x-only-content" in out
    assert "shared-x-y-content" in out
    assert "archived-content" not in out


def test_other_heading_drops_foreign_files_keeps_shared(tmp_path):
    out = _mm(tmp_path, "y", MANIFEST)._load_extra_context_files()
    assert "always-content" in out
    assert "x-only-content" not in out
    assert "shared-x-y-content" in out
    assert "archived-content" not in out


def test_no_heading_loads_only_unscoped_files(tmp_path):
    out = _mm(tmp_path, None, MANIFEST)._load_extra_context_files()
    assert "always-content" in out
    assert "x-only-content" not in out
    assert "shared-x-y-content" not in out


def test_missing_or_invalid_manifest_is_back_compat(tmp_path):
    out = _mm(tmp_path, "x", None)._load_extra_context_files()
    assert all(t in out for t in ("always-content", "x-only-content", "shared-x-y-content", "archived-content"))
    cfg = tmp_path / "config"
    (cfg / "context_scope.json").write_text("{not json", encoding="utf-8")
    out2 = MemoryManager(config_dir=str(cfg), memory_dir=str(tmp_path / "memory"))._load_extra_context_files()
    assert "archived-content" in out2
