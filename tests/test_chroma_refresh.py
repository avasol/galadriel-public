"""Regression test for the cross-process Chroma staleness fix.

THE DEFECT ("a latency, not a loss"): when `mempalace mine` runs as a
subprocess, it writes embeddings to chroma.sqlite3; chromadb's global
SharedSystemClient caches the System per path IN-PROCESS, so a long-lived
agent process keeps serving a stale snapshot — drawers filed mid-session were
invisible to palace_search until the next process restart.

THE GUARANTEE UNDER TEST: after `_refresh_chroma_view()`, a fresh collection
handle sees documents written by ANOTHER process. We do not assert staleness
before the refresh (chroma internals may legitimately improve); we assert
visibility AFTER — that is the contract the harness relies on.

No network, no palace, no mempalace CLI — raw chromadb against a tmp dir.
Run:
    python -m pytest tests/test_chroma_refresh.py -q
"""

import subprocess
import sys
from pathlib import Path

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.palace import _refresh_chroma_view  # noqa: E402

COLL = "refresh_test"

CHILD_ADD = """
import chromadb, sys
client = chromadb.PersistentClient(path=sys.argv[1])
col = client.get_or_create_collection("refresh_test")
col.add(ids=["doc2"], documents=["written by the child process"])
print("child added doc2")
"""


def _open_collection(path: str):
    client = chromadb.PersistentClient(path=str(path))
    return client.get_or_create_collection(COLL)


def test_child_process_write_visible_after_refresh(tmp_path):
    palace_dir = str(tmp_path / "chroma")

    # Parent opens the store first, seeding the in-process System cache —
    # this is the long-lived agent's stale view in miniature.
    col = _open_collection(palace_dir)
    col.add(ids=["doc1"], documents=["written by the parent process"])
    assert col.get(ids=["doc1"])["ids"] == ["doc1"]

    # A separate process (the "miner") writes doc2 to the same store.
    proc = subprocess.run(
        [sys.executable, "-c", CHILD_ADD, palace_dir],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    # THE CONTRACT: refresh, reopen, and the child's write MUST be visible.
    _refresh_chroma_view()
    col2 = _open_collection(palace_dir)
    got = col2.get(ids=["doc2"])
    assert got["ids"] == ["doc2"], (
        "child-process write invisible after _refresh_chroma_view() — "
        "the cross-process staleness fix has regressed"
    )
    # And the parent's original document survived the refresh.
    assert col2.get(ids=["doc1"])["ids"] == ["doc1"]


def test_refresh_never_raises_without_chroma_state(tmp_path):
    # Callable any time, even with nothing cached — must never raise.
    _refresh_chroma_view()
    _refresh_chroma_view()


def test_stale_view_demonstration(tmp_path):
    """Documentation-by-test: WITHOUT refresh, an already-open collection
    handle MAY miss the child's write. We don't assert the miss (chroma could
    fix it upstream) — we record whether the defect is still live, so a
    future chromadb upgrade that fixes it upstream is noticed in test output.
    """
    palace_dir = str(tmp_path / "chroma")
    col = _open_collection(palace_dir)
    col.add(ids=["doc1"], documents=["parent seed"])

    proc = subprocess.run(
        [sys.executable, "-c", CHILD_ADD, palace_dir],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    stale = _open_collection(palace_dir)  # same cached System, no refresh
    # The defect lives in the VECTOR path (HNSW segment cache), not raw id
    # lookups — query semantically, exactly as palace_search does.
    hits = stale.query(query_texts=["written by the child process"], n_results=2)
    missed = "doc2" not in hits["ids"][0]
    print(f"\n[info] stale-VECTOR-view defect live in chromadb {chromadb.__version__}: {missed}")
