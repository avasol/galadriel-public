"""bench.economics — the second axis. Captures per-turn usage() telemetry as
a chat history is ingested and queried, so the cost/latency curve is nearly
free to produce (the harness already emits {input, cache_read, cache_write,
output} after every call via agent.last_usage).

This is the axis a pure accuracy eval misses entirely: the dreaming /
offline-consolidation edge is an ECONOMIC claim (consolidate offline so the
live turn isn't taxed by graph traversal), not an accuracy one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class EconomicsTracker:
    turns: list[dict] = field(default_factory=list)
    _last_ts: float = field(default_factory=time.monotonic)

    def record(self, usage: dict) -> None:
        now = time.monotonic()
        self.turns.append({
            "input": usage.get("input", 0),
            "cache_read": usage.get("cache_read", 0),
            "cache_write": usage.get("cache_write", 0),
            "output": usage.get("output", 0),
            "latency_s": round(now - self._last_ts, 3),
        })
        self._last_ts = now

    def totals(self) -> dict:
        keys = ("input", "cache_read", "cache_write", "output")
        out = {k: sum(t.get(k, 0) for t in self.turns) for k in keys}
        out["turn_count"] = len(self.turns)
        out["total_latency_s"] = round(sum(t["latency_s"] for t in self.turns), 3)
        return out

    def cost_curve(self) -> list[dict]:
        """Running totals after each turn — the 'cost as memory grows' curve."""
        curve = []
        running = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
        for i, t in enumerate(self.turns):
            for k in ("input", "cache_read", "cache_write", "output"):
                running[k] += t.get(k, 0)
            curve.append({"turn": i + 1, **running})
        return curve
