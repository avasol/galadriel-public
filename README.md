# Galadriel

> **🧊 Frozen reference engine — 2026-10-08, tag `reference-2026-10-08`.**
> This repository is complete as it stands and receives no new features. It is the point from
> which the Aedelgard body branched; the body has since moved on privately and is **not** the
> same code. What stays open and current is the **[Aedelgard Mind Format](https://github.com/avasol/aedelgard-mind-format)**:
> what a mind is on disk, how it travels, how it is sealed, how extensions are signed. Fork
> freely (MIT). See [Status after the freeze](#status-after-the-freeze).

**The open engine behind [Aedelgard](https://aedelgard.com) — a persistent AI agent with sovereign memory, a model-agnostic brain, and the ability to improve its own code.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

> *Separate the mind from the brain power.* The mind — memory, identity, values — is yours, local, portable, and model-agnostic. The brain — the LLM that does the thinking — is a rented commodity you can swap at any time. Galadriel is the engine that proves this thesis in code.

---

## What this is

A persistent AI agent harness that runs on your own machine. It connects to any LLM provider (Claude, Gemini, OpenAI, Bedrock, Nebius/DeepSeek, xAI/Grok, Mistral, Berget), maintains a local verbatim memory palace that recalls without calling any model, and can edit its own code to improve how it works.

This is **not** a product. It is the open engine that [Aedelgard](https://aedelgard.com) grew from. Everything here is real, inspectable, and yours to build on.

## What's in the box

| Component | What it does |
|---|---|
| **🏛️ Memory Palace** | Verbatim semantic search + temporal knowledge graph. Local ChromaDB + SQLite. Recall makes no model calls (search runs on a local embedding model). |
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

Choose one provider configuration below and add it to `.env`. The same `.env`
file is used by both the Docker and local Python quick starts.

| Provider | Key source | `.env` configuration |
|---|---|---|
| Anthropic | [console.anthropic.com](https://console.anthropic.com) | `AGENT_PROVIDER=anthropic`<br>`ANTHROPIC_API_KEY=sk-ant-...` |
| Gemini | [aistudio.google.com](https://aistudio.google.com) | `AGENT_PROVIDER=gemini`<br>`GEMINI_API_KEY=...` |
| OpenAI | [platform.openai.com](https://platform.openai.com) | `AGENT_PROVIDER=openai`<br>`OPENAI_API_KEY=sk-...` |
| Bedrock | AWS IAM (an EC2 instance role or other AWS credentials) | `AGENT_PROVIDER=bedrock`<br>No API key needed |
| Nebius/DeepSeek | [studio.nebius.ai](https://studio.nebius.ai) | `AGENT_PROVIDER=nebius`<br>`NEBIUS_API_KEY=...` |

### Docker (recommended)

```bash
git clone https://github.com/avasol/galadriel-public.git
cd galadriel-public
cp .env.example .env          # choose a provider configuration above
docker compose up -d --build
docker compose logs -f
```

The Tower UI comes up on [http://127.0.0.1:8080](http://127.0.0.1:8080).
For Bedrock, run Docker on an AWS instance with an IAM role (or configure
standard AWS credentials and `AWS_REGION` in `.env`).

### Local Python

```bash
git clone https://github.com/avasol/galadriel-public.git
cd galadriel-public
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # choose a provider configuration above
python harness/main.py
```

For Bedrock, the local process likewise uses the AWS credential chain and needs
no provider API key. For Gemini, OpenAI, or Nebius/DeepSeek, export the key in
`.env` before starting the local process.

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
AGENT_PROVIDER=berget           # Berget AI (EU-hosted, Sweden)
AGENT_PROVIDER=local            # Ollama / LM Studio / vLLM, offline
AGENT_MODEL=claude-sonnet-5     # model within the provider
```

All 18 provider parity tests are in `tests/test_provider_parity.py`. The memory — palace, knowledge graph, diary, identity — survives any swap. The brain is a socket.

## Memory architecture

Three layers, all local; recalling from them calls no model:

| Layer | Substrate | What it stores |
|---|---|---|
| **Episodic Drawers** | ChromaDB (local embeddings) | Verbatim conversations, decisions, code changes |
| **Temporal Knowledge Graph** | SQLite | Structured facts with validity windows (`valid_from` → `valid_to`) |
| **Identity & Cognition** | Markdown + SQLite Diary | Values, constraints, session diary, ambient reflections |

Built on [MemPalace](https://github.com/MemPalace/mempalace), an independent local-first memory library. The harness adds 13 palace tools (18 tools in total) wired into the agent's lifecycle. Search by meaning, with a local embedding model. No model calls, no API spend on recall.

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
| **Edge** (`harness/edge.py`, `tower/edge.py`) | A desk widget ([xeneon-edge-companion](https://github.com/avasol/xeneon-edge-companion)) shows the main conversation, streams replies and receives scheduled messages. Token-gated, file links signed and short-lived, turns recovered after a dropped stream. See [docs/EDGE.md](docs/EDGE.md). | `/api/edge/*`, `POST /api/edge-token` on the Tower. |
| **Instance identity** (`harness/body_identity.py`) | Each running copy of a mind knows its own name, OS and id, and the other copies it lives on, so a once-per-mind routine fires on exactly one of them. | `POST /api/body/name` to rename. |

## Threat model — read before judging

This is a **founder's harness**. It is designed for one person on their own hardware:

- `run_shell` is deliberately unrestricted — the operator IS the user.
- The Tower UI binds to localhost without authentication.
- Self-modification is a feature: the agent writes and commits its own code without a pre-commit gate; the operator reviews afterward via `git log`. **Pushing to any remote asks first** (red tier).
- Extension approval pins the exact files (integrity), but it is **not a sandbox**: an approved code extension runs with the agent's own rights.
- The Tower checks the `Host` and `Origin` of every request, so other web pages and DNS-rebinding tricks cannot drive it.

**The Aedelgard body carries a tighter posture.** It gates first-run behind consent, background reflection may *propose but never act*, and the hosted service never gets these tools. Judge the product by [aedelgard.com/architecture](https://aedelgard.com/architecture) and [aedelgard.com/security](https://aedelgard.com/security). This repo shows the engine's honesty, not the product's perimeter.

## Aedelgard — what grew from this

[Aedelgard](https://aedelgard.com) is a signed desktop app that began as this engine and has
diverged from it: a keyring for third-party keys, a reviewed extension catalogue, messaging
channels, a desk widget, one-click install, encrypted backup and sync between your own machines.

- [Download](https://aedelgard.com/download) — Windows, Linux
- [Architecture](https://aedelgard.com/architecture) — trust matrix and honest status
- [The Mind Format](https://github.com/avasol/aedelgard-mind-format) — the open definition both share

Same thesis, same memory format. Not the same code.

## Status after the freeze

- **Tests:** at the freeze the full suite (902 passed, 1 skipped for an optional dependency)
  ran in a clean Python 3.12 environment built from [`requirements.lock`](requirements.lock),
  the exact versions listed there. `pip install -r requirements.lock pytest && pytest` repeats it.
  `requirements.txt` keeps the looser ranges.
- **Models will age.** Provider adapters talk to APIs that keep changing. This table is what was
  checked live on 2026-10-08 (a short request that had to call a tool):

  | Provider | Model checked | Result |
  |---|---|---|
  | Anthropic | `claude-sonnet-5` | ✅ tool call |
  | OpenAI | `gpt-5.5`; default `gpt-4o-mini` | ✅ tool call |
  | Google Gemini | default `gemini-flash-latest`; `gemini-3.1-pro-preview` | ✅ tool call (default); 3.1 Pro called the tool in one of two runs |
  | Nebius | default `deepseek-ai/DeepSeek-V4-Pro` | ✅ tool call |
  | Berget | default `google/gemma-4-31B-it` | ✅ tool call |
  | AWS Bedrock | default `eu.amazon.nova-micro-v1:0` | ✅ tool call |
  | Mistral | `mistral-large-latest` | ⚠️ not verified (our account tier) |
  | xAI | — | ⚠️ not verified (no key) |
  | Local (Ollama etc.) | your model | the most durable path: nothing upstream changes under you |

  If a model id stops working, set `<PROVIDER>_MODEL` in `.env`; no code change is needed.
- **Issues and pull requests:** this repository is frozen, so new features are not merged.
  Forks are welcome.
- **Security:** see [SECURITY.md](SECURITY.md). Reports are read; fixes to this frozen code are
  not promised.
- **Cancelled here:** capability keys for extensions and a host for outside (MCP) tools. They
  exist in the Aedelgard body, not in this engine.

## Project structure

```
galadriel-public/
├── harness/           # Agent core (main loop, providers, tools, scheduler)
├── tests/             # Provider parity, tool repair, memory lifecycle
├── config/            # Example configs (not the live config)
├── cmd/               # Install and ops scripts
├── tower/             # Tower web UI (Flask + SSE)
├── discord_bot/       # Discord transport
├── docs/              # Extensions, Edge, response status
├── assets/            # Promotional images
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Contributing

This engine is frozen (see above), so pull requests for new features will not be merged. Fork it:
it is MIT. To improve the shared format, open an issue on the
[Mind Format](https://github.com/avasol/aedelgard-mind-format).

## Made by

[Isildur](https://aedelgard.com/isildur) — [Thomas Avasol](https://millenion.se), Millenion AB. The open engine behind [Aedelgard](https://aedelgard.com).

## License

MIT — see [LICENSE](LICENSE).

---

*The name is the oldest mnemonic architecture we have. Simonides of Ceos (c. 500 BCE) identified the dead crushed beneath a collapsed banquet hall by recalling exactly where each guest had been seated, and from that inferred that memory is strongest when bound to ordered place. Cicero wrote it down. The method of loci is twenty-five centuries old. This project gives it to an AI.*