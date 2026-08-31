"""bench.datasets.longmemeval — adapter: LongMemEval's own schema -> the
common Dataset/ChatHistoryItem shape (see loader.py).

Schema reference (LongMemEval README, xiaowu0162/LongMemEval, ICLR 2025):
  question_id, question_type, question, answer, question_date,
  haystack_session_ids, haystack_dates, haystack_sessions (list of turns,
  each {"role": user/assistant, "content": ..., "has_answer": bool}),
  answer_session_ids. question_id ending in "_abs" marks an abstention item
  (the model should recognise memory lacks the fact and decline to answer —
  a scored ability per the spec, not a failure mode).
"""
from __future__ import annotations

from pathlib import Path

from .loader import ChatHistoryItem, Dataset, Question, Session, Turn, ensure_downloaded, load_raw


def _adapt_item(raw: dict) -> ChatHistoryItem:
    qid = raw["question_id"]
    question = Question(
        question_id=qid,
        question=raw["question"],
        answer=raw["answer"],
        question_type=raw["question_type"],
        question_date=raw.get("question_date"),
        is_abstention=qid.endswith("_abs"),
        haystack_session_ids=raw.get("haystack_session_ids", []),
        haystack_dates=raw.get("haystack_dates", []),
        answer_session_ids=raw.get("answer_session_ids", []),
    )
    session_ids = raw.get("haystack_session_ids", [])
    dates = raw.get("haystack_dates", [])
    sessions: list[Session] = []
    for i, sess_turns in enumerate(raw.get("haystack_sessions", [])):
        sid = session_ids[i] if i < len(session_ids) else f"{qid}_sess{i}"
        ts = dates[i] if i < len(dates) else None
        turns = [
            Turn(role=t["role"], content=t["content"], has_answer=bool(t.get("has_answer", False)))
            for t in sess_turns
        ]
        sessions.append(Session(session_id=sid, timestamp=ts, turns=turns))
    return ChatHistoryItem(question=question, sessions=sessions)


def load(variant: str = "longmemeval_oracle", data_dir: Path | None = None, limit: int | None = None) -> Dataset:
    """Download (if needed) + parse a LongMemEval variant into the common
    shape. `limit` truncates to the first N instances — use it for cheap
    smoke tests; the full 500-question run is a separate, costed decision."""
    path = ensure_downloaded(variant, data_dir)
    raw_items = load_raw(path)
    if limit:
        raw_items = raw_items[:limit]
    items = [_adapt_item(r) for r in raw_items]
    return Dataset(name=f"longmemeval:{variant}", items=items)
