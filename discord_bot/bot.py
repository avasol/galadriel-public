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


# ─── Status report — pricing + formatter ────────────────────────────
#
# Prices in USD per million tokens, current Anthropic public pricing.
# Keep keys matched to the AGENT_MODEL values actually in use.
MODEL_PRICING_USD_PER_MTOK = {
    # Opus family
    "claude-opus-4-7": {"input": 15.00, "cache_read": 1.50, "cache_write": 18.75, "output": 75.00},
    "claude-opus-4-6": {"input": 15.00, "cache_read": 1.50, "cache_write": 18.75, "output": 75.00},
    "claude-opus-4-5": {"input": 15.00, "cache_read": 1.50, "cache_write": 18.75, "output": 75.00},
    # Sonnet family
    "claude-sonnet-4-6": {"input": 3.00, "cache_read": 0.30, "cache_write": 3.75, "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00, "cache_read": 0.30, "cache_write": 3.75, "output": 15.00},
    "claude-sonnet-4":   {"input": 3.00, "cache_read": 0.30, "cache_write": 3.75, "output": 15.00},
    # Haiku family
    "claude-haiku-4-5":  {"input": 0.80, "cache_read": 0.08, "cache_write": 1.00, "output": 4.00},
}


def _price_call(usage: dict, model: str) -> tuple[float, float, float]:
    """Return (actual_cost_usd, hypothetical_no_cache_cost_usd, savings_pct)
    for a single API call's usage dict {input, cache_read, cache_write, output}.

    Falls back to Sonnet pricing if the model is unknown — still gives a
    usable estimate rather than failing. Returns (0, 0, 0) on malformed usage.
    """
    prices = MODEL_PRICING_USD_PER_MTOK.get(model)
    if prices is None:
        # Try prefix matches (e.g. claude-sonnet-4-6-20250929)
        for k, v in MODEL_PRICING_USD_PER_MTOK.items():
            if model.startswith(k):
                prices = v
                break
    if prices is None:
        prices = MODEL_PRICING_USD_PER_MTOK["claude-sonnet-4-6"]

    try:
        inp = usage.get("input", 0) or 0
        cr = usage.get("cache_read", 0) or 0
        cw = usage.get("cache_write", 0) or 0
        out = usage.get("output", 0) or 0
    except AttributeError:
        return (0.0, 0.0, 0.0)

    actual = (
        inp * prices["input"]
        + cr * prices["cache_read"]
        + cw * prices["cache_write"]
        + out * prices["output"]
    ) / 1_000_000
    no_cache = ((inp + cr + cw) * prices["input"] + out * prices["output"]) / 1_000_000
    savings_pct = (1 - actual / no_cache) * 100 if no_cache > 0 else 0.0
    return (actual, no_cache, savings_pct)


def _progress_bar(pct: float, width: int = 20) -> str:
    """20-char Unicode block progress bar with tier emoji."""
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100))
    bar = "█" * filled + "░" * (width - filled)
    if pct >= 90:
        tier = "🔴"
    elif pct >= 75:
        tier = "🟡"
    else:
        tier = "🟢"
    return f"{tier} `[{bar}]` **{pct:.0f}%**"


def _format_status_report(agent, scheduler) -> str:
    """Build the `/status` + `!status` report.

    Sections:
      - Model + per-model cache minimum + context window
      - Context utilization bar (from last call's total input)
      - Cache efficiency + cost + savings
      - Active channels (top 5 by message count)
      - Output-ceiling streak state (if non-zero)
      - Post-recovery advisory (if any channel has a pending tag)
      - Scheduler (if wired)
    """
    lines: list[str] = []
    lines.append("🧝‍♀️ **Galadriel Harness Status**")
    lines.append(f"**Model:** `{agent.model}` · context window **{agent.context_window:,}** tok · max_output **{agent.max_tokens:,}** tok")
    lines.append("")

    # ── Context window (from last call) ──
    if agent.last_usage:
        u = agent.last_usage
        total_in = (u.get("input", 0) or 0) + (u.get("cache_read", 0) or 0) + (u.get("cache_write", 0) or 0)
        pct = 100 * total_in / agent.context_window if agent.context_window > 0 else 0
        lines.append("**Context (last call)**")
        lines.append(_progress_bar(pct))
        lines.append(f"`{total_in:>7,}` / `{agent.context_window:,}` tokens")
        lines.append("")

        # ── Cache efficiency + cost ──
        cacheable_in = total_in
        cache_read = u.get("cache_read", 0) or 0
        hit_pct = 100 * cache_read / cacheable_in if cacheable_in > 0 else 0
        actual, no_cache, savings_pct = _price_call(u, agent.model)
        lines.append("**Caching (last call)**")
        lines.append(
            f"in=`{u.get('input', 0)}`  cache_read=`{cache_read:,}`  "
            f"cache_write=`{u.get('cache_write', 0):,}`  out=`{u.get('output', 0)}`"
        )
        lines.append(
            f"Hit ratio: **{hit_pct:.1f}%** · "
            f"Cost: **${actual:.4f}**  (vs **${no_cache:.4f}** uncached = **{savings_pct:.0f}% saved**)"
        )
        lines.append("")
    else:
        lines.append("**Context:** *(no API calls yet this session)*")
        lines.append("")

    # ── Channels ──
    channels = agent.conversations
    total_msgs = sum(len(m) for m in channels.values())
    lines.append(f"**Channels** ({len(channels)} active · {total_msgs} msgs total)")
    if channels:
        sorted_ch = sorted(channels.items(), key=lambda kv: -len(kv[1]))
        for cid, msgs in sorted_ch[:5]:
            label = cid if len(cid) <= 20 else cid[:6] + "…" + cid[-4:]
            lines.append(f"• `{label}` — {len(msgs)} msgs")
        if len(sorted_ch) > 5:
            remaining = sum(len(m) for _, m in sorted_ch[5:])
            lines.append(f"• *+{len(sorted_ch) - 5} more channels, {remaining} msgs*")
    else:
        lines.append("*(none)*")
    lines.append("")

    # ── Output-ceiling streak ──
    streaks = getattr(agent, "_output_ceiling_streak", {}) or {}
    active_streaks = {cid: n for cid, n in streaks.items() if n > 0}
    if active_streaks:
        parts = [f"`{cid[:20]}`: {n}/2" for cid, n in active_streaks.items()]
        lines.append(f"**Output ceiling:** 🟡 near-ceiling streak — {', '.join(parts)}")
    else:
        lines.append("**Output ceiling:** ✅ healthy")

    # ── Post-recovery advisory ──
    recovery_tags = getattr(agent, "_post_recovery_archive_tag", {}) or {}
    if recovery_tags:
        tags_str = ", ".join(f"`{t}`" for t in recovery_tags.values())
        lines.append(f"**Post-recovery advisory:** ⚠️ active — archive tags: {tags_str}")
    lines.append("")

    # ── Scheduler ──
    if scheduler:
        s = scheduler.get_status()
        hb_emoji = "🟢 ON" if s["heartbeat_enabled"] else "🔴 OFF"
        custom = " · custom prompt" if s.get("heartbeat_prompt") else ""
        lines.append("**Scheduler**")
        lines.append(f"Heartbeat: {hb_emoji} (every {s['heartbeat_interval']}m{custom})")
        lines.append(f"Morning: {s['morning_time']} · Goodnight: {s['goodnight_time']}")
        lines.append(f"Now: {s['server_time_cet']} · {'Workday' if s['is_workday'] else 'Weekend'}")

    return "\n".join(lines)


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

    async def context_warning_callback(channel_id: str, message: str):
        """Deliver a context-utilization nudge to the relevant Discord channel.

        channel_id comes from the agent: a numeric string for real Discord
        channels, or a synthetic label ("heartbeat", "morning", "goodnight",
        "default") for scheduler-driven conversations. Numeric goes to that
        channel; everything else falls back to the authorized-user DM so the
        nudge still lands somewhere useful.
        """
        channel = None
        if channel_id.isdigit():
            channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await get_dm_channel()
        if channel is None:
            log.warning(f"Could not resolve channel for context warning (channel_id={channel_id})")
            return
        try:
            await channel.send(message)
            log.info(f"Context warning delivered to channel {channel.id}")
        except Exception as e:
            log.warning(f"Could not send context warning: {e}")

    agent.context_warning_callback = context_warning_callback

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
        """Clear conversation history for this channel (archives to palace first)."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return
        archived = await agent.pop_and_archive_history(str(ctx.channel.id))
        suffix = f" ({archived} msgs filed to palace)" if archived else ""
        await ctx.reply(f"🧹 Conversation history cleared.{suffix}")

    @bot.command(name="status")
    async def status_cmd(ctx: commands.Context):
        """Show agent status."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return
        await ctx.reply(_format_status_report(agent, scheduler))

    @bot.command(name="new")
    async def new_cmd(ctx: commands.Context):
        """Start a fresh conversation (archives to palace, then clears)."""
        if ctx.author.id != AUTHORIZED_USER_ID:
            return
        archived = await agent.pop_and_archive_history(str(ctx.channel.id))
        suffix = f" ({archived} msgs filed to palace)" if archived else ""
        await ctx.reply(f"✨ Fresh start. Blank slate.{suffix}")

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

    @bot.tree.command(name="new", description="Start a fresh conversation (archives history to palace, then clears)")
    async def slash_new(interaction: discord.Interaction):
        if interaction.user.id != AUTHORIZED_USER_ID:
            await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
            return
        # Defer because the palace mine can exceed Discord's 3s response window
        await interaction.response.defer()
        archived = await agent.pop_and_archive_history(str(interaction.channel_id))
        suffix = f" ({archived} msgs filed to palace)" if archived else ""
        await interaction.followup.send(f"✨ Fresh start. Blank slate.{suffix}")

    @bot.tree.command(name="status", description="Context utilization, cache efficiency, cost, channels, scheduler")
    async def slash_status(interaction: discord.Interaction):
        if interaction.user.id != AUTHORIZED_USER_ID:
            await interaction.response.send_message("I do not know you, stranger. 🛡️", ephemeral=True)
            return
        await interaction.response.send_message(_format_status_report(agent, scheduler))

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
