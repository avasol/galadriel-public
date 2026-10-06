"""Extensions wired into the prompt and the scheduler.
Acceptance tests, written first. Contract: docs/EXTENSIONS.md ."""
import asyncio
import json
from pathlib import Path

from harness import extensions as ex
from harness.memory import MemoryManager
from harness.scheduler import Scheduler

ROOT = Path(__file__).resolve().parent.parent


def _mind(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "SOUL.md").write_text("SOUL-MARKER\n", encoding="utf-8")
    (cfg / "MEMORY.md").write_text("MEMORY-MARKER\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    return MemoryManager(config_dir=str(cfg), memory_dir=str(tmp_path / "memory"))


def _ext(root, name="rhythms", routines=()):
    d = root / "extensions" / name
    (d / "layers").mkdir(parents=True)
    (d / "layers" / "main.md").write_text("LAYER-MARKER\n", encoding="utf-8")
    man = {"name": name, "version": "1.0.0", "title": "Rhythms", "description": "t",
           "kind": "declarative", "body_min": "0.0.0",
           "contributes": {"layers": [{"file": "layers/main.md", "title": "Main"}],
                           "routines": [r["id"] for r in routines], "tools": [], "routes": [], "hooks": []},
           "keyring_slots": [], "requires": []}
    if routines:
        (d / "routines.json").write_text(json.dumps(list(routines)), encoding="utf-8")
    (d / "extension.json").write_text(json.dumps(man), encoding="utf-8")
    return d


# ── prompt ────────────────────────────────────────────────────────

def test_no_extensions_prompt_is_byte_identical(tmp_path):
    m = _mind(tmp_path)
    before = m.build_stable_text()
    (tmp_path / "extensions").mkdir()
    assert m.build_stable_text() == before
    _ext(tmp_path)  # present but not approved
    assert m.build_stable_text() == before


def test_enabled_layer_sits_after_soul_before_memory(tmp_path):
    m = _mind(tmp_path)
    _ext(tmp_path)
    ex.approve(tmp_path, "rhythms")
    text = m.build_stable_text()
    assert text.index("SOUL-MARKER") < text.index("# Extension — Rhythms") \
        < text.index("LAYER-MARKER") < text.index("# Long-Term Memory")


def test_disable_removes_layer_next_build(tmp_path):
    m = _mind(tmp_path)
    _ext(tmp_path)
    ex.approve(tmp_path, "rhythms")
    assert "LAYER-MARKER" in m.build_stable_text()
    ex.disable(tmp_path, "rhythms")
    assert "LAYER-MARKER" not in m.build_stable_text()


def test_broken_extensions_never_break_the_prompt(tmp_path, monkeypatch):
    m = _mind(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ex, "layers_text", boom)
    assert "SOUL-MARKER" in m.build_stable_text()


# ── routines ──────────────────────────────────────────────────────

class _Fake:
    def __init__(self, memory_dir):
        class _M:
            pass
        self.agent = _M()
        self.agent.memory = _M()
        self.agent.memory.memory_dir = str(memory_dir)
        self.sent = []

    async def _send_agent_message(self, prompt, channel_id):
        self.sent.append((prompt, channel_id))
        return True


R = {"id": "council", "at": "10:00", "days": "workdays", "prompt": "Hold council."}


def test_routine_fires_on_its_own_channel(tmp_path):
    (tmp_path / "memory").mkdir()
    _ext(tmp_path, routines=[R])
    ex.approve(tmp_path, "rhythms")
    f = _Fake(tmp_path / "memory")
    r = dict(R, ext="rhythms")
    asyncio.run(Scheduler._fire_extension_routine(f, r))
    assert f.sent == [("[SYSTEM:ROUTINE:rhythms:council] Hold council.", "ext:rhythms")]


def test_disabled_routine_does_not_fire(tmp_path):
    (tmp_path / "memory").mkdir()
    _ext(tmp_path, routines=[R])
    ex.approve(tmp_path, "rhythms")
    ex.disable(tmp_path, "rhythms")
    f = _Fake(tmp_path / "memory")
    asyncio.run(Scheduler._fire_extension_routine(f, dict(R, ext="rhythms")))
    assert f.sent == []


def test_routine_uses_current_prompt_text(tmp_path):
    (tmp_path / "memory").mkdir()
    d = _ext(tmp_path, routines=[R])
    ex.approve(tmp_path, "rhythms")
    (d / "routines.json").write_text(json.dumps([dict(R, prompt="Changed.")]), encoding="utf-8")
    f = _Fake(tmp_path / "memory")
    asyncio.run(Scheduler._fire_extension_routine(f, dict(R, ext="rhythms")))
    assert f.sent and f.sent[0][0].endswith("Changed.")


def test_scheduler_starts_extension_routines():
    src = (ROOT / "harness" / "scheduler.py").read_text(encoding="utf-8")
    assert "ex.routines(" in src or "extensions.routines(" in src
    assert "_fire_extension_routine" in src
