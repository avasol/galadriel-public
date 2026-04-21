"""Scheduler — heartbeat, morning greeting, and goodnight routines.

Three scheduled activities:
  1. Heartbeat: periodic self-initiated check-in (configurable interval, toggle on/off).
  2. Morning (09:10 CET, workdays only): morning greeting, calendar, coffers check.
  3. Goodnight (21:00 CET): wish good night and disable heartbeat (REST).
"""

import asyncio
import logging
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("galadriel.scheduler")

CET = ZoneInfo("Europe/Stockholm")

# Morning: 09:10 CET on workdays (Mon-Fri)
MORNING_TIME = time(9, 10)
# Goodnight: 21:00 CET every day
GOODNIGHT_TIME = time(21, 0)

# Valid heartbeat intervals in minutes
VALID_INTERVALS = [5, 10, 20, 30]
DEFAULT_INTERVAL = 10

# State file lives in config/ (ReadWritePaths in systemd)
STATE_FILE_NAME = "scheduler_state.json"


class Scheduler:
    """Manages periodic and cron-like tasks for Galadriel."""

    def __init__(self, agent, discord_bot=None, config_dir: str = "config"):
        self.agent = agent
        self.bot = discord_bot
        self._state_path = Path(config_dir) / STATE_FILE_NAME
        self._loop: asyncio.AbstractEventLoop | None = None  # captured in start()

        # Heartbeat state
        self.heartbeat_enabled = False
        self.heartbeat_interval = DEFAULT_INTERVAL  # minutes
        self._heartbeat_task: asyncio.Task | None = None

        # Cron tasks
        self._morning_task: asyncio.Task | None = None
        self._goodnight_task: asyncio.Task | None = None

        # Track last fire times to avoid double-fires
        self._last_morning: str | None = None
        self._last_goodnight: str | None = None

        # Load persisted state
        self._load_state()

    # ── Persistence ──────────────────────────────────────────────

    def _load_state(self):
        """Load heartbeat state from disk."""
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self.heartbeat_enabled = data.get("heartbeat_enabled", False)
                interval = data.get("heartbeat_interval", DEFAULT_INTERVAL)
                if interval in VALID_INTERVALS:
                    self.heartbeat_interval = interval
                log.info(f"Scheduler state loaded: enabled={self.heartbeat_enabled}, interval={self.heartbeat_interval}m")
            except Exception as e:
                log.warning(f"Failed to load scheduler state: {e}")

    def _save_state(self):
        """Persist heartbeat state to disk."""
        try:
            self._state_path.write_text(json.dumps({
                "heartbeat_enabled": self.heartbeat_enabled,
                "heartbeat_interval": self.heartbeat_interval,
            }, indent=2))
        except Exception as e:
            log.warning(f"Failed to save scheduler state: {e}")

    # ── Public API ───────────────────────────────────────────────

    def set_bot(self, bot):
        """Set the Discord bot reference (called after bot creation)."""
        self.bot = bot

    def get_status(self) -> dict:
        """Return current scheduler status for the Tower UI."""
        now_cet = datetime.now(CET)
        return {
            "heartbeat_enabled": self.heartbeat_enabled,
            "heartbeat_interval": self.heartbeat_interval,
            "valid_intervals": VALID_INTERVALS,
            "morning_time": "09:10 CET (workdays)",
            "goodnight_time": "21:00 CET (daily)",
            "server_time_cet": now_cet.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "is_workday": now_cet.weekday() < 5,
        }

    def set_heartbeat(self, enabled: bool, interval: int | None = None):
        """Enable/disable heartbeat, optionally change interval. Thread-safe.

        DISABLING: does NOT cancel the in-flight task. This matters because the
        agent may call this endpoint from inside her own heartbeat tick (e.g.
        "narration complete → disable myself"). Cancelling would kill the
        in-progress agent.respond() and the final message would never reach
        Discord. Instead we just flip the flag; the loop's `while enabled`
        check exits on the next iteration.

        ENABLING / changing interval: cancel old task, start a new one.
        """
        if interval is not None and interval in VALID_INTERVALS:
            self.heartbeat_interval = interval

        self.heartbeat_enabled = enabled
        self._save_state()

        if not enabled:
            log.info("Heartbeat DISABLED (in-flight tick, if any, will complete and deliver)")
            return

        # Enabling or re-enabling: cancel stale task, start fresh
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._loop and self._loop.is_running():
            # Called from a non-async thread (e.g. Flask) — schedule onto the main loop
            future = asyncio.run_coroutine_threadsafe(
                self._heartbeat_loop(), self._loop
            )
            # We keep the concurrent.futures.Future; cancel() works on it too
            self._heartbeat_task = future
            log.info(f"Heartbeat ENABLED (every {self.heartbeat_interval}m) [cross-thread]")
        else:
            # Called from within the event loop (e.g. from start() or goodnight)
            try:
                loop = asyncio.get_running_loop()
                self._heartbeat_task = loop.create_task(self._heartbeat_loop())
                log.info(f"Heartbeat ENABLED (every {self.heartbeat_interval}m)")
            except RuntimeError:
                log.warning("Heartbeat requested but no event loop available")

    def rest(self):
        """REST command — disable heartbeat. Called verbally or at goodnight."""
        self.set_heartbeat(enabled=False)
        log.info("Galadriel is at REST. Heartbeat disabled.")

    # ── Start all cron loops ─────────────────────────────────────

    def start(self):
        """Start all scheduler loops. Call once from the async event loop."""
        log.info("Scheduler starting...")

        # Capture the running event loop so Flask threads can schedule onto it
        self._loop = asyncio.get_event_loop()

        # Always start morning + goodnight watchers
        self._morning_task = asyncio.ensure_future(self._cron_loop(
            name="morning",
            target_time=MORNING_TIME,
            callback=self._morning_routine,
            workday_only=True,
        ))
        self._goodnight_task = asyncio.ensure_future(self._cron_loop(
            name="goodnight",
            target_time=GOODNIGHT_TIME,
            callback=self._goodnight_routine,
            workday_only=False,
        ))

        # Start heartbeat if it was enabled (persisted state)
        if self.heartbeat_enabled:
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())
            log.info(f"Heartbeat resumed from saved state (every {self.heartbeat_interval}m)")

        log.info("Scheduler running.")

    # ── Heartbeat Loop ───────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Periodic heartbeat — self-initiated check-in."""
        try:
            while self.heartbeat_enabled:
                await asyncio.sleep(self.heartbeat_interval * 60)
                if not self.heartbeat_enabled:
                    break

                log.info("Heartbeat firing...")
                await self._send_agent_message(
                    prompt=(
                        "[SYSTEM:HEARTBEAT] This is your periodic heartbeat. "
                        "You may check in, share an observation, "
                        "note something interesting, or simply confirm you are watching. "
                        "Keep it brief and natural — do not repeat the same thing every time. "
                        "If nothing noteworthy, a short check-in is fine."
                    ),
                    channel_id="heartbeat",
                )
        except asyncio.CancelledError:
            log.info("Heartbeat loop cancelled.")
        except Exception as e:
            log.exception(f"Heartbeat loop error: {e}")

    # ── Cron Loop ────────────────────────────────────────────────

    async def _cron_loop(self, name: str, target_time: time, callback, workday_only: bool):
        """Generic cron-style loop that fires a callback once per day at target_time CET."""
        try:
            while True:
                now = datetime.now(CET)
                today_str = now.strftime("%Y-%m-%d")
                tracker = f"_last_{name}"

                # Build today's target datetime
                target_dt = now.replace(
                    hour=target_time.hour,
                    minute=target_time.minute,
                    second=0,
                    microsecond=0,
                )

                already_fired = getattr(self, tracker) == today_str

                if now >= target_dt or already_fired:
                    if not already_fired and now >= target_dt:
                        # We passed the time but haven't fired — fire now if conditions met
                        # Only if we're within 5 minutes of the target (avoid firing hours late)
                        diff = (now - target_dt).total_seconds()
                        if diff < 300:  # 5 min grace
                            if not (workday_only and now.weekday() >= 5):
                                log.info(f"Cron [{name}]: FIRING (within grace period)")
                                setattr(self, tracker, today_str)
                                await callback()
                                continue
                        setattr(self, tracker, today_str)

                    # Sleep until next check (every 30s for precision)
                    await asyncio.sleep(30)
                    continue

                # We haven't fired today and target is in the future
                seconds_to_wait = (target_dt - now).total_seconds()
                log.info(f"Cron [{name}]: sleeping {seconds_to_wait:.0f}s until {target_time}")
                await asyncio.sleep(seconds_to_wait)

                # Re-check after sleep
                now = datetime.now(CET)
                today_str = now.strftime("%Y-%m-%d")

                if getattr(self, tracker) == today_str:
                    continue

                if workday_only and now.weekday() >= 5:
                    log.info(f"Cron [{name}]: skipping — weekend")
                    setattr(self, tracker, today_str)
                    continue

                log.info(f"Cron [{name}]: FIRING")
                setattr(self, tracker, today_str)
                await callback()

        except asyncio.CancelledError:
            log.info(f"Cron [{name}] loop cancelled.")
        except Exception as e:
            log.exception(f"Cron [{name}] loop error: {e}")

    # ── Routines ─────────────────────────────────────────────────

    async def _morning_routine(self):
        """Morning greeting — workday 09:10 CET."""
        log.info("Morning routine starting...")
        await self._send_agent_message(
            prompt=(
                "[SYSTEM:MORNING_ROUTINE] Good morning! It is a new workday. "
                "Please give a warm morning greeting. Then:\n"
                "1. Check for any calendar or planning items he may need to respond to today.\n"
                "2. Check our AWS coffers — run `aws ce get-cost-and-usage` for yesterday's costs "
                "and provide a brief summary of spend.\n"
                "3. Note anything else relevant from overnight.\n"
                "Keep it concise but thorough. This also serves as a healthcheck."
            ),
            channel_id="morning",
        )

    async def _goodnight_routine(self):
        """Goodnight — 21:00 CET, then REST."""
        log.info("Goodnight routine starting...")
        await self._send_agent_message(
            prompt=(
                "[SYSTEM:GOODNIGHT_ROUTINE] It is 21:00 CET. "
                "Wish the user a peaceful good night. "
                "Offer a brief reflection on the day if anything notable happened. "
                "After this message, you will enter REST — your heartbeat will be disabled until morning."
            ),
            channel_id="goodnight",
        )
        # Disable heartbeat
        self.rest()

    # ── Message Delivery ─────────────────────────────────────────

    async def _send_agent_message(self, prompt: str, channel_id: str):
        """Have the agent generate a response and send it to Discord.

        If the agent returns an empty response (Claude end_turn with no text —
        a legitimate "nothing to add" state), we log it and skip the Discord
        send. Heartbeats that have nothing to report stay silent rather than
        spamming a placeholder into the DM.
        """
        try:
            response = await self.agent.respond(prompt, channel_id=channel_id)
            if not response.strip():
                log.info(f"Scheduler [{channel_id}] silent tick — nothing to report, skipping send")
                return
            log.info(f"Scheduler [{channel_id}] response: {response[:100]}...")

            # Send to Discord
            await self._send_to_discord(response)

        except Exception as e:
            log.exception(f"Scheduler [{channel_id}] error: {e}")

    async def _send_to_discord(self, message: str):
        """Send a message to the authorized user via DM (or configured channel).

        Uses bot.get_dm_channel() which handles DM channel resolution
        correctly — DM channels aren't in the bot cache at startup,
        so we fall back to fetch_user() + create_dm().
        """
        if not self.bot:
            log.warning("No Discord bot available for scheduler message.")
            return

        # Use the DM-safe helper attached to the bot by discord_bot/bot.py
        if hasattr(self.bot, 'get_dm_channel'):
            channel = await self.bot.get_dm_channel()
        else:
            # Fallback if bot doesn't have the helper (shouldn't happen)
            import os
            channel_id = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
            channel = self.bot.get_channel(channel_id) if channel_id else None

        if not channel:
            log.warning("Could not resolve Discord channel for scheduler message.")
            return

        # Chunk long messages
        max_len = 1900
        chunks = []
        text = message
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        for chunk in chunks:
            await channel.send(chunk)
            log.info(f"Scheduler message sent ({len(chunk)} chars) to channel {channel.id}")
