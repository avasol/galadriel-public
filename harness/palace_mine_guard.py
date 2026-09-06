"""Mine guard — serialization, lock-aware retry, failure queue and
self-diagnosis for every `mempalace mine` the harness launches.

WHY THIS EXISTS
    mempalace takes an exclusive palace flock (LOCK_NB) for the whole of a
    mine and, if another miner holds it, exits at once with rc=1 and
    "palace <path> is held by PID <n> (...)". It does not wait.

    The harness launches miners from several independent code paths —
    conversation trims, daily-log archival, /new archival, agent-filed
    drawers, the boot gleaner — and two of them landing in the same minute
    made `palace_add_drawer` report "mine failed" three times in two days.
    Every one of those was a collision with a miner the SAME process had
    started seconds earlier. The content was safe on disk; nothing retried
    it; and the failure string named no cause, so the symptom was
    misdiagnosed (as a tokenisation fault) before the log was read.

WHAT IT DOES
    1. Serialises the harness's own miners behind one asyncio.Lock, so the
       process never races itself.
    2. On a lock collision with an EXTERNAL holder (a CLI run, a stuck
       orphan), waits for that PID to exit — bounded — and retries.
    3. Classifies every failure (lock / timeout / error) with the holder's
       PID and command line, so the caller can say WHY, not just "failed".
    4. Appends every unrecovered failure to a durable queue
       (<archive_root>/unmined_queue.jsonl) and re-mines the queue from a
       background sweep, so a failed filing is deferred, never lost.

    Pure asyncio + stdlib. The actual mine is injected as a coroutine
    (`run_once`) so the same guard serves a subprocess CLI, an in-process
    `mempalace.cli.main()` and tests with a fake miner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger("palace.mine_guard")

# Bounded wait for an external lock holder before we give up and queue.
MINE_LOCK_WAIT_SEC = int(os.environ.get("PALACE_MINE_LOCK_WAIT_SEC", "150"))
# How often we re-check whether the holder is still alive.
MINE_LOCK_POLL_SEC = float(os.environ.get("PALACE_MINE_LOCK_POLL_SEC", "3"))
# Attempts per guarded mine (each lock collision costs one attempt).
MINE_MAX_ATTEMPTS = 3
# Background sweep cadence and per-sweep batch.
SWEEP_INTERVAL_SEC = int(os.environ.get("PALACE_UNMINED_SWEEP_SEC", "600"))
SWEEP_BATCH = 10

_HOLDER_RE = re.compile(r"is held by PID (\d+)(?: \(([^)]*)\))?")

# One gate per process: our own miners never contend with each other.
_MINE_GATE: Optional[asyncio.Lock] = None


def _gate() -> asyncio.Lock:
    global _MINE_GATE
    if _MINE_GATE is None:
        _MINE_GATE = asyncio.Lock()
    return _MINE_GATE


# RunOnce performs exactly one mine attempt and returns (rc, stdout, stderr).
# It may raise asyncio.TimeoutError; the guard classifies that as "timeout".
RunOnce = Callable[[], Awaitable[tuple[int, str, str]]]


@dataclass
class MineDiagnosis:
    kind: str                      # "ok" | "lock" | "timeout" | "error"
    rc: Optional[int] = None
    holder_pid: Optional[int] = None
    holder_cmd: Optional[str] = None
    holder_alive: Optional[bool] = None
    detail: str = ""
    attempts: int = 1
    waited_sec: float = 0.0

    def human(self) -> str:
        if self.kind == "ok":
            return "ok"
        if self.kind == "lock":
            who = f"PID {self.holder_pid}" if self.holder_pid else "another process"
            cmd = f" (`{_short(self.holder_cmd)}`)" if self.holder_cmd else ""
            state = "" if self.holder_alive is None else (" still running" if self.holder_alive else " — exited")
            return (f"palace lock held by {who}{cmd}{state}; waited {self.waited_sec:.0f}s "
                    f"over {self.attempts} attempt(s)")
        if self.kind == "timeout":
            return f"miner exceeded its timeout ({self.detail})"
        return f"miner rc={self.rc}: {_short(self.detail, 220)}"


def _short(s: Optional[str], n: int = 90) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ── Diagnosis ────────────────────────────────────────────────────────────────

def classify_failure(rc: int, out: str, err: str) -> MineDiagnosis:
    """Turn a miner's exit into a named cause. Pure; no I/O beyond /proc."""
    if rc == 0:
        return MineDiagnosis(kind="ok", rc=0)
    text = f"{err}\n{out}"
    m = _HOLDER_RE.search(text)
    if m:
        pid = int(m.group(1))
        cmd = m.group(2) or holder_cmdline(pid)
        return MineDiagnosis(kind="lock", rc=rc, holder_pid=pid, holder_cmd=cmd,
                             holder_alive=pid_alive(pid), detail=_short(text, 300))
    return MineDiagnosis(kind="error", rc=rc, detail=(err or out).strip()[:600])


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def holder_cmdline(pid: int) -> Optional[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode(errors="replace").strip() or None
    except Exception:
        return None


# ── Guarded mine ─────────────────────────────────────────────────────────────

async def guarded_mine(run_once: RunOnce, *, label: str = "mine",
                       max_attempts: int = MINE_MAX_ATTEMPTS,
                       lock_wait_sec: float = MINE_LOCK_WAIT_SEC,
                       poll_sec: float = MINE_LOCK_POLL_SEC) -> MineDiagnosis:
    """Run one mine under the process gate, retrying across external lock
    holders. Never raises. Returns the final diagnosis (kind=="ok" on success)."""
    started = time.monotonic()
    diag = MineDiagnosis(kind="error", detail="not run")
    async with _gate():
        for attempt in range(1, max_attempts + 1):
            try:
                rc, out, err = await run_once()
                diag = classify_failure(rc, out, err)
            except asyncio.TimeoutError as e:
                diag = MineDiagnosis(kind="timeout", detail=str(e) or "asyncio.TimeoutError")
            except Exception as e:  # a broken invocation is an error, not a crash
                diag = MineDiagnosis(kind="error", detail=f"{type(e).__name__}: {e}")
            diag.attempts = attempt
            diag.waited_sec = time.monotonic() - started
            if diag.kind == "ok":
                return diag
            if diag.kind != "lock" or attempt == max_attempts:
                break
            # External holder: wait for it to exit, bounded, then try again.
            remaining = lock_wait_sec - (time.monotonic() - started)
            if remaining <= 0:
                break
            log.info(f"{label}: palace lock held by PID {diag.holder_pid} "
                     f"({_short(diag.holder_cmd)}); waiting up to {remaining:.0f}s")
            await _wait_for_pid_exit(diag.holder_pid, remaining, poll_sec)
    log.warning(f"{label}: mine did not complete — {diag.human()}")
    return diag


async def _wait_for_pid_exit(pid: Optional[int], budget_sec: float, poll_sec: float) -> None:
    deadline = time.monotonic() + budget_sec
    while time.monotonic() < deadline:
        if pid is None or not pid_alive(pid):
            # Give the OS a beat to release the flock after exit.
            await asyncio.sleep(min(0.5, poll_sec))
            return
        await asyncio.sleep(min(poll_sec, max(0.0, deadline - time.monotonic())))


# ── Durable failure queue ────────────────────────────────────────────────────

def queue_path(archive_root: Path) -> Path:
    return Path(archive_root) / "unmined_queue.jsonl"


def enqueue_failure(archive_root: Path, batch_dir: Path, diag: MineDiagnosis,
                    *, agent: str, wing: str, mode: Optional[str], extract: Optional[str]) -> int:
    """Append an unrecovered failure. Returns the queue depth after the append.
    Idempotent per batch_dir: a re-failure updates the existing entry."""
    qp = queue_path(archive_root)
    entries = [e for e in load_queue(archive_root) if e.get("batch_dir") != str(batch_dir)]
    entries.append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "batch_dir": str(batch_dir),
        "agent": agent, "wing": wing, "mode": mode, "extract": extract,
        "diag": asdict(diag),
        "failures": 1 + next((e.get("failures", 0) for e in load_queue(archive_root)
                              if e.get("batch_dir") == str(batch_dir)), 0),
    })
    _write_queue(qp, entries)
    return len(entries)


def load_queue(archive_root: Path) -> list[dict]:
    qp = queue_path(archive_root)
    if not qp.exists():
        return []
    out: list[dict] = []
    for line in qp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def dequeue(archive_root: Path, batch_dir: Path) -> None:
    qp = queue_path(archive_root)
    entries = [e for e in load_queue(archive_root) if e.get("batch_dir") != str(batch_dir)]
    _write_queue(qp, entries)


def _write_queue(qp: Path, entries: list[dict]) -> None:
    qp.parent.mkdir(parents=True, exist_ok=True)
    tmp = qp.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    os.replace(tmp, qp)


# ── Sweeper ──────────────────────────────────────────────────────────────────

# MineFn re-mines a queued entry: (batch_dir, agent, wing, mode, extract) -> ok.
MineFn = Callable[[Path, str, str, Optional[str], Optional[str]], Awaitable[bool]]


async def sweep_unmined(archive_root: Path, mine: MineFn, *, batch: int = SWEEP_BATCH) -> dict:
    """Re-mine queued failures (oldest first). Entries whose directory vanished
    are dropped. Returns a summary dict; never raises."""
    entries = load_queue(archive_root)
    summary = {"queued": len(entries), "recovered": 0, "dropped": 0, "still_failing": 0}
    for e in entries[:batch]:
        bd = Path(e.get("batch_dir", ""))
        if not bd.exists():
            dequeue(archive_root, bd)
            summary["dropped"] += 1
            continue
        try:
            ok = await mine(bd, e.get("agent") or "agent-add", e.get("wing") or "",
                            e.get("mode"), e.get("extract"))
        except Exception as ex:
            log.warning(f"sweep: re-mine raised for {bd}: {ex}")
            ok = False
        if ok:
            dequeue(archive_root, bd)
            summary["recovered"] += 1
            log.info(f"sweep: recovered unmined batch {bd.name}")
        else:
            summary["still_failing"] += 1
    return summary


def start_sweeper(archive_root: Path, mine: MineFn, *, interval_sec: int = SWEEP_INTERVAL_SEC) -> asyncio.Task:
    """Spawn the background sweep loop. Silent: logs only, no model calls."""
    async def _loop():
        try:
            await asyncio.sleep(60)  # let boot settle before the first sweep
            while True:
                try:
                    if load_queue(archive_root):
                        s = await sweep_unmined(archive_root, mine)
                        log.info(f"unmined sweep: {s}")
                except Exception as e:
                    log.warning(f"unmined sweep error: {e}")
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            pass
    return asyncio.ensure_future(_loop())


# ── Doctor (manual diagnosis: `python -m harness.palace_mine_guard <archive_root> [palace_path]`) ──

def doctor(archive_root: Path, palace_path: Optional[str] = None) -> str:
    lines = ["palace mine doctor"]
    if palace_path:
        lock = Path(palace_path) / ".mempalace.lock"
        holder = None
        for cand in (lock, Path(palace_path) / "palace.lock"):
            if cand.exists():
                try:
                    holder = cand.read_text(errors="replace").strip()[:200]
                except Exception:
                    holder = "(unreadable)"
                lines.append(f"lock file: {cand} → {holder or '(empty)'}")
                break
        else:
            lines.append("lock file: none present")
    # Live miners, from /proc.
    live = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
        except Exception:
            continue
        if "mempalace" in cmd and " mine " in cmd:
            live.append(f"  PID {p.name}: {_short(cmd, 140)}")
    lines.append(f"live miners: {len(live)}")
    lines.extend(live)
    q = load_queue(Path(archive_root))
    lines.append(f"unmined queue: {len(q)} entr{'y' if len(q) == 1 else 'ies'} at {queue_path(Path(archive_root))}")
    for e in q[:20]:
        d = e.get("diag", {})
        lines.append(f"  {e.get('ts')}  {Path(e.get('batch_dir', '')).name}  "
                     f"kind={d.get('kind')} failures={e.get('failures', 1)}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    print(doctor(root, sys.argv[2] if len(sys.argv) > 2 else None))
