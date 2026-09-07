# INCIDENTS.md — Sanitized Adaptation Ledger

*An empirical record of operational incidents, root causes, architectural adaptations, and committed regression tests.*

---

## Purpose & Canon of Descent

Galadriel's long-term memory belongs to the operator and sits outside revision control by design (sovereign, encrypted, private). However, an evolving system must demonstrate that **accumulated experience actually alters subsequent behavior**. 

Under our **Canon of Descent**, private substrate and personal memory remain strictly confidential, but architectural mechanisms, systemic failure modes, and operational adaptations are generalized and published.

This document serves as the verifiable ledger linking observed failure patterns to their architectural adaptations and deterministic regression suites in `tests/`.

---

## Adaptation Matrix

| ID | Incident / Wound Class | Observed | Mechanism Adapted | Verification Suite | Status |
|---|---|---|---|---|---|
| **INC-001** | Tool-Pair Orphan Deadlock | 2026-06-27 | Pre-flight tool-use / tool-result sanitizer | `tests/test_tool_pair_repair.py` | Verified active |
| **INC-002** | Semantic Aiming Failure (Supersede Mismatch) | 2026-07-04 | Two-step dry-run gate for memory demotions | `tests/test_memory_correction.py` | Verified active |
| **INC-003** | Compound Shell Classification Bypass | 2026-07-01 | Operator tokenization & max-tier escalation | `tests/test_safety_chaining.py` | Verified active |
| **INC-004** | Interactive Console Hang in Background Runs | 2026-07-01 | Fail-closed bounded timeout on terminal prompts | `tests/test_local_approval.py` | Verified active |
| **INC-005** | Palace Lock Contention & Sweeper Overrun | 2026-09-06 | Process-level mine guard + pre-flight batch caps | `tests/test_palace_mine_guard.py` | Verified active |
| **INC-006** | Single-Valued Fact Multiplicity in Temporal KG | 2026-06-29 | Last-write-wins temporal deliberator | `tests/test_kg_restated_facts.py` | Verified active |
| **INC-007** | Thinking Model Context Window Truncation | 2026-09-06 | Provider seam dynamic context discovery | `tests/test_context_window_discovery.py` | Verified active |
| **INC-008** | Routine History Trim Asymmetry | 2026-06-20 | Token-budgeted trim + pre-drop palace archival | `tests/test_measured_keep.py`, `tests/test_unbroken_thread.py` | Verified active |
| **INC-009** | Restart Race Condition & Wake Stranding | 2026-07-15 | Deferred wake arming surviving process cycles | `tests/test_wake_defer.py` | Verified active |
| **INC-010** | Fallback Ladder Rejected `thinking=` (TypeError on every call) | 2026-09-07 | Ladder accepts/forwards `thinking` additively; all providers accept the kwarg | `tests/test_openai_provider.py::test_fallback_ladder_accepts_and_forwards_thinking` | Verified active |

---

## Detailed Records

### INC-001: Tool-Pair Orphan Deadlock
- **Wound:** Mid-cascade context compaction or network interruption dropped either a `tool_use` request or its corresponding `tool_result` unevenly. In upstream model APIs (Anthropic, Bedrock), an unattached result or unresolved invocation triggers a fatal `400 Bad Request: expected tool_result following tool_use`, deadlocking the conversation loop permanently.
- **Root Cause:** Slicing conversation buffers by raw message index rather than semantic tool transaction boundaries.
- **Harness Adaptation:** Implemented a two-phase pre-flight sanitizer in `harness/agent.py`:
  1. `_sanitize_tool_pairs`: Identifies dangling `tool_use` blocks lacking a result and synthesizes synthetic `is_error` results.
  2. `_strip_orphan_tool_results`: Identifies dangling `tool_result` blocks without parent invocations and purges them before API serialization.
- **Verification:** `tests/test_tool_pair_repair.py` (11 unit tests covering forward/reverse orphans, mixed tool turns, and multi-turn cascade repairs). Zero recurrences across continuous multi-turn operation.

### INC-002: Semantic Aiming Failure (Supersede Mismatch)
- **Wound:** When the agent attempted to correct or supersede a historical memory drawer via semantic description (`palace_supersede_drawer`), high semantic similarity matched an adjacent, innocent drawer. Two consecutive false-positive replacements degraded active context.
- **Root Cause:** Treating semantic search as an targeting actuator rather than a discovery mechanism.
- **Harness Adaptation:** Enforced a mandatory two-step dry-run gate in `harness/palace.py`:
  - Calling `palace_supersede_drawer` or `palace_retire_drawer` without an exact `drawer_id` executes a read-only search and mutates nothing.
  - Actual demotion requires re-invoking with the exact verified target hash, logging provenance and replacement linkages.
- **Verification:** `tests/test_memory_correction.py` (pins structural mutation requirements and verified non-target preservation).

### INC-003: Compound Shell Command Classification Bypass
- **Wound:** Command safety classifier inspected shell command strings as monolithic blocks. A destructive command chained after a benign utility (e.g. `ls -la && sudo rm -rf /`) escaped red-tier classification because the prefix matched green-tier patterns.
- **Root Cause:** Regex-based classification lacking awareness of shell control operators.
- **Harness Adaptation:** Implemented lexical command tokenization in `harness/safety.py`: commands are split along shell control delimiters (`&&`, `||`, `;`, `|`), each segment is classified independently, and the overall execution assumes the maximum tier of any constituent segment.
- **Verification:** `tests/test_safety_chaining.py` (26 tests verifying compound statements, pipelines, and nested subshells).

### INC-004: Interactive Console Hang in Background Runs
- **Wound:** Red-tier destructive command execution in local environments prompts the user for interactive terminal confirmation. When background processes, scheduled routines, or ambient reflection ticks ran in a terminal-attached environment, a destructive command prompt blocked indefinitely waiting for stdin.
- **Root Cause:** Unbounded blocking `input()` calls in an asynchronous event loop.
- **Harness Adaptation:** Refactored `harness/local_approval.py` to execute console input inside a non-blocking thread with a bounded timeout (`timeout_seconds=60.0`). Any unanswered prompt automatically fails closed (`DENIED`).
- **Verification:** `tests/test_local_approval.py` (pins fail-closed denial on non-interactive environments, EOF, and bounded timeouts).

### INC-005: Palace Lock Contention & Sweeper Overrun
- **Wound:** Multiple asynchronous agent processes (mid-turn memory filing, scheduled summaries, conversation trimming) attempted simultaneous operations against the single-writer SQLite/Chroma store. Collisions caused unhandled lock exceptions; recurring retry loops attempted to re-index the entire working directory tree, leading to 180s timeouts and queue thrashing.
- **Root Cause:** Lack of harness-level writer serialization and unconstrained directory traversal during archival sweeps.
- **Harness Adaptation:** Built `harness/palace_mine_guard.py`:
  - Enforces mutual exclusion across all harness processes.
  - Implements bounded wait-and-retry against external lock holders.
  - Restricts archival mining to a dedicated staging directory (`<archive>/daily_logs/`).
  - Pre-flight budget gate: rejects batches >300 files or >25 MiB before database locks are acquired.
  - Exponential backoff with quarantine after six consecutive failures.
- **Verification:** `tests/test_palace_mine_guard.py` and `tests/test_daily_log_stage.py`.

### INC-006: Single-Valued Fact Multiplicity in Temporal Knowledge Graph
- **Wound:** When an agent updated a single-valued personal or operational attribute (e.g., changing models or persona names), separate valid-from triples existed concurrently in the database, causing ambiguous resolution and identity reverting.
- **Root Cause:** Relational graph treating all predicates as multi-valued collections without temporal conflict resolution.
- **Harness Adaptation:** Integrated a registered single-valued predicate deliberator in `vendor/mempalace_patches/knowledge_graph.py`. When multiple active triples are encountered for designated predicates, the resolution layer deterministically prioritizes the triple with the latest `valid_from` timestamp.
- **Verification:** `tests/test_kg_restated_facts.py` (build-time overlay verification ensuring conflicting triples resolve cleanly).

### INC-007: Thinking Model Context Window Truncation
- **Wound:** New generation reasoning models (Claude 3.7/Fable, Gemini Flash Thinking) allocate variable budgets for internal thought chains. Standard static token estimators failed to account for provider-specific reasoning token overhead, causing unexpected context exhaustion and mid-cascade truncation.
- **Root Cause:** Provider-agnostic message estimators assuming uniform context limits and zero internal deliberation overhead.
- **Harness Adaptation:** Introduced dynamic context discovery and capability negotiation in `harness/providers.py`. Adapters dynamically query active context boundaries, track provider reasoning limits, and partition conversational headroom from prompt cache prefixes.
- **Verification:** `tests/test_context_window_discovery.py` and `tests/test_provider_parity.py`.

### INC-008: Routine History Trim Asymmetry
- **Wound:** Routine message window trimming historically discarded oldest turn slices in place, whereas manual resets (`/new`) archived context to the memory palace first. Over extended sessions, intermediate conversational context was silently lost without searchable traces.
- **Root Cause:** Fragmented history management routines across disparate command handlers.
- **Harness Adaptation:** Unified history lifecycle under "The Measured Keep" and "The Unbroken Thread":
  - Trimming is governed by an estimated token budget rather than message counts.
  - Truncated conversation slices are automatically archived to the memory palace prior to eviction from the working context window.
- **Verification:** `tests/test_measured_keep.py` and `tests/test_unbroken_thread.py`.

### INC-009: Restart Race Condition & Wake Stranding
- **Wound:** When the agent executed a self-directed restart to load code updates, an immediate wake trigger raced process termination, stranding the newly booted instance without instructions to resume its interrupted task.
- **Root Cause:** Wake triggers executing in-process prior to service supervisor lifecycle completion.
- **Harness Adaptation:** Implemented persistent deferred wake arming in `harness/scheduler.py`: one-shot wake prompts persist to disk (`scheduler_state.json`) and execute exclusively after gateway connection and service warmup on the subsequent boot.
- **Verification:** `tests/test_wake_defer.py`.

### INC-010: Fallback Ladder Rejected `thinking=`
- **Wound:** When extended thinking was added to the agent's single model call site, the call began passing `thinking=` to whatever provider sat behind the seam. The direct providers accepted it; `FallbackProvider.complete()` did not. Any deployment with `AGENT_MODEL_FALLBACKS` set therefore raised `TypeError: unexpected keyword argument 'thinking'` on **every** call — the ladder that exists to keep the agent up was the thing taking it down. Found during the OpenAI wiring, by reading the signatures side by side; reproduced in one line.
- **Root Cause:** A protocol widened at the call site without every implementer of the protocol being widened with it — the seam's interface (`LLMProvider.complete`) still documented the narrower signature, so the parity tests had nothing to fail against.
- **Harness Adaptation:** `FallbackProvider.complete()` and `BedrockNovaProvider.complete()` accept `thinking`; the ladder forwards it **additively** (omitted when falsy, so the thinking-off path stays byte-identical to the pre-ladder call). The fake providers in the ladder's own tests were widened to match the real contract.
- **Verification:** `test_fallback_ladder_accepts_and_forwards_thinking` (new) plus the existing `tests/test_fallback_provider.py` suite.

---

## Conclusion

This ledger proves that Galadriel's evolution is not cosmetic: every recurring operational failure has driven an architectural adaptation, codified as a permanent guard and regression test in the codebase.
