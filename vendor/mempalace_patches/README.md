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

Base version patched: mempalace 3.3.2. If the requirements pin changes, re-cut
this patch against the new base before bumping.
