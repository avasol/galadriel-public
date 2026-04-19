# Galadriel

A persistent, tool-wielding Claude agent with Discord and web UI interfaces. Built to be genuinely cheap to run — not as an afterthought, but as a core design constraint baked into every layer of the architecture.

---

## The cost savings that most people miss

Here is a fact that most Claude API users don't know about: **cached tokens cost 90% less than regular input tokens.** Not 10% less. Not 20% less. Ninety percent. [It's in the Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), but the majority of people building with the API leave this entirely on the table.

The math is brutal in your favour. Every API call you make, Claude processes your system prompt from scratch — your personality definition, your memory files, your tool schemas — and you pay full price for every token, every time. With prompt caching, after the first call, all of that context reads at **$0.30/MTok instead of $3/MTok** (on Sonnet). That's the same intelligence, the same context, for a tenth of the cost. On a long-running personal agent with a rich system prompt, this is not a rounding error. It changes the economics entirely.

Galadriel exploits this with three cache breakpoints, stacked deliberately:

| Cache layer | What it covers | Behaviour |
|---|---|---|
| **Tool definitions** | All four tool schemas | Cached once at startup, never re-sent |
| **Stable system block** | Personality + memory + identity files | Marked `cache_control: ephemeral`; hits at ~100% after first call |
| **Trailing message history** | The growing conversation | Attached per-call; cache hit rate rises every turn |

The stable block alone — your SOUL.md, MEMORY.md, identity files — is typically 4 000–8 000 tokens. On a warm cache, those tokens cost $0.08–$0.30/MTok instead of $0.80–$3.00/MTok depending on model. That's your biggest fixed overhead per call, cut by 90%, on every single turn of the conversation.

Anthropic's own benchmarks show latency dropping by up to 85% on long prompts with caching engaged. A 100K-token context that took 11.5 seconds drops to 2.4 seconds. For a persistent agent that carries memory across sessions, this is the difference between a tool that feels alive and one that grinds.

**Compaction** finishes the job. The `/compact` command uses Claude Haiku — the cheapest model in the family — to summarize old tool results in your conversation history. A 60-message session bloated with verbose shell output compresses to 20% of its token count, for a fraction of a cent. Haiku handles the summarization; Opus handles the thinking.

Use `/status` in Discord at any time to watch live token numbers — input, cache_read, cache_write, output — for the last API call.

### ⚠️ One thing you must do to activate the savings

Prompt caching has a **minimum prefix length** before it engages. If your stable block is too short, the API silently skips caching entirely — you get no error, no warning, just a `cache_read=0` in every log line and a bill that looks exactly like the naive approach.

| Model | Minimum to activate caching |
|---|---|
| Claude Opus (any version) | **4,096 tokens** (~16 KB of text) |
| Claude Haiku 4.5 | **4,096 tokens** |
| Claude Sonnet 4.6 | **2,048 tokens** |
| Claude Sonnet 4.5 / 4 | **1,024 tokens** |

Out of the box, `config/SOUL.md` + `config/MEMORY.md` together are roughly 500–800 tokens. **That is well below the Opus threshold.** Caching will not engage until you cross it.

**The fix:** fill in `config/CONTEXT.md`. Drop your project's architecture, goals, key file paths, known quirks, and current status into it. Any `*.md` file you place in `config/` is automatically loaded into the stable cache block — so adding content there is all it takes. A reasonably filled CONTEXT.md (1–2 pages of project notes) will push the total above 4K tokens and keep it there.

Once you're over the threshold, verify it's working:

```bash
journalctl -u galadriel -f   # or check your terminal output
```

Look for lines like:
```
Tokens | input=60 cache_read=5800 cache_write=0 output=240
```

`cache_read` climbing and `cache_write` near zero after the first call = caching is engaged and you're paying 10 cents on the dollar for that context. If `cache_read` stays at 0, add more content to `config/CONTEXT.md`. See `CACHING.md` for the full breakdown and a worked cost example.

> **Sonnet users:** your minimum is only 2,048 tokens, so SOUL.md + MEMORY.md alone may be enough. But filling CONTEXT.md is still worthwhile — the agent has your project context without needing tool calls to find it.

---

## Baked-in engineering discipline: the Karpathy principles

This project's `CLAUDE.md` embeds the [Andrej Karpathy coding guidelines](https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md) — four principles distilled from Karpathy's observations on how LLMs fail as coding assistants when left to their own instincts.

Karpathy's insight is that LLMs have a systematic failure mode: they over-build. Given any instruction, they add abstraction layers that weren't asked for, refactor adjacent code that wasn't broken, invent "flexibility" that will never be used, and generate 200 lines when 40 would suffice. The guidelines are a direct antidote to that tendency:

**1. Think Before Coding** — State assumptions explicitly. If multiple interpretations exist, surface them — don't pick silently. If something is unclear, stop and ask rather than confidently building the wrong thing.

**2. Simplicity First** — Minimum code that solves the problem, nothing speculative. No unrequested features. No abstractions for single-use code. No error handling for impossible scenarios. If it could be 50 lines, make it 50 lines.

**3. Surgical Changes** — Touch only what the task requires. Don't improve adjacent code. Don't refactor things that aren't broken. Match existing style. When your changes make something obsolete, remove it — but leave pre-existing dead code alone.

**4. Goal-Driven Execution** — Transform vague tasks into verifiable goals. "Fix the bug" becomes "write a test that reproduces it, then make it pass." Clear success criteria let the agent loop independently to completion rather than guessing when it's done.

These aren't abstract ideals — they are mechanically enforced via the `CLAUDE.md` file that Claude Code (and Galadriel, when asked to modify her own harness) reads before every task. The result is fewer rewrites, smaller diffs, and changes that trace directly to what was asked. For a codebase that runs as a persistent service you actually depend on, this matters.

---

## Features

- **Discord gateway** — DMs, channel mentions, or a dedicated channel; gated by user ID
- **Web UI (Tower)** — local chat interface and dashboard at `localhost:8080`
- **Tool use** — shell execution, file read/write, memory logging; all async, non-blocking
- **Safety tiers** — green (auto), yellow (notify), red (Discord reaction approval required)
- **Scheduler** — morning briefing, goodnight, configurable heartbeat
- **Job watcher** — monitors `/tmp/galadriel-jobs/*.done` markers and reports completions
- **Compaction** — Haiku-powered context compression on demand
- **Three-layer prompt caching** — automatically managed, always active

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/avasol/galadriel-public.git
cd galadriel-public

# 2. Install
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY at minimum

# 4. Run
python main.py
```

**Tower-only mode:** Omit `DISCORD_BOT_TOKEN` — the harness runs with just the web UI on port 8080.

**Full mode:** Set both `ANTHROPIC_API_KEY` and `DISCORD_BOT_TOKEN`.

---

## Architecture

```
main.py               Entry point — wires all components, starts Discord + Tower
harness/
  agent.py            Core agent loop: Anthropic API, tool use, cache management
  memory.py           Stable + dynamic system prompt blocks; daily memory logs
  tools.py            Tool execution: run_shell, read_file, write_file, memory_log
  safety.py           Command classification (green / yellow / red)
  compaction.py       Haiku-powered context compression
  scheduler.py        Morning briefing, goodnight, configurable heartbeat
  job_watcher.py      Background job completion notifications
discord_bot/
  bot.py              Discord gateway, approval reactions, slash + prefix commands
tower/
  app.py              Flask dashboard + REST API
  templates/          Tower UI HTML
  static/             CSS
config/
  SOUL.md             Agent personality and values (your main customization point)
  MEMORY.md           Long-term memory (agent-maintained)
  CONTEXT.md          Your project context — fill this in to activate Opus caching
memory/               Daily logs — auto-generated, gitignored
```

---

## Customization

The agent's personality lives in `config/SOUL.md`. Edit it and restart — the stable cache invalidates automatically on the next call. Swap the entire persona by replacing SOUL.md; the harness is persona-agnostic.

`config/MEMORY.md` is the agent's long-term memory. The agent can update it during a session using the `write_file` tool. It's part of the stable cache block, so changes take effect on the next call after a cache miss.

---

## Discord Commands

### Slash commands (native Discord UI — type `/` to see them)

| Command | Description |
|---------|-------------|
| `/new` | Clear this channel's history and start fresh |
| `/compact` | Compress history with Haiku — reports token reduction |
| `/status` | Model, memory usage, last API token breakdown, scheduler state |

### Prefix commands

| Command | Description |
|---------|-------------|
| `!status` | Same as `/status` |
| `!clear` | Clear history for this channel |
| `!new` | Fresh start |
| `!compact` | Compress history |

### Verbal

| Input | Behaviour |
|-------|----------|
| `rest` / `rest.` / `rest!` | Disable heartbeat; agent acknowledges |

---

## Safety Tiers

All shell commands are classified before the agent executes them:

| Tier | Behaviour | Examples |
|------|----------|---------|
| 🟢 **Green** | Auto-execute | `ls`, `git status`, `aws s3 ls`, `cat`, `python3 script.py` |
| 🟡 **Yellow** | Notify, proceed | `git push`, `pip install`, `sudo systemctl`, `sam deploy` |
| 🔴 **Red** | Discord reaction required (✅/❌, 30s timeout → denied) | `rm`, IAM changes, CloudFormation mutations, `shutdown` |

Unknown commands default to yellow. Red commands denied by timeout or ❌ are never executed.

---

## Scheduler

| Event | Default time | Condition |
|-------|-------------|-----------|
| **Morning briefing** | 09:10 CET | Workdays (Mon–Fri) |
| **Goodnight** | 21:00 CET | Daily; disables heartbeat |
| **Heartbeat** | Every 10 min | When enabled; off by default |

---

## Environment Variables

See `.env.example` for the full list with inline documentation.

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `DISCORD_BOT_TOKEN` | No | Enables Discord gateway |
| `DISCORD_AUTHORIZED_USER_ID` | No | Only this Discord user ID can interact |
| `DISCORD_CHANNEL_ID` | No | Guild channel for conversation |
| `TOWER_HOST` | No | Tower bind address (default: `127.0.0.1`) |
| `TOWER_PORT` | No | Tower port (default: `8080`) |
| `TOWER_SECRET_KEY` | No | Flask session secret — change this |
| `AGENT_MODEL` | No | Claude model (default: `claude-opus-4-6`) |
| `AGENT_MAX_TOKENS` | No | Max output tokens per call (default: `8192`) |

---

## Security Notes

**Before running on a public server, read this.**

**Tower UI has no authentication.** It's designed to run on `127.0.0.1` and be accessed via SSH tunnel. Binding it to `0.0.0.0` on a server with an open port gives anyone who can reach that port full agent access — which includes shell execution.

> Access Tower over SSH tunnel: `ssh -L 8080:localhost:8080 user@host` — keep `TOWER_HOST=127.0.0.1`.

**Discord is the secure interface.** Authorization is enforced by `DISCORD_AUTHORIZED_USER_ID`. Only messages from that user ID are processed. Unauthorized users get "I do not know you, stranger."

**`run_shell` is unrestricted.** The agent can execute any command the process user can run. The safety tier system classifies and gates commands, but it's defense-in-depth, not a sandbox. Run the harness as a low-privilege user on a dedicated machine or VM.

**`read_file` and `write_file` have no path restrictions.** The agent can read any file the process can access. This is intentional for a personal assistant that needs to operate freely on your system.

**Debug prompt dumps** are excluded from git (`.gitignore` covers `debug/prompts/`). If you re-enable them, be aware they contain your full system prompt including personality and memory files.

---

## License

MIT
