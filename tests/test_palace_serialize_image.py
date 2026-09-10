"""The archive serializer must never write a binary payload into a .md that
gets mined. (2026-09-03: image blocks str()'d into the archive became 63% of
the palace as base64 noise.)"""
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


from harness.palace import contains_binary_run, sanitize_binary_payloads, add_drawer
from harness.palace_mine_guard import sanitize_batch_files
import pytest
from pathlib import Path
import tempfile


def test_contains_binary_run_predicate():
    # Pure base64
    assert contains_binary_run(FAKE_PNG)
    # Mixed chunk: text before and after
    mixed = "Assistant thinking:\n{'signature': '" + ("A" * 400) + "'}\nThat is all."
    assert contains_binary_run(mixed)
    # Normal text
    assert not contains_binary_run("Hello world, this is a normal conversation with no base64 payloads.")
    # Short base64 (< 300)
    assert not contains_binary_run("short base64: " + ("A" * 150))


def test_sanitize_binary_payloads():
    mixed = "Prefix text " + ("B" * 500) + " suffix text"
    sanitized = sanitize_binary_payloads(mixed)
    assert "Prefix text [binary omitted] suffix text" == sanitized
    assert not contains_binary_run(sanitized)


def test_serialize_mixed_thinking_block():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Here is my reasoning: " + ("C" * 400)},
            {"type": "thinking", "thinking": "private thoughts " + ("D" * 400)},
        ]
    }
    out = _serialize_message(msg)
    assert "[thinking — omitted]" in out
    assert "[binary omitted]" in out
    assert not contains_binary_run(out)


@pytest.mark.anyio
async def test_add_drawer_refuses_pure_binary():
    res = await add_drawer(FAKE_PNG)
    assert "refused: binary payload detected" in res


def test_sanitize_batch_files_cleans_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "test.md"
        p.write_text("Hello " + ("X" * 400) + " world", encoding="utf-8")
        scrubbed = sanitize_batch_files(Path(tmpdir))
        assert scrubbed == 1
        cleaned = p.read_text(encoding="utf-8")
        assert cleaned == "Hello [binary omitted] world"
        assert not contains_binary_run(cleaned)
