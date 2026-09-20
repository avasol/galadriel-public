"""THE GRAFT — a scar drafts its companion skill via a naked prompt.

Drafting is automatic; BLESSING never is. The draft lands as skills/DRAFT_*.md
with a provenance header and never self-installs.
"""

import os
import textwrap
from pathlib import Path

import pytest

from harness import scars, graft

SEED = textwrap.dedent("""\
    # SCAR TISSUE — test header

    <!-- scars:begin -->
    - [S001 | promoted 2026-07-01 | bearing: aedelgard] Before trusting exit codes → verify the artifact.
    <!-- scars:end -->
    """)


@pytest.fixture
def graft_env(tmp_path, monkeypatch):
    f = tmp_path / "SCARS.md"
    f.write_text(SEED, encoding="utf-8")
    mem = tmp_path / "memory"
    skills = tmp_path / "skills"
    monkeypatch.setenv("SCARS_FILE", str(f))
    monkeypatch.setenv("MEMORY_DIR", str(mem))
    monkeypatch.setenv("SKILLS_DIR", str(skills))
    return skills


def _fake_draft(system, user):
    # asserts the naked prompt carries NO soul/memory context — only the scar
    assert "SCAR S001" in user
    return "# Skill: verify-exit-codes — v0.1 (GRAFT DRAFT)\n\n## When to use\n..."


def test_graft_writes_draft_with_provenance(graft_env):
    skills = graft_env
    out = graft.graft("S001", _draft_fn=_fake_draft)
    assert out.startswith("Grafted S001")
    drafts = list(skills.glob("DRAFT_*.md"))
    assert len(drafts) == 1
    body = drafts[0].read_text()
    assert "GRAFT provenance: scar S001" in body
    assert "by naked prompt" in body
    assert "# Skill:" in body


def test_graft_unknown_id_fails_cleanly(graft_env):
    out = graft.graft("S999", _draft_fn=_fake_draft)
    assert out.startswith("FAILED: no scar")


def test_graft_malformed_draft_writes_nothing(graft_env):
    skills = graft_env
    out = graft.graft("S001", _draft_fn=lambda s, u: "not markdown")
    assert out.startswith("FAILED")
    assert list(skills.glob("DRAFT_*.md")) == []


def test_graft_never_self_installs(graft_env):
    """The draft must carry the DRAFT_ prefix — blessing is manual."""
    skills = graft_env
    graft.graft("S001", _draft_fn=_fake_draft)
    active = [p.name for p in skills.glob("*.md") if not p.name.startswith("DRAFT_")]
    assert active == [], "a graft must never install an active skill on its own"
