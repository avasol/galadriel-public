"""EXTENSIONS — signed packages on import/export + author pinning (docs/EXTENSIONS.md). Planner-written, protected.

- Import verifies signatures offline; a broken signature refuses the import.
- The first import of a name pins its author key for this MIND
  (extensions/authors.json). A later package of the same
  name by a different author, or unsigned while an author is pinned, is a
  DIFFERENT AUTHOR: refused unless the user explicitly accepts it (new_author).
- Every record carries provenance for the Lodge: signed / author fingerprint /
  reviewed / edited since import.
- Export can sign with this mind's author key (extensions/author_key.json,
  created on first use, 0600).
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from harness import extensions as ex
from harness import ext_signing as s
from tower.app import create_tower


def _make(root, name="rhythms", version="1.0.0", layer="hello\n"):
    d = Path(root) / "extensions" / name
    (d / "layers").mkdir(parents=True, exist_ok=True)
    (d / "layers" / "main.md").write_text(layer, encoding="utf-8")
    (d / "extension.json").write_text(json.dumps({
        "name": name, "version": version, "title": name.title(), "description": "t",
        "kind": "declarative", "body_min": "0.0.0",
        "contributes": {"layers": [{"file": "layers/main.md", "title": "Main"}],
                        "routines": [], "tools": [], "routes": [], "hooks": []},
        "keyring_slots": [], "requires": []}), encoding="utf-8")
    return d


def _pkg(tmp_path, tag, key=None, version="1.0.0", layer="hello\n", name="rhythms"):
    src = tmp_path / ("src-" + tag)
    _make(src, name=name, version=version, layer=layer)
    blob = ex.export_aedext(src, name)
    return s.sign_package(blob, key) if key else blob


def _rec(root, name="rhythms"):
    return {e["name"]: e for e in ex.discover(root)}[name]


def _meta(blob):
    return json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("AEDEXT.json"))


def _repack_meta(blob, mutate):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        files = {i.filename: z.read(i) for i in z.infolist()}
    meta = json.loads(files["AEDEXT.json"])
    mutate(meta)
    files["AEDEXT.json"] = json.dumps(meta).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


# ── verification on import ────────────────────────────────────────────────

def test_signed_import_records_author(tmp_path):
    root = tmp_path / "mind"
    k = s.generate_key()
    ex.import_aedext(root, _pkg(tmp_path, "a", k))
    p = _rec(root)["provenance"]
    assert p == {"imported": True, "signed": True, "author": s.fingerprint(k["public"]),
                 "reviewed": False, "edited": False}
    pins = json.loads((root / "extensions" / "authors.json").read_text(encoding="utf-8"))
    assert pins["rhythms"]["key"] == k["public"]
    assert _rec(root)["state"] == "awaiting_approval"


def test_tampered_signed_package_is_refused_and_leaves_nothing(tmp_path):
    root = tmp_path / "mind"
    blob = _pkg(tmp_path, "a", s.generate_key())
    bad = _repack_meta(blob, lambda m: m.update(version="1.0.0", files=m["files"] + 1))
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(root, bad)
    assert not (root / "extensions" / "rhythms").exists()
    assert not (root / "extensions" / "authors.json").exists()


def test_unsigned_import_is_allowed_and_says_so(tmp_path):
    root = tmp_path / "mind"
    ex.import_aedext(root, _pkg(tmp_path, "a"))
    p = _rec(root)["provenance"]
    assert p["imported"] is True and p["signed"] is False and p["author"] is None


def test_hand_made_extension_has_neutral_provenance(tmp_path):
    _make(tmp_path)
    assert _rec(tmp_path)["provenance"] == {"imported": False, "signed": False, "author": None,
                                            "reviewed": False, "edited": False}


def test_local_edit_after_import_shows_edited(tmp_path):
    root = tmp_path / "mind"
    ex.import_aedext(root, _pkg(tmp_path, "a", s.generate_key()))
    (root / "extensions" / "rhythms" / "layers" / "main.md").write_text("changed\n", encoding="utf-8")
    assert _rec(root)["provenance"]["edited"] is True


def test_reviewed_package(tmp_path, monkeypatch):
    root = tmp_path / "mind"
    a, cat = s.generate_key(), s.generate_key()
    monkeypatch.setattr(s, "CATALOGUE_KEYS", {"cat-1": cat["public"]})
    blob = _pkg(tmp_path, "a", a)
    blob = _repack_meta(blob, lambda m: m["signatures"].append(
        s.sign_review(m, a["public"], cat, "cat-1")))
    ex.import_aedext(root, blob)
    assert _rec(root)["provenance"]["reviewed"] is True


# ── author pinning ────────────────────────────────────────────────────────

def test_same_author_update_is_accepted(tmp_path):
    root = tmp_path / "mind"
    k = s.generate_key()
    ex.import_aedext(root, _pkg(tmp_path, "a", k))
    ex.import_aedext(root, _pkg(tmp_path, "b", k, version="1.1.0"), replace=True)
    assert _rec(root)["version"] == "1.1.0"


def test_different_author_is_refused_without_consent(tmp_path):
    root = tmp_path / "mind"
    k1, k2 = s.generate_key(), s.generate_key()
    ex.import_aedext(root, _pkg(tmp_path, "a", k1))
    with pytest.raises(ex.ExtensionError) as e:
        ex.import_aedext(root, _pkg(tmp_path, "b", k2, version="1.1.0"), replace=True)
    assert "different author" in str(e.value)
    assert _rec(root)["version"] == "1.0.0"
    assert _rec(root)["provenance"]["author"] == s.fingerprint(k1["public"])


def test_unsigned_replacing_a_pinned_author_is_a_different_author(tmp_path):
    root = tmp_path / "mind"
    ex.import_aedext(root, _pkg(tmp_path, "a", s.generate_key()))
    with pytest.raises(ex.ExtensionError) as e:
        ex.import_aedext(root, _pkg(tmp_path, "b", version="1.1.0"), replace=True)
    assert "different author" in str(e.value)


def test_new_author_with_consent_repins(tmp_path):
    root = tmp_path / "mind"
    k1, k2 = s.generate_key(), s.generate_key()
    ex.import_aedext(root, _pkg(tmp_path, "a", k1))
    ex.import_aedext(root, _pkg(tmp_path, "b", k2, version="1.1.0"), replace=True, new_author=True)
    assert _rec(root)["provenance"]["author"] == s.fingerprint(k2["public"])
    assert _rec(root)["state"] == "awaiting_approval"


def test_pin_survives_removal_of_the_folder(tmp_path):
    """The pin belongs to the mind, not the folder: deleting and re-importing a stranger's
    package under the same name is still a different author."""
    import shutil
    root = tmp_path / "mind"
    ex.import_aedext(root, _pkg(tmp_path, "a", s.generate_key()))
    shutil.rmtree(root / "extensions" / "rhythms")
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(root, _pkg(tmp_path, "b", s.generate_key()))


def test_unsigned_then_signed_pins_the_signer(tmp_path):
    root = tmp_path / "mind"
    k = s.generate_key()
    ex.import_aedext(root, _pkg(tmp_path, "a"))
    ex.import_aedext(root, _pkg(tmp_path, "b", k, version="1.1.0"), replace=True)
    assert _rec(root)["provenance"]["author"] == s.fingerprint(k["public"])




# ── signed export ─────────────────────────────────────────────────────────

def test_export_signed_with_the_mind_author_key(tmp_path):
    _make(tmp_path)
    blob = ex.export_aedext(tmp_path, "rhythms", sign=True)
    kp = tmp_path / "extensions" / "author_key.json"
    assert kp.is_file()
    k = s.load_key(kp)
    assert s.package_status(blob)["author"] == k["public"]
    # the same key next time
    blob2 = ex.export_aedext(tmp_path, "rhythms", sign=True)
    assert s.package_status(blob2)["author"] == k["public"]
    # and unsigned stays the default
    assert _meta(ex.export_aedext(tmp_path, "rhythms"))["signatures"] == []


def test_signed_export_never_carries_the_private_key(tmp_path):
    _make(tmp_path)
    blob = ex.export_aedext(tmp_path, "rhythms", sign=True)
    priv = s.load_key(tmp_path / "extensions" / "author_key.json")["private"].encode()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for i in z.infolist():
            assert priv not in z.read(i)
            assert "author_key" not in i.filename


# ── the Tower doors ───────────────────────────────────────────────────────

class _Agent:
    """The smallest agent the Tower needs: the data root is memory_dir's parent."""
    def __init__(self, root):
        class _M:
            pass
        self.memory = _M()
        self.memory.memory_dir = str(Path(root) / "memory")
        self.conversations = {}


@pytest.fixture()
def client(tmp_path):
    _make(tmp_path)
    app = create_tower(_Agent(tmp_path))
    return app.test_client(), tmp_path


def test_export_route_signs_on_request(client):
    c, root = client
    r = c.get("/api/extensions/rhythms/export?sign=1")
    assert r.status_code == 200
    assert s.package_status(r.data)["author"] is not None
    r = c.get("/api/extensions/rhythms/export")
    assert s.package_status(r.data)["author"] is None


def test_author_route_shows_public_fingerprint_only(client):
    c, root = client
    r = c.get("/api/extensions/author")
    assert r.status_code == 200 and r.get_json()["fingerprint"] is None
    c.get("/api/extensions/rhythms/export?sign=1")
    d = c.get("/api/extensions/author").get_json()
    k = s.load_key(root / "extensions" / "author_key.json")
    assert d["fingerprint"] == s.fingerprint(k["public"])
    assert k["private"] not in json.dumps(d)


def test_import_route_new_author_needs_the_flag(client, tmp_path):
    c, root = client
    k1, k2 = s.generate_key(), s.generate_key()
    h = {"Content-Type": "application/octet-stream"}
    assert c.post("/api/extensions/import", data=_pkg(tmp_path, "a", k1, name="tides"),
                  headers=h).status_code == 200
    r = c.post("/api/extensions/import?replace=1",
               data=_pkg(tmp_path, "b", k2, name="tides", version="2.0.0"), headers=h)
    assert r.status_code == 400 and "different author" in r.get_json()["error"]
    r = c.post("/api/extensions/import?replace=1&new_author=1",
               data=_pkg(tmp_path, "c", k2, name="tides", version="2.0.0"), headers=h)
    assert r.status_code == 200

