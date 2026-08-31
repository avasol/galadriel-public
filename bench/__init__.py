"""bench — the G1 headless benchmark harness (instrument, not trigger).

Sealed spec: BENCHMARK_HARNESS_SPEC.md (aedelgard repo, 2026-07-01, commit 8d2b601).
This module enters LongMemEval (arXiv 2410.10813, ICLR 2025) against a fresh,
ephemeral GaladrielAgent — never a live mind. See bench/README.md for the
"summon a body, point this at it, get a score" on-ramp.

Nothing in this module touches the live hot path. It is purely additive.
"""
