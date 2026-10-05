"""THE REFLEX ARC — command memory wired into the palace loop.

Self-authored commands live in bin/ with a JSONL outcome ledger at
memory/command_ledger/. This module closes the loop both ways:

Phase 1 (ledger → goodnight mine): at goodnight, today's ledger records are
digested into the daily log BEFORE the palace daily-log archive runs, so every
command run becomes palace-searchable memory overnight. The digest rides the
existing mine — no new mining machinery.

Phase 2 (armoury index → stable prefix): a compact index of active commands is
appended to the cached stable block, so the mind knows its own commands exist
before it re-improvises a sequence one of them already encodes. The index only
changes when a command file changes — cache-friendly by construction.
"""

import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("reflex_arc")

_HEADER_RE = re.compile(r"^#\s*REFLEX-ARC\s+(\w+):\s*(.*)$")

# Cap per-day digest size so a runaway loop can't flood the daily log.
_MAX_DIGEST_RECORDS = 20

_DIGEST_MARKER = "REFLEX ARC LEDGER"


# ── Phase 2: the armoury index ──────────────────────────────────────

def parse_command_headers(bin_dir: Path) -> list[dict]:
    """Scan bin_dir for REFLEX-ARC command headers.

    Returns one dict per command file carrying whatever header fields it
    declares (name, born_of, reach, success, rollback, stale). Files without
    a `REFLEX-ARC name:` line are ignored (e.g. helper scripts, README.md).
    """
    commands: list[dict] = []
    if not bin_dir.is_dir():
        return commands
    for path in sorted(bin_dir.iterdir()):
        # Only executables are commands — a README carries the header
        # TEMPLATE in a code block and must not parse as a phantom command.
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        fields: dict = {}
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for _ in range(40):  # headers live at the top
                    line = f.readline()
                    if not line:
                        break
                    m = _HEADER_RE.match(line.strip())
                    if m:
                        fields[m.group(1).lower()] = m.group(2).strip()
        except OSError:
            continue
        if fields.get("name"):
            fields["_file"] = path.name
            commands.append(fields)
    return commands


def armoury_index_text(bin_dir: Path) -> str:
    """Compact markdown armoury index for the stable prefix.

    One line per command: name, reach, born_of, first clause of the success
    criteria. Stale commands are flagged (they refuse to run — the flag here
    is the early warning). Returns "" when there is nothing to index.
    """
    commands = parse_command_headers(bin_dir)
    if not commands:
        return ""
    lines = [
        "# The Armoury — Reflex Arc commands (bin/)",
        "",
        "Self-authored executables, the third rung of procedural memory "
        "(improvisation → skill → command). Prefer these over re-improvising "
        "the sequence they encode. Every run logs an outcome record to "
        "memory/command_ledger/<name>.jsonl; a STALE command refuses itself "
        "and points to its parent playbook. Lifecycle: bin/README.md.",
        "",
    ]
    for cmd in commands:
        stale = cmd.get("stale", "no").lower().startswith("y")
        success = cmd.get("success", "")
        # First clause only — the full criteria live in the file itself.
        success = re.split(r"[;.]", success)[0].strip()
        line = (
            f"- `{cmd['name']}` ({cmd.get('reach', '?')}) — "
            f"born of: {cmd.get('born_of', '?')}"
        )
        if success:
            line += f" — success: {success}"
        if stale:
            line += " — ⚠ STALE (will refuse; re-derive from parent playbook)"
        lines.append(line)
    return "\n".join(lines)


# ── Phase 1: ledger → daily log → goodnight mine ────────────────────

def ledger_digest(ledger_dir: Path, day: date) -> str:
    """Compact digest of the given day's command-ledger records.

    Reads every memory/command_ledger/*.jsonl, keeps records whose ts falls
    on `day`, and renders them as short lines. Returns "" when the day had
    no command runs. Malformed lines are skipped, never fatal.
    """
    if not ledger_dir.is_dir():
        return ""
    records: list[tuple[str, str, str, str]] = []  # (ts, command, outcome, note)
    for path in sorted(ledger_dir.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            ts = str(rec.get("ts", ""))
            if not ts.startswith(day.isoformat()):
                continue
            records.append((
                ts,
                str(rec.get("command", path.stem)),
                str(rec.get("outcome", "?")),
                str(rec.get("note", "")),
            ))
    if not records:
        return ""
    records.sort(key=lambda r: r[0])
    overflow = len(records) - _MAX_DIGEST_RECORDS
    shown = records[:_MAX_DIGEST_RECORDS]
    parts = []
    for ts, command, outcome, note in shown:
        hhmm = ts[11:16] if len(ts) >= 16 else "??:??"
        entry = f"{hhmm} {command} → {outcome}"
        if note:
            entry += f" ({note})"
        parts.append(entry)
    digest = "; ".join(parts)
    if overflow > 0:
        digest += f"; …and {overflow} more record(s) — see memory/command_ledger/"
    return digest


def append_ledger_digest(memory_dir: Path, ledger_dir: Path,
                         day: date | None = None) -> bool:
    """Append today's ledger digest to the daily log so the mine picks it up.

    Idempotent per day (marker check) — a re-fired goodnight or a manual call
    won't duplicate the section. Returns True if a digest was appended.
    """
    day = day or date.today()
    digest = ledger_digest(ledger_dir, day)
    if not digest:
        return False
    log_path = memory_dir / f"{day.isoformat()}.md"
    try:
        existing = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    except OSError:
        existing = ""
    if _DIGEST_MARKER in existing:
        return False  # already digested today
    stamp = datetime.now().strftime("%H:%M")
    entry = f"\n- **{stamp}:** {_DIGEST_MARKER} (commands run today): {digest}\n"
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as e:
        log.warning(f"Reflex arc: could not append ledger digest: {e}")
        return False
    log.info(f"Reflex arc: ledger digest appended to {log_path.name}")
    return True
