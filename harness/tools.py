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
    {
        "name": "palace_search",
        "description": (
            "Semantic search over the verbatim memory palace (MemPalace). "
            "Use this to recall past conversations, decisions, code changes, or facts "
            "that are not in the current context window or daily logs. "
            "Zero API cost — searches run locally against ChromaDB."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language query. Works best with full phrases, not keywords.",
                },
                "wing": {
                    "type": "string",
                    "description": "Optional wing filter (top-level namespace).",
                },
                "room": {
                    "type": "string",
                    "description": "Optional room filter (folder-based — memory, harness, tower, etc).",
                },
                "hall": {
                    "type": "string",
                    "description": "Optional hall filter (keyword-based auto-topic — decisions, problems, milestones, etc). Best scope for cross-cutting topic recall.",
                },
                "k": {
                    "type": "integer",
                    "description": "Number of results (default 5, max 20).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "palace_add_drawer",
        "description": (
            "File a verbatim fact or memory directly into the palace *right now*. "
            "Unlike memory_log (which only writes to today's daily log and isn't "
            "palace-searchable until the next mine), this tool makes the content "
            "immediately retrievable via palace_search. "
            "Use sparingly — only for facts worth remembering across sessions "
            "(decisions, discoveries, one-off configuration, durable context). "
            "Everyday chatter belongs in the daily log."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The verbatim content to file. Write it the way you want to read it back.",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional short topic hint (used for the filename slug).",
                },
                "wing": {
                    "type": "string",
                    "description": "Wing to file under (default 'agent').",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "palace_wake_up",
        "description": (
            "Fetch a fresh L0+L1 wake-up snapshot from the palace (~800 tokens). "
            "Different from the auto-injected wake-up in your system prompt: this "
            "runs live, optionally filtered to a single wing, so you can pull a "
            "targeted palace overview mid-conversation. Useful when you suspect "
            "the auto-injected wake-up is stale, or when you need a focused "
            "recall of a specific project/person."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {
                    "type": "string",
                    "description": "Optional wing filter (project or person). Omit for a global snapshot.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "palace_taxonomy",
        "description": (
            "Show how the palace is organized: wings, rooms, drawer counts per room, "
            "and halls (auto-topic labels). Use this before a targeted search when "
            "you want to know which room/hall to filter on. Zero API cost."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "palace_kg_add",
        "description": (
            "File a fact into the knowledge graph as a (subject, predicate, object) "
            "triple with a validity window. Use for durable relational facts: "
            "`user — prefers — direct commits`, "
            "`service — runs_on — ARM64 t4g`, `project — shipped_at — 2026-04-20`. "
            "Facts can be superseded later via palace_kg_invalidate. "
            "Prefer this over palace_add_drawer for structured relations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity the fact is about."},
                "predicate": {"type": "string", "description": "The relationship verb (is/works_on/prefers/runs_on/ships_as/etc)."},
                "object": {"type": "string", "description": "What the subject relates to."},
                "valid_from": {"type": "string", "description": "Optional ISO date (YYYY-MM-DD) when the fact became true. Defaults to today."},
            },
            "required": ["subject", "predicate", "object"],
        },
    },
    {
        "name": "palace_kg_query",
        "description": (
            "Query the knowledge graph. Any combination of subject/predicate/object can be provided; "
            "the others are wildcards. Returns current + expired facts marked with validity status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "palace_kg_invalidate",
        "description": (
            "Mark a KG fact as no longer valid (sets valid_to). Use when a fact changes: "
            "first invalidate the old triple, then palace_kg_add the new one. Preserves history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
                "ended": {"type": "string", "description": "Optional ISO date when the fact stopped being true. Defaults to today."},
            },
            "required": ["subject", "predicate", "object"],
        },
    },
    {
        "name": "palace_kg_timeline",
        "description": (
            "Return the chronological history of all KG facts touching a given entity. "
            "Current + expired, oldest to newest. Use for 'what do we know about X over time?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name (appears as subject or object)."},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "palace_diary_write",
        "description": (
            "Write a diary entry — your personal journal. Use at end-of-session, goodnight, "
            "or any time something is worth remembering as a reflection (not as a raw fact). "
            "Different from palace_add_drawer (verbatim durable fact) and memory_log "
            "(append to today's daily log). Diary entries are your curated thoughts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry": {"type": "string", "description": "The diary entry text. Write it as you want to read it back."},
                "topic": {"type": "string", "description": "Optional topic tag (default 'general')."},
            },
            "required": ["entry"],
        },
    },
    {
        "name": "palace_diary_read",
        "description": (
            "Read the most recent N diary entries — your own reflections across past sessions. "
            "Useful on wake-up if the auto-injected L1 doesn't cover what you need."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "last_n": {"type": "integer", "description": "Number of entries to return (default 10, max 50)."},
            },
            "required": [],
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
    elif name == "palace_search":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.search(
                query=inputs["query"],
                wing=inputs.get("wing"),
                room=inputs.get("room"),
                hall=inputs.get("hall"),
                k=inputs.get("k", 5),
            ),
        )
    elif name == "palace_add_drawer":
        from . import palace
        return await palace.add_drawer(
            content=inputs["content"],
            topic=inputs.get("topic"),
            wing=inputs.get("wing", "agent"),
        )
    elif name == "palace_wake_up":
        from . import palace
        return await palace.wake_up(wing=inputs.get("wing"))
    elif name == "palace_taxonomy":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(None, palace.taxonomy)
    elif name == "palace_kg_add":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.kg_add(
                subject=inputs["subject"],
                predicate=inputs["predicate"],
                object=inputs["object"],
                valid_from=inputs.get("valid_from"),
            ),
        )
    elif name == "palace_kg_query":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.kg_query(
                subject=inputs.get("subject"),
                predicate=inputs.get("predicate"),
                object=inputs.get("object"),
            ),
        )
    elif name == "palace_kg_invalidate":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.kg_invalidate(
                subject=inputs["subject"],
                predicate=inputs["predicate"],
                object=inputs["object"],
                ended=inputs.get("ended"),
            ),
        )
    elif name == "palace_kg_timeline":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.kg_timeline(entity=inputs["entity"]),
        )
    elif name == "palace_diary_write":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.diary_write(
                entry=inputs["entry"],
                topic=inputs.get("topic", "general"),
            ),
        )
    elif name == "palace_diary_read":
        from . import palace
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: palace.diary_read(last_n=inputs.get("last_n", 10)),
        )
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
