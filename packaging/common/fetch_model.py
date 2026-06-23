#!/usr/bin/env python3
"""Fetch the ChromaDB default embedding model into the bundle location.

CI runs this before PyInstaller so the 167MB ONNX all-MiniLM-L6-v2 model ends
up at packaging/common/onnx_models/ and gets baked into the artifact — making
the body offline-first (no model download on the user's first palace mine).

We don't vendor the model in git (it would bloat the repo); each CI build
fetches it fresh. Triggering ChromaDB's default embedding function downloads
and unpacks it into Chroma's cache; we then mirror that into our bundle dir.
"""
import shutil
import sys
from pathlib import Path

DEST = Path(__file__).resolve().parent / "onnx_models"


def main() -> int:
    if DEST.is_dir() and any(DEST.iterdir()):
        print(f"[fetch_model] already present at {DEST}")
        return 0

    print("[fetch_model] downloading all-MiniLM-L6-v2 via ChromaDB default EF…")
    try:
        from chromadb.utils import embedding_functions
        ef = embedding_functions.ONNXMiniLM_L6_V2()
        # _download_model_if_not_exists is the documented internal; calling the
        # EF on a sample also forces the download. Use the public path: embed.
        ef(["seed"])
    except Exception as e:  # pragma: no cover - CI diagnostic
        print(f"[fetch_model] ERROR triggering model download: {e}", file=sys.stderr)
        return 1

    # Locate Chroma's cache (honors CHROMA_CACHE_DIR; else ~/.cache/chroma).
    import os
    cache = Path(os.environ.get("CHROMA_CACHE_DIR",
                                Path.home() / ".cache" / "chroma")) / "onnx_models"
    if not cache.is_dir():
        print(f"[fetch_model] ERROR: expected cache at {cache}, not found",
              file=sys.stderr)
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    for child in cache.iterdir():
        target = DEST / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)
    size = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print(f"[fetch_model] seeded {DEST} ({size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
