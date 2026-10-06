"""EXTENSIONS — discovery, validation, hash-pinned approval, layers, routines, homes.

See ``docs/EXTENSIONS.md``. This module only *describes* extensions; it never
imports or runs extension code (that is harness/ext_runtime.py). Stdlib only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
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
]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ALL_PLATFORMS = ["windows", "macos", "linux"]
HOOKS = {"on_boot", "on_turn_end", "on_goodnight", "on_termination"}
PERMISSIONS = {"palace_write"}
KINDS = {"declarative", "code"}


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
    """sha256 over sorted 'relpath\\0filesha\\n' lines, excluding data/, local/,
    __pycache__/ and *.pyc."""
    root = Path(ext_dir)
    lines = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if "data" in parts or "local" in parts or "__pycache__" in parts:
            continue
        if rel.endswith(".pyc"):
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{rel}\0{sha}\n")
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return digest


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


def discover(data_root, body=None) -> list:
    """List every extension as a JSON-safe dict, sorted by folder name."""
    body = _resolve_body(data_root, body)
    root = extensions_dir(data_root)
    trust = _read_trust(data_root)
    placement = _read_placement(data_root)
    out = []
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name in ("trust.json", "placement.json"):
            continue
        if not (folder / "extension.json").is_file():
            continue
        out.append(_describe(folder, body, trust, placement))
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