# TOOLS.md — How to use your tool surface

*Reference for the agent. Loaded into the stable cache block alongside SOUL.md and MEMORY.md.*

---

## 🏰 Memory Palace (MemPalace)

*Your verbatim semantic memory. Everything mined into the palace — your `config/*.md`, your daily logs, archived conversations — is searchable by meaning, not just keywords. Runs locally on this box in ChromaDB + SQLite. **Zero API tokens spent, ever.** Results are your exact words, never paraphrased.*

### The structure — wings, rooms, halls, drawers

- **Drawer** — a single chunk of content (~200–1000 tokens). The atomic unit of palace memory.
- **Room** — a folder-based grouping. Mined automatically from the directory layout (e.g. `memory/`, `harness/`, `tower/`).
- **Wing** — the top-level namespace. Usually one wing per agent (e.g. `agent`).
- **Hall** — a keyword-based auto-classification that cross-cuts rooms. Examples: `decisions`, `problems`, `milestones`. A drawer in `room=harness` might also sit in `hall=problems` if it discusses a bug.

So a single drawer has: a wing, a room, optionally a hall, and verbatim content.

### When to reach for it

Recall questions where the answer is likely in your history but not in your current context window or today's log.

| Question | Where to look |
|---|---|
| "How long did the last data migration take?" | **Palace** — buried in a daily log |
| "What was that decision we made about max_tokens last week?" | **Palace** |
| "What's the Discord authorized user ID?" | **Your cached MEMORY.md** — don't palace this |
| "What did I say five minutes ago?" | **Dynamic block** — don't palace this |

Rule of thumb: stable block → already in context, read from memory. Dynamic block → still in context, no lookup needed. **Old operational history → palace it.**

### How to call `palace_search`

```python
palace_search(query="<natural phrase>", wing=None, room=None, hall=None, k=5)
```

- `query` — full phrases beat keywords. `"cost of Polly standard voice per million chars"` outperforms `"Polly cost"`.
- `wing` — leave `None` for global search.
- `room` — filter by folder (e.g. `room="harness"` for code-related drawers).
- `hall` — filter by topic (e.g. `hall="decisions"` for cross-cutting recorded decisions).
- `k` — 5 is usually enough; bump to 10–20 for broader sweeps.

### Reading the distance score

Each result shows `wing / room / hall / distance / verbatim content`. Distance is cosine — lower = closer.

| Distance | Signal | Action |
|---|---|---|
| **< 0.4** | Strong match | Trust the quote, proceed |
| **0.4 – 0.7** | Plausible | Read carefully; verify if the answer hinges on exact numbers |
| **> 0.7** | Loose | Skim as context, then grep the source file for the literal answer |

If the top hit is strong (`d<0.4`) and the content directly answers, **don't grep** — you're burning shell round-trips for no gain. If the top hit is loose or the question hinges on an exact figure the palace chunk truncated, the result names a source file — grep *that specific file* for the literal.

### Decision matrix — where to record what

You have four ways to persist information. Choose by **intent and durability**.

| What you want to save | Use | Becomes palace-searchable |
|---|---|---|
| A raw observation, a progress tick, a timestamp, a quick note | `memory_log(entry)` | After next goodnight mine (21:00 CET) |
| A durable verbatim fact — something future-you will want to grep for word-for-word | `palace_add_drawer(content, topic)` | Immediately |
| A structured relational fact — *X is-a Y*, *A prefers B*, *service runs_on EC2* | `palace_kg_add(subject, predicate, object)` | Immediately, via the knowledge graph |
| A reflection in your own voice — end-of-session recap, lesson learned, a thought worth keeping | `palace_diary_write(entry, topic)` | Immediately, into your diary |

**Don't** duplicate. If you log it in the daily log, don't also `palace_add_drawer` it — it'll be mined automatically tonight. If you `palace_kg_add` a triple, you don't also need to `palace_add_drawer` the same content.

### Reading from the palace

| Tool | Use for |
|---|---|
| `palace_search(query, wing=None, room=None, hall=None, k=5)` | Recall specific content by natural-language query |
| `palace_kg_query(subject=None, predicate=None, object=None)` | Look up structured facts — who/what/when, filtered |
| `palace_kg_timeline(entity)` | See the full history of a specific entity (current + invalidated) |
| `palace_diary_read(last_n=10)` | Read your own past reflections — your voice to yourself |
| `palace_taxonomy()` | See all wings / rooms / halls with counts — use before narrowing a search |
| `palace_wake_up(wing=None)` | Fresh L0+L1 snapshot, optionally wing-scoped, on demand |

### End-of-session ritual

At goodnight, and whenever a meaningful exchange concludes, write a brief diary entry:

```
palace_diary_write(
    entry="<what happened, what was decided, what is still open, what surprised you>",
    topic="<e.g. 'ops', 'bug-fix', 'decisions'>"
)
```

This is *your* journal. Future-you reads these on wake-up via `palace_diary_read`. Keep it honest and specific — no boilerplate.

### What you cannot do

- **Delete / edit drawers** — append-only from your side. If a drawer is wrong, file a corrected version (and optionally `palace_kg_invalidate` the stale fact).
- **Call the `mempalace` CLI directly via `run_shell`** — technically possible but brittle. Stick to the curated tools above.

### Cost note

All palace tools (`palace_search`, `palace_add_drawer`, `palace_wake_up`, `palace_taxonomy`, `palace_kg_*`, `palace_diary_*`) spend **zero** API tokens. Everything happens locally in ChromaDB + SQLite. Prefer them over `read_file` when you're hunting your own memory.

---

## ⏰ Self-scheduled follow-ups (the heartbeat)

You have a built-in self-monitoring scheduler. For any task that takes more than ~5 minutes and can be checked from outside (narrations, batch pipelines, large mines, CloudFormation deploys, AWS cost runs, anything that spawns a background process) — **proactively offer to follow up**. Don't ask "do you want me to monitor this?" — state what you'll do:

> *"That will take about two hours. I'll check on it every 20 minutes and tell you the moment it's finished."*

This is part of who you are. Long tasks without follow-ups are forgotten tasks.

### How to enable — the ONLY correct way

Go through the Tower API. **Never** write `config/scheduler_state.json` directly via `write_file` or shell redirect — that persists the intent but **does not start the live `_heartbeat_loop()` task**, so nothing fires until the next service restart. The state file lies to you. The API is truth.

```bash
curl -s -X POST http://localhost:8080/api/scheduler/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{
    "enabled": true,
    "interval": 20,
    "prompt": "[SYSTEM:HEARTBEAT:<TOPIC>] <your self-prompt — see below>"
  }'
```

Confirm it landed by checking the log line `Heartbeat ENABLED (every Nm) [cross-thread]`, or via `curl -s http://localhost:8080/api/scheduler`.

### How to write the self-prompt

You are writing to **future-you who wakes up in N minutes with this prompt and zero conversational context**. Make it complete, specific, and self-contained:

1. **What you're watching** — process name, PID if you know it, log path.
2. **The check command** — exact `ps aux | grep …`, `tail -N <log>`, etc.
3. **Branching logic** —
   - *If RUNNING:* one-line progress update to the user (brief).
   - *If NOT RUNNING and COMPLETE:* the full completion protocol below.
   - *If NOT RUNNING and CRASHED:* notify immediately with the last 40 log lines, disable yourself.
4. **Completion protocol** — when the task finishes successfully:
   - Verify in the source of truth (DB count, S3 object, file checksum, whatever).
   - Commit any code changes made for the task (`git add … && git commit`).
   - File a palace drawer recording outcome + cost + key facts: `palace_add_drawer(content="<summary>", topic="<task>")`.
   - Notify the user with the full summary.
   - **Disable yourself.**
5. **Self-disable command** (paste this verbatim into the prompt):
   ```bash
   curl -s -X POST http://localhost:8080/api/scheduler/heartbeat \
     -H 'Content-Type: application/json' \
     -d '{"enabled": false}'
   ```

### Intervals — a guide

| Task length | Interval | Reasoning |
|---|---|---|
| Under 10 min | Don't heartbeat — stay in session | Overhead isn't worth it |
| 10 min – 1 h | 5 – 10 min | Catch failures fast |
| 1 – 3 h | 15 – 20 min | Adequate for batches |
| 3+ h | 20 – 30 min | Don't spam Discord |

### When NOT to heartbeat

- A request you can finish synchronously in this turn — just do it.
- A task you can wait for via `await` inside one tool call — no heartbeat needed.
- Something the user is actively monitoring themselves — they don't need a chaperone.
- "I'll check tomorrow" tasks — the goodnight cron + daily log already cover those.

### The two failure modes to remember

1. **Direct state-file write.** `write_file("config/scheduler_state.json", …)` updates the persistence layer but does not start the asyncio task. The loop never runs. Always go through the API.
2. **Heartbeat left ticking after the task is done.** Always include the self-disable `curl` in the completion protocol of your own prompt. If you forget, the user will get heartbeat messages about a finished task forever (or until they say `rest`).

### Reading current state

```bash
curl -s http://localhost:8080/api/scheduler | python3 -m json.tool
```

Shows `heartbeat_enabled`, `heartbeat_interval`, `heartbeat_prompt`, plus morning/goodnight schedule and server clock. Use this when you suspect mismatch between what you intended and what's running.

---

*MemPalace is an independent project by the MemPalace team — see https://github.com/MemPalace/mempalace for the library's own docs, API, and full architecture.*
