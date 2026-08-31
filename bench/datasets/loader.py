"""bench.datasets.loader — the common shape every benchmark adapter targets.

Generic download + parse plumbing, no model spend. A dataset adapter (e.g.
longmemeval.py) converts a benchmark's own JSON schema into these dataclasses;
runner.py only ever sees this common shape, so a second dataset (LoCoMo etc.)
slots in later without touching the runner.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = Path(os.environ.get("BENCH_DATA_DIR", "bench/data"))

# LongMemEval is hosted on HuggingFace (xiaowu0162/longmemeval-cleaned), the
# official re-release referenced by the repo's own README (2025/09 cleanup).
HF_BASE = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main"
KNOWN_FILES = {
    "longmemeval_oracle": "longmemeval_oracle.json",   # only evidence sessions — cheapest, correctness-first
    "longmemeval_s": "longmemeval_s_cleaned.json",     # ~40 sessions / ~115k tokens per history
    "longmemeval_m": "longmemeval_m_cleaned.json",     # ~500 sessions per history — the real spend
}


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str
    has_answer: bool = False  # LongMemEval's turn-level evidence label


@dataclass
class Session:
    session_id: str
    timestamp: str | None
    turns: list[Turn] = field(default_factory=list)


@dataclass
class Question:
    question_id: str
    question: str
    answer: str
    question_type: str
    question_date: str | None
    is_abstention: bool
    haystack_session_ids: list[str] = field(default_factory=list)
    haystack_dates: list[str] = field(default_factory=list)
    answer_session_ids: list[str] = field(default_factory=list)  # session-level evidence label


@dataclass
class ChatHistoryItem:
    """One benchmark instance: a question plus the full session haystack it
    must be answered against, in the order they should be ingested."""
    question: Question
    sessions: list[Session] = field(default_factory=list)


@dataclass
class Dataset:
    name: str
    items: list[ChatHistoryItem] = field(default_factory=list)


def ensure_downloaded(variant: str, data_dir: Path | None = None) -> Path:
    """Download the named LongMemEval variant into data_dir if not already
    present. Never auto-invoked on import — always an explicit call. Network
    + disk only; no model spend."""
    if variant not in KNOWN_FILES:
        raise ValueError(f"unknown LongMemEval variant: {variant!r} (known: {list(KNOWN_FILES)})")
    data_dir = data_dir or DEFAULT_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / KNOWN_FILES[variant]
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = f"{HF_BASE}/{KNOWN_FILES[variant]}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    return dest


def load_raw(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)
