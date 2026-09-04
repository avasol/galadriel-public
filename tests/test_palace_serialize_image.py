"""The archive serializer must never write a binary payload into a .md that
gets mined — image blocks str()'d into the archive would be chunked and
embedded into thousands of meaningless drawers."""
import base64
import os
import re

from harness.palace import _serialize_message, is_binary_noise

FAKE_PNG = base64.b64encode(os.urandom(200 * 1024)).decode()
B64_RUN = re.compile(r"[A-Za-z0-9+/=]{100,}")


def _img_block():
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": FAKE_PNG}}


def test_tool_result_with_image_list_is_placeholder():
    msg = {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": [{"type": "text", "text": "Image saved to /tmp/x.png"}, _img_block()],
    }]}
    out = _serialize_message(msg)
    assert len(out) < 300
    assert "Image saved to /tmp/x.png" in out
    assert "image omitted" in out and "image/png" in out
    assert not B64_RUN.search(out)


def test_top_level_image_block_is_placeholder():
    out = _serialize_message({"role": "user", "content": [_img_block(), {"type": "text", "text": "look"}]})
    assert "image omitted" in out and "look" in out
    assert not B64_RUN.search(out)


def test_string_tool_result_is_verbatim():
    msg = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "plain words 123"}]}
    assert "plain words 123" in _serialize_message(msg)


def test_noise_predicate():
    assert is_binary_noise(FAKE_PNG)
    assert not is_binary_noise("The provider seam shipped with 16 parity tests green.")
    assert not is_binary_noise("short")
