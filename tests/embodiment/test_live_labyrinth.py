from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from collections import deque

import pytest
from genesis_arena.embodiment.api import _validate_live_maze_payload
from genesis_arena.embodiment.labyrinth_run import HEADINGS, _cell_graph, _start_exit
from genesis_arena.embodiment.live_labyrinth import (
    LIVE_TASK_ID,
    MAX_LIVE_PROVIDER_CALLS,
    MAX_LIVE_SPECTATOR_FRAMES,
    MAX_LIVE_SPECTATOR_PATH_CELLS,
    LiveLabyrinthError,
    LiveLabyrinthService,
    LiveMazeEntrant,
    MazeNavigationMemory,
    maze_visible_cells,
    public_live_evaluation,
    run_live_labyrinth_race,
    verify_live_replay,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, strict_json_loads
from genesis_arena.embodiment.providers.contracts import (
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
)


class ScriptedMazeProvider:
    provider_name = "fake"

    def __init__(self, choices: list[str], *, stale: bool = False) -> None:
        self._choices = iter(choices)
        self._stale = stale
        self.requests: list[ProviderRequest] = []

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        self.requests.append(request)
        observation = strict_json_loads(request.observation_json)
        choice = next(self._choices, "wait")
        payload = {
            "protocol_version": "maze-task-plan-v1",
            "episode_id": request.episode_id,
            "observation_id": "obs_participant_0_0000"
            if self._stale
            else observation["observation_id"],
            "participant_id": request.participant_id,
            "passage_choice": choice,
            "movement_mode": "single_cell",
            "max_corridor_cells": 1,
            "scratchpad_update": "private route note",
        }
        return ProviderCallResult.success(
            canonical_json_bytes(payload), ProviderTelemetry(latency_ms=0)
        )


class FailedMazeProvider:
    provider_name = "fake"

    async def request(self, _request: ProviderRequest) -> ProviderCallResult:
        return ProviderCallResult.failed(
            ProviderFailureKind.CREDENTIAL, ProviderTelemetry(latency_ms=0)
        )


class MemoryMazeProvider:
    """Explore only from the participant's backend-managed navigation memory."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        self.requests.append(request)
        observation = strict_json_loads(request.observation_json)
        memory = observation["navigation_memory"]
        assert memory["owner"] == request.participant_id
        current = memory["current"]
        visible = set(observation["visible_passages"])
        choice = next(
            (value for value in current["untried"] if value in visible),
            current["backtrack"] if current["backtrack"] in visible else "wait",
        )
        payload = {
            "protocol_version": "maze-task-plan-v1",
            "episode_id": request.episode_id,
            "observation_id": observation["observation_id"],
            "participant_id": request.participant_id,
            "passage_choice": choice,
            "movement_mode": "follow_corridor" if choice != "wait" else "single_cell",
            "max_corridor_cells": 256 if choice != "wait" else 1,
            # Deliberately useless notes prove that model-written text does not own navigation.
            "scratchpad_update": "model note without a map",
        }
        return ProviderCallResult.success(
            canonical_json_bytes(payload), ProviderTelemetry(latency_ms=0)
        )


def _shortest_choices() -> list[str]:
    graph = _cell_graph()
    start, exit_cell = _start_exit()
    previous = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == exit_cell:
            break
        for candidate in graph[current]:
            if candidate not in previous:
                previous[candidate] = current
                queue.append(candidate)
    path = []
    current = exit_cell
    while current != start:
        path.append(current)
        current = previous[current]
    path.append(start)
    path.reverse()
    heading = 0
    choices = []
    names = ("forward", "right", "back", "left")
    for source, target in zip(path, path[1:]):
        absolute = HEADINGS.index((target[0] - source[0], target[1] - source[1]))
        choices.append(names[(absolute - heading) % 4])
        heading = absolute
    return choices


def _entrants() -> tuple[LiveMazeEntrant, ...]:
    return (
        LiveMazeEntrant("participant_0", "sol", "Sol", "fake", "fake-sol", "#fbbf24"),
        LiveMazeEntrant("participant_1", "terra", "Terra", "fake", "fake-terra", "#34d399"),
        LiveMazeEntrant("participant_2", "luna", "Luna", "fake", "fake-luna", "#a78bfa"),
    )


def test_navigation_memory_tracks_local_pose_branches_and_failed_turns() -> None:
    memory = MazeNavigationMemory("participant_0")
    first = memory.observe(
        observation_seq=0,
        visible_passages=("forward", "right"),
        landmark="none",
    )
    assert first["pose"] == [0, 0, "N"]
    assert first["current"] == {
        "visits": 1,
        "untried": ["forward", "right"],
        "backtrack": None,
    }

    memory.apply_transition("right", "moved")
    second = memory.observe(
        observation_seq=1,
        visible_passages=("back",),
        landmark="blue-crystal",
    )
    assert second["pose"] == [1, 0, "E"]
    assert second["last"] == ["right", "moved"]
    assert second["current"]["untried"] == []
    assert second["current"]["backtrack"] == "back"

    memory.apply_transition("forward", "invalid")
    third = memory.observe(
        observation_seq=2,
        visible_passages=("back",),
        landmark="blue-crystal",
    )
    assert third["pose"] == [1, 0, "E"]
    assert third["last"] == ["forward", "invalid"]

    memory.apply_transition("wait", "provider_failure")
    fourth = memory.observe(
        observation_seq=3,
        visible_passages=("back",),
        landmark="blue-crystal",
    )
    assert fourth["pose"] == [1, 0, "E"]
    assert fourth["last"] == ["wait", "provider_failure"]
    assert len(memory.utf8) <= 2048
    memory.close()


def test_navigation_memory_recovers_when_a_model_declines_loop_backtrack() -> None:
    """A legal move away from a loop hint must not retain a non-adjacent parent."""

    memory = MazeNavigationMemory("participant_0")
    memory.observe(observation_seq=0, visible_passages=("forward",), landmark="none")
    # Walk a four-cell loop back to the origin.  Returning to the already-traversed
    # origin creates a temporary recovery hint to the preceding cell.
    for choice in ("forward", "right", "right", "right"):
        memory.apply_transition(choice, "moved")
    loop_snapshot = memory.observe(
        observation_seq=1,
        visible_passages=("forward", "back"),
        landmark="none",
    )
    assert loop_snapshot["current"]["backtrack"] == "back"

    # The controller is permitted to choose another legal passage.  The old loop hint
    # must be discarded rather than becoming a non-adjacent backtrack parent.
    memory.apply_transition("forward", "moved")
    recovered = memory.observe(
        observation_seq=2,
        visible_passages=("back",),
        landmark="none",
    )
    assert recovered["pose"] == [-1, 0, "W"]
    assert recovered["current"]["backtrack"] == "back"
    assert len(memory.utf8) <= 2048
    memory.close()


def test_configurable_straight_line_vision_stops_at_walls_and_never_turns_corners() -> None:
    start = _start_exit()[0]
    assert maze_visible_cells(start, 1) == ((7, 13), (8, 13))
    assert maze_visible_cells(start, 2) == ((7, 13), (8, 13), (9, 13))
    infinite = maze_visible_cells(start, "infinite")
    assert infinite == ((7, 13), (8, 13), (9, 13), (10, 13), (11, 13))
    # The final visible cell exposes its north branch, but vision does not turn into that branch.
    assert (11, 12) not in infinite


def test_live_maze_api_accepts_the_full_clock_budget_and_rejects_more() -> None:
    payload = {
        "provider": "openai",
        "api_key": "session-only-test-key",
        "entrants": [
            {"display_name": "Sol", "model": "gpt-5.6-sol"},
            {"display_name": "Terra", "model": "gpt-5.6-terra"},
            {"display_name": "Luna", "model": "gpt-5.6-luna"},
        ],
    }
    assert _validate_live_maze_payload(payload)["max_provider_calls"] == 450
    assert _validate_live_maze_payload(payload)["vision_range_cells"] == 4
    assert (
        _validate_live_maze_payload({**payload, "vision_range_cells": "infinite"})[
            "vision_range_cells"
        ]
        == "infinite"
    )
    with pytest.raises(ValueError, match="call budget"):
        _validate_live_maze_payload({**payload, "max_provider_calls": 451})
    with pytest.raises(ValueError, match="vision range"):
        _validate_live_maze_payload({**payload, "vision_range_cells": 0})


@pytest.mark.asyncio
async def test_memory_guided_explorers_finish_without_a_hidden_route() -> None:
    providers = {
        participant_id: MemoryMazeProvider()
        for participant_id in ("participant_0", "participant_1", "participant_2")
    }
    execution = await run_live_labyrinth_race(
        episode_id="ep_live_labyrinth_memory",
        entrants=_entrants(),
        providers=providers,
    )

    assert MAX_LIVE_PROVIDER_CALLS == 450
    assert execution.replay["result"]["reason"] == "all_racers_finished"
    assert execution.replay["provider_calls"] <= MAX_LIVE_PROVIDER_CALLS
    assert all(value["finished"] for value in execution.replay["racers"])
    for participant_id, provider in providers.items():
        assert provider.requests
        assert len(provider.requests) < 64
        assert all(
            strict_json_loads(request.observation_json)["navigation_memory"]["owner"]
            == participant_id
            for request in provider.requests
        )
        assert all(
            len(
                canonical_json_bytes(
                    strict_json_loads(request.observation_json)["navigation_memory"]
                )
            )
            <= 2048
            for request in provider.requests
        )


@pytest.mark.asyncio
async def test_live_race_runs_three_private_provider_calls_and_seals_public_replay() -> None:
    choices = _shortest_choices()
    providers = {
        participant_id: ScriptedMazeProvider(choices)
        for participant_id in ("participant_0", "participant_1", "participant_2")
    }
    execution = await run_live_labyrinth_race(
        episode_id="ep_live_labyrinth_test",
        entrants=_entrants(),
        providers=providers,
        max_provider_calls=180,
    )

    assert execution.replay["task_id"] == LIVE_TASK_ID
    assert execution.replay["result"]["finish_order"] == [
        "participant_0",
        "participant_1",
        "participant_2",
    ]
    assert execution.replay["provider_calls"] == 180
    assert all(len(provider.requests) == 60 for provider in providers.values())
    for provider in providers.values():
        observation = strict_json_loads(provider.requests[0].observation_json)
        assert set(observation) == {
            "episode_id",
            "observation_id",
            "observation_seq",
            "participant_id",
            "protocol_version",
            "profile",
            "tick",
            "visible_passages",
            "vision",
            "landmark",
            "at_exit",
            "movement_receipt",
            "navigation_memory",
        }
        assert "participant_0" not in provider.requests[0].system_prompt
        assert provider.requests[0].deadline_monotonic_ns > time.monotonic_ns()
    serialized = repr(execution.replay).casefold()
    assert all(
        term not in serialized
        for term in ("scratchpad", "raw_output", "private route", "navigation_memory")
    )
    assert any(
        item.raw_output and b"private route" in item.raw_output
        for item in execution.protected_decisions
    )
    verify_live_replay(execution.replay)
    assert public_live_evaluation(execution.replay)["verification"]["state"] == "verified"

    leaked = copy.deepcopy(execution.replay)
    leaked["navigation_memory"] = {"owner": "participant_0"}
    leaked.pop("final_state_sha256")
    leaked["final_state_sha256"] = hashlib.sha256(canonical_json_bytes(leaked)).hexdigest()
    with pytest.raises(LiveLabyrinthError, match="protected controller material"):
        verify_live_replay(leaked)

    for protected_key in (
        "navigation-memory",
        "model_scratchpad",
        "raw-output-copy",
        "rawPrompt",
        "navigationMemory",
        "chainOfThought",
    ):
        leaked = copy.deepcopy(execution.replay)
        leaked[protected_key] = "must not publish"
        leaked.pop("final_state_sha256")
        leaked["final_state_sha256"] = hashlib.sha256(canonical_json_bytes(leaked)).hexdigest()
        with pytest.raises(LiveLabyrinthError, match="protected controller material"):
            verify_live_replay(leaked)

    tampered_replays = []
    tampered = copy.deepcopy(execution.replay)
    tampered["result"]["winner_id"] = "participant_2"
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["result"]["reason"] = "decision_budget_or_tick_limit"
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["result"]["completion_tick"] += 4
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["racers"][0]["path_efficiency_basis_points"] += 1
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["racers"][0]["invalid_decisions"] += 1
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["racers"][0]["waiting_windows"] += 1
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    first_move = next(value for value in tampered["events"] if value["kind"] == "move")
    first_move["choice"] = "back" if first_move["choice"] != "back" else "forward"
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["racers"][0]["finish_tick"] += 4
    tampered_replays.append(tampered)
    tampered = copy.deepcopy(execution.replay)
    tampered["unexpected_public_field"] = True
    tampered_replays.append(tampered)

    for tampered in tampered_replays:
        tampered.pop("final_state_sha256")
        tampered["final_state_sha256"] = hashlib.sha256(canonical_json_bytes(tampered)).hexdigest()
        with pytest.raises(LiveLabyrinthError):
            verify_live_replay(tampered)


@pytest.mark.asyncio
async def test_stale_plan_is_only_an_invalid_wait_for_its_racer() -> None:
    choices = _shortest_choices()
    providers = {
        "participant_0": ScriptedMazeProvider(choices, stale=True),
        "participant_1": ScriptedMazeProvider(choices),
        "participant_2": ScriptedMazeProvider(choices),
    }
    execution = await run_live_labyrinth_race(
        episode_id="ep_live_labyrinth_stale",
        entrants=_entrants(),
        providers=providers,
        max_provider_calls=180,
    )
    racers = {value["participant_id"]: value for value in execution.replay["racers"]}
    # The fixture's first stale id happens to match sequence zero; subsequent windows fail closed.
    assert racers["participant_0"]["invalid_decisions"] == (
        len(providers["participant_0"].requests) - 1
    )
    assert racers["participant_0"]["finished"] is False
    assert racers["participant_1"]["finished"] is True
    assert racers["participant_2"]["finished"] is True


@pytest.mark.asyncio
async def test_service_returns_public_entrants_and_runs_cleanup_once() -> None:
    choices = _shortest_choices()
    providers = {
        participant_id: ScriptedMazeProvider(choices)
        for participant_id in ("participant_0", "participant_1", "participant_2")
    }
    cleaned = 0

    async def cleanup() -> None:
        nonlocal cleaned
        cleaned += 1

    service = LiveLabyrinthService()
    created = await service.create(
        entrants=_entrants(), providers=providers, max_provider_calls=180, cleanup=cleanup
    )
    for _ in range(200):
        status = await service.status(created["episode_id"])
        if status["state"] != "queued" and status["state"] != "running":
            break
        await asyncio.sleep(0)
    assert status["state"] == "completed"
    assert [value["display_name"] for value in status["entrants"]] == ["Sol", "Terra", "Luna"]
    assert cleaned == 1


@pytest.mark.asyncio
async def test_service_reports_a_safe_credential_failure_instead_of_a_tick_zero_success() -> None:
    service = LiveLabyrinthService()
    created = await service.create(
        entrants=_entrants(),
        providers={
            participant_id: FailedMazeProvider()
            for participant_id in ("participant_0", "participant_1", "participant_2")
        },
        max_provider_calls=3,
    )
    for _ in range(20):
        status = await service.status(created["episode_id"])
        if status["state"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0)
    assert status["state"] == "failed"
    assert status["failure"] == "live_provider_credential_rejected"


@pytest.mark.asyncio
async def test_service_exposes_only_a_safe_authority_observer_frame() -> None:
    choices = _shortest_choices()
    service = LiveLabyrinthService()
    created = await service.create(
        entrants=_entrants(),
        providers={
            participant_id: ScriptedMazeProvider(choices)
            for participant_id in ("participant_0", "participant_1", "participant_2")
        },
        max_provider_calls=180,
    )
    episode_id = str(created["episode_id"])
    initial = await service.observer(episode_id)
    assert initial is not None
    assert initial["tick"] == 0
    assert initial["map"]["rows"]
    assert initial["racers"][0]["path"] == [initial["map"]["start"]]
    assert "model" not in initial["racers"][0]
    assert "provider" not in initial["racers"][0]
    initial_feed = await service.spectator_frames(episode_id)
    assert initial_feed["cursor"] >= 1
    assert initial_feed["reset_required"] is False
    assert initial_feed["frames"]
    assert initial_feed["frames"][0]["sequence"] == 1
    assert initial_feed["frames"][0]["frame"]["tick"] == 0

    for _ in range(200):
        status = await service.status(episode_id)
        if status["state"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0)
    latest = await service.observer(episode_id)
    assert latest is not None
    assert latest["provider_calls"] > 0
    assert status["observer"] == {"available": True, "tick": latest["tick"]}
    rendered = canonical_json_bytes(latest).decode("utf-8").casefold()
    for protected in ("navigation_memory", "scratchpad", "raw_output", "prompt"):
        assert protected not in rendered
    terminal_feed = await service.spectator_frames(episode_id, after_sequence=0)
    assert terminal_feed["cursor"] == terminal_feed["frames"][-1]["sequence"]
    assert terminal_feed["frames"][-1]["frame"] == latest
    assert all(
        len(racer["path"]) <= MAX_LIVE_SPECTATOR_PATH_CELLS
        for item in terminal_feed["frames"]
        for racer in item["frame"]["racers"]
    )
    terminal_rendered = canonical_json_bytes(terminal_feed).decode("utf-8").casefold()
    for protected in (
        "episode_id",
        "navigation_memory",
        "scratchpad",
        "raw_output",
        "prompt",
    ):
        assert protected not in terminal_rendered
    assert '"model"' not in terminal_rendered
    assert '"provider"' not in terminal_rendered


@pytest.mark.asyncio
async def test_service_spectator_cursor_resets_after_bounded_frame_overflow() -> None:
    service = LiveLabyrinthService()
    created = await service.create(
        entrants=_entrants(),
        providers={
            participant_id: ScriptedMazeProvider(["wait"] * (MAX_LIVE_SPECTATOR_FRAMES + 2))
            for participant_id in ("participant_0", "participant_1", "participant_2")
        },
        max_provider_calls=MAX_LIVE_SPECTATOR_FRAMES + 1,
    )
    episode_id = str(created["episode_id"])
    for _ in range(MAX_LIVE_SPECTATOR_FRAMES * 4):
        status = await service.status(episode_id)
        if status["state"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0)
    assert status["state"] == "completed"

    overflowed = await service.spectator_frames(episode_id, after_sequence=0)
    assert overflowed["reset_required"] is True
    assert len(overflowed["frames"]) == 1
    assert overflowed["frames"][0]["sequence"] == overflowed["cursor"]
    overflowed["frames"][0]["frame"]["map"]["rows"][0] = "tampered"

    fresh = await service.spectator_frames(episode_id, after_sequence=0)
    assert fresh["frames"][0]["frame"]["map"]["rows"][0] != "tampered"


def test_verifier_rejects_public_protected_material() -> None:
    replay = {
        "schema_version": "worldarena/live-labyrinth-run-replay/1",
        "task_id": LIVE_TASK_ID,
        "protocol_version": "maze-task-plan-v1",
        "map": {"rows": []},
        "final_state_sha256": "not-a-valid-hash",
        "scratchpad": "do not publish",
    }
    with pytest.raises(LiveLabyrinthError):
        verify_live_replay(replay)
