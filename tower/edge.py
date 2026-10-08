"""EDGE on the Tower: the desk widget's routes (Edge Protocol v1.1).

Wires harness/edge.py into Flask routes under /api/edge/*. Every route needs
the Edge token in the X-Edge-Token header (never the query string); signed file
links carry their own signature instead. The token lives in
<data root>/edge_token (0600) or EDGE_TOKEN; the Tower page mints and replaces
it through /api/edge-token, which the normal gates protect.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import queue
import time
from datetime import datetime
from pathlib import Path

from flask import jsonify, request, Response, send_file

from harness import edge
from harness.response_status import present

log = __import__("logging").getLogger("galadriel.tower.edge")


class EdgeState:
    """Everything the Edge routes share: token, turns, outbox, paths."""

    def __init__(self, agent, scheduler):
        self.agent = agent
        self.scheduler = scheduler
        self.turns = edge.TurnTracker()
        self.outbox = edge.Outbox()
        memory_dir = Path(agent.memory.memory_dir) if agent is not None else Path(".")
        self.memory_dir = memory_dir
        self.token_path = memory_dir.parent / "edge_token"
        self.media_dirs = [memory_dir / "media"]
        self.trace_dir = memory_dir / "edge_trace"

    @property
    def token(self):
        return edge.load_token(self.token_path)

    def token_valid(self, supplied) -> bool:
        return edge.token_ok(self.token, supplied)

    def file_url(self, name: str) -> str:
        return "/api/edge/file/" + edge.sign_file(self.token, name, time.time())


def register_edge(app, agent, scheduler) -> EdgeState:
    """Register the /api/edge/* routes and return the shared EdgeState."""
    state = EdgeState(agent, scheduler)
    app.edge = state

    if scheduler is not None and hasattr(scheduler, "add_sink"):
        scheduler.add_sink(lambda message, title: state.outbox.push(message, title))

    def _guard():
        if not state.token_valid(request.headers.get("X-Edge-Token", "")):
            return jsonify({"error": "Refused: a valid X-Edge-Token is required."}), 403
        return None

    @app.route("/api/edge/hello", methods=["GET", "OPTIONS"])
    def edge_hello():
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        return jsonify({
            "body": "galadriel-public",
            "version": os.environ.get("GALADRIEL_VERSION", ""),
            "protocol": "1.1",
            "features": ["chat", "stream", "push", "new"],
        })

    @app.route("/api/edge/history", methods=["GET", "OPTIONS"])
    def edge_history():
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        messages = []
        for msg in (agent.conversations.get("tower", []) if agent else []):
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                    elif hasattr(block, "text"):
                        parts.append(str(block.text))
                text = "\n".join(p for p in parts if p)
            else:
                text = ""
            if not text:
                continue
            role = "usr" if msg.get("role") == "user" else "gal"
            messages.append({
                "role": role,
                "text": text,
                "display_text": present(text, agent) if role == "gal" else text,
                "time": "",
            })
        return jsonify({"messages": messages[-40:]})

    @app.route("/api/edge/stream", methods=["POST", "OPTIONS"])
    def edge_stream():
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        data = request.json or {}
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Empty message"}), 400
        if len(message) > 200000:
            return jsonify({"error": "Message too long"}), 400
        if data.get("images") or data.get("files"):
            return jsonify({"error": "This engine does not take attachments on Edge yet."}), 400
        if not (scheduler and scheduler._loop and scheduler._loop.is_running()):
            return jsonify({"error": "streaming unavailable", "fallback": True}), 503

        turn = state.turns.begin()
        q: queue.Queue = queue.Queue()
        loop = scheduler._loop

        async def _run():
            try:
                text = await agent.respond(message, channel_id="tower")
                q.put({"t": "done", "v": str(text), "display_v": present(text, agent)})
            except Exception as e:
                q.put({"t": "error", "v": str(e) or "The turn failed."})
            finally:
                state.turns.end(turn)

        asyncio.run_coroutine_threadsafe(_run(), loop)

        def _gen():
            yield "data: " + json.dumps({"t": "turn", "v": turn}) + "\n\n"
            try:
                idle_limit = float(os.environ.get("TURN_STREAM_IDLE_MINUTES", "10")) * 60
            except ValueError:
                idle_limit = 600.0
            idle = 0.0
            while True:
                try:
                    ev = q.get(timeout=2)
                except queue.Empty:
                    idle += 2
                    if idle >= idle_limit:
                        yield "data: " + json.dumps(
                            {"t": "error", "v": "The turn timed out."}) + "\n\n"
                        return
                    yield ": keep\n\n"
                    continue
                idle = 0.0
                yield "data: " + json.dumps(ev) + "\n\n"
                if ev.get("t") in ("done", "error"):
                    return

        return Response(_gen(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.route("/api/edge/poll", methods=["GET", "OPTIONS"])
    def edge_poll():
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        item = state.outbox.pop()
        turns = state.turns.view()
        if item is None and turns is None:
            return "", 204
        body = dict(item) if item else {"type": "status"}
        if turns:
            body["edge_turns"] = turns
        return jsonify(body)

    @app.route("/api/edge/turn/<int:turn_id>/ack", methods=["POST", "OPTIONS"])
    def edge_ack(turn_id):
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        if state.turns.ack(turn_id):
            return jsonify({"status": "ok"})
        return jsonify({"error": "Unknown turn"}), 404

    @app.route("/api/edge/new", methods=["POST", "OPTIONS"])
    def edge_new():
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        if agent is not None:
            agent.clear_history("tower")
        return jsonify({"status": "ok"})

    @app.route("/api/edge/trace", methods=["POST", "OPTIONS"])
    def edge_trace():
        if request.method == "OPTIONS":
            return "", 204
        bad = _guard()
        if bad:
            return bad
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"error": "trace must be a JSON object"}), 400
        now = datetime.now()
        try:
            lines = edge.clean_trace(data, recv=now.isoformat(timespec="seconds"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        try:
            written = edge.write_trace(state.trace_dir, lines, day=now.strftime("%Y-%m-%d"))
        except OSError as e:
            return jsonify({"error": str(e)}), 503
        return jsonify({"written": written})

    @app.route("/api/edge/file/<path:signed>", methods=["GET", "OPTIONS"])
    def edge_file(signed):
        if request.method == "OPTIONS":
            return "", 204
        name = edge.verify_file(state.token, signed, time.time())
        if name is None:
            return jsonify({"error": "Refused: bad or expired link."}), 403
        for base in state.media_dirs:
            try:
                base_r = base.resolve()
                target = (base / name).resolve()
            except OSError:
                continue
            if base_r == target or base_r not in target.parents:
                continue
            if target.is_file():
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                resp = send_file(str(target), mimetype=mime)
                resp.headers["Cache-Control"] = "private, max-age=86400"
                return resp
        return jsonify({"error": "Not found"}), 404

    @app.after_request
    def _edge_cors(resp):
        if request.path.startswith("/api/edge/"):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "X-Edge-Token, X-Edge-Boot, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    # ── minting the token (local, gated like every other route) ──────────

    @app.route("/api/edge-token", methods=["GET", "OPTIONS"])
    def edge_token_info():
        if request.method == "OPTIONS":
            return "", 204
        return jsonify({
            "configured": bool(state.token),
            "from_env": bool((os.environ.get("EDGE_TOKEN") or "").strip()),
        })

    @app.route("/api/edge-token", methods=["POST", "OPTIONS"])
    def edge_token_mint():
        if request.method == "OPTIONS":
            return "", 204
        if (os.environ.get("EDGE_TOKEN") or "").strip():
            return jsonify({"error": "The token is set by EDGE_TOKEN; change it there."}), 409
        tok = edge.new_token()
        edge.save_token(state.token_path, tok)
        return jsonify({"token": tok})

    return state
