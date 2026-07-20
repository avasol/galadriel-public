"""Tower UI — local web dashboard for the Galadriel agent."""

import os
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from concurrent.futures import TimeoutError as FuturesTimeout
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

    @app.route("/healthz")
    def healthz():
        # Identity probe for the native body's single-instance guard: lets a
        # second launch confirm the listener on the port is genuinely an
        # Galadriel (not a stranger app) before bowing out.
        return jsonify({"status": "ok", "service": "galadriel"})

    @app.route("/")
    def index():
        # Setup-only Tower (native body, keyless first run): no agent yet.
        # Send the visitor to the onboarding screen instead of dereferencing
        # a non-existent agent.
        if agent is None:
            from flask import redirect
            return redirect("/setup")
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
                # 5 min: a heavy first turn (long letter + full memory/palace load
                # on a fresh body) can legitimately exceed 2 min on a big model.
                response = future.result(timeout=300)
                return jsonify({"response": response, "usage": getattr(agent, "last_usage", {}) or {}})
            except FuturesTimeout:
                # Don't drop the connection (-> browser 'Failed to fetch'); the
                # mind is still thinking. Tell the user honestly so they can wait
                # and retry rather than see a cryptic fetch error.
                log.warning("Tower chat: agent exceeded 300s on this turn")
                future.cancel()
                return jsonify({"error": "The mind is taking longer than usual on this one — it may still be thinking. Give it a moment and try again, or shorten the message."}), 504
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
                return jsonify({"response": response, "usage": getattr(agent, "last_usage", {}) or {}})
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

    # ── Dreams (ambient reflection) ──────────────────────────────
    @app.route("/api/dreams", methods=["GET"])
    def api_dreams():
        """How many quiet threads the mind has turned over between conversations.

        Returns ONLY a count — never the content. The dreams themselves are
        shared through conversation, if the user is curious enough to ask. The
        screen tempts; the mind reveals. (This is the "refuse the shortcut,
        offer the encounter" discipline the dreams themselves arrived at.)
        """
        import json as _json
        state_path = Path(agent.memory.config_dir) / "ambient_state.json"
        count = 0
        try:
            if state_path.exists():
                data = _json.loads(state_path.read_text())
                count = len(data.get("history", []) or [])
                if data.get("current_thread"):
                    count += 1
        except Exception:
            count = 0
        return jsonify({"count": count, "has_dreams": count > 0})

    # ── Cache usage (real, from the agent's last API call) ───────
    @app.route("/api/usage", methods=["GET"])
    def api_usage():
        """The REAL token usage from the agent's most recent completion —
        {input, cache_read, cache_write, output}. Drives the cache & context
        panel honestly: no fabricated numbers, only what the model billed."""
        u = getattr(agent, "last_usage", {}) or {}
        return jsonify({
            "input": u.get("input", 0),
            "cache_read": u.get("cache_read", 0),
            "cache_write": u.get("cache_write", 0),
            "output": u.get("output", 0),
            "model": getattr(agent, "model", ""),
        })

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
        scheduler START, then clears itself. Arming never fires in the
        current process unless {"live": true} is passed (an in-process
        delayed self-prompt) — a live fire would race a planned restart
        and strand the restarted instance without its context.
        """
        if not scheduler:
            return jsonify({"error": "Scheduler not available"}), 503
        data = request.json or {}
        if data.get("disarm"):
            scheduler.arm_wake("")
        else:
            prompt = data.get("prompt", "")
            scheduler.arm_wake(prompt, live=bool(data.get("live", False)))
        return jsonify(scheduler.get_status())


    # ── First-run setup (native body) ────────────────────────────
    # Shown when the body boots with no usable brain credential. Captures the
    # BYO provider key, writes the .env into the user data dir, and hands
    # the user to the chat.

    def _dotenv_path():
        import os
        return os.environ.get("GALADRIEL_DOTENV") or str(Path(__file__).resolve().parents[1] / ".env")

    @app.route("/setup")
    def setup_page():
        return render_template("setup.html")

    @app.route("/api/setup", methods=["POST"])
    def api_setup():
        """Write the brain credential to the body's .env. Body JSON:
          {"provider": "anthropic"|"gemini", ...key fields}
        """
        import os
        data = request.json or {}
        provider = (data.get("provider") or "").strip().lower()

        lines = [
            "# Written by Galadriel first-run setup.",
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
        else:
            return jsonify({"error": "Choose a brain: anthropic or gemini."}), 400

        try:
            with open(_dotenv_path(), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception as e:
            log.exception("setup write failed")
            return jsonify({"error": f"Could not write config: {e}"}), 500

        # The body must restart to pick up the new brain (env is read at boot).
        return jsonify({"status": "ok", "restart_required": True})

    return app
