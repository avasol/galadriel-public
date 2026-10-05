"""THE LAST WORD — termination as a memory act.

The rule:
  - DELIBERATE shutdown (a wake is already armed, i.e. the process was asked
    to restart) → NO extra persist. The armed wake already carries the context
    for the next boot; double-writing is noise.
  - ANYTHING ELSE (a scheduled stop, an operator-initiated stop without an
    armed wake, a kill that still delivers a signal) → persist by DEFAULT: an
    immediate daily-log record of the dying state, and — outside the expected
    quiet window — an auto-armed wake so the next boot knows it died
    mid-thought.

Design constraints (this runs inside a signal handler, in a dying process):
  - File writes only. No network, no chat transport, no palace embedding — the
    handler must be unable to hang. The daily log is mined at the next
    goodnight, which is exactly "the instructions we have."
  - Wake arming is done by DIRECT state persistence (pending_wake +
    _save_state()), NOT scheduler.arm_wake() — arm_wake kicks the live wake
    loop, and a dying process must persist state, never start machinery.
  - Double-fire guarded; the handler always hands control back to the default
    death path (KeyboardInterrupt for SIGINT so the bot closes gracefully,
    re-raised SIGTERM otherwise).

Palace close:
  After the persist step, _close_palace() flushes and closes the live
  collection held in the palace module. This prevents the index metadata file
  from being truncated by a mid-write interrupted by SIGINT/SIGTERM. The
  close() call is fast and safe in this context.
"""

import logging
import os
import signal
from datetime import datetime

log = logging.getLogger("galadriel.last_word")


def _should_arm_wake(now: datetime) -> bool:
    """Arm a recovery wake unless the shutdown falls inside the quiet window.

    The window is read from GALADRIEL_NIGHTLY_STOP_HOUR at call time: an hour
    (0-23). Unset or invalid means there is no quiet window and a wake is
    always armed; when set, a wake is armed only before that hour.
    """
    raw = os.environ.get("GALADRIEL_NIGHTLY_STOP_HOUR")
    if raw is None:
        return True
    try:
        stop_hour = int(raw)
    except (TypeError, ValueError):
        return True
    if not 0 <= stop_hour <= 23:
        return True
    return now.hour < stop_hour


def _close_palace() -> None:
    """Flush and close the live collection held in the palace module.

    Prevents index metadata truncation when SIGINT/SIGTERM interrupts a
    mid-write flush. Safe to call from a signal handler — fast, no network.
    """
    try:
        import harness.palace as palace
        col = palace._drawers_collection()
        if hasattr(col, "close"):
            col.close()
            log.info("LAST WORD: palace collection closed cleanly.")
        else:
            log.warning("LAST WORD: palace collection has no close() method.")
    except Exception as e:
        log.warning(f"LAST WORD: palace close failed (non-fatal): {e}")


def compose_last_word(signame: str, scheduler, now: datetime) -> str:
    """The daily-log record of the dying state. Pure function — tested."""
    hb = "off"
    if getattr(scheduler, "heartbeat_enabled", False):
        prompt = getattr(scheduler, "heartbeat_prompt", None) or ""
        topic = prompt.split("]")[0].lstrip("[") if "]" in prompt else "default"
        hb = f"ON ({getattr(scheduler, 'heartbeat_interval', '?')}m, {topic})"
    return (
        f"LAST WORD ({signame}): process terminated WITHOUT an armed wake at "
        f"{now.strftime('%H:%M:%S')} — auto-persisting per the termination "
        f"covenant. Heartbeat was {hb}. Verbatim context survives in "
        f"memory/journal/{now.strftime('%Y-%m-%d')}.jsonl; recent intent in "
        f"this daily log above. "
        + (
            "Recovery wake ARMED for next boot."
            if _should_arm_wake(now)
            else "Nightly stop window — no wake armed (morning routine covers re-entry)."
        )
    )


def compose_wake_prompt(signame: str, now: datetime) -> str:
    """Self-contained wake for the next boot (zero conversational memory)."""
    day = now.strftime("%Y-%m-%d")
    return (
        f"[SYSTEM:WAKE:UNPLANNED-SHUTDOWN] The previous process was terminated "
        f"by {signame} at {now.strftime('%H:%M')} on {day} WITHOUT a deliberately "
        f"armed wake — this recovery wake was auto-armed by THE LAST WORD "
        f"handler as it died. You may have been mid-task. Recover context: "
        f"(1) read memory/{day}.md (the LAST WORD entry marks the moment of "
        f"death and heartbeat state); (2) journal memory/journal/{day}.jsonl "
        f"holds the verbatim thread; (3) palace_diary_read(last_n=3) for "
        f"intent. If a task was mid-flight, resume or report it honestly. If "
        f"everything is accounted for, say so briefly."
    )


def install(agent, scheduler) -> None:
    """Install SIGINT/SIGTERM last-word handlers. Call once, before the bot runs."""
    fired = {"done": False}

    def _handler(signum, frame):
        signame = signal.Signals(signum).name
        if not fired["done"]:
            fired["done"] = True
            try:
                now = datetime.now()
                deliberate = bool(getattr(scheduler, "pending_wake", None))
                if deliberate:
                    # Deliberate == no: the armed wake IS the persistence.
                    log.info(
                        f"LAST WORD ({signame}): deliberate shutdown — wake "
                        f"already armed, no auto-persist."
                    )
                else:
                    entry = compose_last_word(signame, scheduler, now)
                    agent.memory.append_daily_log(entry)
                    if _should_arm_wake(now):
                        # Direct persistence — never arm_wake() from a dying
                        # process (it kicks the live loop).
                        scheduler.pending_wake = compose_wake_prompt(signame, now)
                        scheduler._save_state()
                    log.info(f"LAST WORD ({signame}): persisted. {entry[:120]}")
            except Exception as e:  # never block death on a persist failure
                log.error(f"LAST WORD failed: {e}")
            finally:
                # Always try to close the palace cleanly, regardless of persist
                # outcome. Prevents index truncation on mid-write kills.
                _close_palace()
        # Hand back to the default death path.
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    log.info("THE LAST WORD installed: termination now persists by default "
             "(deliberate/armed shutdowns exempt).")
