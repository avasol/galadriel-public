# mempalace local patches (overlaid in the Docker image)

These files overlay the pip-installed `mempalace` package (pinned in
`requirements.txt`) at image-build time, via a `COPY` step in the Dockerfile
*after* `pip install`. They are LOCAL PATCHES carrying clearly-marked
`LIVING-MEMORY` blocks, kept here until upstreamed into mempalace proper.

## knowledge_graph.py — restated-fact date deliberator (2026-06-27)
Adds `single_valued` predicate handling + a `SINGLE_VALUED_PREDICATES`
registry (named_self, prefers_name, current_name, goes_by, current_model,
runs_on_model, current_provider). Restating a single-valued fact auto-closes
the prior open object; the DATE DELIBERATOR settles which value is current
(latest valid_from wins, out-of-order safe), history preserved. Fixes the
Jaina->Ellie->Jaina contradiction-pile bug where a web mind that renamed its
self multiple times left several `mind named_self X` triples open at once and
a new device fell back to the oldest name.

## miner.py + searcher.py — Living Memory lifecycle (ported from the private
harness, 2026-08-26, re-cut for mempalace 3.8.0)
`miner.py`: `_build_drawer_metadata`/`add_drawer` gain `lifecycle_status`
(default `active`), `confidence`, `origin`, `session_id`, `superseded_by`;
a front-matter directive parser (`- origin:`, `- confidence:`, `- session:`)
feeds them from any drawer's header. `searcher.py`: `search_memories(...)`
regains `include_stale: bool = False` — 3.8.0's rewritten hybrid-rerank
scoring loop had DROPPED this kwarg entirely, which is a hard break (harness
code calls it) not a soft one. Root cause + full upgrade procedure: palace
drawer `mempalace-version-drift-root-cause-2026-08-26` (galadriel wing).

Base version patched: mempalace 3.8.0 (previously 3.3.2). If the requirements
pin changes, re-cut all three patches against the new base before bumping —
diff the new file layout against the anchor points first; 3.8.0 roughly
doubled/tripled both files' size (logstream, RFC 002/003 adapters, a backend
abstraction layer).
