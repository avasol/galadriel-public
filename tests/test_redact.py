"""THE VEIL — secrets are redacted from tool results before they enter history."""
import hashlib
from harness.redact import redact_text, redact_secrets, find_secrets

# Synthetic secrets — shaped like the real formats, never real.
GH_PAT = "github_pat_11ABCDEFG0" + "a" * 60
GH_CLASSIC = "ghp_" + "A1b2C3d4" * 5          # 40 chars after the prefix
ANT = "sk-ant-api03-" + "Q" * 40
XI = "sk_" + "0123456789abcdef" * 3            # 48 hex
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"             # 20 chars total
JWT = ("eyJ" + "A" * 20 + "." + "eyJ" + "B" * 20 + "." + "C" * 20)


def fp(s):
    return hashlib.sha256(s.encode()).hexdigest()[:6]


def test_github_fine_grained_pat_in_git_credentials_line():
    line = f"https://avasol:{GH_PAT}@github.com\n"
    clean, hits = redact_text(line)
    assert GH_PAT not in clean
    assert clean == f"https://avasol:<REDACTED:github_pat:{fp(GH_PAT)}>@github.com\n"
    assert hits == [("github_pat", fp(GH_PAT))]


def test_same_secret_same_marker_across_texts():
    a, _ = redact_text(f"remote {GH_PAT}")
    b, _ = redact_text(f"cred {GH_PAT} end")
    assert a.split()[-1] == b.split()[1]      # identical fingerprint marker


def test_multiple_kinds_in_one_blob():
    blob = f"{GH_CLASSIC} {ANT} {XI} {AKIA} {JWT}"
    clean, hits = redact_text(blob)
    for s in (GH_CLASSIC, ANT, XI, AKIA, JWT):
        assert s not in clean
    kinds = {k for k, _ in hits}
    assert {"github_token", "anthropic", "elevenlabs", "aws_access_key", "jwt"} <= kinds


def test_env_dump_keeps_names_redacts_values():
    env = ("MODEL=claude-sonnet-5\n"
           "EDGE_TOKEN=abcdef0123456789XYZ\n"
           "DAILY_COST_LIMIT=100\n")
    clean, hits = redact_text(env)
    assert "MODEL=claude-sonnet-5" in clean
    assert "DAILY_COST_LIMIT=100" in clean
    assert "abcdef0123456789XYZ" not in clean
    assert "EDGE_TOKEN=<REDACTED:env_value:" in clean
    assert len(hits) >= 1


def test_placeholders_are_not_redacted():
    txt = "API_KEY=<REDACTED:env_value:abc123>\nTOKEN=your_token_here"
    clean, hits = redact_text(txt)
    assert clean == txt or "<REDACTED:env_value:abc123>" in clean


def test_redact_secrets_handles_str_and_block_list():
    # str shape
    clean, hits = redact_secrets(f"key is {ANT}")
    assert ANT not in clean and hits
    # block-list shape: only text blocks are touched
    blocks = [{"type": "text", "text": f"secret {ANT}"},
              {"type": "image", "source": {"data": "AAAA"}}]
    clean2, hits2 = redact_secrets(blocks)
    assert ANT not in clean2[0]["text"]
    assert clean2[1] == blocks[1]  # non-text block untouched
    assert hits2


def test_find_secrets_reports_without_mutating():
    txt = f"a {ANT} b"
    assert ("anthropic", ANT) in find_secrets(txt)
    assert txt.endswith(" b")  # unchanged


# ── Reviewer findings ────────────────────────────────────────
# Reproduced by her against a live body: AEDELGARD_AEDK was missed in EVERY
# shape because the name carries none of the env_value keywords; and a
# CARTO_OVERPASS_TOKEN echoed bare, or behind a `grep -n`/`Select-String`
# `path:line:` prefix, slipped the `^` anchor. The key is the ONE secret a body
# most needs veiled — it unlocks the mind and its keyring.

AEDK = "aedk_" + "A" * 43          # the real mint shape (48 chars)
GRK = "grk_" + "B" * 43            # admin-issued form
CARTO = "c4rt0Ov3rp4ssT0k3nValue123456"


def test_aedelgard_key_caught_in_every_shape():
    """The mind's own key must never survive any of the shapes it leaks in."""
    shapes = [
        f"AEDELGARD_AEDK={AEDK}",                    # env line
        AEDK,                                        # bare, in prose or a paste
        f".env:12:AEDELGARD_AEDK={AEDK}",            # Select-String
        f"tower/app.py:3110:    AEDELGARD_AEDK={AEDK}",  # grep -n
        f"my key is {AEDK} — keep it safe",          # embedded in a sentence
        f"AEDELGARD_AEDK={GRK}",                     # admin-issued grk_
    ]
    for text in shapes:
        clean, hits = redact_text(text)
        assert AEDK not in clean and GRK not in clean, f"leaked in: {text!r}"
        assert hits, f"nothing redacted in: {text!r}"


def test_aedelgard_key_value_matches_regardless_of_name():
    """The value rule must fire even when the NAME gives no hint."""
    clean, hits = redact_text(AEDK)
    assert clean == f"<REDACTED:aedelgard_key:{fp(AEDK)}>"
    assert hits == [("aedelgard_key", fp(AEDK))]


def test_env_value_survives_a_path_line_prefix():
    """`grep -n` / Select-String output must not defeat the env_value rule.

    The old `^`-anchored pattern only matched at true line start, so the very
    shape a repo-wide secret sweep produces — `file:line:NAME=value` — was the
    one it missed. Anchor now accepts a leading path:line: or whitespace."""
    for prefix in ("", ".env:7:", "scripts/fetch.sh:3:export ", "  "):
        clean, hits = redact_text(f"{prefix}CARTO_OVERPASS_TOKEN={CARTO}")
        assert CARTO not in clean, f"leaked behind prefix {prefix!r}"
        assert ("env_value", fp(CARTO)) in hits


def test_env_dump_shape_is_preserved():
    """The name must survive so `cat .env` stays readable (regression guard)."""
    clean, _ = redact_text(f"CARTO_OVERPASS_TOKEN={CARTO}")
    assert clean.startswith("CARTO_OVERPASS_TOKEN=<REDACTED:env_value:")
