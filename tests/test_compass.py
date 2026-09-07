import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from harness import compass
from tower.app import create_tower


def test_read_compass_from_json(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    cfile = cfg / "compass.json"
    cfile.write_text(json.dumps({
        "focused": "warden",
        "ambient": ["aedelgard"],
        "dormant": ["vinga"]
    }))

    st = compass.read_compass(cfg)
    assert st["focused"] == "warden"
    assert st["ambient"] == ["aedelgard"]
    assert st["dormant"] == ["vinga"]
    assert compass.get_focused(cfg) == "warden"


def test_read_compass_fallback_when_empty(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    st = compass.read_compass(cfg)
    assert st["focused"] is None
    assert st["ambient"] == []
    assert st["dormant"] == []
    assert compass.get_focused(cfg) is None


def test_read_compass_legacy_fallback(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "active_vision.txt").write_text("aedelgard\n")
    st = compass.read_compass(cfg)
    assert st["focused"] == "aedelgard"


def test_set_compass_updates_and_retires_legacy(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    vdir = cfg / "visions"
    vdir.mkdir()
    (vdir / "warden.md").write_text("# Warden")
    (vdir / "aedelgard.md").write_text("# Aedelgard")

    legacy = cfg / "active_vision.txt"
    legacy.write_text("old_vision")

    res = compass.set_compass(
        config_dir=cfg,
        focused="warden",
        ambient=["aedelgard"],
        dormant=[],
        set_by="test"
    )
    assert res["ok"] is True
    assert res["compass"]["focused"] == "warden"
    assert res["compass"]["ambient"] == ["aedelgard"]
    assert not legacy.exists(), "Legacy active_vision.txt must be retired upon set_compass"

    st = compass.read_compass(cfg)
    assert st["focused"] == "warden"
    assert st["ambient"] == ["aedelgard"]


def test_set_compass_rejects_unknown_vision(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    vdir = cfg / "visions"
    vdir.mkdir()
    (vdir / "warden.md").write_text("# Warden")

    res = compass.set_compass(config_dir=cfg, focused="nonexistent")
    assert "error" in res
    assert "not found" in res["error"]


def test_tower_compass_and_vision_endpoints(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    vdir = cfg / "visions"
    vdir.mkdir()
    (vdir / "warden.md").write_text("# Warden")
    (vdir / "aedelgard.md").write_text("# Aedelgard")
    (cfg / "compass.json").write_text(json.dumps({"focused": "warden", "ambient": [], "dormant": []}))

    agent = MagicMock()
    agent.memory.config_dir = str(cfg)
    app = create_tower(agent)
    client = app.test_client()

    # GET /api/compass
    res = client.get("/api/compass")
    assert res.status_code == 200
    assert res.get_json()["focused"] == "warden"

    # GET /api/vision
    res_v = client.get("/api/vision")
    assert res_v.status_code == 200
    assert res_v.get_json()["active"] == "warden"

    # POST legacy /api/vision mutates compass directly
    res_post_v = client.post("/api/vision", json={"name": "aedelgard"})
    assert res_post_v.status_code == 200
    assert res_post_v.get_json()["active"] == "aedelgard"
    assert compass.get_focused(cfg) == "aedelgard"

    # POST /api/compass sets back to warden
    res_post_c = client.post("/api/compass", json={"focused": "warden"})
    assert res_post_c.status_code == 200
    assert compass.get_focused(cfg) == "warden"
