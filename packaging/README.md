# Packaging — the body as a one-double-click app

This directory turns the body (this repo, run via `main.py`) into native, offline-first
installers. **It is not a fork.** The harness *is* the body; packaging is a launcher + a
bundled Python runtime + the embedding model, wrapping the localhost web app that already
exists (Tower UI on `127.0.0.1:8080`).

## The install ladder

| Tier | User does | Ships | Status |
|---|---|---|---|
| **0** | runs `summon_body.sh` / `docker compose up` | Docker + compose | ✅ done — developers |
| **1** | downloads `.AppImage` / `.dmg` / `.msi`, double-clicks | harness + bundled CPython + bundled embedding model; boots agent, opens browser to `127.0.0.1:8080`; first run = web key-paste screen | **← in progress** |
| **2** | same, but feels native | Tier 1 wrapped in Tauri (~3MB) — real window + tray | later |

## Locked decisions (Lord Isildur, 2026-06-23)

1. **First-run key paste = a web screen at `/setup`**, not a native dialog. Consistent with
   "the body is a localhost web app." Zero new UI tech. The screen accepts EITHER an Aedelgard
   key (`aedk…`, one-key onboarding via the broker relay) OR a BYO provider key (Anthropic /
   Gemini). It writes config and boots the agent loop.
2. **Bundle the embedding model.** The palace uses ChromaDB's default ONNX `all-MiniLM-L6-v2`
   (~167 MB, normally cached at `~/.cache/chroma/onnx_models/`). We ship it inside the artifact
   and seed it into the user's data dir on first run, so the body is **offline-instant** — no
   network fetch to start thinking. Bigger download accepted; offline-first is the whole point.

## Why one recipe, three runners

PyInstaller **cannot cross-compile**. Each OS artifact must be built on its own OS:

- `.AppImage` → built on Linux (this box can do it — `packaging/linux/`)
- `.dmg` → built on `macos-latest` (GitHub Actions)
- `.msi` → built on `windows-latest` (GitHub Actions)

So we prove the recipe end-to-end on Linux *here*, then lift the identical spec into a
3-OS GitHub Actions matrix (`packaging/.github` → `.github/workflows/`).

## What the bundle contains

- The full harness (`harness/`, `tower/`, `main.py`, `config/`, `assets/`).
- CPython runtime (PyInstaller frozen) — no system Python needed.
- All deps incl. `chromadb`, `onnxruntime`, `mempalace`, `anthropic`, the Gemini SDK.
- The 167 MB ONNX embedding model, under `packaging/common/onnx_models/`.
- A first-run launcher (`packaging/common/body_launch.py`) that:
  1. picks a per-user data dir (`$XDG_DATA_HOME/aedelgard` / `~/Library/Application Support/Aedelgard` / `%LOCALAPPDATA%\Aedelgard`),
  2. seeds the embedding model into Chroma's cache dir if absent,
  3. starts the agent + Tower,
  4. opens the browser to `127.0.0.1:8080` (which redirects to `/setup` until a key exists).

## Build (Linux, here)

```bash
pip install pyinstaller
bash packaging/linux/build_appimage.sh
# → dist/Aedelgard-x86_64.AppImage   (or aarch64 on this box)
```
