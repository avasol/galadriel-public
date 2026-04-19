"""Context compaction — summarize old tool results with Haiku to keep history lean."""

import logging
from anthropic import AsyncAnthropic

log = logging.getLogger("galadriel.compaction")


async def compact_conversation(messages: list, api_key: str = None) -> dict:
    """Summarize old messages to reduce context size.

    Strategy:
      - Keep last 20 messages verbatim (recent context is important).
      - For older messages: summarize tool_result blocks if content > 3K chars.
      - Use Haiku (cheap) to generate summaries.

    Returns:
      {
        "compacted_messages": [...],  # Updated message list
        "tokens_before": X,
        "tokens_after": Y,
        "compression_ratio": 0.65,    # After/before
        "summaries_created": N,
      }
    """
    if len(messages) <= 20:
        return {
            "compacted_messages": messages,
            "tokens_before": 0,
            "tokens_after": 0,
            "compression_ratio": 1.0,
            "summaries_created": 0,
        }

    client = AsyncAnthropic(api_key=api_key)
    fresh_count = 20
    old_messages = messages[:-fresh_count]
    fresh_messages = messages[-fresh_count:]

    summaries_created = 0
    compacted_old = []

    for msg in old_messages:
        # Tool results are the main culprits for size
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            content = msg["content"]
            new_content = []

            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and len(block.get("content", "")) > 3000
                ):
                    # Summarize this tool result
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

            compacted_old.append({**msg, "content": new_content})
        else:
            # Non-tool-result messages or tool_use blocks: keep as-is
            compacted_old.append(msg)

    compacted = compacted_old + fresh_messages

    # Rough token estimate: 4 chars ≈ 1 token
    tokens_before = sum(
        len(str(m.get("content", ""))) // 4 for m in messages
    )
    tokens_after = sum(
        len(str(m.get("content", ""))) // 4 for m in compacted
    )

    ratio = tokens_after / tokens_before if tokens_before > 0 else 1.0

    return {
        "compacted_messages": compacted,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "compression_ratio": ratio,
        "summaries_created": summaries_created,
    }
