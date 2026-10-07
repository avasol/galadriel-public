"""EXTENSIONS — the .aedext package (docs/EXTENSIONS.md). Planner-written, protected.

An .aedext is a zip: AEDEXT.json at the top, the extension's files under
payload/. Export leaves out data/, local/, caches and this body's trust.
Import is hostile-input code: it refuses anything that could write outside
the extension folder, anything too large, and any package whose files do not
hash to the hash it declares. An imported extension ALWAYS arrives awaiting
approval on this body, even when it replaces one that was approved.
"""
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from harness import extensions as ex
from tower.app import create_tower

ROOT = Path(__file__).resolve().parent.parent


def _make(root, name="rhythms", version="1.0.0", layer="hello\n", kind="declarative"):
    d = Path(root) / "extensions" / name
    (d / "layers").mkdir(parents=True, exist_ok=True)
    (d / "layers" / "main.md").write_text(layer, encoding="utf-8")
    (d / "extension.json").write_text(json.dumps({
        "name": name, "version": version, "title": name.title(), "description": "t",
        "kind": kind, "body_min": "0.0.0",
        "contributes": {"layers": [{"file": "layers/main.md", "title": "Main"}],
                        "routines": [], "tools": [], "routes": [], "hooks": []},
        "keyring_slots": [], "requires": []}), encoding="utf-8")
    return d


def _state(root, name):
    return {e["name"]: e for e in ex.discover(root)}[name]["state"]


def _zip(entries, meta=None):
    """Build a raw .aedext from {arcname: bytes}; meta is AEDEXT.json (dict) or None."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if meta is not None:
            z.writestr("AEDEXT.json", json.dumps(meta))
        for arc, data in entries.items():
            z.writestr(arc, data)
    return buf.getvalue()


def _repack(blob, mutate):
    """Unpack an .aedext into {arc: bytes}, let mutate() change it, repack."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        files = {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}
    meta = json.loads(files.pop("AEDEXT.json"))
    mutate(files, meta)
    return _zip(files, meta)


# ── export ────────────────────────────────────────────────────────────────

def test_export_layout_and_meta(tmp_path):
    d = _make(tmp_path)
    blob = ex.export_aedext(tmp_path, "rhythms")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
        meta = json.loads(z.read("AEDEXT.json"))
    assert "payload/extension.json" in names and "payload/layers/main.md" in names
    assert all(n == "AEDEXT.json" or n.startswith("payload/") for n in names)
    assert meta["format"] == 1
    assert meta["name"] == "rhythms" and meta["version"] == "1.0.0"
    assert meta["kind"] == "declarative"
    assert meta["sha256"] == ex.extension_hash(d)
    assert meta["signatures"] == []


def test_export_leaves_out_state_caches_and_trust(tmp_path):
    d = _make(tmp_path)
    (d / "data").mkdir(); (d / "data" / "secret.txt").write_text("mine", encoding="utf-8")
    (d / "local").mkdir(); (d / "local" / "x.txt").write_text("here", encoding="utf-8")
    (d / "__pycache__").mkdir(); (d / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\0")
    ex.approve(tmp_path, "rhythms")
    blob = ex.export_aedext(tmp_path, "rhythms")
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert not any("data/" in n or "local/" in n or "__pycache__" in n or n.endswith(".pyc")
                   or n.endswith("trust.json") for n in names)


def test_export_unknown_raises(tmp_path):
    with pytest.raises(ex.ExtensionError):
        ex.export_aedext(tmp_path, "nope")


# ── import: the happy paths ───────────────────────────────────────────────

def test_roundtrip_to_a_fresh_body_arrives_awaiting(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    _make(src)
    ex.approve(src, "rhythms")
    blob = ex.export_aedext(src, "rhythms")
    rec = ex.import_aedext(dst, blob)
    assert rec["name"] == "rhythms"
    assert _state(dst, "rhythms") == "awaiting_approval"
    assert ex.extension_hash(dst / "extensions" / "rhythms") == \
        ex.extension_hash(src / "extensions" / "rhythms")


def test_import_existing_without_replace_is_refused(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    _make(src)
    blob = ex.export_aedext(src, "rhythms")
    ex.import_aedext(dst, blob)
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(dst, blob)


def test_replace_keeps_data_and_drops_approval(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    _make(dst, layer="old\n")
    (dst / "extensions" / "rhythms" / "data").mkdir()
    (dst / "extensions" / "rhythms" / "data" / "state.json").write_text("{}", encoding="utf-8")
    ex.approve(dst, "rhythms")
    assert _state(dst, "rhythms") == "enabled"
    _make(src, version="1.1.0", layer="new\n")
    ex.import_aedext(dst, ex.export_aedext(src, "rhythms"), replace=True)
    d = dst / "extensions" / "rhythms"
    assert (d / "layers" / "main.md").read_text(encoding="utf-8") == "new\n"
    assert (d / "data" / "state.json").is_file()
    # even a DECLARATIVE replacement must be approved again on this body
    assert _state(dst, "rhythms") == "awaiting_approval"


def test_failed_import_leaves_no_trace(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    _make(src)
    bad = _repack(ex.export_aedext(src, "rhythms"),
                  lambda f, m: f.__setitem__("payload/layers/main.md", b"tampered\n"))
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(dst, bad)
    edir = dst / "extensions"
    left = [p.name for p in edir.iterdir()] if edir.exists() else []
    assert left == [] or left == ["trust.json"]


def test_failed_replace_keeps_the_old_one(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    _make(dst, layer="old\n")
    ex.approve(dst, "rhythms")
    _make(src, layer="new\n")
    bad = _repack(ex.export_aedext(src, "rhythms"),
                  lambda f, m: m.__setitem__("sha256", "0" * 64))
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(dst, bad, replace=True)
    assert (dst / "extensions" / "rhythms" / "layers" / "main.md").read_text(encoding="utf-8") == "old\n"
    assert _state(dst, "rhythms") == "enabled"


# ── import: hostile packages ──────────────────────────────────────────────

def _good(tmp_path):
    src = tmp_path / "src"
    _make(src)
    return ex.export_aedext(src, "rhythms")


@pytest.mark.parametrize("mutate", [
    lambda f, m: f.__setitem__("payload/layers/main.md", b"tampered\n"),     # content changed
    lambda f, m: f.__setitem__("payload/layers/extra.md", b"sneaked in\n"),  # file added
    lambda f, m: m.__setitem__("sha256", "0" * 64),                          # hash lies
    lambda f, m: m.__setitem__("format", 2),                                 # unknown format
    lambda f, m: m.__setitem__("name", "other"),                             # meta != manifest
    lambda f, m: m.__setitem__("name", "../evil"),                           # bad name
    lambda f, m: f.pop("payload/extension.json"),                            # no manifest
    lambda f, m: f.__setitem__("payload/data/plant.txt", b"x"),              # ships state
    lambda f, m: f.__setitem__("payload/local/plant.txt", b"x"),             # ships local state
    lambda f, m: f.__setitem__("payload/../../escape.txt", b"x"),            # zip-slip
    lambda f, m: f.__setitem__("/abs.txt", b"x"),                            # absolute path
    lambda f, m: f.__setitem__("payload/..\\..\\win.txt", b"x"),             # backslash traversal
    lambda f, m: f.__setitem__("payload/C:/drive.txt", b"x"),                # drive letter
    lambda f, m: f.__setitem__("outside.txt", b"x"),                         # outside payload/
])
def test_hostile_package_refused(tmp_path, mutate):
    bad = _repack(_good(tmp_path), mutate)
    dst = tmp_path / "dst"
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(dst, bad)
    assert not (tmp_path / "escape.txt").exists()
    assert not (dst / "escape.txt").exists()
    assert not (dst / "extensions" / "rhythms").exists()


def test_not_a_zip_or_no_meta_refused(tmp_path):
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(tmp_path, b"this is not a zip")
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(tmp_path, _zip({"payload/extension.json": b"{}"}, meta=None))


def test_symlink_entry_refused(tmp_path):
    good = _good(tmp_path)
    with zipfile.ZipFile(io.BytesIO(good)) as z:
        files = {i.filename: z.read(i) for i in z.infolist()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for arc, data in files.items():
            z.writestr(arc, data)
        info = zipfile.ZipInfo("payload/layers/link.md")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(tmp_path / "dst", buf.getvalue())


def test_size_limits_refuse_bombs(tmp_path):
    assert ex.AEDEXT_MAX_BYTES <= 10 * 1024 * 1024
    assert ex.AEDEXT_MAX_UNPACKED <= 50 * 1024 * 1024
    assert ex.AEDEXT_MAX_FILES <= 1000
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(tmp_path, b"\0" * (ex.AEDEXT_MAX_BYTES + 1))
    bomb = _repack(_good(tmp_path), lambda f, m: f.__setitem__(
        "payload/layers/big.md", b"a" * (ex.AEDEXT_MAX_UNPACKED + 1)))
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(tmp_path / "dst", bomb)
    many = _repack(_good(tmp_path), lambda f, m: f.update(
        {f"payload/layers/n{i}.md": b"x" for i in range(ex.AEDEXT_MAX_FILES + 1)}))
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(tmp_path / "dst2", many)


def test_code_extension_imports_but_never_runs_unapproved(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    d = _make(src, name="tooly", kind="code")
    (d / "extension.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    m = json.loads((d / "extension.json").read_text(encoding="utf-8"))
    m["contributes"]["layers"] = []
    (d / "extension.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "layers" / "main.md").unlink()
    ex.import_aedext(dst, ex.export_aedext(src, "tooly"))
    assert _state(dst, "tooly") == "awaiting_approval"
    assert ex.enabled_code(dst) == []


# ── the Tower doors + the Lodge ───────────────────────────────────────────

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


def test_export_route(client):
    c, root = client
    r = c.get("/api/extensions/rhythms/export")
    assert r.status_code == 200
    assert "rhythms-1.0.0.aedext" in r.headers.get("Content-Disposition", "")
    meta = json.loads(zipfile.ZipFile(io.BytesIO(r.data)).read("AEDEXT.json"))
    assert meta["sha256"] == ex.extension_hash(root / "extensions" / "rhythms")
    assert c.get("/api/extensions/nope/export").status_code == 404


def test_import_route(client, tmp_path):
    c, root = client
    other = tmp_path / "other"
    _make(other, name="tides")
    blob = ex.export_aedext(other, "tides")
    r = c.post("/api/extensions/import", data=blob,
               headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["extension"]["state"] == "awaiting_approval"
    # second time without replace: refused, plain message
    r2 = c.post("/api/extensions/import", data=blob,
                headers={"Content-Type": "application/octet-stream"})
    assert r2.status_code == 400 and r2.get_json().get("error")
    r3 = c.post("/api/extensions/import?replace=1", data=blob,
                headers={"Content-Type": "application/octet-stream"})
    assert r3.status_code == 200


def test_import_route_refuses_bad_and_oversized(client):
    c, _ = client
    r = c.post("/api/extensions/import", data=b"garbage",
               headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 400
    big = b"\0" * (ex.AEDEXT_MAX_BYTES + 1)
    r = c.post("/api/extensions/import", data=big,
               headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 413


def test_import_route_demands_octet_stream(client, tmp_path):
    """A plain cross-site <form> can only send urlencoded/multipart/text-plain;
    demanding application/octet-stream forces a CORS preflight the Tower never grants."""
    c, _ = client
    other = tmp_path / "other2"
    _make(other, name="tides")
    blob = ex.export_aedext(other, "tides")
    for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        r = c.post("/api/extensions/import", data=blob, headers={"Content-Type": ct})
        assert r.status_code == 415, ct




def _rehash(files, meta):
    """Recompute meta['sha256'] the way extension_hash does, so a test package
    is refused for its NAMES, not for a hash mismatch."""
    import hashlib
    entries = []
    for arc, data in files.items():
        if not arc.startswith("payload/"):
            continue
        rel = arc[len("payload/"):]
        parts = rel.split("/")
        if parts[0] in ("data", "local") or "__pycache__" in parts or rel.endswith(".pyc"):
            continue
        entries.append((rel, hashlib.sha256(data).hexdigest()))
    h = hashlib.sha256()
    for rel, fh in sorted(entries):
        h.update(f"{rel}\0{fh}\n".encode("utf-8"))
    meta["sha256"] = h.hexdigest()


# ── review findings (2026-10-07): Windows/macOS filesystems + replace safety ──

@pytest.mark.parametrize("arc", [
    "payload/Data/plant.txt",          # case-insensitive FS: lands in data/
    "payload/LOCAL/plant.txt",
    "payload/data./plant.txt",         # Windows strips the trailing dot -> data/
    "payload/layers/x.md.",            # trailing dot
    "payload/layers/x.md ",            # trailing space
    "payload/layers/con.md",           # Windows device name
    "payload/layers/NUL",
    "payload/layers/com1.txt",
    "payload/layers/bad?name.md",      # outside the safe character set
    "payload/layers/tab\tname.md",
    "payload/__PYCACHE__/m.txt",
])
def test_windows_and_mac_unsafe_names_refused(tmp_path, arc):
    bad = _repack(_good(tmp_path), lambda f, m: (f.__setitem__(arc, b"x"), _rehash(f, m)))
    dst = tmp_path / "dst"
    with pytest.raises(ex.ExtensionError) as ei:
        ex.import_aedext(dst, bad)
    assert "hash" not in str(ei.value), "refused for the hash, not the name"
    assert not (dst / "extensions" / "rhythms").exists()


def test_case_only_duplicates_refused(tmp_path):
    bad = _repack(_good(tmp_path), lambda f, m: f.update(
        {"payload/layers/a.md": b"one", "payload/layers/A.md": b"two"}) or _rehash(f, m))
    with pytest.raises(ex.ExtensionError) as ei:
        ex.import_aedext(tmp_path / "dst", bad)
    assert "hash" not in str(ei.value)


def test_safe_ordinary_names_still_import(tmp_path):
    src = tmp_path / "src"
    d = _make(src)
    (d / "layers" / "notes_v2-final.md").write_text("ok\n", encoding="utf-8")
    (d / "commands").mkdir()
    (d / "commands" / ".gitkeep").write_text("", encoding="utf-8")
    ex.import_aedext(tmp_path / "dst", ex.export_aedext(src, "rhythms"))
    assert (tmp_path / "dst" / "extensions" / "rhythms" / "layers" / "notes_v2-final.md").is_file()


def test_failed_swap_never_loses_data(tmp_path, monkeypatch):
    """If the final rename fails, the old extension AND its data/ must be back in place."""
    src, dst = tmp_path / "a", tmp_path / "b"
    _make(dst, layer="old\n")
    dd = dst / "extensions" / "rhythms" / "data"
    dd.mkdir()
    (dd / "precious.json").write_text('{"keep": true}', encoding="utf-8")
    _make(src, layer="new\n")
    blob = ex.export_aedext(src, "rhythms")

    real_replace = ex.os.replace

    def flaky(a, b):
        if Path(a).name.startswith(".import-") and Path(b).name == "rhythms":
            raise OSError("simulated: file in use")
        return real_replace(a, b)

    monkeypatch.setattr(ex.os, "replace", flaky)
    with pytest.raises(ex.ExtensionError):
        ex.import_aedext(dst, blob, replace=True)
    monkeypatch.setattr(ex.os, "replace", real_replace)
    t = dst / "extensions" / "rhythms"
    assert (t / "layers" / "main.md").read_text(encoding="utf-8") == "old\n"
    assert (t / "data" / "precious.json").read_text(encoding="utf-8") == '{"keep": true}'
    left = sorted(p.name for p in (dst / "extensions").iterdir())
    assert all(not n.startswith(".") for n in left), left


def test_leftover_work_dirs_are_not_shown_as_extensions(tmp_path):
    src = tmp_path / "src"
    _make(src)
    edir = tmp_path / "dst" / "extensions"
    (edir / ".import-abc").mkdir(parents=True)
    (edir / ".import-abc" / "extension.json").write_text("{}", encoding="utf-8")
    (edir / ".old-rhythms-1").mkdir()
    (edir / ".old-rhythms-1" / "extension.json").write_text("{}", encoding="utf-8")
    assert ex.discover(tmp_path / "dst") == []


# ── one hash across every implementation ──────────────────────────────────

def _reference_hash(ext_dir):
    """The package hash, written out: only TOP-LEVEL data/ and local/ are left out
    (plus caches); entries are sorted by their posix path STRING."""
    import hashlib
    ext_dir = Path(ext_dir)
    rows = []
    for p in ext_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ext_dir)
        if rel.parts[0] in ("data", "local") or "__pycache__" in rel.parts or rel.suffix == ".pyc":
            continue
        rows.append((rel.as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()))
    rows.sort(key=lambda t: t[0])
    h = hashlib.sha256()
    for rel, fh in rows:
        h.update(f"{rel}\0{fh}\n".encode("utf-8"))
    return h.hexdigest()


def test_hash_matches_the_reference(tmp_path):
    d = _make(tmp_path)
    for rel in ("a-b/x.md", "a/x.md", "layers/data/inner.md", "pkg/local/z.py", "data/state.json"):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rel, encoding="utf-8")
    assert ex.extension_hash(d) == _reference_hash(d)


def test_nested_data_folder_is_covered_by_the_hash(tmp_path):
    """Only the top-level data/ is state; a nested data/ is content and must be pinned."""
    d = _make(tmp_path)
    (d / "pkg" / "data").mkdir(parents=True)
    f = d / "pkg" / "data" / "helper.py"
    f.write_text("x = 1\n", encoding="utf-8")
    before = ex.extension_hash(d)
    f.write_text("x = 2\n", encoding="utf-8")
    assert ex.extension_hash(d) != before
