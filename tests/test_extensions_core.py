"""Extensions — discovery, validation, approval, layers and routines.

Contract: docs/EXTENSIONS.md (lifecycle).
"""
import json
from pathlib import Path

import pytest

from harness import extensions as ex


def _ext(root: Path, name="rhythms", kind="declarative", layers=("main",),
         routines=(), recipes=(), title=None, extra_files=None, manifest_over=None):
    d = root / "extensions" / name
    (d / "layers").mkdir(parents=True, exist_ok=True)
    man = {
        "name": name, "version": "1.0.0", "title": title or name.title(),
        "description": "test", "kind": kind, "body_min": "0.0.0",
        "contributes": {"layers": [], "routines": [], "tools": [], "routes": [], "hooks": []},
        "keyring_slots": [], "requires": [],
    }
    for l in layers:
        (d / "layers" / f"{l}.md").write_text(f"Layer {l} of {name}.\n", encoding="utf-8")
        man["contributes"]["layers"].append({"file": f"layers/{l}.md", "title": f"{l} title"})
    if routines:
        (d / "routines.json").write_text(json.dumps(list(routines)), encoding="utf-8")
        man["contributes"]["routines"] = [r["id"] for r in routines]
    for r in recipes:
        (d / "commands").mkdir(exist_ok=True)
        (d / "commands" / f"{r}.json").write_text("{}", encoding="utf-8")
    for rel, text in (extra_files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if manifest_over:
        man.update(manifest_over)
    (d / "extension.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def _state(root, name):
    return {e["name"]: e for e in ex.discover(root)}[name]


# ── discovery, validation ─────────────────────────────────────────

def test_no_extensions_dir_means_nothing(tmp_path):
    assert ex.discover(tmp_path) == []
    assert ex.layers_text(tmp_path) == ""
    assert ex.routines(tmp_path) == []
    assert ex.recipe_dirs(tmp_path) == []


def test_extensions_dir_is_under_data_root(tmp_path):
    assert ex.extensions_dir(tmp_path) == tmp_path / "extensions"


def test_new_extension_awaits_approval_and_contributes_nothing(tmp_path):
    _ext(tmp_path)
    e = _state(tmp_path, "rhythms")
    assert e["state"] == "awaiting_approval"
    assert e["kind"] == "declarative" and e["version"] == "1.0.0"
    assert len(e["hash"]) == 64
    assert ex.layers_text(tmp_path) == ""


@pytest.mark.parametrize("over,msg", [
    ({"name": "other"}, "folder"),
    ({"kind": "magic"}, "kind"),
    ({"version": ""}, "version"),
])
def test_bad_manifest_is_failed_not_fatal(tmp_path, over, msg):
    _ext(tmp_path, manifest_over=over)
    e = _state(tmp_path, "rhythms")
    assert e["state"] == "failed" and msg in e["error"].lower()


def test_bad_folder_name_is_failed(tmp_path):
    _ext(tmp_path, name="Bad_Name")
    assert _state(tmp_path, "Bad_Name")["state"] == "failed"


def test_unreadable_manifest_is_failed(tmp_path):
    d = tmp_path / "extensions" / "broken"
    d.mkdir(parents=True)
    (d / "extension.json").write_text("{not json", encoding="utf-8")
    assert _state(tmp_path, "broken")["state"] == "failed"


def test_folder_without_manifest_is_ignored(tmp_path):
    (tmp_path / "extensions" / "stray").mkdir(parents=True)
    assert ex.discover(tmp_path) == []


def test_missing_declared_layer_is_failed(tmp_path):
    d = _ext(tmp_path)
    (d / "layers" / "main.md").unlink()
    e = _state(tmp_path, "rhythms")
    assert e["state"] == "failed" and "layers/main.md" in e["error"]


def test_declarative_with_code_file_is_failed(tmp_path):
    _ext(tmp_path, extra_files={"extension.py": "x = 1\n"})
    assert _state(tmp_path, "rhythms")["state"] == "failed"


# ── hash ──────────────────────────────────────────────────────────

def test_hash_ignores_data_dir_but_sees_every_other_change(tmp_path):
    d = _ext(tmp_path)
    h1 = ex.extension_hash(d)
    (d / "data").mkdir()
    (d / "data" / "state.json").write_text("{}", encoding="utf-8")
    assert ex.extension_hash(d) == h1
    (d / "layers" / "main.md").write_text("changed\n", encoding="utf-8")
    assert ex.extension_hash(d) != h1


def test_hash_is_stable_across_calls(tmp_path):
    d = _ext(tmp_path, layers=("b", "a"))
    assert ex.extension_hash(d) == ex.extension_hash(d)


# ── lifecycle ─────────────────────────────────────────────────────

def test_approve_enables_and_disable_stops(tmp_path):
    _ext(tmp_path)
    ex.approve(tmp_path, "rhythms")
    assert _state(tmp_path, "rhythms")["state"] == "enabled"
    trust = json.loads((tmp_path / "extensions" / "trust.json").read_text())
    assert trust["rhythms"]["enabled"] is True and len(trust["rhythms"]["sha256"]) == 64
    ex.disable(tmp_path, "rhythms")
    assert _state(tmp_path, "rhythms")["state"] == "disabled"
    assert ex.layers_text(tmp_path) == ""


def test_declarative_change_keeps_approval(tmp_path):
    d = _ext(tmp_path)
    ex.approve(tmp_path, "rhythms")
    (d / "layers" / "main.md").write_text("edited\n", encoding="utf-8")
    assert _state(tmp_path, "rhythms")["state"] == "enabled"
    assert "edited" in ex.layers_text(tmp_path)


def test_approve_unknown_raises(tmp_path):
    with pytest.raises(ex.ExtensionError):
        ex.approve(tmp_path, "nope")


def test_trust_json_is_not_an_extension(tmp_path):
    _ext(tmp_path)
    ex.approve(tmp_path, "rhythms")
    assert [e["name"] for e in ex.discover(tmp_path)] == ["rhythms"]


# ── contributions ─────────────────────────────────────────────────

def test_layers_text_shape_and_order(tmp_path):
    _ext(tmp_path, name="zeta", title="Zeta", layers=("one",))
    _ext(tmp_path, name="alpha", title="Alpha", layers=("one", "two"))
    for n in ("zeta", "alpha"):
        ex.approve(tmp_path, n)
    text = ex.layers_text(tmp_path)
    assert text.index("# Extension — Alpha") < text.index("# Extension — Zeta")
    assert "## one title" in text and "Layer two of alpha." in text
    assert text == ex.layers_text(tmp_path)  # deterministic


def test_routines_validated_and_listed(tmp_path):
    good = {"id": "council", "at": "10:00", "days": "workdays", "prompt": "Hold council."}
    _ext(tmp_path, routines=[good])
    ex.approve(tmp_path, "rhythms")
    assert ex.routines(tmp_path) == [{"ext": "rhythms", "id": "council", "at": "10:00",
                                      "days": "workdays", "prompt": "Hold council.",
                                      "scope": "mind"}]


@pytest.mark.parametrize("bad", [
    {"id": "x", "at": "25:00", "days": "daily", "prompt": "p"},
    {"id": "x", "at": "10:00", "days": "sometimes", "prompt": "p"},
    {"id": "x", "at": "10:00", "days": "daily", "prompt": ""},
    {"id": "Bad Id", "at": "10:00", "days": "daily", "prompt": "p"},
])
def test_bad_routine_fails_the_extension(tmp_path, bad):
    _ext(tmp_path, routines=[bad])
    assert _state(tmp_path, "rhythms")["state"] == "failed"


def test_undeclared_routine_fails_the_extension(tmp_path):
    d = _ext(tmp_path, routines=[{"id": "a", "at": "10:00", "days": "daily", "prompt": "p"}])
    man = json.loads((d / "extension.json").read_text())
    man["contributes"]["routines"] = []
    (d / "extension.json").write_text(json.dumps(man), encoding="utf-8")
    e = _state(tmp_path, "rhythms")
    assert e["state"] == "failed" and "undeclared" in e["error"].lower()


def test_recipe_dirs_only_for_enabled(tmp_path):
    _ext(tmp_path, recipes=("tidy",))
    assert ex.recipe_dirs(tmp_path) == []
    ex.approve(tmp_path, "rhythms")
    assert ex.recipe_dirs(tmp_path) == [("rhythms", tmp_path / "extensions" / "rhythms" / "commands")]


def test_discover_is_json_safe(tmp_path):
    _ext(tmp_path)
    json.dumps(ex.discover(tmp_path))


def test_prompt_weight_reported(tmp_path):
    _ext(tmp_path)
    ex.approve(tmp_path, "rhythms")
    assert _state(tmp_path, "rhythms")["prompt_tokens"] > 0


@pytest.mark.parametrize("rel", ["../../secret.txt", "/etc/hostname", "layers/../../../secret.txt"])
def test_layer_path_cannot_escape_the_extension(tmp_path, rel):
    # a declared layer path is read into the prompt,
    # so it must stay inside the extension's own folder.
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    d = _ext(tmp_path)
    man = json.loads((d / "extension.json").read_text())
    man["contributes"]["layers"] = [{"file": rel, "title": "x"}]
    (d / "extension.json").write_text(json.dumps(man), encoding="utf-8")
    ex.approve(tmp_path, "rhythms")
    assert _state(tmp_path, "rhythms")["state"] == "failed"
    assert "SECRET" not in ex.layers_text(tmp_path)


def test_symlinked_layer_cannot_escape(tmp_path):
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    d = _ext(tmp_path)
    (d / "layers" / "main.md").unlink()
    (d / "layers" / "main.md").symlink_to(tmp_path / "secret.txt")
    ex.approve(tmp_path, "rhythms")
    assert "SECRET" not in ex.layers_text(tmp_path)
