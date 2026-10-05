"""THE QUIET LOG — the werkzeug access-line filter must drop success, keep error.

Born 2026-09-22 after the journald log was found spammed by the relay's
`GET /api/local/dequeue` long-poll (every ~2s, 204) and per-request httpx
lines. The filter is categorical on purpose: a path whitelist always loses to
the next poller.

REWRITTEN 2026-09-22 (v2) — the v1 test fed SYNTHETIC CLEAN records and so
verified the regex, not the pipeline. The live filter was half-alive for hours:
werkzeug ANSI-colors the request text before emitting it, so `HTTP/1.1"` never
matched. `test_real_werkzeug_request_is_dropped` now drives an ACTUAL Flask
server and asserts the real emitted record is dropped — substance, not shape.
Every unit record below is ANSI-wrapped to mirror what werkzeug really sends.
"""

import io
import logging
import threading
import time
import urllib.request

from flask import Flask

from harness.log_quiet import (
    _ACCESS_OK_RE,
    _ANSI_RE,
    _WerkzeugPollFilter,
    quiet_log_noise,
)

# What werkzeug actually emits — SGR codes around the request text.
_ANSI_GET = '\x1b[35m\x1b[1mGET {path} HTTP/1.1\x1b[0m'


def _rec(msg: str) -> logging.LogRecord:
    return logging.LogRecord("werkzeug", logging.INFO, __file__, 1, msg, None, None)


def test_success_access_lines_dropped_any_path():
    f = _WerkzeugPollFilter()
    for path in ("/api/local/dequeue", "/api/usage", "/api/compass",
                 "/api/status", "/api/sync/shadow", "/chat"):
        line = f'127.0.0.1 - - [22/Sep/2026 09:25:48] "{_ANSI_GET.format(path=path)}" 204 -'
        assert f.filter(_rec(line)) is False, path


def test_error_access_lines_kept_ansi_or_plain():
    f = _WerkzeugPollFilter()
    for code in ("400", "404", "500"):
        plain = f'127.0.0.1 - - [22/Sep/2026 09:25:48] "POST /api/keys HTTP/1.1" {code} -'
        ansi = f'127.0.0.1 - - [22/Sep/2026 09:25:48] "\x1b[1m\x1b[31mPOST /api/keys HTTP/1.1\x1b[0m" {code} -'
        assert f.filter(_rec(plain)) is True, code
        assert f.filter(_rec(ansi)) is True, f"ansi {code}"


def test_dev_server_banner_dropped():
    f = _WerkzeugPollFilter()
    assert f.filter(_rec("* Running on http://0.0.0.0:8080")) is False
    assert f.filter(_rec("Press CTRL+C to quit")) is False
    # ANSI-stained banner (as it really arrives) is still dropped.
    assert f.filter(_rec("\x1b[31m\x1b[1mWARNING: This is a development server.\x1b[0m")) is False


def test_non_access_messages_kept():
    f = _WerkzeugPollFilter()
    assert f.filter(_rec("Something genuinely wrong")) is True
    assert f.filter(_rec("GET /api/health")) is True  # not a shaped access line


def test_ansi_is_stripped_before_matching():
    # The regression guard: a colored access line MUST match after stripping.
    colored = f'"\x1b[35m\x1b[1mGET /api/local/dequeue HTTP/1.1\x1b[0m" 204 -'
    assert _ACCESS_OK_RE.search(colored) is None, "raw colored line must NOT match"
    assert _ACCESS_OK_RE.search(_ANSI_RE.sub("", colored)) is not None, "stripped must match"


def test_quiet_log_noise_is_idempotent_and_sets_httpx():
    quiet_log_noise()
    quiet_log_noise()  # must not double-install
    wk = logging.getLogger("werkzeug")
    assert sum(isinstance(x, _WerkzeugPollFilter) for x in wk.filters) == 1
    assert logging.getLogger("httpx").level == logging.WARNING
