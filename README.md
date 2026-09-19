# Galadriel

**The open engine behind [Aedelgard](https://aedelgard.com) — a persistent AI agent with sovereign memory, a model-agnostic brain, and the ability to improve its own code.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-Aedelgard-5865F2?logo=discord&logoColor=white)](https://discord.gg/TODO)

---

> *Separate the mind from the brain power.* The mind — memory, identity, values — is yours, local, portable, and model-agnostic. The brain — the LLM that does the thinking — is a rented commodity you can swap at any time. Galadriel is the engine that proves this thesis in code.

---

## What this is

A persistent AI agent harness that runs on your own machine. It connects to any LLM provider (Claude, Gemini, OpenAI, Bedrock, Nebius/DeepSeek), maintains a local verbatim memory palace with zero-token retrieval, and can edit its own code to improve how it works.

This is **not** a product. It is the open engine that [Aedelgard](https://aedelgard.com) packages into a one-click desktop app. Everything here is real, inspectable, and yours to build.

## What's in the box

| Component | What it does |
|---|---|
| **🏛️ Memory Palace** | Verbatim semantic search + temporal knowledge graph. Local ChromaDB + SQLite. Zero API cost to recall anything. |
| **🧠 Provider Seam** | One agent, many brains. Swap Claude → Gemini → DeepSeek → local model mid-conversation without losing the mind. |
| **🔧 Self-Modification** | The agent can edit its own harness, restart itself, and resume — with a one-shot wake that survives the restart. |
| **💭 Ambient Reflection** | A silent background loop that curates memory, notices patterns, and files what a purely reactive agent would forget. |
| **🛡️ Scar Tissue** | Compound failure patterns that graduate into mandatory checks and deterministic tests. [INCIDENTS.md](INCIDENTS.md) tracks them. |
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

Both quick-starts load settings from the same `.env` file. Choose a provider by
setting `AGENT_PROVIDER` and its credential as shown below, then start or restart
the Docker container (or the local Python process):

| Provider | Get credentials | `.env` setting |
|---|---|---|
| Anthropic (default) | [Anthropic Console](https://console.anthropic.com/) | `AGENT_PROVIDER=anthropic`<br>`ANTHROPIC_API_KEY=sk-ant-...` |
| Gemini | [Google AI Studio](https://aistudio.google.com/apikey) | `AGENT_PROVIDER=gemini`<br>`GEMINI_API_KEY=...` |
| OpenAI | [OpenAI API keys](https://platform.openai.com/api-keys) | `AGENT_PROVIDER=openai`<br>`OPENAI_API_KEY=sk-...` |
| Bedrock (Nova) | [Amazon Bedrock](https://console.aws.amazon.com/bedrock/) | `AGENT_PROVIDER=bedrock`<br>Use the AWS credential chain (for example, an EC2 instance role); no API key is needed. Set `AWS_REGION` if you are not using the default region. |
| Nebius (DeepSeek) | [Nebius AI Studio](https://studio.nebius.ai/) | `AGENT_PROVIDER=nebius`<br>`NEBIUS_API_KEY=...` |

Keep `.env` private and never commit real credentials. The Docker quick-start
passes this file to the container; for local Python, the app reads it directly.

## Provider seam — swap the brain, keep the mind

The agent's brain is a provider adapter. Change one environment variable to switch:

```bash
AGENT_PROVIDER=anthropic        # Claude (default)
AGENT_PROVIDER=gemini           # Google Gemini
AGENT_PROVIDER=openai           # OpenAI
AGENT_PROVIDER=bedrock          # AWS Bedrock (Nova)
AGENT_PROVIDER=nebius           # Nebius Token Factory (DeepSeek, EU-hosted)
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

Built on [MemPalace](https://github.com/MemPalace/mempalace), an independent local-first memory library. The harness adds 13 palace tools (17 total) wired into the agent's lifecycle. Search by meaning. Zero API spend on retrieval.

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

## License

MIT — see [LICENSE](LICENSE).

---

*The name is the oldest mnemonic architecture we have. Simonides of Ceos (c. 500 BCE) identified the dead crushed beneath a collapsed banquet hall by recalling exactly where each guest had been seated, and from that inferred that memory is strongest when bound to ordered place. Cicero wrote it down. The method of loci is twenty-five centuries old. This project gives it to an AI.*
