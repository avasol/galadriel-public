"""The per-model vision guard: refuse a text-only brain's image cleanly.

A text-only model must raise ProviderVisionError BEFORE the API call rather
than silently dropping the image (a silent loss is worse than a clean refusal).
Nebius hosts both text-only (V4 Pro) and vision-capable (V4.1 Flash) DeepSeek
models, so the gate is per-model, not per-provider.
"""
import asyncio
import base64
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from harness.providers import (
    ProviderVisionError,
    NebiusProvider,
    _has_user_images,
)

IMG = base64.b64encode(b"\x89PNG\r\n\x1a\nFAKE").decode()


def _img_msg():
    return [{"role": "user", "content": [
        {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": IMG}}]}]


def _text_msg():
    return [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def _nebius():
    return NebiusProvider(api_key="test-key")


def test_nebius_v4_pro_is_text_only():
    p = _nebius()
    assert p._supports_vision_for("deepseek-ai/DeepSeek-V4-Pro") is False


def test_nebius_v4_1_flash_sees_images():
    p = _nebius()
    assert p._supports_vision_for("deepseek-ai/DeepSeek-V4.1-Flash") is True


def test_nebius_v3_text_only():
    p = _nebius()
    assert p._supports_vision_for("deepseek-ai/DeepSeek-V3") is False


def test_has_user_images_detects_plain_image():
    assert _has_user_images(_img_msg()) is True
    assert _has_user_images(_text_msg()) is False


def test_text_only_model_refuses_before_api_call():
    """The guard fires with ProviderVisionError — and with the right TYPE
    (not a NameError from an unbound variable).

    Asserted by class NAME, not the imported class object: under the full
    suite the module can be imported under more than one path, yielding two
    distinct ProviderVisionError classes and a false mismatch."""
    async def _run():
        p = _nebius()
        try:
            await p.complete(model="deepseek-ai/DeepSeek-V4-Pro", max_tokens=16,
                             system=[], tools=[], messages=_img_msg())
        except Exception as e:
            assert type(e).__name__ == "ProviderVisionError", \
                f"wrong exception type: {type(e).__name__}: {e}"
            return
        raise AssertionError("vision guard did not fire")
    asyncio.run(_run())


def test_responses_guard_does_not_raise_nameerror():
    """Regression: the /v1/responses guard once referenced an unbound `model`.
    It must accept the caller's model id (or fall back to oai_model)."""
    import inspect
    from harness.providers import OpenAIProvider
    sig = inspect.signature(OpenAIProvider._complete_responses)
    assert "model" in sig.parameters
