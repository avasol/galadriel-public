"""Interactive red-tier approval for the LOCAL body.

Context
-------
`harness/safety.py` classifies every shell command green / yellow / red. In
`agent.py`, a red-tier command (rm, sudo rm, iam, secretsmanager, cfn
create/update/delete, ec2 terminate, force-push, shutdown, `curl … | bash`, …)
is only executed if an `approval_callback` returns True. If NO callback is set,
the command is BLOCKED (fail-closed) — safe, but a locally-run body would then
refuse every destructive command forever, even ones the user legitimately wants
on their own machine.

This module supplies the missing piece for the body: an approval callback that
ASKS THE HUMAN who is right there. Red-tier stops meaning "block forever" and
starts meaning "the person at the keyboard must say yes." That is the correct
posture for single-user local software — and it is the behaviour the Body Terms
and the first-run consent gate describe, so the legal text and the code AGREE.

Design
------
- Fail-closed by default: any ambiguity, EOF, non-interactive stdin, or an
  unrecognised answer is treated as DENY. A signed binary must never execute a
  destructive command because a prompt was unclear.
- Non-interactive environments (no TTY — e.g. a service with no console) get an
  auto-deny with a clear log line, preserving the existing safe default.
- Pure and dependency-free; unit-testable via the injected `input`/`output`.
"""
from __future__ import annotations

import sys
from typing import Callable


def _default_output(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def console_approval(
    command: str,
    tier: str,
    *,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
    is_interactive: Callable[[], bool] | None = None,
) -> bool:
    """Ask the local user to approve a red-tier command. Returns True to run.

    Fail-closed: denies on no-TTY, EOF, or any answer that is not an explicit
    affirmative. The affirmative set is deliberately small and explicit.
    """
    out = output_fn or _default_output
    interactive = is_interactive or sys.stdin.isatty

    if not interactive():
        out(
            f"🔴 Red-tier command requires approval but no interactive console "
            f"is attached — DENIED (fail-closed): {command}"
        )
        return False

    ask = input_fn or input
    out(
        "\n🔴 Aedelgard wants to run a command classified as DESTRUCTIVE "
        f"({tier}).\n    {command}\n"
        "This can delete files, change infrastructure, or spend money on your "
        "machine.\nType 'yes' to allow it, anything else to deny."
    )
    try:
        answer = ask("Allow this command? [yes/No]: ")
    except (EOFError, KeyboardInterrupt):
        out("Denied (no confirmation).")
        return False

    approved = answer.strip().lower() in {"yes", "y"}
    out("Approved." if approved else "Denied.")
    return approved
