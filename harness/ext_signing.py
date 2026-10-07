"""Ed25519 signing for .aedext extension packages (docs/EXTENSIONS.md).

An author signs ``DOMAIN_AUTHOR + canonical_meta(meta)``; the catalogue
(Aedelgard) countersigns ``DOMAIN_REVIEW + canonical_meta(meta) + b"\\n" +
author_pub.encode("ascii")``.  The ``sha256`` recorded inside ``meta`` already
binds every file in the package, so a signature over the canonical metadata
binds the whole package.  The distinct review domain plus the author key in the
signed bytes means a review can never be lifted onto another author's package.

Verification is entirely offline.  A signature that is present but does not
verify is tampering and is refused, never downgraded to "unsigned".  A review
by a catalogue key this body does not know is not tampering (it may be a newer
catalogue key): it is reported as not reviewed, with a note.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class SigningError(Exception):
    """Raised when a key, signature or package cannot be trusted."""


DOMAIN_AUTHOR = b"aedelgard-aedext-author-v1\n"
DOMAIN_REVIEW = b"aedelgard-aedext-review-v1\n"

# key_id -> "ed25519:..."; filled when the catalogue key is minted (step 3);
# the rotation list ships in releases.
CATALOGUE_KEYS: dict = {}

MAX_SIGNATURES = 4


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    if not isinstance(s, str):
        raise SigningError("expected a base64 string")
    padded = s + "=" * (-len(s) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, TypeError, AttributeError) as exc:
        raise SigningError(f"invalid base64 value: {exc}") from exc


def canonical_meta(meta: dict) -> bytes:
    """The canonical bytes a signature covers: everything except signatures."""
    return json.dumps(
        {k: v for k, v in meta.items() if k != "signatures"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def encode_pub(raw: bytes) -> str:
    return "ed25519:" + _b64e(raw)


def decode_pub(s) -> bytes:
    if not isinstance(s, str) or not s.startswith("ed25519:"):
        raise SigningError("not an ed25519 public key")
    raw = _b64d(s[len("ed25519:"):])
    if len(raw) != 32:
        raise SigningError("an ed25519 public key must be 32 bytes")
    return raw


def fingerprint(pub: str) -> str:
    h = hashlib.sha256(decode_pub(pub)).hexdigest()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def generate_key() -> dict:
    priv = Ed25519PrivateKey.generate()
    return {
        "format": 1,
        "private": _b64e(priv.private_bytes_raw()),
        "public": encode_pub(priv.public_key().public_bytes_raw()),
    }


def _private(key: dict) -> Ed25519PrivateKey:
    if not isinstance(key, dict) or key.get("format") != 1:
        raise SigningError("not an ed25519 key")
    raw = _b64d(key.get("private"))
    if len(raw) != 32:
        raise SigningError("an ed25519 private key must be 32 bytes")
    priv = Ed25519PrivateKey.from_private_bytes(raw)
    if encode_pub(priv.public_key().public_bytes_raw()) != key.get("public"):
        raise SigningError("the key's public half does not match its private half")
    return priv


def save_key(path, key: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SigningError(f"a key already exists at {path}; refusing to overwrite")
    data = json.dumps(key, indent=2, sort_keys=True).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_key(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            key = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SigningError(f"cannot read key at {path}: {exc}") from exc
    _private(key)
    return key


def author_key(path) -> dict:
    path = Path(path)
    if path.exists():
        return load_key(path)
    key = generate_key()
    save_key(path, key)
    return key


def sign_author(meta: dict, key: dict) -> dict:
    sig = _private(key).sign(DOMAIN_AUTHOR + canonical_meta(meta))
    return {"by": "author", "key": key["public"], "sig": _b64e(sig)}


def sign_review(meta: dict, author_pub: str, key: dict, key_id: str) -> dict:
    message = (
        DOMAIN_REVIEW
        + canonical_meta(meta)
        + b"\n"
        + author_pub.encode("ascii")
    )
    sig = _private(key).sign(message)
    return {"by": "aedelgard", "key_id": key_id, "sig": _b64e(sig)}


def _check(pub_str, sig_str, message: bytes) -> bool:
    raw_pub = decode_pub(pub_str)
    raw_sig = _b64d(sig_str)
    if len(raw_sig) != 64:
        raise SigningError("an ed25519 signature must be 64 bytes")
    try:
        Ed25519PublicKey.from_public_bytes(raw_pub).verify(raw_sig, message)
    except InvalidSignature:
        return False
    return True


def verify(meta: dict, catalogue_keys=None) -> dict:
    keys = CATALOGUE_KEYS if catalogue_keys is None else catalogue_keys
    sigs = meta.get("signatures", [])
    if not isinstance(sigs, list):
        raise SigningError("signatures must be a list")
    if len(sigs) > MAX_SIGNATURES:
        raise SigningError(f"too many signatures (max {MAX_SIGNATURES})")

    author_entry = None
    review_entry = None
    for entry in sigs:
        if not isinstance(entry, dict):
            raise SigningError("each signature must be an object")
        by = entry.get("by")
        if by == "author":
            if author_entry is not None:
                raise SigningError("more than one author signature")
            if not isinstance(entry.get("key"), str) or not isinstance(entry.get("sig"), str):
                raise SigningError("malformed author signature")
            author_entry = entry
        elif by == "aedelgard":
            if review_entry is not None:
                raise SigningError("more than one Aedelgard review signature")
            if not isinstance(entry.get("key_id"), str) or not isinstance(entry.get("sig"), str):
                raise SigningError("malformed Aedelgard review signature")
            review_entry = entry
        else:
            raise SigningError(f"unknown signer {by!r}")

    notes = []
    author_pub = None
    if author_entry is not None:
        message = DOMAIN_AUTHOR + canonical_meta(meta)
        if not _check(author_entry["key"], author_entry["sig"], message):
            raise SigningError(
                "the author signature does not match; the package was altered"
            )
        presented = author_entry["key"]
        author_pub = encode_pub(decode_pub(presented))

    reviewed = False
    review_key_id = None
    if review_entry is not None:
        if author_pub is None:
            raise SigningError("a review signature without an author signature")
        key_id = review_entry["key_id"]
        if key_id in keys:
            message = (
                DOMAIN_REVIEW
                + canonical_meta(meta)
                + b"\n"
                + presented.encode("ascii")
            )
            if not _check(keys[key_id], review_entry["sig"], message):
                raise SigningError("the Aedelgard review signature does not match")
            reviewed = True
            review_key_id = key_id
        else:
            notes.append(
                f"reviewed by catalogue key {key_id}, which this body does not know"
            )

    return {
        "author": author_pub,
        "fingerprint": fingerprint(author_pub) if author_pub else None,
        "reviewed": reviewed,
        "review_key_id": review_key_id,
        "notes": notes,
    }


def _read_package(blob):
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise SigningError("not an .aedext package") from exc
    with zf:
        names = zf.namelist()
        if "AEDEXT.json" not in names:
            raise SigningError("package has no AEDEXT.json")
        try:
            meta = json.loads(zf.read("AEDEXT.json"))
        except (ValueError, KeyError) as exc:
            raise SigningError("AEDEXT.json is not valid JSON") from exc
        if not isinstance(meta, dict):
            raise SigningError("AEDEXT.json must be a JSON object")
        entries = [(info, zf.read(info)) for info in zf.infolist()]
    return meta, entries


def package_status(blob) -> dict:
    meta, _entries = _read_package(blob)
    return verify(meta)


def sign_package(blob, key) -> bytes:
    meta, entries = _read_package(blob)
    # Deliberately drop any earlier author and review signatures: a review is
    # bound to its author, so a new author signature invalidates it.
    meta["signatures"] = [sign_author(meta, key)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AEDEXT.json", json.dumps(meta, indent=2, sort_keys=True))
        for info, data in entries:
            if info.filename == "AEDEXT.json":
                continue
            z.writestr(info.filename, data)
    return buf.getvalue()


def _cmd_keygen(args) -> int:
    path = Path(args.path)
    try:
        key = generate_key()
        save_key(path, key)
    except SigningError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"author key written to {path}")
    print(f"fingerprint: {fingerprint(key['public'])}")
    return 0


def _cmd_sign(args) -> int:
    try:
        key = load_key(args.keyfile)
        blob = Path(args.package).read_bytes()
        signed = sign_package(blob, key)
        if args.output:
            out = Path(args.output)
        else:
            src = Path(args.package)
            name = src.name
            if name.endswith(".aedext"):
                name = name[: -len(".aedext")] + ".signed.aedext"
            else:
                name = name + ".signed.aedext"
            out = src.with_name(name)
        out.write_bytes(signed)
    except (SigningError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"signed by {fingerprint(key['public'])}")
    print(str(out))
    return 0


def _cmd_verify(args) -> int:
    try:
        blob = Path(args.package).read_bytes()
        status = package_status(blob)
    except (SigningError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    if status["author"] is None:
        print("unsigned")
    else:
        print(f"author: {status['fingerprint']}")
        if status["reviewed"]:
            print(f"reviewed by Aedelgard ({status['review_key_id']})")
        else:
            print("not reviewed")
    for note in status["notes"]:
        print(note)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ext_signing", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="mint a new author key")
    p_keygen.add_argument("path")
    p_keygen.set_defaults(func=_cmd_keygen)

    p_sign = sub.add_parser("sign", help="sign an .aedext package")
    p_sign.add_argument("package")
    p_sign.add_argument("keyfile")
    p_sign.add_argument("-o", "--output", default=None)
    p_sign.set_defaults(func=_cmd_sign)

    p_verify = sub.add_parser("verify", help="verify an .aedext package")
    p_verify.add_argument("package")
    p_verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
