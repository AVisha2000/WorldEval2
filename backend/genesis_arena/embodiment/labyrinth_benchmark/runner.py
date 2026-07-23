"""Sequential, resumable execution for frozen Labyrinth benchmark schedules."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Protocol

from ..live_labyrinth import (
    BenchmarkMazeRaceAbort,
    LiveMazeEntrant,
    LiveMazeRaceExecution,
    labyrinth_protocol_prompt_sha256,
    load_maze_navigation_skill,
    maze_navigation_skill_sha256,
    run_benchmark_labyrinth_race,
)
from ..maze_maps import MazeMapSpec
from ..protocol import canonical_json_bytes
from ..providers.contracts import ProviderFailureKind
from ..providers.openai_adapter import OpenAIProviderAdapter
from .artifacts import BenchmarkArtifactError, BenchmarkArtifactStore, atomic_write_json
from .spec import (
    MAZE_NAVIGATION_SKILL_ID,
    PARTICIPANTS,
    RESULT_VERSION,
    BenchmarkSchedule,
    LabyrinthBenchmarkSpec,
    MazeSuiteManifest,
    ScheduledRace,
    build_schedule,
)

_FATAL_FAILURES = {
    ProviderFailureKind.CREDENTIAL.value,
    ProviderFailureKind.QUOTA.value,
}
_VOIDABLE_FAILURES = {
    ProviderFailureKind.RATE_LIMIT.value,
    ProviderFailureKind.TRANSPORT.value,
}


class BenchmarkRunError(RuntimeError):
    """A benchmark season stopped safely and remains resumable."""


class BenchmarkExecutor(Protocol):
    async def execute(
        self, race: ScheduledRace, maze: MazeMapSpec, *, attempt: int
    ) -> Mapping[str, object]:
        """Return one validated, public-safe race result."""


@dataclass(frozen=True)
class PhaseRunSummary:
    phase: str
    scheduled: int
    resumed: int
    executed: int
    infrastructure_voids: int


def acknowledge_terminal_stop(store: BenchmarkArtifactStore) -> Mapping[str, object]:
    """Explicitly reopen a stopped season after an operator resolves its external cause."""

    state = dict(store.load_state())
    if state.get("status") != "stopped" or state.get("last_error") not in {
        "benchmark_provider_credential_or_quota_failure",
        "benchmark_repeated_infrastructure_outage",
        "benchmark_retry_was_already_started",
        "benchmark_execution_failed",
    }:
        raise BenchmarkRunError("benchmark_state_has_no_acknowledgeable_stop")
    state.update(
        {
            "status": "generated",
            "active_phase": None,
            "pending_retry": None,
            "last_error": None,
        }
    )
    store.write_state(state)
    return state


class OpenAILabyrinthBenchmarkExecutor:
    """Process-local OpenAI executor; its credential is never represented in artifacts."""

    def __init__(
        self,
        api_key: str,
        spec: LabyrinthBenchmarkSpec,
        *,
        public_observer: Callable[[ScheduledRace, Mapping[str, object]], None] | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise BenchmarkRunError("OPENAI_API_KEY is required")
        if not isinstance(spec, LabyrinthBenchmarkSpec):
            raise TypeError("benchmark spec is required")
        skill_text = load_maze_navigation_skill()
        if spec.protocol_prompt_sha256 != labyrinth_protocol_prompt_sha256() or (
            spec.skill_sha256 != maze_navigation_skill_sha256(skill_text)
        ):
            raise BenchmarkRunError("benchmark prompt or skill hash differs from frozen spec")
        self._spec = spec
        self._models = {item.model_id: item for item in spec.models}
        self._skill_text = skill_text
        self._providers = {
            participant_id: OpenAIProviderAdapter(api_key=api_key, service_tier=spec.service_tier)
            for participant_id in PARTICIPANTS
        }
        self._public_observer = public_observer
        self._closed = False

    def _verify_frozen_inputs(self) -> None:
        prompt_differs = self._spec.protocol_prompt_sha256 != labyrinth_protocol_prompt_sha256()
        skill_digests = {
            maze_navigation_skill_sha256(self._skill_text),
            maze_navigation_skill_sha256(),
        }
        skill_differs = skill_digests != {self._spec.skill_sha256}
        if prompt_differs or skill_differs:
            raise BenchmarkRunError("benchmark prompt or skill hash differs from frozen spec")

    async def execute(
        self, race: ScheduledRace, maze: MazeMapSpec, *, attempt: int
    ) -> Mapping[str, object]:
        if self._closed:
            raise BenchmarkRunError("benchmark executor is closed")
        self._verify_frozen_inputs()
        entrants = tuple(
            LiveMazeEntrant(
                participant_id=participant_id,
                entrant_id=race.seats[index],
                display_name=self._models[race.seats[index]].display_name,
                provider="openai",
                model=self._models[race.seats[index]].provider_model,
                color=self._models[race.seats[index]].color,
            )
            for index, participant_id in enumerate(PARTICIPANTS)
        )
        episode_id = opaque_episode_id(self._spec, race, attempt=attempt)
        started = time.monotonic_ns()
        try:
            execution = await run_benchmark_labyrinth_race(
                episode_id=episode_id,
                entrants=entrants,
                providers=self._providers,
                map_spec=maze,
                participant_call_budget=race.participant_call_budget,
                vision_range_cells=race.vision_depth,
                skill_text=(
                    self._skill_text if race.skill_mode == MAZE_NAVIGATION_SKILL_ID else None
                ),
                public_observer=(
                    None
                    if self._public_observer is None
                    else lambda snapshot: self._public_observer(race, snapshot)
                ),
            )
            wall_time_ms = max(0, time.monotonic_ns() - started) // 1_000_000
            return safe_race_result(
                race,
                execution,
                season_id="",  # Runner binds the season before persistence.
                schedule_sha256="",
                wall_time_ms=wall_time_ms,
                attempt=attempt,
            )
        finally:
            # Audit requests contain protected observations and are never durable benchmark data.
            for provider in self._providers.values():
                provider.audit_log.drain_episode(episode_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(provider.aclose() for provider in self._providers.values()))


def safe_race_result(
    race: ScheduledRace,
    execution: LiveMazeRaceExecution,
    *,
    season_id: str,
    schedule_sha256: str,
    wall_time_ms: int,
    attempt: int,
) -> dict[str, object]:
    """Project live evidence through a strict allow-list; never inspect protected decisions."""

    replay = execution.replay
    racers = {
        value["participant_id"]: value
        for value in replay.get("racers", ())
        if isinstance(value, Mapping) and value.get("participant_id") in PARTICIPANTS
    }
    if set(racers) != set(PARTICIPANTS):
        raise BenchmarkRunError("benchmark replay racers are invalid")
    safe_by_participant = {
        participant_id: [
            decision
            for decision in execution.safe_decisions
            if decision.participant_id == participant_id
        ]
        for participant_id in PARTICIPANTS
    }
    memory = {item.participant_id: item for item in execution.memory_telemetry}
    if set(memory) != set(PARTICIPANTS):
        raise BenchmarkRunError("benchmark memory telemetry is incomplete")
    episodes = []
    for seat_index, participant_id in enumerate(PARTICIPANTS):
        racer = racers[participant_id]
        decisions = safe_by_participant[participant_id]
        failures = Counter(
            item.provider_failure for item in decisions if item.provider_failure is not None
        )
        calls = len(decisions)
        completed = racer.get("finished") is True
        input_tokens = sum(
            item.telemetry.input_tokens or 0 for item in decisions if item.telemetry is not None
        )
        output_tokens = sum(
            item.telemetry.output_tokens or 0 for item in decisions if item.telemetry is not None
        )
        cached_input_tokens = sum(
            item.telemetry.cached_input_tokens or 0
            for item in decisions
            if item.telemetry is not None
        )
        cache_write_tokens = sum(
            item.telemetry.cache_write_tokens or 0
            for item in decisions
            if item.telemetry is not None
        )
        latency_ms = sum(
            item.telemetry.latency_ms for item in decisions if item.telemetry is not None
        )
        tokens_complete = bool(decisions) and all(
            item.telemetry is not None
            and item.telemetry.input_tokens is not None
            and item.telemetry.output_tokens is not None
            for item in decisions
        )
        cache_write_telemetry_complete = bool(decisions) and all(
            item.telemetry is not None and item.telemetry.cache_write_tokens is not None
            for item in decisions
        )
        latency_telemetry_complete = bool(decisions) and all(
            item.telemetry is not None for item in decisions
        )
        corridor_commands = sum(item.movement_mode == "follow_corridor" for item in decisions)
        single_cell_commands = sum(item.movement_mode == "single_cell" for item in decisions)
        cells_moved = sum(item.cells_moved for item in decisions)
        recovery_opportunities = sum(
            item.disposition in {"invalid", "provider_failure"} for item in decisions[:-1]
        )
        successful_recoveries = sum(
            previous.disposition in {"invalid", "provider_failure"}
            and current.disposition == "accepted"
            and current.cells_moved > 0
            for previous, current in zip(decisions, decisions[1:])
        )
        backtrack_commands = sum(item.passage_choice == "back" for item in decisions)
        successful_backtracks = sum(
            item.passage_choice == "back" and item.cells_moved > 0 for item in decisions
        )
        invalid_decisions = int(racer.get("invalid_decisions", 0))
        waiting_windows = int(racer.get("waiting_windows", 0))
        repeated_cells = int(racer.get("repeated_corridor_cells", 0))
        distance_cells = int(racer.get("distance_cells", 0))
        model_id = race.seats[seat_index]
        episodes.append(
            {
                "participant_id": participant_id,
                "model_id": model_id,
                "provider_model": str(racer.get("model", "")),
                "completed": completed,
                "finish_tick": racer.get("finish_tick"),
                "calls": calls,
                "charged_calls": calls if completed else race.participant_call_budget,
                "distance_cells": distance_cells,
                "path": [list(cell) for cell in racer.get("path", ())],
                "shortest_path_cells": int(racer.get("shortest_path_cells", 0)),
                "path_efficiency_basis_points": (
                    0
                    if not completed or distance_cells == 0
                    else int(racer.get("shortest_path_cells", 0)) * 10_000 // distance_cells
                ),
                "unique_corridor_cells": int(racer.get("unique_corridor_cells", 0)),
                "repeated_corridor_cells": repeated_cells,
                "repeated_cell_basis_points": (
                    0 if distance_cells == 0 else repeated_cells * 10_000 // distance_cells
                ),
                "invalid_decisions": invalid_decisions,
                "invalid_decision_basis_points": (
                    0 if calls == 0 else invalid_decisions * 10_000 // calls
                ),
                "waiting_windows": waiting_windows,
                "wait_basis_points": (0 if calls == 0 else waiting_windows * 10_000 // calls),
                "corridor_commands": corridor_commands,
                "single_cell_commands": single_cell_commands,
                "corridor_command_basis_points": (
                    0 if calls == 0 else corridor_commands * 10_000 // calls
                ),
                "cells_moved": cells_moved,
                "cells_per_call_milli": 0 if calls == 0 else cells_moved * 1_000 // calls,
                "backtrack_commands": backtrack_commands,
                "successful_backtracks": successful_backtracks,
                "backtrack_success_basis_points": (
                    0
                    if backtrack_commands == 0
                    else successful_backtracks * 10_000 // backtrack_commands
                ),
                "recovery_opportunities": recovery_opportunities,
                "successful_recoveries": successful_recoveries,
                "recovery_basis_points": (
                    0
                    if recovery_opportunities == 0
                    else successful_recoveries * 10_000 // recovery_opportunities
                ),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_write_tokens": cache_write_tokens,
                "total_tokens": input_tokens + output_tokens,
                "token_telemetry_complete": tokens_complete,
                "cache_write_telemetry_complete": cache_write_telemetry_complete,
                "latency_ms": latency_ms,
                "latency_telemetry_complete": latency_telemetry_complete,
                "peak_memory_bytes": memory[participant_id].peak_bytes,
                "memory_evictions": memory[participant_id].evictions,
                "provider_failures": dict(sorted(failures.items())),
                "decision_trace": [
                    {
                        "observation_seq": item.observation_seq,
                        "disposition": item.disposition,
                        "passage_choice": item.passage_choice,
                        "movement_mode": item.movement_mode,
                        "max_corridor_cells": item.max_corridor_cells,
                        "cells_moved": item.cells_moved,
                        "stopped_because": item.stopped_because,
                        "provider_failure": item.provider_failure,
                    }
                    for item in decisions
                ],
            }
        )
    return {
        "schema_version": RESULT_VERSION,
        "season_id": season_id,
        "schedule_sha256": schedule_sha256,
        "race_id": race.race_id,
        "phase": race.phase,
        "map_id": race.map_id,
        "map_sha256": race.map_sha256,
        "difficulty": race.difficulty,
        "vision_depth": race.vision_depth,
        "repetition": race.repetition,
        "skill_mode": race.skill_mode,
        "participant_call_budget": race.participant_call_budget,
        "wall_time_ms": wall_time_ms,
        "attempt": attempt,
        "episodes": episodes,
    }


def opaque_episode_id(spec: LabyrinthBenchmarkSpec, race: ScheduledRace, *, attempt: int) -> str:
    """Bind a call to its frozen cell without exposing map or condition labels to the model."""

    if attempt not in {1, 2}:
        raise ValueError("benchmark attempt is invalid")
    opaque_material = canonical_json_bytes([spec.spec_sha256, race.race_id, attempt])
    return f"ep_bench_{hashlib.sha256(opaque_material).hexdigest()[:32]}"


def _bind_executor_result(
    result: Mapping[str, object], *, season_id: str, schedule: BenchmarkSchedule
) -> dict[str, object]:
    output = dict(result)
    output["season_id"] = season_id
    output["schedule_sha256"] = schedule.schedule_sha256
    return output


def _result_failure_state(result: Mapping[str, object]) -> str:
    episodes = result.get("episodes")
    if not isinstance(episodes, list):
        raise BenchmarkRunError("benchmark executor result is invalid")
    participant_failures = []
    all_failures = set()
    for episode in episodes:
        if not isinstance(episode, Mapping):
            raise BenchmarkRunError("benchmark executor episode is invalid")
        failures = episode.get("provider_failures")
        trace = episode.get("decision_trace")
        if not isinstance(failures, Mapping) or not isinstance(trace, list):
            raise BenchmarkRunError("benchmark executor failure evidence is invalid")
        all_failures.update(failures)
        participant_failures.append(
            bool(trace)
            and all(
                isinstance(item, Mapping)
                and item.get("disposition") == "provider_failure"
                and item.get("provider_failure") in _VOIDABLE_FAILURES
                for item in trace
            )
        )
    if all_failures & _FATAL_FAILURES:
        return "fatal"
    if len(participant_failures) == 3 and all(participant_failures):
        return "infrastructure_void"
    return "model_outcome"


async def run_schedule(
    store: BenchmarkArtifactStore,
    schedule: BenchmarkSchedule,
    maps: MazeSuiteManifest,
    executor: BenchmarkExecutor,
    *,
    retry_delay_seconds: int = 60,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    progress: Callable[[int, int, str], None] | None = None,
) -> PhaseRunSummary:
    """Run races sequentially, atomically checkpointing every valid result."""

    spec = store.load_specification()
    if (
        schedule.season_id != store.season_id
        or schedule.spec_sha256 != spec.spec_sha256
        or schedule.map_manifest_sha256 != maps.manifest_sha256
    ):
        raise BenchmarkRunError("benchmark phase bindings differ")
    map_lookup = {item.map_id: item for item in maps.maps}
    validated_existing = store.iter_results()
    completed_count = len(validated_existing)
    existing_phase_races = {
        str(result["race_id"])
        for result in validated_existing
        if result.get("phase") == schedule.phase
    }
    resumed = 0
    executed = 0
    voids = 0
    state = dict(store.load_state())
    if state.get("status") == "stopped" and state.get("last_error") in {
        "benchmark_provider_credential_or_quota_failure",
        "benchmark_repeated_infrastructure_outage",
        "benchmark_retry_was_already_started",
    }:
        raise BenchmarkRunError(str(state["last_error"]))
    voids_by_phase = {
        phase: int(
            state.get("infrastructure_voids_by_phase", {}).get(phase, 0)
            if isinstance(state.get("infrastructure_voids_by_phase"), Mapping)
            else 0
        )
        for phase in ("pilot", "baseline", "skill")
    }
    recorded_voids = sum(voids_by_phase.values())
    pending_retry = state.get("pending_retry")
    if isinstance(pending_retry, Mapping):
        pending_race_id = str(pending_retry["race_id"])
        if pending_race_id in existing_phase_races:
            state["pending_retry"] = None
            pending_retry = None
        elif pending_retry.get("phase") != schedule.phase or pending_race_id not in {
            race.race_id for race in schedule.races
        }:
            raise BenchmarkRunError("benchmark_pending_retry_phase_differs")
    state.update({"status": "running", "active_phase": schedule.phase, "last_error": None})
    store.write_state(state)
    try:
        for position, race in enumerate(schedule.races, start=1):
            if race.race_id in existing_phase_races:
                resumed += 1
                if progress is not None:
                    progress(position, len(schedule.races), race.race_id)
                continue
            maze = map_lookup.get(race.map_id)
            if maze is None or maze.map_sha256 != race.map_sha256:
                raise BenchmarkRunError("scheduled maze is unavailable")
            pending_retry = state.get("pending_retry")
            if isinstance(pending_retry, Mapping) and pending_retry.get("race_id") == race.race_id:
                if pending_retry.get("retry_started") is True:
                    state.update(
                        {
                            "status": "stopped",
                            "active_phase": schedule.phase,
                            "pending_retry": None,
                            "last_error": "benchmark_retry_was_already_started",
                        }
                    )
                    store.write_state(state)
                    raise BenchmarkRunError("benchmark_retry_was_already_started")
                await sleep(retry_delay_seconds)
                state["pending_retry"] = {
                    "phase": schedule.phase,
                    "race_id": race.race_id,
                    "retry_started": True,
                }
                store.write_state(state)
                attempts = (2,)
            else:
                attempts = (1, 2)
            for attempt in attempts:
                try:
                    raw_result = await executor.execute(race, maze, attempt=attempt)
                except BenchmarkMazeRaceAbort as abort:
                    if abort.kind == "fatal_provider":
                        raise BenchmarkRunError(
                            "benchmark_provider_credential_or_quota_failure"
                        ) from abort
                    voids += 1
                    voids_by_phase[schedule.phase] += 1
                    recorded_voids = sum(voids_by_phase.values())
                    state.update(
                        {
                            "infrastructure_voids": recorded_voids,
                            "infrastructure_voids_by_phase": voids_by_phase,
                            "pending_retry": {
                                "phase": schedule.phase,
                                "race_id": race.race_id,
                                "retry_started": False,
                            }
                            if attempt == 1
                            else None,
                        }
                    )
                    store.write_state(state)
                    if attempt == 1:
                        await sleep(retry_delay_seconds)
                        state["pending_retry"] = {
                            "phase": schedule.phase,
                            "race_id": race.race_id,
                            "retry_started": True,
                        }
                        store.write_state(state)
                        continue
                    raise BenchmarkRunError("benchmark_repeated_infrastructure_outage") from abort
                result = _bind_executor_result(
                    raw_result, season_id=store.season_id, schedule=schedule
                )
                failure_state = _result_failure_state(result)
                if failure_state == "fatal":
                    raise BenchmarkRunError("benchmark_provider_credential_or_quota_failure")
                if failure_state == "infrastructure_void":
                    voids += 1
                    voids_by_phase[schedule.phase] += 1
                    recorded_voids = sum(voids_by_phase.values())
                    state.update(
                        {
                            "infrastructure_voids": recorded_voids,
                            "infrastructure_voids_by_phase": voids_by_phase,
                            "pending_retry": {
                                "phase": schedule.phase,
                                "race_id": race.race_id,
                                "retry_started": False,
                            }
                            if attempt == 1
                            else None,
                        }
                    )
                    store.write_state(state)
                    if attempt == 1:
                        await sleep(retry_delay_seconds)
                        state["pending_retry"] = {
                            "phase": schedule.phase,
                            "race_id": race.race_id,
                            "retry_started": True,
                        }
                        store.write_state(state)
                        continue
                    raise BenchmarkRunError("benchmark_repeated_infrastructure_outage")
                store.save_result(schedule, result)
                state["pending_retry"] = None
                executed += 1
                completed_count += 1
                existing_phase_races.add(race.race_id)
                break
            state.update(
                {
                    "completed_races": completed_count,
                    "infrastructure_voids": recorded_voids,
                    "infrastructure_voids_by_phase": voids_by_phase,
                }
            )
            store.write_state(state)
            if progress is not None:
                progress(position, len(schedule.races), race.race_id)
        state.update(
            {
                "status": f"{schedule.phase}_complete",
                "active_phase": None,
                "completed_races": completed_count,
                "last_error": None,
            }
        )
        store.write_state(state)
    except Exception as error:
        state.update(
            {
                "status": "stopped",
                "active_phase": schedule.phase,
                "completed_races": completed_count,
                "last_error": (
                    str(error)
                    if isinstance(error, BenchmarkRunError)
                    else "benchmark_execution_failed"
                ),
                "infrastructure_voids": recorded_voids,
                "infrastructure_voids_by_phase": voids_by_phase,
                "pending_retry": None,
            }
        )
        store.write_state(state)
        if isinstance(error, (BenchmarkRunError, BenchmarkArtifactError)):
            raise
        raise BenchmarkRunError("benchmark_execution_failed") from error
    return PhaseRunSummary(schedule.phase, len(schedule.races), resumed, executed, voids)


def validate_pilot_gate(store: BenchmarkArtifactStore) -> Mapping[str, object]:
    schedule = store.load_schedule("pilot")
    results = store.iter_results("pilot")
    if len(results) != len(schedule.races):
        raise BenchmarkRunError("benchmark_pilot_incomplete")
    state = store.load_state()
    by_phase = state.get("infrastructure_voids_by_phase", {})
    voids = int(by_phase.get("pilot", 0)) if isinstance(by_phase, Mapping) else 0
    denominator = len(results) + voids
    failure_basis_points = 0 if denominator == 0 else voids * 10_000 // denominator
    if failure_basis_points >= 200:
        raise BenchmarkRunError("benchmark_pilot_infrastructure_failure_rate")
    store.audit_public_tree()
    return {
        "passed": True,
        "race_count": len(results),
        "infrastructure_voids": voids,
        "infrastructure_failure_basis_points": failure_basis_points,
    }


def build_pilot_projection(store: BenchmarkArtifactStore) -> Mapping[str, object]:
    """Persist a safe numeric baseline+skill projection before automatic continuation."""

    validate_pilot_gate(store)
    results = store.iter_results("pilot")
    pilot_races = len(results)
    remaining_races = 600 + 120
    pilot_calls = sum(int(episode["calls"]) for result in results for episode in result["episodes"])
    pilot_input_tokens = sum(
        int(episode["input_tokens"]) for result in results for episode in result["episodes"]
    )
    pilot_output_tokens = sum(
        int(episode["output_tokens"]) for result in results for episode in result["episodes"]
    )
    pilot_elapsed_ms = sum(int(result["wall_time_ms"]) for result in results)
    episodes = [episode for result in results for episode in result["episodes"]]
    token_telemetry_missing_episodes = sum(
        not bool(episode["token_telemetry_complete"]) for episode in episodes
    )
    token_telemetry_complete = token_telemetry_missing_episodes == 0
    prices = {
        "sol": {
            "input_microusd_per_million_tokens": 5_000_000,
            "cached_input_microusd_per_million_tokens": 500_000,
            "cache_write_microusd_per_million_tokens": 6_250_000,
            "output_microusd_per_million_tokens": 30_000_000,
        },
        "terra": {
            "input_microusd_per_million_tokens": 2_500_000,
            "cached_input_microusd_per_million_tokens": 250_000,
            "cache_write_microusd_per_million_tokens": 3_125_000,
            "output_microusd_per_million_tokens": 15_000_000,
        },
        "luna": {
            "input_microusd_per_million_tokens": 1_000_000,
            "cached_input_microusd_per_million_tokens": 100_000,
            "cache_write_microusd_per_million_tokens": 1_250_000,
            "output_microusd_per_million_tokens": 6_000_000,
        },
    }

    def episode_cost_microusd(episode: Mapping[str, object]) -> int:
        price = prices[str(episode["model_id"])]
        input_tokens = int(episode["input_tokens"])
        cached_tokens = min(input_tokens, int(episode["cached_input_tokens"]))
        uncached_tokens = input_tokens - cached_tokens
        if episode["cache_write_telemetry_complete"]:
            cache_write_tokens = min(uncached_tokens, int(episode["cache_write_tokens"]))
            regular_input_tokens = uncached_tokens - cache_write_tokens
        else:
            # A conservative pricing assumption within each pilot episode; it does not make the
            # pilot-scaled estimate an upper bound for harder remaining maps.
            cache_write_tokens = uncached_tokens
            regular_input_tokens = 0
        numerator = (
            regular_input_tokens * price["input_microusd_per_million_tokens"]
            + cache_write_tokens * price["cache_write_microusd_per_million_tokens"]
            + cached_tokens * price["cached_input_microusd_per_million_tokens"]
            + int(episode["output_tokens"]) * price["output_microusd_per_million_tokens"]
        )
        return (numerator + 999_999) // 1_000_000

    measured_known_pilot_cost_microusd = sum(
        episode_cost_microusd(episode)
        for episode in episodes
        if episode["token_telemetry_complete"]
    )
    cache_write_telemetry_complete = all(
        bool(episode["cache_write_telemetry_complete"])
        for episode in episodes
        if episode["token_telemetry_complete"]
    )
    cache_write_telemetry_missing_episodes = sum(
        bool(episode["token_telemetry_complete"])
        and not bool(episode["cache_write_telemetry_complete"])
        for episode in episodes
    )

    def project(value: int) -> int:
        return round(value * remaining_races / pilot_races)

    body: dict[str, object] = {
        "schema_version": "worldarena/labyrinth-benchmark-pilot-projection/1",
        "season_id": store.season_id,
        "method": "pilot_rate_scaled_to_remaining_races",
        "pilot_races": pilot_races,
        "remaining_races": remaining_races,
        "pilot_calls": pilot_calls,
        "projected_remaining_calls": project(pilot_calls),
        "pilot_input_tokens": pilot_input_tokens,
        "projected_remaining_input_tokens": (
            project(pilot_input_tokens) if token_telemetry_complete else None
        ),
        "pilot_output_tokens": pilot_output_tokens,
        "projected_remaining_output_tokens": (
            project(pilot_output_tokens) if token_telemetry_complete else None
        ),
        "token_telemetry_complete": token_telemetry_complete,
        "token_telemetry_missing_episodes": token_telemetry_missing_episodes,
        "pilot_elapsed_ms": pilot_elapsed_ms,
        "projected_remaining_elapsed_ms": project(pilot_elapsed_ms),
        "measured_known_pilot_cost_microusd": measured_known_pilot_cost_microusd,
        "pilot_estimated_cost_microusd": (
            measured_known_pilot_cost_microusd if token_telemetry_complete else None
        ),
        "projected_remaining_cost_microusd": (
            project(measured_known_pilot_cost_microusd) if token_telemetry_complete else None
        ),
        "estimated_cost_state": (
            "available_pilot_scaled_estimate"
            if token_telemetry_complete
            else "unavailable_incomplete_token_telemetry"
        ),
        "pricing_source": "https://developers.openai.com/api/docs/pricing",
        "pricing_as_of": "2026-07-22",
        "pricing_service_tier": "standard",
        "pricing_rates": prices,
        "cache_write_telemetry_complete": cache_write_telemetry_complete,
        "cache_write_telemetry_missing_episodes": cache_write_telemetry_missing_episodes,
        "cost_estimate_treatment": (
            "observed_cache_write_tokens_used_when_available_otherwise_non_cached_input_"
            "conservatively_priced_as_cache_write_pilot_rates_linearly_scaled_not_upper_bound"
        ),
    }
    body["projection_sha256"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    atomic_write_json(store.pilot_projection_path, body, immutable=True)
    return body


async def run_full_season(
    store: BenchmarkArtifactStore,
    executor: BenchmarkExecutor,
    *,
    retry_delay_seconds: int = 60,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    progress: Callable[[int, int, str], None] | None = None,
    projection_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> tuple[PhaseRunSummary, PhaseRunSummary, PhaseRunSummary]:
    """Run pilot, baseline, objective depth selection, and paired skill study."""

    pilot_maps = store.load_map_manifest("pilot")
    main_maps = store.load_map_manifest("main")
    pilot_summary = await run_schedule(
        store,
        store.load_schedule("pilot"),
        pilot_maps,
        executor,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        progress=progress,
    )
    validate_pilot_gate(store)
    projection = build_pilot_projection(store)
    if projection_callback is not None:
        projection_callback(projection)
    baseline = store.load_schedule("baseline")
    baseline_summary = await run_schedule(
        store,
        baseline,
        main_maps,
        executor,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        progress=progress,
    )
    from .analysis import flatten_results, select_best_vision_depth

    selected = select_best_vision_depth(flatten_results(store.iter_results("baseline")))
    spec: LabyrinthBenchmarkSpec = store.load_specification()
    skill = build_schedule(spec, main_maps, "skill", selected_vision_depth=selected)
    store.save_schedule(skill)
    skill_summary = await run_schedule(
        store,
        skill,
        main_maps,
        executor,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        progress=progress,
    )
    return pilot_summary, baseline_summary, skill_summary


__all__ = [
    "acknowledge_terminal_stop",
    "BenchmarkExecutor",
    "BenchmarkRunError",
    "OpenAILabyrinthBenchmarkExecutor",
    "PhaseRunSummary",
    "build_pilot_projection",
    "opaque_episode_id",
    "run_full_season",
    "run_schedule",
    "safe_race_result",
    "validate_pilot_gate",
]
