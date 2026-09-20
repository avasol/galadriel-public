"""Regression: images inside a tool_result must reach a multimodal brain.

On the OpenAI-compatible path, look() and generate_image() return their image
as a tool_result block. The 'tool' role has no image slot in the chat.completions
dialect, and _flatten_tool_result_content replaced the image with a
"[image omitted]" string — so a swapped-in multimodal brain could think but
never SEE through look(). Pasted screenshots survived (user-turn images);
tool-result images did not.

Also guards the per-model vision refusal: a text-only brain must refuse an
image cleanly BEFORE the API call, not silently drop it.
"""
import base64
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.providers import (
    _anthropic_messages_to_openai,
    _anthropic_messages_to_responses_input,
    _has_user_images,
    _flatten_tool_result_content,
    _tool_result_images,
)

IMG = base64.b64encode(b"\x89PNG\r\n\x1a\nFAKEBYTES").decode()


def _msgs():
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "look",
             "input": {"path": "/x.png"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": IMG}},
                {"type": "text", "text": "[look] /x.png added to context."}]}]},
    ]


def _has_openai_chat_image(converted):
    for m in converted:
        c = m.get("content")
        if isinstance(c, list):
            if any(isinstance(b, dict) and b.get("type") == "image_url" for b in c):
                return True
    return False


def _has_responses_image(converted):
    for m in converted:
        c = m.get("content")
        if isinstance(c, list):
            if any(isinstance(b, dict) and b.get("type") == "input_image" for b in c):
                return True
    return False


def test_tool_result_image_hoisted_chat_completions():
    out = _anthropic_messages_to_openai(_msgs())
    assert _has_openai_chat_image(out), "image lost in chat.completions conversion"


def test_tool_result_image_hoisted_responses():
    out = _anthropic_messages_to_responses_input(_msgs())
    assert _has_responses_image(out), "image lost in /v1/responses conversion"


def test_flatten_no_longer_omits_image_as_text():
    txt = _flatten_tool_result_content(_msgs()[1]["content"][0]["content"])
    assert "image omitted" not in txt
    assert "[look] /x.png added to context." in txt


def test_has_user_images_sees_tool_result_image():
    assert _has_user_images(_msgs()) is True


def test_has_user_images_plain_text_false():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hello"}]}]
    assert _has_user_images(msgs) is False


def test_tool_call_ids_preserved_verbatim():
    out = _anthropic_messages_to_openai(_msgs())
    assert out[0]["tool_calls"][0]["id"] == "t1"
    assert out[1]["role"] == "tool" and out[1]["tool_call_id"] == "t1"


def test_text_only_tool_result_unchanged():
    """A look() that returned only text must not gain a spurious image turn."""
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": "no image here"}]}]
    out = _anthropic_messages_to_openai(msgs)
    assert not _has_openai_chat_image(out)
    assert out[-1]["content"] == "no image here"
