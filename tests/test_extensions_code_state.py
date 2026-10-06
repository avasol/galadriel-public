"""Code extensions: validation and hash-pinned state.
docs/EXTENSIONS.md Nothing here imports or runs
extension code; that is harness/ext_runtime.py."""
import json
from pathlib import Path

import pytest

from harness import extensions as ex

WIN = {"body_id": "b1", "os": "windows", "name": "desk"}


def _code(root, name="voice", tools=("say",), hooks=(), requires=(), code="def register(ctx):\n    pass\n",
          platforms=None, slots=()):
    d = root / "extensions" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "extension.py").write_text(code, encoding="utf-8")
    man = {"name": name, "version": "1.0.0", "title": name.title(), "description": "t",
           "kind": "code", "body_min": "0.0.0",
           "contributes": {"layers": [], "routines": [], "tools": list(tools), "routes": [],
                           "hooks": list(hooks)},
           "keyring_slots": [{"slot": s, "label": s} for s in slots], "requires": list(requires)}
    if platforms is not None:
        man["platforms"] = platforms
    (d / "extension.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def _st(root, name="voice"):
    return {e["name"]: e for e in ex.discover(root, body=WIN)}[name]


def test_new_code_extension_awaits_approval(tmp_path):
    _code(tmp_path)
    e = _st(tmp_path)
    assert e["kind"] == "code" and e["state"] == "awaiting_approval" and e["error"] == ""


def test_approved_code_extension_is_enabled(tmp_path):
    _code(tmp_path)
    ex.approve(tmp_path, "voice", body=WIN)
    assert _st(tmp_path)["state"] == "enabled"


def test_any_change_to_code_revokes_approval(tmp_path):
    d = _code(tmp_path)
    ex.approve(tmp_path, "voice", body=WIN)
    (d / "extension.py").write_text("def register(ctx):\n    ctx.log.info('changed')\n", encoding="utf-8")
    e = _st(tmp_path)
    assert e["state"] == "awaiting_approval" and "changed since" in e["error"].lower()
    ex.approve(tmp_path, "voice", body=WIN)
    assert _st(tmp_path)["state"] == "enabled"


def test_data_and_local_changes_do_not_revoke(tmp_path):
    d = _code(tmp_path)
    ex.approve(tmp_path, "voice", body=WIN)
    for sub in ("data", "local"):
        (d / sub).mkdir()
        (d / sub / "x.json").write_text("{}")
    assert _st(tmp_path)["state"] == "enabled"


def test_code_kind_requires_extension_py(tmp_path):
    d = _code(tmp_path)
    (d / "extension.py").unlink()
    assert _st(tmp_path)["state"] == "failed"


def test_missing_required_module_fails(tmp_path):
    _code(tmp_path, requires=["surely_not_a_real_module_xyz"])
    e = _st(tmp_path)
    assert e["state"] == "failed" and "surely_not_a_real_module_xyz" in e["error"]


def test_present_required_module_is_fine(tmp_path):
    _code(tmp_path, requires=["json"])
    assert _st(tmp_path)["state"] == "awaiting_approval"


@pytest.mark.parametrize("tools", [["Bad Name"], [""], [5]])
def test_bad_tool_names_fail(tmp_path, tools):
    _code(tmp_path, tools=tools)
    assert _st(tmp_path)["state"] == "failed"


def test_unknown_hook_fails(tmp_path):
    _code(tmp_path, hooks=["on_everything"])
    assert _st(tmp_path)["state"] == "failed"


def test_known_hooks_accepted(tmp_path):
    _code(tmp_path, hooks=["on_boot", "on_turn_end", "on_goodnight", "on_termination"])
    assert _st(tmp_path)["state"] == "awaiting_approval"


def test_declarative_may_not_declare_tools_or_hooks(tmp_path):
    d = _code(tmp_path)
    (d / "extension.py").unlink()
    man = json.loads((d / "extension.json").read_text())
    man["kind"] = "declarative"
    (d / "extension.json").write_text(json.dumps(man))
    e = _st(tmp_path)
    assert e["state"] == "failed" and "code" in e["error"].lower()


def test_code_extension_for_other_os(tmp_path):
    _code(tmp_path, platforms=["macos"])
    assert _st(tmp_path)["state"] == "not_for_this_body"


def test_disable_with_reason_is_shown(tmp_path):
    _code(tmp_path)
    ex.approve(tmp_path, "voice", body=WIN)
    ex.disable(tmp_path, "voice", body=WIN, reason="on_turn_end timed out 3 times in a row")
    e = _st(tmp_path)
    assert e["state"] == "disabled" and "timed out 3 times" in e["error"]
    ex.approve(tmp_path, "voice", body=WIN)
    assert _st(tmp_path)["error"] == ""


def test_enabled_code_extensions_listing(tmp_path):
    _code(tmp_path, name="voice")
    _code(tmp_path, name="edge", tools=("feed",))
    ex.approve(tmp_path, "voice", body=WIN)
    rows = ex.enabled_code(tmp_path, body=WIN)
    assert [r["name"] for r in rows] == ["voice"]
    assert rows[0]["path"].endswith("extensions/voice") or rows[0]["path"].endswith("extensions\\voice")
    assert rows[0]["hash"] == _st(tmp_path)["hash"]


def test_code_is_never_imported_by_discovery(tmp_path):
    _code(tmp_path, code="raise SystemExit('must never run during discovery')\n")
    ex.approve(tmp_path, "voice", body=WIN)
    ex.discover(tmp_path, body=WIN)
    ex.layers_text(tmp_path, body=WIN)


def test_bytecode_caches_never_revoke_approval(tmp_path):
    # importing extension.py writes __pycache__/*.pyc
    # INTO the extension folder; hashed, every load revoked its own approval.
    d = _code(tmp_path)
    ex.approve(tmp_path, "voice", body=WIN)
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "extension.cpython-312.pyc").write_bytes(b"\x00bytecode")
    (d / "helper.pyc").write_bytes(b"\x00")
    assert _st(tmp_path)["state"] == "enabled"
