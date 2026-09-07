"""harness.compass — Single source of truth for Compass heading and project stack.

The Compass defines the mind's active heading and project attention:
  - focused: primary active project/corridor (loads config/visions/{focused}.md,
    sets dynamic banner, drives palace recall scoping)
  - ambient: always-monitored projects running in background
  - dormant: paused canons/projects, not abandoned

Replaces and retires the legacy config/active_vision.txt file.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("galadriel.compass")

COMPASS_FILENAME = "compass.json"
LEGACY_VISION_FILENAME = "active_vision.txt"
VISIONS_DIRNAME = "visions"

_PLACEHOLDERS = frozenset({"", "none", "active_vision", "default", "__null__"})


def _resolve_config_dir(config_dir: Optional[str | Path] = None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    env_dir = os.environ.get("GALADRIEL_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("config")


def compass_file_path(config_dir: Optional[str | Path] = None) -> Path:
    return _resolve_config_dir(config_dir) / COMPASS_FILENAME


def legacy_vision_file_path(config_dir: Optional[str | Path] = None) -> Path:
    return _resolve_config_dir(config_dir) / LEGACY_VISION_FILENAME


def available_visions(config_dir: Optional[str | Path] = None) -> list[str]:
    vdir = _resolve_config_dir(config_dir) / VISIONS_DIRNAME
    if not vdir.is_dir():
        return []
    return sorted(f.stem for f in vdir.glob("*.md"))


def retire_legacy_active_vision(config_dir: Optional[str | Path] = None) -> bool:
    """Retire config/active_vision.txt if it exists. Returns True if removed."""
    legacy = legacy_vision_file_path(config_dir)
    try:
        if legacy.exists():
            legacy.unlink()
            log.info("Retired legacy %s (compass rules alone).", legacy)
            return True
    except OSError as e:
        log.warning("Could not unlink legacy %s: %s", legacy, e)
    return False


def read_compass(config_dir: Optional[str | Path] = None) -> dict[str, Any]:
    """Read the current compass state.

    Returns dict with keys:
      - focused: str | None
      - ambient: list[str]
      - dormant: list[str]
    """
    cfg_dir = _resolve_config_dir(config_dir)
    cfile = cfg_dir / COMPASS_FILENAME
    state: dict[str, Any] = {"focused": None, "ambient": [], "dormant": []}

    if cfile.exists():
        try:
            data = json.loads(cfile.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                focused = (data.get("focused") or "").strip()
                state["focused"] = None if focused.lower() in _PLACEHOLDERS else focused
                state["ambient"] = [
                    str(s).strip() for s in data.get("ambient", [])
                    if str(s).strip() and str(s).strip().lower() not in _PLACEHOLDERS
                ]
                state["dormant"] = [
                    str(s).strip() for s in data.get("dormant", [])
                    if str(s).strip() and str(s).strip().lower() not in _PLACEHOLDERS
                ]
                return state
        except Exception as e:
            log.warning("Error reading %s (%s) — checking legacy fallback", cfile, e)

    # Passive fallback if compass.json absent
    legacy = cfg_dir / LEGACY_VISION_FILENAME
    if legacy.exists():
        try:
            name = legacy.read_text(encoding="utf-8").strip()
            if name and name.lower() not in _PLACEHOLDERS:
                state["focused"] = name
                return state
        except OSError:
            pass

    return state


def get_focused(config_dir: Optional[str | Path] = None) -> Optional[str]:
    """Return the current focused heading, or None."""
    return read_compass(config_dir).get("focused")


def set_compass(
    config_dir: Optional[str | Path] = None,
    focused: Any = ... ,
    ambient: Any = ... ,
    dormant: Any = ... ,
    set_by: str = "mind",
) -> dict[str, Any]:
    """Update the compass stack.

    Pass Ellipsis (...) to leave a field unchanged.
    Pass None or empty string to clear 'focused'.
    Pass empty list to clear 'ambient' or 'dormant'.
    """
    cfg_dir = _resolve_config_dir(config_dir)
    cfile = cfg_dir / COMPASS_FILENAME
    current = read_compass(cfg_dir)
    available = set(available_visions(cfg_dir))

    # Helper validator
    def _validate_name(name: str) -> bool:
        if not available:
            return True
        return name in available

    if focused is not ...:
        if focused is None:
            current["focused"] = None
        else:
            name = str(focused).strip()
            if not name or name.lower() in _PLACEHOLDERS:
                current["focused"] = None
            else:
                if not _validate_name(name):
                    return {"error": f"Vision '{name}' not found", "available": sorted(available)}
                current["focused"] = name

    if ambient is not ...:
        names = [str(s).strip() for s in (ambient or []) if str(s).strip()]
        if available:
            invalid = [n for n in names if n not in available]
            if invalid:
                return {"error": f"Unknown ambient visions: {invalid}", "available": sorted(available)}
        current["ambient"] = names

    if dormant is not ...:
        names = [str(s).strip() for s in (dormant or []) if str(s).strip()]
        if available:
            invalid = [n for n in names if n not in available]
            if invalid:
                return {"error": f"Unknown dormant visions: {invalid}", "available": sorted(available)}
        current["dormant"] = names

    # Write compass.json atomically
    payload = {
        "focused": current["focused"],
        "ambient": current["ambient"],
        "dormant": current["dormant"],
        "_comment": "COMPASS — AI-maintained project stack. Galadriel sets FOCUSED autonomously from conversational context. AMBIENT = always-on background. DORMANT = paused, not abandoned.",
    }
    cfg_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = cfile.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_file.replace(cfile)

    # Retire legacy active_vision.txt whenever compass is mutated
    retire_legacy_active_vision(cfg_dir)

    log.info("Compass updated (set_by=%s): focused=%r ambient=%r dormant=%r",
             set_by, current["focused"], current["ambient"], current["dormant"])

    return {"ok": True, "compass": current}


def cli_main(args: list[str]) -> int:
    """CLI runner for bin/compass."""
    cmd = args[0] if args else "get"

    if cmd in ("get", "status", "list"):
        st = read_compass()
        focused = st.get("focused") or "(none)"
        ambient = ", ".join(st.get("ambient", [])) or "(none)"
        dormant = ", ".join(st.get("dormant", [])) or "(none)"
        avail = ", ".join(available_visions()) or "(none)"
        print(f"🧭 Compass:")
        print(f"  Focused:   {focused}")
        print(f"  Ambient:   {ambient}")
        print(f"  Dormant:   {dormant}")
        print(f"  Available: {avail}")
        return 0

    if cmd == "set":
        if len(args) < 2:
            print("Usage: compass set <heading>", file=sys.stderr)
            return 1
        heading = args[1]
        res = set_compass(focused=heading, set_by="cli")
        if "error" in res:
            print(f"Error: {res['error']}", file=sys.stderr)
            print(f"Available: {', '.join(res.get('available', []))}", file=sys.stderr)
            return 1
        print(f"🧭 Compass focused set to: {res['compass']['focused']}")
        return 0

    if cmd == "clear":
        res = set_compass(focused=None, set_by="cli")
        print("🧭 Compass focused cleared.")
        return 0

    if cmd == "ambient":
        items = [x.strip() for x in args[1:] if x.strip()]
        res = set_compass(ambient=items, set_by="cli")
        if "error" in res:
            print(f"Error: {res['error']}", file=sys.stderr)
            return 1
        print(f"🧭 Compass ambient set to: {res['compass']['ambient']}")
        return 0

    if cmd == "dormant":
        items = [x.strip() for x in args[1:] if x.strip()]
        res = set_compass(dormant=items, set_by="cli")
        if "error" in res:
            print(f"Error: {res['error']}", file=sys.stderr)
            return 1
        print(f"🧭 Compass dormant set to: {res['compass']['dormant']}")
        return 0

    # Shorthand: `compass <heading>`
    heading = cmd
    res = set_compass(focused=heading, set_by="cli")
    if "error" in res:
        print(f"Error: {res['error']}", file=sys.stderr)
        print(f"Available: {', '.join(res.get('available', []))}", file=sys.stderr)
        return 1
    print(f"🧭 Compass focused set to: {res['compass']['focused']}")
    return 0


if __name__ == "__main__":
    sys.exit(cli_main(sys.argv[1:]))
