"""Code extensions wired into the engine: tools through the safety gate, hooks,
reload on approval, a bounded goodbye. See docs/EXTENSIONS.md."""
import asyncio
import json
from pathlib import Path

import pytest

from harness import extensions as ex
from harness import ext_runtime as rt
from harness import last_word, tools

ROOT = Path(__file__).resolve().parent.parent
WIN = {"body_id": "b1", "os": "windows", "name": "desk"}
DEF = {"description": "Say something.", "input_schema": {"type": "object", "properties": {}}}
SAY = "def register(ctx):\n    ctx.tool('say', %r, lambda i: 'said', tier=%r)\n"


def _code(root, code, tools_=("say",), hooks=()):
    d = root / "extensions" / "voice"
    d.mkdir(parents=True, exist_ok=True)
    (d / "extension.py").write_text(code, encoding="utf-8")
    (d / "extension.json").write_text(json.dumps({
        "name": "voice", "version": "1.0.0", "title": "Voice", "description": "t", "kind": "code",
        "body_min": "0.0.0", "contributes": {"layers": [], "routines": [], "tools": list(tools_),
                                             "routes": [], "hooks": list(hooks)},
        "keyring_slots": [], "requires": []}), encoding="utf-8")
    ex.approve(root, "voice", body=WIN)


@pytest.fixture(autouse=True)
def _reset(tmp_path_factory):
    yield
    rt.reload(tmp_path_factory.mktemp("empty"), body=WIN)


def test_extension_tools_are_offered(tmp_path):
    _code(tmp_path, SAY % (DEF, "green"))
    rt.reload(tmp_path, body=WIN)
    names = [t["name"] for t in tools.visible_tool_definitions()]
    assert "say" in names and names.count("say") == 1


def test_no_extensions_tools_list_unchanged(tmp_path):
    before = [t["name"] for t in tools.visible_tool_definitions()]
    rt.reload(tmp_path, body=WIN)
    assert [t["name"] for t in tools.visible_tool_definitions()] == before


def test_execute_tool_dispatches_to_the_extension(tmp_path):
    _code(tmp_path, SAY % (DEF, "green"))
    rt.reload(tmp_path, body=WIN)
    assert asyncio.run(tools.execute_tool("say", {})) == "said"


def test_agent_gates_red_extension_tools():
    src = (ROOT / "harness" / "agent.py").read_text(encoding="utf-8")
    i = src.index('ext_runtime.current().tier(tool_name) == "red"')
    window = src[i:i + 2500]
    assert "approval_callback" in window and "[BLOCKED]" in window


def test_agent_fires_turn_end():
    src = (ROOT / "harness" / "agent.py").read_text(encoding="utf-8")
    assert '"on_turn_end"' in src


def test_runtime_generation_moves_on_reload(tmp_path):
    g = rt.generation()
    rt.reload(tmp_path, body=WIN)
    assert rt.generation() == g + 1


def test_agent_refreshes_its_tools_when_extensions_reload(tmp_path):
    from harness import agent as ag

    class _Fake:
        tools = []
        _tools_gen = -1
    f = _Fake()
    _code(tmp_path, SAY % (DEF, "green"))
    rt.reload(tmp_path, body=WIN)
    tl = ag.GaladrielAgent._current_tools(f)
    assert "say" in [t["name"] for t in tl]
    assert tl[-1].get("cache_control")
    assert ag.GaladrielAgent._current_tools(f) is tl
    src = (ROOT / "harness" / "agent.py").read_text(encoding="utf-8")
    assert "tools=self.tools" not in src and "self._current_tools()" in src


def test_boot_loads_extensions_and_fires_hooks():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    sched = (ROOT / "harness" / "scheduler.py").read_text(encoding="utf-8")
    assert "ext_runtime" in main and "reload(" in main and "body_identity" in main
    assert '"on_boot"' in sched and '"on_goodnight"' in sched
    assert "_fire_extension_routine" in sched


def test_last_word_gives_extensions_a_bounded_goodbye(monkeypatch):
    calls = []

    class _R:
        def fire_sync(self, event, *a, timeout=2.0):
            calls.append((event, timeout))
    monkeypatch.setattr(rt, "current", lambda: _R())
    last_word._extension_goodbye()
    assert calls == [("on_termination", 2.0)]
    src = (ROOT / "harness" / "last_word.py").read_text(encoding="utf-8")
    i = src.index("def _handler")
    assert "_extension_goodbye()" in src[i:i + 3000]


# ── Tower API ─────────────────────────────────────────────────────

class _Agent:
    def __init__(self, root):
        class _M:
            pass
        self.memory = _M()
        self.memory.memory_dir = str(root / "memory")
        self.conversations = {}


def test_tower_lists_approves_disables_and_reloads(tmp_path):
    (tmp_path / "memory").mkdir()
    d = tmp_path / "extensions" / "voice"
    d.mkdir(parents=True)
    (d / "extension.py").write_text(SAY % (DEF, "green"), encoding="utf-8")
    (d / "extension.json").write_text(json.dumps({
        "name": "voice", "version": "1.0.0", "title": "Voice", "description": "t", "kind": "code",
        "body_min": "0.0.0", "contributes": {"layers": [], "routines": [], "tools": ["say"], "routes": [], "hooks": []},
        "keyring_slots": [], "requires": []}), encoding="utf-8")
    from tower.app import create_tower
    c = create_tower(_Agent(tmp_path)).test_client()
    rows = c.get("/api/extensions").get_json()
    assert rows["extensions"][0]["state"] == "awaiting_approval" and "warning" in rows and "body" in rows
    assert c.post("/api/extensions/voice/approve").status_code == 200
    assert rt.current().has_tool("say")
    assert c.post("/api/extensions/voice/disable").status_code == 200
    assert not rt.current().has_tool("say")
    assert c.post("/api/extensions/nope/approve").status_code == 404
    assert c.get("/api/extensions/voice/approve").status_code == 405


def test_tower_run_it_here_and_rename(tmp_path):
    (tmp_path / "memory").mkdir()
    d = tmp_path / "extensions" / "rhythms"
    (d / "layers").mkdir(parents=True)
    (d / "layers" / "m.md").write_text("x")
    (d / "routines.json").write_text(json.dumps([{"id": "council", "at": "10:00", "days": "daily", "prompt": "p"}]))
    (d / "extension.json").write_text(json.dumps({
        "name": "rhythms", "version": "1.0.0", "title": "R", "description": "t", "kind": "declarative",
        "body_min": "0.0.0", "contributes": {"layers": [{"file": "layers/m.md", "title": "M"}],
                                             "routines": ["council"], "tools": [], "routes": [], "hooks": []},
        "keyring_slots": [], "requires": []}))
    ex.approve(tmp_path, "rhythms", body={"body_id": "elsewhere", "os": "linux"})
    from tower.app import create_tower
    c = create_tower(_Agent(tmp_path)).test_client()
    assert c.post("/api/extensions/rhythms/routines/council/home").status_code == 200
    from harness import body_identity as bi
    assert ex.placement(tmp_path) == {"rhythms/council": bi.body_info(tmp_path)["body_id"]}
    assert c.post("/api/body/name", json={"name": "desk"}).get_json()["body"]["name"] == "desk"
    assert c.post("/api/body/name", json={"name": ""}).status_code == 400
