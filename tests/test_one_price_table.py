"""One price table: Discord's /status costs come from harness.cost_ledger, so
the two places that price a call can never disagree. And the built-in default
model is a current one."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import cost_ledger


def test_discord_prices_come_from_the_ledger():
    from discord_bot import bot
    assert not hasattr(bot, "MODEL_PRICING_USD_PER_MTOK")
    usage = {"input": 1_000_000, "cache_read": 0, "cache_write": 0, "output": 1_000_000}
    for model in ("claude-sonnet-5", "gemini-3.1-pro-preview", "deepseek-ai/DeepSeek-V4.1-Flash", "unknown-x"):
        r = cost_ledger._rates(model)
        actual, no_cache, pct = bot._price_call(usage, model)
        assert abs(actual - (r["input"] + r["output"])) < 1e-9


def test_cache_savings_still_computed():
    from discord_bot import bot
    usage = {"input": 0, "cache_read": 1_000_000, "cache_write": 0, "output": 0}
    actual, no_cache, pct = bot._price_call(usage, "claude-sonnet-5")
    r = cost_ledger._rates("claude-sonnet-5")
    assert abs(actual - r["cache_read"]) < 1e-9
    assert abs(no_cache - r["input"]) < 1e-9
    assert pct > 0


def test_malformed_usage_is_zero():
    from discord_bot import bot
    assert bot._price_call(None, "claude-sonnet-5") == (0.0, 0.0, 0.0)


def test_default_model_is_current():
    from types import SimpleNamespace
    from harness.agent import _initial_model
    assert _initial_model(None, None, SimpleNamespace(name="anthropic")) == "claude-sonnet-5"
