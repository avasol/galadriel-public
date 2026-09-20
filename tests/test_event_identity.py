"""EVENT IDENTITY — every kind of waking announces itself.

Contract pinned here:
  - the registry is the single source of truth for event identity;
  - `events.apply()` weaves the header into outbound text, unknown is a no-op;
  - the scheduler's outbound boundary applies it (the channel_id IS the key).
"""
import asyncio
import types

from harness import events
from harness.scheduler import Scheduler


# ── the registry ────────────────────────────────────────────────────

def test_registry_has_the_autonomous_wakings():
    for key in ("morning", "wake", "heartbeat", "reflection",
                "goodnight", "council", "monthly", "job", "herald"):
        assert events.is_event(key), f"{key} missing from the event registry"


def test_discord_header_is_human_and_glyphed():
    line = events.header("morning")
    assert "Morning" in line and "🌅" in line
    assert "new day" in line          # a human phrase, not a code


def test_apply_weaves_header_and_is_a_noop_for_unknown():
    assert events.apply("goodnight", "Sleep well.").endswith("Sleep well.")
    assert "Goodnight" in events.apply("goodnight", "Sleep well.")
    # Unknown / absent events never banner, never guess.
    assert events.apply(None, "hello") == "hello"
    assert events.apply("nonsense", "hello") == "hello"


def test_title_is_compact_for_a_panel():
    assert events.title("wake").startswith("🧝")
    assert events.title("nonsense") == ""


# ── the scheduler boundary: the channel_id IS the event key ──────────

class _FakeChannel:
    def __init__(self):
        self.sent = []
        self.id = 1

    async def send(self, text):
        self.sent.append(text)


class _FakeBot:
    def __init__(self):
        self._ch = _FakeChannel()

    async def get_dm_channel(self):
        return self._ch


def _scheduler():
    s = Scheduler.__new__(Scheduler)
    s.bot = _FakeBot()
    return s


def test_discord_send_wears_the_event_header():
    s = _scheduler()
    asyncio.run(s._send_to_discord("Good morning.", channel_id="morning"))
    sent = s.bot._ch.sent[0]
    assert "🌅 **Morning**" in sent
    assert sent.endswith("Good morning.")


def test_discord_send_without_event_has_no_event_header():
    """The status line from response_status.present() is expected; the EVENT
    header must be absent when no event key is given."""
    s = _scheduler()
    asyncio.run(s._send_to_discord("an ordinary message"))
    sent = s.bot._ch.sent[0]
    assert "an ordinary message" in sent
    assert "**Morning**" not in sent and "**Cycled**" not in sent


def test_reflection_and_cycle_carry_their_own_identities():
    s = _scheduler()
    asyncio.run(s._send_to_discord("a thought", channel_id="reflection"))
    asyncio.run(s._send_to_discord("cycled clean", channel_id="wake"))
    assert "🌙 **Unbidden**" in s.bot._ch.sent[0]
    assert "🧝" in s.bot._ch.sent[1] and "Cycled" in s.bot._ch.sent[1]
