"""EXTENSIONS — discovery, validation, hash-pinned approval, layers, routines, homes.

See ``docs/EXTENSIONS.md``. This module only *describes* extensions; it never
imports or runs extension code (that is harness/ext_runtime.py). Stdlib only,
except that ``harness.ext_signing`` (which needs ``cryptography``) is imported
lazily, and only for .aedext packages.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "ExtensionError",
    "extensions_dir",
    "discover",
    "layers_text",
    "approve",
    "disable",
    "set_home",
    "placement",
    "routines",
    "recipe_dirs",
    "enabled_code",
    "extension_hash",
    "export_aedext",
    "import_aedext",
    "author_key_path",
    "AEDEXT_MAX_BYTES",
]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ALL_PLATFORMS = ["windows", "macos", "linux"]
HOOKS = {"on_boot", "on_turn_end", "on_goodnight", "on_termination"}
PERMISSIONS = {"palace_write"}
KINDS = {"declarative", "code"}

AEDEXT_FORMAT = 1
AEDEXT_MAX_BYTES = 10 * 1024 * 1024       # the packed .aedext
AEDEXT_MAX_UNPACKED = 20 * 1024 * 1024    # sum of file sizes inside
AEDEXT_MAX_FILES = 500

_SAFE_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_WIN_DEVICES = ({"con", "prn", "aux", "nul"}
                | {f"com{i}" for i in range(1, 10)}
                | {f"lpt{i}" for i in range(1, 10)})


def _unsafe_part(part):
    """True if one path component is unsafe on any of the three OSes."""
    if not _SAFE_PART_RE.match(part) or part in (".", ".."):
        return True
    if part.endswith(".") or part.endswith(" "):
        return True
    if part.split(".")[0].lower() in _WIN_DEVICES:
        return True
    return False


_AUTHORS = "authors.json"


class ExtensionError(Exception):
    """Raised for unknown extension names and invalid home claims."""


def extensions_dir(data_root) -> Path:
    return Path(data_root) / "extensions"


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def _default_body(data_root) -> dict:
    from harness import body_identity
    return body_identity.body_info(data_root)


def _resolve_body(data_root, body):
    return body if body is not None else _default_body(data_root)


def extension_hash(ext_dir) -> str:
    """Hex SHA-256 over the sorted (relative posix path, file hash) pairs.

    Every file under ``ext_dir`` counts except anything under a TOP-LEVEL
    ``data/`` or ``local/`` (plus ``__pycache__`` and ``*.pyc``). Each pair is
    fed as a ``path\\0filehash\\n`` line into one sha256.
    """
    ext_dir = Path(ext_dir)
    entries = []
    for p in ext_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ext_dir)
        if rel.parts and rel.parts[0] in ("data", "local"):
            continue
        if "__pycache__" in rel.parts or rel.suffix == ".pyc":
            continue
        entries.append((rel.as_posix(), p))
    entries.sort(key=lambda t: t[0])
    h = hashlib.sha256()
    for rel, p in entries:
        try:
            file_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        h.update(f"{rel}\0{file_hash}\n".encode("utf-8"))
    return h.hexdigest()


def _validate(ext_dir: Path, manifest: dict) -> str:
    """Return an error string, or '' when the manifest is valid."""
    name = manifest.get("name")
    folder = ext_dir.name
    if not isinstance(name, str) or not NAME_RE.match(name):
        return "name must match the folder name"
    if name != folder:
        return "name must match the folder name"
    kind = manifest.get("kind")
    if kind not in KINDS:
        return "kind must be declarative or code"
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        return "version must be a non-empty string"
    platforms = manifest.get("platforms", ALL_PLATFORMS)
    if not isinstance(platforms, list) or not platforms:
        return "platforms must be a non-empty list"
    if not all(p in ALL_PLATFORMS for p in platforms):
        return "platforms must be a subset of windows/macos/linux"
    contributes = manifest.get("contributes")
    if not isinstance(contributes, dict):
        return "contributes must be an object"
    return _validate_contributes(ext_dir, manifest, contributes)


def _validate_contributes(ext_dir: Path, manifest: dict, contributes: dict) -> str:
    kind = manifest["kind"]
    layers = contributes.get("layers", [])
    if not isinstance(layers, list):
        return "layers must be a list"
    root = ext_dir.resolve()
    for layer in layers:
        if not isinstance(layer, dict):
            return "layer must be an object"
        file = layer.get("file")
        if not isinstance(file, str):
            return "layer file must be a string"
        target = (ext_dir / file).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return f"layer {file} escapes the extension folder"
        if not target.is_file():
            return f"layer {file} does not exist"
        lp = layer.get("platforms")
        if lp is not None and (not isinstance(lp, list) or not all(p in ALL_PLATFORMS for p in lp)):
            return "layer platforms invalid"

    routines = contributes.get("routines", [])
    if not isinstance(routines, list):
        return "routines must be a list"
    rfile = ext_dir / "routines.json"
    declared = set(routines)
    if rfile.exists():
        data = _read_json(rfile)
        if not isinstance(data, list):
            return "routines.json must be a list"
        for r in data:
            err = _validate_routine(r)
            if err:
                return err
            if r.get("id") not in declared:
                return f"routine {r.get('id')} is undeclared"
    else:
        if declared:
            return "routines.json missing"

    if kind == "declarative":
        if contributes.get("tools") or contributes.get("hooks") or contributes.get("routes"):
            return "declarative extensions may not declare code"
        if (ext_dir / "extension.py").exists():
            return "declarative extensions may not contain extension.py"
    else:
        if not (ext_dir / "extension.py").is_file():
            return "code extensions require extension.py"
        tools = contributes.get("tools", [])
        if not isinstance(tools, list) or not all(isinstance(t, str) and TOOL_RE.match(t) for t in tools):
            return "invalid tool name"
        hooks = contributes.get("hooks", [])
        if not isinstance(hooks, list) or not all(h in HOOKS for h in hooks):
            return "unknown hook"
        for req in manifest.get("requires", []):
            if importlib.util.find_spec(req) is None:
                return f"required module {req} is not importable"
    perms = manifest.get("permissions", [])
    if not isinstance(perms, list):
        return "permissions must be a list"
    for p in perms:
        if p not in PERMISSIONS:
            return f"unknown permission {p}"
    return ""


def _validate_routine(r: dict) -> str:
    if not isinstance(r, dict):
        return "routine must be an object"
    rid = r.get("id")
    if not isinstance(rid, str) or not NAME_RE.match(rid):
        return "routine id invalid"
    at = r.get("at")
    if not isinstance(at, str) or not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", at):
        return "routine at invalid"
    days = r.get("days")
    if days not in ("daily", "workdays"):
        return "routine days invalid"
    prompt = r.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return "routine prompt must be non-empty"
    scope = r.get("scope", "mind")
    if scope not in ("mind", "body"):
        return "routine scope invalid"
    return ""


def _read_trust(data_root) -> dict:
    data = _read_json(extensions_dir(data_root) / "trust.json")
    return data if isinstance(data, dict) else {}


def _read_placement(data_root) -> dict:
    data = _read_json(extensions_dir(data_root) / "placement.json")
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _write_trust(data_root, trust: dict) -> None:
    """Write trust.json atomically (temp file + os.replace)."""
    d = extensions_dir(data_root)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".trust-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(trust, fh, indent=2, sort_keys=True)
        os.replace(tmp, d / "trust.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_authors(data_root) -> dict:
    """Read authors.json; a missing or unreadable file means no pins."""
    path = extensions_dir(data_root) / _AUTHORS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_authors(data_root, authors: dict) -> None:
    """Write authors.json atomically (temp file + os.replace)."""
    d = extensions_dir(data_root)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".authors-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(authors, fh, indent=2, sort_keys=True)
        os.replace(tmp, d / _AUTHORS)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def author_key_path(data_root) -> Path:
    """The path of this mind's author key file."""
    return extensions_dir(data_root) / "author_key.json"


def _provenance(name, ext_hash, authors) -> dict:
    """The provenance record the Lodge shows for an extension."""
    pin = authors.get(name)
    if not isinstance(pin, dict):
        return {"imported": False, "signed": False, "author": None,
                "reviewed": False, "edited": False}
    key = pin.get("key")
    return {
        "imported": True,
        "signed": bool(key),
        "author": (pin.get("fingerprint") if key else None),
        "reviewed": bool(pin.get("reviewed")),
        "edited": bool(ext_hash) and ext_hash != pin.get("sha256"),
    }


def _aedext_files(ext_dir):
    """(relposix, Path) for every file that counts toward the extension hash."""
    ext_dir = Path(ext_dir)
    entries = []
    for p in ext_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ext_dir)
        if rel.parts and rel.parts[0] in ("data", "local"):
            continue
        if "__pycache__" in rel.parts or rel.suffix == ".pyc":
            continue
        entries.append((rel.as_posix(), p))
    entries.sort(key=lambda t: t[0])
    return entries


def export_aedext(data_root, name, sign=False) -> bytes:
    """Pack an extension into an .aedext (zip) and return the bytes."""
    d = _find_ext(data_root, name)
    try:
        manifest = json.loads((d / "extension.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ExtensionError(f"extension {name!r} has no readable manifest")
    if not isinstance(manifest, dict):
        raise ExtensionError(f"extension {name!r} has no readable manifest")
    files = _aedext_files(d)
    meta = {
        "format": AEDEXT_FORMAT,
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "kind": manifest.get("kind"),
        "sha256": extension_hash(d),
        "files": len(files),
        "signatures": [],
    }
    if sign:
        from harness import ext_signing
        meta["signatures"] = [
            ext_signing.sign_author(
                meta, ext_signing.author_key(author_key_path(data_root)))
        ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AEDEXT.json", json.dumps(meta, indent=2, sort_keys=True))
        for rel, p in files:
            z.writestr(f"payload/{rel}", p.read_bytes())
    return buf.getvalue()


def import_aedext(data_root, blob, replace=False, new_author=False) -> dict:
    """Import an .aedext. Hostile input: every refusal raises ExtensionError."""
    if len(blob) > AEDEXT_MAX_BYTES:
        raise ExtensionError(
            f"package is larger than {AEDEXT_MAX_BYTES // (1024 * 1024)} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise ExtensionError("not an .aedext package")

    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > AEDEXT_MAX_FILES + 1:
            raise ExtensionError("package has too many files")
        if sum(i.file_size for i in infos) > AEDEXT_MAX_UNPACKED:
            raise ExtensionError("package unpacks to too much data")

        meta_info = None
        for i in infos:
            if i.filename == "AEDEXT.json":
                meta_info = i
                break
        if meta_info is None:
            raise ExtensionError("package has no AEDEXT.json")
        try:
            meta = json.loads(z.read(meta_info))
        except (ValueError, OSError):
            raise ExtensionError("package has no readable AEDEXT.json")
        if not isinstance(meta, dict):
            raise ExtensionError("package has no readable AEDEXT.json")
        if meta.get("format") != AEDEXT_FORMAT:
            raise ExtensionError("unknown package format")
        name = meta.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ExtensionError("package has a bad extension name")
        if not isinstance(meta.get("sha256"), str):
            raise ExtensionError("package declares no hash")

        from harness import ext_signing
        try:
            sig_status = ext_signing.verify(meta)
        except ext_signing.SigningError as exc:
            raise ExtensionError(f"signature check failed: {exc}")
        authors = _read_authors(data_root)
        pin = authors.get(name) if isinstance(authors.get(name), dict) else None
        pinned_key = pin.get("key") if pin else None
        if pinned_key and sig_status["author"] != pinned_key and not new_author:
            new_fp = sig_status["fingerprint"] or "unsigned"
            old_fp = pin.get("fingerprint") or "?"
            raise ExtensionError(
                f"this package is by a different author ({new_fp}) than the one this mind knows for {name!r} ({old_fp}); "
                "it is not an update. Accept the new author explicitly to install it.")

        payload = []
        seen = set()
        seen_fold = set()
        for i in infos:
            if i is meta_info:
                continue
            n = i.filename
            if "\\" in n or n.startswith("/") or not n.startswith("payload/"):
                raise ExtensionError("package contains an unsafe path")
            if ":" in n:
                raise ExtensionError("package contains an unsafe path")
            parts = n.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise ExtensionError("package contains an unsafe path")
            rel = n[len("payload/"):]
            rparts = rel.split("/")
            if any(_unsafe_part(p) for p in rparts):
                raise ExtensionError("package contains an unsafe path")
            if rparts[0].lower().rstrip(". ") in ("data", "local"):
                raise ExtensionError("package ships state")
            if (any(p.lower() == "__pycache__" for p in rparts)
                    or rel.lower().endswith(".pyc")):
                raise ExtensionError("package ships caches")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ExtensionError("package contains a symlink")
            if rel.lower() in seen_fold:
                raise ExtensionError("package contains a duplicate path")
            seen_fold.add(rel.lower())
            seen.add(rel)
            payload.append((rel, i))

        if "extension.json" not in seen:
            raise ExtensionError("package has no extension.json")

        edir = extensions_dir(data_root)
        edir.mkdir(parents=True, exist_ok=True)
        target = edir / name
        if target.exists() and not replace:
            raise ExtensionError(f"an extension named {name!r} is already installed")

        tmp = Path(tempfile.mkdtemp(dir=str(edir), prefix=".import-"))
        try:
            for rel, info in payload:
                dest = tmp.joinpath(*rel.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                if tmp.resolve() not in dest.resolve().parents:
                    raise ExtensionError("package contains an unsafe path")
                dest.write_bytes(z.read(info))
            try:
                manifest = json.loads(
                    (tmp / "extension.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ExtensionError("package has no readable extension.json")
            if not isinstance(manifest, dict):
                raise ExtensionError("package has no readable extension.json")
            if (manifest.get("name") != name
                    or manifest.get("version") != meta.get("version")):
                raise ExtensionError("manifest does not match the package")
            if extension_hash(tmp) != meta["sha256"]:
                raise ExtensionError(
                    "the files do not match the hash the package declares; "
                    "it was altered")
            if target.exists():
                old = edir / f".old-{name}-{os.getpid()}"
                os.replace(target, old)
                moved = []
                try:
                    for keep in ("data", "local"):
                        if (old / keep).is_dir():
                            shutil.move(str(old / keep), str(tmp / keep))
                            moved.append(keep)
                    os.replace(tmp, target)
                except BaseException:
                    for keep in moved:
                        if (tmp / keep).exists() and not (old / keep).exists():
                            shutil.move(str(tmp / keep), str(old / keep))
                    if not target.exists():
                        os.replace(old, target)
                    raise
                shutil.rmtree(old, ignore_errors=True)
            else:
                os.replace(tmp, target)
        except BaseException as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            if isinstance(exc, ExtensionError):
                raise
            raise ExtensionError(f"import failed: {exc}")

    trust = _read_trust(data_root)
    if name in trust:
        del trust[name]
        _write_trust(data_root, trust)

    if sig_status["author"] or not pinned_key or new_author:
        authors[name] = {"key": sig_status["author"],
                         "fingerprint": sig_status["fingerprint"],
                         "reviewed": sig_status["reviewed"],
                         "review_key_id": sig_status["review_key_id"],
                         "sha256": meta["sha256"],
                         "version": meta.get("version")}
        _write_authors(data_root, authors)

    for rec in discover(data_root):
        if rec.get("name") == name:
            return rec
    return {"name": name}


def discover(data_root, body=None) -> list:
    """List every extension as a JSON-safe dict, sorted by folder name."""
    body = _resolve_body(data_root, body)
    root = extensions_dir(data_root)
    trust = _read_trust(data_root)
    placement = _read_placement(data_root)
    authors = _read_authors(data_root)
    out = []
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name.startswith("."):
            continue
        if folder.name in ("trust.json", "placement.json"):
            continue
        if not (folder / "extension.json").is_file():
            continue
        row = _describe(folder, body, trust, placement)
        row["provenance"] = _provenance(row["name"], row.get("hash") or "", authors)
        out.append(row)
    return out


def _describe(ext_dir: Path, body: dict, trust: dict, placement: dict) -> dict:
    name = ext_dir.name
    manifest = _read_json(ext_dir / "extension.json")
    if not isinstance(manifest, dict):
        return _entry(ext_dir, name, body, placement, state="failed", error="unreadable manifest")
    error = _validate(ext_dir, manifest)
    if error:
        return _entry(ext_dir, name, body, placement, state="failed", error=error)

    platforms = manifest.get("platforms", ALL_PLATFORMS)
    if body.get("os") not in platforms:
        return _entry(ext_dir, name, body, placement, state="not_for_this_body",
                      platforms=platforms, manifest=manifest)

    trust_entry = trust.get(name)
    enabled = bool(trust_entry and trust_entry.get("enabled"))
    if trust_entry and not enabled:
        return _entry(ext_dir, name, body, placement, state="disabled",
                      error=trust_entry.get("disabled_reason", ""), platforms=platforms,
                      manifest=manifest, trust_entry=trust_entry)
    if enabled:
        if manifest["kind"] == "code":
            if trust_entry.get("sha256") != extension_hash(ext_dir):
                return _entry(ext_dir, name, body, placement, state="awaiting_approval",
                              error="code changed since approval — approve again",
                              platforms=platforms, manifest=manifest, trust_entry=trust_entry)
        return _entry(ext_dir, name, body, placement, state="enabled", platforms=platforms,
                      manifest=manifest, trust_entry=trust_entry)
    return _entry(ext_dir, name, body, placement, state="awaiting_approval", platforms=platforms,
                  manifest=manifest, trust_entry=trust_entry)


def _entry(ext_dir, name, body, placement, state, error="", platforms=None,
           manifest=None, trust_entry=None) -> dict:
    manifest = manifest or {}
    e = {
        "name": name,
        "title": manifest.get("title", name),
        "kind": manifest.get("kind", ""),
        "version": manifest.get("version", ""),
        "description": manifest.get("description", ""),
        "hash": extension_hash(ext_dir) if manifest else "",
        "state": state,
        "error": error,
        "contributes": manifest.get("contributes", {}),
        "platforms": platforms or [],
        "permissions": manifest.get("permissions", []),
        "routines": _routines_for(ext_dir, name, body, placement),
        "prompt_tokens": len(_layer_text(ext_dir, manifest, body)) // 4,
    }
    return e


def _routines_for(ext_dir: Path, name: str, body: dict, placement: dict) -> list:
    rfile = ext_dir / "routines.json"
    if not rfile.is_file():
        return []
    data = _read_json(rfile)
    if not isinstance(data, list):
        return []
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        scope = r.get("scope", "mind")
        platforms = r.get("platforms", ALL_PLATFORMS)
        home = None
        fires_here = False
        if scope == "mind":
            key = f"{name}/{r.get('id')}"
            home = placement.get(key)
            fires_here = home == body.get("body_id")
        else:
            fires_here = True
        out.append({
            "id": r.get("id"),
            "at": r.get("at"),
            "days": r.get("days"),
            "scope": scope,
            "platforms": platforms,
            "home": home,
            "fires_here": fires_here,
        })
    return out


def _layer_text(ext_dir: Path, manifest: dict, body: dict) -> str:
    """Render this extension's layers for the given body, or '' on any error."""
    blocks = []
    for layer in manifest.get("contributes", {}).get("layers", []):
        if not isinstance(layer, dict):
            continue
        lp = layer.get("platforms")
        if lp is not None and body.get("os") not in lp:
            continue
        file = layer.get("file")
        if not isinstance(file, str):
            continue
        target = (ext_dir / file).resolve()
        try:
            target.relative_to(ext_dir.resolve())
        except ValueError:
            continue
        try:
            text = target.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        blocks.append(f"## {layer.get('title', '')}\n\n{text}")
    return "\n\n".join(blocks)


def layers_text(data_root, body=None) -> str:
    """Render enabled extensions' layers, in name order."""
    body = _resolve_body(data_root, body)
    parts = []
    for e in discover(data_root, body=body):
        if e["state"] != "enabled":
            continue
        ext_dir = extensions_dir(data_root) / e["name"]
        manifest = _read_json(ext_dir / "extension.json") or {}
        text = _layer_text(ext_dir, manifest, body)
        if not text:
            continue
        parts.append(f"# Extension — {e['title']}\n\n{text}")
    return "\n\n".join(parts)


def _find_ext(data_root, name) -> Path:
    root = extensions_dir(data_root)
    ext_dir = root / name
    if not ext_dir.is_dir() or not (ext_dir / "extension.json").is_file():
        raise ExtensionError(f"unknown extension {name}")
    return ext_dir


def approve(data_root, name, body=None) -> None:
    """Approve an extension: write {sha256, approved_at, enabled: True} and claim
    homes for mind-scope routines without one whose platforms include the body's os."""
    body = _resolve_body(data_root, body)
    ext_dir = _find_ext(data_root, name)
    manifest = _read_json(ext_dir / "extension.json")
    trust = _read_trust(data_root)
    entry = {
        "sha256": extension_hash(ext_dir),
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "enabled": True,
    }
    trust[name] = entry
    _write_json(extensions_dir(data_root) / "trust.json", trust)

    placement = _read_placement(data_root)
    rfile = ext_dir / "routines.json"
    if rfile.is_file():
        data = _read_json(rfile)
        if isinstance(data, list):
            for r in data:
                if not isinstance(r, dict):
                    continue
                if r.get("scope", "mind") != "mind":
                    continue
                rp = r.get("platforms", ALL_PLATFORMS)
                if body.get("os") not in rp:
                    continue
                key = f"{name}/{r.get('id')}"
                if key not in placement:
                    placement[key] = body.get("body_id")
    _write_json(extensions_dir(data_root) / "placement.json", placement)


def disable(data_root, name, body=None, reason="") -> None:
    """Disable an extension, keeping the trust entry but marking it disabled."""
    _resolve_body(data_root, body)
    _find_ext(data_root, name)
    trust = _read_trust(data_root)
    entry = trust.get(name)
    if entry is None:
        entry = {}
    entry["enabled"] = False
    if reason:
        entry["disabled_reason"] = reason
    else:
        entry.pop("disabled_reason", None)
    trust[name] = entry
    _write_json(extensions_dir(data_root) / "trust.json", trust)


def set_home(data_root, name, routine_id, body=None) -> None:
    """Claim the home for a mind-scope routine on this body."""
    body = _resolve_body(data_root, body)
    ext_dir = _find_ext(data_root, name)
    manifest = _read_json(ext_dir / "extension.json")
    if _validate(ext_dir, manifest):
        raise ExtensionError("extension is not valid")
    rfile = ext_dir / "routines.json"
    data = _read_json(rfile) if rfile.is_file() else None
    if not isinstance(data, list):
        raise ExtensionError(f"unknown routine {routine_id}")
    found = None
    for r in data:
        if isinstance(r, dict) and r.get("id") == routine_id:
            found = r
            break
    if found is None:
        raise ExtensionError(f"unknown routine {routine_id}")
    if found.get("scope", "mind") != "mind":
        raise ExtensionError("only mind-scope routines have a home")
    rp = found.get("platforms", ALL_PLATFORMS)
    if body.get("os") not in rp:
        raise ExtensionError("this body cannot run that routine")
    placement = _read_placement(data_root)
    placement[f"{name}/{routine_id}"] = body.get("body_id")
    _write_json(extensions_dir(data_root) / "placement.json", placement)


def placement(data_root) -> dict:
    return _read_placement(data_root)


def routines(data_root, body=None) -> list:
    """Routines that fire here: enabled extensions' routines with fires_here."""
    body = _resolve_body(data_root, body)
    out = []
    for e in discover(data_root, body=body):
        if e["state"] != "enabled":
            continue
        for r in e["routines"]:
            if not r["fires_here"]:
                continue
            out.append({
                "ext": e["name"],
                "id": r["id"],
                "at": r["at"],
                "days": r["days"],
                "prompt": _routine_prompt(extensions_dir(data_root) / e["name"], r["id"]),
                "scope": r["scope"],
            })
    return out


def _routine_prompt(ext_dir: Path, routine_id: str) -> str:
    data = _read_json(ext_dir / "routines.json")
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict) and r.get("id") == routine_id:
                return r.get("prompt", "")
    return ""


def recipe_dirs(data_root, body=None) -> list:
    """(name, commands dir) for enabled extensions that have commands/."""
    body = _resolve_body(data_root, body)
    out = []
    for e in discover(data_root, body=body):
        if e["state"] != "enabled":
            continue
        cmd = extensions_dir(data_root) / e["name"] / "commands"
        if cmd.is_dir():
            out.append((e["name"], cmd))
    return out


def enabled_code(data_root, body=None) -> list:
    """Enabled code extensions: {name, path, hash, manifest}."""
    body = _resolve_body(data_root, body)
    out = []
    for e in discover(data_root, body=body):
        if e["state"] != "enabled" or e["kind"] != "code":
            continue
        ext_dir = extensions_dir(data_root) / e["name"]
        out.append({
            "name": e["name"],
            "path": str(ext_dir),
            "hash": e["hash"],
            "manifest": _read_json(ext_dir / "extension.json") or {},
        })
    return out