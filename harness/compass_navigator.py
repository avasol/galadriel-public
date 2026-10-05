"""compass_navigator.py — autonomous heading selection.

After each completed turn, score the recent conversation against the available
visions and shift FOCUSED if the evidence is clear enough.

Design principles:
  - Conservative: only shift when the signal is unambiguous (score gap ≥ threshold)
  - Non-destructive: AMBIENT and DORMANT are never touched by auto-shift
  - Transparent: every auto-shift is logged; the caller surfaces it once in chat
  - Zero API cost: pure local pattern scoring

Patterns are derived from the visions themselves — the vision name plus any
optional `keywords` (regex strings) declared in config/visions/<vision>.json.
No heading names are hardcoded here.

Scoring:
  Each vision is scored over the last N turns (default 8) — recent turns
  weighted higher than older ones. The candidate with the highest weighted
  score wins. We only shift if:
    1. The winner score > MIN_WINNER_SCORE
    2. The winner score > current_focused_score * DOMINANCE_RATIO

  This means a conversation that's split evenly between topics will NOT shift.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("galadriel")

# ── Tuning knobs ─────────────────────────────────────────────────────────────

# Minimum weighted score to be considered at all
MIN_WINNER_SCORE: float = 3.0

# Winner must beat current focus by this multiplier (prevents jitter)
DOMINANCE_RATIO: float = 2.0

# How many recent turns to look back (user + assistant each count as one)
LOOKBACK_TURNS: int = 8


def _turn_weights(n: int) -> list[float]:
    """Weight applied per turn — index 0 = most recent, decays linearly."""
    return [max(0.3, 1.0 - i * 0.1) for i in range(n)]


# ── Pattern derivation ────────────────────────────────────────────────────────

def _vision_name_pattern(name: str) -> str:
    """Build a word-boundary pattern from a vision name.

    The name is matched case-insensitively. A "-" or "_" in the name matches any
    single separator (space, "-", "_") or nothing at all, so "garden-plan"
    matches "garden plan", "garden_plan", "garden-plan" and "gardenplan".
    """
    parts: list[str] = []
    for ch in name:
        if ch in "-_":
            parts.append(r"[ _-]?")
        else:
            parts.append(re.escape(ch))
    return r"\b" + "".join(parts) + r"\b"


def _load_keywords(config_dir: Optional[str | Path], vision: str) -> list[str]:
    """Read the optional `keywords` list for a vision from its JSON profile.

    Invalid regexes are skipped rather than fatal. Missing or malformed files
    simply yield no extra patterns.
    """
    if config_dir is None:
        return []
    profile = Path(config_dir) / "visions" / f"{vision}.json"
    try:
        if not profile.exists():
            return []
        data = json.loads(profile.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    kws = data.get("keywords")
    if not isinstance(kws, list):
        return []
    return [k for k in kws if isinstance(k, str)]


def _compile_patterns(config_dir: Optional[str | Path], vision: str) -> list[re.Pattern]:
    """Compile the vision-name pattern plus any valid keyword patterns."""
    raw = [_vision_name_pattern(vision)] + _load_keywords(config_dir, vision)
    compiled: list[re.Pattern] = []
    for p in raw:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _score_text(text: str, patterns: list[re.Pattern]) -> float:
    """Count how many distinct patterns fire on the given text."""
    return sum(1.0 for p in patterns if p.search(text))


def _extract_texts(messages: list[dict]) -> list[str]:
    """Pull plain text from the most recent turns, newest first.

    Content that is not a string is handled: only text blocks are used.
    """
    texts: list[str] = []
    for msg in reversed(messages[-LOOKBACK_TURNS * 2:]):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
        if len(texts) >= LOOKBACK_TURNS:
            break
    return texts


def score_conversation(
    messages: list[dict],
    available: list[str],
    config_dir: Optional[str | Path] = None,
) -> dict[str, float]:
    """Score recent conversation turns against each available vision.

    messages: the channel's conversation list (role/content dicts)
    available: list of vision names that actually have .md files
    config_dir: where config/visions/<vision>.json keyword profiles live

    Returns dict[vision_name → weighted_score].
    """
    texts = _extract_texts(messages)
    weights = _turn_weights(len(texts))
    scores: dict[str, float] = {v: 0.0 for v in available}

    compiled = {v: _compile_patterns(config_dir, v) for v in available}

    for i, text in enumerate(texts):
        w = weights[i] if i < len(weights) else 0.3
        for vision in available:
            scores[vision] += _score_text(text, compiled[vision]) * w

    return scores


def auto_shift_compass(
    config_dir: str | Path,
    messages: list[dict],
) -> Optional[str]:
    """Evaluate whether the FOCUSED heading should shift.

    Returns the new vision name if a shift was made, None otherwise.
    Reads and writes config/compass.json directly (same path the compass uses).

    Safe to call fire-and-forget — all exceptions are caught and logged.
    """
    config_dir = Path(config_dir)
    compass_file = config_dir / "compass.json"
    visions_dir = config_dir / "visions"
    shift_log_file = config_dir / "compass_auto_shifts.json"

    try:
        # Load current compass
        current: dict = {"focused": None, "ambient": [], "dormant": []}
        if compass_file.exists():
            try:
                current.update(json.loads(compass_file.read_text(encoding="utf-8")))
            except Exception:
                pass

        current_focused: Optional[str] = (current.get("focused") or "").strip() or None

        # Available visions = .md files in visions/
        available = sorted(f.stem for f in visions_dir.glob("*.md")) if visions_dir.is_dir() else []
        if not available:
            return None

        # Score the conversation
        scores = score_conversation(messages, available, config_dir=config_dir)
        if not scores or all(v == 0.0 for v in scores.values()):
            return None

        # Find the winner
        winner = max(scores, key=lambda k: scores[k])
        winner_score = scores[winner]

        # Must clear the minimum bar
        if winner_score < MIN_WINNER_SCORE:
            log.debug(f"compass_navigator: winner={winner} score={winner_score:.1f} < MIN ({MIN_WINNER_SCORE}) — no shift")
            return None

        # Must dominate the current heading
        current_score = scores.get(current_focused or "", 0.0)
        if current_focused and winner == current_focused:
            log.debug(f"compass_navigator: already on {current_focused} — no shift")
            return None

        if current_focused and winner_score < current_score * DOMINANCE_RATIO:
            log.debug(
                f"compass_navigator: {winner}={winner_score:.1f} vs {current_focused}={current_score:.1f} "
                f"— insufficient dominance (need {DOMINANCE_RATIO}x) — no shift"
            )
            return None

        # Shift
        from . import compass
        compass.set_compass(config_dir=config_dir, focused=winner, set_by="auto-navigator")

        # Log the shift
        shift_record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "from": current_focused,
            "to": winner,
            "scores": {k: round(v, 2) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
        }
        existing: list = []
        if shift_log_file.exists():
            try:
                existing = json.loads(shift_log_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.append(shift_record)
        shift_log_file.write_text(json.dumps(existing[-100:], indent=2, ensure_ascii=False), encoding="utf-8")

        log.info(
            f"compass_navigator: AUTO-SHIFT {current_focused!r} → {winner!r} "
            f"(score {winner_score:.1f} vs {current_score:.1f})"
        )
        return winner

    except Exception as exc:
        log.warning(f"compass_navigator.auto_shift_compass failed: {exc}")
        return None
