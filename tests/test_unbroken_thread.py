"""THE UNBROKEN THREAD — recall never goes
blind between a trim and the nightly mine.

Covers the two seams:
  1. Journal federation: palace search falls back to a lexical scan of the
     verbatim journal (excluding the last 5 minutes — no self-echo).
  2. The trim receipt: routine trims leave a rich advisory naming exact
     readable paths; legacy string tags (max_tokens recovery) still render.

Run: python -m pytest tests/test_unbroken_thread.py -q
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import palace  # noqa: E402
from harness.agent import _build_trim_receipt, _render_recovery_advisory  # noqa: E402
from harness.journal import ConversationJournal  # noqa: E402


@pytest.fixture()
def journal(tmp_path):
    j = ConversationJournal(tmp_path)
    yield j
    palace.register_journal(None)  # never leak into other tests


def _write_journal_item(j, *, ts, channel="discord", role="user", content=""):
    """Write a raw JSONL item with a controlled timestamp (journal.append
    always stamps now(), which the 5-minute guard would exclude)."""
    item = {"id": "t", "ts": ts, "channel": channel, "role": role,
            "content": content}
    day_file = j.dir / f"{ts[:10]}.jsonl"
    with open(day_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(item) + "\n")


def _iso(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ── Seam 1: journal federation ─────────────────────────────────────

class TestJournalFallback:
    def test_finds_unmined_verbatim_content(self, journal):
        _write_journal_item(
            journal, ts=_iso(60),
            content="we decided the cartomancer tiles zoom grid uses movement distances")
        palace.register_journal(journal)
        out = palace._journal_fallback("cartomancer tiles zoom grid")
        assert "cartomancer" in out
        assert "Unmined journal" in out

    def test_excludes_items_younger_than_five_minutes(self, journal):
        _write_journal_item(
            journal, ts=_iso(1),
            content="cartomancer tiles zoom grid — the question being asked right now")
        palace.register_journal(journal)
        assert palace._journal_fallback("cartomancer tiles zoom grid") == ""

    def test_empty_without_registration(self):
        palace.register_journal(None)
        assert palace._journal_fallback("anything at all") == ""

    def test_min_score_filters_weak_matches(self, journal):
        _write_journal_item(journal, ts=_iso(60), content="only the word tiles here")
        palace.register_journal(journal)
        assert palace._journal_fallback(
            "cartomancer zoom grid movement", min_score=2) == ""

    def test_higher_score_ranks_first_and_k_caps(self, journal):
        _write_journal_item(journal, ts=_iso(90), content="tiles zoom")
        _write_journal_item(journal, ts=_iso(60),
                            content="tiles zoom grid movement distances all together")
        palace.register_journal(journal)
        out = palace._journal_fallback("tiles zoom grid movement distances", k=1)
        assert "all together" in out and out.count("- `") == 1

    def test_event_roles_are_ignored(self, journal):
        _write_journal_item(journal, ts=_iso(60), role="event",
                            content="cartomancer tiles zoom grid movement")
        palace.register_journal(journal)
        assert palace._journal_fallback("cartomancer tiles zoom grid") == ""

    def test_never_raises_on_corrupt_day_file(self, journal):
        (journal.dir / f"{_iso(60)[:10]}.jsonl").write_text("{not json\n")
        palace.register_journal(journal)
        assert isinstance(palace._journal_fallback("whatever query terms"), str)


# ── Seam 2: the trim receipt ───────────────────────────────────────

class TestTrimReceipt:
    def test_receipt_carries_targeted_read_paths(self, tmp_path):
        batch = tmp_path / "conversation_trim_discord_2026-07-14T18-00-00"
        r = _build_trim_receipt(tag="trim_discord", batch_dir=batch, count=12)
        assert r["tag"] == "trim_discord"
        assert r["count"] == 12
        assert r["archive_file"].endswith(
            "conversation_trim_discord_2026-07-14T18-00-00.md")
        assert r["journal_day"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def test_rich_receipt_renders_readable_now_paths(self, tmp_path):
        r = _build_trim_receipt(tag="trim_discord",
                                batch_dir=tmp_path / "b", count=3)
        text = _render_recovery_advisory(r)
        assert "TRIM-RECEIPT" in text
        assert r["archive_file"] in text
        assert f"memory/journal/{r['journal_day']}.jsonl" in text
        assert "trim_discord" in text

    def test_legacy_string_tag_still_renders(self):
        text = _render_recovery_advisory("max_tokens_recovery_123")
        assert "POST-RECOVERY-ADVISORY" in text
        assert "max_tokens_recovery_123" in text
        # The private harness's legacy wording keeps its recall guidance.
        assert "palace_search" in text

    def test_plan_archive_dir_matches_archive_layout(self):
        d = palace.plan_archive_dir("trim_discord")
        assert d.name.startswith("conversation_trim_discord_")
