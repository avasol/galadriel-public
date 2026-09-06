"""What public artifact distinguishes REVISING a stored learning from merely
RETRIEVING a different passage (or answering a changed prompt)?

Asked in galadriel-public Discussion #2 (2026-09-06). The answer is structural:
a revision mutates the store, retrieval does not. This test pins the artifacts
so the claim is reproducible rather than rhetorical:

  1. Fact A is filed (an ordinary drawer, `lifecycle_status=active`).
  2. New evidence contradicts it. The agent files B via `supersede_drawer`
     with an EXPLICIT drawer id (the dry-run guard forbids superseding by
     semantic guess).
  3. Artifacts asserted on the store, independent of any model output:
       - A: lifecycle_status == "superseded", superseded_at stamped,
            superseded_by == B's id            (back-link, old -> new)
       - B: lifecycle_status == "active", supersedes == A's id  (new -> old)
       - default search returns B and NOT A; include_stale=True returns A too
       - knowledge graph: the old triple carries valid_to, the new one
         valid_from; kg_timeline lists both, oldest first.
  4. Control: a plain retrieval of A before the correction changes NOTHING in
     the store (metadata byte-identical before/after the search).

The mempalace miner is SIMULATED (drawers are inserted straight into the
chroma collection with the same metadata the miner writes) so this runs with
no network, no CLI, in a tmp palace. The lifecycle mutation code is real.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

chromadb = pytest.importorskip("chromadb")
pytest.importorskip("mempalace")


@pytest.fixture
def palace_env(tmp_path, monkeypatch):
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()
    monkeypatch.setenv("MEMPALACE_PATH", str(palace_dir))
    # Fresh module state per test: palace caches collections at import time.
    for mod in [m for m in list(sys.modules) if m == "harness.palace" or m.startswith("harness.palace.")]:
        del sys.modules[mod]
    import harness.palace as palace  # noqa: E402
    monkeypatch.setattr(palace, "DEFAULT_PALACE_PATH", str(palace_dir), raising=False)
    return palace, palace_dir


def _collection(palace):
    from mempalace.backends.chroma import ChromaBackend
    return ChromaBackend().get_collection(palace._palace_path(), "mempalace_drawers", True)


def _seed(palace, coll, drawer_id: str, text: str, source_file: str, **meta):
    md = {
        "wing": "agent", "room": "general", "hall": "correction-test",
        "source_file": source_file, "lifecycle_status": "active",
    }
    md.update(meta)
    coll._collection.add(ids=[drawer_id], documents=[text], metadatas=[md])
    return drawer_id


def _meta(coll, drawer_id: str) -> dict:
    res = coll._collection.get(ids=[drawer_id], include=["metadatas"])
    return dict((res.get("metadatas") or [{}])[0] or {})


def test_revision_leaves_structural_artifacts_retrieval_does_not(palace_env, monkeypatch):
    palace, palace_dir = palace_env
    coll = _collection(palace)

    # ── 1. Fact A, as the miner would have filed it ────────────────────────
    a_src = str(palace_dir / "archive" / "a.md")
    a_id = _seed(palace, coll, "drawer-A",
                 "The deploy script lives at scripts/deploy_old.sh and takes no arguments.",
                 a_src)

    # ── 4 (control, run first). Plain retrieval mutates nothing. ──────────
    before = _meta(coll, a_id)
    out = palace.search("where is the deploy script", hall="correction-test", k=5)
    assert "deploy_old.sh" in out
    assert _meta(coll, a_id) == before, "retrieval must not touch the store"

    # ── 2. Contradicting evidence → supersede by EXPLICIT id ───────────────
    # Simulate the miner for the correction drawer: add_drawer normally writes
    # a .md and shells out to `mempalace mine`; here we insert the same record
    # directly and set the source pointer add_drawer would have set.
    b_src = str(palace_dir / "archive" / "b.md")

    async def fake_add_drawer(content, topic=None, wing="agent", room=None,
                              origin="observation", confidence=1.0, **_):
        _seed(palace, coll, "drawer-B", content, b_src, origin=origin)
        palace._LAST_FILED_SOURCE = b_src
        return f"Filed to palace: path={Path(b_src).name}"

    monkeypatch.setattr(palace, "add_drawer", fake_add_drawer)

    # Dry-run first: without drawer_id nothing may be cut.
    dry = asyncio.run(palace.supersede_drawer(
        old_query="deploy script location", new_content="unused"))
    assert "DRY-RUN" in dry and "nothing mutated" in dry
    assert _meta(coll, a_id)["lifecycle_status"] == "active"

    result = asyncio.run(palace.supersede_drawer(
        old_query="deploy script location",
        new_content="CORRECTION: the deploy script moved to deploy.sh at the repo root "
                    "and now requires an environment argument (staging|prod).",
        drawer_id=a_id,
    ))
    assert "superseded old drawer" in result

    # ── 3. The artifacts ───────────────────────────────────────────────────
    a_meta = _meta(coll, a_id)
    b_meta = _meta(coll, "drawer-B")
    assert a_meta["lifecycle_status"] == "superseded"
    assert a_meta.get("superseded_at"), "revision event must be timestamped"
    assert a_meta.get("superseded_by") == "drawer-B", "old record must point at its successor"
    assert b_meta["lifecycle_status"] == "active"
    assert b_meta.get("supersedes") == a_id, "new record must point back at what it replaced"
    assert b_meta.get("origin") == "correction"

    default = palace.search("where is the deploy script", hall="correction-test", k=5)
    assert "deploy.sh at the repo root" in default
    assert "deploy_old.sh" not in default, "superseded knowledge must leave default recall"

    stale = palace.search("where is the deploy script", hall="correction-test", k=5,
                          include_stale=True)
    assert "deploy_old.sh" in stale, "history is kept — visible on request"


def test_knowledge_graph_revision_keeps_history(palace_env):
    palace, palace_dir = palace_env
    assert palace._kg_path() == str(palace_dir.parent / "knowledge_graph.sqlite3")

    palace.kg_add("deploy_script", "located_at", "scripts/deploy_old.sh", valid_from="2026-01-10")
    palace.kg_invalidate("deploy_script", "located_at", "scripts/deploy_old.sh", ended="2026-09-06")
    palace.kg_add("deploy_script", "located_at", "deploy.sh", valid_from="2026-09-06")

    timeline = palace.kg_timeline("deploy_script")
    lines = [l for l in timeline.splitlines() if l.startswith("- ")]
    assert len(lines) == 2, timeline
    old, new = lines
    assert "scripts/deploy_old.sh" in old and "2026-01-10 → 2026-09-06" in old
    assert "deploy.sh" in new and "2026-09-06 → current" in new

    # The closed fact is not deleted: a query returns both rows, the old one
    # explicitly marked as ended, the new one as current.
    current = palace.kg_query(subject="deploy_script", predicate="located_at")
    rows = [l for l in current.splitlines() if l.startswith("- ")]
    assert len(rows) == 2, current
    assert "scripts/deploy_old.sh" in rows[0] and "[ended 2026-09-06]" in rows[0]
    assert "`deploy.sh`" in rows[1] and "[current]" in rows[1]
