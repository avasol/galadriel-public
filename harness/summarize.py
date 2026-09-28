"""Stdlib extractive summarizer — LexRank, zero third-party dependencies.

WHY THIS EXISTS
---------------
The trim bridge needs exactly one capability: pick the few most representative
sentences out of a block of text. The obvious library (sumy + nltk) drags in a
COMPILED dependency (`regex`), and the Aedelgard Mind bundle is a SINGLE
platform-agnostic zip serving Windows, Linux and macOS. A compiled extension
cannot ride that zip — it would be correct on one platform and broken on two.

Rather than sign a new Shell just to carry a summarizer (Route B), or fork the
Mind zip per-platform, this module implements the same algorithm — LexRank, a
graph centrality measure over sentence similarity — in pure Python stdlib. It is
a handful of small, testable functions with no imports beyond `re`, `math` and
`collections`.

DESIGN NOTES
------------
* LexRank is the algorithm sumy uses for this exact job. It is not exotic:
  build a cosine-similarity graph over sentences, then run power iteration to
  find the sentences most central to the whole — which is what "representative"
  means.
* A common extension (also applied here) is to blend in each sentence's own
  weight, so a highly central but very short sentence does not crowd out a
  moderately central, substantive one.
* Determinism matters: ties are broken by original position, so the same input
  always yields the same summary.
* Never raises on junk. A summarizer that fails must return None, because its
  caller is a trim — and a trim must never break a live turn.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# ── Sentence segmentation ──────────────────────────────────────────────────

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g",
    "i.e", "no", "fig", "inc", "ltd", "co", "dept", "est", "approx", "apt",
}

# Split after [.!?…] followed by whitespace + an uppercase/quote start.
_SENT_BOUNDARY = re.compile(r'(?<=[.!?…])\s+(?=["\'(\[]?[A-Z0-9])')

# ── Word tokenization ──────────────────────────────────────────────────────

_WORD = re.compile(r"[a-zA-Z][a-zA-Z'\-]*")

_STOPWORDS = frozenset("""
a an the and or but if while of to in on at by for with from as is are was
were be been being do does did doing have has had having i you he she it we
they me him her us them my your his its our their this that these those
what which who whom whose when where why how all any both each few more most
other some such no nor not only own same so than too very can will just don
should now then there here about into over after before between under above
""".split())


def _content_words(sentence: str) -> list[str]:
    """Lowercased content words (stopwords removed), for the similarity graph."""
    words = (w.lower() for w in _WORD.findall(sentence))
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def split_sentences(text: str) -> list[str]:
    """Segment text into sentences. Paragraph breaks are hard boundaries."""
    sentences: list[str] = []
    for paragraph in re.split(r"\n\s*\n|\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # Protect common abbreviations from being read as sentence ends.
        guarded = paragraph
        for abbr in _ABBREVIATIONS:
            guarded = re.sub(rf"\b({abbr})\.", r"\1<DOT>", guarded, flags=re.IGNORECASE)
        for piece in _SENT_BOUNDARY.split(guarded):
            piece = piece.replace("<DOT>", ".").strip()
            if piece:
                sentences.append(piece)
    return sentences


# ── Similarity ─────────────────────────────────────────────────────────────

def _term_frequencies(sentence: str) -> Counter:
    return Counter(_content_words(sentence))


def _cosine(a: Counter, b: Counter) -> float:
    """Cosine similarity of two term-frequency bags. Returns 0.0 on empties."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── LexRank ────────────────────────────────────────────────────────────────

def lexrank(sentences: list[str], *, epsilon: float = 0.1,
            damping: float = 0.85, max_iter: int = 100,
            tol: float = 1e-4) -> list[float]:
    """LexRank centrality scores, one per sentence (same order).

    Sentences are connected when their cosine similarity exceeds `epsilon`;
    binary edges keep the classic formulation (a threshold graph, not a
    weighted one). Power iteration then distributes centrality across it:

        p = (1 - d)/n + d * P^T p

    where P is the row-normalised transition matrix. A node with no edges is
    treated as connected to everything (uniform row), which is the standard
    LexRank convention and keeps the walk well-defined.
    """
    n = len(sentences)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    bags = [_term_frequencies(s) for s in sentences]

    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            c = _cosine(bags[i], bags[j])
            sim[i][j] = sim[j][i] = c

    # Row-normalised transition matrix over the thresholded graph.
    trans = [[0.0] * n for _ in range(n)]
    for i in range(n):
        neighbours = [j for j in range(n) if i != j and sim[i][j] > epsilon]
        if neighbours:
            share = 1.0 / len(neighbours)
            for j in neighbours:
                trans[i][j] = share
        else:
            # No neighbours: diffuse uniformly so the row still sums to 1.
            uniform = 1.0 / n
            for j in range(n):
                trans[i][j] = uniform

    scores = [1.0 / n] * n
    for _ in range(max_iter):
        new = [(1.0 - damping) / n] * n
        for i in range(n):
            si = scores[i]
            if si == 0.0:
                continue
            row = trans[i]
            for j in range(n):
                w = row[j]
                if w:
                    new[j] += damping * si * w
        diff = sum(abs(new[i] - scores[i]) for i in range(n))
        scores = new
        if diff < tol:
            break

    total = sum(scores)
    if total > 0:
        scores = [s / total for s in scores]
    return scores


def summarize(text: str, max_sentences: int = 5) -> str | None:
    """Extractive summary of `text` as up to `max_sentences` joined sentences.

    Selection is centrality-ranked with REDUNDANCY SUPPRESSION: a candidate is
    skipped when it is substantially similar to a sentence already chosen. This
    is what separates a useful summary from a list of near-identical lines —
    without it, a conversation that repeats one pattern five times yields that
    pattern five times.

    Returns None when there is nothing worth summarizing. Never raises.
    """
    try:
        sentences = split_sentences(text)
        if len(sentences) < 2:
            return None

        keep = min(max_sentences, len(sentences))
        scores = lexrank(sentences)
        bags = [_term_frequencies(s) for s in sentences]

        # Rank by centrality, gently blended with length so a very short
        # sentence cannot win on centrality alone. Ties break by position.
        order = sorted(
            range(len(sentences)),
            key=lambda i: (-(scores[i] * math.log(len(sentences[i]) + 1)), i),
        )

        chosen: list[int] = []
        for idx in order:
            if len(chosen) >= keep:
                break
            # Redundancy suppression: skip near-duplicates of what we have.
            if any(_cosine(bags[idx], bags[c]) > 0.85 for c in chosen):
                continue
            chosen.append(idx)

        # NOTE: we deliberately do NOT top up with suppressed near-duplicates.
        # If the slice repeats one pattern, an honest summary is SHORT — the
        # redundancy IS the finding. Padding it to `keep` would resurrect
        # exactly the noise suppression exists to remove.

        chosen.sort()  # narrative order for coherence
        parts = [sentences[i].strip() for i in chosen if sentences[i].strip()]
        if not parts:
            return None
        return " ".join(parts)
    except Exception:  # noqa: BLE001 — a summarizer must never raise
        return None
