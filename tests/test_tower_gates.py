"""The Tower's two request gates: HOST and ORIGIN.

The Tower has no login; it trusts that only the owner's own browser and tools
can reach it. Two classes of web page break that trust:

* DNS rebinding: a page re-points its own hostname at 127.0.0.1, becomes
  same-origin with the Tower, and can POST JSON to /api/chat (a full,
  tool-wielding turn). The HOST GATE answers only to names the Tower is
  served under (loopback, TOWER_HOST, TOWER_ALLOWED_HOSTS); anything else 421.
* Plain cross-site requests: any page can submit a <form> to
  http://127.0.0.1:<port>/... with a legitimate Host. Routes that take no
  JSON body (extension approve/disable) would act on it. The
  ORIGIN GATE refuses state-changing requests a browser marks as coming from
  another site (Sec-Fetch-Site cross-site/same-site, or a foreign Origin).
  Requests with neither header (CLI, knocks, tests) and reads pass.
"""
import json

import pytest

from tower.app import create_tower


class _Memory:
    def __init__(self, d):
        self.memory_dir = str(d)


class FakeAgent:
    def __init__(self, root):
        mem = root / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        self.memory = _Memory(mem)
        self.conversations = {}
        self.last_usage = {}
        self.model = "fake"


def _make_ext(root):
    d = root / "extensions" / "rhythms"
    (d / "layers").mkdir(parents=True)
    (d / "layers" / "main.md").write_text("hello\n", encoding="utf-8")
    (d / "extension.json").write_text(json.dumps({
        "name": "rhythms", "version": "1.0.0", "title": "Rhythms", "description": "t",
        "kind": "declarative", "body_min": "0.0.0",
        "contributes": {"layers": [{"file": "layers/main.md", "title": "Main"}],
                        "routines": [], "tools": [], "routes": [], "hooks": []},
        "keyring_slots": [], "requires": []}), encoding="utf-8")


def _app(tmp_path, monkeypatch, allowed="tower.lan", tower_host=None):
    monkeypatch.setenv("AEDELGARD_DOTENV", str(tmp_path / ".env"))
    monkeypatch.setenv("TOWER_ALLOWED_HOSTS", allowed)
    if tower_host is None:
        monkeypatch.delenv("TOWER_HOST", raising=False)
    else:
        monkeypatch.setenv("TOWER_HOST", tower_host)
    _make_ext(tmp_path)
    app = create_tower(agent=FakeAgent(tmp_path), scheduler=None)
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return _app(tmp_path, monkeypatch).test_client()


APPROVE = "/api/extensions/rhythms/approve"


# ── HOST GATE ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "[::1]:8080",
                                  "localhost", "tower.lan:8080", "TOWER.LAN"])
def test_host_gate_answers_known_names(client, host):
    assert client.get("/healthz", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080",
                                  "127.0.0.1.evil.example:8080", "attacker.test"])
def test_host_gate_refuses_other_names(client, host):
    r = client.get("/healthz", headers={"Host": host})
    assert r.status_code == 421
    assert r.get_json().get("error")


def test_host_gate_refuses_rebound_chat_post(client):
    r = client.post("/api/chat", json={"message": "hi"}, headers={"Host": "rebind.evil.example"})
    assert r.status_code == 421


def test_tower_host_is_allowed_unless_wildcard(tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch, allowed="", tower_host="box.local").test_client()
    assert c.get("/healthz", headers={"Host": "box.local:8080"}).status_code == 200


def test_wildcard_tower_host_is_not_a_name(tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch, allowed="", tower_host="0.0.0.0").test_client()
    assert c.get("/healthz", headers={"Host": "0.0.0.0:8080"}).status_code == 421
    assert c.get("/healthz", headers={"Host": "localhost:8080"}).status_code == 200


# ── ORIGIN GATE ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1.evil.example:8080"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://127.0.0.1:8080", "Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_post_refused(client, headers):
    r = client.post(APPROVE, headers=headers)
    assert r.status_code == 403
    assert r.get_json().get("error")


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "http://127.0.0.1:8080"},
    {"Origin": "http://localhost:8080"},
    {"Origin": "http://[::1]:8080"},
    {"Origin": "http://tower.lan:8080"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": "http://127.0.0.1:8080", "Sec-Fetch-Site": "same-origin"},
])
def test_same_origin_post_allowed(client, headers):
    r = client.post(APPROVE, headers=headers)
    assert r.status_code == 200


def test_reads_are_not_gated_by_origin(client):
    r = client.get("/api/extensions", headers={"Origin": "https://evil.example",
                                               "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


def test_origin_gate_covers_every_mutating_method(client):
    for method in ("put", "patch", "delete"):
        r = getattr(client, method)(APPROVE, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, method


def test_bodyless_disable_is_covered(client):
    r = client.post("/api/extensions/rhythms/disable", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
