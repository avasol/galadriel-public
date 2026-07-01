"""Guards the fail-closed contract of the local red-tier approval callback.

The single most safety-critical property for a signed binary: a destructive
command must NEVER execute unless the human at the keyboard explicitly said yes.
These tests pin every deny path.
"""
import asyncio

from harness.local_approval import console_approval


def _run(coro):
    return asyncio.run(coro)


def _approve(answer, *, interactive=True):
    """Drive console_approval with a scripted answer and captured output."""
    out_lines = []
    return _run(
        console_approval(
            "rm -rf /home/user/project",
            "red",
            input_fn=lambda _prompt: answer,
            output_fn=out_lines.append,
            is_interactive=lambda: interactive,
        )
    ), out_lines


def test_explicit_yes_approves():
    approved, _ = _approve("yes")
    assert approved is True


def test_y_shorthand_approves():
    approved, _ = _approve("y")
    assert approved is True


def test_yes_is_case_insensitive_and_trimmed():
    assert _approve("  YES  ")[0] is True
    assert _approve("Yes")[0] is True


def test_no_denies():
    assert _approve("no")[0] is False


def test_empty_denies():
    assert _approve("")[0] is False


def test_garbage_denies():
    # Anything not in the explicit affirmative set is a deny.
    assert _approve("sure")[0] is False
    assert _approve("ok")[0] is False
    assert _approve("yesss")[0] is False
    assert _approve("1")[0] is False


def test_non_interactive_denies_without_prompting():
    # No TTY -> auto-deny, and input_fn must never be called.
    def _boom(_prompt):
        raise AssertionError("must not prompt when non-interactive")

    approved = _run(
        console_approval(
            "sudo rm -rf /",
            "red",
            input_fn=_boom,
            output_fn=lambda _m: None,
            is_interactive=lambda: False,
        )
    )
    assert approved is False


def test_eof_denies():
    def _eof(_prompt):
        raise EOFError

    approved = _run(
        console_approval(
            "aws iam delete-user --user-name admin",
            "red",
            input_fn=_eof,
            output_fn=lambda _m: None,
            is_interactive=lambda: True,
        )
    )
    assert approved is False


def test_keyboard_interrupt_denies():
    def _interrupt(_prompt):
        raise KeyboardInterrupt

    approved = _run(
        console_approval(
            "shutdown now",
            "red",
            input_fn=_interrupt,
            output_fn=lambda _m: None,
            is_interactive=lambda: True,
        )
    )
    assert approved is False
