"""extensions (docs/EXTENSIONS.md).

Every function takes an optional body={'body_id', 'os'} so one test can play
several bodies of the same mind over one shared data folder.
"""
import json
from pathlib import Path

import pytest

from harness import extensions as ex

WIN = {"body_id": "body-win", "os": "windows", "name": "desk"}
MAC = {"body_id": "body-mac", "os": "macos", "name": "laptop"}
LIN = {"body_id": "body-lin", "os": "linux", "name": "server"}


def _ext(root, name="rhythms", platforms=None, layers=None, routines=()):
    d = root / "extensions" / name
    (d / "layers").mkdir(parents=True, exist_ok=True)
    layers = layers or [{"file": "layers/main.md", "title": "Main"}]
    for l in layers:
        (d / l["file"]).write_text(f"TEXT-{l['title']}\n", encoding="utf-8")
    man = {"name": name, "version": "1.0.0", "title": name.title(), "description": "t",
           "kind": "declarative", "body_min": "0.0.0",
           "contributes": {"layers": layers, "routines": [r["id"] for r in routines],
                           "tools": [], "routes": [], "hooks": []},
           "keyring_slots": [], "requires": []}
    if platforms is not None:
        man["platforms"] = platforms
    if routines:
        (d / "routines.json").write_text(json.dumps(list(routines)), encoding="utf-8")
    (d / "extension.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def _st(root, name="rhythms", body=WIN):
    return {e["name"]: e for e in ex.discover(root, body=body)}[name]


# ── platforms ─────────────────────────────────────────────────────

def test_extension_for_other_os_is_inactive_here_but_valid(tmp_path):
    _ext(tmp_path, platforms=["macos"])
    ex.approve(tmp_path, "rhythms", body=MAC)
    assert _st(tmp_path, body=MAC)["state"] == "enabled"
    win = _st(tmp_path, body=WIN)
    assert win["state"] == "not_for_this_body" and win["platforms"] == ["macos"]
    assert ex.layers_text(tmp_path, body=WIN) == ""
    assert "TEXT-Main" in ex.layers_text(tmp_path, body=MAC)


def test_absent_platforms_means_all(tmp_path):
    _ext(tmp_path)
    assert _st(tmp_path)["platforms"] == ["windows", "macos", "linux"]


@pytest.mark.parametrize("bad", [[], ["amiga"], "windows"])
def test_bad_platforms_fail(tmp_path, bad):
    _ext(tmp_path, platforms=bad)
    assert _st(tmp_path)["state"] == "failed"


def test_layer_platforms_filter_per_body(tmp_path):
    _ext(tmp_path, layers=[{"file": "layers/a.md", "title": "Everywhere"},
                           {"file": "layers/b.md", "title": "WinOnly", "platforms": ["windows"]}])
    ex.approve(tmp_path, "rhythms", body=WIN)
    assert "TEXT-WinOnly" in ex.layers_text(tmp_path, body=WIN)
    assert "TEXT-WinOnly" not in ex.layers_text(tmp_path, body=LIN)
    assert "TEXT-Everywhere" in ex.layers_text(tmp_path, body=LIN)


# ── once per mind vs once per body ────────────────────────────────

MIND_R = {"id": "council", "at": "10:00", "days": "daily", "prompt": "Hold council."}
BODY_R = {"id": "disk", "at": "09:00", "days": "daily", "prompt": "Check this disk.", "scope": "body"}


def _ids(rs):
    return sorted(r["id"] for r in rs)


def test_first_approver_becomes_home_for_mind_routines(tmp_path):
    _ext(tmp_path, routines=[MIND_R, BODY_R])
    ex.approve(tmp_path, "rhythms", body=WIN)
    ex.approve(tmp_path, "rhythms", body=MAC)  # same mind folder, second body
    assert ex.placement(tmp_path) == {"rhythms/council": "body-win"}
    assert _ids(ex.routines(tmp_path, body=WIN)) == ["council", "disk"]
    assert _ids(ex.routines(tmp_path, body=MAC)) == ["disk"]  # never twice


def test_take_over_home(tmp_path):
    _ext(tmp_path, routines=[MIND_R])
    ex.approve(tmp_path, "rhythms", body=WIN)
    ex.set_home(tmp_path, "rhythms", "council", body=MAC)
    assert ex.routines(tmp_path, body=WIN) == []
    assert _ids(ex.routines(tmp_path, body=MAC)) == ["council"]


def test_home_not_claimed_by_a_body_that_cannot_run_it(tmp_path):
    r = dict(MIND_R, platforms=["macos"])
    _ext(tmp_path, routines=[r])
    ex.approve(tmp_path, "rhythms", body=WIN)
    assert ex.placement(tmp_path) == {}
    ex.approve(tmp_path, "rhythms", body=MAC)
    assert ex.placement(tmp_path) == {"rhythms/council": "body-mac"}
    with pytest.raises(ex.ExtensionError):
        ex.set_home(tmp_path, "rhythms", "council", body=WIN)


def test_set_home_unknown_routine_raises(tmp_path):
    _ext(tmp_path, routines=[MIND_R])
    with pytest.raises(ex.ExtensionError):
        ex.set_home(tmp_path, "rhythms", "nope", body=WIN)


def test_bad_scope_fails(tmp_path):
    _ext(tmp_path, routines=[dict(MIND_R, scope="galaxy")])
    assert _st(tmp_path)["state"] == "failed"


def test_discover_shows_routine_homes_for_the_pane(tmp_path):
    _ext(tmp_path, routines=[MIND_R, BODY_R])
    ex.approve(tmp_path, "rhythms", body=WIN)
    rows = {r["id"]: r for r in _st(tmp_path, body=MAC)["routines"]}
    assert rows["council"]["scope"] == "mind" and rows["council"]["home"] == "body-win"
    assert rows["council"]["fires_here"] is False
    assert rows["disk"]["scope"] == "body" and rows["disk"]["fires_here"] is True  # body-scope: every enabled body
    # (trust.json is per data folder = per body; one shared folder here plays both)


# ── shared vs local state ─────────────────────────────────────────

def test_local_state_never_changes_the_hash(tmp_path):
    d = _ext(tmp_path)
    h = ex.extension_hash(d)
    for sub in ("local", "data"):
        (d / sub).mkdir()
        (d / sub / "x.json").write_text("{}")
    assert ex.extension_hash(d) == h



def test_placement_is_not_an_extension(tmp_path):
    _ext(tmp_path, routines=[MIND_R])
    ex.approve(tmp_path, "rhythms", body=WIN)
    assert [e["name"] for e in ex.discover(tmp_path, body=WIN)] == ["rhythms"]


def test_default_body_comes_from_body_identity(tmp_path):
    from harness import body_identity as bi
    _ext(tmp_path, routines=[MIND_R])
    ex.approve(tmp_path, "rhythms")
    assert ex.placement(tmp_path) == {"rhythms/council": bi.body_info(tmp_path)["body_id"]}
