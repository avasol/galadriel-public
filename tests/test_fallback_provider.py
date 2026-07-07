"""The model-fallback ladder (FallbackProvider).

Under test — the covenant of the ladder:
1. PARITY: with AGENT_MODEL_FALLBACKS unset, make_provider returns the plain
   provider — the pre-ladder hot path, untouched.
2. Unavailability (404 closed model, 503 outage, connection death) steps down
   to the next rung, which is called with the RUNG's model, not the caller's.
3. Config/bug errors (401 bad key, 400 malformed, our own TypeError) NEVER
   fall back — they surface.
4. Demotion is sticky: the next call starts at the working rung.
5. The primary is re-probed after the retry window and promoted on recovery.
6. usage() delegates to whichever provider actually answered.
"""

import asyncio

import pytest

from harness.providers import (
    AnthropicProvider,
    FallbackProvider,
    _Rung,
    _is_fallback_worthy,
    _parse_fallback_chain,
    make_provider,
)


class _FakeStatusError(Exception):
    def __init__(self, status):
        super().__init__(f"http {status}")
        self.status_code = status


class _FakeProvider:
    """Scriptable rung: raises from `errors` until exhausted, then succeeds."""

    def __init__(self, name, errors=None):
        self.name = name
        self.errors = list(errors or [])
        self.calls = []          # (model,) per call

    async def complete(self, *, model, max_tokens, system, tools, messages):
        self.calls.append(model)
        if self.errors:
            raise self.errors.pop(0)
        return {"answered_by": self.name, "model": model}

    def usage(self, raw):
        return {"input": 1, "cache_read": 0, "cache_write": 0, "output": 1,
                "provider": self.name}


def _ladder(*providers, retry_primary_s=3600.0, models=None):
    models = models or [None] + [f"model-{p.name}" for p in providers[1:]]
    rungs = [_Rung(p.name, lambda p=p: p, m) for p, m in zip(providers, models)]
    return FallbackProvider(rungs, retry_primary_s=retry_primary_s)


def _call(fp, model="primary-model"):
    return asyncio.run(fp.complete(model=model, max_tokens=64, system=[],
                                   tools=[], messages=[]))


# ── 1. parity ──────────────────────────────────────────────────────────

def test_no_env_means_plain_provider(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_FALLBACKS", raising=False)
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    p = make_provider(anthropic_client=object())
    assert isinstance(p, AnthropicProvider)          # no ladder, no wrapper
    assert not isinstance(p, FallbackProvider)


def test_env_builds_ladder(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_FALLBACKS",
                       "claude-opus-4-8, gemini:gemini-3.1-pro")
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    p = make_provider(anthropic_client=object())
    assert isinstance(p, FallbackProvider)
    assert len(p._rungs) == 3
    assert p._rungs[1].model == "claude-opus-4-8"    # bare name → same provider
    assert p._rungs[2].label == "gemini:gemini-3.1-pro"


# ── 2. stepping down ───────────────────────────────────────────────────

def test_closed_model_steps_down_with_rung_model():
    a = _FakeProvider("primary", errors=[_FakeStatusError(404)])
    b = _FakeProvider("second")
    fp = _ladder(a, b)
    out = _call(fp)
    assert out["answered_by"] == "second"
    assert b.calls == ["model-second"]               # rung's own model pin
    assert a.calls == ["primary-model"]              # primary got caller's model


def test_outage_and_connection_death_step_down():
    a = _FakeProvider("primary", errors=[_FakeStatusError(503)])
    b = _FakeProvider("second", errors=[ConnectionError("refused")])
    c = _FakeProvider("third")
    out = _call(_ladder(a, b, c))
    assert out["answered_by"] == "third"


def test_all_rungs_dead_raises_last_error():
    a = _FakeProvider("primary", errors=[_FakeStatusError(503)])
    b = _FakeProvider("second", errors=[_FakeStatusError(529)])
    with pytest.raises(_FakeStatusError):
        _call(_ladder(a, b))


# ── 3. what must NEVER fall back ───────────────────────────────────────

def test_bad_key_surfaces_immediately():
    a = _FakeProvider("primary", errors=[_FakeStatusError(401)])
    b = _FakeProvider("second")
    with pytest.raises(_FakeStatusError):
        _call(_ladder(a, b))
    assert b.calls == []                             # never consulted


def test_our_own_bug_surfaces_immediately():
    a = _FakeProvider("primary", errors=[TypeError("shim bug")])
    b = _FakeProvider("second")
    with pytest.raises(TypeError):
        _call(_ladder(a, b))
    assert b.calls == []


def test_classification_table():
    assert _is_fallback_worthy(_FakeStatusError(404))
    assert _is_fallback_worthy(_FakeStatusError(429))
    assert _is_fallback_worthy(_FakeStatusError(529))
    assert _is_fallback_worthy(ConnectionError())
    assert _is_fallback_worthy(NotImplementedError())
    assert not _is_fallback_worthy(_FakeStatusError(400))
    assert not _is_fallback_worthy(_FakeStatusError(401))
    assert not _is_fallback_worthy(TypeError())
    assert not _is_fallback_worthy(KeyError())


# ── 4 + 5. stickiness and recovery ─────────────────────────────────────

def test_demotion_is_sticky():
    a = _FakeProvider("primary", errors=[_FakeStatusError(404)])
    b = _FakeProvider("second")
    fp = _ladder(a, b)
    _call(fp)
    _call(fp)                                        # second call
    assert len(a.calls) == 1                         # primary NOT re-tried
    assert len(b.calls) == 2
    assert fp.active_label == "second"


def test_primary_reprobed_after_window_and_promoted():
    a = _FakeProvider("primary", errors=[_FakeStatusError(503)])
    b = _FakeProvider("second")
    fp = _ladder(a, b, retry_primary_s=0.0)          # window elapses instantly
    _call(fp)
    assert fp.active_label == "second"
    out = _call(fp)                                  # probe fires, primary healed
    assert out["answered_by"] == "primary"
    assert fp.active_label == "primary"
    assert fp._demoted_at is None


# ── 6. usage delegation + chain parsing ────────────────────────────────

def test_usage_delegates_to_answering_provider():
    a = _FakeProvider("primary", errors=[_FakeStatusError(404)])
    b = _FakeProvider("second")
    fp = _ladder(a, b)
    raw = _call(fp)
    assert fp.usage(raw)["provider"] == "second"


def test_dead_rung_construction_is_skipped():
    def boom():
        raise RuntimeError("no key for this provider")
    rungs = [_Rung("primary", boom, None),
             _Rung("second", lambda: _FakeProvider("second"), "m2")]
    out = _call(FallbackProvider(rungs))
    assert out["answered_by"] == "second"


def test_parse_chain():
    got = _parse_fallback_chain("claude-opus-4-8, gemini:gemini-3.1-pro,, ",
                                "anthropic")
    assert got == [("anthropic", "claude-opus-4-8"),
                   ("gemini", "gemini-3.1-pro")]
