"""EDGE on the Tower: the desk widget's routes (Edge Protocol v1.1, docs/EDGE.md).

The desk widget (avasol/xeneon-edge-companion) talks to /api/edge/*. Every
route needs the Edge token in the X-Edge-Token header (never the query
string); signed file links carry their own signature instead. The origin gate
lets /api/edge/* through only with a valid token. Edge shows and continues the
owner's main conversation ('tower') and receives what the mind says on its own
(scheduler sinks). This engine has no in-browser approvals, so 'approvals' is
not offered and the widget hides that panel.

The token lives in <data root>/edge_token (0600) or EDGE_TOKEN; the Tower page
mints and replaces it through /api/edge-token, which the gates protect like
every other local route.
"""
import asyncio
import json
import os
import stat
import threading
from unittest.mock import MagicMock

import pytest

from harness import edge as edge_core

TOK = "k" * 43
H = {"X-Edge-Token": TOK}
XSITE = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}


@pytest.fixture()
def loop():
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)


class _Memory:
    def __init__(self, d):
        self.memory_dir = str(d)


class FakeAgent:
    def __init__(self, root, reply="final answer", fail=None):
        mem = root / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        self.memory = _Memory(mem)
        self.conversations = {}
        self.last_usage = {}
        self.model = "fake"
        self.calls = []
        self.cleared = []
        self._reply = reply
        self._fail = fail

    async def respond(self, content, channel_id="default"):
        self.calls.append({"content": content, "channel_id": channel_id})
        if self._fail:
            raise self._fail
        return self._reply

    def clear_history(self, channel_id="default"):
        self.cleared.append(channel_id)


@pytest.fixture()
def make(tmp_path, monkeypatch, loop):
    monkeypatch.setenv("AEDELGARD_DOTENV", str(tmp_path / ".env"))
    monkeypatch.delenv("EDGE_TOKEN", raising=False)
    monkeypatch.delenv("TOWER_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("TOWER_HOST", raising=False)

    def _make(agent=None, token=TOK, with_loop=True, scheduler=None):
        from tower.app import create_tower
        agent = agent or FakeAgent(tmp_path)
        if token:
            edge_core.save_token(tmp_path / "edge_token", token)
        sched = scheduler
        if sched is None:
            sched = MagicMock()
            sched._loop = loop if with_loop else None
        app = create_tower(agent=agent, scheduler=sched)
        app.config["TESTING"] = True
        (tmp_path / "memory" / "media").mkdir(parents=True, exist_ok=True)
        return app, agent
    return _make


def _sse(resp):
    out = []
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


GATED = [
    ("GET", "/api/edge/hello"), ("GET", "/api/edge/history"), ("GET", "/api/edge/poll"),
    ("POST", "/api/edge/new"), ("POST", "/api/edge/stream"),
    ("POST", "/api/edge/turn/1/ack"), ("POST", "/api/edge/trace"),
]


# ── the token gate ───────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", GATED)
def test_routes_refuse_without_token_and_stay_cors_readable(make, method, path):
    app, _ = make()
    c = app.test_client()
    for headers in ({}, {"X-Edge-Token": "wrong" * 9}):
        r = c.open(path, method=method, headers=headers, json={})
        assert r.status_code == 403, (path, headers)
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


@pytest.mark.parametrize("method,path", GATED)
def test_preflight_lists_exactly_the_widget_headers(make, method, path):
    app, _ = make()
    r = app.test_client().open(path, method="OPTIONS")
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    allowed = {h.strip().lower() for h in r.headers["Access-Control-Allow-Headers"].split(",")}
    assert allowed == {"x-edge-token", "x-edge-boot", "content-type"}


def test_token_in_query_string_refused(make):
    app, _ = make()
    assert app.test_client().get("/api/edge/hello?token=" + TOK).status_code == 403


def test_no_token_configured_refuses_everything(make):
    app, _ = make(token=None)
    c = app.test_client()
    assert c.get("/api/edge/hello", headers=H).status_code == 403
    assert c.get("/api/edge/hello", headers={"X-Edge-Token": ""}).status_code == 403


def test_env_token_opens_edge(make, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", "e" * 40)
    app, _ = make(token=None)
    assert app.test_client().get(
        "/api/edge/hello", headers={"X-Edge-Token": "e" * 40}).status_code == 200


def test_hello(make):
    app, _ = make()
    r = app.test_client().get("/api/edge/hello", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["protocol"] == "1.1"
    assert d["body"] == "galadriel-public"
    assert d["features"] == ["chat", "stream", "push", "new"]
    assert "approvals" not in d["features"]


# ── the origin gate ──────────────────────────────────────────────────

def test_cross_site_edge_post_needs_the_token(make):
    app, agent = make()
    c = app.test_client()
    assert c.post("/api/edge/new", headers=XSITE).status_code == 403
    assert c.post("/api/edge/new", headers={**XSITE, "X-Edge-Token": "w" * 43}).status_code == 403
    assert agent.cleared == []
    assert c.post("/api/edge/new", headers={**XSITE, **H}).status_code == 200
    assert agent.cleared == ["tower"]


def test_edge_token_opens_nothing_outside_edge(make):
    app, _ = make()
    c = app.test_client()
    r = c.post("/api/clear", headers={**XSITE, **H}, json={"channel": "tower"})
    assert r.status_code == 403
    r = c.post("/api/edge-token", headers={**XSITE, **H})
    assert r.status_code == 403


# ── history ──────────────────────────────────────────────────────────

def test_history_is_the_main_conversation(make):
    app, agent = make()
    agent.conversations["tower"] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi there"},
                                          {"type": "tool_use", "id": "x", "name": "n", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "secret tool output"}]},
        {"role": "assistant", "content": "done"},
    ]
    agent.conversations["morning"] = [{"role": "assistant", "content": "other thread"}]
    msgs = app.test_client().get("/api/edge/history", headers=H).get_json()["messages"]
    assert [(m["role"], m["text"]) for m in msgs] == [
        ("usr", "hello"), ("gal", "hi there"), ("gal", "done")]
    for m in msgs:
        assert "display_text" in m and "time" in m


def test_history_caps_at_forty(make):
    app, agent = make()
    agent.conversations["tower"] = [{"role": "user" if i % 2 == 0 else "assistant",
                                     "content": f"m{i}"} for i in range(60)]
    msgs = app.test_client().get("/api/edge/history", headers=H).get_json()["messages"]
    assert len(msgs) == 40
    assert msgs[-1]["text"] == "m59"


# ── stream, turns, ack ───────────────────────────────────────────────

def test_stream_runs_a_turn_in_the_main_conversation(make):
    app, agent = make()
    c = app.test_client()
    r = c.post("/api/edge/stream", headers=H, json={"message": "  hi  "})
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    evs = _sse(r)
    assert evs[0] == {"t": "turn", "v": 1}
    assert evs[-1]["t"] == "done" and evs[-1]["v"] == "final answer"
    assert "display_v" in evs[-1]
    assert agent.calls == [{"content": "hi", "channel_id": "tower"}]
    p = c.get("/api/edge/poll", headers=H).get_json()
    assert p["edge_turns"]["done"] == [1] and p["edge_turns"]["unacked"] == [1]
    assert c.post("/api/edge/turn/1/ack", headers=H).status_code == 200
    p = c.get("/api/edge/poll", headers=H).get_json()
    assert p["edge_turns"]["unacked"] == []
    assert c.post("/api/edge/turn/99/ack", headers=H).status_code == 404


def test_stream_error_is_an_error_event_and_ends_the_turn(make, tmp_path):
    app, _ = make(agent=FakeAgent(tmp_path, fail=RuntimeError("boom")))
    c = app.test_client()
    evs = _sse(c.post("/api/edge/stream", headers=H, json={"message": "x"}))
    assert evs[-1]["t"] == "error" and evs[-1]["v"]
    assert c.get("/api/edge/poll", headers=H).get_json()["edge_turns"]["done"] == [1]


def test_stream_guards(make):
    app, _ = make()
    c = app.test_client()
    assert c.post("/api/edge/stream", headers=H, json={"message": "   "}).status_code == 400
    assert c.post("/api/edge/stream", headers=H, json={"message": "x" * 200001}).status_code == 400
    r = c.post("/api/edge/stream", headers=H, json={"message": "x", "images": [{"b64": "AA=="}]})
    assert r.status_code == 400
    app2, _ = make(with_loop=False)
    r = app2.test_client().post("/api/edge/stream", headers=H, json={"message": "x"})
    assert r.status_code == 503 and r.get_json().get("fallback") is True


def test_new_clears_the_main_conversation(make):
    app, agent = make()
    assert app.test_client().post("/api/edge/new", headers=H).status_code == 200
    assert agent.cleared == ["tower"]


# ── poll + push ──────────────────────────────────────────────────────

def test_poll_empty_is_204(make):
    app, _ = make()
    assert app.test_client().get("/api/edge/poll", headers=H).status_code == 204


def test_push_through_a_real_scheduler_sink(make, tmp_path):
    from harness.scheduler import Scheduler
    cfg = tmp_path / "config"
    cfg.mkdir()
    agent = FakeAgent(tmp_path)
    sched = Scheduler(agent=agent, config_dir=str(cfg))       # no Discord bot
    app, _ = make(agent=agent, scheduler=sched)
    asyncio.run(sched._send_to_discord("good morning", channel_id="morning"))
    c = app.test_client()
    p = c.get("/api/edge/poll", headers=H).get_json()
    assert p["type"] == "message"
    assert p["message"] == "good morning"
    assert p["title"] == "🌅 Morning"
    assert c.get("/api/edge/poll", headers=H).status_code == 204


def test_a_broken_sink_never_breaks_delivery(tmp_path):
    from harness.scheduler import Scheduler
    cfg = tmp_path / "config"
    cfg.mkdir()
    sched = Scheduler(agent=FakeAgent(tmp_path), config_dir=str(cfg))
    got = []

    def bad(message, title):
        raise RuntimeError("sink down")
    sched.add_sink(bad)
    sched.add_sink(lambda message, title: got.append((message, title)))
    asyncio.run(sched._send_to_discord("hello", channel_id="nope-not-an-event"))
    assert got == [("hello", "")]


# ── trace ────────────────────────────────────────────────────────────

def test_trace_written(make, tmp_path):
    app, _ = make()
    c = app.test_client()
    r = c.post("/api/edge/trace", headers=H,
               json={"boot": "b1", "version": "1.4.0", "events": [{"ev": "x", "n": 1}]})
    assert r.status_code == 200 and r.get_json() == {"written": 1}
    files = list((tmp_path / "memory" / "edge_trace").glob("*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert row["ev"] == "x" and row["boot"] == "b1"
    assert c.post("/api/edge/trace", headers=H, json={"events": "nope"}).status_code == 400


# ── signed files ─────────────────────────────────────────────────────

def test_signed_file_served_without_token_header(make, tmp_path):
    import time
    app, _ = make()
    (tmp_path / "memory" / "media" / "pic.png").write_bytes(b"\x89PNG data")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    c = app.test_client()
    link = "/api/edge/file/" + edge_core.sign_file(TOK, "pic.png", time.time())
    r = c.get(link)
    assert r.status_code == 200 and r.data == b"\x89PNG data"
    assert c.get("/api/edge/file/" + edge_core.sign_file(TOK, "missing.png", time.time())).status_code == 404
    assert c.get("/api/edge/file/" + edge_core.sign_file("x" * 43, "pic.png", time.time())).status_code == 403
    assert c.get("/api/edge/file/junk").status_code == 403


def test_file_url_helper(make):
    app, _ = make()
    url = app.edge.file_url("pic.png")
    assert url.startswith("/api/edge/file/")
    assert TOK not in url


# ── minting the token (local, gated like every other route) ──────────

def test_tower_mints_and_replaces_the_token(make, tmp_path):
    app, _ = make(token=None)
    c = app.test_client()
    assert c.get("/api/edge-token").get_json() == {"configured": False, "from_env": False}
    r = c.post("/api/edge-token")
    assert r.status_code == 200
    tok = r.get_json()["token"]
    assert len(tok) >= 40
    f = tmp_path / "edge_token"
    assert edge_core.load_token(f) == tok
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert c.get("/api/edge/hello", headers={"X-Edge-Token": tok}).status_code == 200
    info = c.get("/api/edge-token").get_json()
    assert info == {"configured": True, "from_env": False}         # never echoes the token
    tok2 = c.post("/api/edge-token").get_json()["token"]
    assert tok2 != tok
    assert c.get("/api/edge/hello", headers={"X-Edge-Token": tok}).status_code == 403
    assert c.get("/api/edge/hello", headers={"X-Edge-Token": tok2}).status_code == 200


def test_old_signed_links_die_with_the_old_token(make, tmp_path):
    import time
    app, _ = make()
    (tmp_path / "memory" / "media" / "pic.png").write_bytes(b"x")
    c = app.test_client()
    link = app.edge.file_url("pic.png")
    assert c.get(link).status_code == 200
    c.post("/api/edge-token")
    assert c.get(link).status_code == 403


def test_env_token_cannot_be_replaced_here(make, monkeypatch):
    monkeypatch.setenv("EDGE_TOKEN", "e" * 40)
    app, _ = make(token=None)
    c = app.test_client()
    assert c.get("/api/edge-token").get_json() == {"configured": True, "from_env": True}
    assert c.post("/api/edge-token").status_code == 409


def test_minting_refuses_cross_site(make):
    app, _ = make(token=None)
    assert app.test_client().post("/api/edge-token", headers=XSITE).status_code == 403
