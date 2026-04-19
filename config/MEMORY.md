# MEMORY.md - Long Term Memory

## About Your User

- **User Name:** [How Galadriel should address you — e.g. "Commander", "Captain", your first name]
- **Authorized Discord ID:** [Your Discord user ID — right-click your name with Developer Mode on]

## Constraints

- [Add any model or provider restrictions here]
- **FinOps:** [Add cost targets, e.g. "Monthly burn < $20"]

## Infrastructure

- **Server:** [Describe your machine — e.g. "EC2 t4g.medium in eu-north-1" or "local Ubuntu 22.04"]
- **Working Dir:** [Absolute path to this repo — e.g. "/opt/galadriel"]
- **Python Venv:** [Path to your venv — e.g. "/home/ubuntu/.venv"]
- **Model:** claude-opus-4-6 (override with AGENT_MODEL in .env)
- **Systemd Service:** galadriel.service — install via `sudo bash cmd/install.sh`

## Key Paths

- **Harness code:** [path to repo]
- **Config:** [path]/config/
- **Daily memory logs:** [path]/memory/
- **Ops scripts:** [path]/cmd/

## Operational Notes

- This file is yours to evolve. Add what matters. Remove what doesn't.
- Git remote: [your repo URL]
