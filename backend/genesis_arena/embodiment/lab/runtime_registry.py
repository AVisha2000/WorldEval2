"""Path-independent runtime bindings for WorldEval Lab games.

The game-guide catalogue explains product capabilities.  This module is the separate server-side
admission map that says which existing authority service owns a mode and whether that mode may be
launched today.  It intentionally contains no imports of service implementations, filesystem
paths, credentials, provider requests, or presentation components.

Bindings fail closed: a caller must resolve one explicit ``live`` or ``demo`` profile before it
dispatches to a runtime adapter.  Cached replays are represented as ``replay`` profiles and can
never be mistaken for launch authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

from ..protocol import canonical_json_bytes, canonical_sha256

RUNTIME_PROFILE_SCHEMA_VERSION = "worldeval/lab-runtime-profile/1"
RUNTIME_BINDING_SCHEMA_VERSION = "worldeval/lab-runtime-binding/1"
RUNTIME_REGISTRY_SCHEMA_VERSION = "worldeval/lab-runtime-registry/1"

RuntimeAuthorityKind = Literal[
    "arena_websocket",
    "cached_showcase",
    "labyrinth_race",
    "legacy_world_socket",
    "managed_duel_match",
    "paired_series",
    "solo_episode",
    "trio_series",
]
RuntimeAuthorityOwner = Literal["backend", "godot", "sealed_replay"]
RuntimeMode = Literal["live", "demo", "replay"]
RuntimeAvailability = Literal["available", "blocked", "replay_only"]
RuntimeExposure = Literal["catalog", "internal"]

_AUTHORITY_KINDS = frozenset(
    (
        "arena_websocket",
        "cached_showcase",
        "labyrinth_race",
        "legacy_world_socket",
        "managed_duel_match",
        "paired_series",
        "solo_episode",
        "trio_series",
    )
)
_AUTHORITY_OWNERS = frozenset(("backend", "godot", "sealed_replay"))
_MODES = frozenset(("live", "demo", "replay"))
_AVAILABILITY = frozenset(("available", "blocked", "replay_only"))
_EXPOSURES = frozenset(("catalog", "internal"))
_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SAFE_PROTOCOL = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_SAFE_REASON = re.compile(r"^[a-z0-9][a-z0-9_]{0,95}$")


class GameRuntimeRegistryError(ValueError):
    """The runtime registry or one of its immutable records is invalid."""


class GameRuntimeBindingNotFoundError(GameRuntimeRegistryError):
    """No catalog binding exists for the requested game."""


class GameRuntimeModeUnavailableError(GameRuntimeRegistryError):
    """A requested live/demo mode has no admitted launch profile."""


def _safe_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise GameRuntimeRegistryError(f"{field_name} is invalid")
    return value


def _safe_optional_identifier(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _safe_identifier(value, field_name=field_name)


def _safe_protocol(value: object) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_PROTOCOL.fullmatch(value) is None
        or value.startswith(("/", "."))
        or ".." in value
        or "\\" in value
    ):
        raise GameRuntimeRegistryError("protocol_version is invalid")
    return value


@dataclass(frozen=True)
class GameRuntimeProfile:
    """One exact runtime mode owned by one existing authority boundary."""

    profile_id: str
    authority_kind: RuntimeAuthorityKind
    authority_owner: RuntimeAuthorityOwner
    mode: RuntimeMode
    availability: RuntimeAvailability
    protocol_version: str
    canonical_task_id: str | None
    canonical_scenario_id: str | None
    participant_count: int
    providers: tuple[str, ...]
    exposure: RuntimeExposure
    blocked_reason: str | None = None
    schema_version: str = RUNTIME_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_PROFILE_SCHEMA_VERSION:
            raise GameRuntimeRegistryError("runtime profile schema is unsupported")
        _safe_identifier(self.profile_id, field_name="profile_id")
        if self.authority_kind not in _AUTHORITY_KINDS:
            raise GameRuntimeRegistryError("authority_kind is unsupported")
        if self.authority_owner not in _AUTHORITY_OWNERS:
            raise GameRuntimeRegistryError("authority_owner is unsupported")
        if self.mode not in _MODES:
            raise GameRuntimeRegistryError("runtime mode is unsupported")
        if self.availability not in _AVAILABILITY:
            raise GameRuntimeRegistryError("runtime availability is unsupported")
        _safe_protocol(self.protocol_version)
        task_id = _safe_optional_identifier(self.canonical_task_id, field_name="canonical_task_id")
        scenario_id = _safe_optional_identifier(
            self.canonical_scenario_id, field_name="canonical_scenario_id"
        )
        if task_id is None and scenario_id is None:
            raise GameRuntimeRegistryError("a canonical task or scenario id is required")
        if (
            isinstance(self.participant_count, bool)
            or not isinstance(self.participant_count, int)
            or not 1 <= self.participant_count <= 64
        ):
            raise GameRuntimeRegistryError("participant_count is invalid")
        if tuple(sorted(self.providers)) != self.providers or len(set(self.providers)) != len(
            self.providers
        ):
            raise GameRuntimeRegistryError("providers must be unique and sorted")
        for provider in self.providers:
            _safe_identifier(provider, field_name="provider")
        if self.exposure not in _EXPOSURES:
            raise GameRuntimeRegistryError("runtime exposure is unsupported")
        if self.blocked_reason is not None and (
            not isinstance(self.blocked_reason, str)
            or _SAFE_REASON.fullmatch(self.blocked_reason) is None
        ):
            raise GameRuntimeRegistryError("blocked_reason is invalid")

        if self.mode == "replay":
            if (
                self.availability != "replay_only"
                or self.providers
                or self.blocked_reason is not None
                or self.authority_owner != "sealed_replay"
            ):
                raise GameRuntimeRegistryError(
                    "replay profiles must be provider-free sealed replay sources"
                )
        elif self.availability == "replay_only":
            raise GameRuntimeRegistryError("launch profiles cannot be replay_only")
        elif self.availability == "available":
            if not self.providers or self.blocked_reason is not None:
                raise GameRuntimeRegistryError(
                    "available launch profiles require providers and no blocked reason"
                )
        elif not self.providers or self.blocked_reason is None:
            raise GameRuntimeRegistryError(
                "blocked launch profiles require provider identities and a stable reason"
            )

    @property
    def launchable(self) -> bool:
        return self.mode in ("live", "demo") and self.availability == "available"

    def safe_dict(self) -> dict[str, object]:
        """Return the complete secret-free runtime descriptor."""

        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "authority_kind": self.authority_kind,
            "authority_owner": self.authority_owner,
            "mode": self.mode,
            "availability": self.availability,
            "protocol_version": self.protocol_version,
            "canonical_task_id": self.canonical_task_id,
            "canonical_scenario_id": self.canonical_scenario_id,
            "participant_count": self.participant_count,
            "providers": list(self.providers),
            "exposure": self.exposure,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class GameRuntimeBinding:
    """The admitted runtime profiles and truthful capabilities for one catalog game."""

    game_id: str
    profile_ids: tuple[str, ...]
    live_launch: bool
    demo_launch: bool
    replay: bool
    spectator: bool
    schema_version: str = RUNTIME_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_BINDING_SCHEMA_VERSION:
            raise GameRuntimeRegistryError("runtime binding schema is unsupported")
        _safe_identifier(self.game_id, field_name="game_id")
        if (
            not self.profile_ids
            or tuple(sorted(self.profile_ids)) != self.profile_ids
            or len(set(self.profile_ids)) != len(self.profile_ids)
        ):
            raise GameRuntimeRegistryError("profile_ids must be non-empty, unique, and sorted")
        for profile_id in self.profile_ids:
            _safe_identifier(profile_id, field_name="profile_id")
        for field_name in ("live_launch", "demo_launch", "replay", "spectator"):
            if not isinstance(getattr(self, field_name), bool):
                raise GameRuntimeRegistryError(f"{field_name} must be boolean")
        if self.spectator and not self.replay:
            raise GameRuntimeRegistryError("spectator support requires replay evidence")

    def safe_dict(self, *, authority_kinds: tuple[str, ...]) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "game_id": self.game_id,
            "profile_ids": list(self.profile_ids),
            "authority_kinds": list(authority_kinds),
            "live_launch": self.live_launch,
            "demo_launch": self.demo_launch,
            "replay": self.replay,
            "spectator": self.spectator,
        }


@dataclass(frozen=True)
class GameRuntimeRegistry:
    """Immutable lookup and fail-closed launch admission for all known Lab runtimes."""

    profiles: tuple[GameRuntimeProfile, ...]
    bindings: tuple[GameRuntimeBinding, ...]
    schema_version: str = RUNTIME_REGISTRY_SCHEMA_VERSION
    _profiles_by_id: Mapping[str, GameRuntimeProfile] = field(init=False, repr=False, compare=False)
    _bindings_by_id: Mapping[str, GameRuntimeBinding] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_REGISTRY_SCHEMA_VERSION:
            raise GameRuntimeRegistryError("runtime registry schema is unsupported")
        if (
            not self.profiles
            or tuple(sorted(self.profiles, key=lambda profile: profile.profile_id)) != self.profiles
        ):
            raise GameRuntimeRegistryError("runtime profiles must be non-empty and id-sorted")
        if len({profile.profile_id for profile in self.profiles}) != len(self.profiles):
            raise GameRuntimeRegistryError("runtime profile ids must be unique")
        if (
            not self.bindings
            or tuple(sorted(self.bindings, key=lambda binding: binding.game_id)) != self.bindings
        ):
            raise GameRuntimeRegistryError("runtime bindings must be non-empty and game-id-sorted")
        if len({binding.game_id for binding in self.bindings}) != len(self.bindings):
            raise GameRuntimeRegistryError("runtime binding game ids must be unique")

        profiles_by_id = {profile.profile_id: profile for profile in self.profiles}
        referenced_catalog_profiles: set[str] = set()
        for binding in self.bindings:
            try:
                profiles = tuple(profiles_by_id[value] for value in binding.profile_ids)
            except KeyError as error:
                raise GameRuntimeRegistryError(
                    "runtime binding references an unknown profile"
                ) from error
            if any(profile.exposure != "catalog" for profile in profiles):
                raise GameRuntimeRegistryError(
                    "catalog bindings cannot reference internal runtime profiles"
                )
            if referenced_catalog_profiles.intersection(binding.profile_ids):
                raise GameRuntimeRegistryError(
                    "a catalog runtime profile cannot belong to multiple games"
                )
            referenced_catalog_profiles.update(binding.profile_ids)
            for mode, declared in (
                ("live", binding.live_launch),
                ("demo", binding.demo_launch),
            ):
                admitted = tuple(
                    profile for profile in profiles if profile.mode == mode and profile.launchable
                )
                if len(admitted) > 1:
                    raise GameRuntimeRegistryError(
                        "a game cannot have multiple admitted profiles for one launch mode"
                    )
                if declared != bool(admitted):
                    raise GameRuntimeRegistryError(
                        f"{binding.game_id} {mode} capability differs from admitted profiles"
                    )
            has_replay_profile = any(profile.mode == "replay" for profile in profiles)
            if has_replay_profile and not binding.replay:
                raise GameRuntimeRegistryError(
                    "a game with a replay profile must advertise replay support"
                )

        catalog_profile_ids = {
            profile.profile_id for profile in self.profiles if profile.exposure == "catalog"
        }
        if catalog_profile_ids != referenced_catalog_profiles:
            raise GameRuntimeRegistryError(
                "every catalog runtime profile must belong to exactly one game binding"
            )

        object.__setattr__(self, "_profiles_by_id", MappingProxyType(profiles_by_id))
        object.__setattr__(
            self,
            "_bindings_by_id",
            MappingProxyType({binding.game_id: binding for binding in self.bindings}),
        )

    def profile(self, profile_id: str) -> GameRuntimeProfile:
        try:
            return self._profiles_by_id[profile_id]
        except KeyError as error:
            raise GameRuntimeBindingNotFoundError("runtime profile is not registered") from error

    def binding(self, game_id: str) -> GameRuntimeBinding:
        try:
            return self._bindings_by_id[game_id]
        except KeyError as error:
            raise GameRuntimeBindingNotFoundError(
                "game runtime binding is not registered"
            ) from error

    def resolve_profile_launch(self, profile_id: str) -> GameRuntimeProfile:
        """Admit one explicit profile or fail without selecting a fallback."""

        profile = self.profile(profile_id)
        if not profile.launchable:
            raise GameRuntimeModeUnavailableError("runtime profile is not launchable")
        return profile

    def resolve_launch(self, game_id: str, mode: RuntimeMode | str) -> GameRuntimeProfile:
        """Resolve exactly one catalog live/demo mode; replay is never launch authority."""

        if mode not in ("live", "demo"):
            raise GameRuntimeModeUnavailableError("runtime launch mode is unsupported")
        binding = self.binding(game_id)
        candidates = tuple(
            self._profiles_by_id[profile_id]
            for profile_id in binding.profile_ids
            if self._profiles_by_id[profile_id].mode == mode
            and self._profiles_by_id[profile_id].launchable
        )
        if len(candidates) != 1:
            raise GameRuntimeModeUnavailableError(
                f"{game_id} does not admit a {mode} launch profile"
            )
        return candidates[0]

    def _binding_safe_dict(self, binding: GameRuntimeBinding) -> dict[str, object]:
        authority_kinds = tuple(
            sorted(
                {
                    self._profiles_by_id[profile_id].authority_kind
                    for profile_id in binding.profile_ids
                }
            )
        )
        return binding.safe_dict(authority_kinds=authority_kinds)

    def _manifest_body(self) -> dict[str, object]:
        internal_profile_ids = [
            profile.profile_id for profile in self.profiles if profile.exposure == "internal"
        ]
        return {
            "schema_version": self.schema_version,
            "bindings": [self._binding_safe_dict(binding) for binding in self.bindings],
            "profiles": [profile.safe_dict() for profile in self.profiles],
            "internal_profile_ids": internal_profile_ids,
        }

    @property
    def manifest_sha256(self) -> str:
        return canonical_sha256(self._manifest_body())

    def safe_manifest(self) -> dict[str, object]:
        """Return a canonical secret-free manifest suitable for server diagnostics."""

        return {**self._manifest_body(), "manifest_sha256": self.manifest_sha256}

    def canonical_safe_bytes(self) -> bytes:
        return canonical_json_bytes(self.safe_manifest())


_SESSION_PROVIDERS = ("anthropic", "gemini", "openai")
_PAIRED_LIVE_PROVIDERS = ("anthropic", "gemini", "openai", "scripted")


def _profile(
    profile_id: str,
    authority_kind: RuntimeAuthorityKind,
    authority_owner: RuntimeAuthorityOwner,
    mode: RuntimeMode,
    availability: RuntimeAvailability,
    protocol_version: str,
    task_id: str | None,
    scenario_id: str | None,
    participants: int,
    providers: tuple[str, ...],
    exposure: RuntimeExposure,
    blocked_reason: str | None = None,
) -> GameRuntimeProfile:
    return GameRuntimeProfile(
        profile_id=profile_id,
        authority_kind=authority_kind,
        authority_owner=authority_owner,
        mode=mode,
        availability=availability,
        protocol_version=protocol_version,
        canonical_task_id=task_id,
        canonical_scenario_id=scenario_id,
        participant_count=participants,
        providers=providers,
        exposure=exposure,
        blocked_reason=blocked_reason,
    )


GAME_RUNTIME_PROFILES = tuple(
    sorted(
        (
            # The current Crossroads websocket protocol exists, but the Lab has no managed
            # lifecycle adapter for it.  Its checked showcase remains the only admitted surface.
            _profile(
                "arena.crossroads-conquest.demo",
                "arena_websocket",
                "godot",
                "demo",
                "blocked",
                "world-arena/0.4",
                None,
                "tri_13_v1",
                3,
                ("demo",),
                "catalog",
                "managed_lab_adapter_unavailable",
            ),
            _profile(
                "arena.crossroads-conquest.live",
                "arena_websocket",
                "godot",
                "live",
                "blocked",
                "world-arena/0.4",
                None,
                "tri_13_v1",
                3,
                ("openai",),
                "catalog",
                "managed_lab_adapter_unavailable",
            ),
            _profile(
                "duel.central-relay.demo",
                "paired_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.1.0",
                "central-relay-v0",
                "central-relay-v0",
                2,
                ("demo",),
                "catalog",
            ),
            _profile(
                "duel.central-relay.live",
                "paired_series",
                "godot",
                "live",
                "available",
                "llm-controller/0.1.0",
                "central-relay-v0",
                None,
                2,
                _PAIRED_LIVE_PROVIDERS,
                "catalog",
            ),
            _profile(
                "duel.checkpoint-race.demo",
                "paired_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "duo-checkpoint-race-v0",
                "duo-checkpoint-race-v0",
                2,
                ("demo",),
                "catalog",
            ),
            _profile(
                "duel.relay-control.demo",
                "paired_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "duo-relay-control-v0",
                "duo-relay-control-v0",
                2,
                ("demo",),
                "catalog",
            ),
            _profile(
                "duel.resource-relay.demo",
                "paired_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "duo-resource-relay-v0",
                "duo-resource-relay-v0",
                2,
                ("demo",),
                "catalog",
            ),
            _profile(
                "duel.spar.demo",
                "paired_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "duo-spar-v0",
                "duo-spar-v0",
                2,
                ("demo",),
                "catalog",
            ),
            _profile(
                "labyrinth.cached-replay",
                "cached_showcase",
                "sealed_replay",
                "replay",
                "replay_only",
                "maze-task-plan-v1",
                "trio-maze-race-v0",
                "trio-maze-race-v0",
                3,
                (),
                "catalog",
            ),
            # Labyrinth's existing live simulation is backend-owned; Godot currently renders the
            # already-sealed public replay rather than owning this live transition function.
            _profile(
                "labyrinth.live",
                "labyrinth_race",
                "backend",
                "live",
                "available",
                "maze-task-plan-v1",
                "trio-maze-race-v1",
                "live-labyrinth",
                3,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "legacy.survival.live",
                "legacy_world_socket",
                "godot",
                "live",
                "blocked",
                "genesis-arena/0.1",
                None,
                "survival_v1",
                3,
                ("openai",),
                "internal",
                "canonical_lab_lifecycle_unavailable",
            ),
            _profile(
                "rts.cached-replay",
                "cached_showcase",
                "sealed_replay",
                "replay",
                "replay_only",
                "llm-controller/0.2.0",
                "rts-skirmish-v0",
                "rts-skirmish-v0",
                2,
                (),
                "catalog",
            ),
            _profile(
                "rts.skirmish.demo",
                "paired_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "rts-skirmish-v0",
                "rts-skirmish-v0",
                2,
                ("demo",),
                "catalog",
            ),
            _profile(
                "rts.skirmish.live",
                "paired_series",
                "godot",
                "live",
                "available",
                "llm-controller/0.2.0",
                "rts-skirmish-v1",
                None,
                2,
                _PAIRED_LIVE_PROVIDERS,
                "catalog",
            ),
            _profile(
                "showcase.crossroads.replay",
                "cached_showcase",
                "sealed_replay",
                "replay",
                "replay_only",
                "world-arena/0.4",
                "crossroads-conquest-v0",
                "crossroads-conquest-v0",
                3,
                (),
                "catalog",
            ),
            _profile(
                "showcase.solo-multi-action.replay",
                "cached_showcase",
                "sealed_replay",
                "replay",
                "replay_only",
                "llm-controller/0.1.0",
                "construction-v0",
                "multi-action-demo-v0",
                1,
                (),
                "catalog",
            ),
            _profile(
                "solo.construction.demo",
                "solo_episode",
                "godot",
                "demo",
                "available",
                "llm-controller/0.1.0",
                "construction-v0",
                "construction-v0",
                1,
                ("demo",),
                "catalog",
            ),
            _profile(
                "solo.construction.live",
                "solo_episode",
                "godot",
                "live",
                "available",
                "llm-controller/0.1.0",
                "construction-v0",
                None,
                1,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "solo.interaction.demo",
                "solo_episode",
                "godot",
                "demo",
                "available",
                "llm-controller/0.1.0",
                "interaction-v0",
                "interaction-v0",
                1,
                ("demo",),
                "catalog",
            ),
            _profile(
                "solo.interaction.live",
                "solo_episode",
                "godot",
                "live",
                "available",
                "llm-controller/0.1.0",
                "interaction-v0",
                None,
                1,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "solo.movement-maze.demo",
                "solo_episode",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "movement-maze-v0",
                "movement-maze-v0",
                1,
                ("demo",),
                "catalog",
            ),
            _profile(
                "solo.movement-maze.live",
                "solo_episode",
                "godot",
                "live",
                "available",
                "llm-controller/0.2.0",
                "movement-maze-v0",
                None,
                1,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "solo.neutral-encounter.demo",
                "solo_episode",
                "godot",
                "demo",
                "available",
                "llm-controller/0.1.0",
                "neutral-encounter-v0",
                "neutral-encounter-v0",
                1,
                ("demo",),
                "catalog",
            ),
            _profile(
                "solo.neutral-encounter.live",
                "solo_episode",
                "godot",
                "live",
                "available",
                "llm-controller/0.1.0",
                "neutral-encounter-v0",
                None,
                1,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "solo.operator-action-course.demo",
                "solo_episode",
                "godot",
                "demo",
                "available",
                "llm-controller/0.2.0",
                "operator-action-course-v0",
                "operator-action-course-v0",
                1,
                ("demo",),
                "catalog",
            ),
            _profile(
                "solo.operator-action-course.live",
                "solo_episode",
                "godot",
                "live",
                "available",
                "llm-controller/0.2.0",
                "operator-action-course-v0",
                None,
                1,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "solo.orientation.demo",
                "solo_episode",
                "godot",
                "demo",
                "available",
                "llm-controller/0.1.0",
                "orientation-v0",
                "orientation-v0",
                1,
                ("demo",),
                "catalog",
            ),
            _profile(
                "solo.orientation.live",
                "solo_episode",
                "godot",
                "live",
                "available",
                "llm-controller/0.1.0",
                "orientation-v0",
                None,
                1,
                _SESSION_PROVIDERS,
                "catalog",
            ),
            _profile(
                "trio.free-for-all.demo",
                "trio_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.3.0",
                "trio-free-for-all-v0",
                "trio-free-for-all-v0",
                3,
                ("demo",),
                "catalog",
            ),
            _profile(
                "trio.relay.demo",
                "trio_series",
                "godot",
                "demo",
                "available",
                "llm-controller/0.3.0",
                "trio-relay-v0",
                "trio-relay-v0",
                3,
                ("demo",),
                "catalog",
            ),
            _profile(
                "worldarena-duel.baseline",
                "managed_duel_match",
                "godot",
                "demo",
                "available",
                "worldeval-rts/1.0.0",
                None,
                "crossroads-duel-v1",
                2,
                ("baseline.noop", "baseline.rush", "baseline.seeded_random"),
                "internal",
            ),
            _profile(
                "worldarena-duel.live",
                "managed_duel_match",
                "godot",
                "live",
                "available",
                "worldeval-rts/1.0.0",
                None,
                "crossroads-duel-v1",
                2,
                ("openai",),
                "internal",
            ),
        ),
        key=lambda profile: profile.profile_id,
    )
)


GAME_RUNTIME_BINDINGS = (
    GameRuntimeBinding(
        game_id="central-relay",
        profile_ids=("duel.central-relay.demo", "duel.central-relay.live"),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="checkpoint-race",
        profile_ids=("duel.checkpoint-race.demo",),
        live_launch=False,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="crossroads-conquest",
        profile_ids=(
            "arena.crossroads-conquest.demo",
            "arena.crossroads-conquest.live",
            "showcase.crossroads.replay",
        ),
        live_launch=False,
        demo_launch=False,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="duo-spar",
        profile_ids=("duel.spar.demo",),
        live_launch=False,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="interaction",
        profile_ids=("solo.interaction.demo", "solo.interaction.live"),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="labyrinth-run",
        profile_ids=("labyrinth.cached-replay", "labyrinth.live"),
        live_launch=True,
        demo_launch=False,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="mini-rts",
        profile_ids=("rts.cached-replay", "rts.skirmish.demo", "rts.skirmish.live"),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="movement-maze",
        profile_ids=("solo.movement-maze.demo", "solo.movement-maze.live"),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="neutral-encounter",
        profile_ids=("solo.neutral-encounter.demo", "solo.neutral-encounter.live"),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="operator-action-course",
        profile_ids=(
            "solo.operator-action-course.demo",
            "solo.operator-action-course.live",
        ),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="orientation",
        profile_ids=("solo.orientation.demo", "solo.orientation.live"),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="relay-control",
        profile_ids=("duel.relay-control.demo",),
        live_launch=False,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="resource-relay",
        profile_ids=("duel.resource-relay.demo",),
        live_launch=False,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="solo-construction",
        profile_ids=(
            "showcase.solo-multi-action.replay",
            "solo.construction.demo",
            "solo.construction.live",
        ),
        live_launch=True,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="trio-free-for-all",
        profile_ids=("trio.free-for-all.demo",),
        live_launch=False,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
    GameRuntimeBinding(
        game_id="trio-relay",
        profile_ids=("trio.relay.demo",),
        live_launch=False,
        demo_launch=True,
        replay=True,
        spectator=True,
    ),
)


GAME_RUNTIME_REGISTRY = GameRuntimeRegistry(
    profiles=GAME_RUNTIME_PROFILES,
    bindings=GAME_RUNTIME_BINDINGS,
)


def runtime_profile(profile_id: str) -> GameRuntimeProfile:
    return GAME_RUNTIME_REGISTRY.profile(profile_id)


def runtime_binding(game_id: str) -> GameRuntimeBinding:
    return GAME_RUNTIME_REGISTRY.binding(game_id)


def resolve_runtime_launch(game_id: str, mode: RuntimeMode | str) -> GameRuntimeProfile:
    return GAME_RUNTIME_REGISTRY.resolve_launch(game_id, mode)


def safe_runtime_manifest() -> dict[str, object]:
    return GAME_RUNTIME_REGISTRY.safe_manifest()


__all__ = [
    "GAME_RUNTIME_BINDINGS",
    "GAME_RUNTIME_PROFILES",
    "GAME_RUNTIME_REGISTRY",
    "RUNTIME_BINDING_SCHEMA_VERSION",
    "RUNTIME_PROFILE_SCHEMA_VERSION",
    "RUNTIME_REGISTRY_SCHEMA_VERSION",
    "GameRuntimeBinding",
    "GameRuntimeBindingNotFoundError",
    "GameRuntimeModeUnavailableError",
    "GameRuntimeProfile",
    "GameRuntimeRegistry",
    "GameRuntimeRegistryError",
    "RuntimeAuthorityKind",
    "RuntimeAuthorityOwner",
    "RuntimeAvailability",
    "RuntimeExposure",
    "RuntimeMode",
    "resolve_runtime_launch",
    "runtime_binding",
    "runtime_profile",
    "safe_runtime_manifest",
]
