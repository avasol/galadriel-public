#!/usr/bin/env python3
"""Aedelgard body — native launcher.

This is the entry point baked into the Tier-1 native installers (.AppImage /
.dmg / .msi). It is NOT a rewrite of the agent — the body is already a
localhost web app. This launcher just:

  1. resolves a writable USER DATA dir (per-OS), so a double-clicked app keeps
     its mind across runs without the user thinking about volumes;
  2. seeds the BUNDLED embedding model into that dir on first run, so the
     memory palace works fully OFFLINE — no 167MB download on first mine;
  3. boots the harness in Tower-only mode (no Discord token required) and
     opens the browser to the local Tower UI;
  4. routes a true first-run (no .env / no key) to the /setup web screen.

The packaged build sets GALADRIEL_BODY=1 so the harness/Tower know they are
running as a native body and should offer first-run setup at /setup.
"""

import os
import sys
import time
import shutil
import logging
import threading
import webbrowser
from pathlib import Path

log = logging.getLogger("aedelgard.body")


# ── 1. Per-OS user data dir ──────────────────────────────────────────────────
def user_data_dir() -> Path:
    """A stable, writable place for the body's mind (config, memory, palace)."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Aedelgard"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", home)) / "Aedelgard"
    else:  # linux / *nix
        base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "aedelgard"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ── 2. Locate bundled resources (PyInstaller vs source run) ───────────────────
def resource_root() -> Path:
    """Where bundled, read-only resources live.

    Under PyInstaller one-file builds this is sys._MEIPASS; from source it is
    the repo root (two levels up from this file: packaging/common/..).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[2]


def seed_embedding_model(data_dir: Path, res_root: Path) -> None:
    """Copy the bundled ChromaDB ONNX model into the user's cache once.

    ChromaDB's default embedder downloads all-MiniLM-L6-v2 (~167MB) to
    ~/.cache/chroma/onnx_models/ on first use. We bundle it and seed it so the
    body is offline-first and a first mine never stalls on a network fetch.
    The model is vendored at packaging/common/onnx_models/ in the artifact.
    """
    src = res_root / "packaging" / "common" / "onnx_models"
    if not src.is_dir():
        log.info("No bundled ONNX model found (%s); Chroma will fetch on demand.", src)
        return
    # ChromaDB reads from ~/.cache/chroma/onnx_models by default; we keep the
    # cache inside the body's own data dir so it travels with the mind and needs
    # no $HOME writes beyond it.
    cache_root = data_dir / "chroma_cache"
    dest = cache_root / "onnx_models"
    if dest.exists():
        return  # already seeded
    log.info("Seeding bundled embedding model -> %s", dest)
    shutil.copytree(src, dest)
    # Point Chroma at our seeded cache for this process and the harness.
    os.environ.setdefault("CHROMA_CACHE_DIR", str(cache_root))


# ── 3. Wire the harness to the user data dir via env ──────────────────────────
def prepare_environment() -> Path:
    data_dir = user_data_dir()
    res_root = resource_root()

    os.environ["GALADRIEL_BODY"] = "1"
    # FORCE localhost binding — the Tower UI has no auth and must never be
    # reachable from the LAN on a user's personal machine. Not setdefault:
    # the body owns this decision regardless of a stale .env value.
    os.environ["TOWER_HOST"] = "127.0.0.1"
    os.environ.setdefault("TOWER_PORT", "8080")
    # The body's mind lives in the user data dir, not next to the binary.
    os.environ.setdefault("GALADRIEL_CONFIG_DIR", str(data_dir / "config"))
    os.environ.setdefault("GALADRIEL_MEMORY_DIR", str(data_dir / "memory"))
    os.environ.setdefault("MEMPALACE_PATH", str(data_dir / "palace"))
    os.environ.setdefault("PALACE_ARCHIVE_ROOT", str(data_dir / "archive"))
    # The .env the /setup screen writes lives in the user data dir.
    os.environ.setdefault("GALADRIEL_DOTENV", str(data_dir / ".env"))

    for sub in ("config", "memory", "palace", "archive"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    seed_embedding_model(data_dir, res_root)
    return data_dir


def is_first_run(data_dir: Path) -> bool:
    """First run = no .env with a usable brain credential yet.

    The credential markers MUST match exactly what tower/app.py::api_setup
    writes for each provider, or a successful /setup loops back to /setup:
      - anthropic -> ANTHROPIC_API_KEY=sk-...
      - gemini    -> GEMINI_API_KEY=...
      - aedelgard -> AEDELGARD_AEDK=aedk... (the ONE key; device tokens are
                     minted from it at runtime, never written to .env)
    """
    env_path = data_dir / ".env"
    if not env_path.exists():
        return True
    set_up = False
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if not val or val.startswith((".", "<")):  # empty / placeholder
            continue
        if key == "ANTHROPIC_API_KEY" and val.startswith("sk-"):
            set_up = True
        elif key == "GEMINI_API_KEY":
            set_up = True
        elif key == "AEDELGARD_AEDK" and val.startswith("aedk"):
            set_up = True
        elif key == "AEDELGARD_DEVICE_TOKEN":  # legacy/back-compat
            set_up = True
    return not set_up


# ── 4. Open the browser once Tower answers ────────────────────────────────────
def open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.5)
            break
        except Exception:
            time.sleep(0.4)
    try:
        webbrowser.open(url)
    except Exception:
        log.info("Open your browser to %s", url)


def _another_body_is_running(port: int) -> bool:
    """True if something already holds the Tower port on localhost.

    The body is a single localhost web app. Launching a second copy would race
    on the port: main.py runs Tower in a daemon thread, so an EADDRINUSE there
    is swallowed and you get a silent, non-serving zombie process (observed in
    the wild as multiple idle aedelgard-body processes). We pre-flight the port
    here: if it is already served, a body is up — open the browser to it and
    exit cleanly instead of spawning a duplicate.
    """
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        # connect_ex == 0 means something is LISTENING (a live body), so we bow out.
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    data_dir = prepare_environment()

    port = os.environ.get("TOWER_PORT", "8080")
    landing = "/setup" if is_first_run(data_dir) else "/"
    url = f"http://127.0.0.1:{port}{landing}"

    # Single-instance guard: if a body is already serving this port, surface it
    # and exit rather than spawning a duplicate (which would silently fail to
    # bind and linger as a zombie).
    try:
        if _another_body_is_running(int(port)):
            log.info("A body is already running on port %s — opening it instead of starting a second.", port)
            try:
                webbrowser.open(f"http://127.0.0.1:{port}/")
            except Exception:
                pass
            return
    except Exception:
        # Never let the guard itself block a legitimate launch.
        pass

    log.info("Aedelgard body data dir: %s", data_dir)
    log.info("Opening %s", url)

    threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    # Hand off to the existing harness entry point. main.py already supports
    # Tower-only mode when no DISCORD_BOT_TOKEN is set — exactly the body case.
    res_root = resource_root()
    if str(res_root) not in sys.path:
        sys.path.insert(0, str(res_root))
    import main as harness_main  # noqa: E402  (repo root main.py)
    harness_main.main()


if __name__ == "__main__":
    main()
