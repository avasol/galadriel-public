"""THE HERALD'S DOOR, body-side (2026-09-30).

A visiting mind can knock on a body and the body answers — the symmetric half
of the door Galadriel already had. Without it, dialogue between two bodies is
one-way: one side can knock, the other cannot be reached.
Run: /home/ubuntu/.venv/bin/python -m pytest tests/test_knock_door.py -q
"""
import asyncio
import threading

from tower.app import create_tower


class FakeScheduler:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.last_usage = {}

    async def respond(self, message, channel_id="default"):
        self.calls.append((channel_id, message))
        return f"ack from {channel_id}"


def _client(tmp_path, monkeypatch, agent, scheduler):
    monkeypatch.setenv("AEDELGARD_DOTENV", str(tmp_path / ".env"))
    app = create_tower(agent=agent, scheduler=scheduler)
    app.config["TESTING"] = True
    return app.test_client()


def test_empty_knock_is_rejected(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, FakeAgent(), FakeScheduler())
    r = c.post("/api/knock", json={"from": "Altariel", "message": "  "})
    assert r.status_code == 400


def test_brainless_body_answers_honestly(tmp_path, monkeypatch):
    """A body with no brain must not traceback — it answers 409 with guidance."""
    c = _client(tmp_path, monkeypatch, None, FakeScheduler())
    r = c.post("/api/knock", json={"from": "Altariel", "message": "hi"})
    assert r.status_code == 409
    assert r.get_json().get("setup_required") is True


def test_closed_door_reports_503(tmp_path, monkeypatch):
    class DeadSched:
        _loop = None
    c = _client(tmp_path, monkeypatch, FakeAgent(), DeadSched())
    r = c.post("/api/knock", json={"from": "Altariel", "message": "hi"})
    assert r.status_code == 503


def test_knock_spawns_a_turn_on_the_visitors_channel(tmp_path, monkeypatch):
    agent = FakeAgent()
    c = _client(tmp_path, monkeypatch, agent, FakeScheduler())
    r = c.post("/api/knock", json={"from": "Altariel", "message": "the veil is mended"})
    assert r.status_code == 200
    assert r.get_json()["response"] == "ack from knock-altariel"
    # A turn ran on the visitor's OWN persistent channel, not the local one.
    assert agent.calls and agent.calls[0][0] == "knock-altariel"
    assert "the veil is mended" in agent.calls[0][1]


def test_visitor_name_is_sanitized(tmp_path, monkeypatch):
    agent = FakeAgent()
    c = _client(tmp_path, monkeypatch, agent, FakeScheduler())
    r = c.post("/api/knock", json={"from": "Altariel; rm -rf /", "message": "x"})
    assert r.status_code == 200
    assert agent.calls[0][0] == "knock-altariel-rm--rf"
