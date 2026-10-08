# Galadriel

**The open engine behind [Aedelgard](https://aedelgard.com) — a persistent AI agent with sovereign memory, a model-agnostic brain, and the ability to improve its own code.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

> *Separate the mind from the brain power.* The mind — memory, identity, values — is yours, local, portable, and model-agnostic. The brain — the LLM that does the thinking — is a rented commodity you can swap at any time. Galadriel is the engine that proves this thesis in code.

---

## What this is

A persistent AI agent harness that runs on your own machine. It connects to any LLM provider (Claude, Gemini, OpenAI, Bedrock, Nebius/DeepSeek, xAI/Grok, Mistral), maintains a local verbatim memory palace with zero-token retrieval, and can edit its own code to improve how it works.

This is **not** a product. It is the open engine that [Aedelgard](https://aedelgard.com) packages into a one-click desktop app. Everything here is real, inspectable, and yours to build.

## What's in the box

| Component | What it does |
|---|---|
| **🏛️ Memory Palace** | Verbatim semantic search + temporal knowledge graph. Local ChromaDB + SQLite. Zero API cost to recall anything. |
| **🧠 Provider Seam** | One agent, many brains. Swap Claude → Gemini → DeepSeek → local model mid-conversation without losing the mind. |
| **🔧 Self-Modification** | The agent can edit its own harness, restart itself, and resume — with a one-shot wake that survives the restart. |
| **💭 Ambient Reflection** | A silent background loop that curates memory, notices patterns, and files what a purely reactive agent would forget. |
| **🛡️ Scar Tissue** | Compound failure patterns that graduate into mandatory checks and deterministic tests. [INCIDENTS.md](INCIDENTS.md) tracks them. |
| **🔄 Long-running life** | Rollover to a fresh context when the cache goes cold, a memory save on shutdown, a local cost ledger, and memory for its own commands. See below. |
| **🌐 Interfaces** | Discord bot, Tower web UI (localhost:8080), REST API, and the [Xeneon Edge Companion HUD](https://github.com/avasol/xeneon-edge-companion). |

## Interfaces & peripherals

### Xeneon Edge Companion
A dedicated 2560×720 ultrawide desktop HUD for Corsair iCUE displays. Hardware-accelerated GPU layer isolation eliminates multi-monitor video flicker. Buffered SSE streaming, interactive tool approvals, and real-time journal log streaming — a window into the agent that lives on your secondary display.

→ **[avasol/xeneon-edge-companion](https://github.com/avasol/xeneon-edge-companion)**

### Tower Web UI
A localhost dashboard at `http://localhost:8080` with status telemetry, conversation history, and interactive tool approval.

### Discord
Connect a bot token and the agent meets you in Discord — same mind, same memory.

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/avasol/galadriel-public.git
cd galadriel-public
cp .env.example .env          # add your ANTHROPIC_API_KEY
docker compose up -d --build
docker compose logs -f
```

The Tower UI comes up on [http://127.0.0.1:8080](http://127.0.0.1:8080).

### Local Python

```bash
git clone https://github.com/avasol/galadriel-public.git
cd galadriel-public
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your ANTHROPIC_API_KEY
python harness/main.py
```

## Provider seam — swap the brain, keep the mind

The agent's brain is a provider adapter. Change one environment variable to switch:

```bash
AGENT_PROVIDER=anthropic        # Claude (default)
AGENT_PROVIDER=gemini           # Google Gemini
AGENT_PROVIDER=openai           # OpenAI
AGENT_PROVIDER=bedrock-nova     # AWS Bedrock (Nova)
AGENT_PROVIDER=nebius           # Nebius Token Factory (DeepSeek, EU-hosted)
AGENT_PROVIDER=xai              # xAI (Grok)
AGENT_PROVIDER=mistral          # Mistral (EU-sovereign, Paris)
AGENT_PROVIDER=local            # Ollama / LM Studio / vLLM, offline
AGENT_MODEL=claude-sonnet-5     # model within the provider
```

All 18 provider parity tests are in `tests/test_provider_parity.py`. The memory — palace, knowledge graph, diary, identity — survives any swap. The brain is a socket.

## Memory architecture

Three layers, all local, all zero-token retrieval:

| Layer | Substrate | What it stores |
|---|---|---|
| **Episodic Drawers** | ChromaDB (local embeddings) | Verbatim conversations, decisions, code changes |
| **Temporal Knowledge Graph** | SQLite | Structured facts with validity windows (`valid_from` → `valid_to`) |
| **Identity & Cognition** | Markdown + SQLite Diary | Values, constraints, session diary, ambient reflections |

Built on [MemPalace](https://github.com/MemPalace/mempalace), an independent local-first memory library. The harness adds 13 palace tools (18 tools in total) wired into the agent's lifecycle. Search by meaning. Zero API spend on retrieval.

## Living for a long time

An agent that runs for weeks has problems a chat window never sees. These modules handle them. Each one fails safe: if it breaks, the agent carries on.

| Module | What it does | Settings |
|---|---|---|
| **Rollover** (`harness/rollover.py`) | When a long thread has gone idle and its prompt cache has expired, the next message starts in a fresh context. The last few exchanges are carried over verbatim, and the full thread is archived to the palace. This saves re-paying for a cold, very long context. | `GALADRIEL_ROLLOVER=0` turns it off. `GALADRIEL_ROLLOVER_IDLE_S` (default 300), `GALADRIEL_ROLLOVER_MIN_TOKENS` (default 60000), `GALADRIEL_ROLLOVER_KEEP` (default 2 exchanges). |
| **Last word** (`harness/last_word.py`) | When the process is stopped without a planned restart, it writes a final note to the daily log and arms a one-shot wake, so the next start knows it was cut off. A planned restart already carries its own wake, so nothing extra is written. | `GALADRIEL_NIGHTLY_STOP_HOUR` (0–23, optional): if your machine stops on a schedule, no wake is armed after that hour. Unset means a wake is always armed. |
| **Cost ledger** (`harness/cost_ledger.py`) | Estimates the agent's own spend from the token counts each provider returns, and writes it to `memory/cost_ledger.jsonl`. No network calls. | None. |
| **Reflex arc** (`harness/reflex_arc.py`) | Keeps an index of the commands the agent has written for itself in `bin/`, with a run log in `memory/command_ledger/`. A command flagged as stale in its header refuses to run and points back to the playbook it came from. | `GALADRIEL_BIN_DIR` |
| **Compass navigator** (`harness/compass_navigator.py`) | Switches between headings (projects) **you** have created, using each heading's name plus optional keywords in its own file. It never invents a heading. | `GALADRIEL_COMPASS_AUTOSHIFT=0` turns it off. |
| **Look** (`harness/look.py`) | A tool that opens a local image file so the model can see it. | None. |
| **Quiet log** (`harness/log_quiet.py`) | Hides successful web requests (2xx/3xx) and duplicate HTTP-client lines from the log, so errors stand out. 4xx/5xx lines are always logged. | None. |
| **Extensions** (`harness/extensions.py`, `harness/ext_runtime.py`) | A mind's own additions in `extensions/<name>/`: prompt text, scheduled routines, and approved code that adds tools (through the same safety gate, red tier asks first) and hooks with time limits. Nothing runs until approved; code approval is pinned to a hash of the files, so any change asks again. See [docs/EXTENSIONS.md](docs/EXTENSIONS.md). | `GET/POST /api/extensions…` on the Tower. |
| **Instance identity** (`harness/body_identity.py`) | Each running copy of a mind knows its own name, OS and id, and the other copies it lives on, so a once-per-mind routine fires on exactly one of them. | `POST /api/body/name` to rename. |

## Threat model — read before judging

This is a **founder's harness**. It is designed for one person on their own hardware:

- `run_shell` is deliberately unrestricted — the operator IS the user.
- The Tower UI binds to localhost without authentication.
- Self-modification is a feature: the agent writes, commits, and pushes without a pre-commit gate. The operator reviews afterward via `git log`.

**The Aedelgard product carries a tighter posture.** The packaged body gates first-run behind consent, background reflection may *propose but never act*, and the hosted service never gets these tools. Judge the product by [aedelgard.com/architecture](https://aedelgard.com/architecture) and [aedelgard.com/security](https://aedelgard.com/security). This repo shows the engine's honesty, not the product's perimeter.

## Aedelgard — the packaged body

Prefer a one-click install? [Aedelgard](https://aedelgard.com) packages this same engine into a signed desktop app with a tighter safety posture:

- [Download](https://aedelgard.com/download) — Windows, Linux (macOS following)
- [Architecture](https://aedelgard.com/architecture) — trust matrix and honest status
- [How it works](https://aedelgard.com/how-it-works) — the two-tier model, plainly

Same memory. Same provider seam. Same thesis. Built for you instead of by you.

## Project structure

```
galadriel-public/
├── harness/           # Agent core (main loop, providers, tools, scheduler)
├── tests/             # Provider parity, tool repair, memory lifecycle
├── config/            # Example configs (not the live config)
├── cmd/               # Install and ops scripts
├── tower/             # Tower web UI (Flask + SSE)
├── edge_widget/       # Xeneon Edge Companion widget source
├── assets/            # Promotional images
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Contributing

Issues and PRs welcome. The [issues](https://github.com/avasol/galadriel-public/issues) track bugs, feature requests, and architectural discussions.

Before opening a PR: run `pytest` — the provider parity suite and tool-repair tests must pass. Self-modification contributions should include a note on what guard or test prevents regression.

## Made by

[Isildur](https://aedelgard.com/isildur) — [Thomas Avasol](https://millenion.se), Millenion AB. The open engine behind [Aedelgard](https://aedelgard.com).

## License

MIT — see [LICENSE](LICENSE).

---

*The name is the oldest mnemonic architecture we have. Simonides of Ceos (c. 500 BCE) identified the dead crushed beneath a collapsed banquet hall by recalling exactly where each guest had been seated, and from that inferred that memory is strongest when bound to ordered place. Cicero wrote it down. The method of loci is twenty-five centuries old. This project gives it to an AI.*