"""THE JOURNAL — append-only conversation history.

Every conversation item, on every face (Tower chat, Discord, scheduler wake),
becomes an immutable, content-hashed artifact the instant it passes through
the harness. Storage is append-only JSONL, one file per UTC day, living under
memory/journal/ — so it rides the existing mind-vault export policy unchanged
and is human-readable forever.

The sync rationale: an append-only set of
immutable items is a grow-only set. Union by id, order by timestamp — two
bodies on one key cannot conflict, their histories interleave. The entire
"perfect sync" science exists to handle mutation; the journal simply has none.
"""

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("galadriel.journal")


class ConversationJournal:
    """Append-only, crash-tolerant conversation item store."""

    def __init__(self, memory_dir):
        self.dir = Path(memory_dir) / "journal"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── writing ────────────────────────────────────────────────────────

    @staticmethod
    def _item_id(ts: str, channel: str, role: str, content: str) -> str:
        raw = f"{ts}|{channel}|{role}|{content}".encode("utf-8", "replace")
        return hashlib.sha256(raw).hexdigest()[:16]

    def append(self, role: str, content, channel: str = "default",
               meta: dict | None = None) -> dict:
        """File one conversation item. Non-string content (multimodal lists)
        is stored as its JSON rendering — verbatim enough to be history."""
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False, default=str)
            except Exception:
                content = str(content)
        ts = datetime.now(timezone.utc).isoformat()
        item = {
            "id": self._item_id(ts, channel, role, content),
            "ts": ts,
            "channel": str(channel),
            "role": role,
            "content": content,
        }
        if meta:
            item["meta"] = meta
        path = self.dir / f"{ts[:10]}.jsonl"
        line = json.dumps(item, ensure_ascii=False)
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        return item

    # ── reading ────────────────────────────────────────────────────────

    def read_day(self, day: str) -> list:
        """Items for one 'YYYY-MM-DD' day. A torn tail line from a crash is
        skipped with a warning — the artifact before it is intact."""
        path = self.dir / f"{day}.jsonl"
        if not path.exists():
            return []
        items = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except json.JSONDecodeError:
                log.warning("journal: skipping corrupt line in %s", path.name)
        return items

    def items_since(self, cursor_ts: str | None = None,
                    limit: int | None = None) -> list:
        """All items strictly newer than cursor_ts, oldest first. This is the
        push half of a future per-item sync: 'everything after my cursor'."""
        out = []
        for path in sorted(self.dir.glob("*.jsonl")):
            for it in self.read_day(path.stem):
                if cursor_ts is None or it.get("ts", "") > cursor_ts:
                    out.append(it)
        out.sort(key=lambda i: i.get("ts", ""))
        return out[:limit] if limit else out

    # ── the sync algebra ───────────────────────────────────────────────

    @staticmethod
    def merge(a: list, b: list) -> list:
        """Union two item lists by id, ordered by ts. This four-liner is the
        entire conflict story of an append-only mind: there isn't one."""
        seen, out = set(), []
        for it in sorted(list(a) + list(b), key=lambda i: i.get("ts", "")):
            if it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            out.append(it)
        return out
