"""THE PATH GUARD — a memory_dir that is an object repr is not a directory.

Wound (2026-09-20): unit tests set ``agent.memory = MagicMock()`` without a
``memory_dir``. The non-fatal writers (trace_call, archive_cascade,
shadow_observe) stringified that attribute to
``"<MagicMock name='mock.memory_dir' id='...'>"`` and mkdir'd that string
into the repository root — three junk directories per test run, one of them
full of real prompt-trace blobs.

The fix is a low-level SHAPE check at the destination: before any Path()/
mkdir/thread spawn, refuse a dir whose string form is an object repr. This
module is deliberately dependency-free so it can be imported from anywhere.
"""

from __future__ import annotations

_MOCK_MARKERS = ("MagicMock", "Mock name=", "object at 0x", "AsyncMock")


def is_mockish_dir(p) -> bool:
    """True when ``p`` is not a real path but an object repr / empty value.

    Real ``str``/``Path`` locations return False.
    """
    s = str(p)
    if not s:
        return True
    if s.startswith("<") and s.endswith(">"):
        return True
    return any(marker in s for marker in _MOCK_MARKERS)
