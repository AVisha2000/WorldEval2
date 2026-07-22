import pytest
from genesis_arena.embodiment.episode_memory import (
    MAX_EPISODE_MEMORY_BYTES,
    EpisodeMemory,
    EpisodeMemoryError,
)


def test_episode_memory_is_canonical_bounded_and_replaced_atomically() -> None:
    memory = EpisodeMemory()
    memory.replace({"z": 2, "a": ["route", 1]})
    assert memory.utf8 == b'{"a":["route",1],"z":2}'
    assert memory.snapshot == {"a": ["route", 1], "z": 2}

    previous = memory.utf8
    with pytest.raises(EpisodeMemoryError, match="2048 UTF-8 bytes"):
        memory.replace({"oversized": "x" * MAX_EPISODE_MEMORY_BYTES})
    assert memory.utf8 == previous


def test_episode_memory_is_episode_local_and_erased_when_closed() -> None:
    alpha = EpisodeMemory()
    bravo = EpisodeMemory()
    alpha.replace({"owner": "alpha", "cell": [1, 2]})
    bravo.replace({"owner": "bravo", "cell": [7, 8]})

    assert alpha.utf8 != bravo.utf8
    retained_buffer = alpha._value
    alpha.close()
    assert retained_buffer == bytearray()
    assert "alpha" not in repr(alpha)
    with pytest.raises(EpisodeMemoryError, match="closed"):
        _ = alpha.utf8
    assert bravo.snapshot["owner"] == "bravo"
