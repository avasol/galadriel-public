"""Context compaction — summarize old tool results with Haiku to keep history lean."""

import logging
from anthropic import AsyncAnthropic

log = logging.getLogger("galadriel.compaction")

# Keep images in messages within the last N user turns. Beyond that, the visual
# context is usually moot and the base64 blob just burns tokens.
IMAGE_RETENTION_USER_TURNS = 3

# Tool results in the last N messages are kept verbatim. Older long ones get
# summarized by Haiku.
TOOL_RESULT_FRESH_MESSAGES = 20


def _is_user_turn(msg: dict) -> bool:
    """A real user turn — not a tool_result wrapper, which is also role=user."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("text", "image"):
                return True
    return False


async def compact_conversation(messages: list, api_key: str = None) -> dict:
    """Compress conversation history.

    - Images in messages older than the last IMAGE_RETENTION_USER_TURNS user
      turns are replaced with a text placeholder.
    - Long tool_result blocks older than the last TOOL_RESULT_FRESH_MESSAGES
      are summarized by Haiku.
    """
    user_turn_idx = [i for i, m in enumerate(messages) if _is_user_turn(m)]
    if len(user_turn_idx) > IMAGE_RETENTION_USER_TURNS:
        image_retain_from = user_turn_idx[-IMAGE_RETENTION_USER_TURNS]
    else:
        image_retain_from = 0

    summarize_before = max(0, len(messages) - TOOL_RESULT_FRESH_MESSAGES)

    if image_retain_from == 0 and summarize_before == 0:
        return {
            "compacted_messages": messages,
            "tokens_before": 0,
            "tokens_after": 0,
            "compression_ratio": 1.0,
            "summaries_created": 0,
            "images_removed": 0,
        }

    client = AsyncAnthropic(api_key=api_key)
    summaries_created = 0
    images_removed = 0
    compacted = []

    for i, msg in enumerate(messages):
        strip_images_here = i < image_retain_from
        summarize_here = i < summarize_before

        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            new_content = []
            for block in msg["content"]:
                if (
                    strip_images_here
                    and isinstance(block, dict)
                    and block.get("type") == "image"
                ):
                    new_content.append({
                        "type": "text",
                        "text": "[image removed — context compacted]",
                    })
                    images_removed += 1
                    continue

                if (
                    summarize_here
                    and isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and len(block.get("content", "")) > 3000
                ):
                    result_text = block["content"]
                    try:
                        summary_response = await client.messages.create(
                            model="claude-haiku-4-5-20251001",
                            max_tokens=150,
                            messages=[
                                {
                                    "role": "user",
                                    "content": (
                                        f"Summarize this tool output in 1-2 sentences. "
                                        f"Preserve critical details (errors, file paths, counts). "
                                        f"Discard verbose scaffolding:\n\n{result_text}"
                                    ),
                                }
                            ],
                        )
                        summary = summary_response.content[0].text
                        new_content.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("tool_use_id"),
                                "content": f"[SUMMARIZED] {summary}",
                            }
                        )
                        summaries_created += 1
                        log.info(
                            f"Summarized tool result: {len(result_text)} → {len(summary)} chars"
                        )
                    except Exception as e:
                        log.warning(f"Could not summarize tool result: {e}, keeping original")
                        new_content.append(block)
                else:
                    new_content.append(block)

            compacted.append({**msg, "content": new_content})
        else:
            compacted.append(msg)

    # Rough token estimate: 4 chars ≈ 1 token
    tokens_before = sum(len(str(m.get("content", ""))) // 4 for m in messages)
    tokens_after = sum(len(str(m.get("content", ""))) // 4 for m in compacted)
    ratio = tokens_after / tokens_before if tokens_before > 0 else 1.0

    return {
        "compacted_messages": compacted,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "compression_ratio": ratio,
        "summaries_created": summaries_created,
        "images_removed": images_removed,
    }
