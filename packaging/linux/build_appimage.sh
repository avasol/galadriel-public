#!/usr/bin/env bash
# Build the Aedelgard body as a Linux AppImage.
#
# Runs PyInstaller (onedir) then wraps dist/ in an AppDir and folds it into a
# single .AppImage with appimagetool. Architecture follows the build host
# (x86_64 in CI; arm64 if built on an ARM box — proof-of-mechanism only).
#
# Deps (CI installs them):  pyinstaller, wget, fuse, file
set -euo pipefail

ARCH="$(uname -m)"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIST="$ROOT/dist"
APPDIR="$ROOT/build/AppDir"
OUT="$ROOT/dist/Aedelgard-Body-${ARCH}.AppImage"

echo "==> [1/4] PyInstaller bundle"
cd "$ROOT"
pyinstaller packaging/common/aedelgard_body.spec --noconfirm --distpath "$DIST" --workpath "$ROOT/build/pyi"

echo "==> [2/4] assemble AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r "$DIST/aedelgard-body/." "$APPDIR/usr/bin/"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/aedelgard-body" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/aedelgard-body.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Aedelgard Body
Comment=Your persistent mind, thinking on your own machine
Exec=aedelgard-body
Icon=aedelgard-body
Categories=Utility;
Terminal=false
EOF

# A placeholder icon so appimagetool is satisfied (CI replaces with the real one).
ICON="$ROOT/packaging/linux/aedelgard-body.png"
if [ -f "$ICON" ]; then cp "$ICON" "$APPDIR/aedelgard-body.png"; else
  # 1x1 transparent PNG fallback
  printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82' > "$APPDIR/aedelgard-body.png"
fi

echo "==> [3/4] fetch appimagetool"
TOOL="$ROOT/build/appimagetool-${ARCH}.AppImage"
if [ ! -f "$TOOL" ]; then
  wget -q -O "$TOOL" "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  chmod +x "$TOOL"
fi

echo "==> [4/4] build AppImage -> $OUT"
ARCH="$ARCH" "$TOOL" "$APPDIR" "$OUT" || {
  echo "appimagetool needs FUSE; retrying with --appimage-extract-and-run"
  "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
}
echo "==> done: $OUT"
ls -lh "$OUT"
