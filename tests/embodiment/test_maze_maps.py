from __future__ import annotations

import copy

import pytest
from genesis_arena.embodiment.episode_memory import EpisodeMemoryError
from genesis_arena.embodiment.labyrinth_benchmark.spec import VISION_DEPTHS, generate_map_suite
from genesis_arena.embodiment.live_labyrinth import (
    LABYRINTH_PROTOCOL_PROMPT,
    BenchmarkMazeRaceAbort,
    LiveMazeEntrant,
    MazeNavigationMemory,
    _corridor_steps,
    _Racer,
    _visible_observation,
    compose_labyrinth_system_prompt,
    labyrinth_protocol_prompt_sha256,
    load_maze_navigation_skill,
    maze_navigation_skill_sha256,
    maze_visible_cells,
    run_benchmark_labyrinth_race,
    validate_maze_skill_text,
)
from genesis_arena.embodiment.maze_maps import (
    MazeMapError,
    MazeMapSpec,
    default_maze_map_spec,
    generate_maze_map,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, strict_json_loads
from genesis_arena.embodiment.providers.contracts import (
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
)


class _NavigationProvider:
    provider_name = "fake"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        self.requests.append(request)
        observation = strict_json_loads(request.observation_json)
        current = observation["navigation_memory"]["current"]
        visible = set(observation["visible_passages"])
        choice = next(
            (value for value in current["untried"] if value in visible),
            current["backtrack"] if current["backtrack"] in visible else "wait",
        )
        return ProviderCallResult.success(
            canonical_json_bytes(
                {
                    "protocol_version": "maze-task-plan-v1",
                    "episode_id": request.episode_id,
                    "observation_id": observation["observation_id"],
                    "participant_id": request.participant_id,
                    "passage_choice": choice,
                    "movement_mode": "follow_corridor" if choice != "wait" else "single_cell",
                    "max_corridor_cells": 256 if choice != "wait" else 1,
                    "scratchpad_update": "",
                }
            ),
            ProviderTelemetry(latency_ms=2, input_tokens=10, output_tokens=4),
        )


class _FailureProvider:
    provider_name = "fake"

    def __init__(self, failure: ProviderFailureKind) -> None:
        self.failure = failure

    async def request(self, _request: ProviderRequest) -> ProviderCallResult:
        return ProviderCallResult.failed(self.failure, ProviderTelemetry(latency_ms=1))


class _StaggeredRateLimitProvider:
    provider_name = "fake"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        self.requests.append(request)
        if request.observation_seq == 1:
            return ProviderCallResult.failed(
                ProviderFailureKind.RATE_LIMIT, ProviderTelemetry(latency_ms=1)
            )
        if request.observation_seq > 1:
            return ProviderCallResult.failed(
                ProviderFailureKind.TIMEOUT, ProviderTelemetry(latency_ms=1)
            )
        observation = strict_json_loads(request.observation_json)
        return ProviderCallResult.success(
            canonical_json_bytes(
                {
                    "protocol_version": "maze-task-plan-v1",
                    "episode_id": request.episode_id,
                    "observation_id": observation["observation_id"],
                    "participant_id": request.participant_id,
                    "passage_choice": "wait",
                    "movement_mode": "single_cell",
                    "max_corridor_cells": 1,
                    "scratchpad_update": "",
                }
            ),
            ProviderTelemetry(latency_ms=1),
        )


def _entrants() -> tuple[LiveMazeEntrant, ...]:
    return tuple(
        LiveMazeEntrant(
            f"participant_{index}",
            f"entrant-{index}",
            f"Entrant {index}",
            "fake",
            f"fake-model-{index}",
            f"#{index + 1:06x}",
        )
        for index in range(3)
    )


def _provider_free_dfs_probe(map_spec: MazeMapSpec, vision_depth: object) -> dict[str, int | bool]:
    provider = _NavigationProvider()
    racer = _Racer(_entrants()[0], provider, map_spec.start, visits=[map_spec.start])
    budget = 2 * map_spec.metrics.edge_count
    decisions = 0
    cells_moved = 0
    try:
        while racer.position != map_spec.exit and decisions < budget:
            observation = _visible_observation(
                racer,
                episode_id="ep_provider_free_dfs_probe",
                tick=cells_moved * 4,
                vision_range_cells=vision_depth,
                map_spec=map_spec,
            )
            current = observation["navigation_memory"]["current"]
            visible = set(observation["visible_passages"])
            choice = next(
                (value for value in current["untried"] if value in visible),
                current["backtrack"] if current["backtrack"] in visible else "wait",
            )
            steps, _ = _corridor_steps(
                racer,
                map_spec=map_spec,
                passage_choice=choice,
                movement_mode="follow_corridor" if choice != "wait" else "single_cell",
                max_corridor_cells=256 if choice != "wait" else 1,
            )
            if not steps:
                break
            for target, heading, relative_choice in steps:
                racer.navigation_memory.apply_transition(relative_choice, "moved")
                racer.position, racer.heading = target, heading
                cells_moved += 1
            decisions += 1
        return {
            "finished": racer.position == map_spec.exit,
            "decisions": decisions,
            "cells_moved": cells_moved,
            "peak_bytes": racer.navigation_memory.peak_bytes,
            "evictions": racer.navigation_memory.evictions,
        }
    finally:
        racer.scratchpad.close()
        racer.navigation_memory.close()


@pytest.mark.parametrize("seed", [0, 1, 987654321])
def test_generated_difficulties_are_deterministic_hash_bound_and_exact(seed: int) -> None:
    expected = {
        "easy": (15, 0, 5),
        "medium": (21, 0, 4),
        "hard": (31, 12, 2),
        "memory_stress": (41, 32, 0),
    }
    specs = []
    for difficulty, (dimension, cycles, landmarks) in expected.items():
        map_id = f"fixture-{difficulty}-{seed}"
        first = generate_maze_map(seed=seed, difficulty=difficulty, map_id=map_id)
        second = generate_maze_map(seed=seed, difficulty=difficulty, map_id=map_id)
        assert first == second
        assert MazeMapSpec.from_dict(first.as_dict()) == first
        assert first.metrics.width == first.metrics.height == dimension
        assert first.metrics.cycle_rank == cycles
        assert len(first.landmarks) == landmarks
        assert first.metrics.reachable
        assert first.metrics.dfs_upper_bound_cells == 2 * first.metrics.edge_count
        specs.append(first)
    assert [item.metrics.walkable_cells for item in specs] == sorted(
        item.metrics.walkable_cells for item in specs
    )
    assert [item.metrics.edge_count for item in specs] == sorted(
        item.metrics.edge_count for item in specs
    )


def test_map_spec_rejects_tampered_rows_metrics_and_hash() -> None:
    spec = generate_maze_map(seed=42, difficulty="easy", map_id="fixture-tamper")
    value = spec.as_dict()
    value["map_sha256"] = "0" * 64
    with pytest.raises(MazeMapError, match="hash"):
        MazeMapSpec.from_dict(value)

    value = spec.as_dict()
    value["metrics"]["edge_count"] += 1
    with pytest.raises(MazeMapError):
        MazeMapSpec.from_dict(value)

    value = spec.as_dict()
    value["rows"][0] = "." + value["rows"][0][1:]
    with pytest.raises(MazeMapError):
        MazeMapSpec.from_dict(value)


def test_custom_map_sightlines_use_injected_geometry_and_stay_straight() -> None:
    spec = generate_maze_map(seed=9, difficulty="medium", map_id="fixture-vision")
    visible = maze_visible_cells(spec.start, "infinite", spec)
    assert spec.start in visible
    assert all(cell[0] == spec.start[0] or cell[1] == spec.start[1] for cell in visible)
    assert all(cell in spec.graph() for cell in visible)


def test_maze_nav_v2_compacts_deterministically_and_securely_closes() -> None:
    visible_cells = tuple(
        ((index + 1, (index + 1) % 17), 15, f"landmark-{index}")
        for index in range(300)
    )
    first = MazeNavigationMemory("participant_0")
    snapshot = first.observe(
        observation_seq=0,
        visible_passages=("forward", "right"),
        landmark="origin",
        visible_cells=visible_cells,
    )
    second = MazeNavigationMemory("participant_0")
    duplicate = second.observe(
        observation_seq=0,
        visible_passages=("forward", "right"),
        landmark="origin",
        visible_cells=visible_cells,
    )
    assert snapshot == duplicate
    assert snapshot["v"] == "maze-nav/2"
    assert snapshot["current"]["untried"] == ["forward", "right"]
    assert len(first.utf8) <= 2048
    assert first.evictions > 0
    assert first.peak_bytes <= 2048
    first.close()
    with pytest.raises(EpisodeMemoryError, match="closed"):
        _ = first.utf8
    second.close()


def test_protocol_and_optional_skill_are_separate_hash_bound_and_non_map_specific() -> None:
    skill = load_maze_navigation_skill()
    plain = compose_labyrinth_system_prompt()
    skilled = compose_labyrinth_system_prompt(
        skill_mode="maze-navigation-v1", skill_text=skill
    )
    assert plain == LABYRINTH_PROTOCOL_PROMPT
    assert "depth-first" not in plain.casefold()
    assert skilled.count(skill) == 1
    assert maze_navigation_skill_sha256(skill) in skilled
    assert len(labyrinth_protocol_prompt_sha256()) == 64
    with pytest.raises(ValueError, match="map-specific"):
        validate_maze_skill_text("Use hidden route [1,2].")
    for leaked in (
        "seed 123",
        "The start is at the north wall.",
        "The exit location: south.",
        "###S...E###",
        "route: left, forward, right, back",
        "Take left then right, then forward.",
    ):
        with pytest.raises(ValueError, match="map-specific"):
            validate_maze_skill_text(leaked)
    assert validate_maze_skill_text("Use a generic backtracking route during DFS.")
    with pytest.raises(ValueError, match="requires"):
        compose_labyrinth_system_prompt(skill_text=skill)


@pytest.mark.asyncio
@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard", "memory_stress"])
async def test_generated_map_runtime_uses_equal_dynamic_budgets_and_safe_telemetry(
    difficulty: str,
) -> None:
    spec = generate_maze_map(
        seed=2026, difficulty=difficulty, map_id=f"fixture-runtime-{difficulty}"
    )
    providers = {f"participant_{index}": _NavigationProvider() for index in range(3)}
    execution = await run_benchmark_labyrinth_race(
        episode_id="ep_generated_maze_runtime",
        entrants=_entrants(),
        providers=providers,
        map_spec=spec,
        vision_range_cells=2,
    )

    assert execution.replay["map"] == spec.as_dict()
    assert execution.replay["participant_call_budget"] == 2 * spec.metrics.edge_count
    assert execution.replay["maximum_ticks"] == 8 * spec.metrics.edge_count
    assert execution.replay["result"]["reason"] == "all_racers_finished"
    assert all(item["finished"] for item in execution.replay["racers"])
    assert len(execution.safe_decisions) == execution.replay["provider_calls"]
    assert {item.participant_id for item in execution.memory_telemetry} == set(providers)
    assert all(0 < item.peak_bytes <= 2048 for item in execution.memory_telemetry)
    assert all(
        item.evictions <= execution.replay["provider_calls_by_participant"][item.participant_id]
        for item in execution.memory_telemetry
    )
    assert all(item.telemetry is not None for item in execution.safe_decisions)
    assert all(
        request.system_prompt == LABYRINTH_PROTOCOL_PROMPT
        for provider in providers.values()
        for request in provider.requests
    )
    safe_serialized = repr([item.as_dict() for item in execution.safe_decisions]).casefold()
    assert all(
        term not in safe_serialized
        for term in ("raw_output", "scratchpad", "navigation_memory", "chain_of_thought")
    )


def test_every_frozen_map_and_vision_depth_is_dfs_solvable_with_bounded_memory() -> None:
    maps = (*generate_map_suite("pilot").maps, *generate_map_suite("main").maps)
    assert len(maps) == 52
    for map_spec in maps:
        for vision_depth in VISION_DEPTHS:
            result = _provider_free_dfs_probe(map_spec, vision_depth)
            budget = 2 * map_spec.metrics.edge_count
            assert result["finished"], (
                map_spec.map_id,
                vision_depth,
            )
            assert 0 < result["decisions"] <= budget
            assert 0 < result["cells_moved"] <= budget
            assert 0 < result["peak_bytes"] <= 2048
            assert 0 <= result["evictions"] <= result["decisions"]


@pytest.mark.asyncio
async def test_skill_is_injected_exactly_once_into_each_selected_request() -> None:
    spec = generate_maze_map(seed=4, difficulty="easy", map_id="fixture-skilled-request")
    providers = {f"participant_{index}": _NavigationProvider() for index in range(3)}
    skill = load_maze_navigation_skill()
    await run_benchmark_labyrinth_race(
        episode_id="ep_generated_maze_skill",
        entrants=_entrants(),
        providers=providers,
        map_spec=spec,
        participant_call_budget=1,
        skill_text=skill,
    )
    for provider in providers.values():
        assert len(provider.requests) == 1
        assert provider.requests[0].system_prompt.count(skill) == 1
        assert maze_navigation_skill_sha256(skill) in provider.requests[0].system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failures", "kind"),
    [
        ((ProviderFailureKind.CREDENTIAL,) * 3, "fatal_provider"),
        ((ProviderFailureKind.QUOTA,) * 3, "fatal_provider"),
        (
            (
                ProviderFailureKind.TRANSPORT,
                ProviderFailureKind.RATE_LIMIT,
                ProviderFailureKind.TRANSPORT,
            ),
            "infrastructure_outage",
        ),
    ],
)
async def test_benchmark_runner_fails_fast_on_fatal_or_all_active_infrastructure(
    failures: tuple[ProviderFailureKind, ...], kind: str
) -> None:
    spec = generate_maze_map(seed=8, difficulty="easy", map_id=f"fixture-abort-{kind}")
    providers = {
        f"participant_{index}": _FailureProvider(failure)
        for index, failure in enumerate(failures)
    }
    with pytest.raises(BenchmarkMazeRaceAbort) as captured:
        await run_benchmark_labyrinth_race(
            episode_id=f"ep_generated_maze_abort_{kind}",
            entrants=_entrants(),
            providers=providers,
            map_spec=spec,
        )
    assert captured.value.kind == kind
    assert set(captured.value.failures) == {value.value for value in failures}


@pytest.mark.asyncio
async def test_benchmark_timeout_remains_an_ordinary_model_outcome() -> None:
    spec = generate_maze_map(seed=11, difficulty="easy", map_id="fixture-timeout")
    providers = {
        f"participant_{index}": _FailureProvider(ProviderFailureKind.TIMEOUT)
        for index in range(3)
    }
    execution = await run_benchmark_labyrinth_race(
        episode_id="ep_generated_maze_timeout",
        entrants=_entrants(),
        providers=providers,
        map_spec=spec,
        participant_call_budget=1,
    )
    assert len(execution.safe_decisions) == 3
    assert {item.provider_failure for item in execution.safe_decisions} == {"timeout"}


@pytest.mark.asyncio
async def test_desynchronized_single_racer_rate_limit_is_not_an_infrastructure_abort() -> None:
    map_spec = default_maze_map_spec()
    staggered = _StaggeredRateLimitProvider()
    navigation = {
        "participant_0": _NavigationProvider(),
        "participant_2": _NavigationProvider(),
    }
    execution = await run_benchmark_labyrinth_race(
        episode_id="ep_generated_maze_staggered_rate_limit",
        entrants=_entrants(),
        providers={
            "participant_0": navigation["participant_0"],
            "participant_1": staggered,
            "participant_2": navigation["participant_2"],
        },
        map_spec=map_spec,
        participant_call_budget=3,
    )
    participant_one = [
        item for item in execution.safe_decisions if item.participant_id == "participant_1"
    ]
    assert participant_one[1].provider_failure == "rate_limit_error"
    staggered_ticks = [
        strict_json_loads(request.observation_json)["tick"] for request in staggered.requests
    ]
    peer_ticks = [
        strict_json_loads(request.observation_json)["tick"]
        for request in navigation["participant_0"].requests
    ]
    assert staggered_ticks[1] == 4
    assert 4 not in peer_ticks


def test_public_map_roundtrip_is_not_affected_by_caller_mutation() -> None:
    spec = generate_maze_map(seed=17, difficulty="easy", map_id="fixture-copy")
    public = spec.as_dict()
    mutated = copy.deepcopy(public)
    mutated["rows"][1] = mutated["rows"][1].replace(".", "#", 1)
    assert spec.as_dict() == public
