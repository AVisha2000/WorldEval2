from __future__ import annotations

import asyncio
import copy
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from genesis_arena.embodiment.labyrinth_benchmark.analysis import (
    _aggregate_groups,
    flatten_results,
    hierarchical_bootstrap,
)
from genesis_arena.embodiment.labyrinth_benchmark.artifacts import (
    BenchmarkArtifactError,
    BenchmarkArtifactStore,
    assert_public_safe,
    atomic_write,
    atomic_write_json,
    export_curated_report,
)
from genesis_arena.embodiment.labyrinth_benchmark.report import (
    _BASELINE_FIGURES,
    generate_report,
)
from genesis_arena.embodiment.labyrinth_benchmark.runner import (
    BenchmarkRunError,
    acknowledge_terminal_stop,
    run_schedule,
)
from genesis_arena.embodiment.labyrinth_benchmark.spec import (
    DIFFICULTIES,
    PARTICIPANTS,
    VISION_DEPTHS,
    build_benchmark_spec,
    build_schedule,
    generate_map_suite,
)
from genesis_arena.embodiment.live_labyrinth import BenchmarkMazeRaceAbort
from PIL import Image


@pytest.fixture(scope="module")
def benchmark_bundle():
    pilot_maps = generate_map_suite("pilot")
    main_maps = generate_map_suite("main")
    spec = build_benchmark_spec("fixture-season", pilot_maps, main_maps)
    return SimpleNamespace(
        pilot_maps=pilot_maps,
        main_maps=main_maps,
        spec=spec,
        pilot=build_schedule(spec, pilot_maps, "pilot"),
        baseline=build_schedule(spec, main_maps, "baseline"),
        skill=build_schedule(spec, main_maps, "skill", selected_vision_depth=4),
    )


def _store(tmp_path: Path, bundle) -> BenchmarkArtifactStore:
    store = BenchmarkArtifactStore(tmp_path, bundle.spec.season_id)
    store.initialize(
        bundle.spec,
        bundle.pilot_maps,
        bundle.main_maps,
        (bundle.pilot, bundle.baseline),
    )
    return store


def _episode(race, maze, participant_index: int) -> dict[str, object]:
    participant_id = PARTICIPANTS[participant_index]
    model_id = race.seats[participant_index]
    provider_model = {
        "sol": "gpt-5.6-sol",
        "terra": "gpt-5.6-terra",
        "luna": "gpt-5.6-luna",
    }[model_id]
    return {
        "participant_id": participant_id,
        "model_id": model_id,
        "provider_model": provider_model,
        "completed": False,
        "finish_tick": None,
        "calls": 1,
        "charged_calls": race.participant_call_budget,
        "distance_cells": 0,
        "path": [list(maze.start)],
        "shortest_path_cells": maze.metrics.shortest_path_cells,
        "path_efficiency_basis_points": 0,
        "unique_corridor_cells": 1,
        "repeated_corridor_cells": 0,
        "repeated_cell_basis_points": 0,
        "invalid_decisions": 0,
        "invalid_decision_basis_points": 0,
        "waiting_windows": 1,
        "wait_basis_points": 10_000,
        "corridor_commands": 0,
        "single_cell_commands": 1,
        "corridor_command_basis_points": 0,
        "cells_moved": 0,
        "cells_per_call_milli": 0,
        "backtrack_commands": 0,
        "successful_backtracks": 0,
        "backtrack_success_basis_points": 0,
        "recovery_opportunities": 0,
        "successful_recoveries": 0,
        "recovery_basis_points": 0,
        "input_tokens": 10,
        "output_tokens": 2,
        "cached_input_tokens": 1,
        "cache_write_tokens": 0,
        "total_tokens": 12,
        "token_telemetry_complete": True,
        "cache_write_telemetry_complete": True,
        "latency_ms": 5,
        "latency_telemetry_complete": True,
        "peak_memory_bytes": 100,
        "memory_evictions": 0,
        "provider_failures": {},
        "decision_trace": [
            {
                "observation_seq": 0,
                "disposition": "accepted",
                "passage_choice": "wait",
                "movement_mode": "single_cell",
                "max_corridor_cells": 1,
                "cells_moved": 0,
                "stopped_because": "waited",
                "provider_failure": None,
            }
        ],
    }


def _result(schedule, maps) -> dict[str, object]:
    race = schedule.races[0]
    maze = next(item for item in maps.maps if item.map_id == race.map_id)
    return {
        "schema_version": "worldarena/labyrinth-benchmark-race-result/1",
        "season_id": schedule.season_id,
        "schedule_sha256": schedule.schedule_sha256,
        "race_id": race.race_id,
        "phase": schedule.phase,
        "map_id": race.map_id,
        "map_sha256": race.map_sha256,
        "difficulty": race.difficulty,
        "vision_depth": race.vision_depth,
        "repetition": race.repetition,
        "skill_mode": race.skill_mode,
        "participant_call_budget": race.participant_call_budget,
        "wall_time_ms": 20,
        "attempt": 1,
        "episodes": [_episode(race, maze, index) for index in range(3)],
    }


def test_frozen_map_suites_and_factorial_schedules(benchmark_bundle):
    bundle = benchmark_bundle
    assert bundle.pilot_maps.manifest_sha256 == (
        "a0bc832dadae7e386ccdce5996b750e247c839a8e092509fd1f33b88173d1a07"
    )
    assert bundle.main_maps.manifest_sha256 == (
        "0f878f5c7671f9bd8d0299b227c102e0f2481d0e69299932244b282847c2f7d6"
    )
    assert (len(bundle.pilot.races), len(bundle.baseline.races), len(bundle.skill.races)) == (
        120,
        600,
        120,
    )
    assert Counter(race.vision_depth for race in bundle.baseline.races) == {
        vision: 120 for vision in VISION_DEPTHS
    }
    for difficulty, dimension, cycles, landmarks in zip(
        DIFFICULTIES, (15, 21, 31, 41), (0, 0, 12, 32), (5, 4, 2, 0)
    ):
        mazes = [maze for maze in bundle.main_maps.maps if maze.difficulty == difficulty]
        assert all(maze.metrics.width == dimension for maze in mazes)
        assert all(maze.metrics.cycle_rank == cycles for maze in mazes)
        assert all(len(maze.landmarks) == landmarks for maze in mazes)
        assert all(maze.participant_call_budget == 2 * maze.metrics.edge_count for maze in mazes)


def test_skill_schedule_is_exactly_seat_paired(benchmark_bundle):
    baseline = {
        (race.map_id, race.repetition): race
        for race in benchmark_bundle.baseline.races
        if race.vision_depth == 4
    }
    assert all(
        race.seats == baseline[(race.map_id, race.repetition)].seats
        for race in benchmark_bundle.skill.races
    )


@pytest.mark.parametrize(
    "key",
    ("model_scratchpad", "rawPrompt", "navigation-memory-v2", "privateNavigationMemory"),
)
def test_public_artifacts_reject_adversarial_protected_keys(key):
    with pytest.raises(BenchmarkArtifactError, match="protected"):
        assert_public_safe({key: "do not persist"})
    assert_public_safe({"protocol_prompt_sha256": "0" * 64, "peak_memory_bytes": 128})


def test_result_validation_recomputes_metrics_and_binds_filenames(tmp_path, benchmark_bundle):
    store = _store(tmp_path, benchmark_bundle)
    result = _result(benchmark_bundle.pilot, benchmark_bundle.pilot_maps)
    store.save_result(benchmark_bundle.pilot, result)
    assert store.load_bound_result(benchmark_bundle.pilot, str(result["race_id"])) == result

    forged = copy.deepcopy(result)
    forged["episodes"][0]["wait_basis_points"] = 0
    with pytest.raises(BenchmarkArtifactError, match="trace-derived rates"):
        store.validate_result(benchmark_bundle.pilot, forged)

    forged = copy.deepcopy(result)
    forged["wall_time_ms"] = -1
    with pytest.raises(BenchmarkArtifactError, match="execution metadata"):
        store.validate_result(benchmark_bundle.pilot, forged)

    wrong_path = store.result_path("pilot", "different-race-id")
    atomic_write_json(wrong_path, result)
    with pytest.raises(BenchmarkArtifactError, match="filename binding"):
        store.iter_results("pilot")


def test_state_schema_and_bindings_are_strict(tmp_path, benchmark_bundle):
    store = _store(tmp_path, benchmark_bundle)
    state = dict(store.load_state())
    state["extra"] = 1
    with pytest.raises(BenchmarkArtifactError, match="state fields"):
        store.write_state(state)
    state.pop("extra")
    state["completed_races"] = True
    with pytest.raises(BenchmarkArtifactError, match="state counters"):
        store.write_state(state)


class _RetryStore:
    def __init__(self, spec):
        self.season_id = spec.season_id
        self.spec = spec
        self.state = {
            "schema_version": "worldarena/labyrinth-benchmark-state/1",
            "season_id": spec.season_id,
            "spec_sha256": spec.spec_sha256,
            "status": "generated",
            "active_phase": None,
            "completed_races": 0,
            "infrastructure_voids": 0,
            "infrastructure_voids_by_phase": {"pilot": 0, "baseline": 0, "skill": 0},
            "pending_retry": None,
            "last_error": None,
        }

    def load_specification(self):
        return self.spec

    def iter_results(self, phase=None):
        return ()

    def load_state(self):
        return copy.deepcopy(self.state)

    def write_state(self, state):
        self.state = copy.deepcopy(state)


class _OutageExecutor:
    def __init__(self):
        self.attempts: list[int] = []

    async def execute(self, race, maze, *, attempt):
        self.attempts.append(attempt)
        raise BenchmarkMazeRaceAbort(
            "infrastructure_outage", ("rate_limit_error",) * 3
        )


@pytest.mark.asyncio
async def test_retry_is_consumed_once_across_process_interruption(benchmark_bundle):
    store = _RetryStore(benchmark_bundle.spec)
    executor = _OutageExecutor()

    async def interrupted_sleep(_seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_schedule(
            store,
            benchmark_bundle.pilot,
            benchmark_bundle.pilot_maps,
            executor,
            retry_delay_seconds=0,
            sleep=interrupted_sleep,
        )
    assert executor.attempts == [1]
    assert store.state["pending_retry"]["retry_started"] is False

    async def no_sleep(_seconds):
        return None

    with pytest.raises(BenchmarkRunError, match="repeated_infrastructure_outage"):
        await run_schedule(
            store,
            benchmark_bundle.pilot,
            benchmark_bundle.pilot_maps,
            executor,
            retry_delay_seconds=0,
            sleep=no_sleep,
    )
    assert executor.attempts == [1, 2]
    assert store.state["pending_retry"] is None
    with pytest.raises(BenchmarkRunError, match="repeated_infrastructure_outage"):
        await run_schedule(
            store,
            benchmark_bundle.pilot,
            benchmark_bundle.pilot_maps,
            executor,
            retry_delay_seconds=0,
            sleep=no_sleep,
        )
    assert executor.attempts == [1, 2]
    acknowledge_terminal_stop(store)
    assert store.state["status"] == "generated" and store.state["last_error"] is None


def test_hierarchical_bootstrap_is_deterministic_and_missing_cells_are_explicit(
    benchmark_bundle,
):
    rows = (
        {"map_id": "a", "value": 0},
        {"map_id": "a", "value": 0},
        {"map_id": "b", "value": 10},
        {"map_id": "b", "value": 10},
    )
    first = hierarchical_bootstrap(rows, "value", seed=7, iterations=1_000)
    assert first == hierarchical_bootstrap(rows, "value", seed=7, iterations=1_000)
    assert first["point"] == 5 and first["lower"] == 0 and first["upper"] == 10

    result = _result(benchmark_bundle.pilot, benchmark_bundle.pilot_maps)
    result["phase"] = "baseline"
    result["skill_mode"] = "none"
    flattened = list(flatten_results((result,)))
    for row in flattened:
        row["token_telemetry_complete"] = False
        row["cache_write_telemetry_complete"] = False
        row["latency_telemetry_complete"] = False
    groups = _aggregate_groups(flattened, benchmark_bundle.spec)
    unavailable = {
        row["metric"]: row
        for row in groups
        if row["metric"]
        in {
            "path_efficiency_basis_points",
            "input_tokens",
            "latency_ms",
            "recovery_basis_points",
        }
    }
    assert all(row["available"] is False and row["point"] is None for row in unavailable.values())


def test_complete_report_renders_and_exports_without_raw_results(
    tmp_path, benchmark_bundle
):
    groups = []
    for definition in _BASELINE_FIGURES:
        for difficulty in DIFFICULTIES:
            for model_id in ("sol", "terra", "luna"):
                for vision_index, vision in enumerate(VISION_DEPTHS):
                    missing = (
                        definition.metric == "path_efficiency_basis_points"
                        and difficulty == "memory_stress"
                        and model_id == "luna"
                        and vision == 1
                    )
                    point = 5_000 + vision_index * 100 if definition.fixed_basis_points else 100
                    groups.append(
                        {
                            "phase": "baseline",
                            "skill_mode": "none",
                            "difficulty": difficulty,
                            "vision_depth": vision,
                            "model_id": model_id,
                            "metric": definition.metric,
                            "unit": definition.unit,
                            "available": not missing,
                            "point": None if missing else point,
                            "lower": None if missing else point - 10,
                            "upper": None if missing else point + 10,
                            "map_count": 0 if missing else 10,
                            "sample_size": 0 if missing else 30,
                            "censored_count": 0,
                            "missing_count": 30 if missing else 0,
                        }
                    )
    paired = []
    for difficulty in DIFFICULTIES:
        for model_id in ("sol", "terra", "luna"):
            for metric, unit in (
                ("completion_delta_basis_points", "basis_points"),
                ("charged_calls_delta", "calls"),
                ("total_tokens_delta", "tokens"),
                ("path_efficiency_delta_basis_points", "basis_points"),
            ):
                paired.append(
                    {
                        "difficulty": difficulty,
                        "model_id": model_id,
                        "metric": metric,
                        "unit": unit,
                        "available": True,
                        "point": -2 if metric == "charged_calls_delta" else 10,
                        "lower": -4 if metric == "charged_calls_delta" else 0,
                        "upper": 1 if metric == "charged_calls_delta" else 20,
                        "map_count": 10,
                        "sample_size": 30,
                        "missing_pair_count": 0,
                    }
                )
    totals = {
        phase: {
            "race_results": races,
            "model_episodes": races * 3,
            "api_calls": races * 30,
            "budget_charged_calls": races * 32,
            "input_tokens": races * 300,
            "output_tokens": races * 30,
            "total_tokens": races * 330,
            "token_telemetry_complete": True,
            "token_telemetry_missing_episodes": 0,
            "provider_latency_ms": races * 20,
            "latency_telemetry_complete": True,
            "latency_telemetry_missing_episodes": 0,
            "elapsed_wall_time_ms": races * 10,
        }
        for phase, races in (("pilot", 120), ("baseline", 600), ("skill", 120), ("season", 840))
    }
    analysis = {
        "schema_version": "worldarena/labyrinth-benchmark-analysis/1",
        "season_id": benchmark_bundle.spec.season_id,
        "spec_sha256": benchmark_bundle.spec.spec_sha256,
        "schedule_sha256s": {
            "pilot": benchmark_bundle.pilot.schedule_sha256,
            "baseline": benchmark_bundle.baseline.schedule_sha256,
            "skill": benchmark_bundle.skill.schedule_sha256,
        },
        "frozen_hashes": {
            "pilot_manifest_sha256": benchmark_bundle.spec.pilot_manifest_sha256,
            "main_manifest_sha256": benchmark_bundle.spec.main_manifest_sha256,
            "protocol_prompt_sha256": benchmark_bundle.spec.protocol_prompt_sha256,
            "skill_sha256": benchmark_bundle.spec.skill_sha256,
        },
        "selected_vision_depth": 4,
        "sample_counts": {
            "race_results": 840,
            "model_episodes": 2_520,
            "completed_model_episodes": 2_000,
            "censored_model_episodes": 520,
            "pilot_races": 120,
            "pilot_model_episodes": 360,
            "baseline_races": 600,
            "baseline_model_episodes": 1_800,
            "skill_races": 120,
            "skill_model_episodes": 360,
        },
        "phase_totals": totals,
        "infrastructure_voids_by_phase": {"pilot": 0, "baseline": 1, "skill": 0},
        "provider_failures": {"timeout": 3},
        "groups": groups,
        "paired_skill_effects": paired,
        "model_skill_comparisons": [
            {
                "baseline_model_id": opponent,
                "matches_or_exceeds": opponent == "terra",
                "skilled_completion_count": 100,
                "baseline_completion_count": 100,
                "sample_size_each": 120,
                "token_telemetry_complete": True,
            }
            for opponent in ("sol", "terra")
        ],
        "analysis_sha256": "9" * 64,
    }
    store = _store(tmp_path, benchmark_bundle)
    store.save_schedule(benchmark_bundle.skill)
    atomic_write_json(store.analysis_path, analysis)
    atomic_write(store.aggregate_csv_path, b"metric,point\ncompletion,5000\n")
    atomic_write(store.paired_csv_path, b"metric,point\ncharged_calls,-2\n")

    manifest = generate_report(store)

    assert store.report_path.is_file()
    assert len(manifest["figures"]) == len(_BASELINE_FIGURES) + 2
    with Image.open(store.root / "report" / manifest["figures"][0]["png_path"]) as image:
        assert image.size == (1280, 900)
    report_text = store.report_path.read_text(encoding="utf-8")
    assert "Missing token episodes" in report_text
    assert "NA" in report_text
    assert report_text.count('id="calls-by-vision-title"') == 1
    curated = tmp_path / "curated"
    export_manifest = export_curated_report(store, curated)
    assert export_manifest["season_id"] == benchmark_bundle.spec.season_id
    assert (curated / "report" / "index.html").is_file()
    assert not (curated / "results").exists()
    assert not (curated / "state.json").exists()
