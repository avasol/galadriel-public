"""Acceptance tests (planner-written, protected). REGRESSION — a mocked memory_dir must never mkdir into the cwd.

Wound (2026-09-20): tests set `agent.memory = MagicMock()` without a
`memory_dir`, so `str(self.memory.memory_dir)` = "<MagicMock name=...>"
and the non-fatal writers (trace_call, archive_cascade, shadow_observe)
created that directory in the repository root. Three junk dirs per run,
one of which held real prompt-trace blobs.

First descent of scar S001 (Cross-Boundary Shape Verification) into code:
verify SHAPE at the destination — a memory_dir that is an object repr is
not a path. See harness/pathguard.py.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.pathguard import is_mockish_dir
from harness import prompt_trace, fresh_narrative


def test_is_mockish_dir_classifies():
    assert is_mockish_dir(MagicMock().memory_dir) is True
    assert is_mockish_dir("<MagicMock name='mock.memory_dir' id='123'>") is True
    assert is_mockish_dir("<object at 0x7f>") is True
    assert is_mockish_dir("") is True
    assert is_mockish_dir("memory") is False
    assert is_mockish_dir(Path("/tmp/x/memory")) is False
    assert is_mockish_dir("/srv/agent/memory") is False
    assert is_mockish_dir(object()) is True


def test_trace_call_refuses_mock_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prompt_trace.trace_call(
        MagicMock().memory_dir, channel="c", turn_id="t", seq=0,
        model="m", system_blocks=[], messages=[])
    # Nothing created in cwd.
    assert list(tmp_path.iterdir()) == []


def test_archive_cascade_refuses_mock_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fresh_narrative.archive_cascade(
        MagicMock().memory_dir, "c", [{"role": "user", "content": "hi"}])
    assert list(tmp_path.iterdir()) == []


def test_shadow_observe_refuses_mock_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fresh_narrative.shadow_observe(
        str(MagicMock().memory_dir), "c", "hello", [{"role": "user", "content": "hi"}])
    # shadow_observe spawns a daemon thread; guard returns before the thread,
    # so nothing is created synchronously.
    assert list(tmp_path.iterdir()) == []


def test_real_dir_still_writes(tmp_path, monkeypatch):
    """The guard must not break the legitimate path."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "memory"
    d.mkdir()
    prompt_trace.trace_call(
        str(d), channel="c", turn_id="t", seq=0,
        model="m", system_blocks=[{"type": "text", "text": "soul"}],
        messages=[{"role": "user", "content": "hi"}])
    assert (d / "prompt_trace").is_dir()


def test_writers_import_the_guard():
    """The guard lives in one dependency-free module, used by both writers."""
    root = Path(__file__).resolve().parent.parent / "harness"
    src = (root / "pathguard.py").read_text()
    assert "import" not in "".join(l for l in src.splitlines() if l.startswith(("import ", "from ")) and "__future__" not in l)
    for mod in ("prompt_trace.py", "fresh_narrative.py"):
        assert "is_mockish_dir" in (root / mod).read_text()
