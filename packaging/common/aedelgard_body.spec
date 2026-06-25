# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Aedelgard body — one self-contained executable.

Bundles the harness + Tower UI + default config/soul + the ChromaDB embedding
model, so a double-clicked app needs no Python, no pip, and no network on first
mine. Reused by every platform's CI build (Linux AppImage, mac .dmg, win .msi);
only the outer packaging differs.

Build:  pyinstaller packaging/common/aedelgard_body.spec --noconfirm
Output: dist/aedelgard-body  (onedir — fast start, easy to wrap in AppDir/.app)
"""
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = os.path.abspath(os.getcwd())

# Data: ship the UI, the default soul/config, and (if present) the ONNX model.
datas = [
    (os.path.join(ROOT, "tower", "templates"), "tower/templates"),
    (os.path.join(ROOT, "tower", "static"), "tower/static"),
    (os.path.join(ROOT, "config"), "config"),
    (os.path.join(ROOT, "main.py"), "."),
]
_onnx = os.path.join(ROOT, "packaging", "common", "onnx_models")
if os.path.isdir(_onnx):
    datas.append((_onnx, "packaging/common/onnx_models"))
# App icon (rendered keystone mark) — used for the native pywebview window on
# Linux and as a bundled fallback; Windows/mac take their icon from the exe/.app.
_icondir = os.path.join(ROOT, "packaging", "common", "icon_render")
if os.path.isdir(_icondir):
    datas.append((_icondir, "packaging/common/icon_render"))

# mempalace + chromadb pull a lot in dynamically; collect generously.
hiddenimports = []
for pkg in ("mempalace", "chromadb", "anthropic", "flask", "dotenv",
            "google.generativeai", "discord", "tzdata", "webview",
            # pywebview's Windows backend (EdgeChromium/WebView2) loads `clr`
            # from pythonnet dynamically; PyInstaller can't see that, so name
            # the modules explicitly or webview.start() dies at runtime.
            "webview.platforms", "clr_loader", "pythonnet",
            # System-tray presence (pystray) + its image dep (Pillow). The tray
            # gives the body a visible presence in browser-mode and lets the
            # harness own the main thread (the fix for the silent-exit death).
            "pystray", "PIL"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
# `clr` is a compiled extension surfaced by pythonnet; ensure it is named.
for mod in ("clr", "clr_loader.ffi"):
    if mod not in hiddenimports:
        hiddenimports.append(mod)
for pkg in ("chromadb", "mempalace"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass
# tzdata ships the IANA zoneinfo database as package data — required on Windows,
# which has no system tz db, so ZoneInfo("Europe/Stockholm") can resolve.
try:
    datas += collect_data_files("tzdata")
except Exception:
    pass

a = Analysis(
    [os.path.join(ROOT, "packaging", "common", "body_launch.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="aedelgard-body",
    # No console window on Windows (a black terminal flash is unprofessional
    # for a double-clicked app); keep it elsewhere where users may launch
    # from a shell. Logs still go to the body's data dir regardless.
    console=(sys.platform != "win32"),
    disable_windowed_traceback=False,
    icon=(os.path.join(ROOT, "packaging", "windows", "aedelgard.ico")
          if sys.platform == "win32" else None),
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="aedelgard-body",
)
