"""THE JOURNAL — append-only conversation history.

Under test: items are immutable artifacts with stable content-hashed ids;
multimodal content survives as JSON; torn tail lines (crash mid-write) do not
poison the day; cursor reads return strictly-newer items; and merge() — the
entire sync algebra — unions without duplicates and orders by time.
"""

import json

from harness.journal import ConversationJournal


def _j(tmp_path):
    return ConversationJournal(tmp_path)


def test_append_and_read_back(tmp_path):
    j = _j(tmp_path)
    item = j.append("user", "hello there", channel="tower")
    day = item["ts"][:10]
    got = j.read_day(day)
    assert len(got) == 1
    assert got[0] == item
    assert got[0]["role"] == "user"
    assert got[0]["channel"] == "tower"
    assert len(got[0]["id"]) == 16


def test_multimodal_content_stored_as_json(tmp_path):
    j = _j(tmp_path)
    content = [{"type": "text", "text": "look"}, {"type": "image", "data": "..."}]
    item = j.append("user", content)
    assert isinstance(item["content"], str)
    assert json.loads(item["content"])[0]["text"] == "look"


def test_torn_tail_line_is_skipped(tmp_path):
    j = _j(tmp_path)
    item = j.append("assistant", "intact artifact")
    day = item["ts"][:10]
    # Simulate a crash mid-write: a torn, unparseable tail line.
    with open(j.dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write('{"id": "torn')
    got = j.read_day(day)
    assert len(got) == 1
    assert got[0]["content"] == "intact artifact"


def test_items_since_cursor(tmp_path):
    j = _j(tmp_path)
    a = j.append("user", "first")
    b = j.append("assistant", "second")
    assert [i["id"] for i in j.items_since()] == [a["id"], b["id"]]
    newer = j.items_since(cursor_ts=a["ts"])
    assert [i["id"] for i in newer] == [b["id"]]
    assert j.items_since(cursor_ts=b["ts"]) == []


def test_merge_unions_without_duplicates(tmp_path):
    j = _j(tmp_path)
    a = j.append("user", "one")
    b = j.append("assistant", "two")
    c = j.append("user", "three")
    # Two bodies: one holds [a, b], the other [b, c].
    merged = ConversationJournal.merge([a, b], [b, c])
    assert [i["id"] for i in merged] == [a["id"], b["id"], c["id"]]


def test_ids_differ_across_channels_and_content(tmp_path):
    j = _j(tmp_path)
    x = j.append("user", "same words", channel="tower")
    y = j.append("user", "same words", channel="discord")
    assert x["id"] != y["id"]


def test_agent_respond_journals_user_item(tmp_path):
    """Wiring smoke: respond() files the user item before the provider call —
    even when the provider immediately raises (crash-durability)."""
    import asyncio
    from unittest.mock import MagicMock, patch

    with patch("harness.agent.make_provider") as mk, \
         patch("harness.agent.MemoryManager") as mm:
        mm.return_value.memory_dir = tmp_path
        mm.return_value.build_system_blocks.return_value = [
            {"type": "text", "text": "soul"}]
        provider = MagicMock()
        async def boom(**kwargs):
            raise RuntimeError("provider down")
        provider.create = boom
        mk.return_value = provider

        from harness.agent import GaladrielAgent
        agent = GaladrielAgent(api_key="test", config_dir=str(tmp_path),
                               memory_dir=str(tmp_path))
        try:
            asyncio.run(agent.respond("remember me", channel_id="tower"))
        except Exception:
            pass

    items = ConversationJournal(tmp_path).items_since()
    assert any(i["role"] == "user" and i["content"] == "remember me"
               for i in items), "user item must be journaled before any provider call"
