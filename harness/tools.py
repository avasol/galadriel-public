"""Tool definitions and execution for the agent."""

import asyncio
import os
from pathlib import Path

TOOL_DEFINITIONS = [
    {
        "name": "run_shell",
        "description": (
            "Execute a shell command on the EC2 instance. "
            "Use for AWS CLI, git, file operations, system commands, python scripts, etc. "
            "Commands run in the project working directory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory. Defaults to the project root.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file from the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file on the local filesystem. Creates parent directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "memory_log",
        "description": "Append an entry to today's memory log. Use this to persist important information across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "string",
                    "description": "The memory entry to log.",
                },
            },
            "required": ["entry"],
        },
    },
]


async def execute_tool(name: str, inputs: dict, memory_manager=None, working_dir: str = None) -> str:
    """Execute a tool and return the result as a string. All operations are non-blocking."""
    if name == "run_shell":
        return await _run_shell(inputs["command"], inputs.get("working_dir", working_dir))
    elif name == "read_file":
        return await _read_file(inputs["path"])
    elif name == "write_file":
        return await _write_file(inputs["path"], inputs["content"])
    elif name == "memory_log":
        if memory_manager:
            memory_manager.append_daily_log(inputs["entry"])
            return "Logged to daily memory."
        return "Memory manager not available."
    else:
        return f"Unknown tool: {name}"


async def _run_shell(command: str, working_dir: str = None) -> str:
    """Execute a shell command asynchronously with a timeout."""
    cwd = working_dir or os.getcwd()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "[error] Command timed out after 120 seconds."

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += f"\n[stderr] {stderr.decode('utf-8', errors='replace')}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output.strip() or "(no output)"
    except Exception as e:
        return f"[error] {e}"


async def _read_file(path: str) -> str:
    """Read a file's contents without blocking the event loop."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _read_file_sync, path)
    except Exception as e:
        return f"[error] {e}"


def _read_file_sync(path: str) -> str:
    """Synchronous file read, run in executor."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error] File not found: {path}"
    if p.stat().st_size > 500_000:
        return f"[error] File too large ({p.stat().st_size} bytes). Use run_shell with head/tail instead."
    return p.read_text(encoding="utf-8")


async def _write_file(path: str, content: str) -> str:
    """Write content to a file without blocking the event loop."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _write_file_sync, path, content)
    except Exception as e:
        return f"[error] {e}"


def _write_file_sync(path: str, content: str) -> str:
    """Synchronous file write, run in executor."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"
