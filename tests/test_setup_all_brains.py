"""First-run setup offers every brain the engine can think with, sends the key,
and never lets a pasted value write extra lines into .env."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEYED = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "nebius": "NEBIUS_API_KEY",
    "xai": "XAI_API_KEY",
    "berget": "BERGET_API_KEY",
}
MODEL_VAR = {
    "anthropic": "AGENT_MODEL",
    "gemini": "GEMINI_MODEL",
    "openai": "OPENAI_MODEL",
    "mistral": "MISTRAL_MODEL",
    "nebius": "NEBIUS_MODEL",
    "xai": "XAI_MODEL",
    "berget": "BERGET_MODEL",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setenv("GALADRIEL_DOTENV", str(env))
    from tower import app as tower_app
    from unittest.mock import MagicMock
    agent = MagicMock()
    agent.memory.memory_dir = str(tmp_path / "memory")
    (tmp_path / "memory").mkdir()
    a = tower_app.create_tower(agent)
    a.config["TESTING"] = True
    return a.test_client(), env


def _post(c, payload):
    return c.post("/api/setup", json=payload, headers={"Host": "127.0.0.1:8080", "Origin": "http://127.0.0.1:8080"})


@pytest.mark.parametrize("prov", sorted(KEYED))
def test_each_keyed_brain_writes_its_key_and_model(client, prov):
    c, env = client
    key = "sk-test-123" if prov == "anthropic" else "k-test-123"
    r = _post(c, {"provider": prov, "api_key": key, "model": "some-model-1"})
    assert r.status_code == 200, r.get_json()
    text = env.read_text()
    assert f"AGENT_PROVIDER={prov}\n" in text
    assert f"{KEYED[prov]}={key}\n" in text
    assert f"{MODEL_VAR[prov]}=some-model-1\n" in text


def test_old_field_names_still_work(client):
    c, env = client
    r = _post(c, {"provider": "anthropic", "anthropic_api_key": "sk-old"})
    assert r.status_code == 200
    assert "ANTHROPIC_API_KEY=sk-old\n" in env.read_text()


def test_missing_key_is_refused(client):
    c, env = client
    r = _post(c, {"provider": "mistral"})
    assert r.status_code == 400
    assert not env.exists()


def test_anthropic_key_must_look_like_one(client):
    c, _ = client
    assert _post(c, {"provider": "anthropic", "api_key": "nope"}).status_code == 400


def test_local_needs_a_model_and_takes_a_base_url(client):
    c, env = client
    assert _post(c, {"provider": "local"}).status_code == 400
    r = _post(c, {"provider": "local", "model": "llama3.1", "base_url": "http://localhost:11434/v1"})
    assert r.status_code == 200
    t = env.read_text()
    assert "AGENT_PROVIDER=local\n" in t and "LOCAL_MODEL=llama3.1\n" in t
    assert "LOCAL_BASE_URL=http://localhost:11434/v1\n" in t


def test_local_base_url_must_be_http(client):
    c, _ = client
    r = _post(c, {"provider": "local", "model": "m", "base_url": "file:///etc/passwd"})
    assert r.status_code == 400


def test_bedrock_nova_needs_no_key(client):
    c, env = client
    r = _post(c, {"provider": "bedrock-nova"})
    assert r.status_code == 200
    assert "AGENT_PROVIDER=bedrock-nova\n" in env.read_text()


@pytest.mark.parametrize("field,value", [
    ("api_key", "k-ok\nAGENT_PROVIDER=evil"),
    ("api_key", "k-ok\rX=1"),
    ("api_key", "k ok"),
    ("api_key", "x" * 600),
    ("model", "m\nX=1"),
])
def test_values_cannot_inject_lines(client, field, value):
    c, env = client
    payload = {"provider": "openai", "api_key": "k-ok", "model": "m"}
    payload[field] = value
    r = _post(c, payload)
    assert r.status_code == 400
    assert not env.exists()


def test_unknown_brain_is_refused(client):
    c, _ = client
    r = _post(c, {"provider": "aedelgard", "api_key": "x"})
    assert r.status_code == 400


def test_setup_page_offers_every_brain_and_sends_the_key():
    html = (ROOT / "tower" / "templates" / "setup.html").read_text(encoding="utf-8")
    values = set(re.findall(r'<option value="([a-z\-]+)"', html))
    assert values >= set(KEYED) | {"local", "bedrock-nova"}
    save = html[html.index("async function save"):]
    save = save[:save.index("fetch(")]
    for f in ("api_key", "model", "base_url"):
        assert f in save, f"save() must send {f}"
    assert "Claude key (operator-blind" not in html
