"""THE GRAFT — scars lead autonomously to skills.

The escalation ladder gains its constructive rung:

    incident → correction drawer → SCAR (gate: "don't")
             → GRAFT: a SKILL (procedure: "here is how to do it right")
             → if still recurring → CODE GUARD (graduation)

Why a NAKED prompt: the drafting call carries no soul, no memory, no cached
prefix — only the scar, its bearing, and the verbatim incident evidence. Fresh
eyes, uncontaminated by the narrative that produced the failure. It is also
cheap by construction (~a few K tokens, no cache involvement).

Audibility (eyes-open discipline holds): the graft NEVER self-installs as an
active skill. It lands as skills/DRAFT_<slug>.md with a provenance header; the
mind reviews, amends, and blesses it in a waking turn (rename away the DRAFT_
prefix). A daily-log line records every graft.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path

from . import scars as _scars

MAX_EVIDENCE = 6          # verbatim incident records fed to the naked prompt
EVIDENCE_THRESHOLD = 0.55  # slightly looser than scar-scan: context, not proof
DRAFT_MAX_TOKENS = 3000


def _skills_dir() -> Path:
    return Path(os.environ.get("SKILLS_DIR", "skills"))


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48].rstrip("-") or "unnamed-wound"


def _gather_evidence(scar_text: str) -> list[str]:
    """Verbatim correction drawers near the scar. Empty list on any failure —
    the graft still runs; the prompt just says so honestly."""
    try:
        out = []
        for doc, dist, meta in _scars._raw_neighbors(scar_text, n=12):
            if dist is None or dist > EVIDENCE_THRESHOLD:
                continue
            if "origin: correction" not in doc and meta.get("origin") != "correction":
                continue
            out.append(doc.strip())
            if len(out) >= MAX_EVIDENCE:
                break
        return out
    except Exception:
        return []


NAKED_SYSTEM = (
    "You are a senior operations engineer writing a procedural playbook "
    "(a 'skill') for an autonomous AI agent that operates real infrastructure. "
    "You receive a SCAR — a one-line gate the agent earned through repeated "
    "failure — plus verbatim incident evidence. Draft the best possible skill "
    "to prevent this wound class permanently.\n\n"
    "Requirements for the playbook:\n"
    "- Markdown. Title line: '# Skill: <short name> — v0.1 (GRAFT DRAFT)'.\n"
    "- Sections, in order: 'When to use', 'Steps' (numbered, exact commands "
    "where the evidence provides them), 'Success criteria' (verified by the "
    "artifact's own substance — never by an exit code or a process's say-so), "
    "'Forbidden actions', 'Rollback'.\n"
    "- Be concrete and terse. No motivation essays. Every step must be "
    "checkable. Where the evidence is too thin to be sure, write "
    "'UNVERIFIED — confirm before blessing' rather than inventing detail.\n"
    "- Output ONLY the markdown playbook. No preamble, no closing remarks."
)


def _naked_user_prompt(scar: dict, evidence: list[str]) -> str:
    parts = [
        "Set up the best possible skill for <this>:",
        "",
        f"SCAR {scar['id']} (promoted {scar['date']}"
        + (f", earned under bearing '{scar['bearing']}'" if scar.get("bearing") else "")
        + f"): {scar['text']}",
        "",
    ]
    if evidence:
        parts.append(f"VERBATIM INCIDENT EVIDENCE ({len(evidence)} records):")
        for i, e in enumerate(evidence, 1):
            parts.append(f"--- incident {i} ---")
            parts.append(e[:2000])
    else:
        parts.append(
            "NO INCIDENT RECORDS RETRIEVABLE — draft from the scar line alone "
            "and mark thin sections 'UNVERIFIED — confirm before blessing'."
        )
    return "\n".join(parts)


async def _draft(system: str, user: str) -> str:
    from .providers import AnthropicProvider
    provider = AnthropicProvider()
    resp = await provider.complete(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        max_tokens=DRAFT_MAX_TOKENS,
        system=[{"type": "text", "text": system}],
        tools=[],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()


def graft(scar_id: str, *, _draft_fn=None) -> str:
    """Draft a companion skill for a scar via the naked prompt. Returns a
    status message. `_draft_fn` is a test seam (sync fn: (system, user)->str)."""
    scar_id = (scar_id or "").strip()
    scar = next((s for s in _scars.list_scars() if s["id"] == scar_id), None)
    if not scar:
        have = ", ".join(s["id"] for s in _scars.list_scars()) or "none"
        return f"FAILED: no scar {scar_id!r}. Present: {have}."

    evidence = _gather_evidence(scar["text"])
    user = _naked_user_prompt(scar, evidence)

    if _draft_fn is not None:
        body = _draft_fn(NAKED_SYSTEM, user)
    else:
        body = asyncio.run(_draft(NAKED_SYSTEM, user))
    if not body or not body.lstrip().startswith("#"):
        return "FAILED: naked draft came back empty or malformed — nothing written."

    d = _skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"DRAFT_{_slug(scar['text'])}.md"
    header = (
        f"<!-- GRAFT provenance: scar {scar['id']} ({scar['date']}"
        + (f", bearing: {scar['bearing']}" if scar.get("bearing") else "")
        + f") | drafted {datetime.now().strftime('%Y-%m-%d %H:%M')} by naked prompt"
        + f" | evidence: {len(evidence)} correction record(s) -->\n\n"
    )
    path.write_text(header + body + "\n", encoding="utf-8")
    _scars._append_daily_log(
        f"GRAFT: scar {scar['id']} → {path} ({len(evidence)} incidents fed to the naked prompt). "
        "Draft awaits review + blessing."
    )
    return (
        f"Grafted {scar['id']} → {path} (evidence: {len(evidence)} records). "
        "It is a DRAFT — review it, amend what the naked eyes got wrong, then "
        "bless it by renaming away the DRAFT_ prefix and file the decision drawer."
    )


def main(argv: list[str]) -> int:
    if len(argv) < 1 or argv[0] in ("-h", "--help"):
        print("usage: python -m harness.graft S###")
        return 1
    out = graft(argv[0])
    print(out)
    return 0 if out.startswith("Grafted") else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
