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

        # Accept a custom heartbeat prompt under either key (back-compat).
        prompt = data.get("prompt", data.get("heartbeat_prompt"))

        scheduler.set_heartbeat(
            enabled=bool(enabled), interval=interval, prompt=prompt,
        )
        return jsonify(scheduler.get_status())

    @app.route("/api/scheduler/wake", methods=["POST"])
    def api_scheduler_wake():
        """Arm (or disarm) a single restart-surviving one-shot wake.

        Body: {"prompt": "<self-prompt>"} to arm; {"prompt": ""} or
        {"disarm": true} to disarm. The wake fires exactly once on the next
        scheduler loop (or on the next process start if armed and then
        restarted), then clears itself.
        """
        if not scheduler:
            return jsonify({"error": "Scheduler not available"}), 503
        data = request.json or {}
        if data.get("disarm"):
            scheduler.arm_wake("")
        else:
            prompt = data.get("prompt", "")
            scheduler.arm_wake(prompt)
        return jsonify(scheduler.get_status())


    # ── First-run setup (native body) ────────────────────────────
    # Shown when the body boots with no usable brain credential. Captures the
    # ONE key (Aedelgard device token) OR a BYO provider key, writes the .env
    # into the per-OS user data dir, and hands the user to the chat.

    def _dotenv_path():
        import os
        return os.environ.get("GALADRIEL_DOTENV") or str(Path(__file__).resolve().parents[1] / ".env")

    @app.route("/setup")
    def setup_page():
        return render_template("setup.html")

    @app.route("/api/setup", methods=["POST"])
    def api_setup():
        """Write the brain credential to the body's .env. Body JSON:
          {"provider": "anthropic"|"gemini"|"aedelgard", ...key fields}
        """
        import os
        data = request.json or {}
        provider = (data.get("provider") or "").strip().lower()

        lines = [
            "# Written by the Aedelgard body first-run setup.",
            f"AGENT_PROVIDER={provider}",
            "TOWER_HOST=127.0.0.1",
            "TOWER_PORT=8080",
        ]

        if provider == "anthropic":
            key = (data.get("anthropic_api_key") or "").strip()
            if not key.startswith("sk-"):
                return jsonify({"error": "Enter a valid Anthropic key (starts with sk-)."}), 400
            lines.append(f"ANTHROPIC_API_KEY={key}")
        elif provider == "gemini":
            key = (data.get("gemini_api_key") or "").strip()
            if not key:
                return jsonify({"error": "Enter your Google Gemini API key."}), 400
            lines.append(f"GEMINI_API_KEY={key}")
            lines.append("GEMINI_MODEL=" + (data.get("gemini_model") or "gemini-2.5-flash").strip())
        elif provider == "aedelgard":
            # ONE KEY: the user pastes their aedk (registration key). The
            # AedelgardProvider mints + silently refreshes device tokens from it,
            # so the body keeps thinking past the ~1h token TTL. No device-token
            # paste, no expiry surprise — "paste your key once" made true.
            aedk = (data.get("aedelgard_aedk") or data.get("aedelgard_key") or "").strip()
            broker = (data.get("aedelgard_broker_url") or "https://api.aedelgard.com").strip()
            if not aedk:
                return jsonify({"error": "Paste your Aedelgard key (aedk…)."}), 400
            lines.append(f"AEDELGARD_BROKER_URL={broker}")
            lines.append(f"AEDELGARD_AEDK={aedk}")
        else:
            return jsonify({"error": "Choose a brain: anthropic, gemini, or aedelgard."}), 400

        try:
            with open(_dotenv_path(), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception as e:
            log.exception("setup write failed")
            return jsonify({"error": f"Could not write config: {e}"}), 500

        # The body must restart to pick up the new brain (env is read at boot).
        return jsonify({"status": "ok", "restart_required": True})

    return app
