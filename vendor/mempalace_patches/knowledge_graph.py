"""
knowledge_graph.py — Temporal Entity-Relationship Graph for MemPalace
=====================================================================

Real knowledge graph with:
  - Entity nodes (people, projects, tools, concepts)
  - Typed relationship edges (daughter_of, does, loves, works_on, etc.)
  - Temporal validity (valid_from → valid_to — knows WHEN facts are true)
  - Closet references (links back to the verbatim memory)

Storage: SQLite (local, no dependencies, no subscriptions)
Query: entity-first traversal with time filtering

This is what competes with Zep's temporal knowledge graph.
Zep uses Neo4j in the cloud ($25/mo+). We use SQLite locally (free).

Usage:
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.add_triple("Max", "child_of", "Alice", valid_from="2015-04-01")
    kg.add_triple("Max", "does", "swimming", valid_from="2025-01-01")
    kg.add_triple("Max", "loves", "chess", valid_from="2025-10-01")

    # Query: everything about Max
    kg.query_entity("Max")

    # Query: what was true about Max in January 2026?
    kg.query_entity("Max", as_of="2026-01-15")

    # Query: who is connected to Alice?
    kg.query_entity("Alice", direction="both")

    # Invalidate: Max's sports injury resolved
    kg.invalidate("Max", "has_issue", "sports_injury", ended="2026-02-15")
"""

import hashlib
import json
import os
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path


DEFAULT_KG_PATH = os.path.expanduser("~/.mempalace/knowledge_graph.sqlite3")

# ── LIVING-MEMORY (restated-fact deliberator) ──────────────────────────────
# Predicates that are inherently FUNCTIONAL: a subject may hold exactly one
# open object at a time. add_triple auto-applies the date-deliberator for
# these even when the caller does not pass single_valued=True, so every call
# site (harness, body, public engine) gets correct restate behaviour for free.
# Stored normalised (lower, underscores) — matches add_triple's `pred`.
SINGLE_VALUED_PREDICATES = {
    "named_self", "prefers_name", "current_name", "goes_by",
    "current_model", "runs_on_model", "current_provider",
}
# ───────────────────────────────────────────────────────────────────────────


class KnowledgeGraph:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_KG_PATH
        db_parent = Path(self.db_path).parent
        db_parent.mkdir(parents=True, exist_ok=True)
        try:
            db_parent.chmod(0o700)
        except (OSError, NotImplementedError):
            pass
        self._connection = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT DEFAULT 'unknown',
                properties TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS triples (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                confidence REAL DEFAULT 1.0,
                source_closet TEXT,
                source_file TEXT,
                source_drawer_id TEXT,
                adapter_name TEXT,
                extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subject) REFERENCES entities(id),
                FOREIGN KEY (object) REFERENCES entities(id)
            );

            CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
            CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
            CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
            CREATE INDEX IF NOT EXISTS idx_triples_valid ON triples(valid_from, valid_to);
        """)
        self._migrate_schema(conn)
        conn.commit()

    def _migrate_schema(self, conn):
        """Backwards-compatible schema migration for older triples tables.

        Fresh palaces get ``source_drawer_id`` / ``adapter_name`` (RFC 002 §5.5)
        directly from the canonical ``CREATE TABLE`` above, so this path is a
        no-op on new installs. It exists for palaces that were created before
        those columns were added: SQLite has no ``ADD COLUMN IF NOT EXISTS``,
        so we introspect the schema and only issue the ALTER when the column
        is missing.
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(triples)")}
        if "source_drawer_id" not in existing:
            conn.execute("ALTER TABLE triples ADD COLUMN source_drawer_id TEXT")
        if "adapter_name" not in existing:
            conn.execute("ALTER TABLE triples ADD COLUMN adapter_name TEXT")

    def _conn(self):
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self):
        """Close the database connection."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _entity_id(self, name: str) -> str:
        return name.lower().replace(" ", "_").replace("'", "")

    # ── Write operations ──────────────────────────────────────────────────

    def add_entity(self, name: str, entity_type: str = "unknown", properties: dict = None):
        """Add or update an entity node."""
        eid = self._entity_id(name)
        props = json.dumps(properties or {})
        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entities (id, name, type, properties) VALUES (?, ?, ?, ?)",
                    (eid, name, entity_type, props),
                )
        return eid

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str = None,
        valid_to: str = None,
        confidence: float = 1.0,
        source_closet: str = None,
        source_file: str = None,
        source_drawer_id: str = None,
        adapter_name: str = None,
        single_valued: bool = False,
    ):
        """
        Add a relationship triple: subject → predicate → object.

        # ── LIVING-MEMORY (restated-fact deliberator) ──────────────────────
        ``single_valued=True`` marks this a FUNCTIONAL predicate: the subject
        may hold exactly ONE open object for it at a time (e.g. ``named_self``,
        ``current_model``). Restating it auto-closes the prior open value via a
        DATE DELIBERATOR — latest ``valid_from`` wins, so an out-of-order or
        older assertion never overrides a newer one. History is preserved
        (closed chapters keep their row). Default ``False`` = today's
        multi-valued, accumulate-forever behaviour, unchanged.
        # ───────────────────────────────────────────────────────────────────

        ``source_drawer_id`` and ``adapter_name`` are RFC 002 §5.5 provenance
        fields populated by adapters that advertise ``supports_kg_triples``;
        they default to ``None`` so every existing caller stays source-compatible.

        Examples:
            add_triple("Max", "child_of", "Alice", valid_from="2015-04-01")
            add_triple("Max", "does", "swimming", valid_from="2025-01-01")
            add_triple("Alice", "worried_about", "Max injury", valid_from="2026-01", valid_to="2026-02")
        """
        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(obj)
        pred = predicate.lower().replace(" ", "_")

        # Auto-create entities if they don't exist
        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)", (sub_id, subject)
                )
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)", (obj_id, obj)
                )

                # Check for existing identical triple
                existing = conn.execute(
                    "SELECT id FROM triples WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                    (sub_id, pred, obj_id),
                ).fetchone()

                if existing:
                    return existing["id"]  # Already exists and still valid

                # ── LIVING-MEMORY (restated-fact deliberator) ──────────────
                # For a single-valued (functional) predicate, the subject may
                # hold only ONE open object. Settle the argument by date:
                #   * find all OTHER open objects for (subject, predicate)
                #   * the winner is the one with the latest valid_from,
                #     comparing the incoming fact against them
                #   * close every loser (valid_to = winner's valid_from)
                # If the INCOMING fact loses (it is older than a current open
                # one), insert it already-closed so history stays complete but
                # it never surfaces as current.
                if single_valued or pred in SINGLE_VALUED_PREDICATES:
                    open_rows = conn.execute(
                        "SELECT id, object, valid_from FROM triples "
                        "WHERE subject=? AND predicate=? AND valid_to IS NULL",
                        (sub_id, pred),
                    ).fetchall()

                    def _key(vf):
                        # None sorts oldest; ISO strings compare lexically.
                        return vf or ""

                    incoming_key = _key(valid_from)
                    # The latest valid_from among incoming + all open rows.
                    latest_key = incoming_key
                    for r in open_rows:
                        if _key(r["valid_from"]) > latest_key:
                            latest_key = _key(r["valid_from"])

                    incoming_wins = incoming_key >= latest_key

                    if incoming_wins:
                        # Close every other open object for this predicate.
                        for r in open_rows:
                            conn.execute(
                                "UPDATE triples SET valid_to=? WHERE id=?",
                                (valid_from or date.today().isoformat(), r["id"]),
                            )
                        # fall through and insert the incoming as OPEN.
                    else:
                        # Incoming is stale: keep current winner open, store
                        # the incoming already-closed (closed at the winner's
                        # valid_from) so it is history, not current.
                        valid_to = latest_key or date.today().isoformat()
                # ───────────────────────────────────────────────────────────

                triple_id = f"t_{sub_id}_{pred}_{obj_id}_{hashlib.sha256(f'{valid_from}{datetime.now().isoformat()}'.encode()).hexdigest()[:12]}"

                conn.execute(
                    """INSERT INTO triples (
                        id, subject, predicate, object,
                        valid_from, valid_to, confidence,
                        source_closet, source_file,
                        source_drawer_id, adapter_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        triple_id,
                        sub_id,
                        pred,
                        obj_id,
                        valid_from,
                        valid_to,
                        confidence,
                        source_closet,
                        source_file,
                        source_drawer_id,
                        adapter_name,
                    ),
                )
        return triple_id

    def invalidate(self, subject: str, predicate: str, obj: str, ended: str = None):
        """Mark a relationship as no longer valid (set valid_to date)."""
        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(obj)
        pred = predicate.lower().replace(" ", "_")
        ended = ended or date.today().isoformat()

        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute(
                    "UPDATE triples SET valid_to=? WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                    (ended, sub_id, pred, obj_id),
                )

    # ── Query operations ──────────────────────────────────────────────────

    def query_entity(self, name: str, as_of: str = None, direction: str = "outgoing"):
        """
        Get all relationships for an entity.

        direction: "outgoing" (entity → ?), "incoming" (? → entity), "both"
        as_of: date string — only return facts valid at that time
        """
        eid = self._entity_id(name)

        results = []
        with self._lock:
            conn = self._conn()

            if direction in ("outgoing", "both"):
                query = "SELECT t.*, e.name as obj_name FROM triples t JOIN entities e ON t.object = e.id WHERE t.subject = ?"
                params = [eid]
                if as_of:
                    query += " AND (t.valid_from IS NULL OR t.valid_from <= ?) AND (t.valid_to IS NULL OR t.valid_to >= ?)"
                    params.extend([as_of, as_of])
                for row in conn.execute(query, params).fetchall():
                    results.append(
                        {
                            "direction": "outgoing",
                            "subject": name,
                            "predicate": row["predicate"],
                            "object": row["obj_name"],
                            "valid_from": row["valid_from"],
                            "valid_to": row["valid_to"],
                            "confidence": row["confidence"],
                            "source_closet": row["source_closet"],
                            "current": row["valid_to"] is None,
                        }
                    )

            if direction in ("incoming", "both"):
                query = "SELECT t.*, e.name as sub_name FROM triples t JOIN entities e ON t.subject = e.id WHERE t.object = ?"
                params = [eid]
                if as_of:
                    query += " AND (t.valid_from IS NULL OR t.valid_from <= ?) AND (t.valid_to IS NULL OR t.valid_to >= ?)"
                    params.extend([as_of, as_of])
                for row in conn.execute(query, params).fetchall():
                    results.append(
                        {
                            "direction": "incoming",
                            "subject": row["sub_name"],
                            "predicate": row["predicate"],
                            "object": name,
                            "valid_from": row["valid_from"],
                            "valid_to": row["valid_to"],
                            "confidence": row["confidence"],
                            "source_closet": row["source_closet"],
                            "current": row["valid_to"] is None,
                        }
                    )

        return results

    def query_relationship(self, predicate: str, as_of: str = None):
        """Get all triples with a given relationship type."""
        pred = predicate.lower().replace(" ", "_")
        query = """
            SELECT t.*, s.name as sub_name, o.name as obj_name
            FROM triples t
            JOIN entities s ON t.subject = s.id
            JOIN entities o ON t.object = o.id
            WHERE t.predicate = ?
        """
        params = [pred]
        if as_of:
            query += " AND (t.valid_from IS NULL OR t.valid_from <= ?) AND (t.valid_to IS NULL OR t.valid_to >= ?)"
            params.extend([as_of, as_of])

        results = []
        with self._lock:
            conn = self._conn()
            for row in conn.execute(query, params).fetchall():
                results.append(
                    {
                        "subject": row["sub_name"],
                        "predicate": pred,
                        "object": row["obj_name"],
                        "valid_from": row["valid_from"],
                        "valid_to": row["valid_to"],
                        "current": row["valid_to"] is None,
                    }
                )
        return results

    def timeline(self, entity_name: str = None):
        """Get all facts in chronological order, optionally filtered by entity."""
        with self._lock:
            conn = self._conn()
            if entity_name:
                eid = self._entity_id(entity_name)
                rows = conn.execute(
                    """
                    SELECT t.*, s.name as sub_name, o.name as obj_name
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    JOIN entities o ON t.object = o.id
                    WHERE (t.subject = ? OR t.object = ?)
                    ORDER BY t.valid_from ASC NULLS LAST
                    LIMIT 100
                """,
                    (eid, eid),
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT t.*, s.name as sub_name, o.name as obj_name
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    JOIN entities o ON t.object = o.id
                    ORDER BY t.valid_from ASC NULLS LAST
                    LIMIT 100
                """).fetchall()

        return [
            {
                "subject": r["sub_name"],
                "predicate": r["predicate"],
                "object": r["obj_name"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                "current": r["valid_to"] is None,
            }
            for r in rows
        ]

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self):
        with self._lock:
            conn = self._conn()
            entities = conn.execute("SELECT COUNT(*) as cnt FROM entities").fetchone()["cnt"]
            triples = conn.execute("SELECT COUNT(*) as cnt FROM triples").fetchone()["cnt"]
            current = conn.execute(
                "SELECT COUNT(*) as cnt FROM triples WHERE valid_to IS NULL"
            ).fetchone()["cnt"]
            expired = triples - current
            predicates = [
                r["predicate"]
                for r in conn.execute(
                    "SELECT DISTINCT predicate FROM triples ORDER BY predicate"
                ).fetchall()
            ]
        return {
            "entities": entities,
            "triples": triples,
            "current_facts": current,
            "expired_facts": expired,
            "relationship_types": predicates,
        }

    # ── Seed from known facts ─────────────────────────────────────────────

    def seed_from_entity_facts(self, entity_facts: dict):
        """
        Seed the knowledge graph from fact_checker.py ENTITY_FACTS.
        This bootstraps the graph with known ground truth.
        """
        for key, facts in entity_facts.items():
            name = facts.get("full_name", key.capitalize())
            etype = facts.get("type", "person")
            self.add_entity(
                name,
                etype,
                {
                    "gender": facts.get("gender", ""),
                    "birthday": facts.get("birthday", ""),
                },
            )

            # Relationships
            parent = facts.get("parent")
            if parent:
                self.add_triple(
                    name, "child_of", parent.capitalize(), valid_from=facts.get("birthday")
                )

            partner = facts.get("partner")
            if partner:
                self.add_triple(name, "married_to", partner.capitalize())

            relationship = facts.get("relationship", "")
            if relationship == "daughter":
                self.add_triple(
                    name,
                    "is_child_of",
                    facts.get("parent", "").capitalize() or name,
                    valid_from=facts.get("birthday"),
                )
            elif relationship == "husband":
                self.add_triple(name, "is_partner_of", facts.get("partner", name).capitalize())
            elif relationship == "brother":
                self.add_triple(name, "is_sibling_of", facts.get("sibling", name).capitalize())
            elif relationship == "dog":
                self.add_triple(name, "is_pet_of", facts.get("owner", name).capitalize())
                self.add_entity(name, "animal")

            # Interests
            for interest in facts.get("interests", []):
                self.add_triple(name, "loves", interest.capitalize(), valid_from="2025-01-01")
