"""Discord gateway — relays messages between Discord and the GaladrielAgent."""

import os
import base64
import logging
import asyncio
import discord
from discord.ext import commands
from harness.agent import GaladrielAgent
from harness.compaction import compact_conversation
from harness.error_humanizer import humanize_anthropic_error

log = logging.getLogger("galadriel.discord")

AUTHORIZED_USER_ID = int(os.environ.get("DISCORD_AUTHORIZED_USER_ID", "0"))
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))

# Discord messages cap at 2000 chars
MAX_DISCORD_LENGTH = 1900

# Image handling
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB — Claude's per-image limit


def sniff_image_media_type(data: bytes) -> str | None:
    """Detect image media type from magic bytes. Discord's content_type is
    unreliable on iOS (reports PNG screenshots as image/jpeg), and Anthropic's
    API rejects mismatches with a 400."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def chunk_message(text: str) -> list[str]:
    """Split a long message into Discord-safe chunks."""
    if len(text) <= MAX_DISCORD_LENGTH:
        return [text]

    chunks = []
    while text:
        if len(text) <= MAX_DISCORD_LENGTH:
            chunks.append(text)
            break
        # Try to split at a newline
        split_at = text.rfind("\n", 0, MAX_DISCORD_LENGTH)
        if split_at == -1:
            split_at = MAX_DISCORD_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def create_bot(agent: GaladrielAgent, scheduler=None, job_watcher=None) -> commands.Bot:
    """Create and configure the Discord bot."""
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    # In-flight approval bubbles keyed by command text. Duplicate requests
    # for the same command (e.g. from max_tokens retries) attach to the
    # existing bubble rather than spawning another.
    pending_approvals: dict[str, "ApprovalView"] = {}

    async def get_dm_channel() -> discord.abc.Messageable | None:
        """Open a direct-message channel with the authorized user.

        Used for unsolicited messages: startup greeting, heartbeat,
        morning/goodnight routines, approval prompts. These should always
        be private — the guild channel (DISCORD_CHANNEL_ID) is for
        conversational replies to the user's posts, not for push messages.

        DM channels aren't in the bot's cache at startup, so we always
        resolve via fetch_user() + create_dm(). discord.py caches the
        result on the user object after the first call.
        """
        if not AUTHORIZED_USER_ID:
            return None
        try:
            user = await bot.fetch_user(AUTHORIZED_USER_ID)
            dm = await user.create_dm()
            return dm
        except Exception as e:
            log.warning(f"Could not open DM with user {AUTHORIZED_USER_ID}: {e}")
            return None

    class ApprovalView(discord.ui.View):
        """Button-based approval prompt. Replaces reaction-based approval to
        avoid the "1/1" counter artifact from the bot's own seed reactions,
        and to give a proper disabled state once resolved."""

        def __init__(self, command: str, future: asyncio.Future):
            super().__init__(timeout=30.0)
            self.command = command
            self.future = future
            self.message: discord.Message | None = None
            self.dedup_count = 0

        async def _resolve(self, interaction: discord.Interaction, approved: bool):
            if not self.future.done():
                self.future.set_result(approved)
            for child in self.children:
                child.disabled = True
            prefix = "✅ Approved" if approved else "❌ Denied"
            suffix = f" (merged {self.dedup_count + 1} requests)" if self.dedup_count else ""
            await interaction.response.edit_message(
                content=f"{prefix}{suffix}: `{self.command}`",
                view=self,
            )
            self.stop()

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
        async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != AUTHORIZED_USER_ID:
                await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
                return
            if self.future.done():
                await interaction.response.send_message("Already resolved.", ephemeral=True)
                return
            await self._resolve(interaction, True)

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != AUTHORIZED_USER_ID:
                await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
                return
            if self.future.done():
                await interaction.response.send_message("Already resolved.", ephemeral=True)
                return
            await self._resolve(interaction, False)

        async def on_timeout(self):
            if not self.future.done():
                self.future.set_result(False)
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(
                        content=f"⏰ Timed out (denied): `{self.command}`",
                        view=self,
                    )
                except Exception as e:
                    log.debug(f"Could not edit timed-out approval message: {e}")

    async def approval_callback(command: str, tier: str) -> bool:
        """Ask for approval via Discord buttons. Returns True if approved.

        Duplicate requests for the same command while a bubble is already
        in flight attach to the existing Future — the user sees one bubble,
        clicks once, and every caller gets the same answer.
        """
        existing = pending_approvals.get(command)
        if existing is not None and not existing.future.done():
            existing.dedup_count += 1
            log.info(f"Dedup approval ({existing.dedup_count + 1}× for same command): {command[:80]}")
            return await existing.future

        channel = await get_dm_channel()
        if not channel:
            return False

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        view = ApprovalView(command, future)

        msg = await channel.send(
            f"🔴 **Approval required**\n```\n{command}\n```\n"
            f"Click a button below. (30s → denied)",
            view=view,
        )
        view.message = msg
        pending_approvals[command] = view

        try:
            return await future
        finally:
            pending_approvals.pop(command, None)

    # Wire the approval callback into the agent
    agent.approval_callback = approval_callback

    # Expose get_dm_channel on the bot so the scheduler can use it
    bot.get_dm_channel = get_dm_channel

    async def safe_send(message: discord.Message, text: str):
        """Send a reply with fallback to channel.send if reply fails."""
        chunks = chunk_message(text)
        for chunk in chunks:
            try:
                await message.reply(chunk)
                log.info(f"✅ Reply sent ({len(chunk)} chars) to channel {message.channel.id}")
            except discord.HTTPException as e:
                log.warning(f"⚠️ message.reply() failed: {e} — falling back to channel.send()")
                try:
                    channel = bot.get_channel(message.channel.id)
                    if channel:
                        await channel.send(chunk)
                        log.info(f"✅ Fallback channel.send() succeeded ({len(chunk)} chars)")
                    else:
                        log.error(f"❌ Could not get channel {message.channel.id} for fallback")
                except Exception as e2:
                    log.error(f"❌ Fallback channel.send() also failed: {e2}")
            except Exception as e:
                log.error(f"❌ Unexpected error sending reply: {e}")

    @bot.event
    async def on_ready():
        log.info(f"Connected to Discord as {bot.user} (id: {bot.user.id})")

        # Start the scheduler once the event loop is running
        if scheduler:
            scheduler.start()
            log.info("Scheduler started from Discord on_ready.")

        # Start the job watcher once the event loop is running
        if job_watcher:
            job_watcher.start()
            log.info("Job watcher started from Discord on_ready.")

        # Register slash commands with Discord
        try:
            synced = await bot.tree.sync()
            log.info(f"Slash commands synced: {[c.name for c in synced]}")
        except Exception as e:
            log.warning(f"Slash command sync failed: {e}")

        # Send startup greeting (DM-safe)
        channel = await get_dm_channel()
        if channel:
            await channel.send("🧝‍♀️ Mae govannen. The harness is awake.")
            log.info(f"Startup greeting sent to channel {channel.id}")
        else:
            log.warning("Could not send startup greeting — no channel resolved.")

    @bot.event
    async def on_message(message: discord.Message):
        # Ignore own messages
        if message.author.id == bot.user.id:
            return

        # Security: only respond to authorized user
        if message.author.id != AUTHORIZED_USER_ID:
            if bot.user.mentioned_in(message):
                await message.reply("I do not know you, stranger. 🛡️")
            return

        # Only respond if mentioned or in DM or in the configured channel
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = bot.user.mentioned_in(message)
        is_target_channel = message.channel.id == CHANNEL_ID

        if not (is_dm or is_mentioned or is_target_channel):
            return

        # Strip the bot mention from the message
        content = message.content
        if bot.user:
            content = content.replace(f"<@{bot.user.id}>", "").strip()

        if not content:
            return

        # REST command: text-only, no attachments
        content_lower = content.lower().strip()
        if not message.attachments and content_lower in ("rest", "rest.", "rest!"):
            if scheduler:
                scheduler.rest()
            async with message.channel.typing():
                try:
                    channel_id = str(message.channel.id)
                    response = await agent.respond(
                        "[SYSTEM:REST_COMMAND] REST command received. "
                        "Your heartbeat has been disabled. Acknowledge gracefully "
                        "and keep it brief.",
                        channel_id=channel_id,
                    )
                    await safe_send(message, response or "🌙 *(resting — no words needed)*")
                except Exception as e:
                    log.exception("Error processing REST command")
                    await safe_send(message, humanize_anthropic_error(e) or f"⚠️ Something went wrong: `{e}`")
            return

        # Build content blocks: text + any image attachments
        content_blocks = []
        if content:
            content_blocks.append({"type": "text", "text": content})

        skipped = []
        for attachment in message.attachments:
            ct = (attachment.content_type or "").split(";")[0].strip().lower()
            if ct in SUPPORTED_IMAGE_TYPES:
                if attachment.size > MAX_IMAGE_BYTES:
                    skipped.append(
                        f"`{attachment.filename}` "
                        f"({attachment.size // (1024 * 1024)}MB — 5MB limit)"
                    )
                    continue
                try:
                    image_bytes = await attachment.read()
                    sniffed = sniff_image_media_type(image_bytes)
                    if sniffed is None:
                        skipped.append(f"`{attachment.filename}` (unrecognized image format)")
                        continue
                    if sniffed != ct:
                        log.info(f"📎 Media type corrected: {ct} → {sniffed} ({attachment.filename})")
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    content_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": sniffed, "data": b64},
                    })
                    log.info(f"📎 Image attached: {attachment.filename} ({attachment.size} bytes, {sniffed})")
                except Exception as e:
                    log.warning(f"Failed to read attachment {attachment.filename}: {e}")
                    skipped.append(f"`{attachment.filename}` (download error)")
            elif ct in ("image/heic", "image/heif"):
                skipped.append(
                    f"`{attachment.filename}` (HEIC/HEIF — convert to JPEG or PNG first)"
                )

        if skipped:
            await safe_send(message, f"⚠️ Skipped attachment(s): {', '.join(skipped)}")

        if not content_blocks:
            return

        # Flatten to string for text-only messages (preserves existing behaviour)
        user_input = (
            content_blocks[0]["text"]
            if len(content_blocks) == 1 and content_blocks[0]["type"] == "text"
            else content_blocks
        )

        # Show typing indicator while processing
        log.info(f"📥 Processing message from {message.author} in {message.channel.id}: {content[:80]}")
        async with message.channel.typing():
            try:
                channel_id = str(message.channel.id)
                response = await agent.respond(user_input, channel_id=channel_id)
                log.info(f"📤 Agent response ready ({len(response)} chars), sending to Discord...")
                if not response.strip():
                    log.info("Agent returned empty response — substituting placeholder")
                    response = "🌙 *(nothing to add — acknowledged.)*"
                await safe_send(message, response)

            except Exception as e:
                log.exception("Error processing message")
                await safe_send(message, humanize_anthropic_error(e) or f"⚠️ Something went wrong: `{e}`")

    @bot.command(name="clear")
    async def clear_cmd(ctx: commands.Context):
        """Clear conversation history for this channel."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return
        agent.clear_history(str(ctx.channel.id))
        await ctx.reply("🧹 Conversation history cleared.")

    @bot.command(name="status")
    async def status_cmd(ctx: commands.Context):
        """Show agent status."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return
        channels = len(agent.conversations)
        total_msgs = sum(len(m) for m in agent.conversations.values())

        sched_info = ""
        if scheduler:
            s = scheduler.get_status()
            hb = "🟢 ON" if s["heartbeat_enabled"] else "🔴 OFF"
            sched_info = (
                f"\n**Heartbeat:** {hb} (every {s['heartbeat_interval']}m)\n"
                f"**Morning:** {s['morning_time']}\n"
                f"**Goodnight:** {s['goodnight_time']}\n"
                f"**CET Time:** {s['server_time_cet']}\n"
                f"**Workday:** {'Yes' if s['is_workday'] else 'No'}"
            )

        await ctx.reply(
            f"🧝‍♀️ **Galadriel Harness Status**\n"
            f"Model: `{agent.model}`\n"
            f"Active channels: {channels}\n"
            f"Messages in memory: {total_msgs}\n"
            f"{sched_info}"
        )

    @bot.command(name="new")
    async def new_cmd(ctx: commands.Context):
        """Start a fresh conversation (clear history for this channel)."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return
        agent.clear_history(str(ctx.channel.id))
        await ctx.reply("✨ Fresh start. History cleared, blank slate.")

    @bot.command(name="compact")
    async def compact_cmd(ctx: commands.Context):
        """Compress conversation history by summarizing old tool results."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return

        channel_id = str(ctx.channel.id)
        messages = agent._get_messages(channel_id)

        async with ctx.channel.typing():
            try:
                result = await compact_conversation(messages, api_key=os.environ.get("ANTHROPIC_API_KEY"))
                imgs = result.get("images_removed", 0)
                if result["summaries_created"] == 0 and imgs == 0:
                    await ctx.reply(f"📚 {len(messages)} messages — nothing to compact.")
                    return
                agent.conversations[channel_id] = result["compacted_messages"]

                ratio_pct = int((1 - result["compression_ratio"]) * 100)
                img_line = f"\nImages: {imgs} stripped" if imgs else ""
                await ctx.reply(
                    f"🗜️ **Compacted**\n"
                    f"Messages: {len(messages)} → {len(result['compacted_messages'])}\n"
                    f"Tokens: {result['tokens_before']} → {result['tokens_after']} (~{ratio_pct}% reduction)\n"
                    f"Summaries: {result['summaries_created']} tool results compressed"
                    f"{img_line}"
                )
                log.info(
                    f"Compaction complete: {len(messages)} msgs, "
                    f"{result['summaries_created']} summaries, "
                    f"{ratio_pct}% token reduction"
                )
            except Exception as e:
                log.exception("Error during compaction")
                await ctx.reply(humanize_anthropic_error(e) or f"⚠️ Compaction failed: `{e}`")

    # ── Slash Commands ───────────────────────────────────────────

    @bot.tree.command(name="new", description="Start a fresh conversation (clears this channel's history)")
    async def slash_new(interaction: discord.Interaction):
        if interaction.user.id != AUTHORIZED_USER_ID:
            await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
            return
        agent.clear_history(str(interaction.channel_id))
        await interaction.response.send_message("✨ Fresh start. History cleared, blank slate.")

    @bot.tree.command(name="status", description="Show harness status, model info, and last API token usage")
    async def slash_status(interaction: discord.Interaction):
        if interaction.user.id != AUTHORIZED_USER_ID:
            await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
            return

        channels = len(agent.conversations)
        total_msgs = sum(len(m) for m in agent.conversations.values())

        usage_info = ""
        if agent.last_usage:
            u = agent.last_usage
            usage_info = (
                f"\n**Last call tokens:** "
                f"input={u['input']} cache_read={u['cache_read']} "
                f"cache_write={u['cache_write']} output={u['output']}"
            )

        sched_info = ""
        if scheduler:
            s = scheduler.get_status()
            hb = "🟢 ON" if s["heartbeat_enabled"] else "🔴 OFF"
            sched_info = (
                f"\n**Heartbeat:** {hb} (every {s['heartbeat_interval']}m)\n"
                f"**Morning:** {s['morning_time']}\n"
                f"**Goodnight:** {s['goodnight_time']}\n"
                f"**CET Time:** {s['server_time_cet']}\n"
                f"**Workday:** {'Yes' if s['is_workday'] else 'No'}"
            )

        await interaction.response.send_message(
            f"🧝‍♀️ **Galadriel Harness Status**\n"
            f"Model: `{agent.model}`\n"
            f"Active channels: {channels}\n"
            f"Messages in memory: {total_msgs}"
            f"{usage_info}"
            f"{sched_info}"
        )

    @bot.tree.command(name="compact", description="Compress conversation history using Haiku (reduces token usage)")
    async def slash_compact(interaction: discord.Interaction):
        if interaction.user.id != AUTHORIZED_USER_ID:
            await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
            return

        channel_id = str(interaction.channel_id)
        messages = agent._get_messages(channel_id)

        await interaction.response.defer()
        try:
            result = await compact_conversation(messages, api_key=os.environ.get("ANTHROPIC_API_KEY"))
            imgs = result.get("images_removed", 0)
            if result["summaries_created"] == 0 and imgs == 0:
                await interaction.followup.send(f"📚 {len(messages)} messages — nothing to compact.")
                return
            agent.conversations[channel_id] = result["compacted_messages"]

            ratio_pct = int((1 - result["compression_ratio"]) * 100)
            img_line = f"\nImages: {imgs} stripped" if imgs else ""
            await interaction.followup.send(
                f"🗜️ **Compacted**\n"
                f"Messages: {len(messages)} → {len(result['compacted_messages'])}\n"
                f"Tokens: {result['tokens_before']} → {result['tokens_after']} (~{ratio_pct}% reduction)\n"
                f"Summaries: {result['summaries_created']} tool results compressed"
                f"{img_line}"
            )
            log.info(
                f"Compaction complete: {len(messages)} msgs, "
                f"{result['summaries_created']} summaries, "
                f"{ratio_pct}% token reduction"
            )
        except Exception as e:
            log.exception("Error during compaction")
            await interaction.followup.send(humanize_anthropic_error(e) or f"⚠️ Compaction failed: `{e}`")

    return bot
