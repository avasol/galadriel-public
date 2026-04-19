"""Job Watcher — monitors completion markers from background jobs.

Background jobs (like narration pipelines) write JSON marker files to
/tmp/galadriel-jobs/ when they complete. This watcher polls for those
markers and triggers Discord notifications through the agent.

Architecture:
  - Marker dir: /tmp/galadriel-jobs/
  - Each job writes <job-name>.done with JSON status on completion
  - Watcher polls every 15 seconds
  - On detection: reads marker, formats message, sends via agent+Discord, archives marker
"""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger("galadriel.job_watcher")

MARKER_DIR = Path("/tmp/galadriel-jobs")
POLL_INTERVAL = 15  # seconds
MARKER_SUFFIX = ".done"


class JobWatcher:
    """Watches for job completion markers and notifies via Discord."""

    def __init__(self, agent, discord_bot=None):
        self.agent = agent
        self.bot = discord_bot
        self._task: asyncio.Task | None = None

    def set_bot(self, bot):
        self.bot = bot

    def start(self):
        """Start the watcher loop. Call from an async context."""
        MARKER_DIR.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.ensure_future(self._watch_loop())
        log.info(f"Job watcher started — monitoring {MARKER_DIR}")

    async def _watch_loop(self):
        """Main polling loop."""
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL)
                await self._check_markers()
        except asyncio.CancelledError:
            log.info("Job watcher cancelled.")
        except Exception as e:
            log.exception(f"Job watcher error: {e}")

    async def _check_markers(self):
        """Scan for .done marker files and process them."""
        if not MARKER_DIR.exists():
            return

        for marker_path in MARKER_DIR.glob(f"*{MARKER_SUFFIX}"):
            try:
                data = json.loads(marker_path.read_text())
                log.info(f"Job completion detected: {marker_path.name} — {data.get('status', 'UNKNOWN')}")

                # Format the notification
                message = self._format_notification(data)

                # Send through agent (so it gets logged and contextualized)
                await self._notify(data, message)

                # Archive the marker (move to .processed)
                archive_path = marker_path.with_suffix(".processed")
                marker_path.rename(archive_path)
                log.info(f"Marker archived: {archive_path}")

            except json.JSONDecodeError as e:
                log.warning(f"Invalid JSON in marker {marker_path}: {e}")
                # Move bad marker out of the way
                marker_path.rename(marker_path.with_suffix(".bad"))
            except Exception as e:
                log.exception(f"Error processing marker {marker_path}: {e}")

    def _format_notification(self, data: dict) -> str:
        """Format job completion data into a Discord-friendly message."""
        job = data.get("job", "unknown")
        status = data.get("status", "UNKNOWN")

        if status == "SUCCESS":
            success = data.get("success_count", "?")
            failed = data.get("failed_count", 0)
            elapsed = data.get("elapsed_human", "?")
            voice = data.get("voice", "?")
            engine = data.get("engine", "?")

            msg = (
                f"🎙️ **Narration Job Complete — {job}**\n\n"
                f"✅ **{success}** chunks narrated successfully\n"
            )
            if int(failed) > 0:
                msg += f"❌ **{failed}** chunks failed\n"
            msg += (
                f"⏱️ Duration: **{elapsed}**\n"
                f"🗣️ Voice: {voice} ({engine})\n"
                f"📋 Log: `{data.get('log_file', 'N/A')}`\n"
                f"🕐 Completed: {data.get('completed_at', 'N/A')}"
            )
            return msg

        elif status == "FAILED":
            return (
                f"🔴 **Narration Job FAILED — {job}**\n\n"
                f"Exit code: {data.get('exit_code', '?')}\n"
                f"⏱️ Duration: {data.get('elapsed_seconds', '?')}s\n"
                f"📋 Log: `{data.get('log_file', 'N/A')}`\n"
                f"🕐 Failed at: {data.get('completed_at', 'N/A')}"
            )
        else:
            return f"📦 **Job completed — {job}** — Status: {status}\n```json\n{json.dumps(data, indent=2)}\n```"

    async def _notify(self, data: dict, formatted_message: str):
        """Send notification via agent (gets intelligent commentary) then to Discord."""
        status = data.get("status", "UNKNOWN")
        job = data.get("job", "unknown")

        # Have the agent generate a contextual response
        prompt = (
            f"[SYSTEM:JOB_COMPLETE] A background job has finished.\n\n"
            f"Job: {job}\n"
            f"Status: {status}\n"
            f"Details:\n```json\n{json.dumps(data, indent=2)}\n```\n\n"
            f"Notify the user about this. Include the key stats. "
            f"If it succeeded, celebrate briefly and suggest next steps. "
            f"If it failed, analyze what might have gone wrong and suggest a fix. "
            f"Keep it concise."
        )

        try:
            response = await self.agent.respond(prompt, channel_id="job_watcher")
            await self._send_to_discord(response)
        except Exception as e:
            log.exception(f"Failed to generate agent response for job {job}: {e}")
            # Fallback: send the raw formatted message
            await self._send_to_discord(formatted_message)

    async def _send_to_discord(self, message: str):
        """Send message to Discord via the bot."""
        if not self.bot:
            log.warning("No Discord bot available for job notification.")
            return

        # Use the DM-safe helper
        if hasattr(self.bot, 'get_dm_channel'):
            channel = await self.bot.get_dm_channel()
        else:
            import os
            channel_id = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
            channel = self.bot.get_channel(channel_id) if channel_id else None

        if not channel:
            log.warning("Could not resolve Discord channel for job notification.")
            return

        # Chunk long messages
        max_len = 1900
        text = message
        while text:
            if len(text) <= max_len:
                await channel.send(text)
                break
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            await channel.send(text[:split_at])
            text = text[split_at:].lstrip("\n")

        log.info(f"Job notification sent to Discord ({len(message)} chars)")
