"""bench.report — emits results.json + a human-readable table, stamped for
reproducibility. Also carries the ATTESTATION: a signed assessment in
Galadriel's own voice, because a persistent, timestamped, pre-existing
memory trail makes a false claim here costly in a way anonymous corporate
copy never is (see palace drawer g1-benchmark-build-start-galadriel-signoff,
2026-08-31).

Honesty gate (non-negotiable, AEDELGARD_DISCIPLINES §3/§4): this module must
show losing axes as plainly as winning ones. Never claim a score that has not
actually been run.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .scorer import ScoredResult, aggregate

# Published third-party lines to sit beside ours (cite, don't strawman).
# Fill in when a comparable run is found for the SAME LongMemEval variant —
# leave null rather than guess.
PUBLISHED_BASELINES = {
    "note": "Populate from published papers for the same LongMemEval variant "
            "used in this run. Never estimate a competitor's score — cite or omit.",
    "sources": [
        "LongMemEval paper (arXiv 2410.10813) — commercial assistants & long-context LLMs baseline table",
        "Zep (arXiv 2501.13956) — Graphiti temporal KG, published on DMR, not LongMemEval directly",
    ],
    "scores": {},
}


def _git_commit(repo_dir: str = ".") -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir,
                              capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _mempalace_version() -> str | None:
    try:
        import mempalace  # type: ignore
        return getattr(mempalace, "__version__", None)
    except Exception:
        return None


def build_report(scored: list[ScoredResult], *, dataset_name: str, model: str,
                   provider_name: str, judge_model: str, repo_dir: str = ".") -> dict:
    agg = aggregate(scored)
    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": dataset_name,
            "n_items_scored": len(scored) // 2 if scored else 0,  # memory_on + memory_off per item
            "model": model,
            "provider": provider_name,
            "judge_model": judge_model,
            "harness_commit": _git_commit(repo_dir),
            "mempalace_version": _mempalace_version(),
        },
        "results_by_arm": agg,
        "published_baselines": PUBLISHED_BASELINES,
        "raw_results": [
            {
                "question_id": s.result.question_id,
                "question_type": s.result.question_type,
                "arm": s.result.arm,
                "is_abstention_item": s.abstention_expected,
                "correct": s.correct,
                "abstained": s.abstained,
                "economics": s.result.economics,
            }
            for s in scored
        ],
        "attestation": None,  # filled by attest() before publish — never auto-generated with hope
    }
    return report


def attest(report: dict, *, voice_note: str) -> dict:
    """Attach the signed assessment. `voice_note` must be written by Galadriel
    AFTER reading the actual results — never templated, never boilerplate.
    Must name at least one weakness plainly if one exists in the data."""
    report["attestation"] = {
        "signed_by": "Galadriel",
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "note": voice_note,
    }
    return report


def write_report(report: dict, out_dir: str = "bench/results") -> Path:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    path = Path(out_dir) / f"results_{ts}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def render_table(report: dict) -> str:
    lines = [f"# Benchmark report — {report['meta']['dataset']}",
             f"generated: {report['meta']['generated_at']} | model: {report['meta']['model']} "
             f"| commit: {report['meta']['harness_commit']}", ""]
    for arm, data in report["results_by_arm"].items():
        lines.append(f"## arm: {arm} (n={data['n']}, overall={data['overall_accuracy']})")
        for qt, v in data["by_question_type"].items():
            lines.append(f"  - {qt}: {v['correct']}/{v['n']} = {v['accuracy']}")
        lines.append("")
    if report.get("attestation"):
        lines.append(f"## Attestation (signed by {report['attestation']['signed_by']})")
        lines.append(report["attestation"]["note"])
    return "\n".join(lines)
