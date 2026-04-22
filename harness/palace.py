"""MemPalace integration — semantic search + verbatim archival.

Thin wrapper around `mempalace.searcher.search_memories`. The palace itself
lives at `MEMPALACE_PATH` (default `~/.mempalace/palace`). Seeded out-of-band
via `mempalace mine` — run that once against your `config/` and `memory/`
directories after install.

Imports of `mempalace` are deferred until first call so cold harness startup
does not pay ChromaDB + onnxruntime load cost when no palace tool is invoked.

Archival helpers (`archive_conversation`, `mine_batch_dir`) are used by
the compaction hook and by `/new` to preserve verbatim content before it
would otherwise be lost. Both are fire-and-forget from callers; failures
log a warning but never propagate.

Environment overrides:
    MEMPALACE_PATH           Palace directory (default ~/.mempalace/palace).
                             Read by the mempalace library itself.
    PALACE_ARCHIVE_ROOT      Where conversation/tool_result archives land.
                             Default ~/.mempalace/archive.
    PALACE_WAKE_UP_FILE      Cached wake-up snapshot path.
                             Default ~/.mempalace/wake_up.md.
    PALACE_WAKE_UP_INJECT    Set to "0" to disable wake-up injection into
                             the dynamic system prompt block.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("galadriel.palace")

DEFAULT_PALACE_PATH = str(Path.home() / ".mempalace" / "palace")
DEFAULT_ARCHIVE_ROOT = str(Path.home() / ".mempalace" / "archive")
DEFAULT_WAKE_UP_FILE = str(Path.home() / ".mempalace" / "wake_up.md")
DEFAULT_WING = "agent"
MINE_TIMEOUT_SEC = 90
WAKE_UP_TIMEOUT_SEC = 30

# Resolve the mempalace CLI from the same venv as the running Python, so
# subprocess calls do not silently break if PATH is not set (test harnesses,
# bare shells, cron contexts).
MEMPALACE_BIN = str(Path(sys.executable).parent / "mempalace")


def _palace_path() -> str:
    return os.environ.get("MEMPALACE_PATH", DEFAULT_PALACE_PATH)


def _archive_root() -> Path:
    return Path(os.environ.get("PALACE_ARCHIVE_ROOT", DEFAULT_ARCHIVE_ROOT))


def search(
    query: str,
    wing: str | None = None,
    room: str | None = None,
    hall: str | None = None,
    k: int = 5,
) -> str:
    """Semantic search over the palace. Returns a markdown-formatted string
    ready to be handed back as a tool result.

    Filters:
      - wing: top-level (e.g. 'agent', or whatever you named yours).
      - room: folder-based room name (memory/harness/tower/…).
      - hall: keyword-based auto-topic (decisions/problems/milestones/…).
              Not supported by search_memories; when given, we bypass
              search_memories and query chromadb directly with a native
              `where={"hall": hall}` filter. Loses BM25 + closet boost
              but keeps semantic ranking — acceptable for scoped queries.
    """
    path = _palace_path()
    if not os.path.isdir(path):
        return f"[palace unavailable] no palace at {path} — run `mempalace init` + `mine` first"

    want = max(1, min(k, 20))

    if hall:
        # Direct chromadb path: native where filter on hall
        try:
            from mempalace.backends.chroma import ChromaBackend
        except ImportError as e:
            return f"[palace unavailable] mempalace not installed: {e}"
        try:
            backend = ChromaBackend()
            coll = backend.get_collection(path, "mempalace_drawers")
            where: dict = {"hall": hall}
            if wing: where = {"$and": [where, {"wing": wing}]}
            if room: where = {"$and": [where if isinstance(where, dict) else where, {"room": room}]}
            res = coll._collection.query(
                query_texts=[query], n_results=want, where=where,
            )
            drawers = []
            for i, doc in enumerate((res.get("documents") or [[]])[0]):
                md = (res.get("metadatas") or [[]])[0][i] if res.get("metadatas") else {}
                dist = (res.get("distances") or [[]])[0][i] if res.get("distances") else None
                drawers.append({
                    "text": doc,
                    "wing": (md or {}).get("wing", "?"),
                    "room": (md or {}).get("room", "?"),
                    "hall": (md or {}).get("hall", "?"),
                    "source_file": (md or {}).get("source_file", ""),
                    "distance": dist,
                })
        except Exception as e:
            return f"[palace error] {type(e).__name__}: {e}"
    else:
        try:
            from mempalace.searcher import search_memories
        except ImportError as e:
            return f"[palace unavailable] mempalace not installed: {e}"
        try:
            result = search_memories(
                query=query, palace_path=path, wing=wing, room=room,
                n_results=want,
            )
        except Exception as e:
            return f"[palace error] {type(e).__name__}: {e}"
        drawers = result.get("results") or result.get("drawers") or []

    if not drawers:
        filters = []
        if wing: filters.append(f"wing=`{wing}`")
        if room: filters.append(f"room=`{room}`")
        if hall: filters.append(f"hall=`{hall}`")
        filter_str = f" [{', '.join(filters)}]" if filters else ""
        return f"No drawers matched `{query}`{filter_str}"

    header_bits = [f"`{query}`"]
    if wing: header_bits.append(f"wing=`{wing}`")
    if room: header_bits.append(f"room=`{room}`")
    if hall: header_bits.append(f"hall=`{hall}`")
    lines = [f"**Palace search:** " + " ".join(header_bits), ""]
    for i, d in enumerate(drawers, 1):
        wing_name = d.get("wing", "?")
        room_name = d.get("room", "?")
        hall_name = d.get("hall", "?")
        distance = d.get("distance")
        content = (d.get("content") or d.get("text") or "").strip()
        header = f"### {i}. {wing_name} / {room_name} / hall={hall_name}"
        if distance is not None:
            header += f"  _(d={distance:.3f})_"
        lines.append(header)
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip()


# ─── Archival ──────────────────────────────────────────────────────

async def mine_batch_dir(
    batch_dir: Path,
    agent: str = DEFAULT_WING,
    mode: str | None = None,
    extract: str | None = None,
) -> bool:
    """Run `mempalace mine` on a directory. Returns True on clean exit.
    Failures log at WARNING but never raise. Also refreshes the wake-up
    cache on success so wake-up injection tracks the current palace state.

    mode='convos' + extract='general' auto-classifies into 5 memory types
    (decisions, preferences, milestones, problems, emotional) — used by the
    /new conversation archival path.
    """
    cmd = [MEMPALACE_BIN, "mine", str(batch_dir),
           "--wing", DEFAULT_WING, "--agent", agent]
    if mode:
        cmd += ["--mode", mode]
    if extract:
        cmd += ["--extract", extract]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=MINE_TIMEOUT_SEC
        )
        if proc.returncode == 0:
            # Refresh the wake-up cache so the dynamic block picks up new drawers
            asyncio.ensure_future(refresh_wake_up_cache())
            return True
        log.warning(
            f"Palace mine rc={proc.returncode} at {batch_dir}: "
            f"{stderr.decode(errors='replace')[:500]}"
        )
    except asyncio.TimeoutError:
        log.warning(f"Palace mine timed out after {MINE_TIMEOUT_SEC}s at {batch_dir}")
    except Exception as e:
        log.warning(f"Palace mine failed at {batch_dir}: {e}")
    return False


def _serialize_message(msg: dict) -> str:
    """Render a single API-format message as a markdown section."""
    role = msg.get("role", "?")
    content = msg.get("content")
    parts = [f"## {role}"]
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type", "?")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(
                    f"### tool_use: {block.get('name', '?')} "
                    f"(id={block.get('id', '?')})\n\n"
                    f"```json\n{block.get('input', {})}\n```"
                )
            elif btype == "tool_result":
                parts.append(
                    f"### tool_result (id={block.get('tool_use_id', '?')})\n\n"
                    f"{block.get('content', '')}"
                )
            elif btype == "image":
                parts.append("[image block — omitted]")
            else:
                parts.append(f"[{btype} block]\n\n{block}")
    else:
        parts.append(str(content))
    return "\n\n".join(parts)


async def archive_conversation(channel_id: str, messages: list[dict]) -> None:
    """Archive a full conversation to the palace before it's wiped (`/new`).

    Writes a single timestamped .md file per conversation (mempalace chunks
    internally). Uses convos-mode + general extraction so the archive gets
    auto-classified into 5 memory types (decisions, preferences, milestones,
    problems, emotional). Fire-and-forget from callers; failure never raises.
    """
    if not messages:
        return

    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    safe_channel = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(channel_id))
    batch_dir = _archive_root() / f"conversation_{safe_channel}_{ts}"

    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        sections = [
            f"# Conversation archive — channel {channel_id}\n",
            f"- archived: {ts}",
            f"- message count: {len(messages)}\n",
            "---\n",
        ]
        for i, msg in enumerate(messages):
            sections.append(f"<!-- message {i} -->")
            sections.append(_serialize_message(msg))
            sections.append("")

        (batch_dir / f"conversation_{safe_channel}_{ts}.md").write_text(
            "\n".join(sections), encoding="utf-8"
        )
    except Exception as e:
        log.warning(f"Palace conversation archive write failed: {e}")
        return

    ok = await mine_batch_dir(batch_dir, agent="new-clear", mode="convos", extract="general")
    if ok:
        log.info(
            f"Palace conversation archive: channel={channel_id} "
            f"messages={len(messages)} dir={batch_dir}"
        )


async def archive_daily_logs(memory_dir: str = "memory") -> None:
    """Mine the daily-log directory so today's entries become palace-searchable.
    Typically called at the goodnight tick.

    Uses mempalace's natural deduplication (mtime-based) so re-mining the
    same dir only files new content. No need to track state separately.
    """
    memory_path = Path(memory_dir)
    if not memory_path.is_dir():
        log.warning(f"Daily-log archive: {memory_dir} does not exist, skipping")
        return
    ok = await mine_batch_dir(memory_path, agent="goodnight")
    if ok:
        log.info(f"Palace daily-log mine complete: {memory_dir}")


# ─── Wake-up injection ─────────────────────────────────────────────

def _wake_up_file() -> Path:
    return Path(os.environ.get("PALACE_WAKE_UP_FILE", DEFAULT_WAKE_UP_FILE))


def read_wake_up_text() -> str:
    """Return cached wake-up content (empty string if missing/empty/stale).

    Called by MemoryManager.build_dynamic_text() on every API call. Must
    stay cheap — just a file read, no chromadb load.
    """
    path = _wake_up_file()
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log.warning(f"Wake-up read failed: {e}")
        return ""
    # Strip mempalace's interactive header lines if present
    if text.startswith("Wake-up text"):
        text = text.split("\n", 1)[-1].lstrip("=").lstrip()
    return text


# ─── Write tools ───────────────────────────────────────────────────

def _slug(text: str, limit: int = 40) -> str:
    """Short filesystem-safe slug from arbitrary text."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in text.lower())
    safe = safe.strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe[:limit] or "drawer"


async def add_drawer(content: str, topic: str | None = None, wing: str = DEFAULT_WING) -> str:
    """File a verbatim drawer into the palace immediately.

    Used by the `palace_add_drawer` tool when the agent wants a fact filed
    into searchable memory *now*, without waiting for the next mine cycle.
    One-shot: writes a single .md file into the archive tree, runs mempalace
    mine on it. Returns a human-readable status string for the tool result.
    """
    if not content or not content.strip():
        return "[palace add] empty content — nothing filed."

    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    slug = _slug(topic) if topic else _slug(content.strip().split("\n", 1)[0])
    batch_dir = _archive_root() / f"agent_add_{ts}_{slug}"
    fname = f"{ts}_{slug}.md"

    header = [f"# Agent-filed drawer", ""]
    header.append(f"- filed: {ts}")
    header.append(f"- wing: {wing}")
    if topic:
        header.append(f"- topic: {topic}")
    header.append("")
    header.append("---")
    header.append("")

    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / fname).write_text(
            "\n".join(header) + content.strip() + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        return f"[palace add] write failed: {e}"

    ok = await mine_batch_dir(batch_dir, agent="agent-add")
    if not ok:
        return f"[palace add] mine failed — content still on disk at {batch_dir}"
    return (
        f"Filed to palace: wing=`{wing}`"
        + (f", topic=`{topic}`" if topic else "")
        + f", path={fname}"
    )


async def wake_up(wing: str | None = None) -> str:
    """Fetch a fresh wake-up snapshot (on-demand tool).

    Different from the always-on dynamic-block injection: this runs
    `mempalace wake-up` with an optional --wing filter *right now*,
    so the agent can pull a targeted palace overview mid-conversation.
    """
    args = [MEMPALACE_BIN, "wake-up"]
    if wing:
        args += ["--wing", wing]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=WAKE_UP_TIMEOUT_SEC
        )
        if proc.returncode != 0:
            return (
                f"[palace wake-up] rc={proc.returncode}: "
                f"{stderr.decode(errors='replace')[:400]}"
            )
        text = stdout.decode(errors="replace").strip()
        if text.startswith("Wake-up text"):
            text = text.split("\n", 1)[-1].lstrip("=").lstrip()
        return text or "[palace wake-up] empty output"
    except asyncio.TimeoutError:
        return f"[palace wake-up] timed out after {WAKE_UP_TIMEOUT_SEC}s"
    except Exception as e:
        return f"[palace wake-up] {type(e).__name__}: {e}"


# ─── Wake-up cache ─────────────────────────────────────────────────

async def refresh_wake_up_cache() -> bool:
    """Regenerate the wake-up cache file via `mempalace wake-up` subprocess.

    Keeps chromadb out of the main process. Silent on failure.
    Typically called at the end of mine_batch_dir() so the cache tracks
    the current palace state.
    """
    out_path = _wake_up_file()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            MEMPALACE_BIN, "wake-up",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=WAKE_UP_TIMEOUT_SEC
        )
        if proc.returncode != 0:
            log.warning(
                f"Wake-up refresh rc={proc.returncode}: "
                f"{stderr.decode(errors='replace')[:400]}"
            )
            return False
        out_path.write_text(stdout.decode(errors="replace"), encoding="utf-8")
        log.info(f"Wake-up cache refreshed ({len(stdout)} bytes at {out_path})")
        return True
    except asyncio.TimeoutError:
        log.warning(f"Wake-up refresh timed out after {WAKE_UP_TIMEOUT_SEC}s")
    except Exception as e:
        log.warning(f"Wake-up refresh failed: {e}")
    return False


# ─── Knowledge graph ───────────────────────────────────────────────

def kg_add(
    subject: str,
    predicate: str,
    object: str,
    valid_from: str | None = None,
) -> str:
    """File a temporal entity-relationship fact into the palace KG.

    Subject/object are entity names (free text). Predicate is the
    relationship (e.g., 'is', 'works_on', 'lives_in', 'prefers').
    valid_from is an ISO date (defaults to today) — facts can later be
    invalidated to mark when they stopped being true.
    """
    if not subject or not predicate or not object:
        return "[kg add] subject, predicate, and object are required."
    try:
        from mempalace.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg.add_triple(
            subject=str(subject),
            predicate=str(predicate),
            obj=str(object),
            valid_from=valid_from,
        )
        return f"KG: filed `{subject}` --[{predicate}]-> `{object}`" + (
            f" (valid_from={valid_from})" if valid_from else ""
        )
    except Exception as e:
        return f"[kg add] {type(e).__name__}: {e}"


def _fmt_triple(r: dict) -> str:
    """Render one KG row as a markdown line."""
    s = r.get("subject", "?")
    p = r.get("predicate", "?")
    o = r.get("object") or r.get("obj") or "?"
    vf = r.get("valid_from") or ""
    vt = r.get("valid_to") or ""
    status = f"[ended {vt}]" if vt else "[current]"
    when = f" (from {vf})" if vf else ""
    return f"- `{s}` --[{p}]-> `{o}`{when}  {status}"


def kg_query(
    subject: str | None = None,
    predicate: str | None = None,
    object: str | None = None,
) -> str:
    """Query the KG. Any of S/P/O may be given.

    Routing (KnowledgeGraph's actual API):
    - subject → query_entity(subject, direction='outgoing')
    - object only → query_entity(object, direction='incoming')
    - predicate only → query_relationship(predicate)
    - S+P or P+O → filter client-side from the first query
    """
    if not subject and not predicate and not object:
        return "[kg query] give at least one of subject, predicate, object."
    try:
        from mempalace.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        if subject:
            rows = kg.query_entity(name=subject, direction="outgoing") or []
        elif object:
            rows = kg.query_entity(name=object, direction="incoming") or []
        else:  # predicate only
            rows = kg.query_relationship(predicate=predicate) or []

        # Client-side filter for narrowing
        def matches(r):
            if subject and r.get("subject") != subject: return False
            if predicate and r.get("predicate") != predicate: return False
            o = r.get("object") or r.get("obj")
            if object and o != object: return False
            return True
        rows = [r for r in rows if matches(r)]
    except Exception as e:
        return f"[kg query] {type(e).__name__}: {e}"

    if not rows:
        parts = []
        if subject: parts.append(f"subject=`{subject}`")
        if predicate: parts.append(f"predicate=`{predicate}`")
        if object: parts.append(f"object=`{object}`")
        return "No KG facts match " + ", ".join(parts) + "."

    lines = [f"**KG query** ({len(rows)} fact(s)):", ""]
    for r in rows:
        lines.append(_fmt_triple(r))
    return "\n".join(lines)


def kg_invalidate(subject: str, predicate: str, object: str, ended: str | None = None) -> str:
    """Mark a KG fact as no longer valid (sets valid_to date)."""
    try:
        from mempalace.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg.invalidate(subject=str(subject), predicate=str(predicate), obj=str(object), ended=ended)
        return f"KG: invalidated `{subject}` --[{predicate}]-> `{object}`"
    except Exception as e:
        return f"[kg invalidate] {type(e).__name__}: {e}"


def kg_timeline(entity: str) -> str:
    """Return chronological history of all facts touching an entity."""
    try:
        from mempalace.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        facts = kg.timeline(entity_name=entity) or []
    except Exception as e:
        return f"[kg timeline] {type(e).__name__}: {e}"

    if not facts:
        return f"No KG history for `{entity}`."
    lines = [f"**KG timeline for `{entity}`** ({len(facts)} fact(s)):", ""]
    for f in facts:
        s = f.get("subject", "?")
        p = f.get("predicate", "?")
        o = f.get("object") or f.get("obj") or "?"
        vf = f.get("valid_from") or "?"
        vt = f.get("valid_to") or "current"
        lines.append(f"- {vf} → {vt}: `{s}` --[{p}]-> `{o}`")
    return "\n".join(lines)


# ─── Diary ─────────────────────────────────────────────────────────

DEFAULT_DIARY_AGENT = DEFAULT_WING


def diary_write(entry: str, topic: str = "general", agent_name: str = DEFAULT_DIARY_AGENT) -> str:
    """Write a diary entry. Each agent gets its own wing with a diary room.

    Use this to record end-of-session reflections: what happened, what was
    learned, what matters. Persistent across restarts.
    """
    if not entry or not entry.strip():
        return "[diary write] empty entry — nothing saved."
    try:
        from mempalace.mcp_server import tool_diary_write as _dw
        result = _dw(agent_name=agent_name, entry=entry, topic=topic)
    except Exception as e:
        return f"[diary write] {type(e).__name__}: {e}"
    if isinstance(result, dict) and result.get("error"):
        return f"[diary write] {result['error']}"
    return f"Diary entry saved to wing `{agent_name}`, topic `{topic}`."


def diary_read(last_n: int = 10, agent_name: str = DEFAULT_DIARY_AGENT) -> str:
    """Read the most recent N diary entries for an agent."""
    try:
        from mempalace.mcp_server import tool_diary_read as _dr
        result = _dr(agent_name=agent_name, last_n=max(1, min(last_n, 50)))
    except Exception as e:
        return f"[diary read] {type(e).__name__}: {e}"

    if isinstance(result, dict) and result.get("error"):
        return f"[diary read] {result['error']}"

    entries = (result or {}).get("entries") or (result or {}).get("diary_entries") or []
    if not entries:
        return f"No diary entries yet for agent `{agent_name}`."

    lines = [f"**Diary — `{agent_name}` (last {len(entries)})**", ""]
    for e in entries:
        ts = e.get("timestamp") or e.get("filed_at") or "?"
        tp = e.get("topic", "?")
        txt = (e.get("entry") or e.get("content") or "").strip()
        lines.append(f"### {ts}  _(topic: {tp})_")
        lines.append(txt)
        lines.append("")
    return "\n".join(lines).rstrip()


# ─── Taxonomy ──────────────────────────────────────────────────────

def taxonomy() -> str:
    """Return wing → room breakdown with drawer counts.

    Reads metadata directly from the palace collection. Used by the agent to
    discover how memory is organized before narrowing a search.
    """
    try:
        from mempalace.backends.chroma import ChromaBackend
    except ImportError as e:
        return f"[taxonomy] mempalace not installed: {e}"

    path = _palace_path()
    if not os.path.isdir(path):
        return f"[taxonomy] no palace at {path}"

    try:
        backend = ChromaBackend()
        coll = backend.get_collection(path, "mempalace_drawers")
        data = coll._collection.get(include=["metadatas"])
    except Exception as e:
        return f"[taxonomy] {type(e).__name__}: {e}"

    from collections import Counter
    wing_room_counts: dict[str, Counter] = {}
    hall_counts: Counter = Counter()
    for m in data.get("metadatas") or []:
        if not m:
            continue
        w = m.get("wing", "?")
        r = m.get("room", "?")
        h = m.get("hall", "?")
        wing_room_counts.setdefault(w, Counter())[r] += 1
        hall_counts[h] += 1

    total = len(data.get("metadatas") or [])
    if total == 0:
        return "Palace is empty."

    lines = [f"**Palace taxonomy** — {total} drawers total", ""]
    for wing in sorted(wing_room_counts):
        wtotal = sum(wing_room_counts[wing].values())
        lines.append(f"- **Wing `{wing}`** ({wtotal} drawer(s)):")
        for room, n in sorted(wing_room_counts[wing].items(), key=lambda x: -x[1]):
            lines.append(f"    - room `{room}`: {n}")
    lines.append("")
    lines.append("**Halls** (topic auto-classification):")
    for hall, n in hall_counts.most_common():
        lines.append(f"  - `{hall}`: {n}")
    return "\n".join(lines)
