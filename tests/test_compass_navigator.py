"""Acceptance tests (planner-written, protected): the compass auto-navigator.

After a completed user turn, score the recent conversation against the available
visions and shift the FOCUSED heading only when the evidence is unambiguous.
Patterns come from the visions themselves — no heading names are hardcoded.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import compass_navigator as nav  # noqa: E402


def _cfg(tmp_path, visions, focused=None, keywords=None):
    cfg = tmp_path / "config"
    (cfg / "visions").mkdir(parents=True)
    for v in visions:
        (cfg / "visions" / f"{v}.md").write_text(f"# {v}\n")
    for v, kws in (keywords or {}).items():
        (cfg / "visions" / f"{v}.json").write_text(json.dumps({"keywords": kws}))
    if focused:
        (cfg / "compass.json").write_text(json.dumps({"focused": focused, "ambient": [], "dormant": []}))
    return cfg


def _talk(*texts):
    out = []
    for i, t in enumerate(texts):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": t})
    return out


def test_no_hardcoded_heading_table():
    src = (Path(__file__).resolve().parent.parent / "harness" / "compass_navigator.py").read_text()
    assert "_VISION_KEYWORDS" not in src


def test_vision_name_is_a_pattern(tmp_path):
    cfg = _cfg(tmp_path, ["garden-plan", "taxes"])
    s = nav.score_conversation(_talk("the garden plan needs work", "yes, the garden_plan"), ["garden-plan", "taxes"], config_dir=cfg)
    assert s["garden-plan"] > 0 and s["taxes"] == 0


def test_profile_keywords_count(tmp_path):
    cfg = _cfg(tmp_path, ["garden", "taxes"], keywords={"taxes": [r"\binvoice\b", r"\bvat\b", "[unclosed"]})
    s = nav.score_conversation(_talk("the invoice and the vat return"), ["garden", "taxes"], config_dir=cfg)
    assert s["taxes"] >= 2   # the invalid regex is skipped, not fatal


def test_recent_turns_weigh_more(tmp_path):
    cfg = _cfg(tmp_path, ["garden", "taxes"])
    s = nav.score_conversation(_talk("taxes", "x", "x", "x", "x", "garden"), ["garden", "taxes"], config_dir=cfg)
    assert s["garden"] > s["taxes"]


def test_clear_evidence_shifts_and_logs(tmp_path):
    cfg = _cfg(tmp_path, ["garden", "taxes"], focused="garden",
               keywords={"taxes": [r"\binvoice\b", r"\bvat\b", r"\breceipt\b"]})
    msgs = _talk("taxes: the invoice", "vat on the receipt, taxes", "another invoice and receipt for taxes", "vat vat taxes")
    assert nav.auto_shift_compass(cfg, msgs) == "taxes"
    assert json.loads((cfg / "compass.json").read_text())["focused"] == "taxes"
    log = json.loads((cfg / "compass_auto_shifts.json").read_text())
    assert log[-1]["from"] == "garden" and log[-1]["to"] == "taxes"


def test_weak_or_split_evidence_does_not_shift(tmp_path):
    cfg = _cfg(tmp_path, ["garden", "taxes"], focused="garden")
    assert nav.auto_shift_compass(cfg, _talk("a word about taxes")) is None
    assert nav.auto_shift_compass(cfg, _talk("garden taxes", "garden taxes", "garden taxes", "garden taxes")) is None
    assert json.loads((cfg / "compass.json").read_text())["focused"] == "garden"


def test_no_visions_no_shift(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    assert nav.auto_shift_compass(cfg, _talk("anything")) is None


def test_never_raises(tmp_path):
    assert nav.auto_shift_compass(tmp_path / "nope", [{"role": "user", "content": None}]) is None


def test_agent_wires_it_with_a_kill_switch():
    src = (Path(__file__).resolve().parent.parent / "harness" / "agent.py").read_text()
    assert "auto_shift_compass(" in src
    assert "GALADRIEL_COMPASS_AUTOSHIFT" in src
