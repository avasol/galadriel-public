# CONTEXT.md - Project Context

This file is part of the stable cache block. Fill it in with your project details —
what you're building, your architecture, your current state, your goals. The more
context you put here, the less you need to re-explain every session.

**Why this file matters for cost:** Galadriel uses Anthropic prompt caching. The stable
block (SOUL.md + MEMORY.md + this file + any other *.md in config/) is cached at ~10%
of normal input cost after the first call. For caching to engage on Claude Opus, the
stable block must exceed 4096 tokens (~16KB of text). Keep this file reasonably detailed
and you'll always clear that threshold. See CACHING.md for the full explanation.

---

## What I'm Building

[Describe your project here. What is it? What problem does it solve? Who is it for?]

Example:
> A personal finance dashboard that aggregates transactions from multiple banks,
> runs monthly budget analysis, and sends me a summary every Sunday morning.
> Built on AWS Lambda + DynamoDB. Frontend in React hosted on S3.

---

## Architecture

[Describe your tech stack and infrastructure.]

| Component | Technology | Notes |
|-----------|-----------|-------|
| Backend   | [e.g. AWS Lambda / FastAPI / Django] | |
| Database  | [e.g. DynamoDB / PostgreSQL / SQLite] | |
| Frontend  | [e.g. React / plain HTML / None] | |
| Hosting   | [e.g. EC2 / Vercel / Raspberry Pi] | |
| CI/CD     | [e.g. GitHub Actions / manual] | |

### Key Resources

| Name | Type | Purpose |
|------|------|---------|
| [resource name] | [e.g. S3 bucket] | [what it's for] |
| [resource name] | [e.g. Lambda function] | [what it's for] |

---

## Current State

[What phase is the project in? What's working, what's not?]

### Done
- [ ] [completed milestone]
- [ ] [completed milestone]

### In Progress
- [ ] [current focus]

### Blocked / Next
- [ ] [what's coming up]

---

## Key Files and Paths

[Help Galadriel find her way around your codebase.]

| Path | Purpose |
|------|---------|
| [/path/to/file] | [what it does] |
| [/path/to/dir/] | [what lives here] |

---

## Active Goals

[What are you trying to accomplish right now? Be specific — Galadriel will orient
her suggestions and tool use around these.]

1. [Goal one — e.g. "Get the signup flow working end to end"]
2. [Goal two — e.g. "Reduce Lambda cold start time below 500ms"]
3. [Goal three]

---

## Conventions and Preferences

[How do you like things done? Coding style, naming conventions, deployment habits.]

- **Language:** [e.g. Python 3.12, TypeScript 5]
- **Style:** [e.g. Black formatter, 4-space indent, no semicolons]
- **Branching:** [e.g. trunk-based, feature branches, direct to main]
- **Deployment:** [e.g. `sam deploy` for infra, `bash deploy.sh` for frontend]
- **Testing:** [e.g. pytest, none yet, manual only]

---

## Known Issues and Quirks

[Things Galadriel should know about to avoid false starts.]

- [e.g. "AWS_PROFILE must be blank when using instance role — setting it breaks the CLI"]
- [e.g. "The DynamoDB table uses on-demand billing, not provisioned — don't set WCU/RCU"]
- [e.g. "Port 8080 is occupied by another service on this machine"]

---

## Important Links

- Repo: [URL]
- Docs: [URL if any]
- Dashboard / Console: [URL if any]

---

_Keep this file updated. Galadriel reads it every session._
