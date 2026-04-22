# Prompt caching in the Galadriel harness

This document explains the caching wiring in `harness/memory.py` and
`harness/agent.py`, what it costs, and how to verify it's working.

## TL;DR

Three explicit cache breakpoints are set on every API call:
1. Last tool definition (caches the `tools` prefix).
2. The stable system block (caches SOUL.md + MEMORY.md + any other `*.md`
   in `config/`, including your CONTEXT.md).
3. The last content block of the last message (caches the growing conversation).

Expected saving on repeat-read tokens: **~90%** off the normal input price.
Token usage is logged after every API call so you can watch it work.

## Cache minimums — important for Opus users

For the cache to engage, the prefix up to a breakpoint must exceed the
model's minimum cacheable length:

| Model | Minimum cacheable prefix |
|---|---|
| Claude Opus 4.x / Haiku 4.5 | 4,096 tokens (~16KB of text) |
| Claude Sonnet 4.6 | 2,048 tokens |
| Claude Sonnet 4.5 / 4 | 1,024 tokens |

SOUL.md + MEMORY.md + TOOLS.md alone is typically 2–3K tokens — still below
the Opus threshold. This is why `config/CONTEXT.md` exists: fill it with your
project details (architecture, goals, known issues, key paths) and the stable
block will comfortably clear 4K. You get the context for free (cache reads),
and Galadriel never needs tool calls to reference it.

If you see `cache_read=0` and `cache_write=0` in every log line, your stable
block is under the minimum. Add content to CONTEXT.md.

## What the code does

### `harness/memory.py`

`MemoryManager.build_system_blocks()` returns **two content blocks**:

```python
[
    {
        "type": "text",
        "text": <stable content>,
        "cache_control": {"type": "ephemeral"},   # cache breakpoint
    },
    {
        "type": "text",
        "text": <dynamic content>,                # timestamp + daily logs
    },
]
```

**Stable content** (cached):
- `SOUL.md` — always first
- Active Vision (if set via Tower `/api/vision`)
- `MEMORY.md`
- Any other `*.md` in `config/` — auto-loaded alphabetically

**Dynamic content** (not cached, but small):
- MemPalace wake-up snapshot (if installed and seeded; ~800 tokens). Disable
  with `PALACE_WAKE_UP_INJECT=0` to recover this overhead.
- Yesterday's and today's daily logs
- Current timestamp

### `harness/agent.py`

- `self.tools` is computed once at init with `cache_control` on the last tool.
- Every `messages.create()` call passes the 2-block system list and the
  trailing-cache-attached message history.
- The stored history is **never mutated** — cache markers only exist on the wire.
- `_log_usage()` runs after every response.

## Verifying it works

Tail the service logs:

```bash
journalctl -u galadriel -f
```

A healthy warm cache looks like:

```
Tokens | input=50  cache_read=0     cache_write=5200 output=120   ← cold: writes prefix
Tokens | input=80  cache_read=5200  cache_write=180  output=340   ← warm: reads prefix
Tokens | input=50  cache_read=5380  cache_write=220  output=200   ← subsequent turns
```

Key signals:
1. **`cache_write` is large on the first call** — writing the stable prefix + tools.
2. **`cache_read` climbs on call #2 onward** — prefix is warm.
3. **`input_tokens` stays small** — only new user message + dynamic context.

Use `/status` in Discord to see the last API call's token breakdown in real time.

## Expected cost impact

Quick back-of-envelope for a heavy day on Opus 4.6 ($5/MTok input, $25/MTok output):

| | Without caching | With caching |
|---|---|---|
| Stable prefix (~5K tok), 20 turns/day | 20 × 5K × $5/MTok = **$0.50/day** | ~2 writes + 18 reads × 5K = **~$0.08/day** |
| Conversation tail (growing) | Sent in full each turn | Cached from 2nd message onward |

The stable prefix cost drops ~84%. Tool-heavy agentic workloads are input-heavy,
so the total bill impact is substantial.

## Model choice and the cache minimum

Sonnet 4.6 ($3/$15 per MTok) has a **2048-token cache minimum** — easier to
clear than Opus's 4096. If you're doing mostly bash/CLI work, Sonnet's cache
engages more readily and the base token cost is 40% lower.

To switch: set `AGENT_MODEL=claude-sonnet-4-6` in `.env`.
