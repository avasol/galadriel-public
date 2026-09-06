# Prompt caching in the Galadriel harness

This document explains how the harness caches the stable part of the prompt —
soul, memory, tools — on each brain it can run, what it costs, and how to
verify it's working. The mechanism differs per vendor; the *mind* being cached
is the same bytes either way, and `harness/providers.py` picks the dialect.

## TL;DR

| Brain | Mechanism | Where | Minimum prefix | Warm read |
|---|---|---|---|---|
| Claude | explicit `cache_control` breakpoints on the request | `AnthropicProvider` + `memory.py` | 1,024–4,096 tokens by model | ~10% of input price |
| Gemini | explicit `cachedContents` object + Google's implicit caching | `GeminiProvider._get_or_create_cache` | 2,048 (2.5) / 4,096 (3.x) tokens | ~10% of input price, plus storage per hour |
| OpenAI | vendor-automatic prefix caching | nothing to wire | — | vendor-set |
| Bedrock Nova / local | none wired | — | — | full price / free |

Token usage is logged after every API call so you can watch it work on any brain.

> **Lineage.** Caching arrived here on Claude first (spring 2026) with the three-breakpoint
> design documented below; the measured 86.5% hit ratio in the README was taken on that
> path. Gemini caching followed in September 2026 once the provider seam made a second
> brain a real option. The Claude section is kept intact because it is still exactly how
> the Anthropic path works — it is simply no longer the only path.

## Cache minimums — the one thing you must clear

For a cache to engage, the prefix must exceed the model's minimum cacheable
length. Below it, the API silently declines — no error, just `cache_read=0`.

| Model | Minimum cacheable prefix |
|---|---|
| Claude Sonnet 4.6 / 4.5, Opus 4.8 | 1,024 tokens |
| Claude Opus 4.7 | 2,048 tokens |
| Claude Opus 4.6 / 4.5, Haiku 4.5 | 4,096 tokens (~16 KB of text) |
| Gemini 2.5 Flash / 2.5 Pro | 2,048 tokens |
| Gemini 3.1 Pro, 3.5–3.8 Flash | 4,096 tokens |

SOUL.md + MEMORY.md + TOOLS.md alone is typically 2–3K tokens — below the
4,096 floor that the Gemini 3.x and older-Opus brains share. This is why
`config/CONTEXT.md` exists: fill it with your project details (architecture,
goals, known issues, key paths) and the stable block will comfortably clear 4K
on every brain. You get the context for free (cache reads), and Galadriel never
needs tool calls to reference it.

If you see `cache_read=0` and `cache_write=0` in every log line, your stable
block is under the minimum. Add content to CONTEXT.md.

## The Gemini path (`GeminiProvider`)

Google offers two layers and the harness uses both:

- **Implicit caching** is on by default for every Gemini 2.5+ model: repeated
  prompt prefixes are discounted automatically, nothing to wire. This is why a
  warm Gemini turn typically logs `input=2 cache_read=<almost the whole prompt>` —
  `cachedContentTokenCount` counts implicit hits too. That line is correct.
- **Explicit caching** is what the seam adds. On each call the provider splits
  the system blocks by their `cache_control` marker — the same marker the
  Anthropic path uses — into a *stable* text (soul, memory, config) and a
  *dynamic* text (timestamp, daily logs, wake-up snapshot). If the stable text
  is long enough it is hashed together with the tool schemas, and a
  `cachedContents` object is created once (`ttl: 3600s`, system instruction +
  tools inside it). Every later call references that object by name and sends
  only the conversation plus the dynamic text, which is injected at the top of
  the first user message. The hash → name map lives in the provider instance;
  the cache is recreated when the stable prefix changes.
- **Self-healing.** If Google reports the cached object gone (TTL expired,
  400/404 mentioning the cache), the provider forgets the name and retries the
  same call once without it — a cold write, not a failure.
- **Usage.** `cache_read` = `cachedContentTokenCount`; the turn after a create
  reports the cached size as `cache_write` so the ledger sees the cost of the
  write. `thoughtsTokenCount` is folded into output, because Google bills it as
  output.

Two honest costs the Anthropic path does not have: an explicit cache is billed
**storage** for as long as it lives (per MTok per hour — cents a day at soul
scale, refreshed each turn it is used), and **Gemini 3.1 Pro doubles its rates
above 200k prompt tokens**, cached or not. Check the vendor pricing page for
your model; the harness does not hide either.

## The Anthropic path — the original three breakpoints

Three explicit cache breakpoints are set on every API call:
1. Last tool definition (caches the `tools` prefix).
2. The stable system block (caches SOUL.md + MEMORY.md + any other `*.md`
   in `config/`, including your CONTEXT.md).
3. The last content block of the last message (caches the growing conversation).

Expected saving on repeat-read tokens: **~90%** off the normal input price.

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

On Gemini the same healthy state reads differently — the explicit object is
created once, then Google's implicit layer covers most of the rest:

```
Tokens | input=61290 cache_read=0      cache_write=0     output=310   ← cold: cachedContents created
Tokens | input=2     cache_read=287169 cache_write=391   output=488   ← warm: whole prefix + history served from cache
```

Key signals:
1. **`cache_write` is large on the first call** — writing the stable prefix + tools.
2. **`cache_read` climbs on call #2 onward** — prefix is warm.
3. **`input_tokens` stays small** — only new user message + dynamic context.

Use `/status` in Discord to see the last API call's token breakdown in real time.

## Expected cost impact

Quick back-of-envelope for a heavy day on Claude Opus 4.6 ($5/MTok input, $25/MTok output):

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
