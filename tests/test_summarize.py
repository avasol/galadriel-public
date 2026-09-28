"""Stdlib LexRank summarizer — the module that lets the bridge ride free.

WHY THESE TESTS MATTER
----------------------
This module exists to remove a third-party dependency. If it silently stopped
working, the bridge would go hollow and nothing would crash — the failure mode
this whole exercise is meant to prevent. So the tests assert not just "it
returns something" but that it returns something USEFUL: compressed, distinct,
in narrative order, and never a duplicate list.

The decisive test is `test_importable_without_site_packages`, which runs the
real import path in a subprocess with `-S` (no site-packages at all). That is
the property the whole design rests on: no compiled, no third-party, nothing
that would force a code signature.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.summarize import (  # noqa: E402
    split_sentences, lexrank, summarize, _content_words, _cosine, _term_frequencies,
)


# ── segmentation ───────────────────────────────────────────────────────────

def test_split_basic_sentences():
    sents = split_sentences("One thing happened. Then another. And a third.")
    assert len(sents) == 3


def test_split_respects_abbreviations():
    sents = split_sentences("We met Dr. Smith at 9 a.m. and discussed the plan.")
    # "Dr." and "a.m." must not create sentence breaks
    assert len(sents) == 1, f"abbreviation split wrongly: {sents}"


def test_split_hard_breaks_on_newlines():
    sents = split_sentences("First paragraph here.\n\nSecond paragraph here.")
    assert len(sents) == 2


def test_split_never_returns_empty_strings():
    sents = split_sentences("A.\n\n\n\nB.\n   \nC.")
    assert all(s.strip() for s in sents)


# ── similarity ─────────────────────────────────────────────────────────────

def test_identical_sentences_score_one():
    a = "the memory palace stores drawers verbatim"
    assert abs(_cosine(_term_frequencies(a), _term_frequencies(a)) - 1.0) < 1e-9


def test_unrelated_sentences_score_zero():
    a = "the memory palace stores drawers"
    b = "oranges ripen slowly in winter"
    assert _cosine(_term_frequencies(a), _term_frequencies(b)) == 0.0


def test_stopwords_excluded_from_content():
    words = _content_words("the quick brown fox and the lazy dog")
    assert "the" not in words and "and" not in words
    assert "quick" in words and "fox" in words


def test_cosine_handles_empty_gracefully():
    assert _cosine(_term_frequencies(""), _term_frequencies("anything")) == 0.0


# ── lexrank ────────────────────────────────────────────────────────────────

def test_lexrank_scores_sum_to_one():
    sents = split_sentences(
        "The palace stores drawers. The compass scopes retrieval. "
        "Retrieval latency matters for the turn budget. Latency is measured in tokens."
    )
    scores = lexrank(sents)
    assert len(scores) == len(sents)
    assert abs(sum(scores) - 1.0) < 1e-6


def test_lexrank_single_sentence():
    assert lexrank(["only one sentence here"]) == [1.0]


def test_lexrank_empty():
    assert lexrank([]) == []


def test_lexrank_deterministic():
    sents = split_sentences(
        "Alpha beta gamma delta. Beta gamma delta epsilon. "
        "Gamma delta epsilon zeta. Delta epsilon zeta eta."
    )
    assert lexrank(sents) == lexrank(sents)


# ── summarize: the useful-output contract ──────────────────────────────────

def test_summarize_compresses():
    text = " ".join(
        f"Sentence number {i} discusses topic {i} and its tradeoffs in detail."
        for i in range(20)
    )
    out = summarize(text, 5)
    assert out is not None
    assert len(out) < len(text), "summary did not compress the input"


def test_summarize_preserves_narrative_order():
    text = ("First we reviewed the trim behaviour. Next we looked at the Discord relay. "
            "Then we examined the ARM64 migration. Finally we planned the macOS build.")
    out = summarize(text, 3)
    assert out is not None
    # the surviving sentences must appear in their original relative order
    idxs = [text.index(s.strip()) for s in split_sentences(out) if s.strip() in text]
    assert idxs == sorted(idxs), f"summary reordered the narrative: {out}"


def test_summarize_suppresses_redundancy():
    """A slice repeating one pattern must NOT yield that pattern N times."""
    text = " ".join(
        "I explained that drawers are stored verbatim and the knowledge graph tracks relations."
        for _ in range(10)
    )
    out = summarize(text, 5)
    assert out is not None
    # one distinct idea → a short summary, not five duplicates
    assert out.count("knowledge graph") == 1, \
        f"redundancy not suppressed: {out}"


def test_summarize_yields_distinct_sentences():
    text = ("We reviewed the trim behaviour and the token budget. "
            "The bridge survives re-trimming which surprised me. "
            "Discord feedback relay sanitizes input and blocks mentions. "
            "The ARM64 migration explained the slow build. "
            "Apple developer enrolment blocks the macOS artifact. "
            "Redundancy suppression makes summaries useful.")
    out = summarize(text, 4)
    sents = [s.strip() for s in split_sentences(out)]
    assert len(set(sents)) == len(sents), "summary contains duplicate sentences"


def test_summarize_short_input_returns_none():
    assert summarize("Just one sentence.", 3) is None


def test_summarize_never_raises_on_junk():
    for junk in (None, "", 12345, ["not", "a", "string"], {"a": 1}):
        summarize(junk, 3)  # must not raise


# ── THE DECISIVE TEST ──────────────────────────────────────────────────────

def test_importable_without_site_packages():
    """The whole design rests on this: the summarizer must import and RUN with
    no site-packages at all (no numpy, no sumy, no nltk, no compiled parts).
    Run in a subprocess with -S so the real interpreter state is exercised."""
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "from harness.bridge import build_bridge, inject_bridge;"
        "msgs=[{'role':'user','content':'We reviewed the token budget and the trim bridge carefully.'},"
        "      {'role':'assistant','content':'The archive writes to the palace before the drop occurs.'},"
        "      {'role':'user','content':'Discord relay sanitizes mentions and allow-lists the type field.'},"
        "      {'role':'assistant','content':'Apple developer enrolment is the blocker for the macOS artifact.'}];"
        "b=build_bridge(msgs);"
        "assert b is None or isinstance(b,str), b;"
        "print('OK' if b is None else b[:80])"
    ) % str(ROOT)
    r = subprocess.run(
        [sys.executable, "-S", "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"stdlib-only import failed:\n{r.stderr[-800:]}"
    assert "ModuleNotFoundError" not in r.stderr


if __name__ == "__main__":
    test_split_basic_sentences()
    test_split_respects_abbreviations()
    test_split_hard_breaks_on_newlines()
    test_split_never_returns_empty_strings()
    test_identical_sentences_score_one()
    test_unrelated_sentences_score_zero()
    test_stopwords_excluded_from_content()
    test_cosine_handles_empty_gracefully()
    test_lexrank_scores_sum_to_one()
    test_lexrank_single_sentence()
    test_lexrank_empty()
    test_lexrank_deterministic()
    test_summarize_compresses()
    test_summarize_preserves_narrative_order()
    test_summarize_suppresses_redundancy()
    test_summarize_yields_distinct_sentences()
    test_summarize_short_input_returns_none()
    test_summarize_never_raises_on_junk()
    test_importable_without_site_packages()
    print("all summarizer tests passed")
