"""Bounded, canonical, episode-local memory for live controllers."""

from __future__ import annotations

from typing import Any, Mapping

from .protocol import canonical_json_bytes, strict_json_loads

MAX_EPISODE_MEMORY_BYTES = 2048


class EpisodeMemoryError(ValueError):
    """Episode memory is invalid, oversized, or no longer available."""


class EpisodeMemory:
    """Own one canonical JSON object and erase its mutable bytes when closed.

    The object deliberately has no persistence API.  A game owns one instance per participant,
    replaces it only with participant-visible derived state, and closes it at the episode boundary.
    """

    __slots__ = ("_closed", "_value")

    def __init__(self) -> None:
        self._value = bytearray(b"{}")
        self._closed = False

    def __repr__(self) -> str:
        return f"EpisodeMemory(bytes={len(self._value)}, closed={self._closed})"

    def replace(self, value: Mapping[str, Any]) -> None:
        if self._closed:
            raise EpisodeMemoryError("episode memory is closed")
        if not isinstance(value, Mapping):
            raise TypeError("episode memory must be a JSON object")
        encoded = canonical_json_bytes(dict(value))
        if len(encoded) > MAX_EPISODE_MEMORY_BYTES:
            raise EpisodeMemoryError("episode memory exceeds 2048 UTF-8 bytes")
        self._erase()
        self._value.extend(encoded)

    @property
    def snapshot(self) -> Mapping[str, Any]:
        if self._closed:
            raise EpisodeMemoryError("episode memory is closed")
        value = strict_json_loads(self._value)
        # Construction only accepts objects; keep the invariant local.
        if not isinstance(value, dict):
            raise EpisodeMemoryError("episode memory is not an object")
        return value

    @property
    def utf8(self) -> bytes:
        if self._closed:
            raise EpisodeMemoryError("episode memory is closed")
        return bytes(self._value)

    def reset(self) -> None:
        self.replace({})

    def close(self) -> None:
        if not self._closed:
            self._erase()
            self._closed = True

    def _erase(self) -> None:
        for index in range(len(self._value)):
            self._value[index] = 0
        self._value.clear()


__all__ = [
    "EpisodeMemory",
    "EpisodeMemoryError",
    "MAX_EPISODE_MEMORY_BYTES",
]
