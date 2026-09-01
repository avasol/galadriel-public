"""bench.runner — drives a fresh, ephemeral GaladrielAgent over a benchmark
chat history, then asks its questions. Never touches a real mind: every item
gets its own MEMPALACE_PATH tmpdir, torn down after.

METHODOLOGY (the design decision this module encodes — read before trusting
a result): each haystack session is archived+mined through the SAME code
path the live harness uses when a real conversation ends (`palace.
archive_conversation` -> `mine_batch_dir`), not replayed turn-by-turn through
`agent.respond()`. Two reasons:
  1. The dataset's assistant turns are canned ground truth: making the agent
     regenerate its own replies would substitute real evidence phrasing for
     invented phrasing, contaminating the test.
  2. This is the actual production path a real conversation's facts take to
     become palace-searchable (drawers + KG), so the query phase genuinely
     exercises retrieval quality, not a raw-scrollback long-context read
     (which would conflate our memory system with a long-context baseline —
     a different, already-published comparison LongMemEval itself measures).

Control arm (`GALADRIEL_NO_PALACE=1`): no archiving happens at all; the
question is asked with zero prior context. This isolates what memory
contributes and should score near-abstention for most fact-bearing questions
— if it doesn't, something in the harness is leaking context and the result
is not trustworthy.

SANDBOX (`GALADRIEL_BENCH_SANDBOX=1`, both arms): filesystem/shell tools
(run_shell/read_file/write_file) are removed from the tool set entirely —
found live 2026-09-01 that a benchmark question can trigger the agent's own
"be resourceful" instinct into `run_shell("grep -r <fact> /")`, and cwd-based
`working_dir` does NOT contain an absolute-path command. A LongMemEval
question has no legitimate need for real files, so the capability is
removed rather than path-restricted (an absolute-path jail is easy to
defeat and hard to verify airtight; removing the tool class is neither).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

from .datasets.loader import ChatHistoryItem, Dataset, Session
from .economics import EconomicsTracker


@dataclass
class RunResult:
    question_id: str
    question: str
    expected_answer: str
    hypothesis: str
    question_type: str
    is_abstention: bool
    arm: str  # "memory_on" | "memory_off"
    economics: dict = field(default_factory=dict)
    cost_curve: list = field(default_factory=list)


def _session_to_messages(session: Session) -> list[dict]:
    """Render a LongMemEval session as Anthropic-format messages, timestamped,
    exactly as given — no regeneration."""
    return [{"role": t.role, "content": t.content} for t in session.turns]


async def _run_one_item(item: ChatHistoryItem, *, memory_on: bool, model: str | None,
                          working_dir: str) -> RunResult:
    from harness.agent import GaladrielAgent  # local import: bench stays importable without a live env
    from harness import palace

    tmp_root = tempfile.mkdtemp(prefix="bench_")
    palace_path = os.path.join(tmp_root, "palace")
    memory_dir = os.path.join(tmp_root, "memory")
    sandbox_dir = os.path.join(tmp_root, "sandbox")
    os.makedirs(memory_dir, exist_ok=True)
    os.makedirs(sandbox_dir, exist_ok=True)

    env_backup = {}
    archive_root = os.path.join(tmp_root, "archive")
    overrides = {
        "MEMPALACE_PATH": palace_path,
        "PALACE_ARCHIVE_ROOT": archive_root,
        "GALADRIEL_NO_PALACE": "0" if memory_on else "1",
        # Least-privilege for an untrusted eval question (found live
        # 2026-09-01: the agent's own "be resourceful before asking"
        # instinct led it to `run_shell("grep -r <fact> /")` scanning the
        # box's real filesystem for a benchmark answer — cwd alone does not
        # sandbox absolute paths). A LongMemEval question has no legitimate
        # need for shell/file tools at all; remove the capability entirely
        # for BOTH arms, not just the control arm.
        "GALADRIEL_BENCH_SANDBOX": "1",
    }
    for k, v in overrides.items():
        env_backup[k] = os.environ.get(k)
        os.environ[k] = v

    tracker = EconomicsTracker()
    try:
        # working_dir is ALWAYS the ephemeral sandbox, never the caller's repo
        # checkout — a tool-cascade (run_shell/read_file/write_file) must not
        # be able to touch real files during a benchmark run. The `working_dir`
        # parameter is accepted for future use (e.g. seeding fixture files into
        # the sandbox) but is never passed through to the live agent as-is.
        agent = GaladrielAgent(model=model, memory_dir=memory_dir, working_dir=sandbox_dir)
        channel = f"bench-{item.question.question_id}"

        if memory_on:
            for i, session in enumerate(item.sessions):
                messages = _session_to_messages(session)
                if not messages:
                    continue
                batch_dir = palace.plan_archive_dir(f"{channel}-sess{i}")
                await palace.archive_conversation(f"{channel}-sess{i}", messages, batch_dir=batch_dir)

        hypothesis = await agent.respond(item.question.question, channel_id=channel)
        tracker.record(agent.last_usage)
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp_root, ignore_errors=True)

    return RunResult(
        question_id=item.question.question_id,
        question=item.question.question,
        expected_answer=item.question.answer,
        hypothesis=hypothesis,
        question_type=item.question.question_type,
        is_abstention=item.question.is_abstention,
        arm="memory_on" if memory_on else "memory_off",
        economics=tracker.totals(),
        cost_curve=tracker.cost_curve(),
    )


async def run_dataset(dataset: Dataset, *, model: str | None = None, working_dir: str = ".",
                        control_arm: bool = True) -> list[RunResult]:
    """Run every item in `dataset` memory-ON, and (if control_arm) again
    memory-OFF as the null baseline. Sequential by design — correctness-and-
    cost-first, not throughput-first. The full 500-question run is a
    separately costed decision, never a casual loop over this function."""
    results: list[RunResult] = []
    for item in dataset.items:
        results.append(await _run_one_item(item, memory_on=True, model=model, working_dir=working_dir))
        if control_arm:
            results.append(await _run_one_item(item, memory_on=False, model=model, working_dir=working_dir))
    return results
