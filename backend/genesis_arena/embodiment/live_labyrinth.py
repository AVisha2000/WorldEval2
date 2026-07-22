"""Provider-neutral, input-responsive three-racer Labyrinth Run authority.

This module deliberately does not alter the cached ``trio-maze-race-v0`` showcase.  A live run
has a fresh ``v1`` identity, asks every racer for a strict task plan at every decision point, and
publishes only deterministic spatial evidence.  Provider output and scratchpads are kept in
process-private evidence and never copied into public replay or evaluation projections.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    Union,
    runtime_checkable,
)

from .episode_memory import EpisodeMemory, EpisodeMemoryError
from .labyrinth_run import (
    HEADINGS,
    PARTICIPANTS,
    PROTOCOL_VERSION,
    TICKS_PER_CELL,
)
from .maze_maps import MazeMapError, MazeMapSpec, default_maze_map_spec
from .protocol import canonical_json_bytes, strict_json_loads
from .providers.contracts import (
    ProviderAdapter,
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
)
from .scratchpad import EpisodeScratchpad, ScratchpadError

LIVE_TASK_ID = "trio-maze-race-v1"
LIVE_REPLAY_SCHEMA = "worldarena/live-labyrinth-run-replay/1"
MAX_LIVE_PROVIDER_CALLS = 450
DEFAULT_VISION_RANGE_CELLS = 4
MAX_FINITE_VISION_RANGE_CELLS = 128
MAX_CORRIDOR_COMMAND_CELLS = 256
_PROVIDER_TIMEOUT_NS = 45 * 1_000_000_000
LABYRINTH_PROTOCOL_PROMPT = (
    "You control one racer in a private maze lane. Return exactly the MazeTaskPlan JSON object. "
    "Use only the current observation, your private scratchpad, and navigation_memory. "
    "navigation_memory is authoritative episode-local state derived only from your prior visible "
    "passages and accepted moves. Its maze-nav/2 pose is [local_x,local_y,heading]; last is the "
    "previous [choice,outcome]; current gives visits, untried relative passages, and the relative "
    "backtrack passage. route has omitted_steps plus run-length [N|E|S|W,count] rows. unresolved "
    "rows are [x,y,untried_mask], landmark rows are [x,y,label,last_seen_turn], known rows are "
    "[x,y,open_mask,traversed_mask,visits], and omitted counts excluded rows. Direction bits are "
    "N=1,E=2,S=4,W=8. movement_receipt describes the previous submitted command, outcome, "
    "relative path, cells moved, and stop reason. "
    "vision.visible_cells contains straight-line, wall-"
    "occluded cells as [x,y,open_mask,landmark]; it never reveals around a corner. Choose only a "
    "currently visible relative passage or wait. Set "
    "movement_mode to follow_corridor only when you explicitly authorize the racer to continue "
    "through every cell that has exactly one non-backtracking exit; execution stops before the "
    "next junction, dead end, exit, episode origin, previously traversed cell, or "
    "max_corridor_cells. Use single_cell for exact one-cell control. A wait must use single_cell "
    "and max_corridor_cells=1. "
    "scratchpad_update is an "
    "optional full-replacement private note and cannot change navigation_memory."
)
MAZE_NAVIGATION_SKILL_ID = "maze-navigation-v1"
MAZE_NAVIGATION_SKILL_PATH = (
    Path(__file__).resolve().parent / "skills" / MAZE_NAVIGATION_SKILL_ID / "SKILL.md"
)
_SKILL_FORBIDDEN = (
    re.compile(r"(?i)\b(?:map[_ -]?id|map[_ -]?seed|hidden[_ -]?route|seed)\b"),
    re.compile(r"(?i)\b(?:start|exit)(?:[_ -]?location)?\s*(?:is|at|=|:)"),
    re.compile(r"(?:\[|\()\s*\d+\s*,\s*\d+\s*(?:\]|\))"),
    re.compile(r"[.#SE]{7,}"),
    re.compile(
        r"(?i)\broute\s*:\s*(?:left|right|forward|back)"
        r"(?:\s*(?:,|then|->)\s*(?:left|right|forward|back))+"
    ),
    re.compile(
        r"(?i)\btake\s+(?:the\s+)?(?:left|right|forward|back)\s*"
        r"(?:,|then|->)\s*(?:take\s+)?(?:the\s+)?(?:left|right|forward|back)"
    ),
)
_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "protocol_version",
        "episode_id",
        "observation_id",
        "participant_id",
        "passage_choice",
        "movement_mode",
        "max_corridor_cells",
        "scratchpad_update",
    ],
    "properties": {
        "protocol_version": {"const": PROTOCOL_VERSION},
        "episode_id": {"type": "string"},
        "observation_id": {"type": "string"},
        "participant_id": {"type": "string", "enum": list(PARTICIPANTS)},
        "passage_choice": {"enum": ["left", "forward", "right", "back", "wait"]},
        "movement_mode": {"enum": ["single_cell", "follow_corridor"]},
        "max_corridor_cells": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_CORRIDOR_COMMAND_CELLS,
        },
        "scratchpad_update": {"type": "string", "maxLength": 2048},
    },
}
_PLAN_SCHEMA_JSON = canonical_json_bytes(_PLAN_SCHEMA)
_PROTECTED_TERMS = frozenset(
    (
        "scratchpad",
        "raw_output",
        "credential",
        "prompt",
        "chain_of_thought",
        "navigation_memory",
        "episode_memory",
    )
)
_LIVE_REPLAY_FIELDS = frozenset(
    (
        "schema_version",
        "task_id",
        "protocol_version",
        "episode_id",
        "authority_hz",
        "maximum_ticks",
        "elapsed_ticks",
        "provider_calls",
        "participant_call_budget",
        "provider_calls_by_participant",
        "vision",
        "map",
        "racers",
        "events",
        "result",
        "final_state_sha256",
    )
)
_LIVE_RACER_FIELDS = frozenset(
    (
        "participant_id",
        "entrant_id",
        "display_name",
        "provider",
        "model",
        "color",
        "place",
        "finish_tick",
        "finished",
        "distance_cells",
        "shortest_path_cells",
        "path_efficiency_basis_points",
        "unique_corridor_cells",
        "repeated_corridor_cells",
        "invalid_decisions",
        "waiting_windows",
        "provider_calls",
        "final_cell",
        "path",
    )
)


class LiveLabyrinthError(RuntimeError):
    """A live race, its public evidence, or lifecycle projection is invalid."""


class LiveLabyrinthNotFoundError(KeyError):
    pass


class LiveLabyrinthNotReadyError(RuntimeError):
    pass


class BenchmarkMazeRaceAbort(RuntimeError):
    """Sanitized fail-fast signal used only by resumable benchmark orchestration."""

    def __init__(self, kind: str, failures: Sequence[str]) -> None:
        if kind not in {"fatal_provider", "infrastructure_outage"}:
            raise ValueError("benchmark maze abort kind is invalid")
        normalized = tuple(sorted(failures))
        if not normalized or any(
            value not in {item.value for item in ProviderFailureKind} for value in normalized
        ):
            raise ValueError("benchmark maze abort failures are invalid")
        self.kind = kind
        self.failures = normalized
        super().__init__(f"benchmark maze race aborted: {kind}")


VisionRange = Union[int, Literal["infinite"]]
SkillMode = Literal["none", "maze-navigation-v1"]


def load_maze_navigation_skill() -> str:
    try:
        text = MAZE_NAVIGATION_SKILL_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise LiveLabyrinthError("maze navigation skill is unavailable") from error
    return validate_maze_skill_text(text)


def validate_maze_skill_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 16_384:
        raise ValueError("maze navigation skill text is invalid")
    if "\x00" in value or any(pattern.search(value) for pattern in _SKILL_FORBIDDEN):
        raise ValueError("maze navigation skill contains map-specific material")
    return value


def maze_navigation_skill_sha256(skill_text: str | None = None) -> str:
    normalized = load_maze_navigation_skill() if skill_text is None else validate_maze_skill_text(
        skill_text
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def labyrinth_protocol_prompt_sha256() -> str:
    return hashlib.sha256(LABYRINTH_PROTOCOL_PROMPT.encode("utf-8")).hexdigest()


def compose_labyrinth_system_prompt(
    *, skill_mode: SkillMode = "none", skill_text: str | None = None
) -> str:
    """Compose the protocol and, when selected, exactly one hash-bound generic skill."""

    if skill_mode == "none":
        if skill_text is not None:
            raise ValueError("skill text requires maze-navigation-v1 mode")
        return LABYRINTH_PROTOCOL_PROMPT
    if skill_mode != MAZE_NAVIGATION_SKILL_ID:
        raise ValueError("live maze skill mode is invalid")
    normalized = load_maze_navigation_skill() if skill_text is None else validate_maze_skill_text(
        skill_text
    )
    digest = maze_navigation_skill_sha256(normalized)
    return (
        f"{LABYRINTH_PROTOCOL_PROMPT}\n\n"
        f"<skill id=\"{MAZE_NAVIGATION_SKILL_ID}\" sha256=\"{digest}\">\n"
        f"{normalized}\n</skill>"
    )


@dataclass(frozen=True)
class _LiveMazeTaskPlan:
    episode_id: str
    observation_id: str
    participant_id: str
    passage_choice: Literal["left", "forward", "right", "back", "wait"]
    movement_mode: Literal["single_cell", "follow_corridor"]
    max_corridor_cells: int
    scratchpad_update: str
    protocol_version: Literal["maze-task-plan-v1"] = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("live maze task plan protocol is invalid")
        if not isinstance(self.episode_id, str) or not self.episode_id.startswith("ep_"):
            raise ValueError("live maze task plan episode is invalid")
        if not isinstance(self.observation_id, str) or not self.observation_id:
            raise ValueError("live maze task plan observation is invalid")
        if self.participant_id not in PARTICIPANTS:
            raise ValueError("live maze task plan participant is invalid")
        if self.passage_choice not in {"left", "forward", "right", "back", "wait"}:
            raise ValueError("live maze task plan passage is invalid")
        if self.movement_mode not in {"single_cell", "follow_corridor"}:
            raise ValueError("live maze task plan movement mode is invalid")
        if (
            isinstance(self.max_corridor_cells, bool)
            or not isinstance(self.max_corridor_cells, int)
            or not 1 <= self.max_corridor_cells <= MAX_CORRIDOR_COMMAND_CELLS
        ):
            raise ValueError("live maze corridor limit is invalid")
        if self.movement_mode == "single_cell" and self.max_corridor_cells != 1:
            raise ValueError("single-cell movement must have a one-cell limit")
        if self.passage_choice == "wait" and self.movement_mode != "single_cell":
            raise ValueError("wait cannot start a corridor command")
        if (
            not isinstance(self.scratchpad_update, str)
            or len(self.scratchpad_update.encode()) > 2048
        ):
            raise ValueError("live maze scratchpad update is invalid")


def _parse_live_maze_task_plan(payload: bytes, *, expected: Mapping[str, str]) -> _LiveMazeTaskPlan:
    value = strict_json_loads(payload)
    required = {
        "protocol_version",
        "episode_id",
        "observation_id",
        "participant_id",
        "passage_choice",
        "movement_mode",
        "max_corridor_cells",
        "scratchpad_update",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("live maze task plan fields are invalid")
    plan = _LiveMazeTaskPlan(**value)
    for name in ("episode_id", "observation_id", "participant_id"):
        if getattr(plan, name) != expected.get(name):
            raise ValueError("live maze task plan is stale or mismatched")
    return plan


@dataclass(frozen=True)
class LiveMazeEntrant:
    participant_id: str
    entrant_id: str
    display_name: str
    provider: str
    model: str
    color: str

    def __post_init__(self) -> None:
        if self.participant_id not in PARTICIPANTS:
            raise ValueError("live maze participant is invalid")
        for name in ("entrant_id", "display_name", "provider", "model", "color"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"live maze {name} is invalid")

    def public_dict(self) -> dict[str, str]:
        return {
            "participant_id": self.participant_id,
            "entrant_id": self.entrant_id,
            "display_name": self.display_name,
            "provider": self.provider,
            "model": self.model,
            "color": self.color,
        }


@dataclass(frozen=True)
class ProtectedMazeDecision:
    """In-memory-only call material; never use this in a response or archive."""

    participant_id: str
    observation_id: str
    raw_output: bytes | None
    scratchpad_utf8: bytes
    provider_failure: str | None


@dataclass(frozen=True)
class SafeMazeDecision:
    """Allow-listed behavioral evidence suitable for aggregate benchmark artifacts."""

    participant_id: str
    observation_seq: int
    disposition: str
    passage_choice: str
    movement_mode: str
    max_corridor_cells: int
    cells_moved: int
    stopped_because: str
    provider_failure: str | None
    telemetry: ProviderTelemetry | None

    def as_dict(self) -> dict[str, object]:
        return {
            "participant_id": self.participant_id,
            "observation_seq": self.observation_seq,
            "disposition": self.disposition,
            "passage_choice": self.passage_choice,
            "movement_mode": self.movement_mode,
            "max_corridor_cells": self.max_corridor_cells,
            "cells_moved": self.cells_moved,
            "stopped_because": self.stopped_because,
            "provider_failure": self.provider_failure,
            "telemetry": None if self.telemetry is None else self.telemetry.as_dict(),
        }


@dataclass(frozen=True)
class MazeMemoryTelemetry:
    participant_id: str
    peak_bytes: int
    evictions: int

    def as_dict(self) -> dict[str, object]:
        return {
            "participant_id": self.participant_id,
            "peak_bytes": self.peak_bytes,
            "evictions": self.evictions,
        }


@dataclass(frozen=True)
class LiveMazeRaceExecution:
    replay: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    protected_decisions: tuple[ProtectedMazeDecision, ...] = field(repr=False)
    safe_decisions: tuple[SafeMazeDecision, ...] = ()
    memory_telemetry: tuple[MazeMemoryTelemetry, ...] = ()


@runtime_checkable
class MazeProvider(ProviderAdapter, Protocol):
    """The existing retry-free provider interface, narrowed for live maze controllers."""


@dataclass
class _Racer:
    entrant: LiveMazeEntrant
    provider: MazeProvider
    position: tuple[int, int]
    heading: int = 0
    observation_seq: int = 0
    ready_tick: int = 0
    distance_cells: int = 0
    invalid_decisions: int = 0
    waiting_windows: int = 0
    finished_tick: int | None = None
    provider_calls: int = 0
    movement_receipt: Mapping[str, object] | None = None
    visits: list[tuple[int, int]] = field(default_factory=list)
    scratchpad: EpisodeScratchpad = field(default_factory=EpisodeScratchpad, repr=False)
    navigation_memory: MazeNavigationMemory = field(init=False, repr=False)
    memory_peak_bytes: int = 0
    memory_evictions: int = 0

    def __post_init__(self) -> None:
        self.navigation_memory = MazeNavigationMemory(self.entrant.participant_id)


@dataclass
class _KnownMazeCell:
    open_mask: int = 0
    traversed_mask: int = 0
    visits: int = 0
    landmark: str = ""
    seen_seq: int = 0


class MazeNavigationMemory:
    """Bounded v2 DFS state reconstructed only from visible passages and accepted moves."""

    _RELATIVE = {"forward": 0, "right": 1, "back": 2, "left": 3}
    _OUTCOMES = frozenset(("moved", "waited", "invalid", "provider_failure"))

    def __init__(self, participant_id: str) -> None:
        if participant_id not in PARTICIPANTS:
            raise ValueError("maze navigation memory participant is invalid")
        self._participant_id = participant_id
        self._position = (0, 0)
        self._heading = 0
        self._route = [(0, 0)]
        self._cells: dict[tuple[int, int], _KnownMazeCell] = {}
        self._last: tuple[str, str] | None = None
        self._forced_backtrack: tuple[int, int] | None = None
        self._memory = EpisodeMemory()
        self._peak_bytes = len(self._memory.utf8)
        self._evictions = 0

    @property
    def utf8(self) -> bytes:
        return self._memory.utf8

    @property
    def peak_bytes(self) -> int:
        return self._peak_bytes

    @property
    def evictions(self) -> int:
        return self._evictions

    @property
    def traversed_positions(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            sorted(
                position
                for position, cell in self._cells.items()
                if cell.traversed_mask != 0
            )
        )

    def observe(
        self,
        *,
        observation_seq: int,
        visible_passages: Sequence[str],
        landmark: str,
        visible_cells: Sequence[tuple[tuple[int, int], int, str]] = (),
    ) -> Mapping[str, Any]:
        if (
            isinstance(observation_seq, bool)
            or not isinstance(observation_seq, int)
            or observation_seq < 0
        ):
            raise ValueError("maze navigation observation sequence is invalid")
        if any(choice not in self._RELATIVE for choice in visible_passages):
            raise ValueError("maze navigation passages are invalid")
        if not isinstance(landmark, str):
            raise TypeError("maze navigation landmark must be a string")
        for position, open_mask, visible_landmark in visible_cells:
            if (
                not isinstance(position, tuple)
                or len(position) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in position)
                or isinstance(open_mask, bool)
                or not isinstance(open_mask, int)
                or not 0 <= open_mask <= 15
                or not isinstance(visible_landmark, str)
            ):
                raise ValueError("maze navigation visible cell is invalid")
            visible = self._cells.setdefault(position, _KnownMazeCell())
            visible.open_mask |= open_mask
            visible.seen_seq = observation_seq
            if visible_landmark != "none":
                visible.landmark = visible_landmark
        cell = self._cells.setdefault(self._position, _KnownMazeCell())
        cell.seen_seq = observation_seq
        for choice in visible_passages:
            cell.open_mask |= 1 << ((self._heading + self._RELATIVE[choice]) % 4)
        cell.visits += 1
        if landmark != "none":
            cell.landmark = landmark
        value, evictions = self._bounded_snapshot(observation_seq)
        self._memory.replace(value)
        # Telemetry counts compaction events, not the same omitted projection rows every turn.
        self._evictions += int(evictions > 0)
        self._peak_bytes = max(self._peak_bytes, len(self._memory.utf8))
        return self._memory.snapshot

    def apply_transition(self, choice: str, outcome: str) -> None:
        if choice not in (*self._RELATIVE, "wait"):
            raise ValueError("maze navigation choice is invalid")
        if outcome not in self._OUTCOMES:
            raise ValueError("maze navigation outcome is invalid")
        if outcome == "moved":
            if choice == "wait":
                raise ValueError("maze navigation cannot move while waiting")
            absolute = (self._heading + self._RELATIVE[choice]) % 4
            source = self._position
            dx, dy = HEADINGS[absolute]
            target = (source[0] + dx, source[1] + dy)
            source_cell = self._cells.setdefault(source, _KnownMazeCell())
            source_cell.open_mask |= 1 << absolute
            source_cell.traversed_mask |= 1 << absolute
            reverse = (absolute + 2) % 4
            target_cell = self._cells.setdefault(target, _KnownMazeCell())
            target_was_traversed = target_cell.traversed_mask != 0
            target_cell.open_mask |= 1 << reverse
            target_cell.traversed_mask |= 1 << reverse
            self._position = target
            self._heading = absolute
            if self._forced_backtrack is not None and target == self._forced_backtrack:
                self._route.pop()
                self._forced_backtrack = None
            elif len(self._route) > 1 and target == self._route[-2]:
                self._route.pop()
            elif target_was_traversed:
                # A reconverging edge is not allowed to replace the DFS tree parent.  Retain a
                # temporary duplicate node and force the next accepted move back to its source.
                self._route.append(target)
                self._forced_backtrack = source
            else:
                self._route.append(target)
        self._last = (choice, outcome)

    def close(self) -> None:
        self._memory.close()
        self._cells.clear()
        self._route.clear()
        self._last = None
        self._forced_backtrack = None

    def _snapshot(self, observation_seq: int) -> dict[str, Any]:
        current = self._cells[self._position]
        untried_mask = (
            0
            if self._forced_backtrack is not None
            else current.open_mask & ~current.traversed_mask
        )
        backtrack = None
        parent = self._forced_backtrack
        if parent is None and len(self._route) > 1:
            parent = self._route[-2]
        if parent is not None:
            vector = (parent[0] - self._position[0], parent[1] - self._position[1])
            absolute = HEADINGS.index(vector)
            backtrack = _relative_name((absolute - self._heading) % 4)
        route_runs: list[list[object]] = []
        for source, target in zip(self._route, self._route[1:]):
            vector = (target[0] - source[0], target[1] - source[1])
            direction = "NESW"[HEADINGS.index(vector)]
            if route_runs and route_runs[-1][0] == direction:
                route_runs[-1][1] = int(route_runs[-1][1]) + 1
            else:
                route_runs.append([direction, 1])
        unresolved = []
        landmarks = []
        for position, cell in self._cells.items():
            remaining = cell.open_mask & ~cell.traversed_mask
            if position != self._position and remaining:
                unresolved.append([position[0], position[1], remaining])
            if cell.landmark:
                landmarks.append([position[0], position[1], cell.landmark, cell.seen_seq])
        route_set = set(self._route)
        known_positions = sorted(
            self._cells,
            key=lambda position: (
                position != self._position,
                position not in route_set,
                not bool(
                    self._cells[position].open_mask
                    & ~self._cells[position].traversed_mask
                ),
                -self._cells[position].seen_seq,
                position,
            ),
        )
        known = [
            [
                position[0],
                position[1],
                self._cells[position].open_mask,
                self._cells[position].traversed_mask,
                self._cells[position].visits,
            ]
            for position in known_positions
        ]
        unresolved.sort()
        landmarks.sort(key=lambda value: (-int(value[3]), value[:2]))
        omitted_route_runs = route_runs[:-32]
        retained_route_runs = route_runs[-32:]
        omitted_route_steps = sum(int(value[1]) for value in omitted_route_runs)
        omitted_known = max(0, len(known) - 32)
        omitted_unresolved = max(0, len(unresolved) - 32)
        omitted_landmarks = max(0, len(landmarks) - 8)
        return {
            "v": "maze-nav/2",
            "owner": self._participant_id,
            "turn": observation_seq,
            "pose": [self._position[0], self._position[1], "NESW"[self._heading]],
            "last": None if self._last is None else list(self._last),
            "current": {
                "visits": current.visits,
                "untried": self._relative_passages(untried_mask),
                "backtrack": backtrack,
            },
            "route": {
                "omitted_steps": omitted_route_steps,
                "runs": retained_route_runs,
            },
            "unresolved": unresolved[:32],
            "landmarks": landmarks[:8],
            "known": known[:32],
            "omitted": {
                "known": omitted_known,
                "unresolved": omitted_unresolved,
                "landmarks": omitted_landmarks,
            },
        }

    def _bounded_snapshot(self, observation_seq: int) -> tuple[dict[str, Any], int]:
        value = self._snapshot(observation_seq)
        evictions = sum(int(value) for value in value["omitted"].values()) + int(
            value["route"]["omitted_steps"] > 0
        )
        while len(canonical_json_bytes(value)) > 2048:
            known = value["known"]
            landmarks = value["landmarks"]
            route = value["route"]
            unresolved = value["unresolved"]
            omitted = value["omitted"]
            if len(known) > 1:
                remove = max(1, len(known) - 32, (len(known) - 1) // 2)
                del known[-remove:]
                omitted["known"] += remove
            elif landmarks:
                remove = max(1, len(landmarks) - 8, len(landmarks) // 2)
                del landmarks[-remove:]
                omitted["landmarks"] += remove
            elif route["runs"]:
                remove = max(1, len(route["runs"]) - 32, len(route["runs"]) // 2)
                removed_runs = route["runs"][:remove]
                del route["runs"][:remove]
                route["omitted_steps"] += sum(int(item[1]) for item in removed_runs)
            elif unresolved:
                remove = max(1, len(unresolved) - 32, len(unresolved) // 2)
                del unresolved[-remove:]
                omitted["unresolved"] += remove
            else:
                raise EpisodeMemoryError("maze navigation state cannot fit episode memory")
            evictions += remove
        return value, evictions

    def _relative_passages(self, mask: int) -> list[str]:
        return [
            name
            for name in ("forward", "right", "back", "left")
            if mask & (1 << ((self._heading + self._RELATIVE[name]) % 4))
        ]


def _relative_name(relative: int) -> str:
    return ("forward", "right", "back", "left")[relative]


def _relative_target(
    racer: _Racer,
    choice: str,
    graph: Mapping[tuple[int, int], Sequence[tuple[int, int]]],
) -> tuple[tuple[int, int], int] | None:
    if choice == "wait":
        return None
    relative = {"forward": 0, "right": 1, "back": 2, "left": 3}[choice]
    heading = (racer.heading + relative) % 4
    dx, dy = HEADINGS[heading]
    candidate = (racer.position[0] + dx, racer.position[1] + dy)
    return (candidate, heading) if candidate in graph[racer.position] else None


def normalize_vision_range(value: object) -> VisionRange:
    if value == "infinite":
        return "infinite"
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_FINITE_VISION_RANGE_CELLS
    ):
        raise ValueError("live maze vision range is invalid")
    return value


def maze_visible_cells(
    position: tuple[int, int],
    vision_range_cells: VisionRange,
    map_spec: MazeMapSpec | None = None,
) -> tuple[tuple[int, int], ...]:
    """Return 360-degree straight sightlines, stopping at walls and never looking around corners."""

    vision_range_cells = normalize_vision_range(vision_range_cells)
    normalized_map = default_maze_map_spec() if map_spec is None else map_spec
    if not isinstance(normalized_map, MazeMapSpec):
        raise TypeError("live maze map spec is invalid")
    graph = normalized_map.graph()
    if position not in graph:
        raise ValueError("live maze vision origin is invalid")
    limit = (
        max(len(normalized_map.rows), max(map(len, normalized_map.rows)))
        if vision_range_cells == "infinite"
        else vision_range_cells
    )
    visible = {position}
    for dx, dy in HEADINGS:
        for distance in range(1, limit + 1):
            candidate = (position[0] + dx * distance, position[1] + dy * distance)
            if candidate not in graph:
                break
            visible.add(candidate)
    return tuple(sorted(visible))


def _open_mask(
    position: tuple[int, int],
    graph: Mapping[tuple[int, int], Sequence[tuple[int, int]]],
) -> int:
    mask = 0
    for candidate in graph[position]:
        vector = (candidate[0] - position[0], candidate[1] - position[1])
        mask |= 1 << HEADINGS.index(vector)
    return mask


def _corridor_steps(
    racer: _Racer,
    *,
    map_spec: MazeMapSpec,
    passage_choice: str,
    movement_mode: str,
    max_corridor_cells: int,
) -> tuple[list[tuple[tuple[int, int], int, str]], str]:
    graph = map_spec.graph()
    traversed_cells = {
        (map_spec.start[0] + position[0], map_spec.start[1] + position[1])
        for position in racer.navigation_memory.traversed_positions
    }
    target = _relative_target(racer, passage_choice, graph)
    if target is None:
        return [], "waited" if passage_choice == "wait" else "passage_unavailable"
    first_cell, first_heading = target
    steps = [(first_cell, first_heading, passage_choice)]
    if movement_mode == "single_cell":
        return steps, "single_cell_complete"

    previous = racer.position
    current = first_cell
    heading = first_heading
    exit_cell = map_spec.exit
    while len(steps) < max_corridor_cells:
        if current == exit_cell:
            return steps, "exit_reached"
        if current == map_spec.start:
            return steps, "origin_reached"
        if current in traversed_cells:
            return steps, "revisited_cell_reached"
        onward = [candidate for candidate in graph[current] if candidate != previous]
        if not onward:
            return steps, "dead_end_reached"
        if len(onward) > 1:
            return steps, "junction_reached"
        candidate = onward[0]
        vector = (candidate[0] - current[0], candidate[1] - current[1])
        next_heading = HEADINGS.index(vector)
        relative_choice = _relative_name((next_heading - heading) % 4)
        steps.append((candidate, next_heading, relative_choice))
        previous, current, heading = current, candidate, next_heading
    if current == exit_cell:
        return steps, "exit_reached"
    if current == map_spec.start:
        return steps, "origin_reached"
    if current in traversed_cells:
        return steps, "revisited_cell_reached"
    onward = [candidate for candidate in graph[current] if candidate != previous]
    if not onward:
        return steps, "dead_end_reached"
    if len(onward) > 1:
        return steps, "junction_reached"
    return steps, "max_cells_reached"


def _visible_observation(
    racer: _Racer,
    *,
    episode_id: str,
    tick: int,
    vision_range_cells: VisionRange,
    map_spec: MazeMapSpec,
) -> dict[str, object]:
    graph = map_spec.graph()
    passages = []
    for candidate in graph[racer.position]:
        vector = (candidate[0] - racer.position[0], candidate[1] - racer.position[1])
        absolute = HEADINGS.index(vector)
        passages.append(_relative_name((absolute - racer.heading) % 4))
    # A sorted relative list makes the request deterministic and contains no coordinates, map,
    # opponent, or spectator material.
    passages.sort(key=("forward", "right", "back", "left").index)
    observation_id = f"obs_{racer.entrant.participant_id}_{racer.observation_seq:04d}"
    landmark_map = map_spec.landmark_map
    landmark = landmark_map.get(racer.position, "none")
    start = map_spec.start
    visible_cells = maze_visible_cells(racer.position, vision_range_cells, map_spec)
    visible_cell_rows = [
        [
            cell[0] - start[0],
            cell[1] - start[1],
            _open_mask(cell, graph),
            landmark_map.get(cell, "none"),
        ]
        for cell in visible_cells
    ]
    navigation_memory = racer.navigation_memory.observe(
        observation_seq=racer.observation_seq,
        visible_passages=passages,
        landmark=landmark,
        visible_cells=tuple(((row[0], row[1]), row[2], row[3]) for row in visible_cell_rows),
    )
    return {
        "episode_id": episode_id,
        "observation_id": observation_id,
        "observation_seq": racer.observation_seq,
        "participant_id": racer.entrant.participant_id,
        "protocol_version": PROTOCOL_VERSION,
        "profile": "maze-relative-passages-and-sightlines-v2",
        "tick": tick,
        "visible_passages": passages,
        "vision": {
            "range_cells": vision_range_cells,
            "occlusion": "straight_line_walls",
            "visible_cells": visible_cell_rows,
        },
        "landmark": landmark,
        "at_exit": racer.position == map_spec.exit,
        "movement_receipt": racer.movement_receipt,
        "navigation_memory": navigation_memory,
    }


async def run_live_labyrinth_race(
    *,
    episode_id: str,
    entrants: Sequence[LiveMazeEntrant],
    providers: Mapping[str, MazeProvider],
    max_provider_calls: int | None = None,
    vision_range_cells: VisionRange = DEFAULT_VISION_RANGE_CELLS,
    map_spec: MazeMapSpec | None = None,
    participant_call_budget: int | None = None,
    skill_mode: SkillMode = "none",
    skill_text: str | None = None,
    cancel_event: asyncio.Event | None = None,
    _benchmark_fail_fast: bool = False,
) -> LiveMazeRaceExecution:
    """Run a simultaneous, input-responsive race with exactly three isolated controllers.

    Calls for a window are gathered concurrently.  An invalid, stale, failed, or illegal response
    becomes a deterministic wait for that racer only; it never exposes another racer's state.
    """

    if not isinstance(episode_id, str) or not episode_id.startswith("ep_"):
        raise ValueError("live maze episode id is invalid")
    if len(entrants) != 3 or {value.participant_id for value in entrants} != set(PARTICIPANTS):
        raise ValueError("live maze needs exactly the three fixed participant seats")
    if max_provider_calls is not None and (
        isinstance(max_provider_calls, bool)
        or not isinstance(max_provider_calls, int)
        or not 1 <= max_provider_calls <= MAX_LIVE_PROVIDER_CALLS
    ):
        raise ValueError("live maze provider budget is invalid")
    normalized_map = default_maze_map_spec() if map_spec is None else map_spec
    if not isinstance(normalized_map, MazeMapSpec):
        raise TypeError("live maze map spec is invalid")
    dynamic_budget = normalized_map.participant_call_budget
    if participant_call_budget is not None and (
        isinstance(participant_call_budget, bool)
        or not isinstance(participant_call_budget, int)
        or not 1 <= participant_call_budget <= dynamic_budget
    ):
        raise ValueError("live maze participant budget is invalid")
    effective_budget = (
        dynamic_budget if participant_call_budget is None else participant_call_budget
    )
    if max_provider_calls is not None:
        # Compatibility input: the former competition-wide limit is now an equal per-racer
        # ceiling.  It can constrain a short interactive race but cannot let one racer consume
        # another racer's allowance.
        effective_budget = min(effective_budget, max_provider_calls)
    maximum_ticks = 4 * effective_budget
    system_prompt = compose_labyrinth_system_prompt(skill_mode=skill_mode, skill_text=skill_text)
    vision_range_cells = normalize_vision_range(vision_range_cells)
    if set(providers) != set(PARTICIPANTS) or any(
        not isinstance(value, ProviderAdapter) for value in providers.values()
    ):
        raise ValueError("live maze requires one provider adapter per participant")
    if any(providers[value.participant_id].provider_name != value.provider for value in entrants):
        raise ValueError("live maze entrant provider identity differs")
    start, exit_cell = normalized_map.start, normalized_map.exit
    racers = [
        _Racer(value, providers[value.participant_id], start, visits=[start]) for value in entrants
    ]
    racers.sort(key=lambda value: value.entrant.participant_id)
    protected: list[ProtectedMazeDecision] = []
    safe_decisions: list[SafeMazeDecision] = []
    events: list[dict[str, object]] = []
    tick = 0
    calls = 0
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            pending = [
                value
                for value in racers
                if value.finished_tick is None
                and value.ready_tick < maximum_ticks
                and value.provider_calls < effective_budget
            ]
            if not pending:
                break
            tick = min(value.ready_tick for value in pending)
            active = [value for value in pending if value.ready_tick == tick]
            observations = {
                value.entrant.participant_id: _visible_observation(
                    value,
                    episode_id=episode_id,
                    tick=tick,
                    vision_range_cells=vision_range_cells,
                    map_spec=normalized_map,
                )
                for value in active
            }
            requests = {
                value.entrant.participant_id: ProviderRequest(
                    episode_id=episode_id,
                    participant_id=value.entrant.participant_id,
                    observation_seq=value.observation_seq,
                    # Provider adapters compare this against the process monotonic clock.  A
                    # synthetic tick-derived value is already far in the past on a running
                    # process, which turns every live request into an immediate timeout.
                    deadline_monotonic_ns=time.monotonic_ns() + _PROVIDER_TIMEOUT_NS,
                    model=value.entrant.model,
                    system_prompt=system_prompt,
                    observation_json=canonical_json_bytes(
                        observations[value.entrant.participant_id]
                    ),
                    action_schema_json=_PLAN_SCHEMA_JSON,
                    scratchpad_utf8=value.scratchpad.utf8,
                    max_output_bytes=4096,
                )
                for value in active
            }
            results = await asyncio.gather(
                *(
                    value.provider.request(requests[value.entrant.participant_id])
                    for value in active
                ),
                return_exceptions=True,
            )
            if _benchmark_fail_fast:
                window_failures = tuple(
                    result.failure
                    for result in results
                    if isinstance(result, ProviderCallResult) and result.failure is not None
                )
                if any(
                    failure in {ProviderFailureKind.CREDENTIAL, ProviderFailureKind.QUOTA}
                    for failure in window_failures
                ):
                    raise BenchmarkMazeRaceAbort(
                        "fatal_provider", tuple(value.value for value in window_failures)
                    )
                if (
                    {value.entrant.participant_id for value in active} == set(PARTICIPANTS)
                    and len(window_failures) == len(active)
                    and set(window_failures)
                    <= {
                        ProviderFailureKind.TRANSPORT,
                        ProviderFailureKind.RATE_LIMIT,
                    }
                ):
                    raise BenchmarkMazeRaceAbort(
                        "infrastructure_outage",
                        tuple(value.value for value in window_failures),
                    )
            calls += len(active)
            for racer, result in zip(active, results):
                racer.provider_calls += 1
                observation = observations[racer.entrant.participant_id]
                raw = result.raw_output if isinstance(result, ProviderCallResult) else None
                failure = (
                    result.failure.value
                    if isinstance(result, ProviderCallResult) and result.failure
                    else "provider_error"
                    if isinstance(result, Exception)
                    else None
                )
                protected.append(
                    ProtectedMazeDecision(
                        racer.entrant.participant_id,
                        str(observation["observation_id"]),
                        raw,
                        racer.scratchpad.utf8,
                        failure,
                    )
                )
                choice = "wait"
                submitted_choice = "wait"
                movement_mode = "single_cell"
                max_corridor_cells = 1
                disposition = "wait"
                if raw is not None:
                    try:
                        plan = _parse_live_maze_task_plan(
                            raw,
                            expected={
                                "episode_id": episode_id,
                                "observation_id": str(observation["observation_id"]),
                                "participant_id": racer.entrant.participant_id,
                            },
                        )
                        racer.scratchpad.set(plan.scratchpad_update)
                        choice = plan.passage_choice
                        submitted_choice = plan.passage_choice
                        movement_mode = plan.movement_mode
                        max_corridor_cells = plan.max_corridor_cells
                        disposition = "accepted"
                    except (ValueError, ScratchpadError):
                        racer.invalid_decisions += 1
                        disposition = "invalid"
                elif failure is not None:
                    racer.invalid_decisions += 1
                    disposition = "provider_failure"
                available_steps = max(1, (maximum_ticks - tick) // TICKS_PER_CELL)
                steps, stopped_because = _corridor_steps(
                    racer,
                    map_spec=normalized_map,
                    passage_choice=choice,
                    movement_mode=movement_mode,
                    max_corridor_cells=min(max_corridor_cells, available_steps),
                )
                if choice != "wait" and not steps:
                    racer.invalid_decisions += 1
                    disposition = "invalid"
                    choice = "wait"
                    stopped_because = "passage_unavailable"
                command = {
                    "passage_choice": submitted_choice,
                    "movement_mode": movement_mode,
                    "max_corridor_cells": max_corridor_cells,
                }
                if not steps:
                    memory_outcome = (
                        "provider_failure"
                        if disposition == "provider_failure"
                        else "waited"
                        if disposition in {"accepted", "wait"} and submitted_choice == "wait"
                        else "invalid"
                    )
                    racer.navigation_memory.apply_transition(submitted_choice, memory_outcome)
                    racer.waiting_windows += 1
                    racer.ready_tick = min(maximum_ticks, tick + TICKS_PER_CELL)
                    racer.movement_receipt = {
                        "command": command,
                        "outcome": memory_outcome,
                        "cells_moved": 0,
                        "relative_path": [],
                        "stopped_because": stopped_because,
                    }
                    events.append(
                        {
                            "tick": racer.ready_tick,
                            "participant_id": racer.entrant.participant_id,
                            "kind": disposition,
                            "choice": choice,
                            "movement_mode": movement_mode,
                        }
                    )
                else:
                    relative_path: list[str] = []
                    for index, (target, heading, relative_choice) in enumerate(steps, start=1):
                        racer.navigation_memory.apply_transition(relative_choice, "moved")
                        racer.position, racer.heading = target, heading
                        racer.distance_cells += 1
                        racer.visits.append(racer.position)
                        relative_path.append(relative_choice)
                        move_tick = tick + index * TICKS_PER_CELL
                        events.append(
                            {
                                "tick": move_tick,
                                "participant_id": racer.entrant.participant_id,
                                "kind": "move",
                                "choice": relative_choice,
                                "movement_mode": movement_mode,
                            }
                        )
                    racer.ready_tick = tick + len(steps) * TICKS_PER_CELL
                    racer.movement_receipt = {
                        "command": command,
                        "outcome": "moved",
                        "cells_moved": len(steps),
                        "relative_path": relative_path,
                        "stopped_because": stopped_because,
                    }
                    if racer.position == exit_cell:
                        racer.finished_tick = racer.ready_tick
                        events.append(
                            {
                                "tick": racer.finished_tick,
                                "participant_id": racer.entrant.participant_id,
                                "kind": "finish",
                            }
                        )
                safe_decisions.append(
                    SafeMazeDecision(
                        participant_id=racer.entrant.participant_id,
                        observation_seq=racer.observation_seq,
                        disposition=disposition,
                        passage_choice=submitted_choice,
                        movement_mode=movement_mode,
                        max_corridor_cells=max_corridor_cells,
                        cells_moved=len(steps),
                        stopped_because=stopped_because,
                        provider_failure=failure,
                        telemetry=result.telemetry
                        if isinstance(result, ProviderCallResult)
                        else None,
                    )
                )
                racer.observation_seq += 1
    finally:
        # The objects held in `protected` retain snapshots for internal evidence only; every live
        # mutable controller scratchpad and navigation memory is erased at the episode boundary.
        for racer in racers:
            racer.memory_peak_bytes = racer.navigation_memory.peak_bytes
            racer.memory_evictions = racer.navigation_memory.evictions
            racer.scratchpad.close()
            racer.navigation_memory.close()
    tick = max((int(event["tick"]) for event in events), default=tick)
    replay = _public_replay(
        episode_id,
        racers,
        events,
        calls,
        tick,
        vision_range_cells=vision_range_cells,
        map_spec=normalized_map,
        participant_call_budget=effective_budget,
        maximum_ticks=maximum_ticks,
    )
    verify_live_replay(replay)
    memory_telemetry = tuple(
        MazeMemoryTelemetry(
            racer.entrant.participant_id,
            racer.memory_peak_bytes,
            racer.memory_evictions,
        )
        for racer in racers
    )
    return LiveMazeRaceExecution(
        replay,
        public_live_evaluation(replay),
        tuple(protected),
        tuple(safe_decisions),
        memory_telemetry,
    )


async def run_benchmark_labyrinth_race(
    *,
    episode_id: str,
    entrants: Sequence[LiveMazeEntrant],
    providers: Mapping[str, MazeProvider],
    map_spec: MazeMapSpec,
    participant_call_budget: int | None = None,
    vision_range_cells: VisionRange = DEFAULT_VISION_RANGE_CELLS,
    skill_text: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> LiveMazeRaceExecution:
    """Benchmark-facing entry point with no legacy competition-wide budget semantics."""

    return await run_live_labyrinth_race(
        episode_id=episode_id,
        entrants=entrants,
        providers=providers,
        map_spec=map_spec,
        participant_call_budget=participant_call_budget,
        vision_range_cells=vision_range_cells,
        skill_mode="none" if skill_text is None else MAZE_NAVIGATION_SKILL_ID,
        skill_text=skill_text,
        cancel_event=cancel_event,
        _benchmark_fail_fast=True,
    )


def _public_replay(
    episode_id: str,
    racers: Sequence[_Racer],
    events: Sequence[Mapping[str, object]],
    calls: int,
    tick: int,
    *,
    vision_range_cells: VisionRange,
    map_spec: MazeMapSpec,
    participant_call_budget: int,
    maximum_ticks: int,
) -> dict[str, Any]:
    shortest = map_spec.metrics.shortest_path_cells
    finished = sorted(
        (value for value in racers if value.finished_tick is not None),
        key=lambda value: (value.finished_tick, value.entrant.participant_id),
    )
    placements = {value.entrant.participant_id: index + 1 for index, value in enumerate(finished)}
    public_racers = []
    for racer in racers:
        public_racers.append(
            {
                **racer.entrant.public_dict(),
                "place": placements.get(racer.entrant.participant_id),
                "finish_tick": racer.finished_tick,
                "finished": racer.finished_tick is not None,
                "distance_cells": racer.distance_cells,
                "shortest_path_cells": shortest,
                "path_efficiency_basis_points": 0
                if racer.distance_cells == 0
                else shortest * 10_000 // racer.distance_cells,
                "unique_corridor_cells": len(set(racer.visits)),
                "repeated_corridor_cells": len(racer.visits) - len(set(racer.visits)),
                "invalid_decisions": racer.invalid_decisions,
                "waiting_windows": racer.waiting_windows,
                "provider_calls": racer.provider_calls,
                "final_cell": list(racer.position),
                # Spatial replay is intentionally public; controller reasoning is not.  Keeping this
                # complete physical track allows a verifier to reproduce every distance metric.
                "path": [list(cell) for cell in racer.visits],
            }
        )
    result = {
        "completion_tick": max((value.finished_tick or 0 for value in racers), default=0),
        "finish_order": [value.entrant.participant_id for value in finished],
        "reason": "all_racers_finished" if len(finished) == 3 else "decision_budget_or_tick_limit",
        "winner_id": finished[0].entrant.participant_id if finished else None,
    }
    body: dict[str, Any] = {
        "schema_version": LIVE_REPLAY_SCHEMA,
        "task_id": LIVE_TASK_ID,
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": episode_id,
        "authority_hz": 10,
        "maximum_ticks": maximum_ticks,
        "elapsed_ticks": tick,
        "provider_calls": calls,
        "participant_call_budget": participant_call_budget,
        "provider_calls_by_participant": {
            value.entrant.participant_id: value.provider_calls for value in racers
        },
        "vision": {
            "range_cells": vision_range_cells,
            "occlusion": "straight_line_walls",
        },
        "map": map_spec.as_dict(),
        "racers": public_racers,
        "events": sorted(
            (dict(value) for value in events),
            key=lambda value: (
                int(value["tick"]),
                str(value["participant_id"]),
                str(value["kind"]),
            ),
        ),
        "result": result,
    }
    body["final_state_sha256"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return body


def public_live_evaluation(replay: Mapping[str, Any]) -> Mapping[str, Any]:
    verify_live_replay(replay)
    racers = sorted(
        replay["racers"],
        key=lambda value: (value["place"] is None, value["place"] or 99, value["participant_id"]),
    )
    winner = replay["result"]["winner_id"]
    summary = "No racer finished"
    if winner is not None:
        winner_name = next(
            value["display_name"] for value in racers if value["participant_id"] == winner
        )
        summary = f"{winner_name} won the verified live maze race."
    return {
        "task_id": LIVE_TASK_ID,
        "scope": "trio_maze_race_live",
        "summary": summary,
        "participants": [
            {
                key: value[key]
                for key in (
                    "participant_id",
                    "display_name",
                    "model",
                    "color",
                    "place",
                    "finish_tick",
                    "finished",
                    "distance_cells",
                    "shortest_path_cells",
                    "path_efficiency_basis_points",
                    "unique_corridor_cells",
                    "repeated_corridor_cells",
                    "invalid_decisions",
                    "waiting_windows",
                    "provider_calls",
                )
            }
            for value in racers
        ],
        "verification": {
            "state": "verified",
            "deterministic": True,
            "map_sha256": replay["map"]["map_sha256"],
            "final_state_sha256": replay["final_state_sha256"],
        },
    }


def verify_live_replay(replay: Mapping[str, Any]) -> None:
    """Verify public spatial state and reject any protected material in replay evidence."""
    if (
        not isinstance(replay, Mapping)
        or replay.get("schema_version") != LIVE_REPLAY_SCHEMA
        or replay.get("task_id") != LIVE_TASK_ID
    ):
        raise LiveLabyrinthError("live labyrinth replay identity is invalid")
    _reject_protected(replay)
    if set(replay) != _LIVE_REPLAY_FIELDS:
        raise LiveLabyrinthError("live labyrinth replay fields are invalid")
    if (
        replay.get("protocol_version") != PROTOCOL_VERSION
        or replay.get("authority_hz") != 10
        or not isinstance(replay.get("episode_id"), str)
        or not replay["episode_id"].startswith("ep_")
    ):
        raise LiveLabyrinthError("live labyrinth replay rules differ")
    try:
        map_spec = MazeMapSpec.from_dict(replay.get("map"))
    except (MazeMapError, TypeError) as error:
        raise LiveLabyrinthError("live labyrinth map is invalid") from error
    vision = replay.get("vision")
    if (
        not isinstance(vision, Mapping)
        or set(vision) != {"range_cells", "occlusion"}
        or vision.get("occlusion") != "straight_line_walls"
    ):
        raise LiveLabyrinthError("live labyrinth vision rules differ")
    try:
        normalize_vision_range(vision.get("range_cells"))
    except ValueError as error:
        raise LiveLabyrinthError("live labyrinth vision range is invalid") from error
    body = dict(replay)
    final_hash = body.pop("final_state_sha256", None)
    if (
        not isinstance(final_hash, str)
        or hashlib.sha256(canonical_json_bytes(body)).hexdigest() != final_hash
    ):
        raise LiveLabyrinthError("live labyrinth replay hash differs")
    participant_budget = replay.get("participant_call_budget")
    maximum_ticks = replay.get("maximum_ticks")
    if (
        isinstance(participant_budget, bool)
        or not isinstance(participant_budget, int)
        or not 1 <= participant_budget <= map_spec.participant_call_budget
        or maximum_ticks != 4 * participant_budget
        or isinstance(replay.get("elapsed_ticks"), bool)
        or not isinstance(replay.get("elapsed_ticks"), int)
        or not 0 <= replay["elapsed_ticks"] <= maximum_ticks
    ):
        raise LiveLabyrinthError("live labyrinth dynamic budget differs")
    racers = replay.get("racers")
    if (
        not isinstance(racers, list)
        or len(racers) != len(PARTICIPANTS)
        or any(not isinstance(value, Mapping) for value in racers)
        or {value.get("participant_id") for value in racers} != set(PARTICIPANTS)
    ):
        raise LiveLabyrinthError("live labyrinth racers differ")
    result = replay.get("result")
    if not isinstance(result, Mapping) or set(result) != {
        "completion_tick",
        "finish_order",
        "reason",
        "winner_id",
    }:
        raise LiveLabyrinthError("live labyrinth result fields are invalid")
    graph = map_spec.graph()
    start, exit_cell = map_spec.start, map_spec.exit
    calls_by_participant = replay.get("provider_calls_by_participant")
    if (
        not isinstance(calls_by_participant, Mapping)
        or set(calls_by_participant) != set(PARTICIPANTS)
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= participant_budget
            for value in calls_by_participant.values()
        )
        or replay.get("provider_calls") != sum(calls_by_participant.values())
    ):
        raise LiveLabyrinthError("live labyrinth provider accounting differs")
    events = replay.get("events")
    if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
        raise LiveLabyrinthError("live labyrinth events are invalid")
    canonical_events = sorted(
        (dict(value) for value in events),
        key=lambda value: (
            value.get("tick", -1)
            if isinstance(value.get("tick"), int)
            and not isinstance(value.get("tick"), bool)
            else -1,
            str(value.get("participant_id", "")),
            str(value.get("kind", "")),
        ),
    )
    if events != canonical_events:
        raise LiveLabyrinthError("live labyrinth events are not canonical")
    events_by_participant: dict[str, list[Mapping[str, object]]] = {
        participant_id: [] for participant_id in PARTICIPANTS
    }
    for event in events:
        participant_id = event.get("participant_id")
        tick = event.get("tick")
        kind = event.get("kind")
        if (
            participant_id not in PARTICIPANTS
            or isinstance(tick, bool)
            or not isinstance(tick, int)
            or not 0 < tick <= maximum_ticks
            or not isinstance(kind, str)
        ):
            raise LiveLabyrinthError("live labyrinth event identity is invalid")
        if kind == "finish":
            if set(event) != {"tick", "participant_id", "kind"}:
                raise LiveLabyrinthError("live labyrinth finish event fields differ")
        else:
            if set(event) != {
                "tick",
                "participant_id",
                "kind",
                "choice",
                "movement_mode",
            }:
                raise LiveLabyrinthError("live labyrinth movement event fields differ")
            if event.get("choice") not in {"left", "forward", "right", "back", "wait"}:
                raise LiveLabyrinthError("live labyrinth event choice is invalid")
            if event.get("movement_mode") not in {"single_cell", "follow_corridor"}:
                raise LiveLabyrinthError("live labyrinth event movement mode is invalid")
            if kind == "move" and event.get("choice") == "wait":
                raise LiveLabyrinthError("live labyrinth move event cannot wait")
            if kind not in {"move", "accepted", "invalid", "provider_failure", "wait"}:
                raise LiveLabyrinthError("live labyrinth event kind is invalid")
        events_by_participant[participant_id].append(event)

    finished: list[tuple[int, str]] = []
    for racer in racers:
        if set(racer) != _LIVE_RACER_FIELDS:
            raise LiveLabyrinthError("live labyrinth racer fields are invalid")
        participant_id = racer.get("participant_id")
        try:
            LiveMazeEntrant(
                participant_id=participant_id,
                entrant_id=racer.get("entrant_id"),
                display_name=racer.get("display_name"),
                provider=racer.get("provider"),
                model=racer.get("model"),
                color=racer.get("color"),
            )
        except (TypeError, ValueError) as error:
            raise LiveLabyrinthError("live labyrinth entrant is invalid") from error
        if racer.get("finished") != (racer.get("finish_tick") is not None):
            raise LiveLabyrinthError("live labyrinth finish state differs")
        if racer.get("provider_calls") != calls_by_participant.get(participant_id):
            raise LiveLabyrinthError("live labyrinth racer call accounting differs")
        for field_name in (
            "distance_cells",
            "shortest_path_cells",
            "path_efficiency_basis_points",
            "unique_corridor_cells",
            "repeated_corridor_cells",
            "invalid_decisions",
            "waiting_windows",
        ):
            value = racer.get(field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LiveLabyrinthError("live labyrinth racer metric is invalid")
        if (
            racer["invalid_decisions"] > racer["provider_calls"]
            or racer["waiting_windows"] > racer["provider_calls"]
        ):
            raise LiveLabyrinthError("live labyrinth decision metrics are invalid")
        path = racer.get("path")
        if (
            not isinstance(path, list)
            or not path
            or any(
                not isinstance(cell, list) or len(cell) != 2 or tuple(cell) not in graph
                for cell in path
            )
        ):
            raise LiveLabyrinthError("live labyrinth path is invalid")
        cells = [tuple(cell) for cell in path]
        if cells[0] != start or any(
            target not in graph[source] for source, target in zip(cells, cells[1:])
        ):
            raise LiveLabyrinthError("live labyrinth path is not contiguous")
        expected_efficiency = (
            0
            if len(cells) == 1
            else map_spec.metrics.shortest_path_cells * 10_000 // (len(cells) - 1)
        )
        if (
            racer.get("final_cell") != list(cells[-1])
            or racer.get("distance_cells") != len(cells) - 1
            or racer.get("unique_corridor_cells") != len(set(cells))
            or racer.get("repeated_corridor_cells") != len(cells) - len(set(cells))
            or racer.get("shortest_path_cells") != map_spec.metrics.shortest_path_cells
            or racer.get("path_efficiency_basis_points") != expected_efficiency
        ):
            raise LiveLabyrinthError("live labyrinth spatial metrics differ")
        participant_events = events_by_participant[participant_id]
        move_events = [value for value in participant_events if value["kind"] == "move"]
        passive_events = [
            value
            for value in participant_events
            if value["kind"] not in {"move", "finish"}
        ]
        finish_events = [value for value in participant_events if value["kind"] == "finish"]
        expected_choices = []
        heading = 0
        for source, target in zip(cells, cells[1:]):
            absolute = HEADINGS.index((target[0] - source[0], target[1] - source[1]))
            expected_choices.append(_relative_name((absolute - heading) % 4))
            heading = absolute
        move_ticks = [int(value["tick"]) for value in move_events]
        if (
            len(move_events) != len(cells) - 1
            or [value["choice"] for value in move_events] != expected_choices
            or any(
                target <= source or (target - source) % TICKS_PER_CELL != 0
                for source, target in zip(move_ticks, move_ticks[1:])
            )
            or len(passive_events) != racer["waiting_windows"]
            or sum(
                value["kind"] in {"invalid", "provider_failure"}
                for value in passive_events
            )
            != racer["invalid_decisions"]
        ):
            raise LiveLabyrinthError("live labyrinth event timeline differs")
        if racer.get("finished"):
            finish_tick = racer.get("finish_tick")
            if (
                cells[-1] != exit_cell
                or isinstance(finish_tick, bool)
                or not isinstance(finish_tick, int)
                or not move_ticks
                or finish_tick != move_ticks[-1]
                or len(finish_events) != 1
                or finish_events[0]["tick"] != finish_tick
            ):
                raise LiveLabyrinthError("live labyrinth finish is invalid")
            finished.append((finish_tick, participant_id))
        elif cells[-1] == exit_cell or finish_events:
            raise LiveLabyrinthError("live labyrinth unrecorded finish")

    finished.sort()
    finish_order = [participant_id for _, participant_id in finished]
    expected_places = {
        participant_id: index + 1 for index, participant_id in enumerate(finish_order)
    }
    if any(
        racer.get("place") != expected_places.get(racer.get("participant_id"))
        for racer in racers
    ):
        raise LiveLabyrinthError("live labyrinth placements differ")
    expected_result = {
        "completion_tick": max((tick for tick, _ in finished), default=0),
        "finish_order": finish_order,
        "reason": "all_racers_finished"
        if len(finished) == len(PARTICIPANTS)
        else "decision_budget_or_tick_limit",
        "winner_id": finish_order[0] if finish_order else None,
    }
    if dict(result) != expected_result:
        raise LiveLabyrinthError("live labyrinth result order differs")
    expected_elapsed = max((int(value["tick"]) for value in events), default=0)
    if replay.get("elapsed_ticks") != expected_elapsed:
        raise LiveLabyrinthError("live labyrinth elapsed timeline differs")


def _reject_protected(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
                compact = re.sub(r"[^a-z0-9]+", "", key.casefold())
                if any(
                    re.sub(r"[^a-z0-9]+", "_", term.casefold()).strip("_") in normalized
                    or re.sub(r"[^a-z0-9]+", "", term.casefold()) in compact
                    for term in _PROTECTED_TERMS
                ):
                    raise LiveLabyrinthError(
                        "protected controller material leaked into public replay"
                    )
            _reject_protected(child)
    elif isinstance(value, list):
        for child in value:
            _reject_protected(child)


@dataclass
class _ServiceRecord:
    task: asyncio.Task[None] | None = None
    state: str = "queued"
    execution: LiveMazeRaceExecution | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    failure: str | None = None
    entrants: tuple[LiveMazeEntrant, ...] = ()
    vision_range_cells: VisionRange = DEFAULT_VISION_RANGE_CELLS
    map_spec: MazeMapSpec = field(default_factory=default_maze_map_spec)
    participant_call_budget: int | None = None
    skill_mode: SkillMode = "none"
    video_state: str = "unavailable"
    video_path: Path | None = None
    video_error: str | None = None


class LiveLabyrinthService:
    """Small async lifecycle suitable for an API route integration.

    It intentionally has no credentials or web framework dependency: the future API layer owns
    session credential resolution and supplies already-built provider adapters.
    """

    def __init__(
        self, *, render_video: Callable[[Mapping[str, Any]], object] | None = None
    ) -> None:
        self._records: dict[str, _ServiceRecord] = {}
        self._lock = asyncio.Lock()
        self._render_video = render_video

    async def create(
        self,
        *,
        entrants: Sequence[LiveMazeEntrant],
        providers: Mapping[str, MazeProvider],
        max_provider_calls: int = MAX_LIVE_PROVIDER_CALLS,
        vision_range_cells: VisionRange = DEFAULT_VISION_RANGE_CELLS,
        map_spec: MazeMapSpec | None = None,
        participant_call_budget: int | None = None,
        skill_mode: SkillMode = "none",
        skill_text: str | None = None,
        cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> Mapping[str, object]:
        episode_id = f"ep_live_labyrinth_{secrets.token_hex(12)}"
        normalized_entrants = tuple(entrants)
        normalized_vision = normalize_vision_range(vision_range_cells)
        normalized_map = default_maze_map_spec() if map_spec is None else map_spec
        if not isinstance(normalized_map, MazeMapSpec):
            raise TypeError("live maze map spec is invalid")
        # Validate composition before creating a lifecycle record or background task.
        compose_labyrinth_system_prompt(skill_mode=skill_mode, skill_text=skill_text)
        requested_budget = (
            normalized_map.participant_call_budget
            if participant_call_budget is None
            else participant_call_budget
        )
        if (
            isinstance(requested_budget, bool)
            or not isinstance(requested_budget, int)
            or not 1 <= requested_budget <= normalized_map.participant_call_budget
        ):
            raise ValueError("live maze participant budget is invalid")
        effective_budget = min(requested_budget, max_provider_calls)
        record = _ServiceRecord(
            entrants=normalized_entrants,
            vision_range_cells=normalized_vision,
            map_spec=normalized_map,
            participant_call_budget=effective_budget,
            skill_mode=skill_mode,
        )
        async with self._lock:
            self._records[episode_id] = record
            record.task = asyncio.create_task(
                self._run(
                    record,
                    episode_id,
                    normalized_entrants,
                    providers,
                    max_provider_calls,
                    normalized_vision,
                    normalized_map,
                    participant_call_budget,
                    skill_mode,
                    skill_text,
                    cleanup,
                )
            )
        return self._status(episode_id, record)

    async def _run(
        self,
        record: _ServiceRecord,
        episode_id: str,
        entrants: Sequence[LiveMazeEntrant],
        providers: Mapping[str, MazeProvider],
        max_provider_calls: int,
        vision_range_cells: VisionRange,
        map_spec: MazeMapSpec,
        participant_call_budget: int | None,
        skill_mode: SkillMode,
        skill_text: str | None,
        cleanup: Callable[[], Awaitable[None]] | None,
    ) -> None:
        record.state = "running"
        try:
            record.execution = await run_live_labyrinth_race(
                episode_id=episode_id,
                entrants=entrants,
                providers=providers,
                max_provider_calls=max_provider_calls,
                vision_range_cells=vision_range_cells,
                map_spec=map_spec,
                participant_call_budget=participant_call_budget,
                skill_mode=skill_mode,
                skill_text=skill_text,
                cancel_event=record.cancel_event,
            )
            provider_failures = [
                decision.provider_failure
                for decision in record.execution.protected_decisions
                if decision.provider_failure is not None
            ]
            if provider_failures and len(provider_failures) == len(
                record.execution.protected_decisions
            ):
                record.state = "failed"
                record.failure = _live_provider_failure_code(provider_failures)
                return
            record.state = "completed"
            if self._render_video is not None:
                record.video_state = "saving"
                asyncio.create_task(
                    self._render(record, episode_id), name=f"live-maze-video-{episode_id}"
                )
        except asyncio.CancelledError:
            record.state = "cancelled"
        except Exception:
            record.state = "failed"
            record.failure = "live_labyrinth_execution_failed"
        finally:
            if cleanup is not None:
                try:
                    await cleanup()
                except Exception:
                    if record.state == "completed":
                        record.state = "failed"
                    if record.failure is None:
                        record.failure = "live_labyrinth_cleanup_failed"

    async def _render(self, record: _ServiceRecord, episode_id: str) -> None:
        if self._render_video is None or record.execution is None:
            return
        try:
            path = await asyncio.to_thread(self._render_video, record.execution.replay)
            path = getattr(path, "video_path", path)
            if not isinstance(path, Path) or not path.is_file():
                raise LiveLabyrinthError("live labyrinth renderer returned no video")
            record.video_path = path
            record.video_state = "ready"
        except Exception:
            record.video_state = "unavailable"
            record.video_error = "live_labyrinth_video_unavailable"

    async def status(self, episode_id: str) -> Mapping[str, object]:
        return self._status(episode_id, await self._record(episode_id))

    async def result(self, episode_id: str) -> Mapping[str, Any]:
        record = await self._record(episode_id)
        if record.execution is None:
            raise LiveLabyrinthNotReadyError("live_labyrinth_result_not_ready")
        return record.execution.replay["result"]

    async def evaluation(self, episode_id: str) -> Mapping[str, Any]:
        record = await self._record(episode_id)
        if record.execution is None:
            raise LiveLabyrinthNotReadyError("live_labyrinth_evaluation_not_ready")
        return record.execution.evaluation

    async def replay(self, episode_id: str) -> Mapping[str, Any]:
        record = await self._record(episode_id)
        if record.execution is None:
            raise LiveLabyrinthNotReadyError("live_labyrinth_replay_not_ready")
        return record.execution.replay

    async def cancel(self, episode_id: str) -> Mapping[str, object]:
        record = await self._record(episode_id)
        record.cancel_event.set()
        return self._status(episode_id, record)

    async def video_path(self, episode_id: str) -> Path | None:
        return (await self._record(episode_id)).video_path

    async def _record(self, episode_id: str) -> _ServiceRecord:
        async with self._lock:
            try:
                return self._records[episode_id]
            except KeyError as error:
                raise LiveLabyrinthNotFoundError(episode_id) from error

    @staticmethod
    def _status(episode_id: str, record: _ServiceRecord) -> Mapping[str, object]:
        return {
            "episode_id": episode_id,
            "task_id": LIVE_TASK_ID,
            "state": record.state,
            "failure": record.failure,
            "entrants": [value.public_dict() for value in record.entrants],
            "vision": {
                "range_cells": record.vision_range_cells,
                "occlusion": "straight_line_walls",
            },
            "map": {
                "map_id": record.map_spec.map_id,
                "difficulty": record.map_spec.difficulty,
                "map_sha256": record.map_spec.map_sha256,
            },
            "participant_call_budget": record.participant_call_budget,
            "skill_mode": record.skill_mode,
            "video": {"state": record.video_state},
        }


def _live_provider_failure_code(failures: Sequence[str]) -> str:
    """Publish only a stable, credential-safe reason for an all-provider outage."""

    kinds = set(failures)
    if kinds == {"credential_error"}:
        return "live_provider_credential_rejected"
    if kinds in ({"rate_limit_error"}, {"quota_error"}):
        return "live_provider_rate_limited"
    if kinds == {"timeout"}:
        return "live_provider_timed_out"
    return "live_provider_unavailable"


__all__ = [
    "BenchmarkMazeRaceAbort",
    "LABYRINTH_PROTOCOL_PROMPT",
    "LIVE_REPLAY_SCHEMA",
    "LIVE_TASK_ID",
    "MAZE_NAVIGATION_SKILL_ID",
    "MAZE_NAVIGATION_SKILL_PATH",
    "MAX_LIVE_PROVIDER_CALLS",
    "DEFAULT_VISION_RANGE_CELLS",
    "MAX_FINITE_VISION_RANGE_CELLS",
    "LiveLabyrinthError",
    "LiveLabyrinthNotFoundError",
    "LiveLabyrinthNotReadyError",
    "LiveLabyrinthService",
    "LiveMazeEntrant",
    "LiveMazeRaceExecution",
    "MazeMemoryTelemetry",
    "MazeNavigationMemory",
    "MazeProvider",
    "ProtectedMazeDecision",
    "SafeMazeDecision",
    "SkillMode",
    "compose_labyrinth_system_prompt",
    "labyrinth_protocol_prompt_sha256",
    "load_maze_navigation_skill",
    "maze_navigation_skill_sha256",
    "maze_visible_cells",
    "normalize_vision_range",
    "public_live_evaluation",
    "run_benchmark_labyrinth_race",
    "run_live_labyrinth_race",
    "validate_maze_skill_text",
    "verify_live_replay",
]
