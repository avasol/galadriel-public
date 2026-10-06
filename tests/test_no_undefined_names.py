"""A guard against the NameError class: a name read in a function scope that is
bound nowhere in its module raises only when that line runs, which structural
tests miss. This walks each module's symbol tables and fails on
any name that is read as a global but is bound nowhere in the module and is
not a builtin."""
import builtins
import symtable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = ["harness/agent.py", "harness/tools.py", "harness/scheduler.py", "harness/last_word.py",
         "harness/ext_runtime.py", "harness/extensions.py", "harness/body_identity.py",
         "harness/memory.py", "tower/app.py", "main.py"]
ALLOWED = {"__file__", "__name__", "__doc__", "__spec__", "__builtins__", "__package__",
           "__loader__", "__path__", "WindowsError", "unicode"}


def _undefined(path: Path):
    src = path.read_text(encoding="utf-8")
    top = symtable.symtable(src, str(path), "exec")
    bound = {s.get_name() for s in top.get_symbols()
             if s.is_assigned() or s.is_imported() or s.is_namespace()}
    bad = set()

    def walk(t):
        for s in t.get_symbols():
            if (s.is_referenced() and (s.is_global() and not s.is_declared_global())
                    and s.get_name() not in bound and not hasattr(builtins, s.get_name())
                    and s.get_name() not in ALLOWED):
                bad.add(f"{t.get_name()}:{s.get_name()}")
        for c in t.get_children():
            walk(c)
    for c in top.get_children():
        walk(c)
    return sorted(bad)


@pytest.mark.parametrize("rel", FILES)
def test_no_undefined_global_names(rel):
    assert _undefined(ROOT / rel) == []
