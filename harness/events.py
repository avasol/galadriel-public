"""EVENT IDENTITY — how each kind of waking announces itself.

The wound this heals: a deliberate restart and a routine morning both arrived
as a bare wall of text, and a wake that went silent was indistinguishable from
a wake that never happened. Every autonomous event had the *same* shape at the
door — so the operator could not tell, at a glance, what had just spoken.

THIS MODULE IS THE SINGLE SOURCE OF TRUTH. The scheduler channel_id IS the
event key; every transport reads its identity from here, so the doors can never
drift apart. One event, one glyph, one human line.

PRESENTATION-ONLY. This module never touches journal or history — the agent's
own words are the record; the header is the *envelope*, applied at the outbound
boundary and nowhere earlier (mirrors response_status.present()). Add a new
autonomous routine → add it here, not by hand-rolling a string at the call site.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    glyph: str   # the mark seen first
    label: str   # short, human, Title Case — the "what kind of waking"
    motto: str   # one human line, present tense — the "why it is speaking"


# The registry. Keyed by the scheduler channel_id / transport item 'event' field.
EVENTS: dict[str, Event] = {
    "morning":    Event("🌅", "Morning",    "a new day begins"),
    "wake":       Event("🧝\u200d♀️", "Cycled", "a fresh body; the mind is intact"),
    "heartbeat":  Event("💓", "Heartbeat",  "a quiet check-in"),
    "reflection": Event("🌙", "Unbidden",   "a thought, surfacing on its own"),
    "goodnight":  Event("🕯️", "Goodnight",  "the day closes; entering rest"),
    "council":    Event("⚖️", "Council",    "nightly counsel — both advisors heard"),
    "monthly":    Event("🗓️", "Monthly",    "a scheduled monthly event"),
    "job":        Event("⏱️", "Job",        "a background task reports"),
    "herald":     Event("🚪", "Herald",     "a knock at the Door"),
    "task":       Event("🜂", "Task",       "a watched task reports"),
}


def _lookup(event: str | None) -> Event | None:
    if not event:
        return None
    return EVENTS.get(str(event).strip().lower())


def header(event: str | None) -> str:
    """The one-line identity banner, or '' for an unknown/absent event.

    Unknown keys degrade silently to no header — never banner, never guess.
    """
    e = _lookup(event)
    if not e:
        return ""
    return f"{e.glyph} **{e.label}** · _{e.motto}_"


def apply(event: str | None, message: str) -> str:
    """Prepend the event header to a message. No-op when the event is unknown."""
    head = header(event)
    if not head:
        return message
    return f"{head}\n\n{message}"


def title(event: str | None) -> str:
    """Compact label for a UI panel, e.g. '🌅 Morning'. '' when unknown."""
    e = _lookup(event)
    return f"{e.glyph} {e.label}" if e else ""


def is_event(event: str | None) -> bool:
    return _lookup(event) is not None
