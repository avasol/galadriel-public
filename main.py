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

load_dotenv()

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
    app.run(host=host, port=port, use_reloader=False)


def main():
    # Validate required env vars
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    # Resolve config and memory paths relative to this file
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_dir = os.path.join(base_dir, "config")
    memory_dir = os.path.join(base_dir, "memory")

    from harness.agent import GaladrielAgent
    from harness.scheduler import Scheduler
    from harness.job_watcher import JobWatcher

    agent = GaladrielAgent(
        config_dir=config_dir,
        memory_dir=memory_dir,
        working_dir=base_dir,
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
