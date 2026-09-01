"""GALADRIEL_BENCH_SANDBOX=1 — least-privilege for untrusted eval questions
(fix shipped 2026-09-01, same day as the G1 control-arm confound fix).

Found live during a benchmark smoke run: the agent's own "be resourceful
before asking" instinct led it to `run_shell("grep -ril '<fact from the
question>' /")` — hunting the box's REAL filesystem for a LongMemEval
answer string. `working_dir` only sets the shell's cwd; it does not stop an
absolute-path command from escaping the sandbox. The runaway grep ran for
10+ minutes (well past its own 120s timeout — see test_run_shell_orphan_kill
for that half of the fix) scanning the EBS mount, Plex media, and other
repos on a shared production box.

A LongMemEval question has no legitimate reason to touch real files at all
(it tests conversational recall, not file lookup) — so the fix removes the
capability class entirely in bench mode, rather than trying to make an
absolute-path jail watertight (symlinks, `..`, env expansion all defeat
that cheaply — the same reasoning SOUL.md itself would apply to any other
half-measure sandbox).
"""

import asyncio
import os

from harness import tools


def _with_env(key, value, fn):
    prev = os.environ.get(key)
    os.environ[key] = value
    try:
        return fn()
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def test_bench_sandbox_removes_filesystem_tools_from_visible_defs():
    names = _with_env("GALADRIEL_BENCH_SANDBOX", "1",
                       lambda: {t["name"] for t in tools.visible_tool_definitions()})
    assert "run_shell" not in names
    assert "read_file" not in names
    assert "write_file" not in names


def test_bench_sandbox_off_by_default_leaves_filesystem_tools_visible():
    names = _with_env("GALADRIEL_BENCH_SANDBOX", "0",
                       lambda: {t["name"] for t in tools.visible_tool_definitions()})
    assert "run_shell" in names
    assert "read_file" in names
    assert "write_file" in names


def test_bench_sandbox_refuses_run_shell_even_if_requested_anyway():
    async def _call():
        return await tools.execute_tool("run_shell", {"command": "echo hi"})

    result = _with_env("GALADRIEL_BENCH_SANDBOX", "1", lambda: asyncio.run(_call()))
    assert "bench sandbox" in result.lower()
    assert result.startswith("[bench sandbox]")  # the refusal, not the echoed output


def test_bench_sandbox_refuses_write_file_even_if_requested_anyway(tmp_path):
    target = tmp_path / "should_not_exist.txt"

    async def _call():
        return await tools.execute_tool(
            "write_file", {"path": str(target), "content": "nope"}
        )

    result = _with_env("GALADRIEL_BENCH_SANDBOX", "1", lambda: asyncio.run(_call()))
    assert "bench sandbox" in result.lower()
    assert not target.exists()


def test_bench_sandbox_combines_with_no_palace():
    # The control arm sets BOTH flags. Confirm both filters apply together
    # rather than one masking the other.
    def _names():
        return {t["name"] for t in tools.visible_tool_definitions()}

    names = _with_env(
        "GALADRIEL_NO_PALACE", "1",
        lambda: _with_env("GALADRIEL_BENCH_SANDBOX", "1", _names),
    )
    assert "palace_search" not in names
    assert "run_shell" not in names
    # non-palace, non-filesystem tools survive both filters
    assert "memory_log" in names


def test_run_shell_still_returns_normally_for_ordinary_commands():
    # Regression guard for the start_new_session=True change: ordinary
    # commands must still work exactly as before.
    result = asyncio.run(tools._run_shell("echo hello-world"))
    assert "hello-world" in result
