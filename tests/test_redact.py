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
