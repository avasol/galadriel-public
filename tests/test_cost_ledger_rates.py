"""Pin the vendor rates the spend ledger mirrors (Opus 5.5: $4/$20, 5-minute
cache write $5, cache read $0.20 — the vendor pricing page, re-read 2026-10-05)."""
from harness import cost_ledger as c


def test_opus_5_5_rates():
    assert c._rates("claude-opus-5-5") == {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20}


def test_opus_5_keeps_its_own_rates():
    assert c._rates("claude-opus-5")["input"] == 5.0
