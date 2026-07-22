"""Immutable, player-scoped contracts shared by every live model provider.

Provider SDK values stop at this boundary.  Requests contain only the current player's visible
observation and frame, while results expose raw output bytes or a small sanitized failure enum.
Credentials and HTTP headers are deliberately not representable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, Tuple, runtime_checkable

from ..protocol import canonical_json_bytes, strict_json_loads

ProviderName = str
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ProviderCapabilities:
    """Small, immutable provider properties used before credentials are requested.

    These flags describe transport requirements only.  They deliberately contain no model,
    credential, endpoint, or run-specific material.
    """

    provider_name: ProviderName
    requires_credential: bool
    is_networked: bool

    def __post_init__(self) -> None:
        if not isinstance(self.provider_name, str) or not _SAFE_ID.fullmatch(self.provider_name):
            raise ValueError("provider_name must be a safe identifier")
        for name in ("requires_credential", "is_networked"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "requires_credential": self.requires_credential,
            "is_networked": self.is_networked,
        }


_PROVIDER_CAPABILITIES: Mapping[ProviderName, ProviderCapabilities] = MappingProxyType(
    {
        name: ProviderCapabilities(name, requires_credential=True, is_networked=True)
        for name in ("openai", "anthropic", "gemini")
    }
    | {
        name: ProviderCapabilities(name, requires_credential=False, is_networked=False)
        for name in ("scripted", "demo")
    }
)


def provider_capabilities(name: ProviderName) -> ProviderCapabilities:
    """Return the frozen capabilities for one registered provider, failing closed."""

    if not isinstance(name, str):
        raise TypeError("provider name must be a string")
    try:
        return _PROVIDER_CAPABILITIES[name]
    except KeyError as error:
        raise ValueError("provider is not registered") from error


class ProviderFailureKind(str, Enum):
    """Stable failures the episode runner may record without leaking provider details."""

    TIMEOUT = "timeout"
    CREDENTIAL = "credential_error"
    RATE_LIMIT = "rate_limit_error"
    QUOTA = "quota_error"
    REFUSAL = "provider_refusal"
    TRANSPORT = "transport_error"
    INVALID_RESPONSE = "invalid_response"
    OUTPUT_TOO_LARGE = "output_too_large"
    INTERNAL = "provider_error"


@dataclass(frozen=True)
class ProviderTelemetry:
    """Allow-listed accounting metadata safe for protected evidence."""

    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    request_id_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.request_id_sha256 is not None and not _SHA256.fullmatch(self.request_id_sha256):
            raise ValueError("request_id_sha256 must be lowercase SHA-256")

    def as_dict(self) -> dict[str, object]:
        return {
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "request_id_sha256": self.request_id_sha256,
        }


@dataclass(frozen=True)
class ProviderRequest:
    """One frozen provider call with no opponent or spectator state."""

    episode_id: str
    participant_id: str
    observation_seq: int
    deadline_monotonic_ns: int
    model: str
    system_prompt: str
    observation_json: bytes
    action_schema_json: bytes
    scratchpad_utf8: bytes = b""
    frame_png: bytes | None = None
    max_input_bytes: int = 8_388_608
    max_output_bytes: int = 4_096

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.episode_id) or not _SAFE_ID.fullmatch(self.participant_id):
            raise ValueError("episode_id and participant_id must be safe identifiers")
        if not _SAFE_ID.fullmatch(self.model):
            raise ValueError("model must be a safe provider model identifier")
        for name in (
            "observation_seq",
            "deadline_monotonic_ns",
            "max_input_bytes",
            "max_output_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.max_output_bytes < 1 or self.max_output_bytes > 1_048_576:
            raise ValueError("max_output_bytes must be from 1 to 1048576")
        if self.max_input_bytes < 1 or self.max_input_bytes > 67_108_864:
            raise ValueError("max_input_bytes must be from 1 to 67108864")
        if not isinstance(self.system_prompt, str) or not self.system_prompt:
            raise ValueError("system_prompt is required")
        for name in ("observation_json", "action_schema_json", "scratchpad_utf8"):
            if not isinstance(getattr(self, name), bytes):
                raise TypeError(f"{name} must be immutable bytes")
        try:
            observation = strict_json_loads(self.observation_json)
        except Exception as error:
            raise ValueError("observation_json must be strict JSON") from error
        if (
            not isinstance(observation, dict)
            or canonical_json_bytes(observation) != self.observation_json
            or observation.get("episode_id") != self.episode_id
            or observation.get("observation_seq") != self.observation_seq
        ):
            raise ValueError("observation_json is not bound to this request")
        if len(self.scratchpad_utf8) > 2048:
            raise ValueError("scratchpad_utf8 exceeds 2048 bytes")
        try:
            self.scratchpad_utf8.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("scratchpad_utf8 must be UTF-8") from error
        if self.frame_png is not None:
            if not isinstance(self.frame_png, bytes):
                raise TypeError("frame_png must be immutable bytes")
            if not self.frame_png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("frame_png must be a PNG")
            metadata = observation.get("frame")
            if (
                not isinstance(metadata, dict)
                or observation.get("profile") != "hybrid-visible-v1"
                or len(self.frame_png) < 24
                or self.frame_png[12:16] != b"IHDR"
                or int.from_bytes(self.frame_png[16:20], "big") != 1280
                or int.from_bytes(self.frame_png[20:24], "big") != 720
                or metadata.get("mime_type") != "image/png"
                or metadata.get("width") != 1280
                or metadata.get("height") != 720
                or not isinstance(metadata.get("transport_ref"), str)
                or not metadata["transport_ref"].startswith(f"frame:{self.participant_id}.")
                or hashlib.sha256(self.frame_png).hexdigest() != metadata.get("sha256")
            ):
                raise ValueError("frame_png is not bound to the hybrid observation")
        elif observation.get("profile") == "hybrid-visible-v1":
            raise ValueError("hybrid observation requires frame_png")
        if self.input_bytes > self.max_input_bytes:
            raise ValueError("provider request material exceeds max_input_bytes")

    @property
    def input_bytes(self) -> int:
        """Return the provider-neutral request-material byte count.

        Provider-specific envelopes are deliberately excluded. Every adapter receives these same
        immutable materials, so this is the scored fairness ceiling rather than an SDK wire-size
        estimate.
        """

        return sum(
            (
                len(self.system_prompt.encode("utf-8")),
                len(self.observation_json),
                len(self.action_schema_json),
                len(self.scratchpad_utf8),
                0 if self.frame_png is None else len(self.frame_png),
            )
        )


@dataclass(frozen=True)
class ProviderCallResult:
    """Exactly one raw model output or sanitized failure."""

    raw_output: bytes | None
    failure: ProviderFailureKind | None
    telemetry: ProviderTelemetry

    def __post_init__(self) -> None:
        if (self.raw_output is None) == (self.failure is None):
            raise ValueError("provider result requires exactly one output or failure")
        if self.raw_output is not None and not isinstance(self.raw_output, bytes):
            raise TypeError("raw_output must be immutable bytes")
        if self.failure is not None and not isinstance(self.failure, ProviderFailureKind):
            raise TypeError("failure must be ProviderFailureKind")
        if not isinstance(self.telemetry, ProviderTelemetry):
            raise TypeError("telemetry must be ProviderTelemetry")

    @classmethod
    def success(cls, raw_output: bytes, telemetry: ProviderTelemetry) -> ProviderCallResult:
        return cls(raw_output, None, telemetry)

    @classmethod
    def failed(
        cls, failure: ProviderFailureKind, telemetry: ProviderTelemetry
    ) -> ProviderCallResult:
        return cls(None, failure, telemetry)


@dataclass(frozen=True)
class ProviderAuditRecord:
    """Protected evidence for one call; credentials and headers cannot be stored here."""

    provider: ProviderName
    request: ProviderRequest
    result: ProviderCallResult
    started_monotonic_ns: int
    completed_monotonic_ns: int

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.provider):
            raise ValueError("provider must be a safe identifier")
        if not isinstance(self.request, ProviderRequest) or not isinstance(
            self.result, ProviderCallResult
        ):
            raise TypeError("audit request/result have invalid types")
        if self.started_monotonic_ns < 0 or self.completed_monotonic_ns < self.started_monotonic_ns:
            raise ValueError("audit monotonic interval is invalid")


class InMemoryProviderAuditLog:
    """Process-local protected log drained when an episode bundle is sealed."""

    def __init__(self) -> None:
        self._records: list[ProviderAuditRecord] = []

    def record(self, record: ProviderAuditRecord) -> None:
        if not isinstance(record, ProviderAuditRecord):
            raise TypeError("record must be ProviderAuditRecord")
        self._records.append(record)

    def drain_episode(self, episode_id: str) -> Tuple[ProviderAuditRecord, ...]:
        selected = tuple(r for r in self._records if r.request.episode_id == episode_id)
        self._records = [r for r in self._records if r.request.episode_id != episode_id]
        return selected


@runtime_checkable
class ProviderAdapter(Protocol):
    """Retry-free async provider interface."""

    provider_name: ProviderName

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        """Perform exactly one call and return sanitized evidence."""
