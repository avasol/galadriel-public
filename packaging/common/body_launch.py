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
    # Prefer the native, labelled "Aedelgard" window by default. It is SAFE now:
    # the launcher pre-flights the WebView2 runtime (Windows) and falls back to
    # the system browser automatically if it is missing — no more hard-aborts.
    # Set AEDELGARD_NATIVE_WINDOW=0 to force the browser.
    os.environ.setdefault("AEDELGARD_NATIVE_WINDOW", "1")
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


def _webview2_runtime_present() -> bool:
    """Detect whether the Edge WebView2 Runtime is actually INSTALLED. pywebview's
    Windows backend hard-ABORTS the process (not a catchable exception) when the
    runtime is missing — so we must check BEFORE calling webview.start(). The
    runtime registers its version under these keys (per Microsoft's documented
    detection method). Empty/absent pv => not installed."""
    if os.name != "nt":
        return True  # mac/Linux use their own reliable backends
    try:
        import winreg
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ]
        for hive, path in keys:
            try:
                with winreg.OpenKey(hive, path) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and pv != "0.0.0.0":
                        return True
            except FileNotFoundError:
                continue
            except Exception:
                continue
        return False
    except Exception:
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


# ── Windows always-on: per-user logon auto-start ─────────────────────────────
# SPEC: packaging/windows/SPEC_always_on_logon.md. PER-USER, never LocalSystem
# (a Session-0 service can't read %APPDATA%\\Aedelgard — the user's mind). The
# body self-registers a per-user Scheduled Task at logon when the install marker
# is set. Full-dreaming: no throttle, the mind is simply present.

_TASK_NAME = "Aedelgard\\Body"  # schtasks folder\\name


def _autostart_desired() -> bool:
    """True if the user opted into logon auto-start (the MSI checkbox writes
    HKCU\\Software\\Aedelgard\\Body\\autostart=1; default ON)."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Aedelgard\\Body") as k:
            val, _ = winreg.QueryValueEx(k, "autostart")
            return int(val) == 1
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _logon_task_exists() -> bool:
    import subprocess
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", _TASK_NAME],
                          capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


def _register_logon_task() -> None:
    """Register a PER-USER logon task running this exe with --logon. Runs as the
    current user (interactive), least privilege, never stops on idle/battery —
    the body must stay present and dreaming. Idempotent."""
    import subprocess
    if _logon_task_exists():
        return
    exe = sys.executable  # the packaged aedelgard-body.exe
    # /SC ONLOGON + /IT (interactive) + /RL LIMITED; /F overwrites if stale.
    cmd = [
        "schtasks", "/Create", "/TN", _TASK_NAME,
        "/TR", f'"{exe}" --logon',
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/IT",
        "/F",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            log.info("Registered per-user logon auto-start task %s", _TASK_NAME)
        else:
            log.warning("Could not register logon task: %s", (r.stderr or "").strip())
    except Exception as e:
        log.warning("Logon task registration failed: %s", e)


def _maybe_self_register_autostart() -> None:
    """On a normal (non --logon) launch, if the user opted in and the task is
    absent, register it — from the user's own context, so no admin needed."""
    if os.name != "nt":
        return
    if "--logon" in sys.argv:
        return  # don't re-register from within the auto-started run
    if _autostart_desired() and not _logon_task_exists():
        _register_logon_task()



# ── System tray: the body's permanent visible presence ───────────────────────
# Why a tray: in browser-mode the body has NO window of its own, so a healthy
# running body and a dead one look identical (just a flashed console). A tray
# icon gives the mind a persistent, visible presence — right-click to Open or
# Quit — and, crucially, lets the launcher run the HARNESS ON THE MAIN THREAD
# (Flask/Werkzeug's dev server needs main-thread signal ownership to stay up;
# running it two levels deep in daemon threads is what caused the silent
# exit-after-first-request death). The tray runs in a background thread.
def run_tray(url: str, icon_path: "Path | None", stop_cb) -> bool:
    """Show a system-tray icon. Returns True if the tray loop started (it then
    blocks in this thread until quit), False if pystray/Pillow is unavailable
    so the caller can fall back to a plain keep-alive."""
    try:
        import pystray
        from PIL import Image
    except Exception as e:
        log.info("System tray unavailable (%s); running headless presence.", e)
        return False
    try:
        if icon_path and icon_path.exists():
            image = Image.open(str(icon_path))
        else:
            # 1x1 fallback so pystray still has an icon to draw.
            image = Image.new("RGBA", (64, 64), (40, 30, 70, 255))
    except Exception as e:
        log.warning("Tray icon image failed (%s); using a flat fallback.", e)
        from PIL import Image as _I
        image = _I.new("RGBA", (64, 64), (40, 30, 70, 255))

    def _open(icon, item):
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _quit(icon, item):
        log.info("Tray: Quit selected — shutting the body down.")
        try:
            icon.stop()
        finally:
            stop_cb()

    menu = pystray.Menu(
        pystray.MenuItem("Open Aedelgard", _open, default=True),
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("Aedelgard", image, "Aedelgard — the Mirror is present", menu)
    log.info("System tray active — the body has a visible presence.")
    icon.run()  # blocks this (background) thread until Quit
    return True


def main() -> None:
    log_path = _setup_file_logging()
    logon_mode = "--logon" in sys.argv  # auto-started at logon: stay silent
    log.info("Aedelgard body launching%s (log: %s)",
             " [logon auto-start]" if logon_mode else "", log_path)

    # If the user opted into always-on, register the per-user logon task now
    # (from their own context — no admin). No-op if already present or not opted.
    _maybe_self_register_autostart()

    # Resolve the port BEFORE preparing the env, so the harness binds what we
    # chose (and so a stranger on 8080 triggers a clean fallback, not a zombie).
    port = _resolve_port()

    data_dir = prepare_environment()

    landing = "/setup" if is_first_run(data_dir) else "/chat"
    url = f"http://127.0.0.1:{port}{landing}"

    # Single-instance guard: if OUR OWN body already serves this port, surface
    # it and exit rather than spawning a duplicate (which would otherwise fail
    # to bind and linger as a zombie, or — worse — collapse the whole process).
    if _port_in_use(port) and _is_our_body(port):
        log.info("An Aedelgard body is already running on port %s — opening it.", port)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/chat")
        except Exception:
            pass
        return

    log.info("Aedelgard body data dir: %s", data_dir)
    log.info("Opening %s", url)

    # Import the existing harness entry point. main.py runs Tower-only when no
    # DISCORD_BOT_TOKEN is set — exactly the body case.
    res_root = resource_root()
    if str(res_root) not in sys.path:
        sys.path.insert(0, str(res_root))
    import main as harness_main  # noqa: E402  (repo root main.py)

    icon_master = res_root / "packaging" / "common" / "icon_render" / "aedelgard.png"
    tray_icon = res_root / "packaging" / "common" / "icon_render" / "aedelgard_64.png"

    # ── ARCHITECTURE (the fix for the silent exit-after-first-request death) ──
    # The harness runs on the MAIN THREAD. Flask/Werkzeug's dev server installs
    # signal handlers and is only stable when it owns the main thread; running it
    # nested two daemon-threads deep (the old design) let the whole daemon chain
    # collapse the instant the server returned — the process exited with code 0,
    # no traceback, no crash log: exactly the "serves /chat 200 then vanishes"
    # symptom. So: harness -> main thread; PRESENCE (browser/window + tray) ->
    # a background thread that waits for the server, then surfaces the UI.
    harness_main_callable = harness_main.main

    def _stop_everything() -> None:
        # Tray "Quit" — the harness owns the main thread and blocks forever in
        # tower_thread.join(); the only clean way out of a windowed process is a
        # hard exit. The mind is already persisted to disk continuously.
        os._exit(0)

    def _presence() -> None:
        # Wait until the harness actually answers before showing anything, so a
        # crashed harness yields a clear log line rather than a dead window.
        if not _wait_for_server(url, timeout=30.0):
            log.error(
                "Harness did not start within 30s — see body.log for the cause.")
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return

        # Logon auto-start: present + dreaming silently. Don't fling a window at
        # the user on every boot; the tray gives a discreet, visible presence.
        if logon_mode:
            log.info("Logon auto-start: body present and dreaming silently on %s. "
                     "Open it from the tray (or Start Menu) when you want it.", url)
            if run_tray(url, tray_icon, _stop_everything):
                return  # tray blocks until Quit
            return  # no tray -> stay silent; harness main thread keeps us alive

        # Normal launch. On Windows, the native pywebview/WebView2 window can
        # hard-ABORT the process when the runtime can't paint — so DEFAULT to the
        # reliable system browser, and only try the native window on explicit
        # opt-in WITH the runtime present. mac/Linux keep the native window.
        native_optin = os.environ.get("AEDELGARD_NATIVE_WINDOW") == "1"
        if os.name == "nt":
            try_native = native_optin and _webview2_runtime_present()
            if native_optin and not try_native:
                log.info("Native window requested but WebView2 runtime not "
                         "detected; opening the browser instead.")
        else:
            try_native = native_optin

        if try_native:
            try:
                if open_native_window(url, icon_path=icon_master):
                    return  # genuine window opened + closed -> presence done
            except Exception as e:
                log.warning("Native window raised (%s); using system browser.", e)

        # Browser + tray: open the chat once, then hold a visible tray presence
        # so a running body is never mistaken for a dead one again.
        log.info("Opening Aedelgard in your default browser.")
        try:
            webbrowser.open(url)
        except Exception:
            log.info("Open your browser to %s", url)
        if not run_tray(url, tray_icon, _stop_everything):
            log.info("No tray available — body running headless on %s.", url)

    presence = threading.Thread(target=_presence, name="presence", daemon=True)
    presence.start()

    # Hand the MAIN THREAD to the harness. This blocks (Tower-only mode joins the
    # Tower thread; Discord mode runs the bot) and keeps the process alive for
    # the life of the body. When it returns, the body is genuinely done.
    try:
        harness_main_callable()
    except SystemExit:
        raise
    except Exception:
        # A windowed build has no console; make the cause findable.
        import traceback
        try:
            crash = user_data_dir() / "body-crash.log"
            crash.write_text(traceback.format_exc(), encoding="utf-8")
            log.error("FATAL in harness — traceback written to %s", crash)
        except Exception:
            pass
        raise


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
