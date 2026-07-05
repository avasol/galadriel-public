"""The Toolshed — a mutable tool environment OUTSIDE the frozen body.

The body's core is a signed, immutable PyInstaller bundle (MSIX on Windows):
nothing can be pip-installed into it, and its install directory is read-only
by design. But the mind is allowed to grow tools. The toolshed is a
body-managed Python environment in the user's profile root —
``~/.aedelgard/tools`` — which MSIX does NOT virtualize, so it survives
updates and uninstalls exactly like the palace does.

Layout (all under ~/.aedelgard):
    tools/uv[.exe]     the bootstrapper — astral-sh/uv, one static binary
    tools/venv/        the shed venv (uv-managed CPython, independent of
                       the frozen bundle AND of any Store/system Python)
    tools/uv-cache/    uv's cache + its downloaded CPython builds
    browsers/          PLAYWRIGHT_BROWSERS_PATH (set at boot by apply_env)

Frozen core for trust and signing; mutable toolshed for growth.
"""

import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

SHED_ROOT = Path.home() / ".aedelgard" / "tools"
VENV_DIR = SHED_ROOT / "venv"
BROWSERS_DIR = Path.home() / ".aedelgard" / "browsers"

_IS_WIN = os.name == "nt"
UV_EXE = SHED_ROOT / ("uv.exe" if _IS_WIN else "uv")
import platform

if _IS_WIN:
    _UV_ASSET = "uv-x86_64-pc-windows-msvc.zip"
elif platform.machine().lower() in ("aarch64", "arm64"):
    _UV_ASSET = "uv-aarch64-unknown-linux-gnu.tar.gz"
else:
    _UV_ASSET = "uv-x86_64-unknown-linux-gnu.tar.gz"
_UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/" + _UV_ASSET


def venv_bin() -> Path:
    return VENV_DIR / ("Scripts" if _IS_WIN else "bin")


def apply_env() -> None:
    """Called once at boot. Makes the shed visible to every run_shell child:
    the venv's bin dir leads PATH (so `python`, `pip`, `playwright` resolve to
    the shed, never to the sandboxed Microsoft Store alias), and Playwright's
    browsers live outside the MSIX container."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    os.environ["UV_CACHE_DIR"] = str(SHED_ROOT / "uv-cache")
    os.environ["UV_PYTHON_INSTALL_DIR"] = str(SHED_ROOT / "uv-cache" / "pythons")
    parts = [str(venv_bin()), str(SHED_ROOT)]
    cur = os.environ.get("PATH", "")
    add = os.pathsep.join(p for p in parts if p not in cur)
    if add:
        os.environ["PATH"] = add + os.pathsep + cur


def _run(args: list, timeout: int = 600) -> str:
    """Run a shed subprocess without ever flashing a console (frozen windowed
    parent on Windows — same lesson as tools._run_shell_blocking_windows)."""
    kw = {"capture_output": True, "timeout": timeout}
    if _IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(args, **kw)
    out = (completed.stdout or b"").decode("utf-8", errors="replace")
    err = (completed.stderr or b"").decode("utf-8", errors="replace")
    text = out + (("\n[stderr] " + err) if err.strip() else "")
    if completed.returncode != 0:
        raise RuntimeError(
            f"{' '.join(map(str, args[:3]))}... exited {completed.returncode}:\n{text[-1500:]}")
    return text


def ensure_uv() -> str:
    """Download the uv binary (once). Returns a status line."""
    if UV_EXE.exists():
        return f"uv present at {UV_EXE}"
    SHED_ROOT.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(_UV_URL, timeout=120).read()
    if _IS_WIN:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.endswith("uv.exe"):
                    UV_EXE.write_bytes(z.read(n))
                    break
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            for m in t.getmembers():
                if m.name.endswith("/uv") or m.name == "uv":
                    UV_EXE.write_bytes(t.extractfile(m).read())
                    UV_EXE.chmod(0o755)
                    break
    if not UV_EXE.exists():
        raise RuntimeError("uv download succeeded but no uv binary was extracted")
    return f"uv downloaded to {UV_EXE} ({len(data) // 1048576} MB archive)"


def ensure_venv() -> str:
    """Create the shed venv (uv fetches its own CPython — no dependency on any
    system or Store Python). Returns a status line."""
    py = venv_bin() / ("python.exe" if _IS_WIN else "python")
    if py.exists():
        return f"shed venv present at {VENV_DIR}"
    _run([str(UV_EXE), "venv", "--python", "3.12", str(VENV_DIR)], timeout=600)
    return f"shed venv created at {VENV_DIR} (CPython 3.12 via uv)"


def install(packages: str) -> str:
    """Install packages into the shed venv."""
    steps = [ensure_uv(), ensure_venv()]
    pkgs = packages.split()
    py = venv_bin() / ("python.exe" if _IS_WIN else "python")
    _run([str(UV_EXE), "pip", "install", "--python", str(py), *pkgs], timeout=900)
    steps.append(f"installed: {packages}")
    if any(p.startswith("playwright") for p in pkgs):
        steps.append(
            "NOTE: playwright needs a browser next — use action=install_browsers "
            f"(downloads chromium to {BROWSERS_DIR}, outside the MSIX container).")
    return "\n".join(steps)


def install_browsers() -> str:
    """`playwright install chromium` with a long timeout (~150 MB download)."""
    exe = venv_bin() / ("playwright.exe" if _IS_WIN else "playwright")
    if not exe.exists():
        return "[error] playwright is not installed in the shed yet — run action=install with packages='playwright' first."
    BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
    _run([str(exe), "install", "chromium"], timeout=1800)
    return (f"chromium installed under {BROWSERS_DIR}. If launch fails inside the "
            "MSIX container, pass chromium_sandbox=False (python API) or "
            "--no-sandbox: the body is already a local, user-owned process.")


def status() -> str:
    lines = [
        f"shed root: {SHED_ROOT} ({'exists' if SHED_ROOT.exists() else 'not created yet'})",
        f"uv: {'present' if UV_EXE.exists() else 'not downloaded'}",
        f"venv: {'present' if (venv_bin() / ('python.exe' if _IS_WIN else 'python')).exists() else 'not created'}",
        f"browsers dir: {BROWSERS_DIR} ({'exists' if BROWSERS_DIR.exists() else 'empty'})",
        f"PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '(unset)')}",
    ]
    if UV_EXE.exists() and (venv_bin() / ("python.exe" if _IS_WIN else "python")).exists():
        try:
            listing = _run([str(UV_EXE), "pip", "list",
                            "--python", str(venv_bin() / ("python.exe" if _IS_WIN else "python"))],
                           timeout=60)
            lines.append("packages:\n" + listing.strip())
        except Exception as e:  # status must never explode
            lines.append(f"packages: (listing failed: {e})")
    return "\n".join(lines)


async def execute(action: str, packages: str = "") -> str:
    """Async tool entrypoint — heavy work runs in a thread."""
    import asyncio
    try:
        if action == "status":
            return await asyncio.to_thread(status)
        if action == "install":
            if not packages.strip():
                return "[error] action=install requires packages."
            return await asyncio.to_thread(install, packages)
        if action == "install_browsers":
            return await asyncio.to_thread(install_browsers)
        return f"[error] unknown action '{action}' (use status | install | install_browsers)"
    except Exception as e:
        return f"[error] toolshed: {e}"
