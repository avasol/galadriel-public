"""The runtime that loads approved code extensions.
docs/EXTENSIONS.md"""
import asyncio
import json
import time
from pathlib import Path

import pytest

from harness import extensions as ex
from harness import ext_runtime as rt

WIN = {"body_id": "b1", "os": "windows", "name": "desk"}

DEF = {"description": "Say something.", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}


def _code(root, code, name="voice", tools=("say",), hooks=(), slots=()):
    d = root / "extensions" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "extension.py").write_text(code, encoding="utf-8")
    (d / "extension.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "title": name.title(), "description": "t",
        "kind": "code", "body_min": "0.0.0",
        "contributes": {"layers": [], "routines": [], "tools": list(tools), "routes": [], "hooks": list(hooks)},
        "keyring_slots": [{"slot": s, "label": s} for s in slots], "requires": []}), encoding="utf-8")
    ex.approve(root, name, body=WIN)
    return d


SAY = """
def register(ctx):
    def say(inputs):
        return "said: " + inputs.get("text", "") + " on " + ctx.body["os"]
    ctx.tool("say", %r, say, tier="green")
""" % (DEF,)


def _load(root, **kw):
    return rt.Runtime.load(root, body=WIN, **kw)


def test_empty_runtime(tmp_path):
    r = _load(tmp_path)
    assert r.tool_definitions() == [] and r.loaded == [] and r.errors == {}


def test_tool_registers_and_runs(tmp_path):
    _code(tmp_path, SAY)
    r = _load(tmp_path)
    assert r.loaded == ["voice"]
    assert [d["name"] for d in r.tool_definitions()] == ["say"]
    assert r.tool_definitions()[0]["input_schema"]["type"] == "object"
    assert r.has_tool("say") and r.tier("say") == "green" and r.owner("say") == "voice"
    assert asyncio.run(r.call("say", {"text": "hi"})) == "said: hi on windows"


def test_async_handler(tmp_path):
    _code(tmp_path, """
import asyncio
def register(ctx):
    async def say(inputs):
        await asyncio.sleep(0)
        return {"ok": True}
    ctx.tool("say", %r, say)
""" % (DEF,))
    out = asyncio.run(_load(tmp_path).call("say", {}))
    assert json.loads(out) == {"ok": True}


def test_raising_tool_returns_error_text(tmp_path):
    _code(tmp_path, """
def register(ctx):
    def say(inputs):
        raise ValueError("boom")
    ctx.tool("say", %r, say)
""" % (DEF,))
    out = asyncio.run(_load(tmp_path).call("say", {}))
    assert "failed" in out and "ValueError" in out and "boom" in out


def test_slow_tool_times_out(tmp_path):
    _code(tmp_path, """
import time
def register(ctx):
    def say(inputs):
        time.sleep(2)
        return "late"
    ctx.tool("say", %r, say)
""" % (DEF,))
    out = asyncio.run(_load(tmp_path, tool_timeout=0.2).call("say", {}))
    assert "timed out" in out.lower()


def test_undeclared_tool_fails_the_extension(tmp_path):
    _code(tmp_path, SAY.replace('"say", ', '"shout", ', 1).replace("ctx.tool(\"say\"", "ctx.tool(\"shout\""))
    r = _load(tmp_path)
    assert r.loaded == [] and "voice" in r.errors and "shout" in r.errors["voice"]
    assert r.tool_definitions() == []


def test_core_tool_name_collision_fails(tmp_path):
    _code(tmp_path, """
def register(ctx):
    ctx.tool("run_shell", %r, lambda i: "x")
""" % (DEF,), tools=("run_shell",))
    r = _load(tmp_path)
    assert r.loaded == [] and "run_shell" in r.errors["voice"]


def test_collision_between_extensions_first_wins(tmp_path):
    _code(tmp_path, SAY, name="alpha")
    _code(tmp_path, SAY, name="beta")
    r = _load(tmp_path)
    assert r.loaded == ["alpha"] and "beta" in r.errors
    assert r.owner("say") == "alpha"


def test_bad_tier_and_bad_definition_fail(tmp_path):
    _code(tmp_path, SAY.replace('tier="green"', 'tier="purple"'))
    assert _load(tmp_path).loaded == []
    _code(tmp_path, """
def register(ctx):
    ctx.tool("say", {"description": "no schema"}, lambda i: "x")
""", name="voice")
    assert _load(tmp_path).loaded == []


def test_register_exception_is_contained(tmp_path):
    _code(tmp_path, "def register(ctx):\n    raise RuntimeError('broken extension')\n")
    r = _load(tmp_path)
    assert r.loaded == [] and "broken extension" in r.errors["voice"]


def test_import_error_is_contained(tmp_path):
    _code(tmp_path, "import surely_not_a_module_abc\ndef register(ctx): pass\n")
    r = _load(tmp_path)
    assert r.loaded == [] and "voice" in r.errors


def test_missing_register_is_an_error(tmp_path):
    _code(tmp_path, "x = 1\n")
    assert "register" in _load(tmp_path).errors["voice"]


def test_unapproved_or_changed_code_never_runs(tmp_path):
    marker = tmp_path / "ran.txt"
    d = _code(tmp_path, f"open({str(marker)!r}, 'w').write('ran')\ndef register(ctx): pass\n")
    (d / "extension.py").write_text(
        f"open({str(marker)!r}, 'w').write('changed ran')\ndef register(ctx): pass\n", encoding="utf-8")
    r = _load(tmp_path)
    assert r.loaded == [] and not marker.exists()


def test_red_tier_is_reported(tmp_path):
    _code(tmp_path, SAY.replace('tier="green"', 'tier="red"'))
    assert _load(tmp_path).tier("say") == "red"


def test_context_dirs_and_body(tmp_path):
    _code(tmp_path, """
def register(ctx):
    (ctx.data_dir / "shared.txt").write_text("d")
    (ctx.local_dir / "mine.txt").write_text("l")
    assert ctx.body["body_id"] == "b1" and ctx.name == "voice"
""", tools=())
    r = _load(tmp_path)
    assert r.loaded == ["voice"]
    base = tmp_path / "extensions" / "voice"
    assert (base / "data" / "shared.txt").exists() and (base / "local" / "mine.txt").exists()


def test_secret_only_for_declared_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-123")
    monkeypatch.setenv("TAVILY_API_KEY", "tv-456")
    out = tmp_path / "out.json"
    _code(tmp_path, f"""
import json
def register(ctx):
    open({str(out)!r}, "w").write(json.dumps([ctx.secret("elevenlabs"), ctx.secret("tavily")]))
""", tools=(), slots=("elevenlabs",))
    _load(tmp_path)
    assert json.loads(out.read_text()) == ["el-123", None]


def test_daily_log_is_tagged(tmp_path):
    lines = []
    _code(tmp_path, "def register(ctx):\n    ctx.daily_log('hello')\n", tools=())
    _load(tmp_path, daily_log=lines.append)
    assert lines == ["[ext:voice] hello"]


# ── hooks ─────────────────────────────────────────────────────────

def test_hook_fires_with_args(tmp_path):
    out = tmp_path / "hook.txt"
    _code(tmp_path, f"""
def register(ctx):
    def end(summary):
        open({str(out)!r}, "w").write(summary["channel_id"])
    ctx.hook("on_turn_end", end)
""", tools=(), hooks=("on_turn_end",))
    r = _load(tmp_path)
    asyncio.run(r.fire("on_turn_end", {"channel_id": "tower", "user": "u", "reply": "r"}))
    assert out.read_text() == "tower"


def test_undeclared_hook_fails_the_extension(tmp_path):
    _code(tmp_path, "def register(ctx):\n    ctx.hook('on_boot', lambda: None)\n", tools=(), hooks=())
    assert _load(tmp_path).loaded == []


def test_hook_errors_never_raise(tmp_path):
    _code(tmp_path, "def register(ctx):\n    ctx.hook('on_boot', lambda: 1/0)\n", tools=(), hooks=("on_boot",))
    asyncio.run(_load(tmp_path).fire("on_boot"))


def test_three_timeouts_in_a_row_disable_the_extension(tmp_path):
    _code(tmp_path, """
import time
def register(ctx):
    ctx.hook("on_turn_end", lambda s: time.sleep(1))
    ctx.tool("say", %r, lambda i: "x")
""" % (DEF,), hooks=("on_turn_end",))
    r = _load(tmp_path, hook_timeouts={"on_turn_end": 0.1})
    for _ in range(3):
        asyncio.run(r.fire("on_turn_end", {}))
    st = {e["name"]: e for e in ex.discover(tmp_path, body=WIN)}["voice"]
    assert st["state"] == "disabled" and "timed out 3 times" in st["error"]
    assert not r.has_tool("say")


def test_a_success_resets_the_strikes(tmp_path):
    flag = tmp_path / "slow"
    _code(tmp_path, f"""
import os, time
def register(ctx):
    def end(s):
        if os.path.exists({str(flag)!r}):
            time.sleep(1)
    ctx.hook("on_turn_end", end)
""", tools=(), hooks=("on_turn_end",))
    r = _load(tmp_path, hook_timeouts={"on_turn_end": 0.1})
    flag.write_text("1")
    asyncio.run(r.fire("on_turn_end", {})); asyncio.run(r.fire("on_turn_end", {}))
    flag.unlink()
    asyncio.run(r.fire("on_turn_end", {}))
    flag.write_text("1")
    asyncio.run(r.fire("on_turn_end", {})); asyncio.run(r.fire("on_turn_end", {}))
    st = {e["name"]: e for e in ex.discover(tmp_path, body=WIN)}["voice"]
    assert st["state"] == "enabled"


def test_fire_sync_is_bounded(tmp_path):
    _code(tmp_path, "import time\ndef register(ctx):\n    ctx.hook('on_termination', lambda: time.sleep(5))\n",
          tools=(), hooks=("on_termination",))
    r = _load(tmp_path)
    t = time.monotonic()
    r.fire_sync("on_termination", timeout=0.3)
    assert time.monotonic() - t < 1.5


# ── the singleton ─────────────────────────────────────────────────

def test_current_and_reload(tmp_path):
    rt.reload(tmp_path, body=WIN)
    assert rt.current().tool_definitions() == []
    _code(tmp_path, SAY)
    rt.reload(tmp_path, body=WIN)
    assert rt.current().has_tool("say")
    ex.disable(tmp_path, "voice", body=WIN)
    rt.reload(tmp_path, body=WIN)
    assert not rt.current().has_tool("say")


def test_reload_picks_up_new_code_after_reapproval(tmp_path):
    d = _code(tmp_path, SAY)
    rt.reload(tmp_path, body=WIN)
    (d / "extension.py").write_text(SAY.replace("said: ", "spoke: "), encoding="utf-8")
    ex.approve(tmp_path, "voice", body=WIN)
    rt.reload(tmp_path, body=WIN)
    assert asyncio.run(rt.current().call("say", {"text": "x"})).startswith("spoke: ")


# ── memory writing, by declared permission only ──

def _code_perm(root, code, permissions):
    d = _code(root, code, tools=())
    man = json.loads((d / "extension.json").read_text())
    man["permissions"] = permissions
    (d / "extension.json").write_text(json.dumps(man))
    ex.approve(root, "voice", body=WIN)


def test_palace_add_requires_the_declared_permission(tmp_path, monkeypatch):
    calls = []

    async def fake_add(**kw):
        calls.append(kw)
        return "filed"
    from harness import palace
    monkeypatch.setattr(palace, "add_drawer", fake_add)
    out = tmp_path / "out.txt"
    code = f"""
import asyncio
def register(ctx):
    try:
        r = asyncio.run(ctx.palace_add("felt warmth", topic="felt-warmth", room="r", origin="reflection", confidence=0.9))
    except Exception as e:
        r = type(e).__name__
    open({str(out)!r}, "w").write(str(r))
"""
    _code_perm(tmp_path, code, [])
    _load(tmp_path)
    assert out.read_text() == "ExtensionError" and calls == []
    _code_perm(tmp_path, code, ["palace_write"])
    _load(tmp_path)
    assert out.read_text() == "filed"
    assert calls[0]["content"] == "felt warmth" and calls[0]["room"] == "r"
    assert calls[0]["confidence"] == 0.9 and calls[0]["origin"] == "reflection"


def test_unknown_permission_fails_validation(tmp_path):
    _code_perm(tmp_path, "def register(ctx): pass\n", ["root_access"])
    st = {e["name"]: e for e in ex.discover(tmp_path, body=WIN)}["voice"]
    assert st["state"] == "failed" and "root_access" in st["error"]


def test_permissions_are_listed_for_the_pane(tmp_path):
    _code_perm(tmp_path, "def register(ctx): pass\n", ["palace_write"])
    st = {e["name"]: e for e in ex.discover(tmp_path, body=WIN)}["voice"]
    assert st["permissions"] == ["palace_write"]


def test_default_body_comes_from_instance_identity(tmp_path):
    # Found by exercising the engine: reload(root) with no body left ctx.body as None.
    from harness import body_identity as bi
    _code(tmp_path, """
def register(ctx):
    ctx.tool("say", %r, lambda i: ctx.body["os"] + "/" + ctx.body["body_id"])
""" % (DEF,))
    ex.approve(tmp_path, "voice")
    r = rt.reload(tmp_path)
    me = bi.body_info(tmp_path)
    assert asyncio.run(r.call("say", {})) == me["os"] + "/" + me["body_id"]
