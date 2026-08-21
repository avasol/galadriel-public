# SKILL: Heartbeat Monitor — self-scheduled follow-up on long-running tasks

- version: 1.0
- sealed: 2026-08-21
- origin: procedure — Lord Isildur's observation: the heartbeat machinery in every
  Aedelgard body is identical to the private harness; a skill should ship as default
  so every body knows the practice correctly without improvising from TOOLS.md each time.
- ledger: `memory/command_ledger/heartbeat-monitor.jsonl`
- STATUS: ACTIVE

---

## When to use

Any task that takes more than ~5 minutes AND can be observed from outside:
a batch pipeline, a large file operation, a cloud deploy, a build, a mine, a sync.

**Proactively offer it — do not ask "should I monitor this?"**
State what you'll do: *"That'll take about an hour. I'll watch every 20 minutes and
tell you the moment it's finished."*

**Do NOT heartbeat when:**
- You can finish the task synchronously this turn.
- The task can be awaited inside a single tool call.
- The user is actively watching themselves.
- "I'll check tomorrow" — the goodnight cron and daily log cover those.

---

## Phase 1 — Enable (the only correct way)

**Always go through the Tower API. Never write `config/scheduler_state.json` directly.**
A direct file write updates the persistence layer but does NOT start the live asyncio loop —
nothing fires until the next restart. The API is truth; the file is a shadow.

```bash
curl -s -X POST http://localhost:8080/api/scheduler/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{
    "enabled": true,
    "interval": <N>,
    "prompt": "[SYSTEM:HEARTBEAT:<TOPIC>] <self-contained monitor prompt — see Phase 2>"
  }'
```

**Valid intervals (minutes):** `5`, `10`, `20`, `30` — no other values are accepted.

**Interval guide:**

| Task length   | Interval  | Reason                        |
|---------------|-----------|-------------------------------|
| Under 10 min  | Don't — stay in session | Overhead isn't worth it |
| 10 min – 1 h  | 5 – 10 min | Catch failures fast         |
| 1 – 3 h       | 15 – 20 min | Adequate for batches       |
| 3+ h          | 20 – 30 min | Don't spam the user        |

Confirm the loop started: look for log line `Heartbeat ENABLED (every Nm) [cross-thread]`,
or check state: `curl -s http://localhost:8080/api/scheduler | python3 -m json.tool`.

---

## Phase 2 — Write the self-prompt

You are writing to **future-you who wakes in N minutes with this prompt and ZERO conversational
context**. Every monitor prompt must be self-contained and include all five elements:

1. **What you're watching** — process name, PID if known, log path.
2. **The check command** — exact shell invocation (`ps aux | grep …`, `tail -N <log>`, etc.).
3. **Branching logic:**
   - *If RUNNING:* brief progress update to the user (in character is fine; keep it short).
   - *If NOT RUNNING and COMPLETE:* run the Phase 3 completion protocol (below).
   - *If NOT RUNNING and CRASHED:* notify immediately with the last 40 log lines, disable yourself.
4. **Completion protocol** — Phase 3, pasted verbatim into the prompt.
5. **Self-disable command** — must appear in the prompt so the tick can execute it without
   consulting external context:
   ```bash
   curl -s -X POST http://localhost:8080/api/scheduler/heartbeat \
     -H 'Content-Type: application/json' \
     -d '{"enabled": false}'
   ```

---

## Phase 3 — Completion protocol (runs inside the final tick, or in-session)

When the task finishes successfully:
1. **Verify by substance** — check the artifact, not the exit code: DB count, S3 object, file
   checksum, HTTP 200 + real content. A process that exited 0 and left nothing is not done.
2. **Commit code changes** — if the task produced code (`git add … && git commit -m "…"`).
3. **File a palace drawer** — record outcome, cost, duration, key facts:
   `palace_add_drawer(content="<summary>", topic="<task-slug>")`.
4. **Notify the user** — full summary: what ran, what it produced, any caveats.
5. **Disable the heartbeat** — the self-disable curl from Phase 2. A heartbeat left running
   after task completion is noise. The user will receive phantom messages forever (or until
   they say `rest`). Disable. Every. Time.

---

## Success criteria (substance)

- Task completion verified against the real artifact (not exit code alone).
- User notified with a complete summary.
- Heartbeat **disabled** in the same tick that declares completion.
- Palace drawer filed for any non-trivial run.
- Ledger entry appended to `memory/command_ledger/heartbeat-monitor.jsonl`.

---

## Forbidden

- Writing `config/scheduler_state.json` directly (bypasses the live asyncio loop).
- Leaving the heartbeat enabled after the task ends.
- Writing a self-prompt that requires the next tick to re-read external context — the
  prompt must be fully self-contained at arm time.
- Using an interval not in `[5, 10, 20, 30]` — the scheduler silently ignores it and
  falls back to the default.
- Announcing "I'll monitor this" without actually arming the heartbeat before the turn ends.

---

## Rollback

If the heartbeat fires on a bad prompt (missing self-disable, wrong check command):
```bash
curl -s -X POST http://localhost:8080/api/scheduler/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false}'
```
Then re-arm with a corrected prompt if the task is still running.

---

## Lifecycle

A run that deviates from this playbook must supersede this file in the same gesture —
skills stay alive, or they lie. If the scheduler API changes (new endpoints, new valid
intervals), amend version + sealed date and note what changed.

When this skill's results drift (e.g. a tick consistently fires without the self-disable,
or the Tower API path changes), mark STATUS: STALE and propose an amendment before the
next run.
