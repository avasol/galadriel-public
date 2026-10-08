"""The model the agent asks for at boot belongs to the brain it talks to.

AGENT_MODEL is optional. When it is unset, a non-Claude brain uses its own
default; when it names a Claude model but the brain is not Claude (a template
default, or a leftover after switching provider), the brain's own default wins
instead of sending a Claude id to, say, Mistral."""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.agent import _initial_model


def prov(name, default=None):
    p = SimpleNamespace(name=name)
    if default is not None:
        p.default_model = default
    return p


def test_explicit_model_always_wins():
    assert _initial_model("x-1", "claude-sonnet-5", prov("berget", "g")) == "x-1"


def test_claude_brain_uses_agent_model_or_current_default():
    assert _initial_model(None, "claude-opus-5-5", prov("anthropic")) == "claude-opus-5-5"
    assert _initial_model(None, "", prov("anthropic")) == "claude-sonnet-5"
    assert _initial_model(None, None, prov("anthropic")) == "claude-sonnet-5"


def test_other_brain_without_agent_model_uses_its_default():
    assert _initial_model(None, None, prov("mistral", "mistral-large-latest")) == "mistral-large-latest"
    assert _initial_model(None, "", prov("berget", "google/gemma-4-31B-it")) == "google/gemma-4-31B-it"


def test_claude_id_on_another_brain_is_replaced_by_its_default():
    assert _initial_model(None, "claude-sonnet-5", prov("nebius", "deepseek-ai/DeepSeek-V3")) == "deepseek-ai/DeepSeek-V3"
    assert _initial_model(None, "Claude-Opus-5-5", prov("xai", "grok-4.6")) == "grok-4.6"


def test_a_non_claude_agent_model_is_kept_on_another_brain():
    assert _initial_model(None, "mistral-medium-latest", prov("mistral", "mistral-large-latest")) == "mistral-medium-latest"


def test_brain_without_a_default_falls_back_to_agent_model_or_claude_default():
    assert _initial_model(None, "claude-sonnet-5", prov("bedrock-nova")) == "claude-sonnet-5"
    assert _initial_model(None, None, prov("bedrock-nova")) == "claude-sonnet-5"
