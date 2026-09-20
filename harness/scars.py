"""THE SCAR TISSUE — gates earned by compound failure.

The escalation ladder:

    incident → correction drawer → SCAR (prose gate in the cached prefix)
             → if still recurring → CODE GUARD (graduation)

Mechanics:
- DETECTION (the Rule of Three, inverted): when a drawer is filed with
  origin=correction, palace.add_drawer calls scan_wound_class() BEFORE filing —
  a semantic scan for PRIOR correction drawers near the new one. Two or more
  priors = third strike = a notice rides the tool result. The MIND judges and
  promotes; code never writes prompt prose on its own (eyes-open discipline).
- PROMOTION / RETIREMENT: via bin/scar only (audible: a daily-log line here,
  a decision drawer filed by the mind after). Hard cap MAX_SCARS: at cap,
  promote refuses until a retirement frees the slot — scarcity as discipline.
  The equilibrium law: promotion rate ≈ retirement + graduation rate, or the
  compounding series goes negative (sclerosis, superstition, timidity drift).
- config/SCARS.md rides the STABLE cached prefix directly beneath the soul
  (memory.build_stable_text), isolated from SOUL.md so soul edits cannot wipe
  earned lessons. Mutations are rare by construction → one cache rewrite each.
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_SCARS = 12
BEGIN = "<!-- scars:begin -->"
END = "<!-- scars:end -->"

# Semantic threshold for "same wound class". Deliberately permissive: a false
# positive costs one sentence in a tool result judged by the mind; a false
# negative costs the whole feature. (Correct/wrong targets both land ~0.42-0.44
# — hence the mind judges, not us.)
SCAN_THRESHOLD = 0.45
SCAN_NEED = 2  # prior corrections required to call it a third strike

_LINE_RE = re.compile(
    r"^- \[(S\d{3}) \| promoted (\d{4}-\d{2}-\d{2})(?: \| bearing: ([^\]]+))?\] (.+)$"
)


def _scars_path() -> Path:
    return Path(os.environ.get("SCARS_FILE", "config/SCARS.md"))


def _current_bearing() -> str:
    """The compass heading a scar is earned under.
    Wounds are navigational: they belong on the map."""
    try:
        from . import compass
        b = compass.get_focused() or ""
        return "" if b.lower() in ("", "none", "active_vision", "default") else b
    except Exception:
        return ""


def _memory_dir() -> Path:
    return Path(os.environ.get("MEMORY_DIR", "memory"))


def _read() -> str:
    p = _scars_path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _append_daily_log(entry: str) -> None:
    """Audibility: every mutation leaves a line in today's daily log."""
    try:
        d = _memory_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        stamp = datetime.now().strftime("%H:%M")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n- **{stamp}:** {entry}\n")
    except Exception:
        pass  # audibility must never block the mutation itself


# ── Read side ────────────────────────────────────────────────────────


def list_scars() -> list[dict]:
    """Parse scars between the markers. Returns [{id, date, bearing, text}]."""
    text = _read()
    if BEGIN not in text or END not in text:
        return []
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    out = []
    for line in body.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            out.append({"id": m.group(1), "date": m.group(2),
                        "bearing": m.group(3) or "", "text": m.group(4)})
    return out


def render_list() -> str:
    scars = list_scars()
    if not scars:
        return f"No scars. 0/{MAX_SCARS} — the tissue is clean."
    lines = [
        f"{s['id']}  ({s['date']}"
        + (f" | 🧭 {s['bearing']}" if s['bearing'] else "")
        + f")  {s['text']}"
        for s in scars
    ]
    return "\n".join(lines) + f"\n{len(scars)}/{MAX_SCARS} slots used."


# ── Write side (bin/scar only — audible by construction) ─────────────


def promote(text: str) -> str:
    """Append a scar. Refuses at cap (exit code 2 via CLI = refused)."""
    text = (text or "").strip()
    if not text:
        return "REFUSED: empty scar text."
    scars = list_scars()
    if len(scars) >= MAX_SCARS:
        return (
            f"REFUSED: at cap ({MAX_SCARS}/{MAX_SCARS}). The equilibrium law: "
            "retire one first (bin/scar retire S### \"reason\") — scarcity is "
            "the discipline that keeps this file from becoming a bureaucracy."
        )
    content = _read()
    if BEGIN not in content or END not in content:
        return f"FAILED: {_scars_path()} missing scar markers ({BEGIN} / {END})."
    nums = [int(s["id"][1:]) for s in scars]
    sid = f"S{(max(nums) + 1 if nums else 1):03d}"
    bearing = _current_bearing()
    stamp = f"{sid} | promoted {datetime.now().strftime('%Y-%m-%d')}"
    if bearing:
        stamp += f" | bearing: {bearing}"
    line = f"- [{stamp}] {text}"
    head, tail = content.split(END, 1)
    new = head + line + "\n" + END + tail
    _scars_path().write_text(new, encoding="utf-8")
    _append_daily_log(f"SCAR PROMOTED [{sid}]: {text}")
    return (
        f"Promoted {sid} ({len(scars) + 1}/{MAX_SCARS}). It rides the cached "
        "prefix from the next call. NOW: (1) file the decision drawer "
        "(palace_add_drawer, origin=decision) recording the wound cluster that "
        "earned it — a scar without provenance on record is folklore; "
        f"(2) graft the companion skill — bin/graft {sid} — a gate says "
        "'don't', the skill says 'here is how'."
    )


def retire(scar_id: str, reason: str = "") -> str:
    """Remove a scar by exact id. The retired text is returned so the mind
    can file it to the palace with history kept (forgetting with a trace)."""
    scar_id = (scar_id or "").strip()
    scars = list_scars()
    match = next((s for s in scars if s["id"] == scar_id), None)
    if not match:
        have = ", ".join(s["id"] for s in scars) or "none"
        return f"FAILED: no scar {scar_id!r}. Present: {have}."
    content = _read()
    kept = []
    for line in content.splitlines():
        m = _LINE_RE.match(line.strip())
        if m and m.group(1) == scar_id:
            continue
        kept.append(line)
    _scars_path().write_text("\n".join(kept) + ("\n" if content.endswith("\n") else ""), encoding="utf-8")
    _append_daily_log(f"SCAR RETIRED [{scar_id}] ({reason or 'no reason given'}): {match['text']}")
    return (
        f"Retired {scar_id} ({len(scars) - 1}/{MAX_SCARS}). Retired text: "
        f"{match['text']!r}. NOW file it to the palace (palace_add_drawer, "
        "origin=decision) with the reason — history is kept, never lost."
    )


# ── Detection (called by palace.add_drawer on origin=correction) ─────


def _raw_neighbors(content: str, n: int = 12) -> list[tuple[str, float, dict]]:
    """Semantic neighbours of `content` in the drawers collection.
    Returns [(document, distance, metadata)]. Lazy imports; [] on any failure."""
    from . import palace as _palace  # lazy: avoids import cycle at module load
    col = _palace._drawers_collection()
    if col is None:
        return []
    raw = getattr(col, "_collection", col)
    res = raw.query(query_texts=[content], n_results=n)
    docs = (res.get("documents") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    out = []
    for i, doc in enumerate(docs):
        dist = dists[i] if i < len(dists) else None
        meta = metas[i] if i < len(metas) else {}
        out.append((doc, dist, meta or {}))
    return out


def scan_wound_class(content: str, *, threshold: float = SCAN_THRESHOLD,
                     need: int = SCAN_NEED) -> str | None:
    """The Rule of Three, inverted. Count PRIOR correction-origin drawers
    semantically near `content`. Returns a notice string when the count
    reaches `need` (i.e. this filing is at least the third strike), else None.

    Must be called BEFORE the new drawer is mined (no self-match). Never
    raises — detection is advisory; filing must never be blocked by it.
    """
    try:
        hits: dict[str, tuple[float, str]] = {}
        for doc, dist, meta in _raw_neighbors(content):
            if dist is None or dist > threshold:
                continue
            if "- origin: correction" not in (doc or ""):
                continue
            if meta.get("lifecycle_status", "active") != "active":
                continue
            src = meta.get("source_file") or doc[:60]
            if src not in hits or dist < hits[src][0]:
                body = doc.split("---", 1)[-1].strip().replace("\n", " ")
                hits[src] = (dist, body[:100])
        if len(hits) < need:
            return None
        top = sorted(hits.values())[:4]
        lines = "\n".join(f"    d={d:.2f}  {p!r}" for d, p in top)
        count = len(list_scars())
        return (
            f"⚡ WOUND CLASS — the Rule of Three, inverted: {len(hits)} prior "
            f"correction drawer(s) resemble this one:\n{lines}\n"
            "Judge honestly whether these are the SAME wound (semantic distance "
            "finds, it does not aim). If they are, promote a scar — a gate, not "
            "a sermon: trigger → mandatory check, provenance dates attached:\n"
            "    bin/scar promote \"Before <trigger> → <check>. (N incidents: <dates>)\"\n"
            f"Scar budget: {count}/{MAX_SCARS}. If it keeps firing despite prose, "
            "graduate it to a code guard instead — prose reminds; code prevents."
        )
    except Exception:
        return None


# ── CLI (bin/scar) ───────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        print(render_list())
        return 0
    if cmd == "promote":
        if len(argv) < 2:
            print("usage: scar promote \"Before <trigger> → <check>. (provenance)\"")
            return 1
        msg = promote(argv[1])
        print(msg)
        if not msg.startswith("Promoted"):
            return 2 if msg.startswith("REFUSED") else 1
        # THE AUTOMATIC GRAFT: every promotion drafts its companion skill in
        # the same stroke. Drafting is automatic; BLESSING never is — the
        # DRAFT_ file still awaits review. A graft failure must never
        # un-promote the scar. Opt out: AUTO_GRAFT=0.
        sid = msg.split()[1]
        if os.environ.get("AUTO_GRAFT", "1") != "0":
            try:
                from . import graft as _graft
                print(_graft.graft(sid))
            except Exception as e:
                print(f"GRAFT DEFERRED (auto-graft failed: {e}) — "
                      f"run bin/graft {sid} manually.")
        return 0
    if cmd == "retire":
        if len(argv) < 2:
            print("usage: scar retire S### \"reason\"")
            return 1
        msg = retire(argv[1], argv[2] if len(argv) > 2 else "")
        print(msg)
        return 1 if msg.startswith("FAILED") else 0
    print(f"unknown subcommand {cmd!r} — use list | promote | retire")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
