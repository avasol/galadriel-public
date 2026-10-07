"""EXTENSIONS — signing (docs/EXTENSIONS.md). Planner-written, protected.

Ed25519. An author signs the canonical AEDEXT.json (everything except
"signatures"); the declared sha256 inside it already binds every file, so the
signature binds the whole package. The catalogue (Aedelgard) countersigns the
same bytes PLUS the author's key, under a different domain, so a review can
never be lifted onto another author's package. Verification is offline.

A signature that is present but does not verify is tampering: refused, never
downgraded to "unsigned". A review by a key this body does not know is not
tampering (it may be a newer catalogue key): shown as not reviewed, with a note.
"""
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from harness import ext_signing as s

ROOT = Path(__file__).resolve().parent.parent


def _meta(**over):
    m = {"format": 1, "name": "rhythms", "version": "1.0.0", "kind": "declarative",
         "sha256": "ab" * 32, "files": 2, "signatures": []}
    m.update(over)
    return m


def _pkg(meta, payload=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AEDEXT.json", json.dumps(meta))
        for arc, data in (payload or {"payload/extension.json": b"{}",
                                      "payload/layers/main.md": b"hello\n"}).items():
            z.writestr(arc, data)
    return buf.getvalue()


def _read(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


# ── canonical bytes + key encoding ────────────────────────────────────────

def test_canonical_ignores_signatures_and_key_order():
    a = _meta()
    b = dict(reversed(list(_meta(signatures=[{"by": "author"}]).items())))
    assert s.canonical_meta(a) == s.canonical_meta(b)
    assert b"signatures" not in s.canonical_meta(a)
    assert s.canonical_meta(a) == json.dumps(
        {k: v for k, v in a.items() if k != "signatures"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def test_domains_are_distinct_constants():
    assert s.DOMAIN_AUTHOR == b"aedelgard-aedext-author-v1\n"
    assert s.DOMAIN_REVIEW == b"aedelgard-aedext-review-v1\n"


def test_key_shape_and_fingerprint():
    k = s.generate_key()
    assert k["format"] == 1 and k["public"].startswith("ed25519:")
    raw = s.decode_pub(k["public"])
    assert len(raw) == 32
    fp = s.fingerprint(k["public"])
    assert len(fp) == 19 and fp.count("-") == 3
    assert fp == s.fingerprint(k["public"])
    assert fp != s.fingerprint(s.generate_key()["public"])


@pytest.mark.parametrize("bad", ["", "ed25519:", "rsa:AAAA", "ed25519:!!!", "ed25519:" + "A" * 10, 7, None])
def test_decode_pub_refuses_garbage(bad):
    with pytest.raises(s.SigningError):
        s.decode_pub(bad)


def test_catalogue_keys_ship_empty_until_minted():
    """The real catalogue key is minted with step 3; until then nothing is 'reviewed'."""
    assert s.CATALOGUE_KEYS == {}


# ── author signatures ─────────────────────────────────────────────────────

def test_unsigned_verifies_as_unsigned():
    v = s.verify(_meta())
    assert v["author"] is None and v["fingerprint"] is None
    assert v["reviewed"] is False and v["review_key_id"] is None


def test_author_signature_roundtrip():
    k = s.generate_key()
    m = _meta()
    sig = s.sign_author(m, k)
    assert sig["by"] == "author" and sig["key"] == k["public"] and isinstance(sig["sig"], str)
    m["signatures"] = [sig]
    v = s.verify(m)
    assert v["author"] == k["public"] and v["fingerprint"] == s.fingerprint(k["public"])
    assert v["reviewed"] is False


@pytest.mark.parametrize("field,value", [("sha256", "cd" * 32), ("version", "1.0.1"),
                                         ("name", "tides"), ("kind", "code"), ("files", 3)])
def test_any_change_to_signed_meta_is_tampering(field, value):
    k = s.generate_key()
    m = _meta()
    m["signatures"] = [s.sign_author(m, k)]
    m[field] = value
    with pytest.raises(s.SigningError):
        s.verify(m)


def test_signature_under_the_wrong_domain_fails():
    """A raw signature over the canonical bytes WITHOUT the author domain must not verify."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = s.generate_key()
    priv = Ed25519PrivateKey.from_private_bytes(s._b64d(k["private"]))
    m = _meta()
    m["signatures"] = [{"by": "author", "key": k["public"],
                        "sig": s._b64e(priv.sign(s.canonical_meta(m)))}]
    with pytest.raises(s.SigningError):
        s.verify(m)


def test_swapped_author_key_fails():
    k1, k2 = s.generate_key(), s.generate_key()
    m = _meta()
    sig = s.sign_author(m, k1)
    sig["key"] = k2["public"]
    m["signatures"] = [sig]
    with pytest.raises(s.SigningError):
        s.verify(m)


@pytest.mark.parametrize("sigs", [
    "nope", {"by": "author"}, [1], [{"by": "stranger", "sig": "x"}],
    [{"by": "author", "key": "ed25519:AAAA", "sig": "x"}],
    [{"by": "author", "sig": "x"}],
])
def test_malformed_signatures_are_refused(sigs):
    with pytest.raises(s.SigningError):
        s.verify(_meta(signatures=sigs))


def test_two_author_signatures_are_refused():
    k = s.generate_key()
    m = _meta()
    a = s.sign_author(m, k)
    m["signatures"] = [a, dict(a)]
    with pytest.raises(s.SigningError):
        s.verify(m)


def test_too_many_signatures_are_refused():
    m = _meta(signatures=[{"by": "aedelgard", "key_id": str(i), "sig": "x"} for i in range(5)])
    with pytest.raises(s.SigningError):
        s.verify(m)


# ── the catalogue countersignature ────────────────────────────────────────

def _reviewed(cat, key_id="cat-1"):
    a = s.generate_key()
    m = _meta()
    m["signatures"] = [s.sign_author(m, a), s.sign_review(m, a["public"], cat, key_id)]
    return a, m


def test_review_roundtrip_with_known_key():
    cat = s.generate_key()
    a, m = _reviewed(cat)
    v = s.verify(m, {"cat-1": cat["public"]})
    assert v["reviewed"] is True and v["review_key_id"] == "cat-1"
    assert v["author"] == a["public"]


def test_review_is_bound_to_the_author():
    """A review lifted onto a different author's signature must fail."""
    cat = s.generate_key()
    _, m = _reviewed(cat)
    other = s.generate_key()
    m["signatures"][0] = s.sign_author(m, other)
    with pytest.raises(s.SigningError):
        s.verify(m, {"cat-1": cat["public"]})


def test_bad_review_with_known_key_is_tampering():
    cat, imposter = s.generate_key(), s.generate_key()
    _, m = _reviewed(imposter)
    with pytest.raises(s.SigningError):
        s.verify(m, {"cat-1": cat["public"]})


def test_review_by_unknown_key_is_not_reviewed_but_not_refused():
    cat = s.generate_key()
    a, m = _reviewed(cat, key_id="cat-9")
    v = s.verify(m, {"cat-1": s.generate_key()["public"]})
    assert v["reviewed"] is False and v["author"] == a["public"]
    assert v["notes"] and any("cat-9" in n for n in v["notes"])


def test_review_without_author_is_refused():
    cat = s.generate_key()
    m = _meta()
    m["signatures"] = [s.sign_review(m, "", cat, "cat-1")]
    with pytest.raises(s.SigningError):
        s.verify(m, {"cat-1": cat["public"]})


def test_verify_defaults_to_shipped_catalogue_keys(monkeypatch):
    cat = s.generate_key()
    _, m = _reviewed(cat)
    monkeypatch.setattr(s, "CATALOGUE_KEYS", {"cat-1": cat["public"]})
    assert s.verify(m)["reviewed"] is True


# ── key files ─────────────────────────────────────────────────────────────

def test_key_file_roundtrip_private_and_no_overwrite(tmp_path):
    p = tmp_path / "k" / "author_key.json"
    k = s.generate_key()
    s.save_key(p, k)
    assert s.load_key(p) == k
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    with pytest.raises(s.SigningError):
        s.save_key(p, s.generate_key())
    assert s.load_key(p) == k


def test_load_key_refuses_garbage(tmp_path):
    p = tmp_path / "k.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(s.SigningError):
        s.load_key(p)
    with pytest.raises(s.SigningError):
        s.load_key(tmp_path / "missing.json")


def test_author_key_get_or_create(tmp_path):
    p = tmp_path / "author_key.json"
    k1 = s.author_key(p)
    k2 = s.author_key(p)
    assert k1 == k2 and p.is_file()


# ── signing a package ─────────────────────────────────────────────────────

def test_sign_package_adds_one_author_sig_and_keeps_payload():
    m = _meta()
    blob = _pkg(m)
    k = s.generate_key()
    signed = s.sign_package(blob, k)
    before, after = _read(blob), _read(signed)
    assert set(before) == set(after)
    for n in before:
        if n != "AEDEXT.json":
            assert before[n] == after[n]
    meta = json.loads(after["AEDEXT.json"])
    assert [x["by"] for x in meta["signatures"]] == ["author"]
    assert s.verify(meta)["author"] == k["public"]


def test_resigning_replaces_the_author_and_drops_reviews():
    """A new author signature invalidates any review (it was bound to the old author)."""
    cat = s.generate_key()
    _, m = _reviewed(cat)
    k = s.generate_key()
    meta = json.loads(_read(s.sign_package(_pkg(m), k))["AEDEXT.json"])
    assert [x["by"] for x in meta["signatures"]] == ["author"]
    assert meta["signatures"][0]["key"] == k["public"]


def test_sign_package_refuses_non_packages():
    with pytest.raises(s.SigningError):
        s.sign_package(b"garbage", s.generate_key())
    with pytest.raises(s.SigningError):
        s.sign_package(_no_meta(), s.generate_key())


def _no_meta():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("payload/extension.json", b"{}")
    return buf.getvalue()


def test_package_status_reads_and_verifies():
    k = s.generate_key()
    signed = s.sign_package(_pkg(_meta()), k)
    assert s.package_status(signed)["author"] == k["public"]
    assert s.package_status(_pkg(_meta()))["author"] is None


# ── the author CLI ────────────────────────────────────────────────────────

def _cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "harness.ext_signing", *args],
                          capture_output=True, text=True, cwd=str(ROOT), timeout=60)


def test_cli_keygen_sign_verify(tmp_path):
    key = tmp_path / "me.json"
    pkg = tmp_path / "x.aedext"
    pkg.write_bytes(_pkg(_meta()))
    r = _cli("keygen", str(key), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ed25519:" not in r.stdout or "private" not in r.stdout.lower()
    r = _cli("sign", str(pkg), str(key), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = tmp_path / "x.signed.aedext"
    assert out.is_file()
    r = _cli("verify", str(out), cwd=tmp_path)
    assert r.returncode == 0 and s.fingerprint(s.load_key(key)["public"]) in r.stdout
    r = _cli("keygen", str(key), cwd=tmp_path)
    assert r.returncode != 0          # never overwrites a key


def test_cli_verify_tampered_fails(tmp_path):
    k = s.generate_key()
    m = _meta()
    m["signatures"] = [s.sign_author(m, k)]
    m["version"] = "9.9.9"
    pkg = tmp_path / "t.aedext"
    pkg.write_bytes(_pkg(m))
    r = _cli("verify", str(pkg), cwd=tmp_path)
    assert r.returncode != 0
