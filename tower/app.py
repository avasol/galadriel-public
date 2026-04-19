"""Tower UI — local web dashboard for the Galadriel agent."""

import os
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify

log = logging.getLogger("galadriel.tower")


def create_tower(agent, scheduler=None) -> Flask:
    """Create the Flask Tower app wired to the agent and scheduler."""
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = os.environ.get("TOWER_SECRET_KEY", "change-me")

    @app.route("/")
    def index():
        channels = len(agent.conversations)
        total_msgs = sum(len(m) for m in agent.conversations.values())
        memory_files = sorted(Path(agent.memory.memory_dir).glob("*.md"), reverse=True)
        recent_memories = [f.stem for f in memory_files[:7]]
        sched_status = scheduler.get_status() if scheduler else None
        return render_template(
            "index.html",
            model=agent.model,
            channels=channels,
            total_msgs=total_msgs,
            recent_memories=recent_memories,
            now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            scheduler=sched_status,
        )

    @app.route("/chat")
    def chat_page():
        return render_template("chat.html")

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        data = request.json
        message = data.get("message", "").strip()
        if not message:
            return jsonify({"error": "Empty message"}), 400

        # Schedule the async agent call onto the main event loop (Discord's loop)
        # This avoids creating a new event loop and works with AsyncAnthropic
        if scheduler and scheduler._loop and scheduler._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                agent.respond(message, channel_id="tower"),
                scheduler._loop,
            )
            try:
                response = future.result(timeout=120)  # 2 min timeout
                return jsonify({"response": response})
            except Exception as e:
                log.exception("Tower chat error")
                return jsonify({"error": str(e)}), 500
        else:
            # Fallback: create a new event loop (shouldn't normally happen)
            loop = asyncio.new_event_loop()
            try:
                response = loop.run_until_complete(
                    agent.respond(message, channel_id="tower")
                )
                return jsonify({"response": response})
            except Exception as e:
                log.exception("Tower chat error")
                return jsonify({"error": str(e)}), 500
            finally:
                loop.close()

    @app.route("/api/history", methods=["GET"])
    def api_history():
        channel = request.args.get("channel", "tower")
        messages = agent.conversations.get(channel, [])
        # Serialize for the frontend
        history = []
        for msg in messages:
            if isinstance(msg.get("content"), str):
                history.append({"role": msg["role"], "text": msg["content"]})
            elif isinstance(msg.get("content"), list):
                texts = []
                for block in msg["content"]:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                    elif isinstance(block, dict) and "content" in block:
                        texts.append(str(block["content"])[:200])
                if texts:
                    history.append({"role": msg["role"], "text": "\n".join(texts)})
        return jsonify({"history": history})

    @app.route("/api/clear", methods=["POST"])
    def api_clear():
        channel = request.json.get("channel", "tower")
        agent.clear_history(channel)
        return jsonify({"status": "ok"})

    @app.route("/api/memory", methods=["GET"])
    def api_memory():
        date = request.args.get("date")
        if date:
            path = Path(agent.memory.memory_dir) / f"{date}.md"
            if path.exists():
                return jsonify({"date": date, "content": path.read_text()})
            return jsonify({"error": "Not found"}), 404
        # List all memory files
        files = sorted(Path(agent.memory.memory_dir).glob("*.md"), reverse=True)
        return jsonify({"files": [f.stem for f in files]})

    # ── Vision API ───────────────────────────────────────────────

    @app.route("/api/vision", methods=["GET"])
    def api_vision_get():
        """Return the active vision and the list of available ones."""
        config_dir = Path(agent.memory.config_dir)
        visions_dir = config_dir / "visions"
        active_file = config_dir / "active_vision.txt"

        available = []
        if visions_dir.is_dir():
            available = sorted(f.stem for f in visions_dir.glob("*.md"))

        active = None
        if active_file.exists():
            active = active_file.read_text(encoding="utf-8").strip() or None

        return jsonify({"active": active, "available": available})

    @app.route("/api/vision", methods=["POST"])
    def api_vision_set():
        """Set the active vision. Pass {"name": "<stem>"} or {"name": ""} to clear.

        The change takes effect on the NEXT API call — existing cached
        prefixes become stale and will be re-cached naturally.
        """
        data = request.json or {}
        name = (data.get("name") or "").strip()

        config_dir = Path(agent.memory.config_dir)
        visions_dir = config_dir / "visions"
        active_file = config_dir / "active_vision.txt"

        if name:
            vision_path = visions_dir / f"{name}.md"
            if not vision_path.exists():
                return jsonify({"error": f"Vision '{name}' not found"}), 404
            active_file.write_text(name, encoding="utf-8")
        else:
            if active_file.exists():
                active_file.unlink()

        return jsonify({"active": name or None})

    # ── Scheduler API ────────────────────────────────────────────

    @app.route("/api/scheduler", methods=["GET"])
    def api_scheduler_status():
        if not scheduler:
            return jsonify({"error": "Scheduler not available"}), 503
        return jsonify(scheduler.get_status())

    @app.route("/api/scheduler/heartbeat", methods=["POST"])
    def api_scheduler_heartbeat():
        if not scheduler:
            return jsonify({"error": "Scheduler not available"}), 503
        data = request.json or {}
        enabled = data.get("enabled")
        interval = data.get("interval")

        if enabled is None:
            return jsonify({"error": "Missing 'enabled' field"}), 400

        if interval is not None:
            interval = int(interval)

        scheduler.set_heartbeat(enabled=bool(enabled), interval=interval)
        return jsonify(scheduler.get_status())

    return app
