"""Local spend ledger — estimates this agent's OWN provider spend from the
token usage already logged on every API call. No admin key, no network call,
nothing leaves the box. Estimate (not the provider's billed figure), but
accurate to the published per-token prices for the agent model.

Ledger is a JSONL file, one line per API call:
  {"ts": ISO, "model": str, "input": N, "cache_read": N, "cache_write": N, "output": N}

Totals are computed on demand (e.g. by the morning report).
"""
from __future__ import annotations
import json, datetime
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "memory" / "cost_ledger.jsonl"

# USD per 1M tokens (published list prices; an offline estimate, re-read when vendors change prices).
PRICING = {
    "default":            {"input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 1.00},
    # Anthropic
    "claude-fable-5-1":   {"input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 0.25},
    "claude-fable":       {"input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 1.00},
    "claude-opus-5-5":    {"input":  5.0, "output": 25.0, "cache_write":  6.25, "cache_read": 0.50},
    "claude-opus-5":      {"input":  5.0, "output": 25.0, "cache_write":  6.25, "cache_read": 0.50},
    "claude-opus-4-8":    {"input":  5.0, "output": 25.0, "cache_write":  6.25, "cache_read": 0.50},
    "claude-opus-4-7":    {"input":  5.0, "output": 25.0, "cache_write":  6.25, "cache_read": 0.50},
    "claude-opus":        {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-5":    {"input":  2.0, "output": 10.0, "cache_write":  2.50, "cache_read": 0.20},
    "claude-sonnet":      {"input":  3.0, "output": 15.0, "cache_write":  3.75, "cache_read": 0.30},
    "claude-haiku":       {"input":  1.0, "output":  5.0, "cache_write":  1.25, "cache_read": 0.10},
    # Google (no cache fields — our wiring does not call Gemini's context cache)
    "gemini-3.1-pro":     {"input":  2.0, "output": 12.0, "cache_write":  2.0,  "cache_read": 0.20},
    "gemini-3.8-flash":   {"input":  0.75,"output":  3.75,"cache_write":  0.75, "cache_read": 0.075},
    "gemini-3.7-flash":   {"input":  0.75,"output":  3.75,"cache_write":  0.75, "cache_read": 0.075},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50, "cache_write": 0.30, "cache_read": 0.03},
    "gemini-2.5-pro":     {"input":  1.25,"output": 10.0, "cache_write":  1.25, "cache_read": 0.125},
    "gemini-2.5-flash":   {"input":  0.30,"output":  2.50,"cache_write":  0.30, "cache_read": 0.03},
    "gemini":             {"input":  2.0, "output": 12.0, "cache_write":  2.0,  "cache_read": 0.20},
    # OpenAI
    "gpt-5.6-sol":        {"input":  5.0, "output": 30.0, "cache_write":  5.0,  "cache_read": 0.50},
    "gpt-5.6-terra":      {"input":  2.0, "output": 12.0, "cache_write":  2.0,  "cache_read": 0.20},
    "gpt-5.6-luna":       {"input":  0.2, "output":  1.2, "cache_write":  0.2,  "cache_read": 0.02},
    # OpenAI-compatible hosted models. Some expose no cache fields (the prompt
    # token details come back null), so the cache rates below are inert for
    # those rows; they are kept only so the table mirrors the published sheet.
    "deepseek-v4-pro":     {"input":  0.55,"output":  2.19,"cache_write":  0.55, "cache_read": 0.14},
    "deepseek-v4.1-flash": {"input":  0.15,"output":  0.30,"cache_write":  0.15, "cache_read": 0.038},
    "deepseek-v4-flash":   {"input":  0.14,"output":  0.28,"cache_write":  0.14, "cache_read": 0.035},
    "deepseek":            {"input":  2.0, "output":  8.0, "cache_write":  2.0,  "cache_read": 0.20},
}


def _rates(model: str) -> dict:
    """Longest matching key wins (so a versioned row beats its family row)."""
    m = (model or "").lower()
    best = None
    for key in PRICING:
        if key != "default" and key in m and (best is None or len(key) > len(best)):
            best = key
    return PRICING[best] if best else PRICING["default"]


def record(model: str, usage: dict) -> None:
    """Append one API call's usage to the ledger. Never raises into the caller."""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "model": model or "",
            "input": int(usage.get("input", 0) or 0),
            "cache_read": int(usage.get("cache_read", 0) or 0),
            "cache_write": int(usage.get("cache_write", 0) or 0),
            "output": int(usage.get("output", 0) or 0),
        }
        with LEDGER.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # ledger must never break the agent


def _cost_of(row: dict) -> float:
    r = _rates(row.get("model", ""))
    return (
        row.get("input", 0)       * r["input"]       +
        row.get("cache_read", 0)  * r["cache_read"]  +
        row.get("cache_write", 0) * r["cache_write"] +
        row.get("output", 0)      * r["output"]
    ) / 1_000_000.0


def summary(today: datetime.date | None = None) -> dict:
    """Return {'day': $, 'mtd': $, 'calls_today': N} estimated spend."""
    today = today or datetime.date.today()
    month_prefix = today.strftime("%Y-%m")
    day_str = today.isoformat()
    day = mtd = 0.0
    calls_today = 0
    if not LEDGER.exists():
        return {"day": 0.0, "mtd": 0.0, "calls_today": 0}
    for line in LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts = row.get("ts", "")
        c = _cost_of(row)
        if ts.startswith(month_prefix):
            mtd += c
        if ts.startswith(day_str):
            day += c
            calls_today += 1
    return {"day": round(day, 4), "mtd": round(mtd, 2), "calls_today": calls_today}


def report_line() -> str:
    """One-line human string for the morning report — per-provider breakdown."""
    import collections
    today = datetime.date.today()
    day_str = today.isoformat()
    providers = collections.defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0})
    total_calls = 0
    total_cost = 0.0
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not row.get("ts", "").startswith(day_str):
                continue
            total_calls += 1
            m = row.get("model", "unknown")
            # Map model → provider brand
            m_l = m.lower()
            if "claude" in m_l:
                prov = "Anthropic"
            elif "gemini" in m_l:
                prov = "Gemini"
            elif "gpt" in m_l or "openai" in m_l:
                prov = "OpenAI"
            elif "deepseek" in m_l:
                prov = "Nebius"
            else:
                prov = m  # fallback: use model ID
            c = _cost_of(row)
            providers[prov]["input"] += row.get("input", 0)
            providers[prov]["output"] += row.get("output", 0)
            providers[prov]["cost"] += c
            total_cost += c
    parts = []
    for prov in sorted(providers.keys()):
        p = providers[prov]
        parts.append(f"{prov} ${p['cost']:.2f} ({p['input']//1000}K in)")
    s = summary()
    base = f"💸 Today: ${total_cost:.2f} across {total_calls} calls"
    if parts:
        base += " · " + " | ".join(parts)
    base += f" · MTD ${s['mtd']:.2f}"
    return base


if __name__ == "__main__":
    print(report_line())
