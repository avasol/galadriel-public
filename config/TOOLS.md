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

*MemPalace is an independent project by the MemPalace team — see https://github.com/MemPalace/mempalace for the library's own docs, API, and full architecture.*
