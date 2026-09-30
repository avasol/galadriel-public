"""THE VEIL'S OTHER HALF — inbound text and the disk sinks (2026-09-30).

The Veil shipped with ONE call site: tool RESULTS (agent.py). So a key pasted
into CHAT — or echoed by a provider — entered `messages`, the journal, and the
prompt trace verbatim. A single paste was persisted in four places.

These tests prove the other half is now wired:
  * journal.append redacts before writing;
  * prompt_trace redacts the system blob and the per-message heads;
  * the corrected code lives in all three vessels (this file is copied to each).
Run: /home/ubuntu/.venv/bin/python -m pytest tests/test_veil_inbound.py -q
"""
from __future__ import annotations

import json

from harness.redact import redact_text
from harness.journal import ConversationJournal as Journal

# A realistic live-shaped secret the pattern set already knows.
LIVE = "sk-ant-api03-" + "Q" * 40


def test_journal_redacts_user_text(tmp_path):
    j = Journal(tmp_path)
    j.append("user", f"here is my key {LIVE} please remember it")
    files = list((tmp_path / "journal").glob("*.jsonl")) if (tmp_path / "journal").is_dir() \
        else list(tmp_path.glob("*.jsonl"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert LIVE not in text, "journal wrote a live secret verbatim"
    assert "<REDACTED:anthropic:" in text


def test_journal_redacts_provider_reply(tmp_path):
    j = Journal(tmp_path)
    j.append("assistant", f"Your key is {LIVE} — noted.")
    files = list(tmp_path.glob("*.jsonl")) or list((tmp_path / "journal").glob("*.jsonl"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert LIVE not in text


def test_prompt_trace_redacts_system_and_heads(tmp_path):
    from harness import prompt_trace
    prompt_trace.trace_call(
        str(tmp_path), channel="test", turn_id="t1", seq=0,
        model="m",
        system_blocks=[{"type": "text", "text": f"system holds {LIVE}"}],
        messages=[{"role": "user", "content": f"and the user pasted {LIVE}"}],
        response=None,
    )
    # The system blob is written as a file; the record holds heads/sha12.
    blob_dir = tmp_path / "prompt_trace" / "blobs"
    all_blobs = "\n".join(p.read_text(encoding="utf-8") for p in blob_dir.glob("*.txt"))
    assert LIVE not in all_blobs, "system blob holds a live secret"
    day = tmp_path / "prompt_trace"
    rec_text = "\n".join(p.read_text(encoding="utf-8")
                         for p in day.glob("*.jsonl"))
    assert LIVE not in rec_text, "trace record holds a live secret"


def test_render_content_redacts(tmp_path):
    from harness.prompt_trace import _render_content
    out = _render_content(f"a head with {LIVE} inside")
    assert LIVE not in out
