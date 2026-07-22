"""Provider-neutral, input-responsive three-racer Labyrinth Run authority.

This module deliberately does not alter the cached ``trio-maze-race-v0`` showcase.  A live run
has a fresh ``v1`` identity, asks every racer for a strict :class:`MazeTaskPlan` at every decision
window, and publishes only deterministic spatial evidence.  Provider output and scratchpads are
kept in process-private evidence and never copied into public replay or evaluation projections.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .episode_memory import EpisodeMemory
from .labyrinth_run import (
    HEADINGS,
    LANDMARKS,
    MAXIMUM_TICKS,
    MAZE_ROWS,
    PARTICIPANTS,
    PROTOCOL_VERSION,
    TICKS_PER_CELL,
    _cell_graph,
    _start_exit,
    maze_analysis,
    parse_maze_task_plan,
)
from .protocol import canonical_json_bytes
from .providers.contracts import ProviderAdapter, ProviderCallResult, ProviderRequest
from .scratchpad import EpisodeScratchpad, ScratchpadError

LIVE_TASK_ID = "trio-maze-race-v1"
LIVE_REPLAY_SCHEMA = "worldarena/live-labyrinth-run-replay/1"
MAX_LIVE_PROVIDER_CALLS = 450
_PROVIDER_TIMEOUT_NS = 45 * 1_000_000_000
_SYSTEM_PROMPT = (
    "You control one racer in a private maze lane. Return exactly the MazeTaskPlan JSON object. "
    "Use only the current observation, your private scratchpad, and navigation_memory. "
    "navigation_memory is an authoritative episode-local map derived only from your prior visible "
    "passages and accepted moves. Its cells are [x,y,open_mask,traversed_mask,visits,landmark], "
    "where direction bits are N=1,E=2,S=4,W=8. Navigate systematically: prefer a currently "
    "visible passage listed in navigation_memory.current.untried; otherwise use "
    "navigation_memory.current.backtrack. Do not wait when either is available. Choose only a "
    "currently visible relative passage or wait. scratchpad_update is an optional full-replacement "
    "private note and cannot change navigation_memory."
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
        "scratchpad_update",
    ],
    "properties": {
        "protocol_version": {"const": PROTOCOL_VERSION},
        "episode_id": {"type": "string"},
        "observation_id": {"type": "string"},
        "participant_id": {"type": "string", "enum": list(PARTICIPANTS)},
        "passage_choice": {"enum": ["left", "forward", "right", "back", "wait"]},
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


class LiveLabyrinthError(RuntimeError):
    """A live race, its public evidence, or lifecycle projection is invalid."""


class LiveLabyrinthNotFoundError(KeyError):
    pass


class LiveLabyrinthNotReadyError(RuntimeError):
    pass


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
class LiveMazeRaceExecution:
    replay: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    protected_decisions: tuple[ProtectedMazeDecision, ...] = field(repr=False)


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
    distance_cells: int = 0
    invalid_decisions: int = 0
    waiting_windows: int = 0
    finished_tick: int | None = None
    visits: list[tuple[int, int]] = field(default_factory=list)
    scratchpad: EpisodeScratchpad = field(default_factory=EpisodeScratchpad, repr=False)
    navigation_memory: MazeNavigationMemory = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.navigation_memory = MazeNavigationMemory(self.entrant.participant_id)


@dataclass
class _KnownMazeCell:
    open_mask: int = 0
    traversed_mask: int = 0
    visits: int = 0
    landmark: str = ""


class MazeNavigationMemory:
    """Participant-local map reconstructed only from visible passages and accepted moves."""

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
        self._memory = EpisodeMemory()

    @property
    def utf8(self) -> bytes:
        return self._memory.utf8

    def observe(
        self,
        *,
        observation_seq: int,
        visible_passages: Sequence[str],
        landmark: str,
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
        cell = self._cells.setdefault(self._position, _KnownMazeCell())
        for choice in visible_passages:
            cell.open_mask |= 1 << ((self._heading + self._RELATIVE[choice]) % 4)
        cell.visits += 1
        if landmark != "none":
            cell.landmark = landmark
        value = self._snapshot(observation_seq)
        self._memory.replace(value)
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
            target_cell.open_mask |= 1 << reverse
            target_cell.traversed_mask |= 1 << reverse
            self._position = target
            self._heading = absolute
            if len(self._route) > 1 and target == self._route[-2]:
                self._route.pop()
            elif target in self._route:
                self._route = self._route[: self._route.index(target) + 1]
            else:
                self._route.append(target)
        self._last = (choice, outcome)

    def close(self) -> None:
        self._memory.close()
        self._cells.clear()
        self._route.clear()
        self._last = None

    def _snapshot(self, observation_seq: int) -> dict[str, Any]:
        current = self._cells[self._position]
        untried_mask = current.open_mask & ~current.traversed_mask
        backtrack = None
        if len(self._route) > 1:
            parent = self._route[-2]
            vector = (parent[0] - self._position[0], parent[1] - self._position[1])
            absolute = HEADINGS.index(vector)
            backtrack = _relative_name((absolute - self._heading) % 4)
        return {
            "v": "maze-nav/1",
            "owner": self._participant_id,
            "turn": observation_seq,
            "pose": [self._position[0], self._position[1], "NESW"[self._heading]],
            "last": None if self._last is None else list(self._last),
            "current": {
                "visits": current.visits,
                "untried": self._relative_passages(untried_mask),
                "backtrack": backtrack,
            },
            "cells": [
                [
                    position[0],
                    position[1],
                    cell.open_mask,
                    cell.traversed_mask,
                    cell.visits,
                    cell.landmark,
                ]
                for position, cell in sorted(self._cells.items())
            ],
        }

    def _relative_passages(self, mask: int) -> list[str]:
        return [
            name
            for name in ("forward", "right", "back", "left")
            if mask & (1 << ((self._heading + self._RELATIVE[name]) % 4))
        ]


def _relative_name(relative: int) -> str:
    return ("forward", "right", "back", "left")[relative]


def _relative_target(racer: _Racer, choice: str) -> tuple[tuple[int, int], int] | None:
    if choice == "wait":
        return None
    relative = {"forward": 0, "right": 1, "back": 2, "left": 3}[choice]
    heading = (racer.heading + relative) % 4
    dx, dy = HEADINGS[heading]
    candidate = (racer.position[0] + dx, racer.position[1] + dy)
    return (candidate, heading) if candidate in _cell_graph()[racer.position] else None


def _visible_observation(racer: _Racer, *, episode_id: str, tick: int) -> dict[str, object]:
    graph = _cell_graph()
    passages = []
    for candidate in graph[racer.position]:
        vector = (candidate[0] - racer.position[0], candidate[1] - racer.position[1])
        absolute = HEADINGS.index(vector)
        passages.append(_relative_name((absolute - racer.heading) % 4))
    # A sorted relative list makes the request deterministic and contains no coordinates, map,
    # opponent, or spectator material.
    passages.sort(key=("forward", "right", "back", "left").index)
    observation_id = f"obs_{racer.entrant.participant_id}_{racer.observation_seq:04d}"
    landmark = LANDMARKS.get(racer.position, "none")
    navigation_memory = racer.navigation_memory.observe(
        observation_seq=racer.observation_seq,
        visible_passages=passages,
        landmark=landmark,
    )
    return {
        "episode_id": episode_id,
        "observation_id": observation_id,
        "observation_seq": racer.observation_seq,
        "participant_id": racer.entrant.participant_id,
        "protocol_version": PROTOCOL_VERSION,
        "profile": "maze-relative-passages-v1",
        "tick": tick,
        "visible_passages": passages,
        "landmark": landmark,
        "at_exit": racer.position == _start_exit()[1],
        "navigation_memory": navigation_memory,
    }


async def run_live_labyrinth_race(
    *,
    episode_id: str,
    entrants: Sequence[LiveMazeEntrant],
    providers: Mapping[str, MazeProvider],
    max_provider_calls: int = MAX_LIVE_PROVIDER_CALLS,
    cancel_event: asyncio.Event | None = None,
) -> LiveMazeRaceExecution:
    """Run a simultaneous, input-responsive race with exactly three isolated controllers.

    Calls for a window are gathered concurrently.  An invalid, stale, failed, or illegal response
    becomes a deterministic wait for that racer only; it never exposes another racer's state.
    """

    if not isinstance(episode_id, str) or not episode_id.startswith("ep_"):
        raise ValueError("live maze episode id is invalid")
    if len(entrants) != 3 or {value.participant_id for value in entrants} != set(PARTICIPANTS):
        raise ValueError("live maze needs exactly the three fixed participant seats")
    if (
        isinstance(max_provider_calls, bool)
        or not 1 <= max_provider_calls <= MAX_LIVE_PROVIDER_CALLS
    ):
        raise ValueError("live maze provider budget is invalid")
    if set(providers) != set(PARTICIPANTS) or any(
        not isinstance(value, ProviderAdapter) for value in providers.values()
    ):
        raise ValueError("live maze requires one provider adapter per participant")
    if any(providers[value.participant_id].provider_name != value.provider for value in entrants):
        raise ValueError("live maze entrant provider identity differs")
    start, exit_cell = _start_exit()
    racers = [
        _Racer(value, providers[value.participant_id], start, visits=[start]) for value in entrants
    ]
    racers.sort(key=lambda value: value.entrant.participant_id)
    protected: list[ProtectedMazeDecision] = []
    events: list[dict[str, object]] = []
    tick = 0
    calls = 0
    try:
        while tick < MAXIMUM_TICKS and calls < max_provider_calls:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            active = [value for value in racers if value.finished_tick is None]
            if not active:
                break
            if calls + len(active) > max_provider_calls:
                break
            observations = {
                value.entrant.participant_id: _visible_observation(
                    value, episode_id=episode_id, tick=tick
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
                    system_prompt=_SYSTEM_PROMPT,
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
            calls += len(active)
            for racer, result in zip(active, results):
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
                disposition = "wait"
                if raw is not None:
                    try:
                        plan = parse_maze_task_plan(
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
                        disposition = "accepted"
                    except (ValueError, ScratchpadError):
                        racer.invalid_decisions += 1
                        disposition = "invalid"
                elif failure is not None:
                    racer.invalid_decisions += 1
                    disposition = "provider_failure"
                target = _relative_target(racer, choice)
                if choice != "wait" and target is None:
                    racer.invalid_decisions += 1
                    disposition = "invalid"
                    choice = "wait"
                memory_outcome = (
                    "moved"
                    if target is not None
                    else "provider_failure"
                    if disposition == "provider_failure"
                    else "waited"
                    if disposition in {"accepted", "wait"} and submitted_choice == "wait"
                    else "invalid"
                )
                racer.navigation_memory.apply_transition(submitted_choice, memory_outcome)
                if target is None:
                    racer.waiting_windows += 1
                    events.append(
                        {
                            "tick": tick + TICKS_PER_CELL,
                            "participant_id": racer.entrant.participant_id,
                            "kind": disposition,
                            "choice": choice,
                        }
                    )
                else:
                    racer.position, racer.heading = target
                    racer.distance_cells += 1
                    racer.visits.append(racer.position)
                    events.append(
                        {
                            "tick": tick + TICKS_PER_CELL,
                            "participant_id": racer.entrant.participant_id,
                            "kind": "move",
                            "choice": choice,
                        }
                    )
                    if racer.position == exit_cell:
                        racer.finished_tick = tick + TICKS_PER_CELL
                        events.append(
                            {
                                "tick": tick + TICKS_PER_CELL,
                                "participant_id": racer.entrant.participant_id,
                                "kind": "finish",
                            }
                        )
                racer.observation_seq += 1
            tick += TICKS_PER_CELL
    finally:
        # The objects held in `protected` retain snapshots for internal evidence only; every live
        # mutable controller scratchpad and navigation memory is erased at the episode boundary.
        for racer in racers:
            racer.scratchpad.close()
            racer.navigation_memory.close()
    replay = _public_replay(episode_id, racers, events, calls, tick)
    verify_live_replay(replay)
    return LiveMazeRaceExecution(replay, public_live_evaluation(replay), tuple(protected))


def _public_replay(
    episode_id: str,
    racers: Sequence[_Racer],
    events: Sequence[Mapping[str, object]],
    calls: int,
    tick: int,
) -> dict[str, Any]:
    shortest = int(maze_analysis()["shortest_path_cells"])
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
        "maximum_ticks": MAXIMUM_TICKS,
        "elapsed_ticks": tick,
        "provider_calls": calls,
        "map": {"rows": list(MAZE_ROWS), **maze_analysis()},
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
    if replay.get("protocol_version") != PROTOCOL_VERSION or replay.get("map", {}).get(
        "rows"
    ) != list(MAZE_ROWS):
        raise LiveLabyrinthError("live labyrinth replay rules differ")
    body = dict(replay)
    final_hash = body.pop("final_state_sha256", None)
    if (
        not isinstance(final_hash, str)
        or hashlib.sha256(canonical_json_bytes(body)).hexdigest() != final_hash
    ):
        raise LiveLabyrinthError("live labyrinth replay hash differs")
    _reject_protected(replay)
    racers = replay.get("racers")
    if not isinstance(racers, list) or {
        value.get("participant_id") for value in racers if isinstance(value, Mapping)
    } != set(PARTICIPANTS):
        raise LiveLabyrinthError("live labyrinth racers differ")
    finish_order = replay.get("result", {}).get("finish_order")
    if not isinstance(finish_order, list) or len(set(finish_order)) != len(finish_order):
        raise LiveLabyrinthError("live labyrinth finish order is invalid")
    expected_places = {
        participant_id: index + 1 for index, participant_id in enumerate(finish_order)
    }
    graph = _cell_graph()
    start, exit_cell = _start_exit()
    finished = []
    for racer in racers:
        if not isinstance(racer, Mapping):
            raise LiveLabyrinthError("live labyrinth racer is invalid")
        participant_id = racer.get("participant_id")
        if racer.get("place") != expected_places.get(participant_id):
            raise LiveLabyrinthError("live labyrinth placements differ")
        if racer.get("finished") != (racer.get("finish_tick") is not None):
            raise LiveLabyrinthError("live labyrinth finish state differs")
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
        if (
            racer.get("final_cell") != list(cells[-1])
            or racer.get("distance_cells") != len(cells) - 1
            or racer.get("unique_corridor_cells") != len(set(cells))
            or racer.get("repeated_corridor_cells") != len(cells) - len(set(cells))
        ):
            raise LiveLabyrinthError("live labyrinth spatial metrics differ")
        if racer.get("finished"):
            if cells[-1] != exit_cell or not isinstance(racer.get("finish_tick"), int):
                raise LiveLabyrinthError("live labyrinth finish is invalid")
            finished.append((racer["finish_tick"], participant_id))
        elif cells[-1] == exit_cell:
            raise LiveLabyrinthError("live labyrinth unrecorded finish")
    if [participant_id for _, participant_id in sorted(finished)] != finish_order:
        raise LiveLabyrinthError("live labyrinth result order differs")


def _reject_protected(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _PROTECTED_TERMS:
                raise LiveLabyrinthError("protected controller material leaked into public replay")
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
        cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> Mapping[str, object]:
        episode_id = f"ep_live_labyrinth_{secrets.token_hex(12)}"
        normalized_entrants = tuple(entrants)
        record = _ServiceRecord(entrants=normalized_entrants)
        async with self._lock:
            self._records[episode_id] = record
            record.task = asyncio.create_task(
                self._run(
                    record,
                    episode_id,
                    normalized_entrants,
                    providers,
                    max_provider_calls,
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
        cleanup: Callable[[], Awaitable[None]] | None,
    ) -> None:
        record.state = "running"
        try:
            record.execution = await run_live_labyrinth_race(
                episode_id=episode_id,
                entrants=entrants,
                providers=providers,
                max_provider_calls=max_provider_calls,
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
    "LIVE_REPLAY_SCHEMA",
    "LIVE_TASK_ID",
    "MAX_LIVE_PROVIDER_CALLS",
    "LiveLabyrinthError",
    "LiveLabyrinthNotFoundError",
    "LiveLabyrinthNotReadyError",
    "LiveLabyrinthService",
    "LiveMazeEntrant",
    "LiveMazeRaceExecution",
    "MazeNavigationMemory",
    "MazeProvider",
    "ProtectedMazeDecision",
    "public_live_evaluation",
    "run_live_labyrinth_race",
    "verify_live_replay",
]
