"""The provider-keyed context window map is LIVE (2026-09-30).

_PROVIDER_CONTEXT_WINDOWS was populated at two discovery sites and read
NOWHERE — a half-built seam (Altariel's max-tokens plan caught it; her "a
Bedrock row could leak onto a direct id" worry was already in the tree). These
tests pin the fix: the map is consulted, keyed on the NORMALIZED provider, and
strictly additive — a bare-id discovery value still wins, so nothing regresses.
Run: /home/ubuntu/.venv/bin/python -m pytest tests/test_provider_context_window.py -q
"""
from harness import agent as A


def test_bare_id_discovery_takes_priority(monkeypatch):
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    A._DISCOVERED_CONTEXT_WINDOWS["m1"] = 111
    A._PROVIDER_CONTEXT_WINDOWS[("anthropic", "m1")] = 999
    try:
        # bare-id discovery wins (precedence preserved)
        assert A._resolve_context_window("m1", "anthropic") == 111
    finally:
        A._DISCOVERED_CONTEXT_WINDOWS.pop("m1", None)
        A._PROVIDER_CONTEXT_WINDOWS.pop(("anthropic", "m1"), None)


def test_provider_map_is_consulted_when_bare_id_missing(monkeypatch):
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    A._PROVIDER_CONTEXT_WINDOWS[("anthropic", "m-only-provider")] = 333
    try:
        assert A._resolve_context_window("m-only-provider", "anthropic") == 333
    finally:
        A._PROVIDER_CONTEXT_WINDOWS.pop(("anthropic", "m-only-provider"), None)


def test_provider_label_is_normalized(monkeypatch):
    """Palantir says 'google'; the body says 'gemini'. The lookup must match."""
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    A._PROVIDER_CONTEXT_WINDOWS[("gemini", "g-model")] = 777
    try:
        assert A._resolve_context_window("g-model", "google") == 777
    finally:
        A._PROVIDER_CONTEXT_WINDOWS.pop(("gemini", "g-model"), None)


def test_no_provider_behaves_exactly_as_before(monkeypatch):
    """With no provider passed, the resolver is the old bare-id path."""
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    A._PROVIDER_CONTEXT_WINDOWS[("anthropic", "zzz")] = 5
    try:
        # no bare-id entry, no provider passed -> static/default floor
        assert A._resolve_context_window("zzz") == A.CONTEXT_WINDOW_DEFAULT
    finally:
        A._PROVIDER_CONTEXT_WINDOWS.pop(("anthropic", "zzz"), None)


def test_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW", "12345")
    A._PROVIDER_CONTEXT_WINDOWS[("anthropic", "w")] = 1
    try:
        assert A._resolve_context_window("w", "anthropic") == 12345
    finally:
        A._PROVIDER_CONTEXT_WINDOWS.pop(("anthropic", "w"), None)


def test_normalize_provider_alias():
    assert A._normalize_provider("google") == "gemini"
    assert A._normalize_provider("aedelgard") == "anthropic"
    assert A._normalize_provider("anthropic") == "anthropic"
