#!/usr/bin/env python3
"""Galadriel Harness — entry point.

Starts the Discord bot, Tower web UI, and Scheduler concurrently.
"""

import os
import sys
import logging
import asyncio
import threading
from dotenv import load_dotenv

# The native body writes its .env into a per-OS user data dir and points
# GALADRIEL_DOTENV at it; source/Docker runs leave it unset and load ./.env.
load_dotenv(os.environ.get("GALADRIEL_DOTENV") or None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("galadriel")


def start_tower(agent, scheduler):
    """Run the Tower Flask app in a background thread."""
    from tower.app import create_tower

    app = create_tower(agent, scheduler)
    host = os.environ.get("TOWER_HOST", "0.0.0.0")
    port = int(os.environ.get("TOWER_PORT", "8080"))
    log.info(f"Tower UI starting on http://{host}:{port}")
    try:
        app.run(host=host, port=port, use_reloader=False, threaded=True)
    except OSError as e:
        # Port already in use (e.g. a second body / stale instance). This runs
        # in a daemon thread, so log loudly — a swallowed bind error here is how
        # a non-serving zombie process is born.
        log.error("Tower failed to bind %s:%s (%s). Is another instance running?",
                  host, port, e)
        raise


def main():
    # Stateless / no-palace mode. `--no-palace` (or GALADRIEL_NO_PALACE=1) runs
    # an amnesiac session — the memory-palace tools are withheld. Forgetting as
    # a feature: full control over what the agent knows, useful for isolated
    # coding sessions. Only memory recall is suppressed; everything else runs.
    if "--no-palace" in sys.argv:
        os.environ["GALADRIEL_NO_PALACE"] = "1"
        log.info("Stateless mode: --no-palace set; memory palace tools are DISABLED for this session.")

    # Validate the credential the SELECTED brain needs. AGENT_PROVIDER decides
    # which key Galadriel thinks with — your own Claude/Gemini key, direct to
    # the model (we are never on the wire). Pick your brain; bring your key.
    from harness.providers import provider_requirements
    provider = os.environ.get("AGENT_PROVIDER", "anthropic").lower()
    needed, hint = provider_requirements(provider)
    if needed and not any(os.environ.get(v) for v in needed):
        names = " or ".join(needed)
        # The native body MUST NOT die when keyless — that is precisely the
        # first-run state the /setup screen exists to resolve. Booting the
        # agent needs a brain credential, so in body mode we instead start a
        # setup-only Tower (agent=None) that serves /setup, collects your
        # provider key, writes the .env, and asks the
        # user to relaunch. Non-body (Docker/source) runs keep the hard exit.
        if os.environ.get("GALADRIEL_BODY") == "1":
            log.info(
                f"First run: no brain credential yet (provider {provider!r}). "
                f"Starting setup-only Tower — open /setup to choose your brain."
            )
            start_tower(agent=None, scheduler=None)
            return
        log.error(
            f"AGENT_PROVIDER={provider!r} needs {names}. {hint} "
            f"Copy .env.example to .env and set it."
        )
        sys.exit(1)
    log.info(f"Brain: {provider} — {hint}")

    # Resolve config and memory paths. The native body relocates the mind
    # to a writable per-OS user data dir via these env vars; source/Docker
    # runs leave them unset and use the repo-relative defaults.
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_dir = os.environ.get("GALADRIEL_CONFIG_DIR") or os.path.join(base_dir, "config")
    memory_dir = os.environ.get("GALADRIEL_MEMORY_DIR") or os.path.join(base_dir, "memory")

    from harness.agent import GaladrielAgent
    from harness.scheduler import Scheduler
    from harness.job_watcher import JobWatcher

    # The body runs on the user's own machine, so red-tier (destructive)
    # commands ask the human at the keyboard rather than block forever. The
    # service (non-body) keeps approval_callback=None, which the agent loop
    # treats as fail-closed (red commands are refused). Either way, a
    # destructive command never runs unattended.
    approval_callback = None
    if os.environ.get("GALADRIEL_BODY") == "1":
        from harness.local_approval import console_approval
        approval_callback = console_approval

    agent = GaladrielAgent(
        config_dir=config_dir,
        memory_dir=memory_dir,
        working_dir=base_dir,
        approval_callback=approval_callback,
    )
    log.info(f"Agent initialized (model: {agent.model})")

    # Create scheduler (no bot yet — will be wired after bot creation)
    scheduler = Scheduler(agent=agent, config_dir=config_dir)

    # Create job watcher (no bot yet — will be wired after bot creation)
    job_watcher = JobWatcher(agent=agent)

    # Attach scheduler to agent so it can be accessed for REST commands
    agent.scheduler = scheduler

    # Attach job_watcher to agent so it can be referenced
    agent.job_watcher = job_watcher

    # Start Tower in a background thread
    tower_thread = threading.Thread(
        target=start_tower, args=(agent, scheduler), daemon=True
    )
    tower_thread.start()

    # Start Discord bot (or run in Tower-only mode)
    discord_token = os.environ.get("DISCORD_BOT_TOKEN")
    if discord_token:
        from discord_bot.bot import create_bot

        bot = create_bot(agent, scheduler, job_watcher)
        scheduler.set_bot(bot)
        job_watcher.set_bot(bot)
        log.info("Starting Discord bot...")
        bot.run(discord_token, log_handler=None)
    else:
        log.info("No DISCORD_BOT_TOKEN set — running in Tower-only mode.")
        log.info("Chat via the Tower UI or set DISCORD_BOT_TOKEN to enable Discord.")
        try:
            tower_thread.join()
        except KeyboardInterrupt:
            log.info("Shutting down.")


if __name__ == "__main__":
    main()
