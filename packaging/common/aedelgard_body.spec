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

# mempalace + chromadb pull a lot in dynamically; collect generously.
hiddenimports = []
for pkg in ("mempalace", "chromadb", "anthropic", "flask", "dotenv",
            "google.generativeai", "discord", "tzdata"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
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
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="aedelgard-body",
)
