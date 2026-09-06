"""Context windows must be provider-aware.

A Gemini brain used to fall through to the 200k default -> a false
"context full" alarm and a premature history trim on a 1M-token model.
"""
import harness.agent as agent


def test_gemini_static_floor_is_one_million(monkeypatch):
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    monkeypatch.setattr(agent, "_DISCOVERED_CONTEXT_WINDOWS", {})
    assert agent._resolve_context_window("gemini-3.8-flash") >= 1_000_000
    assert agent._resolve_context_window("GEMINI-2.5-PRO") >= 1_000_000


def test_discovered_beats_static(monkeypatch):
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    monkeypatch.setattr(agent, "_DISCOVERED_CONTEXT_WINDOWS", {"gemini-3.8-flash": 42})
    assert agent._resolve_context_window("gemini-3.8-flash") == 42


def test_discovery_swallows_every_failure(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(agent, "_DISCOVERED_CONTEXT_WINDOWS", {})
    monkeypatch.setattr(agent, "_DISCOVERY_DONE", False)
    agent._run_model_discovery("")
    assert agent._DISCOVERY_DONE is True
    assert agent._resolve_context_window("gemini-3.8-flash") >= 1_000_000


def test_gemini_http_error_carries_status_for_the_ladder():
    """The fallback ladder decides by status_code; a bare RuntimeError would
    be judged a bug and never step down on a Gemini 429/503."""
    import inspect
    from harness import providers
    src = inspect.getsource(providers.GeminiProvider.complete)
    assert "exc.status_code = r.status_code" in src
