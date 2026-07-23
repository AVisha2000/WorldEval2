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

GAME_SPEC_SCHEMA_VERSION = "worldeval/lab-game-spec/2"
GAME_CATALOG_SCHEMA_VERSION = "worldeval/lab-game-catalog/2"

GameReadiness = Literal["live_ready", "demo_replay_ready", "experimental"]
ControlType = Literal["fixed", "model", "number", "provider", "roster", "select"]
GameCategoryId = Literal[
    "sandbox-primitives",
    "solo-agent-tasks",
    "two-agent-games",
    "multi-agent-games",
    "strategy-worlds",
]
GameInteractionKind = Literal["solo", "cooperative", "competitive", "mixed"]

_READINESS_VALUES = frozenset(("live_ready", "demo_replay_ready", "experimental"))
_CONTROL_TYPES = frozenset(("fixed", "model", "number", "provider", "roster", "select"))
_CATEGORY_IDS = frozenset(
    (
        "sandbox-primitives",
        "solo-agent-tasks",
        "two-agent-games",
        "multi-agent-games",
        "strategy-worlds",
    )
)
_INTERACTION_KINDS = frozenset(("solo", "cooperative", "competitive", "mixed"))


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
class GameCategory:
    """One stable, presentation-ordered catalogue category.

    Category identity is deliberately independent from source layout.  Deployment code may move
    an authority without changing the public game passport or grouped-picker contract.
    """

    id: GameCategoryId
    label: str
    order: int
    description: str

    def __post_init__(self) -> None:
        _identifier(self.id, field_name="game category id")
        if self.id not in _CATEGORY_IDS:
            raise GameCatalogError("game category is unsupported")
        _text(self.label, field_name="game category label", maximum_bytes=96)
        if (
            isinstance(self.order, bool)
            or not isinstance(self.order, int)
            or not 0 <= self.order <= 999
        ):
            raise GameCatalogError("game category order is invalid")
        _text(self.description, field_name="game category description", maximum_bytes=320)

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "order": self.order,
            "description": self.description,
        }


GAME_CATEGORIES = (
    GameCategory(
        id="sandbox-primitives",
        label="Sandbox Primitives",
        order=10,
        description=(
            "Godot-owned building blocks used to compose and verify movement, visibility, "
            "resources, interaction, scoring, and termination."
        ),
    ),
    GameCategory(
        id="solo-agent-tasks",
        label="Solo Agent Tasks",
        order=20,
        description="Single-agent environments focused on control, planning, and execution.",
    ),
    GameCategory(
        id="two-agent-games",
        label="Two-Agent Games",
        order=30,
        description="Paired competitive or cooperative environments with isolated agent state.",
    ),
    GameCategory(
        id="multi-agent-games",
        label="Multi-Agent Games",
        order=40,
        description="Environments with three or more independently controlled participants.",
    ),
    GameCategory(
        id="strategy-worlds",
        label="Strategy Worlds",
        order=50,
        description=(
            "Long-horizon worlds centred on economy, tactics, territorial control, and adaptation."
        ),
    ),
)

if (
    len({category.id for category in GAME_CATEGORIES}) != len(GAME_CATEGORIES)
    or len({category.order for category in GAME_CATEGORIES}) != len(GAME_CATEGORIES)
    or tuple(sorted(GAME_CATEGORIES, key=lambda category: category.order)) != GAME_CATEGORIES
):
    raise GameCatalogError("game category registry is not unique and presentation-ordered")

_GAME_CATEGORY_BY_ID: Mapping[str, GameCategory] = MappingProxyType(
    {category.id: category for category in GAME_CATEGORIES}
)


@dataclass(frozen=True)
class GameParticipantRange:
    """Participant cardinality supported by one game authority."""

    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        for field_name, value in (("minimum", self.minimum), ("maximum", self.maximum)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
                raise GameCatalogError(f"participant {field_name} is invalid")
        if self.minimum > self.maximum:
            raise GameCatalogError("participant range is invalid")

    def public_dict(self) -> dict[str, int]:
        return {"minimum": self.minimum, "maximum": self.maximum}


@dataclass(frozen=True)
class GameCapabilities:
    """Explicit product support; ``True`` never grants or invents gameplay authority."""

    live_launch: bool
    demo: bool
    replay: bool
    spectator: bool
    benchmark: bool
    checkpoint: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bool)
            for value in (
                self.live_launch,
                self.demo,
                self.replay,
                self.spectator,
                self.benchmark,
                self.checkpoint,
            )
        ):
            raise GameCatalogError("game capabilities must be explicit booleans")
        if self.benchmark and not self.replay:
            raise GameCatalogError("benchmark-capable games require replay evidence")

    def public_dict(self) -> dict[str, bool]:
        return {
            "live_launch": self.live_launch,
            "demo": self.demo,
            "replay": self.replay,
            "spectator": self.spectator,
            "benchmark": self.benchmark,
            "checkpoint": self.checkpoint,
        }


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
    primary_category: GameCategory
    secondary_tags: tuple[str, ...]
    participants: GameParticipantRange
    interaction_kind: GameInteractionKind
    capabilities: GameCapabilities
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
        registered_category = _GAME_CATEGORY_BY_ID.get(self.primary_category.id)
        if registered_category is None or registered_category != self.primary_category:
            raise GameCatalogError("game primary category is not the registered category")
        if (
            not self.secondary_tags
            or tuple(sorted(self.secondary_tags)) != self.secondary_tags
            or len(set(self.secondary_tags)) != len(self.secondary_tags)
        ):
            raise GameCatalogError("game secondary tags must be non-empty, unique, and sorted")
        for tag in self.secondary_tags:
            _identifier(tag, field_name="game secondary tag")
        if self.interaction_kind not in _INTERACTION_KINDS:
            raise GameCatalogError("game interaction kind is unsupported")
        if self.interaction_kind == "solo" and self.participants != GameParticipantRange(1, 1):
            raise GameCatalogError("solo interaction requires exactly one participant")
        if self.interaction_kind != "solo" and self.participants.maximum < 2:
            raise GameCatalogError(
                "multi-participant interaction requires at least two participants"
            )
        if self.readiness == "live_ready" and not self.capabilities.live_launch:
            raise GameCatalogError("live-ready games must support live launch")
        if self.readiness == "demo_replay_ready" and (
            self.capabilities.live_launch
            or not (self.capabilities.demo or self.capabilities.replay)
        ):
            raise GameCatalogError(
                "demo/replay-ready games must be non-live and support demo or replay"
            )
        if self.readiness == "experimental" and self.capabilities.live_launch:
            raise GameCatalogError("experimental games cannot advertise live launch")
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
            "primary_category": self.primary_category.public_dict(),
            "secondary_tags": list(self.secondary_tags),
            "participants": self.participants.public_dict(),
            "interaction_kind": self.interaction_kind,
            "capabilities": self.capabilities.public_dict(),
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
        for game in self.games:
            if game.primary_category.id not in _GAME_CATEGORY_BY_ID:
                raise GameCatalogError("game catalog contains an unknown category")
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
            "categories": self.category_groups(),
            "games": [game.public_dict() for game in self.games],
        }

    def category_groups(self) -> list[dict[str, object]]:
        """Project the complete ordered taxonomy with stable game identities.

        Empty groups remain present so clients can explain the product taxonomy without inventing
        placeholder games.  A grouped picker may hide an empty group or render it as coming soon.
        """

        game_ids_by_category = {
            category.id: [game.id for game in self.games if game.primary_category.id == category.id]
            for category in GAME_CATEGORIES
        }
        return [
            {
                **category.public_dict(),
                "game_ids": game_ids_by_category[category.id],
            }
            for category in GAME_CATEGORIES
        ]

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


def _category(category_id: GameCategoryId) -> GameCategory:
    return _GAME_CATEGORY_BY_ID[category_id]


def _participants(minimum: int, maximum: int) -> GameParticipantRange:
    return GameParticipantRange(minimum=minimum, maximum=maximum)


def _capabilities(
    *,
    live_launch: bool,
    demo: bool,
    replay: bool,
    spectator: bool,
    benchmark: bool = False,
    checkpoint: bool = False,
) -> GameCapabilities:
    return GameCapabilities(
        live_launch=live_launch,
        demo=demo,
        replay=replay,
        spectator=spectator,
        benchmark=benchmark,
        checkpoint=checkpoint,
    )


# Keep this tuple sorted by public ``id``.  The claims below are bound to the current live APIs,
# deterministic/demo catalogues, and sealed showcases; this is not an aspirational feature list.
_GAME_SPECS = (
    GameSpec(
        id="central-relay",
        title="Central Relay Duel",
        readiness="live_ready",
        readiness_note=(
            "The frozen v1 paired-series authority supports both session-key live entrants and "
            "the credential-free Alpha/Bravo Demo pair."
        ),
        primary_category=_category("two-agent-games"),
        secondary_tags=("duel", "objective-control", "seat-swapped"),
        participants=_participants(2, 2),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("central-relay-v0",),
        capability_statements=(
            "Simultaneous two-agent control around a shared relay objective.",
            "Opponent-aware pressure, guarding, and movement from participant-visible evidence.",
            "Fairness through two deterministic legs with the entrants assigned to opposite seats.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each duelist receives its own participant-visible relay and rival semantics, "
                "including qualitative bearing, distance, state, and available affordances."
            ),
            actions=(
                "Each entrant supplies an ordinary controller action for the shared fixed-tick "
                "decision window; Godot applies both accepted actions under one joint clock."
            ),
            memory=(
                "Entrant scratchpads are private and episode-local. They reset between the two "
                "seat-swapped legs and never become authority state."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Godot determines each leg's terminal outcome and winner. The paired result reports "
                "wins, draws, verification, and the winner only after both seat assignments finish."
            ),
            metrics=(
                _metric("leg_outcomes", "Leg outcomes", "Terminal outcome and reason per leg."),
                _metric(
                    "series_result",
                    "Series result",
                    "Entrant wins, draws, and overall paired-series status.",
                ),
                _metric(
                    "seat_symmetry",
                    "Seat symmetry",
                    "Like-for-like comparison across the two swapped seat assignments.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "entrants",
                "Entrant controllers",
                "roster",
                "Choose two supported live provider/models or the locked Alpha/Bravo Demo pair.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic paired-series seed."),
            _control(
                "max_live_provider_calls",
                "Live-call safety limit",
                "number",
                "Set the bounded provider-call allowance for the complete two-leg series.",
            ),
        ),
        failure_modes=(
            "Missing, stale, malformed, or timed-out input becomes neutral input for only the affected participant.",
            "A leg can finish normally, draw, time out, or seal as void without manufacturing a winner.",
        ),
        safety=GameSafety(
            public_view=(
                "The spectator feed, sealed replay, hashes, terminal outcomes, and paired result are "
                "safe authority projections and do not grant control."
            ),
            private_agent_state=(
                "Participant observations and frames, prompts, scratchpads, raw provider output, "
                "and credentials remain outside public artifacts."
            ),
        ),
        supported_modes=(
            _mode(
                "live_two_leg_series",
                "Live two-leg series",
                "Provider-backed v1 authority run with deterministic seat swapping.",
            ),
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Credential-free Alpha and Bravo policies through the normal provider boundary.",
            ),
            _mode(
                "sealed_replay",
                "Sealed replay",
                "Offline-verifiable authority replay and paired result.",
            ),
        ),
    ),
    GameSpec(
        id="checkpoint-race",
        title="Checkpoint Race",
        readiness="demo_replay_ready",
        readiness_note=(
            "A deterministic, two-leg Demo game with seat swaps; external live-provider entrants "
            "are not enabled for this additive duo task."
        ),
        primary_category=_category("two-agent-games"),
        secondary_tags=("navigation", "race", "seat-swapped"),
        participants=_participants(2, 2),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
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
        primary_category=_category("strategy-worlds"),
        secondary_tags=("partial-observation", "resource-management", "strategy"),
        participants=_participants(3, 3),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
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
        id="duo-spar",
        title="Duo Sparring",
        readiness="demo_replay_ready",
        readiness_note=(
            "The managed v2 authority has a deterministic pressure-versus-counter-guard Demo pair; "
            "external live-provider entrants are not enabled for this additive duo task."
        ),
        primary_category=_category("two-agent-games"),
        secondary_tags=("combat", "duel", "seat-swapped"),
        participants=_participants(2, 2),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("duo-spar-v0",),
        capability_statements=(
            "Close-range positioning, facing, pressure, and counter-guard timing.",
            "Visible-opponent action selection without health, transforms, or rival-private state.",
            "Symmetric combat comparison across two deterministic seat assignments.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each participant receives only its visible rival's qualitative bearing, distance, "
                "combat state, and affordances plus its own participant-safe status."
            ),
            actions=(
                "The locked Demo policies move, turn, strike, or guard for one fixed ten-tick joint "
                "window; Godot resolves both controls and combat simultaneously."
            ),
            memory=(
                "Each participant has private episode-local controller memory that is cleared before "
                "the seat-swapped second leg."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Win by authority knockout or resolve the time-limit result. Evaluation reports "
                "hits, knockouts, reliability, and cross-seat symmetry without exposing health."
            ),
            metrics=(
                _metric("completion", "Completion", "Terminal tick, outcome, and reason."),
                _metric(
                    "combat",
                    "Combat",
                    "Authority-recorded hits landed, hits received, and knockouts.",
                ),
                _metric(
                    "reliability",
                    "Reliability",
                    "Accepted decision windows versus safe fallback windows.",
                ),
                _metric("symmetry", "Symmetry", "Hit delta across the two participants."),
            ),
        ),
        configuration_controls=(
            _control(
                "demo_pair",
                "Demo entrants",
                "fixed",
                "The checked-in pressure and counter-guard visible-only policies are required.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic paired-series seed."),
        ),
        failure_modes=(
            "Missing, stale, malformed, or timed-out input becomes neutral input for the affected participant only.",
            "The game can end by knockout, time limit, simultaneous terminal state, or a void result.",
        ),
        safety=GameSafety(
            public_view=(
                "Safe evaluation exposes terminal evidence and aggregate combat totals without "
                "participant transforms, observations, or health values."
            ),
            private_agent_state=(
                "Private frames, observations, scratchpads, raw policy output, and any provider "
                "credentials are excluded from public replay and evaluation."
            ),
        ),
        supported_modes=(
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Two locked visible-only policies in seat-swapped authority legs.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Allow-listed combat, completion, reliability, and symmetry projection.",
            ),
        ),
    ),
    GameSpec(
        id="interaction",
        title="Interaction",
        readiness="live_ready",
        readiness_note=(
            "The managed solo v1 authority accepts a session-key live model or the locked "
            "credential-free Interaction Demo policy."
        ),
        primary_category=_category("solo-agent-tasks"),
        secondary_tags=("interaction", "resource-management", "solo-control"),
        participants=_participants(1, 1),
        interaction_kind="solo",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("interaction-v0",),
        capability_statements=(
            "Grounded resource gathering and delivery from visible entity affordances.",
            "Alignment, interaction timing, inventory use, and recovery from interrupted progress.",
            "Short-horizon action sequencing without coordinates or hidden world state.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The agent sees visible resource and relay entities with qualitative bearing, "
                "distance, state, affordances, its inventory, recent events, and prior receipt."
            ),
            actions=(
                "The agent emits one strict controller action per decision window using movement, "
                "look, interact, cancel, and ordinary controller state."
            ),
            memory=(
                "A bounded private memory update may carry episode-local notes between calls; it "
                "is not authority state and is absent from public evidence."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Gather one marked material and deposit it at the home relay. Evaluation reports "
                "success, completion, valid control, collisions, and typed progress evidence."
            ),
            metrics=(
                _metric("completion", "Completion", "Authority success and completion tick."),
                _metric(
                    "progress",
                    "Progress",
                    "Typed resource-gathered and material-deposited checkpoints.",
                ),
                _metric(
                    "control_quality",
                    "Control quality",
                    "Valid actions, interaction alignment failures, and unnecessary collisions.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider_model",
                "Provider and model",
                "model",
                "Choose a supported live provider/model or the locked Interaction Demo policy.",
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
            "Invalid, missing, stale, or timed-out provider input becomes a recorded neutral window.",
            "Misaligned interaction or early cancellation preserves authority state and may require recovery.",
            "The episode fails at the authority time limit if the deposit is incomplete.",
        ),
        safety=GameSafety(
            public_view=(
                "Public replay and evaluation contain typed authority progress, safe receipts, "
                "terminal evidence, and integrity hashes."
            ),
            private_agent_state=(
                "Exact transforms, participant observations and frames, private memory, prompts, "
                "raw provider output, and credentials remain protected."
            ),
        ),
        supported_modes=(
            _mode(
                "live_solo_episode",
                "Live solo episode",
                "Provider-backed managed v1 authority episode.",
            ),
            _mode(
                "deterministic_demo",
                "Deterministic Demo",
                "Locked credential-free visible-only interaction policy.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Allow-listed completion, progress, and control-quality evidence.",
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
        primary_category=_category("multi-agent-games"),
        secondary_tags=("memory", "navigation", "partial-observation", "race"),
        participants=_participants(3, 3),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
            benchmark=True,
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
        primary_category=_category("strategy-worlds"),
        secondary_tags=("combat", "partial-observation", "resource-management", "strategy"),
        participants=_participants(2, 2),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
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
        primary_category=_category("solo-agent-tasks"),
        secondary_tags=("navigation", "solo-control"),
        participants=_participants(1, 1),
        interaction_kind="solo",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
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
        id="neutral-encounter",
        title="Neutral Encounter",
        readiness="live_ready",
        readiness_note=(
            "The managed solo v1 authority accepts a session-key live model or the locked "
            "credential-free Neutral Encounter Demo policy."
        ),
        primary_category=_category("solo-agent-tasks"),
        secondary_tags=("combat", "interaction", "solo-control"),
        participants=_participants(1, 1),
        interaction_kind="solo",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("neutral-encounter-v0",),
        capability_statements=(
            "Combat positioning, guard and dash timing, and energy-aware primary attacks.",
            "Transition from resolving a visible neutral threat to activating a defended relay.",
            "Recovery from damage and action cooldowns using only participant-visible state.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The agent sees a visible neutral and relay through qualitative bearing, distance, "
                "state, and affordances plus health, energy, cooldown status, recent events, and receipt."
            ),
            actions=(
                "The agent emits strict movement, look, primary, guard, dash, interact, or cancel "
                "controller input for each bounded decision window."
            ),
            memory=(
                "The runner may retain a bounded private episode scratchpad; Godot does not retain "
                "it in authority checkpoints and it is excluded from public artifacts."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Defeat or safely resolve the deterministic neutral and activate the relay before "
                "knockout or time limit. Evaluation exposes safe terminal and control evidence."
            ),
            metrics=(
                _metric(
                    "completion",
                    "Completion",
                    "Relay activation, knockout, or time-limit terminal evidence.",
                ),
                _metric(
                    "combat_control",
                    "Combat control",
                    "Damage taken and authority-recorded control changes.",
                ),
                _metric(
                    "progress",
                    "Progress",
                    "Typed neutral-damaged and relay-activated authority checkpoints.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider_model",
                "Provider and model",
                "model",
                "Choose a supported live provider/model or the locked Neutral Encounter Demo policy.",
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
            "Invalid, missing, stale, or timed-out provider input becomes a recorded neutral window.",
            "Poor guard, dash, or attack timing can lead to authority-recorded knockout.",
            "The episode fails at the authority time limit if the relay is not activated.",
        ),
        safety=GameSafety(
            public_view=(
                "Public evaluation exposes safe terminal, progress, damage, and integrity evidence "
                "without exact positions or private observations."
            ),
            private_agent_state=(
                "Participant frames and observations, exact transforms, private memory, prompts, "
                "raw provider output, and credentials remain protected."
            ),
        ),
        supported_modes=(
            _mode(
                "live_solo_episode",
                "Live solo episode",
                "Provider-backed managed v1 authority episode.",
            ),
            _mode(
                "deterministic_demo",
                "Deterministic Demo",
                "Locked credential-free visible-only encounter policy.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Allow-listed completion, combat-control, and progress evidence.",
            ),
        ),
    ),
    GameSpec(
        id="operator-action-course",
        title="Operator Action Course",
        readiness="live_ready",
        readiness_note=(
            "This is the canonical composite Sandbox Primitives playground: its managed v2 "
            "Godot authority supports a session-key live model and a locked visible-only Demo."
        ),
        primary_category=_category("sandbox-primitives"),
        secondary_tags=("composite-primitives", "control-validation", "sandbox"),
        participants=_participants(1, 1),
        interaction_kind="solo",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("operator-action-course-v0",),
        capability_statements=(
            "One authority-owned course composes movement, turning, gathering, carrying, depositing, and building.",
            "It also validates dash, guard, primary, cancel, hazard response, and celebration controls.",
            "The station matrix diagnoses concrete control primitives without creating a second gameplay authority.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The agent receives only participant-visible station targets, relative semantics, "
                "affordances, local status, recent events, and the previous authority receipt."
            ),
            actions=(
                "The agent explicitly chooses ordinary controller inputs while Godot advances and "
                "validates the twelve ordered stations: walk, turn, gather, carry, deposit, build, "
                "dash, guard, primary, cancel, hazard, and celebrate."
            ),
            memory=(
                "A bounded private episode scratchpad can support station tracking, but it cannot "
                "alter station state and is never persisted in public evidence."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Complete all twelve ordered stations before the time limit. The strict safe evaluator "
                "reports the station pass matrix, control accuracy, invalid windows, damage, and distance."
            ),
            metrics=(
                _metric(
                    "station_matrix",
                    "Station matrix",
                    "Pass/fail evidence for each of the twelve authority-owned control stations.",
                ),
                _metric(
                    "control_accuracy",
                    "Control accuracy",
                    "Successful command attempts relative to all recorded attempts.",
                ),
                _metric(
                    "safety",
                    "Safety",
                    "Invalid windows and authority-recorded damage taken.",
                ),
                _metric(
                    "distance",
                    "Travelled distance",
                    "Authority-measured course travel in map units.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider_model",
                "Provider and model",
                "model",
                "Choose a supported live provider/model or the locked Operator Action Course Demo.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic authority seed."),
            _control(
                "maximum_episode_ticks",
                "Episode tick budget",
                "number",
                "Set the bounded solo course horizon accepted by the episode API.",
            ),
        ),
        failure_modes=(
            "Missing, stale, malformed, or timed-out output advances as neutral input and cannot imply continuation.",
            "A command attempted at the wrong station or with invalid state is recorded without granting progress.",
            "The course fails at the authority time limit if any required station remains incomplete.",
        ),
        safety=GameSafety(
            public_view=(
                "The public course view and evaluation expose the ordered station matrix and safe "
                "numeric authority aggregates; they do not become a competing simulator."
            ),
            private_agent_state=(
                "Exact transforms, private observations and frames, prompts, raw provider output, "
                "scratchpad content, and credentials remain protected."
            ),
        ),
        supported_modes=(
            _mode(
                "live_solo_course",
                "Live solo course",
                "Provider-backed managed v2 composite primitive run.",
            ),
            _mode(
                "deterministic_demo",
                "Deterministic Demo",
                "Locked credential-free visible-only policy over the same Godot authority.",
            ),
            _mode(
                "primitive_diagnostics",
                "Primitive diagnostics",
                "Allow-listed station, control, safety, and distance evaluation.",
            ),
        ),
    ),
    GameSpec(
        id="orientation",
        title="Orientation",
        readiness="live_ready",
        readiness_note=(
            "The managed solo v1 authority accepts a session-key live model or the locked "
            "credential-free Orientation Demo policy."
        ),
        primary_category=_category("solo-agent-tasks"),
        secondary_tags=("navigation", "orientation", "solo-control"),
        participants=_participants(1, 1),
        interaction_kind="solo",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("orientation-v0",),
        capability_statements=(
            "Egocentric turning and movement from qualitative relative bearing and distance.",
            "Stable target approach and sustained beacon hold without coordinates.",
            "Correction after overshoot, collision, or leaving the goal radius early.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "The agent sees the goal beacon through qualitative bearing, distance, state, and "
                "affordances plus its facing, contact, recent events, and previous receipt."
            ),
            actions=(
                "The agent emits one strict controller action per decision window using movement, "
                "look, and ordinary button state; Godot applies and receipts it."
            ),
            memory=(
                "The runner may carry a bounded private episode memory update between decisions; "
                "it is not included in authority state or public evidence."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Reach the visible beacon and remain inside its radius for the required hold. "
                "Evaluation reports success, completion, held ticks, control validity, and collisions."
            ),
            metrics=(
                _metric("completion", "Completion", "Beacon-held success and completion tick."),
                _metric(
                    "beacon_hold",
                    "Beacon hold",
                    "Authority-recorded total held ticks and typed beacon checkpoints.",
                ),
                _metric(
                    "control_quality",
                    "Control quality",
                    "Valid-action rate, controller changes, and unnecessary collisions.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "provider_model",
                "Provider and model",
                "model",
                "Choose a supported live provider/model or the locked Orientation Demo policy.",
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
            "Invalid, missing, stale, or timed-out provider input becomes a recorded neutral window.",
            "Leaving the beacon before the hold completes resets authority hold progress.",
            "The episode fails at the authority time limit if the beacon hold is incomplete.",
        ),
        safety=GameSafety(
            public_view=(
                "Public replay and evaluation expose typed beacon events, safe receipts, terminal "
                "evidence, and integrity hashes without exact transforms."
            ),
            private_agent_state=(
                "Participant frames and observations, private memory, prompts, raw provider output, "
                "and credentials remain protected."
            ),
        ),
        supported_modes=(
            _mode(
                "live_solo_episode",
                "Live solo episode",
                "Provider-backed managed v1 authority episode.",
            ),
            _mode(
                "deterministic_demo",
                "Deterministic Demo",
                "Locked credential-free visible-only orientation policy.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Allow-listed completion, beacon-hold, and control-quality evidence.",
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
        primary_category=_category("two-agent-games"),
        secondary_tags=("duel", "objective-control", "seat-swapped"),
        participants=_participants(2, 2),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
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
        primary_category=_category("two-agent-games"),
        secondary_tags=("combat", "economy", "resource-management", "seat-swapped"),
        participants=_participants(2, 2),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
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
        primary_category=_category("solo-agent-tasks"),
        secondary_tags=("construction", "long-horizon", "resource-management"),
        participants=_participants(1, 1),
        interaction_kind="solo",
        capabilities=_capabilities(
            live_launch=True,
            demo=True,
            replay=True,
            spectator=True,
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
    GameSpec(
        id="trio-free-for-all",
        title="Trio Free-for-All",
        readiness="demo_replay_ready",
        readiness_note=(
            "The v3 three-participant authority currently runs the fixed credential-free "
            "Sol/Luna/Terra roster across a cyclic three-leg Demo series."
        ),
        primary_category=_category("multi-agent-games"),
        secondary_tags=("combat", "free-for-all", "seat-rotated"),
        participants=_participants(3, 3),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("trio-free-for-all-v0",),
        capability_statements=(
            "Three-way combat positioning, threat selection, guard, dash, and attack timing.",
            "Adaptation as rivals are damaged or eliminated under simultaneous joint windows.",
            "Cyclic fairness: every Demo entrant uses every seat and spawn exactly once.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each participant receives only its own visible rivals, objective semantics, "
                "qualitative geometry, status, recent events, and prior receipt."
            ),
            actions=(
                "Each active participant chooses movement, turn, primary, guard, dash, interact, "
                "or neutral control for a shared fixed ten-tick window; Godot commits all actions."
            ),
            memory=(
                "The three Demo controllers have independent private episode memory. Eliminated "
                "participants become permanently call-free neutral seats for the rest of that leg."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Resolve each leg by last standing, simultaneous knockout, or time-limit ranking. "
                "The series aggregates placements, damage, objective points, reliability, and seat symmetry."
            ),
            metrics=(
                _metric(
                    "placements", "Placements", "Competition rankings and explicit ties per leg."
                ),
                _metric(
                    "combat",
                    "Combat",
                    "Authority-recorded damage dealt, damage taken, and elimination state.",
                ),
                _metric(
                    "reliability",
                    "Reliability",
                    "Decision windows, fallback windows, provider calls, and suppressed post-elimination calls.",
                ),
                _metric(
                    "cyclic_symmetry",
                    "Cyclic symmetry",
                    "Aggregate range after every entrant uses every seat and spawn.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "demo_roster",
                "Demo roster",
                "fixed",
                "The checked-in Sol, Luna, and Terra visible-only policies are required.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic three-leg series seed."),
            _control(
                "cyclic_schedule",
                "Cyclic schedule",
                "fixed",
                "Three legs rotate every entrant through every participant seat and spawn.",
            ),
        ),
        failure_modes=(
            "Missing, stale, malformed, or timed-out input becomes neutral input for only the affected participant.",
            "Eliminated participants cannot act and do not consume further provider calls in that leg.",
            "A leg can resolve last-standing, simultaneous knockout, time-limit ranking or tie, or void.",
        ),
        safety=GameSafety(
            public_view=(
                "The spectator projection, replay, placements, safe aggregates, and verification "
                "hashes are browser-safe authority evidence."
            ),
            private_agent_state=(
                "Participant observations and frames, transforms, private memory, raw policy output, "
                "and credentials remain excluded from public artifacts."
            ),
        ),
        supported_modes=(
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Three cyclic legs with the locked Sol/Luna/Terra roster.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Placements, combat, reliability, and cyclic-normalization aggregates.",
            ),
            _mode(
                "sealed_replay",
                "Sealed replay",
                "Offline-verifiable per-leg authority evidence.",
            ),
        ),
    ),
    GameSpec(
        id="trio-relay",
        title="Trio Relay",
        readiness="demo_replay_ready",
        readiness_note=(
            "The v3 three-participant authority currently runs the fixed credential-free "
            "Sol/Luna/Terra roster across a cyclic three-leg Demo series."
        ),
        primary_category=_category("multi-agent-games"),
        secondary_tags=("objective-control", "race", "seat-rotated"),
        participants=_participants(3, 3),
        interaction_kind="competitive",
        capabilities=_capabilities(
            live_launch=False,
            demo=True,
            replay=True,
            spectator=True,
        ),
        task_ids=("trio-relay-v0",),
        capability_statements=(
            "Three-way movement, contested objective control, and pressure-versus-defence timing.",
            "Simultaneous decisions around a relay whose hold progress resets when contested.",
            "Cyclic fairness: every Demo entrant uses every seat and spawn exactly once.",
        ),
        agent_interface=GameAgentInterface(
            observation=(
                "Each participant receives only its visible rivals and central relay through "
                "qualitative bearing, distance, state, and affordances plus local status and receipts."
            ),
            actions=(
                "Each entrant chooses movement, turn, interact, or neutral controller input for the "
                "same fixed ten-tick window; combat controls are disabled by this authority."
            ),
            memory=(
                "Sol, Luna, and Terra keep isolated private episode memory that resets for each "
                "new cyclic seat assignment."
            ),
        ),
        scoring=GameScoring(
            summary=(
                "Secure the relay for the required uninterrupted hold or resolve a time-limit ranking. "
                "The series reports placements, objective points, reliability, and cyclic symmetry."
            ),
            metrics=(
                _metric(
                    "placements", "Placements", "Competition rankings and explicit ties per leg."
                ),
                _metric(
                    "objective_points",
                    "Objective points",
                    "Authority-recorded relay-control contribution by entrant.",
                ),
                _metric(
                    "reliability",
                    "Reliability",
                    "Decision windows, fallback windows, and provider calls.",
                ),
                _metric(
                    "cyclic_symmetry",
                    "Cyclic symmetry",
                    "Aggregate range after every entrant uses every seat and spawn.",
                ),
            ),
        ),
        configuration_controls=(
            _control(
                "demo_roster",
                "Demo roster",
                "fixed",
                "The checked-in Sol, Luna, and Terra visible-only policies are required.",
            ),
            _control("seed", "Seed", "number", "Select the deterministic three-leg series seed."),
            _control(
                "cyclic_schedule",
                "Cyclic schedule",
                "fixed",
                "Three legs rotate every entrant through every participant seat and spawn.",
            ),
        ),
        failure_modes=(
            "Missing, stale, malformed, or timed-out input becomes neutral input for only the affected participant.",
            "Contested or empty relay occupancy clears the current uninterrupted hold.",
            "A leg can resolve relay hold, time-limit ranking or tie, or void.",
        ),
        safety=GameSafety(
            public_view=(
                "The spectator projection, replay, placements, safe aggregates, and verification "
                "hashes are browser-safe authority evidence."
            ),
            private_agent_state=(
                "Participant observations and frames, transforms, private memory, raw policy output, "
                "and credentials remain excluded from public artifacts."
            ),
        ),
        supported_modes=(
            _mode(
                "deterministic_demo_series",
                "Deterministic Demo series",
                "Three cyclic legs with the locked Sol/Luna/Terra roster.",
            ),
            _mode(
                "safe_evaluation",
                "Safe evaluation",
                "Placements, objective, reliability, and cyclic-normalization aggregates.",
            ),
            _mode(
                "sealed_replay",
                "Sealed replay",
                "Offline-verifiable per-leg authority evidence.",
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
    "GAME_CATEGORIES",
    "GAME_SPEC_SCHEMA_VERSION",
    "GameAgentInterface",
    "GameCapabilities",
    "GameCatalog",
    "GameCatalogError",
    "GameCategory",
    "GameCategoryId",
    "GameConfigurationControl",
    "GameInteractionKind",
    "GameMetric",
    "GameMode",
    "GameParticipantRange",
    "GameReadiness",
    "GameSafety",
    "GameScoring",
    "GameSpec",
    "game_spec",
]
