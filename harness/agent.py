"""Core agent — wraps the Anthropic API with tools, memory, and safety.

Prompt caching strategy (3 explicit breakpoints, see harness/memory.py for details):

  1. cache_control on the LAST tool definition
       → caches the `tools` prefix on its own.
       → survives changes to system[0] (unlikely but cheap insurance).

  2. cache_control on system[0] (the stable block built by MemoryManager)
       → caches tools + stable system as one prefix.
       → this is the big win: all of SOUL/IDENTITY/USER/MEMORY and any
         project .md files in config/ ride free reads after the first call.

  3. cache_control on the LAST block of the LAST message (injected per-call)
       → caches the growing message history, advancing as conversation grows.
       → captures the tool_use ↔ tool_result cascade within a turn.
       → implemented via _attach_trailing_cache_control() to avoid mutating
         stored conversation state.

Usage tokens are logged after every API call so you can verify caching is
actually engaging. You want cache_read to climb on the second API call within
a turn and stay high afterward.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from anthropic import AsyncAnthropic
from .memory import MemoryManager
from .tools import TOOL_DEFINITIONS, execute_tool
from .safety import classify_command, format_safety_notice

log = logging.getLogger("galadriel")


def _serialize_content(content):
    """Convert SDK ContentBlock objects to plain dicts for reliable serialization."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        serialized = []
        for block in content:
            if hasattr(block, "model_dump"):
                serialized.append(block.model_dump(exclude_none=True))
            elif isinstance(block, dict):
                serialized.append(block)
            else:
                serialized.append({"type": "text", "text": str(block)})
        return serialized
    if hasattr(content, "model_dump"):
        return content.model_dump(exclude_none=True)
    return str(content)


def _contains_tool_use(msg: dict) -> bool:
    """Check if an assistant message contains tool_use blocks."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "tool_use"
        for b in content
    )


def _contains_tool_result(msg: dict) -> bool:
    """Check if a user message contains tool_result blocks."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        for b in content
    )


def _build_cached_tools() -> list[dict]:
    """Attach cache_control to the last tool so the tools prefix gets cached.

    Cache breakpoints themselves are free — they only affect what gets
    hashed into a cache entry. Placing one on the last tool means the
    entire `tools` array forms its own cache prefix, which survives
    unchanged across every call (tools never change at runtime).
    """
    if not TOOL_DEFINITIONS:
        return []
    cached = [dict(t) for t in TOOL_DEFINITIONS]
    cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
    return cached


def _attach_trailing_cache_control(messages: list) -> list:
    """Return a shallow copy of `messages` with cache_control on the last block.

    The stored `messages` list is left untouched — we only attach cache_control
    to the version sent to the API. This way the persistent conversation
    history in self.conversations never contains cache_control markers
    (which would complicate serialization / history display).

    If the last message's content is:
      - a list of blocks: add cache_control to the last block (most common).
      - a plain string: wrap it in a text block with cache_control.
      - empty/malformed: return messages unchanged.
    """
    if not messages:
        return messages

    out = list(messages)
    last = out[-1]
    content = last.get("content")

    if isinstance(content, list) and content:
        new_content = list(content[:-1]) + [
            {**content[-1], "cache_control": {"type": "ephemeral"}}
        ]
        out[-1] = {**last, "content": new_content}
    elif isinstance(content, str):
        out[-1] = {
            **last,
            "content": [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    return out


def _dump_prompt_to_file(memory: "MemoryManager", tools: list, debug_dir: str = "debug"):
    """Serialize the system prompt (blocks) and tools to JSON for inspection.

    Stored in {debug_dir}/prompts/ with ISO timestamp. Useful for:
      - Verifying the exact system prompt sent to the API
      - Debugging cache behavior (confirm stable block size)
      - Tracking changes over time
    """
    try:
        debug_path = Path(debug_dir)
        prompts_path = debug_path / "prompts"
        prompts_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().isoformat()
        filename = f"prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        system_blocks = memory.build_system_blocks()
        dump = {
            "timestamp": timestamp,
            "system_blocks": system_blocks,
            "tools_count": len(tools),
            "tools": tools,
        }

        filepath = prompts_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(dump, f, indent=2)

        log.info(f"Prompt snapshot saved to {filepath}")
    except Exception as e:
        log.warning(f"Could not dump prompt to file: {e}")


class GaladrielAgent:
    """Stateful conversational agent backed by Claude with tool use."""

    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        max_tokens: int = None,
        config_dir: str = "config",
        memory_dir: str = "memory",
        working_dir: str = None,
        approval_callback=None,
        debug_dir: str = "debug",
    ):
        self.client = AsyncAnthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model or os.environ.get("AGENT_MODEL", "claude-opus-4-6")
        self.max_tokens = max_tokens or int(os.environ.get("AGENT_MAX_TOKENS", "8192"))
        self.memory = MemoryManager(config_dir=config_dir, memory_dir=memory_dir)
        self.working_dir = working_dir or os.getcwd()
        self.conversations: dict[str, list] = {}
        self.approval_callback = approval_callback
        self.last_usage: dict = {}  # Populated after each API call; used by /status

        # Precompute tools-with-cache once. Tools never change at runtime,
        # so this object can be reused across every API call.
        self.tools = _build_cached_tools()

        # Log stable block metadata on startup
        stable_text = self.memory.build_stable_text()
        stable_chars = len(stable_text)
        stable_tokens_est = stable_chars // 4  # rough estimate: 4 chars per token
        log.info(
            f"Stable block loaded: {stable_chars} chars (~{stable_tokens_est} tokens). "
            f"Model {self.model} cache minimum: "
            f"{'4096' if 'opus' in self.model or 'haiku' in self.model else '2048'} tokens."
        )

        # Dump the complete prompt (system blocks + tools) to JSON for inspection
        _dump_prompt_to_file(self.memory, self.tools, debug_dir=debug_dir)

    def _get_messages(self, channel_id: str) -> list:
        if channel_id not in self.conversations:
            self.conversations[channel_id] = []
        return self.conversations[channel_id]

    def _trim_history(self, messages: list, max_messages: int = 100):
        """Trim conversation history, preserving tool_use/tool_result pairs.

        Default raised to 100 (from 30). With prompt caching, long histories
        are cheap — you pay the write premium once and read at 10% of base
        input cost. Aggressive trimming destroys cache continuity between
        Discord turns, which is expensive.

        The Anthropic API requires every assistant tool_use block to have a
        matching user tool_result block. Naive slicing can orphan one half.
        We trim from the front, but if the new start lands inside a
        tool_use→tool_result pair, we walk forward to a safe boundary.

        SAFETY: Never trims to fewer than 1 message.
        """
        if len(messages) <= max_messages:
            return

        cut = len(messages) - max_messages

        # Walk forward from the cut point to find a safe boundary.
        # A safe boundary is where we start with a plain user message
        # (not a tool_result).
        safe_cut = None
        scan = cut
        while scan < len(messages):
            msg = messages[scan]

            # Skip tool_result user messages — their tool_use was cut.
            if msg.get("role") == "user" and _contains_tool_result(msg):
                scan += 1
                continue

            # Skip assistant messages — API requires starting with user.
            if msg.get("role") == "assistant":
                scan += 1
                continue

            # Plain user message — safe to start here.
            safe_cut = scan
            break

        if safe_cut is not None and safe_cut < len(messages):
            if safe_cut > 0:
                del messages[:safe_cut]
                log.info(f"Trimmed conversation to {len(messages)} messages (cut {safe_cut} from front)")
            return

        # --- FALLBACK: No safe cut found via scanning ---
        # This means the entire tail is tool_use/tool_result pairs with no
        # plain user messages in the trimmable range. Find the LAST plain
        # user message in the entire list and cut everything before it.
        # If there are none, keep only the last message.
        log.warning(
            f"_trim_history: no safe cut found via scan (len={len(messages)}), "
            "using fallback — searching for last plain user message"
        )

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") == "user" and not _contains_tool_result(msg):
                del messages[:i]
                log.info(f"Fallback trim: kept from last plain user msg, now {len(messages)} messages")
                return

        # Absolute last resort: no plain user messages at all.
        # Keep only the very last message.
        last = messages[-1]
        messages.clear()
        messages.append(last)
        log.warning(f"Fallback trim: no plain user messages found, kept only last message")

    def _hard_reset(self, messages: list, user_message: str):
        """Nuclear option: clear conversation and start fresh with the user message.

        Used when max_tokens keeps hitting and normal trimming can't help.
        """
        messages.clear()
        messages.append({"role": "user", "content": user_message})
        log.warning("Hard reset: cleared entire conversation, re-seeded with original user message")

    def _log_usage(self, response):
        """Log token usage fields so caching behavior is observable.

        Healthy output on a warm cache:
            cache_read >> input_tokens,  cache_write small or 0.
        Cold/miss:
            cache_read = 0, cache_write ≈ prefix size.
        """
        usage = response.usage
        try:
            inp = usage.input_tokens
            cr = getattr(usage, 'cache_read_input_tokens', 0)
            cw = getattr(usage, 'cache_creation_input_tokens', 0)
            out = usage.output_tokens
            log.info(
                f"Tokens | input={inp} cache_read={cr} cache_write={cw} output={out}"
            )
            self.last_usage = {"input": inp, "cache_read": cr, "cache_write": cw, "output": out}
        except Exception:
            log.debug("Could not log usage fields", exc_info=True)

    async def respond(self, user_message: str, channel_id: str = "default") -> str:
        messages = self._get_messages(channel_id)
        messages.append({"role": "user", "content": user_message})
        self._trim_history(messages)

        # System is a list of blocks with cache_control on [0]. See memory.py.
        system_blocks = self.memory.build_system_blocks()
        max_tokens_retries = 0  # Track consecutive max_tokens hits

        while True:
            # Guard against empty message list
            if not messages:
                log.error("Message list is empty — cannot call API. Seeding with user message.")
                messages.append({"role": "user", "content": user_message})

            log.info(f"API call with {len(messages)} messages, last role: {messages[-1]['role']}")
            # Attach cache_control to the last block of the last message.
            # This advances the messages-cache breakpoint as the conversation
            # grows, giving hits within tool_use cascades.
            messages_for_api = _attach_trailing_cache_control(messages)
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_blocks,
                tools=self.tools,
                messages=messages_for_api,
            )

            self._log_usage(response)

            # Extract tool IDs from response BEFORE serialization
            tool_ids_from_response = set()
            if response.stop_reason == "tool_use":
                for block in response.content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_ids_from_response.add(block.id)

            # Now serialize for storage
            assistant_content = _serialize_content(response.content)
            messages.append({"role": "assistant", "content": assistant_content})
            log.info(f"Response stop_reason: {response.stop_reason}")

            if response.stop_reason == "end_turn":
                max_tokens_retries = 0  # Reset counter on success
                text_parts = [
                    block["text"]
                    for block in (assistant_content if isinstance(assistant_content, list) else [])
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                final_text = "\n".join(text_parts) if text_parts else "(no response)"
                self.memory.append_daily_log(
                    f"[chat:{channel_id}] User: {user_message[:100]}..."
                )
                return final_text

            if response.stop_reason == "max_tokens":
                max_tokens_retries += 1

                # Remove the incomplete assistant message
                del messages[-1]

                # Extract any text from the truncated response to return
                # if we're about to give up.
                truncated_text_parts = [
                    block.text
                    for block in response.content
                    if hasattr(block, "type") and block.type == "text" and hasattr(block, "text")
                ]
                truncated_text = "\n".join(truncated_text_parts).strip() if truncated_text_parts else ""

                log.warning(
                    f"Hit max_tokens mid-response (attempt {max_tokens_retries}/3), "
                    f"conversation has {len(messages)} messages"
                )

                if max_tokens_retries >= 3:
                    # We've tried 3 times — give up gracefully.
                    # Hard reset the conversation so next message works.
                    self._hard_reset(messages, user_message)
                    suffix = (
                        "\n\n*(My response was too long and I could not recover after multiple attempts. "
                        "The conversation has been reset. Please repeat your request — I'll be more concise.)*"
                    )
                    if truncated_text:
                        return truncated_text + suffix
                    return "(Response exceeded token limit repeatedly. Conversation reset. Please try again.)"

                # First two attempts: try progressively harder trimming.
                count_before = len(messages)
                if max_tokens_retries == 1:
                    self._trim_history(messages, max_messages=50)
                else:
                    self._trim_history(messages, max_messages=20)

                # If trim didn't actually reduce the count, force a hard reset.
                if len(messages) >= count_before:
                    log.warning(
                        f"Trim was ineffective ({count_before} → {len(messages)}), "
                        "performing hard reset"
                    )
                    self._hard_reset(messages, user_message)

                # Ensure we end with a user message for the API
                if messages and messages[-1].get("role") != "user":
                    messages.append({"role": "user", "content": user_message})

                continue

            if response.stop_reason == "tool_use":
                max_tokens_retries = 0  # Reset counter on successful tool use
                tool_results = []
                # Use the original response.content blocks to extract tool IDs
                for block in response.content:
                    if not hasattr(block, "type") or block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input
                    tool_id = block.id

                    if tool_name == "run_shell":
                        command = tool_input.get("command", "")
                        tier = classify_command(command)
                        log.info(format_safety_notice(command, tier))

                        if tier == "red":
                            if self.approval_callback:
                                approved = await self.approval_callback(command, tier)
                                if not approved:
                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": tool_id,
                                        "content": f"[BLOCKED] Denied: {command}",
                                    })
                                    continue
                            else:
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": f"[BLOCKED] Red-tier, no approval callback: {command}",
                                })
                                continue

                    result = await execute_tool(
                        tool_name, tool_input,
                        memory_manager=self.memory,
                        working_dir=self.working_dir,
                    )

                    if len(result) > 15000:
                        result = result[:15000] + "\n...[truncated]"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result,
                    })

                messages.append({"role": "user", "content": tool_results})
                # Loop back to send tool results to the API
                continue


    def clear_history(self, channel_id: str = "default"):
        self.conversations.pop(channel_id, None)
