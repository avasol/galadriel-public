"""Freeze defaults: a provider's built-in model must be one the vendor still
serves (checked live on the freeze date). Aliases are preferred where the
vendor offers one, so the default ages more slowly."""
import os
from unittest import mock

from harness import providers as P


def test_gemini_default_is_the_flash_alias():
    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}, clear=False):
        os.environ.pop("GEMINI_MODEL", None)
        assert P.GeminiProvider().default_model == "gemini-flash-latest"


def test_gemini_env_still_wins():
    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x", "GEMINI_MODEL": "gemini-pro-latest"}):
        assert P.GeminiProvider().default_model == "gemini-pro-latest"


def test_nebius_default_is_served():
    with mock.patch.dict(os.environ, {"NEBIUS_API_KEY": "x"}, clear=False):
        os.environ.pop("NEBIUS_MODEL", None)
        p = P.NebiusProvider()
        assert p.default_model == "deepseek-ai/DeepSeek-V4-Pro"
