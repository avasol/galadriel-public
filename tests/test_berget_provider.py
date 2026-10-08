"""Berget AI as a brain door (api.berget.ai, OpenAI-compatible, EU-hosted in Sweden).

The catalogue may list Berget models as `berget:<model id>`; the provider strips
that prefix before calling the API. Vision is per model.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import providers as P


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("BERGET_API_KEY", "BERGET_MODEL", "BERGET_BASE_URL",
              "AGENT_MODEL_FALLBACKS", "AGENT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_registry_knows_berget():
    assert P._REGISTRY["berget"] is P.BergetProvider
    assert issubclass(P.BergetProvider, P.OpenAIProvider)


def test_make_provider_builds_berget(clean_env):
    clean_env.setenv("BERGET_API_KEY", "test-key")
    p = P.make_provider("berget")
    assert isinstance(p, P.BergetProvider)
    assert p.name == "berget"
    assert p.base_url.rstrip("/") == "https://api.berget.ai/v1"
    assert p.default_model == "google/gemma-4-31B-it"


def test_model_and_base_from_env(clean_env):
    clean_env.setenv("BERGET_API_KEY", "test-key")
    clean_env.setenv("BERGET_MODEL", "moonshotai/Kimi-K3")
    clean_env.setenv("BERGET_BASE_URL", "https://eu.example/v1")
    p = P.BergetProvider()
    assert p.default_model == "moonshotai/Kimi-K3"
    assert p.base_url.rstrip("/") == "https://eu.example/v1"


def test_missing_key_fails_loudly(clean_env):
    with pytest.raises(RuntimeError, match="BERGET_API_KEY"):
        P.BergetProvider()


def test_boot_requirement_is_the_berget_key():
    env_vars, hint = P.provider_requirements("berget")
    assert env_vars == ("BERGET_API_KEY",)
    assert "berget" in hint.lower()


def test_catalogue_prefix_is_stripped_before_the_api_call():
    p = P.BergetProvider(api_key="test-key")
    assert p._pick_model("berget:google/gemma-4-31B-it") == "google/gemma-4-31B-it"
    assert p._pick_model("moonshotai/Kimi-K3") == "moonshotai/Kimi-K3"


@pytest.mark.parametrize("mid,sees", [
    ("berget:google/gemma-4-31B-it", True),
    ("google/gemma-4-31B-it", True),
    ("berget:moonshotai/Kimi-K3", True),
    ("berget:zai-org/GLM-5.3-Flash", True),
    ("berget:Qwen/Qwen3.8-27B-FP8", True),
    ("berget:mistralai/Mistral-Small-3.2-24B-Instruct-2506", False),
    ("berget:some/unknown-model", False),
])
def test_vision_gate_is_per_model(mid, sees):
    p = P.BergetProvider(api_key="test-key")
    assert p._supports_vision_for(mid) is sees


@pytest.mark.parametrize("name,var", [
    ("openai", "OPENAI_API_KEY"),
    ("nebius", "NEBIUS_API_KEY"),
    ("mistral", "MISTRAL_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("berget", "BERGET_API_KEY"),
    ("local", "OPENAI_API_KEY"),
])
def test_auth_hint_names_the_right_key(name, var):
    # A 401 must tell the user which key to check, for every OpenAI-dialect door.
    assert P._auth_key_var(name) == var
