"""BODY IDENTITY — which instance of the mind is this?

One mind may run on several instances ("bodies"). Each data root carries a
``body.json`` naming the body it belongs to, and ``bodies.json`` lists every
body of the mind. A copy of the data folder on another machine, or in another
folder on the same machine, is a *new* body: a body is the pair
(machine, data root).

See ``docs/EXTENSIONS.md`` § "Instance identity" for the design. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import platform
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

__all__ = [
    "os_name",
    "machine_fingerprint",
    "body_info",
    "rename",
    "register",
    "bodies",
    "prompt_line",
]


def os_name(system: str) -> str:
    """Normalise a ``platform.system()`` value to windows / macos / linux."""
    s = (system or "").lower()
    if s.startswith("win"):
        return "windows"
    if s == "darwin":
        return "macos"
    return "linux"


def machine_fingerprint() -> str:
    """A stable fingerprint of this machine.

    ``uuid.getnode()`` returns a *random* value (multicast bit set) when no
    hardware address is found, and it differs in every process. Folding such a
    value into the fingerprint would make every boot look like a copied folder,
    so it is left out when the multicast bit is set.
    """
    parts = [platform.system(), platform.machine(), platform.node()]
    node = uuid.getnode()
    if not (node >> 40) & 1:
        parts.append(f"{node:012x}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _default_name() -> str:
    node = (platform.node() or "").strip()
    return node[:40] or "body"


def _write(path: Path, info: dict) -> None:
    path.write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def body_info(data_root) -> dict:
    """Return this body's identity, minting and persisting it on first use.

    A body is (machine, data root): a ``body.json`` whose machine or root no
    longer matches is replaced by a fresh body. A ``body.json`` lacking
    ``root`` (an older format) is adopted rather than replaced.
    """
    root = Path(data_root).resolve()
    machine = machine_fingerprint()
    path = root / "body.json"

    existing = _read_json(path)
    if existing is not None:
        if "root" not in existing:
            existing["root"] = str(root)
            existing["machine"] = machine
            _write(path, existing)
            return existing
        if existing.get("machine") == machine and existing.get("root") == str(root):
            return existing

    info = {
        "body_id": uuid.uuid4().hex,
        "name": _default_name(),
        "os": os_name(platform.system()),
        "machine": machine,
        "root": str(root),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(path, info)
    return info


def rename(data_root, name: str) -> dict:
    """Rename this body. ``name`` must be 1..40 printable characters."""
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    if not 1 <= len(name) <= 40 or not name.isprintable():
        raise ValueError("name must be 1..40 printable characters")
    root = Path(data_root).resolve()
    info = body_info(root)
    info["name"] = name
    _write(root / "body.json", info)
    return info


def register(data_root, today: str | None = None) -> dict:
    """Upsert this body into ``bodies.json`` and return the registry.

    The file is rewritten only when its content actually changes, so a quiet
    same-day boot does not wake up backups.
    """
    root = Path(data_root)
    me = body_info(root)
    path = root / "bodies.json"
    data = _read_json(path) or {}
    day = today or date.today().isoformat()

    entry = data.get(me["body_id"])
    if not isinstance(entry, dict):
        entry = {}
    entry["name"] = me["name"]
    entry["os"] = me["os"]
    entry.setdefault("first_seen", day)
    entry["last_seen"] = day
    data[me["body_id"]] = entry

    text = json.dumps(data, indent=2, sort_keys=True)
    if _read_text(path) != text:
        path.write_text(text, encoding="utf-8")
    return data


def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def bodies(data_root) -> dict:
    """The ``bodies.json`` registry, or an empty dict when absent/unreadable."""
    return _read_json(Path(data_root) / "bodies.json") or {}


def prompt_line(data_root) -> str:
    """The prompt line naming this body and the others the mind lives on.

    Never carries dates, so a daily registry update does not churn the prompt
    cache. Returns ``""`` on any error.
    """
    try:
        me = body_info(data_root)
        line = f"You are on body '{me['name']}' ({me['os']})."
        others = []
        for bid, entry in bodies(data_root).items():
            if bid == me["body_id"] or not isinstance(entry, dict):
                continue
            others.append((entry.get("name", "?"), entry.get("os", "?")))
        others.sort(key=lambda pair: pair[0])
        if others:
            line += " This mind also lives on: " + ", ".join(
                f"'{name}' ({osname})" for name, osname in others)
        return line
    except Exception:
        return ""
