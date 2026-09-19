import pytest
import os
from harness.providers import (
    NebiusProvider,
    make_provider,
    _make_single,
    provider_requirements,
    ProviderAuthError,
)

def test_nebius_missing_key_raises(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        NebiusProvider(api_key="")
    assert "NebiusProvider needs NEBIUS_API_KEY" in str(exc.value)

def test_nebius_make_single(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "fake-key")
    p = _make_single("nebius")
    assert isinstance(p, NebiusProvider)
    assert p.name == "nebius"
    assert p.base_url == "https://api.studio.nebius.ai/v1"
    assert p.default_model == "deepseek-ai/DeepSeek-V3"

def test_nebius_make_provider_without_fallbacks(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "fake-key")
    monkeypatch.delenv("AGENT_MODEL_FALLBACKS", raising=False)
    p = make_provider("nebius")
    assert isinstance(p, NebiusProvider)

def test_nebius_provider_requirements():
    reqs, hint = provider_requirements("nebius")
    assert reqs == ("NEBIUS_API_KEY",)
    assert "Nebius" in hint
