"""THE EYES — look(path) tests.

The tool places a local image file into the model's own visual context by
returning tool_result content blocks (image + caption). Everything that can
go wrong must come back as an honest STRING; everything that goes right must
come back as a LIST the cascade passes through untouched.

Run: /home/ubuntu/.venv/bin/python -m pytest tests/test_look.py -v
"""

import base64
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.look import MAX_EDGE, look  # noqa: E402

PIL = pytest.importorskip("PIL", reason="look's downscale path needs Pillow")
from PIL import Image  # noqa: E402


def _png(tmp_path, name="img.png", size=(40, 30), mode="RGB"):
    p = tmp_path / name
    Image.new(mode, size, color=(200, 30, 90) if mode == "RGB" else None).save(p)
    return p


def test_look_returns_image_and_caption_blocks(tmp_path):
    p = _png(tmp_path)
    result = look(str(p))
    assert isinstance(result, list) and len(result) == 2
    img, cap = result
    assert img["type"] == "image"
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    # data must round-trip to the real bytes
    assert base64.standard_b64decode(img["source"]["data"]) == p.read_bytes()
    assert cap["type"] == "text"
    assert str(p) in cap["text"]


def test_look_missing_file_is_an_honest_string(tmp_path):
    result = look(str(tmp_path / "nowhere.png"))
    assert isinstance(result, str)
    assert "No file" in result


def test_look_rejects_non_image(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not an image at all")
    result = look(str(p))
    assert isinstance(result, str)
    assert "not a PNG/JPEG/GIF/WebP" in result


def test_look_rejects_empty_file(tmp_path):
    p = tmp_path / "zero.png"
    p.write_bytes(b"")
    assert "empty" in look(str(p))


def test_look_downscales_oversized_edges(tmp_path):
    p = _png(tmp_path, size=(MAX_EDGE * 2, 400))
    result = look(str(p))
    assert isinstance(result, list)
    img, cap = result
    shrunk = Image.open(io.BytesIO(base64.standard_b64decode(img["source"]["data"])))
    assert max(shrunk.size) <= MAX_EDGE
    assert "downscaled" in cap["text"]


def test_look_preserves_alpha_as_png(tmp_path):
    p = _png(tmp_path, name="alpha.png", size=(MAX_EDGE + 100, 200), mode="RGBA")
    result = look(str(p))
    assert isinstance(result, list)
    assert result[0]["source"]["media_type"] == "image/png"


def test_look_jpeg_sniff(tmp_path):
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (20, 20), (1, 2, 3)).save(p, format="JPEG")
    result = look(str(p))
    assert isinstance(result, list)
    assert result[0]["source"]["media_type"] == "image/jpeg"


def test_tool_definition_and_dispatch_wired():
    from harness import tools

    names = [t["name"] for t in tools.TOOL_DEFINITIONS]
    assert "look" in names
    look_def = next(t for t in tools.TOOL_DEFINITIONS if t["name"] == "look")
    assert look_def["input_schema"]["required"] == ["path"]


def test_execute_tool_returns_list_for_look(tmp_path):
    import asyncio

    from harness.tools import execute_tool

    p = _png(tmp_path)
    result = asyncio.run(execute_tool("look", {"path": str(p)}))
    assert isinstance(result, list)
    assert result[0]["type"] == "image"


def test_agent_truncation_guard_skips_lists():
    """The cascade's 15k truncation must never touch a list result — a
    base64 image is far bigger than 15k chars when stringified."""
    src = (Path(__file__).resolve().parent.parent / "harness" / "agent.py").read_text()
    assert 'isinstance(result, str) and len(result) > 15000' in src
