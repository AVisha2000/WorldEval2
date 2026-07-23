"""Safe, path-independent adapters over the existing Godot-owned game services.

The services wrapped here already own execution, credentials, replay sealing, and scoring.  This
module deliberately does not implement gameplay.  It gives the Lab one small vocabulary for
reading lifecycle state, collecting public terminal evidence, showing a participant frame, and
cancelling an in-flight authority.

Provider credentials never cross this boundary.  Launch remains an API concern because each
existing service has a different, already-validated credential shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from ..duel.service import (
    DuelSeriesEvidenceNotReadyError,
    DuelSeriesNotFoundError,
)
from ..episode_service import (
    EpisodeEvaluationNotReadyError,
    EpisodeNotFoundError,
    EpisodeReplayNotReadyError,
    EpisodeResultNotReadyError,
)
from ..protocol import canonical_json_bytes, strict_json_loads
from ..trio_games.service import TrioSeriesNotFoundError, TrioSeriesNotReadyError
from .contracts import LabContractError, assert_public_projection_safe

AuthorityKind = Literal["solo_episode", "paired_series", "trio_series"]

_AUTHORITY_KINDS = frozenset(("solo_episode", "paired_series", "trio_series"))
_STATES = frozenset(("queued", "running", "completed", "failed", "cancelled"))
_SAFE_FAILURE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_MAX_TERMINAL_SECTION_BYTES = 2 * 1024 * 1024
_MAX_TIMELINE_EVENTS = 4_096


class GameAuthorityError(RuntimeError):
    """A wrapped authority returned malformed or unavailable public state."""


class GameAuthorityNotFoundError(GameAuthorityError):
    """The process or durable archive no longer knows the source run."""


class GameAuthorityNotReadyError(GameAuthorityError):
    """Terminal evidence is not sealed yet."""


class SoloAuthorityService(Protocol):
    async def status(self, episode_id: str) -> Mapping[str, Any]: ...

    async def result(self, episode_id: str) -> Mapping[str, Any]: ...

    async def evaluation(self, episode_id: str) -> Mapping[str, Any]: ...

    async def timeline(self, episode_id: str) -> tuple[Mapping[str, Any], ...]: ...

    async def replay(self, episode_id: str) -> object: ...

    async def frame(self, episode_id: str) -> object: ...

    async def cancel(self, episode_id: str) -> Mapping[str, Any]: ...


class PairedAuthorityService(Protocol):
    async def status(self, series_id: str) -> Mapping[str, object]: ...

    async def result(self, series_id: str) -> Mapping[str, object]: ...

    async def evaluation(self, series_id: str) -> Mapping[str, object]: ...

    async def timeline(self, series_id: str) -> Mapping[str, object]: ...

    async def replay(self, series_id: str) -> object: ...

    async def participant_frame(
        self, series_id: str, participant_id: str
    ) -> tuple[str, object]: ...

    async def cancel(self, series_id: str) -> Mapping[str, object]: ...


class TrioAuthorityService(Protocol):
    async def status(self, series_id: str) -> Mapping[str, object]: ...

    async def result(self, series_id: str) -> Mapping[str, object]: ...

    async def evaluation(self, series_id: str) -> Mapping[str, object]: ...

    async def timeline(self, series_id: str) -> Mapping[str, object]: ...

    async def replay(self, series_id: str) -> object: ...

    async def participant_frame(
        self, series_id: str, participant_id: str
    ) -> tuple[str, object]: ...

    async def cancel(self, series_id: str) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class AuthorityStatus:
    """Allow-listed lifecycle and progress, with no upstream capability handle."""

    state: str
    failure_code: str | None = None
    authority_tick: int | None = None
    observation_sequence: int | None = None
    replay_state: str | None = None

    def __post_init__(self) -> None:
        if self.state not in _STATES:
            raise GameAuthorityError("game authority lifecycle is invalid")
        if self.failure_code is not None and _SAFE_FAILURE.fullmatch(self.failure_code) is None:
            raise GameAuthorityError("game authority failure code is invalid")
        for value in (self.authority_tick, self.observation_sequence):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise GameAuthorityError("game authority progress is invalid")
        if self.replay_state not in {None, "pending", "saving", "ready", "unavailable"}:
            raise GameAuthorityError("game authority replay state is invalid")

    def public_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"state": self.state}
        if self.failure_code is not None:
            value["failure_code"] = self.failure_code
        if self.authority_tick is not None and self.observation_sequence is not None:
            value["progress"] = {
                "authority_tick": self.authority_tick,
                # This is a count only.  The name intentionally avoids suggesting that a private
                # participant observation is present in the public Lab projection.
                "decision_sequence": self.observation_sequence,
            }
        if self.replay_state is not None:
            value["replay_state"] = self.replay_state
        return value


@dataclass(frozen=True)
class AuthorityFrame:
    """One already-sanitized participant frame from an existing service."""

    state: str
    png: bytes | None
    sha256: str | None

    def __post_init__(self) -> None:
        if self.state not in {"loading", "live", "finished", "unavailable"}:
            raise GameAuthorityError("game authority frame state is invalid")
        if self.png is None:
            if self.sha256 is not None:
                raise GameAuthorityError("empty game authority frame has a digest")
            return
        if (
            not isinstance(self.png, bytes)
            or len(self.png) > 16 * 1024 * 1024
            or not self.png.startswith(b"\x89PNG\r\n\x1a\n")
            or not isinstance(self.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            raise GameAuthorityError("game authority frame is invalid")


@dataclass(frozen=True)
class AuthorityTerminalProjection:
    """Bounded browser-safe terminal result assembled from existing public projections."""

    snapshot: Mapping[str, object]
    events: tuple[Mapping[str, object], ...]


def _safe_failure(value: object) -> str:
    if isinstance(value, str) and _SAFE_FAILURE.fullmatch(value) is not None:
        return value
    return "game_authority_execution_failed"


def _nonnegative(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GameAuthorityError(f"{label} is invalid")
    try:
        copied = strict_json_loads(canonical_json_bytes(value))
    except Exception as error:
        raise GameAuthorityError(f"{label} is invalid") from error
    if not isinstance(copied, Mapping):
        raise GameAuthorityError(f"{label} is invalid")
    return copied


_SAFE_LEGACY_PUBLIC_KEY_RENAMES = MappingProxyType(
    {
        # These fields are public numeric/configuration telemetry in the pre-Lab authority
        # projections.  Their historic names collide with the Lab's deliberately broad private
        # observation scanner, so give only these exact, typed fields neutral public names.
        "observation_profile": "sensor_profile",
        "observation_seq": "decision_sequence",
        "observation_sequence": "decision_sequence",
    }
)
_PRIVATE_AUTHORITY_HANDLE_KEYS = frozenset(
    {
        "episode_id",
        "replay_id",
        "series_id",
        "source_id",
    }
)


def _strip_private_authority_handles(value: object, *, path: str) -> object:
    """Remove only known authority capabilities from an already-public typed projection.

    The wrapped services expose browser-safe result/evaluation objects, but their public API
    envelopes still carry the lower-level episode or series identity used to retrieve them.  The
    Lab has its own run identity, so retaining those handles in a cartridge would add a second
    process/durable capability and would make an explicitly published replay disclose it.

    This is deliberately a very small exact-key transform.  It does not make arbitrary material
    safe: every remaining field still passes through ``assert_public_projection_safe`` below, so
    prompts, observations, memories, provider material, and other untyped protected fields fail
    closed exactly as before.
    """

    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise GameAuthorityError(f"{path} contains a non-string key")
            if raw_key in _PRIVATE_AUTHORITY_HANDLE_KEYS:
                continue
            result[raw_key] = _strip_private_authority_handles(
                child,
                path=f"{path}.{raw_key}",
            )
        return result
    if isinstance(value, list):
        return [
            _strip_private_authority_handles(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    return value


def _normalise_legacy_public_telemetry(value: object, *, path: str) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise GameAuthorityError(f"{path} contains a non-string key")
            key = _SAFE_LEGACY_PUBLIC_KEY_RENAMES.get(raw_key, raw_key)
            if key in result:
                raise GameAuthorityError(f"{path} contains colliding public telemetry")
            if raw_key in {"observation_seq", "observation_sequence"}:
                if isinstance(child, bool) or not isinstance(child, int) or child < 0:
                    raise GameAuthorityError(f"{path}.{raw_key} is invalid")
            elif raw_key == "observation_profile":
                if (
                    not isinstance(child, str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", child) is None
                ):
                    raise GameAuthorityError(f"{path}.{raw_key} is invalid")
            result[key] = _normalise_legacy_public_telemetry(child, path=f"{path}.{key}")
        return result
    if isinstance(value, list):
        return [
            _normalise_legacy_public_telemetry(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    return value


def _safe_section(value: object, *, label: str) -> Mapping[str, object] | None:
    """Return an independently copied public section, or omit it closed.

    Existing result/evaluation projections predate the Lab's stricter protected-key scanner.
    Three exact legacy fields are known public telemetry rather than observations: the configured
    sensor profile and numeric decision sequence.  Normalize only those typed fields before
    applying the full protected-material scanner.  Every other collision still makes the entire
    optional section unavailable.
    """

    try:
        copied = _mapping(value, label=label)
        copied = _mapping(
            _strip_private_authority_handles(
                _normalise_legacy_public_telemetry(copied, path=label),
                path=label,
            ),
            label=label,
        )
        if not copied:
            return None
        if len(canonical_json_bytes(copied)) > _MAX_TERMINAL_SECTION_BYTES:
            return None
        assert_public_projection_safe(copied, path=label)
    except (GameAuthorityError, LabContractError, TypeError, ValueError):
        return None
    return copied


def _safe_events(value: object) -> tuple[Mapping[str, object], ...]:
    raw_events: object = value
    if isinstance(value, Mapping):
        raw_events = value.get("events", ())
        if raw_events == () and isinstance(value.get("legs"), list):
            flattened: list[object] = []
            for leg in value["legs"]:
                if isinstance(leg, Mapping) and isinstance(leg.get("events"), list):
                    flattened.extend(leg["events"])
            raw_events = flattened
    if not isinstance(raw_events, (list, tuple)):
        return ()
    events: list[Mapping[str, object]] = []
    for raw in raw_events[:_MAX_TIMELINE_EVENTS]:
        section = _safe_section(raw, label="game_authority.timeline_event")
        if section is not None:
            events.append(section)
    return tuple(events)


def _replay_state(status: Mapping[str, object]) -> str | None:
    replay = status.get("replay")
    if isinstance(replay, Mapping) and replay.get("state") in {
        "pending",
        "saving",
        "ready",
        "unavailable",
    }:
        return str(replay["state"])
    archive_state = status.get("archive_state")
    if archive_state in {"pending", "saving", "ready", "unavailable"}:
        return str(archive_state)
    return None


class GameAuthorityGateway:
    """Normalize existing services without taking authority away from Godot."""

    def __init__(
        self,
        *,
        solo: SoloAuthorityService,
        paired: PairedAuthorityService,
        trio: TrioAuthorityService,
    ) -> None:
        self._solo = solo
        self._paired = paired
        self._trio = trio

    @staticmethod
    def validate_kind(value: object) -> AuthorityKind:
        if value not in _AUTHORITY_KINDS:
            raise GameAuthorityError("game authority kind is invalid")
        return value  # type: ignore[return-value]

    async def status(self, kind: AuthorityKind, source_id: str) -> AuthorityStatus:
        service = self._service(kind)
        try:
            raw = await service.status(source_id)
        except (EpisodeNotFoundError, DuelSeriesNotFoundError, TrioSeriesNotFoundError) as error:
            raise GameAuthorityNotFoundError(source_id) from error
        value = _mapping(raw, label="game_authority.status")
        state = value.get("state")
        if state not in _STATES:
            raise GameAuthorityError("game authority lifecycle is invalid")
        progress = value.get("progress")
        tick = sequence = None
        if isinstance(progress, Mapping):
            tick = _nonnegative(progress.get("authority_tick"))
            sequence = _nonnegative(
                progress.get("observation_seq", progress.get("observation_sequence"))
            )
            if (tick is None) != (sequence is None):
                tick = sequence = None
        failure = _safe_failure(value.get("failure")) if state == "failed" else None
        if state == "cancelled":
            failure = "game_authority_cancelled"
        return AuthorityStatus(
            state=str(state),
            failure_code=failure,
            authority_tick=tick,
            observation_sequence=sequence,
            replay_state=_replay_state(value),
        )

    async def terminal_projection(
        self, kind: AuthorityKind, source_id: str
    ) -> AuthorityTerminalProjection:
        service = self._service(kind)
        try:
            result, evaluation, timeline = await self._terminal_sections(kind, service, source_id)
        except (
            EpisodeNotFoundError,
            DuelSeriesNotFoundError,
            TrioSeriesNotFoundError,
        ) as error:
            raise GameAuthorityNotFoundError(source_id) from error
        except (
            EpisodeResultNotReadyError,
            EpisodeReplayNotReadyError,
            EpisodeEvaluationNotReadyError,
            DuelSeriesEvidenceNotReadyError,
            TrioSeriesNotReadyError,
            RuntimeError,
        ) as error:
            raise GameAuthorityNotReadyError(source_id) from error

        snapshot: dict[str, object] = {}
        safe_result = _safe_section(result, label="game_authority.result")
        safe_evaluation = _safe_section(evaluation, label="game_authority.evaluation")
        if safe_result is not None:
            snapshot["result"] = safe_result
        if safe_evaluation is not None:
            snapshot["evaluation"] = safe_evaluation
        if not snapshot:
            # A completed run without any Lab-safe terminal projection is not a valid cartridge.
            raise GameAuthorityError("game authority terminal projection is unavailable")
        return AuthorityTerminalProjection(snapshot=snapshot, events=_safe_events(timeline))

    async def frame(
        self, kind: AuthorityKind, source_id: str, *, participant_id: str
    ) -> AuthorityFrame:
        try:
            if kind == "solo_episode":
                view = await self._solo.frame(source_id)
                state = getattr(view, "state", None)
                snapshot = getattr(view, "snapshot", None)
            elif kind == "paired_series":
                state, snapshot = await self._paired.participant_frame(source_id, participant_id)
            else:
                state, snapshot = await self._trio.participant_frame(source_id, participant_id)
        except (EpisodeNotFoundError, DuelSeriesNotFoundError, TrioSeriesNotFoundError) as error:
            raise GameAuthorityNotFoundError(source_id) from error
        if state not in {"loading", "live", "finished", "unavailable"}:
            raise GameAuthorityError("game authority frame state is invalid")
        if snapshot is None:
            return AuthorityFrame(str(state), None, None)
        png = getattr(snapshot, "png", None)
        sha256 = getattr(snapshot, "sha256", None)
        return AuthorityFrame(str(state), png, sha256)

    async def cancel(self, kind: AuthorityKind, source_id: str) -> AuthorityStatus:
        service = self._service(kind)
        try:
            await service.cancel(source_id)
        except (EpisodeNotFoundError, DuelSeriesNotFoundError, TrioSeriesNotFoundError) as error:
            raise GameAuthorityNotFoundError(source_id) from error
        return await self.status(kind, source_id)

    def _service(self, kind: AuthorityKind) -> object:
        parsed = self.validate_kind(kind)
        if parsed == "solo_episode":
            return self._solo
        if parsed == "paired_series":
            return self._paired
        return self._trio

    @staticmethod
    async def _terminal_sections(
        kind: AuthorityKind, service: object, source_id: str
    ) -> tuple[object, object, object]:
        # Keep these sequential.  The services expose already-computed in-memory or durable
        # projections; concurrent reads would not reduce authority work and complicate fake
        # service determinism in conformance tests.
        result = await service.result(source_id)  # type: ignore[attr-defined]
        evaluation = await service.evaluation(source_id)  # type: ignore[attr-defined]
        timeline = await service.timeline(source_id)  # type: ignore[attr-defined]
        return result, evaluation, timeline


__all__ = [
    "AuthorityFrame",
    "AuthorityKind",
    "AuthorityStatus",
    "AuthorityTerminalProjection",
    "GameAuthorityError",
    "GameAuthorityGateway",
    "GameAuthorityNotFoundError",
    "GameAuthorityNotReadyError",
]
