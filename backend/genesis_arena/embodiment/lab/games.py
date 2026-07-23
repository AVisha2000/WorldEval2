# ruff: noqa: E501
"""Versioned, browser-safe guide metadata for the WorldEval Lab game catalogue.

This module is intentionally a product metadata boundary, not a second game registry.  Each
entry reflects an existing authority/showcase capability and makes an unsupported configuration
explicitly unavailable instead of implying that every game has a live-provider path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

from ..protocol import canonical_json_bytes, canonical_sha256

GAME_SPEC_SCHEMA_VERSION = "worldeval/lab-game-spec/1"
GAME_CATALOG_SCHEMA_VERSION = "worldeval/lab-game-catalog/1"

GameReadiness = Literal["live_ready", "demo_replay_ready", "experimental"]
ControlType = Literal["fixed", "model", "number", "provider", "roster", "select"]

_READINESS_VALUES = frozenset(("live_ready", "demo_replay_ready", "experimental"))
_CONTROL_TYPES = frozenset(("fixed", "model", "number", "provider", "roster", "select"))


class GameCatalogError(ValueError):
    """A game-guide definition cannot be safely projected by the Lab."""


def _identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GameCatalogError(f"{field_name} must be a non-empty identifier")
    if len(value) > 96 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in value
    ):
        raise GameCatalogError(f"{field_name} is not a safe identifier")
    return value


def _text(value: object, *, field_name: str, maximum_bytes: int = 1_000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum_bytes
        or "\x00" in value
    ):
        raise GameCatalogError(f"{field_name} is not safe public text")
    return value


def _unique_ids(values: tuple[object, ...], *, field_name: str) -> None:
    identifiers = []
    for value in values:
        identifier = getattr(value, "id", None)
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise GameCatalogError(f"{field_name} contains duplicate ids")


@dataclass(frozen=True)
class GameMetric:
    """One displayed metric, described without making it a universal score."""

    id: str
    label: str
    description: str

    def __post_init__(self) -> None:
        _identifier(self.id, field_name="metric id")
        _text(self.label, field_name="metric label", maximum_bytes=96)
        _text(self.description, field_name="metric description", maximum_bytes=320)

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "description": self.description}


@dataclass(frozen=True)
class GameScoring:
    """A game-specific outcome explanation and the metrics safe to display beside it."""

    summary: str
    metrics: tuple[GameMetric, ...]

    def __post_init__(self) -> None:
        _text(self.summary, field_name="scoring summary", maximum_bytes=600)
        if not self.metrics:
            raise GameCatalogError("scoring requires at least one metric")
        _unique_ids(self.metrics, field_name="scoring metrics")

    def public_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "metrics": [metric.public_dict() for metric in self.metrics],
        }


@dataclass(frozen=True)
class GameAgentInterface:
    """Plain-language contract for what one agent may see, do, and retain."""

    observation: str
    actions: str
    memory: str

    def __post_init__(self) -> None:
        _text(self.observation, field_name="agent observation", maximum_bytes=900)
        _text(self.actions, field_name="agent actions", maximum_bytes=900)
        _text(self.memory, field_name="agent memory", maximum_bytes=900)

    def public_dict(self) -> dict[str, str]:
        return {
            "observation": self.observation,
            "actions": self.actions,
            "memory": self.memory,
        }


@dataclass(frozen=True)
class GameConfigurationControl:
    """A supported composer control, including deliberately fixed showcase material."""

    id: str
    label: str
    control_type: ControlType
    description: str
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.id, field_name="configuration control id")
        _text(self.label, field_name="configuration control label", maximum_bytes=96)
        if self.control_type not in _CONTROL_TYPES:
            raise GameCatalogError("configuration control type is unsupported")
        _text(self.description, field_name="configuration control description", maximum_bytes=420)
        if not isinstance(self.options, tuple) or len(set(self.options)) != len(self.options):
            raise GameCatalogError("configuration control options are invalid")
        for option in self.options:
            _text(option, field_name="configuration control option", maximum_bytes=120)

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "control_type": self.control_type,
            "description": self.description,
            "options": list(self.options),
        }


@dataclass(frozen=True)
class GameMode:
    """One currently supported way to launch, study, or verify a game."""

    id: str
    label: str
    description: str

    def __post_init__(self) -> None:
        _identifier(self.id, field_name="game mode id")
        _text(self.label, field_name="game mode label", maximum_bytes=96)
        _text(self.description, field_name="game mode description", maximum_bytes=320)

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "description": self.description}


@dataclass(frozen=True)
class GameSafety:
    """The public versus participant-private boundary for one game guide."""

    public_view: str
    private_agent_state: str

    def __post_init__(self) -> None:
        _text(self.public_view, field_name="public safety text", maximum_bytes=900)
        _text(self.private_agent_state, field_name="private safety text", maximum_bytes=900)

    def public_dict(self) -> dict[str, str]:
        return {
            "public_view": self.public_view,
            "private_agent_state": self.private_agent_state,
        }


@dataclass(frozen=True)
class GameSpec:
    """One immutable public guide definition for an existing WorldEval environment."""

    id: str
    title: str
    readiness: GameReadiness
    readiness_note: str
    task_ids: tuple[str, ...]
    capability_statements: tuple[str, ...]
    agent_interface: GameAgentInterface
    scoring: GameScoring
    configuration_controls: tuple[GameConfigurationControl, ...]
    failure_modes: tuple[str, ...]
    safety: GameSafety
    supported_modes: tuple[GameMode, ...]
    schema_version: str = GAME_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != GAME_SPEC_SCHEMA_VERSION:
            raise GameCatalogError("game spec schema version is unsupported")
        _identifier(self.id, field_name="game id")
        _text(self.title, field_name="game title", maximum_bytes=120)
        if self.readiness not in _READINESS_VALUES:
            raise GameCatalogError("game readiness is unsupported")
        _text(self.readiness_note, field_name="game readiness note", maximum_bytes=360)
        if not self.task_ids or len(set(self.task_ids)) != len(self.task_ids):
            raise GameCatalogError("game task ids are invalid")
        for task_id in self.task_ids:
            _identifier(task_id, field_name="game task id")
        if not self.capability_statements:
            raise GameCatalogError("game requires at least one capability statement")
        for statement in self.capability_statements:
            _text(statement, field_name="capability statement", maximum_bytes=320)
        if not self.configuration_controls:
            raise GameCatalogError("game requires configuration-control coverage")
        _unique_ids(self.configuration_controls, field_name="configuration controls")
        if not self.failure_modes:
            raise GameCatalogError("game requires failure-mode coverage")
        for failure_mode in self.failure_modes:
            _text(failure_mode, field_name="failure mode", maximum_bytes=320)
        if not self.supported_modes:
            raise GameCatalogError("game requires mode coverage")
        _unique_ids(self.supported_modes, field_name="supported modes")

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "readiness": self.readiness,
            "readiness_note": self.readiness_note,
            "task_ids": list(self.task_ids),
            "capability_statements": list(self.capability_statements),
            "agent_interface": self.agent_interface.public_dict(),
            "scoring": self.scoring.public_dict(),
            "configuration_controls": [
                control.public_dict() for control in self.configuration_controls
            ],
            "failure_modes": list(self.failure_modes),
            "safety": self.safety.public_dict(),
            "supported_modes": [mode.public_dict() for mode in self.supported_modes],
        }

    @property
    def spec_sha256(self) -> str:
        """Digest of the complete guide definition, excluding its self-reference."""

        return canonical_sha256(self._hash_body())

    def public_dict(self) -> dict[str, object]:
        return {**self._hash_body(), "spec_sha256": self.spec_sha256}

    def canonical_public_bytes(self) -> bytes:
        return canonical_json_bytes(self.public_dict())


@dataclass(frozen=True)
class GameCatalog:
    """A sorted, immutable registry whose browser projection is hash-addressable."""

    games: tuple[GameSpec, ...]
    schema_version: str = GAME_CATALOG_SCHEMA_VERSION
    _by_id: Mapping[str, GameSpec] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != GAME_CATALOG_SCHEMA_VERSION:
            raise GameCatalogError("game catalog schema version is unsupported")
        if not self.games or tuple(sorted(self.games, key=lambda game: game.id)) != self.games:
            raise GameCatalogError("game catalog entries must be non-empty and id-sorted")
        if len({game.id for game in self.games}) != len(self.games):
            raise GameCatalogError("game catalog contains duplicate game ids")
        object.__setattr__(
            self,
            "_by_id",
            MappingProxyType({game.id: game for game in self.games}),
        )

    def game(self, game_id: str) -> GameSpec:
        try:
            return self._by_id[game_id]
        except KeyError as error:
            raise GameCatalogError("game is not registered") from error

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "games": [game.public_dict() for game in self.games],
        }

    @property
    def catalog_sha256(self) -> str:
        return canonical_sha256(self._hash_body())

    def public_dict(self) -> dict[str, object]:
        return {**self._hash_body(), "catalog_sha256": self.catalog_sha256}

    def canonical_public_bytes(self) -> bytes:
        return canonical_json_bytes(self.public_dict())


def _metric(id: str, label: str, description: str) -> GameMetric:
    return GameMetric(id=id, label=label, description=description)


def _control(
    id: str,
    label: str,
    control_type: ControlType,
    description: str,
    *options: str,
) -> GameConfigurationControl:
    return GameConfigurationControl(
        id=id,
        label=label,
        control_type=control_type,
        description=description,
        options=tuple(options),
    )


def _mode(id: str, label: str, description: str) -> GameMode:
    return GameMode(id=id, label=label, description=description)


# Keep this tuple sorted by public ``id``.  The claims below are bound to the current live APIs,
# deterministic/demo catalogues, and sealed showcases; this is not an aspirational feature list.
_GAME_SPECS = (
    GameSpec(
        id="checkpoint-race",
        title="Checkpoint Race",
        readiness="demo_replay_ready",
        readiness_note=(
            "A deterministic, two-leg Demo game with seat swaps; external live-provider entrants "
            "are not enabled for this additive duo task."
        ),
        task_ids=("duo-checkpoint-race-v0",),
        capability_statements=(
            "Ordered-target navigation from participant-visible markers.",
            "Course correction after a target is not immediately visible.",
            "Timing and reliable execution under fixed joint decision windows.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The active racer receives participant-visible next checkpoints or the finish "
                "beacon, plus relative bearing, qualitative distance, and contact state."
            ),
            actions=(
                "Each racer submits an ordinary controller action for a fixed ten-tick joint "
                "window; crossing is resolved by Godot authority."
            ),
            memory=(
                "Each participant has private episode-local controller memory that resets for "
                "the seat-swapped second leg."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Complete every ordered checkpoint and finish first; the safe evaluation also "
                "reports progress, completion, and cross-seat symmetry."
            ),
            metrics=(
                _metric("completion", "Completion", "Terminal tick, outcome, and reason."),
                _metric(
                    "checkpoint_progress",
                    "Checkpoint progress",
                    "Ordered checkpoints reached relative to the fixed total.",
                ),
                _metric(
                    "decision_windows",
                    "Decision windows",
                    "Accepted and invalid fixed-window decisions by participant.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "demo_pair",
                "Demo entrants",
                "fixed",
                "The checked-in Alpha and Bravo visible-only Demo policies are required.",
            ),
            _control(
                "seed",
                "Seed",
                "number",
                "Select the deterministic authority seed for the paired series.",
            ),
        ),
        failure_modes=(
            "A missing, stale, malformed, or timed-out decision becomes neutral input for that participant only.",
            "The race can end by time limit, simultaneous terminal resolution, or a void result.",
        ),
        safety=GameSafety(
            public_view=(
                "Public evaluation contains aggregate checkpoint and outcome evidence, not route "
                "geometry or participant observations."
            ),
            private_agent_state=(
                "Participant observations, frame data, scratchpads, provider output, and credentials "
                "remain outside public replay and evaluation projections."
            ),
        ),
        supported_modes=(
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Two seat-swapped authority legs with the locked Demo pair.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Allow-listed aggregate result projection after the series seals.",
            ),
        ),
    ),
    GameSpec(
        id="crossroads-conquest",
        title="Crossroads Conquest",
        readiness="demo_replay_ready",
        readiness_note=(
            "The current product surface is a sealed three-faction showcase with cached video and "
            "evaluation; it does not expose a live run composer."
        ),
        task_ids=("crossroads-conquest-v0",),
        capability_statements=(
            "Multi-agent strategy, opponent modelling, negotiation, and trust.",
            "Resource management, timing, opportunism, and adaptation as threats change.",
            "Long-horizon expansion, defence, and attack decisions under partial information.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each faction receives a private visibility-filtered world observation rather than "
                "the shared spectator map."
            ),
            actions=(
                "Eligible advisors run before Commanders plan concurrently; plans are commit-locked, "
                "revealed, and resolved by the fixed-tick Godot authority."
            ),
            memory=(
                "The sealed showcase does not expose a configurable model-memory interface to the "
                "Lab; private faction cognition is not public evidence."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "The competitive winner is the last surviving stronghold. A separate deterministic "
                "WorldEval Score explains behaviour but never changes placement."
            ),
            metrics=(
                _metric("placement", "Placement", "Winner, placements, and elimination order."),
                _metric(
                    "worldeval_score",
                    "WorldEval Score",
                    "Evidence-backed objective, planning, efficiency, social, cognition, and reliability categories.",
                ),
                _metric(
                    "verification",
                    "Verification",
                    "Deterministic replay, trace, and sealed artifact hash checks.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "sealed_showcase",
                "Sealed showcase",
                "fixed",
                "The current map, seed, rules, and demonstration policy are fixed and hash-bound.",
            ),
        ),
        failure_modes=(
            "At the 120-round cap, the match truncates and publishes diagnostic standings without declaring a winner.",
            "Invalid or unsupported plans are rejected by the authority rather than altering the fixed round order.",
        ),
        safety=GameSafety(
            public_view=(
                "The public showcase exposes a verified result, timeline, and video selected from an "
                "allow-listed presentation projection."
            ),
            private_agent_state=(
                "Faction observations, private messages, plans, raw provider material, and private "
                "cognition remain sealed outside the browser projection."
            ),
        ),
        supported_modes=(
            _mode("sealed_showcase", "Sealed showcase", "Cached verified three-faction broadcast."),
            _mode("video_playback", "Video playback", "Browser-safe native gameplay video."),
            _mode(
                "cached_evaluation",
                "Cached evaluation",
                "Verified placement and evidence-backed evaluation projection.",
            ),
        ),
    ),
    GameSpec(
        id="labyrinth-run",
        title="Labyrinth Run",
        readiness="live_ready",
        readiness_note=(
            "The v1 three-racer maze endpoint accepts a session-only provider credential; the v0 "
            "showcase remains separately cached."
        ),
        task_ids=("trio-maze-race-v0", "trio-maze-race-v1"),
        capability_statements=(
            "Spatial reasoning and route planning with partial, wall-occluded sightlines.",
            "Episode-local navigation memory, depth-first exploration, and recovery from wrong turns.",
            "Explicit corridor control that preserves agent choice while reducing repeated calls.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "A racer receives visible relative passages, landmarks, and straight-line cells up "
                "to walls; it never sees through walls or around a corner."
            ),
            actions=(
                "A strict MazeTaskPlan chooses left, forward, right, back, or wait and explicitly "
                "authorizes either one-cell movement or bounded follow-corridor movement."
            ),
            memory=(
                "Each racer has isolated maze-nav/2 navigation memory derived from visible observations "
                "and accepted moves; model scratchpad text cannot overwrite it."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Finish the maze efficiently. Public evaluation compares completion, route efficiency, "
                "provider-call use, invalid decisions, and waiting behaviour per racer."
            ),
            metrics=(
                _metric(
                    "completion", "Completion", "Finish state and finish tick for every racer."
                ),
                _metric(
                    "path_efficiency",
                    "Path efficiency",
                    "Shortest-path distance relative to travelled maze cells.",
                ),
                _metric(
                    "provider_calls",
                    "Provider calls",
                    "Independent decision-budget use for each racer.",
                ),
                _metric(
                    "recovery",
                    "Recovery behaviour",
                    "Invalid decisions, waiting windows, and repeated-corridor movement.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider",
                "Provider",
                "provider",
                "Choose the provider for all three live racers in the current endpoint.",
                "openai",
                "anthropic",
                "gemini",
            ),
            _control(
                "entrant_models",
                "Racer models",
                "roster",
                "Assign a model to the fixed Sol, Terra, and Luna racer slots.",
            ),
            _control(
                "vision_range_cells",
                "Agent field of view",
                "select",
                "Use wall-occluded sightlines at one of the UI depths or infinite distance to the next wall.",
                "1",
                "2",
                "4",
                "8",
                "infinite",
            ),
            _control(
                "max_provider_calls",
                "Per-race call ceiling",
                "number",
                "Set a bounded live provider-call ceiling up to the supported 450-call maximum.",
            ),
        ),
        failure_modes=(
            "Malformed, stale, waiting, or failed provider responses become safe recorded outcomes and cannot move another racer.",
            "Each racer has an independent call budget, so an exhausted racer cannot spend another racer's allowance.",
            "An all-provider outage publishes a stable sanitized failure code rather than provider material.",
        ),
        safety=GameSafety(
            public_view=(
                "Public live results and broadcast projections contain verified spatial evidence, safe "
                "telemetry, and the observer map needed to render the race."
            ),
            private_agent_state=(
                "Credentials, raw provider responses, prompts, scratchpads, navigation memory, and "
                "provider-private decision evidence remain process-private."
            ),
        ),
        supported_modes=(
            _mode(
                "live_provider_race",
                "Live provider race",
                "Fresh three-racer v1 authority episode with session-only credentials.",
            ),
            _mode("cached_showcase", "Cached showcase", "Fixed v0 presentation race and video."),
            _mode(
                "completed_live_replay",
                "Completed live replay",
                "Verified public replay and optional native video after a completed v1 race.",
            ),
            _mode(
                "benchmark_season",
                "Benchmark season",
                "Frozen, resumable Labyrinth benchmark schedules run outside the interactive composer.",
            ),
        ),
    ),
    GameSpec(
        id="mini-rts",
        title="Mini RTS Skirmish",
        readiness="live_ready",
        readiness_note=(
            "The current Lab starts a fresh two-leg live RTS Skirmish authority run; a separate "
            "cached v0 showcase remains available for immediate playback."
        ),
        task_ids=("rts-skirmish-v0", "rts-skirmish-v1"),
        capability_statements=(
            "Long-horizon planning, resource allocation, and task sequencing.",
            "Adaptation, tactical positioning, and combat prioritization under partial observation.",
            "Economy, construction, training, central-ground control, and stronghold pressure.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each entrant receives its participant-visible semantic observation and camera frame, "
                "not the audience tactical view or the rival's private observation."
            ),
            actions=(
                "The live v1 plan assigns up to three owned units to visible targets for gather, "
                "return, build, train, rally, attack, retreat, or hold tasks."
            ),
            memory=(
                "Each entrant's private scratchpad is episode-local and resets before the symmetric "
                "seat-swapped second leg."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Godot determines the competitive outcome. The evaluation reports economy, construction, "
                "training, objective control, combat, and seat-symmetry aggregates."
            ),
            metrics=(
                _metric("completion", "Completion", "Terminal tick, outcome, and reason."),
                _metric(
                    "economy",
                    "Economy",
                    "Materials gathered, deposits, and constructed infrastructure.",
                ),
                _metric(
                    "tactics",
                    "Tactics",
                    "Units trained, central hold, stronghold damage, and combat exchanges.",
                ),
                _metric(
                    "symmetry",
                    "Seat symmetry",
                    "Paired-leg aggregate comparison across the two seat assignments.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "entrants",
                "Entrant controllers",
                "roster",
                "Choose two providers/models or one permitted scripted opponent for the live two-leg series.",
            ),
            _control(
                "seed",
                "Seed",
                "number",
                "Select the deterministic authority seed used by the pair of legs.",
            ),
            _control(
                "max_live_provider_calls",
                "Live-call safety limit",
                "number",
                "Set the bounded provider-call allowance for the complete two-leg series.",
            ),
        ),
        failure_modes=(
            "Missing, stale, malformed, or timed-out participant input becomes neutral input for that participant without stalling the joint window.",
            "A series can finish by central-objective victory, a scored time limit, or a void result.",
        ),
        safety=GameSafety(
            public_view=(
                "The public tactical view, timeline, evaluation, and replay are presentation/evidence "
                "projections and do not grant the audience control."
            ),
            private_agent_state=(
                "Participant observations, camera frames, scratchpads, plans, provider output, and "
                "credentials are private to the authority path."
            ),
        ),
        supported_modes=(
            _mode(
                "live_two_leg_series",
                "Live two-leg series",
                "Fresh v1 provider-backed series with swapped seats.",
            ),
            _mode("cached_showcase", "Cached showcase", "Verified deterministic v0 broadcast."),
            _mode(
                "saved_native_replay",
                "Saved native replay",
                "Durable participant or broadcast media when archive export is configured.",
            ),
        ),
    ),
    GameSpec(
        id="movement-maze",
        title="Movement Maze",
        readiness="live_ready",
        readiness_note=(
            "A managed solo provider path and a locked participant-visible Demo policy are both "
            "implemented for this control-game task."
        ),
        task_ids=("movement-maze-v0",),
        capability_statements=(
            "Short-horizon navigation from qualitative relative bearings and contact feedback.",
            "Ordered checkpoint completion without a hidden route table or coordinate input.",
            "Movement correction and efficient traversal of a trusted fixed map.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The player sees only the current ordered checkpoint or final beacon, relative bearing, "
                "qualitative distance, and local contact condition; coordinates and routes are forbidden."
            ),
            actions=(
                "The agent emits one strict controller action per decision window, using movement, look, "
                "and ordinary button states."
            ),
            memory=(
                "The controller contract permits episode-local private memory updates; route geometry and "
                "spectator state never become agent memory inputs."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Reach the ordered checkpoints and final beacon. Evaluation compares completion, travel "
                "distance, corrections, collisions, invalid windows, and path efficiency."
            ),
            metrics=(
                _metric(
                    "completion",
                    "Completion",
                    "Final-beacon completion tick or time-limit outcome.",
                ),
                _metric(
                    "distance",
                    "Travelled distance",
                    "Authority-measured path distance in map units.",
                ),
                _metric(
                    "checkpoint_order",
                    "Checkpoint order",
                    "Reached count, expected total, validity, and ordering violations.",
                ),
                _metric(
                    "path_efficiency",
                    "Path efficiency",
                    "Trusted shortest route relative to travelled distance.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider_model",
                "Provider and model",
                "model",
                "Choose a supported live provider/model, or the locked credential-free Demo policy.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic authority seed."),
            _control(
                "maximum_episode_ticks",
                "Episode tick budget",
                "number",
                "Set the bounded solo authority horizon accepted by the episode API.",
            ),
        ),
        failure_modes=(
            "Malformed observations or actions fail closed rather than inventing hidden route information.",
            "Invalid, missing, stale, or timed-out input becomes a recorded neutral window.",
            "The episode ends at the final beacon or the authority time limit.",
        ),
        safety=GameSafety(
            public_view=(
                "Public evaluation exposes trusted map identity and aggregate navigation metrics without "
                "publishing the participant's private observation stream."
            ),
            private_agent_state=(
                "Coordinates, legal route, spectator state, private memory, provider output, and credentials "
                "are excluded from public evidence."
            ),
        ),
        supported_modes=(
            _mode(
                "live_solo_episode",
                "Live solo episode",
                "Provider-backed managed authority episode.",
            ),
            _mode("deterministic_demo", "Deterministic Demo", "Locked no-key visible-only policy."),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Allow-listed movement and checkpoint metrics.",
            ),
        ),
    ),
    GameSpec(
        id="relay-control",
        title="Relay Control",
        readiness="demo_replay_ready",
        readiness_note=(
            "A deterministic two-leg Demo game with visible-only policies; external live-provider "
            "entrants are not enabled for this additive duo task."
        ),
        task_ids=("duo-relay-control-v0",),
        capability_statements=(
            "Objective control, local rival awareness, and guard-versus-pressure timing.",
            "Short-horizon movement and interaction decisions under a fixed joint clock.",
            "Symmetric play across seat-swapped paired legs.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each participant receives visible relay and rival semantics with qualitative bearing, "
                "distance, state, and interaction affordances."
            ),
            actions=(
                "Participants issue ordinary movement, turn, interact, guard, or primary controller input "
                "for each ten-tick joint window."
            ),
            memory=(
                "Per-participant episode memory is private and resets before the second seat assignment."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Hold the relay target or lead at the time limit. Evaluation reports completion, control "
                "ticks, and the paired symmetry delta."
            ),
            metrics=(
                _metric("completion", "Completion", "Terminal tick, outcome, and reason."),
                _metric("control_ticks", "Control time", "Authority-recorded relay-control ticks."),
                _metric("symmetry", "Symmetry", "Control-time delta across the two participants."),
            ),
        ),
        configuration_controls=(
            _control(
                "demo_pair",
                "Demo entrants",
                "fixed",
                "The checked-in pressure and guard visible-only Demo policies are required.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic paired-series seed."),
        ),
        failure_modes=(
            "A failed participant receives neutral input without changing the other participant's action.",
            "The authority may resolve hold-target victory, a time limit, simultaneous terminal state, or a void.",
        ),
        safety=GameSafety(
            public_view=(
                "The safe evaluation exposes outcome and aggregate control metrics without private occupancy or transforms."
            ),
            private_agent_state=(
                "Participant observations, private frames, scratchpads, provider output, and credentials are not public artifacts."
            ),
        ),
        supported_modes=(
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Two seat-swapped authority legs.",
            ),
            _mode(
                "safe_evaluation", "Safe evaluation", "Aggregate relay-control outcome projection."
            ),
        ),
    ),
    GameSpec(
        id="resource-relay",
        title="Resource Relay",
        readiness="demo_replay_ready",
        readiness_note=(
            "A richer deterministic two-leg Demo game; external live-provider entrants are not enabled "
            "for this additive duo task."
        ),
        task_ids=("duo-resource-relay-v0",),
        capability_statements=(
            "Gather, carry, deposit, build, defend, and contest resources under local observation.",
            "Trade-offs between economy, fortification, patrolling, and short-range combat.",
            "Visible-state action selection without hidden rival coordinates or authority inputs.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each participant sees visible resources, relays, barricades, rivals, affordances, inventory, "
                "and local status through its own participant projection."
            ),
            actions=(
                "Participants emit fixed-window controller input for movement, interaction, building, guarding, "
                "dashing, and visible-rival combat."
            ),
            memory=(
                "Private per-participant episode memory is reset between the two seat-swapped legs."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Reach the fixed objective score first, win the time-limit score, or resolve a draw. "
                "Evaluation exposes economy, defence, combat, and paired symmetry aggregates."
            ),
            metrics=(
                _metric(
                    "objective_score", "Objective score", "Deposits converted into authority score."
                ),
                _metric(
                    "economy",
                    "Economy",
                    "Resources gathered, deposits, drops, and completed builds.",
                ),
                _metric("defence", "Defence", "Defend and guard ticks plus dash use."),
                _metric("combat", "Combat", "Hits and knockouts recorded by authority."),
            ),
        ),
        configuration_controls=(
            _control(
                "demo_pair",
                "Demo entrants",
                "fixed",
                "The checked-in harvester and warden visible-only Demo policies are required.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic paired-series seed."),
        ),
        failure_modes=(
            "Missing, stale, invalid, or timed-out actions become neutral input for the affected participant only.",
            "The game resolves objective-target, time-limit score, time-limit draw, simultaneous objective, knockout, or void outcomes.",
        ),
        safety=GameSafety(
            public_view=(
                "Safe evaluation publishes aggregate objective and participant evidence without transforms, private observations, or hidden patrol inputs."
            ),
            private_agent_state=(
                "Private inventory observations, frames, scratchpads, provider output, and credentials are excluded from public artifacts."
            ),
        ),
        supported_modes=(
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Two locked visible-only policies in paired legs.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Aggregate resource, defence, and combat result.",
            ),
        ),
    ),
    GameSpec(
        id="solo-construction",
        title="Solo Construction",
        readiness="live_ready",
        readiness_note=(
            "The managed solo authority accepts either a live provider or the dedicated no-key construction "
            "Demo path; the multi-action showcase reuses the same construction authority task."
        ),
        task_ids=("construction-v0",),
        capability_statements=(
            "Visible-state task sequencing across gathering, carrying, delivery, and construction.",
            "Repeated, resumable construction progress with materials checked throughout.",
            "Longer-horizon execution through a strict milestone-plan interface.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The participant receives visible entities, visible entity states, inventory, and the authority tick; "
                "it does not receive spectator data or hidden positions."
            ),
            actions=(
                "The provider returns a strict construction task plan such as gather materials, deliver materials, "
                "build a barricade, or wait; the normal executor expands accepted tasks into controller actions."
            ),
            memory=(
                "The provider contract supports a bounded private memory update; it is not sent to Godot or included in public evidence."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Godot remains authoritative for task progress and terminal outcome. The multi-action showcase "
                "requires the ordered gather, deposit, build, and success sequence."
            ),
            metrics=(
                _metric(
                    "completion", "Completion", "Authority terminal outcome and completion tick."
                ),
                _metric(
                    "task_sequence",
                    "Task sequence",
                    "Ordered visible gather, delivery, and construction milestones.",
                ),
                _metric(
                    "duration",
                    "Duration",
                    "Authority ticks used within the bounded episode horizon.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider_model",
                "Provider and model",
                "model",
                "Choose a supported live provider/model or the dedicated scripted construction Demo.",
            ),
            _control(
                "scenario",
                "Scenario",
                "select",
                "Choose the standard construction scenario or the fixed multi-action showcase scenario.",
                "construction-v0",
                "multi-action-demo-v0",
            ),
            _control("seed", "Seed", "number", "Select the deterministic authority seed."),
            _control(
                "maximum_episode_ticks",
                "Episode tick budget",
                "number",
                "Set the bounded solo authority horizon accepted by the episode API.",
            ),
        ),
        failure_modes=(
            "Malformed task plans become neutral progress through the normal live-runner fallback rather than a hidden semantic command.",
            "Construction can be interrupted and resumed; terminal authority safety timeouts bound an episode.",
        ),
        safety=GameSafety(
            public_view=(
                "Public presentation can show authority-derived milestones and verified media without revealing participant observations or task-plan text."
            ),
            private_agent_state=(
                "Credentials, prompts, raw provider output, private memory, and hidden world state are not sent to public replay or video."
            ),
        ),
        supported_modes=(
            _mode(
                "live_solo_episode",
                "Live solo episode",
                "Managed authority run with a session-only live credential.",
            ),
            _mode(
                "scripted_demo", "Scripted Demo", "Dedicated credential-free construction provider."
            ),
            _mode(
                "multi_action_showcase",
                "Multi-action showcase",
                "Fixed long-form deterministic construction presentation.",
            ),
        ),
    ),
)

GAME_CATALOG = GameCatalog(games=_GAME_SPECS)


def game_spec(game_id: str) -> GameSpec:
    """Return one exact immutable game guide definition."""

    return GAME_CATALOG.game(game_id)


__all__ = [
    "GAME_CATALOG",
    "GAME_CATALOG_SCHEMA_VERSION",
    "GAME_SPEC_SCHEMA_VERSION",
    "GameAgentInterface",
    "GameCatalog",
    "GameCatalogError",
    "GameConfigurationControl",
    "GameMetric",
    "GameMode",
    "GameReadiness",
    "GameSafety",
    "GameScoring",
    "GameSpec",
    "game_spec",
]
