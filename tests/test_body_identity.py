"""MANY BODIES, ONE MIND — body identity (docs/EXTENSIONS.md §12.1). Written first."""
import json
from pathlib import Path

import pytest

from harness import body_identity as bi

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("system,expected", [("Windows", "windows"), ("Darwin", "macos"),
                                             ("Linux", "linux"), ("FreeBSD", "linux")])
def test_os_name(system, expected):
    assert bi.os_name(system) == expected


def test_body_info_is_created_once_and_stable(tmp_path):
    a = bi.body_info(tmp_path)
    assert set(a) >= {"body_id", "name", "os", "machine", "created_at"}
    assert len(a["body_id"]) >= 16 and a["os"] in ("windows", "macos", "linux")
    assert bi.body_info(tmp_path) == a
    assert json.loads((tmp_path / "body.json").read_text())["body_id"] == a["body_id"]


def test_copied_folder_on_another_machine_gets_a_new_body(tmp_path, monkeypatch):
    a = bi.body_info(tmp_path)
    monkeypatch.setattr(bi, "machine_fingerprint", lambda: "another-machine")
    b = bi.body_info(tmp_path)
    assert b["body_id"] != a["body_id"] and b["machine"] == "another-machine"
    assert bi.body_info(tmp_path) == b


def test_rename(tmp_path):
    bi.body_info(tmp_path)
    assert bi.rename(tmp_path, "desk")["name"] == "desk"
    assert bi.body_info(tmp_path)["name"] == "desk"
    for bad in ("", "x" * 41, "a\nb"):
        with pytest.raises(ValueError):
            bi.rename(tmp_path, bad)


def test_register_upserts_and_is_quiet_on_the_same_day(tmp_path):
    me = bi.body_info(tmp_path)
    reg = bi.register(tmp_path, today="2026-10-06")
    assert reg[me["body_id"]]["first_seen"] == "2026-10-06"
    assert reg[me["body_id"]]["last_seen"] == "2026-10-06"
    p = tmp_path / "bodies.json"
    t = p.stat().st_mtime_ns
    bi.register(tmp_path, today="2026-10-06")
    assert p.stat().st_mtime_ns == t  # no rewrite, no backup wake-up
    reg = bi.register(tmp_path, today="2026-10-07")
    assert reg[me["body_id"]]["first_seen"] == "2026-10-06"
    assert reg[me["body_id"]]["last_seen"] == "2026-10-07"


def test_register_keeps_other_bodies(tmp_path):
    (tmp_path / "bodies.json").write_text(json.dumps(
        {"other": {"name": "laptop", "os": "macos", "first_seen": "2026-10-01", "last_seen": "2026-10-02"}}))
    reg = bi.register(tmp_path, today="2026-10-06")
    assert "other" in reg and len(reg) == 2


def test_prompt_line(tmp_path):
    bi.rename(tmp_path, "desk")
    me = bi.body_info(tmp_path)
    assert bi.prompt_line(tmp_path).startswith(f"You are on body 'desk' ({me['os']}).")
    (tmp_path / "bodies.json").write_text(json.dumps(
        {"other": {"name": "laptop", "os": "macos", "first_seen": "2026-10-01", "last_seen": "2026-10-02"}}))
    line = bi.prompt_line(tmp_path)
    assert "'laptop' (macos)" in line and "2026-10-02" not in line  # no daily cache churn


def test_prompt_carries_the_body_line(tmp_path):
    from harness.memory import MemoryManager
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "SOUL.md").write_text("soul\n")
    (tmp_path / "memory").mkdir()
    m = MemoryManager(str(tmp_path / "config"), str(tmp_path / "memory"))
    assert "You are on body '" in m.build_stable_text()


def test_boot_registers_the_body():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "body_identity" in src and "register(" in src


def test_random_node_id_is_not_part_of_the_fingerprint(monkeypatch):
    # uuid.getnode() returns a RANDOM value (multicast
    # bit set) when no hardware address is found, different in every process.
    # Folded into the fingerprint, every boot would look like a copied folder.
    import uuid
    monkeypatch.setattr(uuid, "getnode", lambda: 0x010000000001)  # multicast bit set
    a = bi.machine_fingerprint()
    monkeypatch.setattr(uuid, "getnode", lambda: 0x01FFFFFFFFFF)
    assert bi.machine_fingerprint() == a


def test_copied_folder_on_the_same_machine_is_a_new_body(tmp_path):
    # two copies of one mind on ONE machine (a test copy,
    # a second server-mode instance) would share a body_id and both fire the
    # mind's once-only routines. A body is (machine, data folder).
    import shutil
    a = tmp_path / "a"
    a.mkdir()
    first = bi.body_info(a)
    b = tmp_path / "b"
    shutil.copytree(a, b)
    second = bi.body_info(b)
    assert second["body_id"] != first["body_id"]
    assert bi.body_info(a) == first
    assert bi.body_info(b) == second
