"""Runtime that loads APPROVED code extensions.

See docs/EXTENSIONS.md for the extension model and the ctx API contract.
This module imports and runs extension code only after it has been approved
and its hash still matches the approval; registration is all-or-nothing.
"""
import asyncio
import importlib.util
import json
import os
import threading
from pathlib import Path

from harness import extensions as ex
from harness import tools as _tools

CAPABILITY_ENV = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "openai": "OPENAI_API_KEY",
}

DEFAULT_HOOK_TIMEOUTS = {
    "on_turn_end": 2.0,
    "on_boot": 10.0,
    "on_goodnight": 10.0,
    "on_termination": 2.0,
}

TIERS = ("green", "yellow", "red")

_CORE_TOOL_NAMES = {d.get("name") for d in _tools.TOOL_DEFINITIONS}


class _Log:
    """Minimal logger handed to extensions as ctx.log."""

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class _Ctx:
    """The object handed to an extension's register(ctx)."""

    def __init__(self, runtime, name, body, ext_dir, manifest):
        self.name = name
        self.body = body
        self.data_dir = ext_dir / "data"
        self.local_dir = ext_dir / "local"
        self.data_dir.mkdir(exist_ok=True)
        self.local_dir.mkdir(exist_ok=True)
        self.log = _Log()
        self._runtime = runtime
        self._manifest = manifest
        self._tools = []
        self._hooks = []

    def tool(self, name, definition, handler, tier="green"):
        if tier not in TIERS:
            raise ex.ExtensionError(f"tier must be one of {TIERS}")
        if not isinstance(definition, dict):
            raise ex.ExtensionError("definition must be an object")
        if not isinstance(definition.get("description"), str):
            raise ex.ExtensionError("definition needs a str description")
        if not isinstance(definition.get("input_schema"), dict):
            raise ex.ExtensionError("definition needs a dict input_schema")
        declared = self._manifest.get("contributes", {}).get("tools", [])
        if name not in declared:
            raise ex.ExtensionError(f"tool {name} is not declared in the manifest")
        if name in _CORE_TOOL_NAMES:
            raise ex.ExtensionError(f"tool {name} shadows a core tool")
        if self._runtime.has_tool(name):
            raise ex.ExtensionError(f"tool {name} is already provided by another extension")
        self._tools.append((name, definition, handler, tier))

    def hook(self, event, fn):
        declared = self._manifest.get("contributes", {}).get("hooks", [])
        if event not in declared:
            raise ex.ExtensionError(f"hook {event} is not declared in the manifest")
        self._hooks.append((event, fn))

    def secret(self, slot):
        declared = [s.get("slot") for s in self._manifest.get("keyring_slots", [])]
        if slot not in declared:
            return None
        env = CAPABILITY_ENV.get(slot)
        if env is None:
            return None
        return os.environ.get(env)

    def daily_log(self, text):
        self._runtime._daily_log(f"[ext:{self.name}] {text}")

    def palace_search(self, query, k=5):
        from harness import palace
        return palace.search(query, k=k)

    async def palace_add(self, content, topic=None, room=None,
                         origin="reflection", confidence=1.0):
        if "palace_write" not in self._manifest.get("permissions", []):
            raise ex.ExtensionError("palace_add requires the palace_write permission")
        from harness import palace
        result = await palace.add_drawer(
            content=content, topic=topic, room=room, origin=origin, confidence=confidence)
        self.daily_log(f"palace_add: {content[:80]}")
        return result


class Runtime:
    """Holds the loaded tools and hooks of all approved code extensions."""

    def __init__(self, data_root, body=None, daily_log=None, hook_timeouts=None,
                 tool_timeout=120.0):
        self.data_root = Path(data_root)
        self.body = body
        if daily_log is None:
            from harness.memory import MemoryManager
            mm = MemoryManager(str(self.data_root / "config"), str(self.data_root / "memory"))
            self._daily_log = mm.append_daily_log
        else:
            self._daily_log = daily_log
        self.hook_timeouts = dict(DEFAULT_HOOK_TIMEOUTS)
        if hook_timeouts:
            self.hook_timeouts.update(hook_timeouts)
        self.tool_timeout = tool_timeout
        self.loaded = []
        self.errors = {}
        self._tools = {}
        self._hooks = {}
        self._strikes = {}
        self._lock = threading.RLock()

    @classmethod
    def load(cls, data_root, body=None, daily_log=None, hook_timeouts=None,
             tool_timeout=120.0):
        r = cls(data_root, body=body, daily_log=daily_log,
                hook_timeouts=hook_timeouts, tool_timeout=tool_timeout)
        r._load_all()
        return r

    def _load_all(self):
        for row in ex.enabled_code(self.data_root, body=self.body):
            self._load_one(row)

    def _load_one(self, row):
        name = row["name"]
        ext_dir = Path(row["path"])
        manifest = row["manifest"]
        # Re-hash immediately before import; a change revokes approval.
        if ex.extension_hash(ext_dir) != row["hash"]:
            self.errors[name] = "code changed since approval — approve again"
            return
        try:
            module = self._import_module(name, row["hash"], ext_dir)
            ctx = _Ctx(self, name, self.body, ext_dir, manifest)
            register = getattr(module, "register", None)
            if register is None:
                raise ex.ExtensionError("extension has no register(ctx)")
            register(ctx)
            for tname, tdef, handler, tier in ctx._tools:
                self._tools[tname] = {
                    "name": tname, "definition": tdef, "handler": handler,
                    "tier": tier, "ext": name,
                }
            for event, fn in ctx._hooks:
                self._hooks.setdefault(event, []).append((name, fn))
            self.loaded.append(name)
        except Exception as e:
            self.errors[name] = f"{type(e).__name__}: {e}"

    def _import_module(self, name, hash_, ext_dir):
        mod_name = f"_ext_{name}_{hash_}"
        spec = importlib.util.spec_from_file_location(mod_name, ext_dir / "extension.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def tool_definitions(self):
        out = []
        for t in self._tools.values():
            d = dict(t["definition"])
            d["name"] = t["name"]
            out.append(d)
        return out

    def has_tool(self, name):
        return name in self._tools

    def tier(self, name):
        return self._tools[name]["tier"]

    def owner(self, name):
        return self._tools[name]["ext"]

    async def call(self, name, inputs):
        tool = self._tools[name]
        handler = tool["handler"]
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(handler(inputs), timeout=self.tool_timeout)
            else:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, handler, inputs),
                    timeout=self.tool_timeout)
            if isinstance(result, str):
                return result
            return json.dumps(result)
        except asyncio.TimeoutError:
            return f"[extension {tool['ext']} tool {name} failed] timed out after {self.tool_timeout}s"
        except Exception as e:
            return f"[extension {tool['ext']} tool {name} failed] {type(e).__name__}: {e}"

    async def fire(self, event, *args):
        timeout = self.hook_timeouts.get(event, 2.0)
        for name, fn in list(self._hooks.get(event, [])):
            try:
                if asyncio.iscoroutinefunction(fn):
                    await asyncio.wait_for(fn(*args), timeout=timeout)
                else:
                    await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, fn, *args),
                        timeout=timeout)
                self._strikes.pop((name, event), None)
            except asyncio.TimeoutError:
                self._strike(name, event, timeout)
            except Exception:
                self._strikes.pop((name, event), None)

    def _strike(self, name, event, timeout):
        key = (name, event)
        n = self._strikes.get(key, 0) + 1
        self._strikes[key] = n
        if n >= 3:
            ex.disable(self.data_root, name, body=self.body,
                       reason=f"{event} timed out 3 times in a row")
            self._drop(name)

    def _drop(self, name):
        self._tools = {k: v for k, v in self._tools.items() if v["ext"] != name}
        for event in list(self._hooks):
            self._hooks[event] = [(n, f) for (n, f) in self._hooks[event] if n != name]
            if not self._hooks[event]:
                del self._hooks[event]
        if name in self.loaded:
            self.loaded.remove(name)

    def fire_sync(self, event, *args, timeout=2.0):
        threads = []
        for name, fn in list(self._hooks.get(event, [])):
            t = threading.Thread(target=self._run_hook_sync, args=(name, event, fn, args, timeout),
                                 daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout + 0.1)

    def _run_hook_sync(self, name, event, fn, args, timeout):
        result = {}

        def target():
            try:
                if asyncio.iscoroutinefunction(fn):
                    result["v"] = asyncio.run(fn(*args))
                else:
                    result["v"] = fn(*args)
            except Exception as e:
                result["e"] = e

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self._strike(name, event, timeout)
        else:
            self._strikes.pop((name, event), None)


_SINGLETON = None
_GENERATION = 0


def current():
    return _SINGLETON


def generation():
    return _GENERATION


def reload(data_root, body=None, **kw):
    global _SINGLETON, _GENERATION
    _SINGLETON = Runtime.load(data_root, body=body, **kw)
    _GENERATION += 1
    return _SINGLETON