"""THE FADING SIGHT — image payloads age out of the live thread.

An image block living in the live conversation is re-sent to the API on EVERY
call for the life of the thread. A screenshot from look() (or generate_image())
therefore keeps costing context — bytes, or the flat 800-token estimate — on
every turn long after it is relevant. Beyond a small recent window, the payload
is replaced by a short text placeholder; the journal keeps the full record.

Contract under test:
  1. An image older than the window is aged out (top-level AND nested in a
     tool_result — the look() shape).
  2. An image WITHIN the window is KEPT (the mind can still see what it just
     looked at).
  3. Idempotent — a second pass changes nothing.
  4. Never raises on junk; non-image content is untouched.
"""
import sys
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agent import _age_out_stale_images, _IMAGE_LIVE_WINDOW  # noqa: E402


def _img_block():
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(b"X" * 4096).decode(),
        },
    }


def _old_image_message():
    return {"role": "user", "content": [_img_block(), {"type": "text", "text": "look at this"}]}


def _look_tool_result_message():
    """The exact shape look() produces: image + caption INSIDE a tool_result."""
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [_img_block(),
             {"type": "text", "text": "[look] shot.png — the image above is now in your visual context."}]},
        ],
    }


def _text_only_message(i):
    return {"role": "user", "content": f"plain text message {i}"}


def test_old_top_level_image_is_aged_out():
    msgs = [_old_image_message()] + [_text_only_message(i) for i in range(_IMAGE_LIVE_WINDOW + 2)]
    aged = _age_out_stale_images(msgs)
    assert aged == 1
    assert msgs[0]["content"][0]["type"] == "text"
    assert "no longer re-sent" in msgs[0]["content"][0]["text"]


def test_old_look_tool_result_image_is_aged_out():
    """The look() shape: image nested in a tool_result content list."""
    msgs = [_look_tool_result_message()] + [_text_only_message(i) for i in range(_IMAGE_LIVE_WINDOW + 2)]
    aged = _age_out_stale_images(msgs)
    assert aged == 1, "look()'s nested image was not aged out"
    inner = msgs[0]["content"][0]["content"]
    assert inner[0]["type"] == "text"
    assert "no longer re-sent" in inner[0]["text"]
    # the tool_result wrapper itself is preserved (tool-pair integrity intact)
    assert msgs[0]["content"][0]["type"] == "tool_result"
    assert msgs[0]["content"][0]["tool_use_id"] == "t1"


def test_recent_image_is_kept():
    """An image inside the recent window stays live — the mind can still see it."""
    msgs = [_text_only_message(i) for i in range(6)] + [_old_image_message()]
    aged = _age_out_stale_images(msgs)
    assert aged == 0
    assert msgs[-1]["content"][0]["type"] == "image"


def test_idempotent():
    msgs = [_old_image_message()] + [_text_only_message(i) for i in range(_IMAGE_LIVE_WINDOW + 2)]
    first = _age_out_stale_images(msgs)
    second = _age_out_stale_images(msgs)
    assert first == 1 and second == 0, "aging is not idempotent"


def test_never_raises_on_junk():
    for junk in ([], [None], ["a string"], [{"role": "user", "content": 42}],
                 [{"role": "user", "content": [None, {"type": "text", "text": "x"}]}]):
        _age_out_stale_images(junk)  # must not raise


def test_text_content_untouched():
    msgs = [_text_only_message(i) for i in range(20)]
    assert _age_out_stale_images(msgs) == 0
    assert all(isinstance(m["content"], str) for m in msgs)
