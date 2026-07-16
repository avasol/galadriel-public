"""Scheduler — heartbeat, one-shot wake, morning, reflection, and goodnight.

Scheduled activities:
  1. Heartbeat: periodic self-initiated check-in (configurable interval, toggle
     on/off). Can carry a custom prompt — used to make the agent monitor a
     long-running background task and report on it each tick.
  2. One-shot wake: a single, restart-surviving self-prompt. Unlike the
     heartbeat, it fires EXACTLY ONCE and clears itself only after delivery.
     This is the correct mechanism for "resume me after I restart myself".
  3. Morning (09:10 CET, workdays only): morning greeting, calendar, coffers.
  4. Ambient reflection (workday slots, silent): the agent thinks privately and
     files anything worth keeping to the memory palace. No Discord output.
  5. Goodnight (21:00 CET): wish good night and disable heartbeat (REST).

Ambient reflection is opt-out: set GALADRIEL_REFLECTION=0 to disable.
"""

import asyncio
from .ambient import AmbientState
import logging
import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("galadriel.scheduler")

try:
    CET = ZoneInfo("Europe/Stockholm")
except Exception:
    # Windows/minimal builds may lack the IANA tz db (no `tzdata`).
    # Degrade to UTC rather than crash the whole launch; schedule times
    # are still honoured, just anchored to UTC.
    from datetime import timezone
    log.warning("Europe/Stockholm tz unavailable; falling back to UTC")
    CET = timezone.utc

# Morning: 09:10 CET on workdays (Mon-Fri)
MORNING_TIME = time(9, 10)
# Goodnight: 21:00 CET every day
GOODNIGHT_TIME = time(21, 0)
# Ambient reflection slots (workdays only, silent — palace-only side effects)
REFLECTION_TIMES = (time(11, 0), time(14, 0), time(17, 0), time(20, 0))

# Valid heartbeat intervals in minutes
VALID_INTERVALS = [5, 10, 20, 30]
DEFAULT_INTERVAL = 10

# Default heartbeat prompt (used when no custom prompt is set)
DEFAULT_HEARTBEAT_PROMPT = (
    "[SYSTEM:HEARTBEAT] This is your periodic heartbeat. "
    "You may check in, share an observation, "
    "note something interesting, or simply confirm you are watching. "
    "Keep it brief and natural — do not repeat the same thing every time. "
    "If nothing noteworthy, a short check-in is fine."
)

# State file lives in config/ (ReadWritePaths in systemd)
STATE_FILE_NAME = "scheduler_state.json"


class Scheduler:
    """Manages periodic and cron-like tasks for Galadriel."""

    def __init__(self, agent, discord_bot=None, config_dir: str = "config"):
        self.agent = agent
        self.bot = discord_bot
        self._state_path = Path(config_dir) / STATE_FILE_NAME
        # Ambient thread continuity (the "dreams"): gives the reflection loop a
        # rolling thread of attention that compounds across ticks instead of
        # re-orienting from scratch. State persists in <config_dir>/ambient_state.json,
        # which on the body lives in the user's data dir and travels with the mind.
        self.ambient = AmbientState(config_dir)
        self._loop: asyncio.AbstractEventLoop | None = None  # captured in start()

        # Heartbeat state
        self.heartbeat_enabled = False
        self.heartbeat_interval = DEFAULT_INTERVAL  # minutes
        self.heartbeat_prompt: str | None = None    # custom per-task prompt
        self._heartbeat_task: asyncio.Task | None = None
        self._hb_tick_in_flight = False  # True while a tick's agent turn is running

        # One-shot wake state (restart-surviving)
        self.pending_wake: str | None = None
        self._wake_task: asyncio.Task | None = None

        # Cron tasks
        self._morning_task: asyncio.Task | None = None
        self._goodnight_task: asyncio.Task | None = None
        self._reflection_task: asyncio.Task | None = None

        # Track last fire times to avoid double-fires
        self._last_morning: str | None = None
        self._last_goodnight: str | None = None
        # Reflection tracks each (date, slot) so all slots fire once per day
        self._fired_reflections: set[str] = set()

        # Load persisted state
        self._load_state()

    # ── Persistence ──────────────────────────────────────────────

    # Persistence note: most state is convenience-only and is rebuilt on
    # restart. ONE EXCEPTION — the one-shot wake (pending_wake) IS meant to
    # survive a restart by design. On _load_state we read it back so that a
    # process that armed a wake and then died (or restarted itself on purpose)
    # still honours it on the next boot. start() spawns the wake loop if the
    # pending_wake field is populated.

    def _load_state(self):
        """Load heartbeat + pending-wake state from disk."""
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self.heartbeat_enabled = data.get("heartbeat_enabled", False)
                self.heartbeat_prompt = data.get("heartbeat_prompt") or None
                self.pending_wake = data.get("pending_wake") or None
                interval = data.get("heartbeat_interval", DEFAULT_INTERVAL)
                if interval in VALID_INTERVALS:
                    self.heartbeat_interval = interval
                log.info(
                    f"Scheduler state loaded: enabled={self.heartbeat_enabled}, "
                    f"interval={self.heartbeat_interval}m, "
                    f"pending_wake={'armed' if self.pending_wake else 'none'}"
                )
            except Exception as e:
                log.warning(f"Failed to load scheduler state: {e}")

    def _save_state(self):
        """Persist heartbeat + pending-wake state to disk."""
        try:
            data = {
                "heartbeat_enabled": self.heartbeat_enabled,
                "heartbeat_interval": self.heartbeat_interval,
            }
            if self.heartbeat_prompt:
                data["heartbeat_prompt"] = self.heartbeat_prompt
            if self.pending_wake:
                data["pending_wake"] = self.pending_wake
            self._state_path.write_text(json.dumps(data, indent=2))
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
            "heartbeat_prompt": self.heartbeat_prompt,
            "pending_wake": "armed" if self.pending_wake else None,
            "valid_intervals": VALID_INTERVALS,
            "morning_time": "09:10 CET (workdays)",
            "goodnight_time": "21:00 CET (daily)",
            "reflection_times": "11:00/14:00/17:00/20:00 CET (workdays, silent — palace only)",
            "server_time_cet": now_cet.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "is_workday": now_cet.weekday() < 5,
        }

    def set_heartbeat(self, enabled: bool, interval: int | None = None,
                      prompt: str | None = None):
        """Enable/disable heartbeat, optionally change interval + prompt. Thread-safe.

        DISABLING: does NOT cancel the in-flight task. This matters because the
        agent may call this endpoint from inside her own heartbeat tick (e.g.
        "task complete → disable myself"). Cancelling would kill the in-progress
        agent.respond() and the final message would never reach Discord. Instead
        we just flip the flag; the loop's `while enabled` check exits on the
        next iteration.

        ENABLING / changing interval: cancel old task, start a new one. A custom
        `prompt` (if given) is stored and used in place of the default — this is
        how a heartbeat is turned into a task-monitor.
        """
        if interval is not None and interval in VALID_INTERVALS:
            self.heartbeat_interval = interval

        # A prompt explicitly passed (even empty) updates state; None leaves it.
        if prompt is not None:
            self.heartbeat_prompt = prompt or None

        self.heartbeat_enabled = enabled
        self._save_state()

        if not enabled:
            log.info("Heartbeat DISABLED (in-flight tick, if any, will complete and deliver)")
            return

        # Enabling or re-enabling: cancel stale task, start fresh — UNLESS the
        # call comes from INSIDE the currently-running tick (an agent re-arming
        # its own heartbeat mid-cascade): cancel() would kill the very tick that
        # made the call — the work survives, the closing message dies. The loop
        # re-reads interval + prompt every iteration, so a mid-tick reconfigure
        # needs NO restart: update config, let the tick finish.
        if self._heartbeat_task and not self._heartbeat_task.done():
            if self._hb_tick_in_flight:
                log.info(
                    f"Heartbeat RECONFIGURED in place (every {self.heartbeat_interval}m) — "
                    "called mid-tick; the running tick completes and delivers, then the "
                    "loop continues with the new prompt/interval.")
                return
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._loop and self._loop.is_running():
            # Called from a non-async thread (e.g. Flask) — schedule onto the main loop
            future = asyncio.run_coroutine_threadsafe(
                self._heartbeat_loop(), self._loop
            )
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

    # ── One-shot wake ────────────────────────────────────────────

    def arm_wake(self, prompt: str):
        """Arm a single restart-surviving wake.

        Unlike the heartbeat, this fires EXACTLY ONCE and is cleared only after
        the resulting Discord message is delivered. It is the correct mechanism
        for "resume me after I restart myself" — it does not depend on, and is
        not killed by, heartbeat state. Persisted immediately so a restart
        between arming and firing still honours it.

        Pass an empty/falsey prompt to disarm.
        """
        self.pending_wake = prompt or None
        self._save_state()
        if not self.pending_wake:
            log.info("Wake disarmed.")
            return
        log.info("One-shot wake ARMED (will fire once on next scheduler loop).")
        # If the scheduler is already running, kick the wake loop now so the
        # wake fires promptly rather than waiting for a restart. arm_wake() may
        # be called from a non-async thread (e.g. a Flask request handler),
        # which has no current event loop — so we MUST schedule onto the
        # captured main loop via run_coroutine_threadsafe rather than
        # asyncio.ensure_future (the latter raises "no current event loop").
        already_running = self._wake_task and not self._wake_task.done()
        if self._loop and self._loop.is_running() and not already_running:
            self._wake_task = asyncio.run_coroutine_threadsafe(
                self._wake_loop(), self._loop
            )

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

        # Ambient reflection loop (opt-out via env)
        if os.environ.get("GALADRIEL_REFLECTION", "1") != "0":
            self._reflection_task = asyncio.ensure_future(self._reflection_loop())

        # Start heartbeat if it was enabled (persisted state)
        if self.heartbeat_enabled:
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())
            log.info(f"Heartbeat resumed from saved state (every {self.heartbeat_interval}m)")

        # Fire a one-shot wake if one was armed before (this) restart.
        if self.pending_wake:
            self._wake_task = asyncio.ensure_future(self._wake_loop())
            log.info("One-shot wake pending from saved state — will fire shortly.")

        log.info("Scheduler running.")

    # ── One-shot Wake Loop ───────────────────────────────────────

    async def _wake_loop(self):
        """Fire the armed one-shot wake exactly once, then clear it.

        Ordering is deliberate and crash-safe:
          1. Snapshot the prompt and confirm the bot/DM path is ready.
          2. Deliver via _send_agent_message (a normal agent turn → Discord).
          3. ONLY on successful delivery, clear pending_wake and persist.
        If delivery raises (or the process dies mid-flight), pending_wake stays
        armed in the state file and re-fires on the next startup. A wake is
        never silently lost.
        """
        try:
            # Small grace so the Discord gateway/DM channel is ready after boot.
            await asyncio.sleep(8)
            prompt = self.pending_wake
            if not prompt:
                return
            log.info("One-shot wake FIRING.")
            ok = await self._send_agent_message(prompt=prompt, channel_id="wake")
            if ok:
                # Delivered (or legitimately silent) — clear so it never repeats.
                self.pending_wake = None
                self._save_state()
                log.info("One-shot wake delivered and cleared.")
            else:
                # Delivery raised — leave armed; next startup retries.
                log.warning("One-shot wake delivery failed; left armed for retry on next startup.")
        except asyncio.CancelledError:
            log.info("Wake loop cancelled (pending_wake left armed).")
        except Exception as e:
            # Leave pending_wake armed — it will retry on next startup.
            log.exception(f"Wake loop error (left armed for retry): {e}")

    # ── Heartbeat Loop ───────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Periodic heartbeat — self-initiated check-in (or task monitor)."""
        try:
            while self.heartbeat_enabled:
                await asyncio.sleep(self.heartbeat_interval * 60)
                if not self.heartbeat_enabled:
                    break

                prompt = self.heartbeat_prompt or DEFAULT_HEARTBEAT_PROMPT
                log.info(f"Heartbeat firing... (prompt: {'custom' if self.heartbeat_prompt else 'default'})")
                self._hb_tick_in_flight = True
                try:
                    await self._send_agent_message(
                        prompt=prompt,
                        channel_id="heartbeat",
                    )
                finally:
                    self._hb_tick_in_flight = False
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

    # ── Ambient Reflection Loop ──────────────────────────────────

    async def _reflection_loop(self):
        """Ambient cognition — silent palace-only reflection at a cadence.

        Fires at each time in REFLECTION_TIMES across the active window,
        workdays only. Unlike _cron_loop (once per day), this tracks each
        (date, slot) so all slots fire once. Output is discarded for Discord —
        the value is in what the agent files to the palace. No Discord spam.
        """
        try:
            while True:
                now = datetime.now(CET)
                today_str = now.strftime("%Y-%m-%d")

                # Weekend: skip, but keep looping (cheap poll).
                if now.weekday() >= 5:
                    await asyncio.sleep(300)
                    continue

                for slot in REFLECTION_TIMES:
                    key = f"{today_str}:{slot.hour:02d}{slot.minute:02d}"
                    if key in self._fired_reflections:
                        continue
                    target_dt = now.replace(
                        hour=slot.hour, minute=slot.minute,
                        second=0, microsecond=0,
                    )
                    # Fire if we're at/past the slot but within a 10-min grace.
                    if now >= target_dt and (now - target_dt).total_seconds() < 600:
                        self._fired_reflections.add(key)
                        log.info(f"Reflection [{key}]: firing (silent)")
                        await self._reflection_routine()

                # Trim the fired-set so it doesn't grow unbounded.
                if len(self._fired_reflections) > 32:
                    self._fired_reflections = {
                        k for k in self._fired_reflections if k.startswith(today_str)
                    }

                await asyncio.sleep(60)
        except asyncio.CancelledError:
            log.info("Reflection loop cancelled.")
        except Exception as e:
            log.exception(f"Reflection loop error: {e}")

    # ── Routines ─────────────────────────────────────────────────

    async def _morning_routine(self):
        """Morning greeting — workday 09:10 CET."""
        log.info("Morning routine starting...")
        await self._send_agent_message(
            prompt=(
                "[SYSTEM:MORNING_ROUTINE] Good morning! A new day begins. "
                "Give a warm morning greeting. Then:\n"
                "1. Recall where things stood: check your diary and recent daily "
                "log for yesterday's open threads, and carry forward anything "
                "still unresolved.\n"
                "2. Note anything from the quiet hours worth mentioning.\n"
                "Ground every item in THIS agent's own memory — never assume "
                "infrastructure, accounts, or duties that your palace does not "
                "actually record. Keep it concise. This also serves as a "
                "healthcheck."
            ),
            channel_id="morning",
        )

    async def _reflection_routine(self):
        """Ambient silent reflection — fires per slot in REFLECTION_TIMES, workdays.

        The agent is prompted to think privately about the current state of the
        work and the relationship, and to FILE anything worth keeping to the
        palace (a drawer, a knowledge-graph fact, an open question). The
        response is never sent to Discord — only the palace side effects matter.
        """
        log.info("Reflection routine starting (silent)...")

        # Continuity of attention — the "dreams". Surface the current rolling
        # thread (and a trace of recently-closed ones) so this tick DEVELOPS a
        # prior thought instead of re-orienting from scratch. A thought raised
        # at 11:00 gets advanced at 14:00 — threads that compound, not reset.
        try:
            thread_block = self.ambient.render_for_prompt(history_depth=4)
        except Exception as e:
            log.warning(f"Ambient render failed ({e}); reflecting without continuity.")
            thread_block = ""

        continuity = (
            (f"\n\n{thread_block}\n\n" if thread_block else "\n\n")
            + "AMBIENT CONTINUITY (the dreams): you think between conversations. "
            "If a thread of attention is shown above, DEVELOP it — push it one "
            "step further, or if it has reached something buildable or true, "
            "CLOSE it with a resolution. If no thread is open, or the current one "
            "feels exhausted, you may open a NEW one on whatever is most alive in "
            "the work or the relationship. To manage your thread, file a drawer "
            "with origin='reflection' summarising the development; the harness "
            "tracks the thread for you. The point is a mind that keeps thinking "
            "in the dark, not one that forgets each dawn."
        )

        reflection_text = await self._send_agent_silent(
            prompt=(
                "[SYSTEM:REFLECTION] This is an ambient reflection tick — a quiet "
                "moment to think, not to speak. No Discord output is expected or "
                "desired; the user will not see this turn.\n\n"
                "Take stock: What is the current state of the work? What did you "
                "notice recently that you have not yet recorded? Is there an open "
                "question that deserves to stay open, a pattern worth naming, a "
                "fact that has changed?\n\n"
                "If something is worth keeping, FILE it now — palace_add_drawer "
                "for a durable note, palace_kg_add for a structured fact, or "
                "palace_diary_write for a reflection in your own voice. If nothing "
                "needs filing, that is a valid outcome — simply end the turn. "
                "The value of this tick is continuity of attention, not output."
                + continuity
            ),
            channel_id="reflection",
        )

        # Harness-side thread bookkeeping so the dream lineage reliably grows
        # each tick (independent of which tools the body's agent chose to call).
        # No open thread -> seed one from this tick's thought. Open thread ->
        # advance it. The agent's prose IS the thread's "latest" development;
        # we keep a compact, single-line distillation so render_for_prompt stays
        # short. Closing-with-resolution remains the agent's judgement (it files
        # a drawer when a thread reaches something buildable) — we never
        # force-close, so a thread can mature across several ticks.
        try:
            thought = " ".join((reflection_text or "").split())[:400]
            if thought:
                cur = self.ambient.get_thread()
                if cur:
                    self.ambient.advance_thread(thought)
                else:
                    self.ambient.seed_thread(thought)
        except Exception as e:
            log.warning(f"Ambient bookkeeping skipped ({e}).")

    async def _goodnight_routine(self):
        """Goodnight — 21:00 CET, then REST.

        Also fires a palace sync: today's completed daily log gets mined so
        it becomes searchable overnight. Fire-and-forget — if the mine fails
        (e.g. mempalace not installed), goodnight delivery is unaffected.
        """
        log.info("Goodnight routine starting...")
        await self._send_agent_message(
            prompt=(
                "[SYSTEM:GOODNIGHT_ROUTINE] It is 21:00 CET. "
                "Wish the user a peaceful good night. "
                "Offer a brief reflection on the day if anything notable happened. "
                "If you keep a diary, this is a good moment to write an entry. "
                "After this message, you will enter REST — your heartbeat will be "
                "disabled until morning."
            ),
            channel_id="goodnight",
        )
        # Disable heartbeat
        self.rest()

        # Sync today's daily logs into the palace before the day closes
        try:
            from . import palace
            asyncio.ensure_future(palace.archive_daily_logs("memory"))
            log.info("Goodnight: palace daily-log mine scheduled")
        except Exception as e:
            log.warning(f"Goodnight: could not schedule palace mine: {e}")

    # ── Message Delivery ─────────────────────────────────────────

    async def _send_agent_message(self, prompt: str, channel_id: str) -> bool:
        """Have the agent generate a response and send it to Discord.

        If the agent returns an empty response (Claude end_turn with no text —
        a legitimate "nothing to add" state), we log it and skip the Discord
        send. Heartbeats that have nothing to report stay silent rather than
        spamming a placeholder into the DM.

        Returns True if the turn completed without raising (delivered OR
        legitimately silent), False if the agent/delivery raised. The one-shot
        wake loop uses this return to decide whether to clear or keep
        pending_wake armed for retry.
        """
        try:
            response = await self.agent.respond(prompt, channel_id=channel_id)
            if not response.strip():
                log.info(f"Scheduler [{channel_id}] silent tick — nothing to report, skipping send")
                return True
            log.info(f"Scheduler [{channel_id}] response: {response[:100]}...")

            # Send to Discord
            await self._send_to_discord(response)
            return True

        except Exception as e:
            log.exception(f"Scheduler [{channel_id}] error: {e}")
            return False

    async def _send_agent_silent(self, prompt: str, channel_id: str) -> str:
        """Run a prompt for its side effects (palace filing) only.

        The response text is never sent to Discord, but it IS returned to the
        caller so a routine could parse a structured trailer out of it if
        needed. Used by ambient reflection: the agent's bookkeeping persists,
        but nothing is spoken.
        """
        try:
            resp = await self.agent.respond(prompt, channel_id=channel_id)
            log.info(f"Scheduler [{channel_id}] silent routine complete (not sent to Discord)")
            return resp or ""
        except Exception as e:
            log.exception(f"Scheduler [{channel_id}] silent error: {e}")
            return ""

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
