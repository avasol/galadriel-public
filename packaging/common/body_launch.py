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
    # Prompt-debug dumps default to a CWD-relative "debug/" — read-only under
    # Program Files (WinError 5). Point it at the writable data dir.
    os.environ.setdefault("GALADRIEL_DEBUG_DIR", str(data_dir / "debug"))
    # The .env the /setup screen writes lives in the user data dir.
    os.environ.setdefault("GALADRIEL_DOTENV", str(data_dir / ".env"))

    for sub in ("config", "memory", "palace", "archive", "debug"):
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


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """Block until the local harness answers <url>, or timeout. Returns True if
    it came up. Used to distinguish 'server still starting' from 'server died'
    so a crashed harness produces a clear log line, not a silent dead window."""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.5)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def open_native_window(url: str, icon_path: "Path | None" = None) -> bool:
    """Open the chat UI in a NATIVE app window (not the system browser).

    Uses pywebview, which wraps the OS-native webview (Edge WebView2 on Windows,
    WebKit on macOS, GTK/Qt WebKit on Linux) around our localhost UI — giving a
    real "app" feeling, no browser chrome, its own taskbar icon. pywebview's
    .start() BLOCKS on the main thread until the window closes (a GUI-loop
    requirement on macOS especially), so the caller must already have the
    harness running in a background thread.

    Returns True if a native window was shown; False if pywebview / its backend
    is unavailable, so the caller can fall back to webbrowser.open().
    """
    try:
        import webview  # pywebview
    except Exception as e:
        log.info("pywebview unavailable (%s); using system browser.", e)
        return False

    # Wait for Tower to answer before painting the window (avoids a blank/err
    # frame). Reuse the same readiness probe semantics as the browser path.
    import urllib.request
    deadline = time.time() + 30.0
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.5)
            break
        except Exception:
            time.sleep(0.4)

    try:
        webview.create_window(
            "Aedelgard",
            url,
            width=1100, height=820,
            min_size=(620, 560),
            text_select=True,
        )
        # gui=None lets pywebview auto-pick the best backend per OS. icon is
        # honoured on the GTK/Qt (Linux) backends; Windows/mac take it from the
        # bundled exe/.app icon, which we set in packaging.
        start_kwargs = {}
        if icon_path and icon_path.exists():
            start_kwargs["icon"] = str(icon_path)
        # webview.start() BLOCKS until the window is closed — UNLESS the backend
        # failed to truly paint (a very common WebView2 failure mode on Windows
        # where the window flashes and the GUI loop exits in milliseconds). In
        # that case start() returns almost instantly. We measure the block time:
        # a near-instant return means the window never really opened, so we treat
        # it as a failure and fall back to the browser instead of quitting the
        # whole body (the silent "opens then vanishes" symptom).
        t0 = time.time()
        webview.start(**start_kwargs)
        elapsed = time.time() - t0
        if elapsed < 3.0:
            log.warning(
                "Native window closed in %.1fs — the WebView2 backend likely "
                "failed to render. Falling back to the system browser.", elapsed)
            return False
        return True  # window was genuinely open and the user closed it
    except Exception as e:
        log.warning("Native window failed (%s); falling back to browser.", e)
        return False


def _port_in_use(port: int) -> bool:
    """True if something is LISTENING on 127.0.0.1:<port>."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _is_our_body(port: int) -> bool:
    """True if the listener on <port> is an Aedelgard body, not a stranger.

    The single-instance guard must not mistake an unrelated app on 8080 (a dev
    server, another tool) for our own body — opening the browser to a stranger
    would be wrong, and silently failing to bind would leave a zombie. The Tower
    serves /healthz with an identifying marker; we probe it briefly.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.0) as r:
            body = r.read(256).decode("utf-8", "ignore").lower()
            return "aedelgard" in body or "galadriel" in body or "ok" in body
    except Exception:
        return False


def _first_free_port(start: int, tries: int = 20) -> int:
    """Return the first free port at or after <start> (cap the search)."""
    for p in range(start, start + tries):
        if not _port_in_use(p):
            return p
    return start  # give up; let the bind error surface loudly


def _resolve_port() -> int:
    """Decide which port the body should serve on.

    Precedence: --port CLI arg > TOWER_PORT env/.env > 8080 default. Then:
      - if our OWN body already serves the chosen port -> caller will surface it
        (single-instance);
      - if a STRANGER holds it -> auto-fallback to the next free port and tell
        the user (so we never silently fail to bind into a zombie);
      - free -> use it.
    Returns the resolved port; sets os.environ["TOWER_PORT"] so the harness
    binds the same one.
    """
    chosen = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            chosen = argv[i + 1]
        elif a.startswith("--port="):
            chosen = a.split("=", 1)[1]
    if chosen is None:
        chosen = os.environ.get("TOWER_PORT", "8080")
    try:
        port = int(chosen)
    except (TypeError, ValueError):
        log.warning("Invalid port %r; falling back to 8080.", chosen)
        port = 8080

    if _port_in_use(port) and not _is_our_body(port):
        free = _first_free_port(port + 1)
        log.warning(
            "Port %s is in use by another application. Aedelgard will use port %s "
            "instead. To pin a port, set TOWER_PORT in your .env or launch with "
            "--port <number>.", port, free)
        port = free

    os.environ["TOWER_PORT"] = str(port)
    return port


def _setup_file_logging() -> "Path | None":
    """Tee logs to a file in the user data dir so a windowed (console=False)
    build NEVER dies silently. The single most important diagnostic: when the
    app vanishes in 1-2s, the reason is written to <data_dir>/body.log."""
    try:
        ddir = user_data_dir()
        log_path = ddir / "body.log"
        handlers = [logging.FileHandler(log_path, encoding="utf-8")]
        # Keep a console handler too where one exists (non-Windows / shell launch).
        if sys.stderr is not None:
            handlers.append(logging.StreamHandler(sys.stderr))
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
            handlers=handlers,
            force=True,
        )
        return log_path
    except Exception:
        logging.basicConfig(level=logging.INFO)
        return None


def main() -> None:
    log_path = _setup_file_logging()
    log.info("Aedelgard body launching (log: %s)", log_path)

    # Resolve the port BEFORE preparing the env, so the harness binds what we
    # chose (and so a stranger on 8080 triggers a clean fallback, not a zombie).
    port = _resolve_port()

    data_dir = prepare_environment()

    landing = "/setup" if is_first_run(data_dir) else "/"
    url = f"http://127.0.0.1:{port}{landing}"

    # Single-instance guard: if OUR OWN body already serves this port, surface
    # it and exit rather than spawning a duplicate (which would otherwise fail
    # to bind in a daemon thread and linger as a zombie).
    if _port_in_use(port) and _is_our_body(port):
        log.info("An Aedelgard body is already running on port %s — opening it.", port)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass
        return

    log.info("Aedelgard body data dir: %s", data_dir)
    log.info("Opening %s", url)

    # Import + run the existing harness entry point. main.py supports Tower-only
    # mode when no DISCORD_BOT_TOKEN is set — exactly the body case.
    res_root = resource_root()
    if str(res_root) not in sys.path:
        sys.path.insert(0, str(res_root))
    import main as harness_main  # noqa: E402  (repo root main.py)

    # The app-feeling path: wrap the localhost UI in a NATIVE window via
    # pywebview. Because webview.start() must own the MAIN thread (a hard GUI
    # requirement on macOS), we run the harness server in a BACKGROUND thread
    # and let the window block the main thread. When the window closes, the
    # process exits (daemon server thread dies with it) — closing the window
    # quits the app, as users expect.
    icon_master = res_root / "packaging" / "common" / "icon_render" / "aedelgard.png"

    server = threading.Thread(target=harness_main.main, daemon=True)
    server.start()

    # Confirm the harness server actually came UP before we try anything. If the
    # harness thread crashed (bad credential, missing dep), the window would
    # otherwise paint a dead frame or pywebview would fail confusingly. Probe
    # first; if it never answers, the harness died — say so loudly (the reason
    # is in body.log) and open the browser to the URL anyway so the user isn't
    # staring at a vanished process.
    if not _wait_for_server(url, timeout=30.0):
        log.error(
            "Harness did not start within 30s — see body.log for the cause. "
            "The app window cannot open without it.")
        # Still try the browser so a transient slow start isn't a dead end.
        try:
            webbrowser.open(url)
        except Exception:
            pass
        # Keep the process alive briefly so any late server log flushes.
        for _ in range(10):
            if server.is_alive():
                server.join(timeout=1.0)
        return

    # Server is up. Try the native window; on ANY failure fall back to browser
    # and keep the process alive on the (daemon) server thread.
    shown = False
    try:
        shown = open_native_window(url, icon_path=icon_master)
    except Exception as e:
        log.warning("Native window raised (%s); using system browser.", e)
        shown = False

    if shown:
        return  # window closed -> quit the body

    log.info("Native window unavailable; opening the system browser instead.")
    try:
        webbrowser.open(url)
    except Exception:
        log.info("Open your browser to %s", url)
    try:
        while server.is_alive():
            server.join(timeout=1.0)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Last-resort: a windowed build has no console, so an uncaught exception
        # would vanish (the 1-2s silent death). Write the traceback where the
        # user — and we — can find it, then surface a hint via the browser.
        import traceback
        try:
            ddir = user_data_dir()
            crash = ddir / "body-crash.log"
            crash.write_text(traceback.format_exc(), encoding="utf-8")
            log.error("FATAL — traceback written to %s", crash)
        except Exception:
            pass
        raise
