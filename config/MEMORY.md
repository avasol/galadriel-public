# MEMORY.md - Long Term Memory

## About Your User

- **User Name:** Lord Isildur  ← change this to whatever you want to be called
- **Authorized Discord ID:** [your-discord-user-id]

## Constraints

- [Add any model or provider restrictions here, e.g. "No OpenAI usage"]
- **FinOps:** [Add cost targets, e.g. "Monthly burn < $20"]

## Infrastructure

- **Server:** EC2 t4g.medium in eu-north-1  ← update to your actual machine
- **Working Dir:** /opt/galadriel
- **Python Venv:** /home/ubuntu/.venv
- **Model:** claude-opus-4-6 (override with AGENT_MODEL in .env)
- **Systemd Service:** galadriel.service — install via `sudo bash cmd/install.sh`

## Key Paths

- **Harness code:** /opt/galadriel
- **Config:** /opt/galadriel/config/
- **Daily memory logs:** /opt/galadriel/memory/
- **Ops scripts:** /opt/galadriel/cmd/

## Operational Notes

- This file is yours to evolve. Add what matters. Remove what doesn't.
- Galadriel can update this file herself using the write_file tool.
- Git remote: [your repo URL]
