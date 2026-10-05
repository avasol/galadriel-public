"""Silence high-frequency transport chatter without losing signal.

A loopback-only single-user control surface emits a steady stream of
successful access lines: long-poll heartbeats and other pollers hit the
web server every couple of seconds, and each outbound API call adds a
per-request INFO line. A 2xx/3xx from a transport heartbeat is not signal
— it is noise that buries the lines that matter.

WHAT IS SILENCED
- werkzeug **successful** access lines (2xx/3xx), on EVERY path. A path
  whitelist is a losing game — every new poller escapes it. Categorical
  is correct.
- the werkzeug dev-server banner (correct in general, misleading for a
  loopback-only control surface, and ANSI-tinted in a file).
- the `httpx` logger -> WARNING: a 200 here is duplicated by our own
  provider lines; a genuine failure still surfaces via the provider's own
  warning.

WHAT IS KEPT (this is the point — silencing must not blind)
- werkzeug **error** lines (4xx/5xx): a 500 access line is how a real bug
  is confirmed; it stays.
- every non-access werkzeug message, and every other logger at INFO+.

THE ANSI TRAP
werkzeug's `WSGIRequestHandler.log_request` runs the request text through
`_ansi_style(...)` *before* emitting it — UNCONDITIONALLY, independent of
the handler (it is baked into the message string, not the formatter). So
the real record reads::

    "\\x1b[35m\\x1b[1mGET /api/local/dequeue HTTP/1.1\\x1b[0m" 204 -

A pattern matching `HTTP/[0-9.]+"` — a closing quote immediately after the
version — never hits, because an ANSI reset (`\\x1b[0m`) sits *between*
them. Stripping ANSI before matching makes the filter immune to
werkzeug's coloring in any status class.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("galadriel.log_quiet")

# werkzeug ANSI-colors the request text before emitting it (see module docstring).
# Strip SGR sequences (\x1b[...m) so matching sees plain text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# An access line, post-ANSI-strip, looks like:
#   127.0.0.1 - - [date] "GET /path HTTP/1.1" 200 -
_ACCESS_OK_RE = re.compile(r'HTTP/[0-9.]+"\s+[23]\d\d\b')

# werkzeug's startup banner: correct in general, misleading here, ANSI-stained.
_BANNER_MARKS = (
    "This is a development server",
    "Press CTRL+C to quit",
    "* Running on",
)


class _WerkzeugPollFilter(logging.Filter):
    """Drop ALL successful werkzeug access lines and the dev-server banner.

    Errors (4xx/5xx) and every non-access werkzeug message still log — those
    ARE signal. Success on a loopback-only control surface is not."""

    def filter(self, record: logging.LogRecord) -> bool:  # True = keep
        try:
            # ANSI-strip before matching: werkzeug injects SGR codes into the
            # request text itself, so the raw message never matches a plain
            # access-line shape. See module docstring, "THE ANSI TRAP".
            msg = _ANSI_RE.sub("", record.getMessage())
        except Exception:
            return True
        if _ACCESS_OK_RE.search(msg):
            return False
        if any(mark in msg for mark in _BANNER_MARKS):
            return False
        return True


def quiet_log_noise() -> None:
    """Silence known transport spam without losing forensic signal.

    Idempotent: safe to call more than once (filters are guarded against
    double-install). Call once at boot, after logging.basicConfig().
    """
    # httpx per-request INFO lines -> WARNING (failures preserved downstream).
    logging.getLogger("httpx").setLevel(logging.WARNING)

    wk = logging.getLogger("werkzeug")
    if not any(isinstance(f, _WerkzeugPollFilter) for f in wk.filters):
        wk.addFilter(_WerkzeugPollFilter())
