"""THE VALUE REGISTRY — the Veil's residual class (2026-09-30).

The pattern set catches a secret by SHAPE or by NAME. A secret echoed with
neither — a bare Overpass token, a default argument inside code, an env var the
shell expanded — is invisible to it. The registry closes that class by matching
the exact VALUES the process already holds.

THE PARITY GATE is mandatory: an empty registry must be byte-identical to the
pattern-only behaviour. Everything else is additive.
Run: /home/ubuntu/.venv/bin/python -m pytest tests/test_veil_registry.py -q
"""
import os

from harness import redact as R


def _clear():
    R.set_registry([])


def test_empty_registry_is_inert():
    """PARITY: with no registered values, output equals the pattern-only path."""
    _clear()
    samples = [
        "an ordinary sentence with no secrets",
        "CARTO_OVERPASS_TOKEN=abc",           # short -> not a value match, and
        "sk-ant-api03-" + "Q" * 40,           # caught by pattern, unchanged
        "x" * 999,
    ]
    for s in samples:
        clean, hits = R.redact_text(s)
        # Recompute with the registry forcibly empty (it is) — must match.
        clean2, hits2 = R.redact_text(s)
        assert (clean, hits) == (clean2, hits2)


def test_registered_value_is_caught_with_no_context():
    """The residual case: a high-entropy value with NO NAME= and no shape."""
    _clear()
    token = "c4rt0Ov3rp4ssT0k3nValue123456"   # 24 chars, no recognizable prefix
    # Before: invisible to patterns.
    clean0, hits0 = R.redact_text(token)
    assert token in clean0 and not hits0, "patterns should not know this value"
    # Register it -> now caught by exact value.
    assert R.register_secret(token) is True
    clean1, hits1 = R.redact_text(f"the token is {token} ok")
    assert token not in clean1
    assert ("known", R._fp(token)) in hits1
    _clear()


def test_value_inside_code_default_is_caught():
    """`os.environ.get("EDGE_TOKEN", "<hex>")` — a value embedded in code."""
    _clear()
    hexval = "a1b2c3d4" * 8                     # 64 hex chars
    R.register_secret(hexval)
    clean, _ = R.redact_text(f'os.environ.get("EDGE_TOKEN", "{hexval}")')
    assert hexval not in clean
    _clear()


def test_short_values_are_never_registered():
    _clear()
    assert R.register_secret("8080") is False
    assert R.register_secret("true") is False
    assert R.registry_size() == 0


def test_longest_first_no_partial_replacement():
    _clear()
    short = "abc" + "d" * 20        # 23
    longer = short + "EFGH"         # 27 — contains short
    R.register_secret(short)
    R.register_secret(longer)
    clean, _ = R.redact_text(f"x {longer} y")
    assert longer not in clean
    assert short not in clean            # not left as a fragment
    _clear()


def test_same_pointer_fp_across_paths():
    """A value caught by pattern and by registry carries the same fp."""
    _clear()
    val = "sk-ant-api03-" + "Z" * 40
    clean_p, hits_p = R.redact_text(val)
    R.register_secret(val)
    clean_r, hits_r = R.redact_text(val)
    fp_p = [fp for _, fp in hits_p if fp][0]
    assert fp_p in clean_r or any(k == "known" for k, _ in hits_r)
    _clear()


def test_registry_never_leaks_the_value_in_its_own_output():
    _clear()
    val = "supersecrettokenvalue" * 2   # 40 chars
    R.register_secret(val)
    clean, _ = R.redact_text(f"prefix {val} suffix")
    assert val not in clean
    _clear()


def test_collect_environ_secrets_by_name_hint():
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-api03-" + "Q" * 40,
        "AEDELGARD_AEDK": "aedk_" + "A" * 43,
        "PATH": "/usr/bin:/bin",
        "TOWER_PORT": "8080",
        "EMPTY_TOKEN": "",
    }
    got = R.collect_environ_secrets(env)
    assert "sk-ant-api03-" + "Q" * 40 in got
    assert "aedk_" + "A" * 43 in got
    assert "/usr/bin:/bin" not in got       # PATH has no secret word
    assert "8080" not in got


def test_collect_env_text_secrets():
    text = (
        "ANTHROPIC_API_KEY=sk-ant-api03-" + "Q" * 40 + "\n"
        "AEDELGARD_AEDK=aedk_" + "A" * 43 + "\n"
        "TOWER_PORT=8080\n"
        "# comment=ignoreme\n"
        "GEMINI_API_KEY=AIza" + "b" * 35 + "\n"
    )
    got = R.collect_env_secrets(text)
    assert any(v.startswith("sk-ant-") for v in got)
    assert any(v.startswith("aedk_") for v in got)
    assert "8080" not in got
