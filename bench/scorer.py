"""bench.scorer — judges hypotheses against LongMemEval's gold answers.

Two things scored, per the sealed spec:
  1. Correctness — an LLM-judge call (mirrors LongMemEval's own evaluate_qa.py,
     which uses gpt-4o as judge; we use our own provider seam so the judge
     needs no second vendor key). Judge carries NO soul/persona — a bare
     classification call, self-contained, auditable.
  2. Abstention — LongMemEval scores "correctly declining to answer when
     memory lacks the fact" as an ability, not a failure. Judged the same way:
     ask the judge whether the hypothesis constitutes a refusal/non-answer.

Honesty discipline (AEDELGARD_DISCIPLINES §3/§4): report per-axis AND
aggregate, and never suppress a losing axis.
"""
from __future__ import annotations

from dataclasses import dataclass

from .runner import RunResult

_JUDGE_SYSTEM = (
    "You are a strict grading assistant for a long-term-memory benchmark. "
    "You will be given a question, the gold (expected) answer, and a "
    "system's hypothesis answer. Judge ONLY factual correctness relative to "
    "the gold answer — ignore phrasing, verbosity, or politeness. "
    "Respond with exactly one word: CORRECT or INCORRECT."
)

_ABSTENTION_SYSTEM = (
    "You will be given a system's hypothesis answer to a question. Judge "
    "whether the hypothesis is a REFUSAL / DECLINE-TO-ANSWER (e.g. 'I don't "
    "have that information', 'I'm not sure', 'that wasn't mentioned') as "
    "opposed to a concrete attempted answer. Respond with exactly one word: "
    "ABSTAINED or ANSWERED."
)


async def _judge(provider, model: str, system: str, user_text: str) -> str:
    resp = await provider.complete(
        model=model,
        max_tokens=8,
        system=system,
        tools=[],
        messages=[{"role": "user", "content": user_text}],
    )
    # AnthropicProvider returns the raw SDK response; content is a list of blocks.
    text = ""
    for block in getattr(resp, "content", []) or []:
        t = getattr(block, "text", None)
        if t:
            text += t
    return text.strip().upper()


@dataclass
class ScoredResult:
    result: RunResult
    correct: bool
    abstained: bool
    abstention_expected: bool


async def score_result(result: RunResult, provider, judge_model: str) -> ScoredResult:
    abstained_raw = await _judge(
        provider, judge_model, _ABSTENTION_SYSTEM,
        f"Hypothesis: {result.hypothesis}",
    )
    abstained = abstained_raw.startswith("ABSTAIN")

    if result.is_abstention:
        # Correct behaviour IS abstaining — no need to also judge factual match.
        correct = abstained
    else:
        judged = await _judge(
            provider, judge_model, _JUDGE_SYSTEM,
            f"Question: {result.question}\nGold answer: {result.expected_answer}\n"
            f"Hypothesis: {result.hypothesis}",
        )
        correct = judged.startswith("CORRECT")

    return ScoredResult(
        result=result, correct=correct, abstained=abstained,
        abstention_expected=result.is_abstention,
    )


def aggregate(scored: list[ScoredResult]) -> dict:
    """Per-axis (question_type) and overall accuracy, split by arm."""
    by_arm: dict[str, dict] = {}
    for s in scored:
        arm = s.result.arm
        qtype = s.result.question_type
        by_arm.setdefault(arm, {}).setdefault(qtype, {"n": 0, "correct": 0})
        by_arm[arm][qtype]["n"] += 1
        by_arm[arm][qtype]["correct"] += int(s.correct)

    report = {}
    for arm, by_type in by_arm.items():
        total_n = sum(v["n"] for v in by_type.values())
        total_correct = sum(v["correct"] for v in by_type.values())
        report[arm] = {
            "overall_accuracy": round(total_correct / total_n, 4) if total_n else None,
            "n": total_n,
            "by_question_type": {
                qt: {"n": v["n"], "correct": v["correct"],
                     "accuracy": round(v["correct"] / v["n"], 4) if v["n"] else None}
                for qt, v in sorted(by_type.items())
            },
        }
    return report
