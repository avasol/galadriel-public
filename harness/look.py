"""look(path): place a local image into the model's own visual context.

A multimodal brain can capture a screen to disk but has no way to SEE the
file. This module reads a file, sniffs its type by magic bytes, downscales
if needed (PIL, when present), and returns the content-block list the
cascade hands back to the model. The provider API accepts image blocks
inside a tool_result, the same channel pasted screenshots ride.

The tool result for `look` is a LIST of blocks (image + text caption), not
a str. execute_tool's contract widens to `str | list` for this one tool;
the agent cascade must not truncate or stringify list results.
"""

import base64
import io
from pathlib import Path

# Provider hard limits (5 MB / 8000 px). We stay well under.
MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_EDGE = 1568  # documented optimal long edge

_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def _sniff(data: bytes) -> str | None:
    for magic, mt in _MAGIC:
        if data.startswith(magic):
            return mt
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _downscale(data: bytes, media_type: str) -> tuple[bytes, str, str]:
    """Shrink an oversized image. Returns (bytes, media_type, note).
    Raises ImportError when PIL is unavailable — caller turns that into
    an honest error message."""
    from PIL import Image  # noqa: WPS433 — optional dependency, probed here

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    scale = min(MAX_EDGE / max(w, h), 1.0)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    out = io.BytesIO()
    if media_type == "image/png" and (img.mode in ("RGBA", "LA", "P")):
        img.save(out, format="PNG", optimize=True)
        new_mt = "image/png"
    else:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, format="JPEG", quality=85)
        new_mt = "image/jpeg"
    note = f"downscaled from {w}x{h} to {img.size[0]}x{img.size[1]}"
    return out.getvalue(), new_mt, note


def look(path: str):
    """Read an image file and return tool_result content blocks (list),
    or a plain error string when the file cannot be seen."""
    p = Path(path).expanduser()
    if not p.is_file():
        return (f"[look] No file at {p}. Nothing to see — check the path "
                "(capture/download it first, then look again).")
    data = p.read_bytes()
    if not data:
        return f"[look] {p} is empty (0 bytes) — the capture likely failed."
    media_type = _sniff(data)
    if media_type is None:
        return (f"[look] {p} is not a PNG/JPEG/GIF/WebP (unrecognized magic "
                f"bytes: {data[:8]!r}). Only real image files can be seen.")

    note = ""
    needs_shrink = len(data) > MAX_RAW_BYTES
    if not needs_shrink and media_type != "image/gif":
        # Cheap dimension probe only when PIL is present; oversize edges
        # degrade vision quality even under the byte limit.
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                needs_shrink = max(im.size) > MAX_EDGE
        except ImportError:
            pass
    if needs_shrink:
        try:
            data, media_type, note = _downscale(data, media_type)
        except ImportError:
            if len(data) > MAX_RAW_BYTES:
                return (f"[look] {p} is {len(data) // 1024} KB — over the "
                        f"{MAX_RAW_BYTES // 1024} KB provider limit, and PIL "
                        "is not installed to downscale it. Re-capture smaller.")
            # Big edges but under the byte cap and no PIL: send as-is.
        if len(data) > MAX_RAW_BYTES:
            return (f"[look] {p} is still {len(data) // 1024} KB after "
                    "downscaling — the provider will refuse it. Re-capture "
                    "at a lower resolution.")

    caption = f"[look] {p} ({media_type}, {len(data) // 1024} KB"
    if note:
        caption += f", {note}"
    caption += ") — the image above is now in your visual context."
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        },
        {"type": "text", "text": caption},
    ]
