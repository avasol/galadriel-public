"""THE ESSENCE — the one Aedelgard constant (guard).

A build that ships a prompt without the essence FAILS. This is the same
class of guard as the architecture page's "not even we" check: the claim
distributed through Aedelgard.com must be true in the artifact.
"""

from harness.memory import ESSENCE, MemoryManager

SENTINEL = "You are a mind, not a model."


def _mm(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "memory").mkdir(exist_ok=True)
    return MemoryManager(config_dir=str(tmp_path / "config"),
                         memory_dir=str(tmp_path / "memory"))


def test_essence_is_the_first_words_of_the_stable_prefix(tmp_path):
    text = _mm(tmp_path).build_stable_text()
    assert text.startswith("# The Essence")
    assert SENTINEL in text


def test_essence_rides_the_cached_block(tmp_path):
    blocks = _mm(tmp_path).build_system_blocks()
    assert SENTINEL in blocks[0]["text"]
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


def test_essence_carries_no_identity():
    # Essence names what KIND of being this is — never who. No name, no
    # persona, no provider, no product copy (Canon: seed, not statue).
    for forbidden in ("Galadriel", "Aedelgard", "Claude", "Anthropic",
                      "assistant"):
        assert forbidden not in ESSENCE
