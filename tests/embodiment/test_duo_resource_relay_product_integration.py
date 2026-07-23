from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from genesis_arena.embodiment.duel.evidence import _evaluate_duo_game_replay
from genesis_arena.embodiment.duel.live_runtime import default_duel_series_service
from genesis_arena.embodiment.duel.managed import _winner_participant
from genesis_arena.embodiment.duo_games.catalog import DUO_GAME_CATALOG
from genesis_arena.embodiment.transport import ManagedWebSocketEndpoint

ROOT = Path(__file__).resolve().parents[2]
GODOT = Path("/Applications/Godot.app/Contents/MacOS/Godot")
TASK_ID = "duo-resource-relay-v0"


def _summary(
    participant_id: str,
    outcome: str,
    *,
    score: int,
    deposits: int,
    gathered: int,
) -> dict[str, object]:
    return {
        "kind": "duo_resource_relay_participant_summary",
        "participant_ids": [participant_id],
        "data": {
            "task_id": TASK_ID,
            "completion_tick": 600,
            "terminal_outcome": "win",
            "terminal_reason": "objective_target",
            "participant_id": participant_id,
            "outcome": outcome,
            "decision_windows": 60,
            "fallback_windows": 0,
            "resources_gathered": gathered,
            "deposits": deposits,
            "objective_score": score,
            "builds_completed": min(1, deposits),
            "defend_ticks": 0,
            "hits_landed": 0,
            "hits_received": 0,
            "knockouts": 0,
            "resources_dropped": 0,
            "dash_uses": 0,
            "guard_ticks": 0,
        },
    }


def test_resource_relay_sealed_typed_events_drive_safe_evaluation() -> None:
    replay = {
        "config": {"task_id": TASK_ID},
        "steps": [
            {
                "result": {
                    "public_events": [
                        {
                            "kind": "duo_game_completed",
                            "participant_ids": ["participant_0", "participant_1"],
                            "data": {
                                "task_id": TASK_ID,
                                "completion_tick": 600,
                                "terminal_outcome": "win",
                                "terminal_reason": "objective_target",
                                "winner_id": "participant_0",
                            },
                        },
                        _summary("participant_0", "win", score=300, deposits=3, gathered=3),
                        _summary("participant_1", "loss", score=200, deposits=2, gathered=2),
                    ]
                }
            }
        ],
    }
    value = _evaluate_duo_game_replay(replay)
    assert value["task_id"] == TASK_ID
    assert value["completion"] == {
        "tick": 600,
        "outcome": "win",
        "reason": "objective_target",
    }
    assert value["participants"]["participant_0"]["objective_score"] == 300
    serialized = repr(value).casefold()
    for forbidden in (
        "position_mt",
        "coordinate",
        "health",
        "energy",
        "spectator",
        "prompt",
        "raw_output",
        "credential",
    ):
        assert forbidden not in serialized


def test_resource_relay_redundant_typed_terminal_events_bind_one_winner() -> None:
    replay = {
        "steps": [
            {
                "result": {
                    "public_events": [
                        {"kind": "episode_won", "data": {"winner": "participant_0"}},
                        {
                            "kind": "duo_game_completed",
                            "data": {"winner_id": "participant_0"},
                        },
                    ]
                }
            }
        ]
    }
    assert _winner_participant(replay) == "participant_0"
    replay["steps"][0]["result"]["public_events"][1]["data"]["winner_id"] = "participant_1"
    with pytest.raises(ValueError, match="multiple winner"):
        _winner_participant(replay)


@pytest.mark.skipif(not GODOT.is_file(), reason="pinned local Godot build is unavailable")
@pytest.mark.asyncio
async def test_resource_relay_two_leg_demo_managed_hybrid_evaluation_and_archive(
    tmp_path: Path,
) -> None:
    endpoint = ManagedWebSocketEndpoint()
    app = FastAPI()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]

    @app.websocket("/ws/embodiment/{ticket}")
    async def attach(ticket: str, websocket: WebSocket) -> None:
        await endpoint.handle(ticket, websocket)

    service = default_duel_series_service(
        repository_root=ROOT,
        godot_executable=GODOT,
        godot_project_path=ROOT / "godot",
        gateway_port=port,
        endpoint=endpoint,
        provider_timeout_s=5,
        runs_dir=tmp_path,
    )
    execution_failures: list[str] = []
    original_executor = service._executor

    async def diagnostic_executor(spec, credentials, cancel_event):
        try:
            return await original_executor(spec, credentials, cancel_event)
        except Exception as error:
            chain = []
            current: BaseException | None = error
            while current is not None:
                chain.append(
                    f"{type(current).__name__}:{getattr(current, 'code', str(current))}"
                )
                current = current.__cause__
            execution_failures.append(" <- ".join(chain))
            raise

    service._executor = diagnostic_executor
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0)
    game = DUO_GAME_CATALOG[TASK_ID]
    try:
        created = await service.create(
            entrants=(
                {"provider": "demo", "model": game.models[0]},
                {"provider": "demo", "model": game.models[1]},
            ),
            seed=8675309,
            max_live_provider_calls=2160,
            task_id=TASK_ID,
        )
        series_id = str(created["series_id"])
        # Two rendered 1,200-tick legs can exceed one minute on a modest or thermally
        # constrained development machine even though the authority is still progressing.
        for _ in range(600):
            status = await service.status(series_id)
            if status["state"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.2)
        if status["state"] != "completed":
            status = {
                **status,
                "internal_failure_type": service._records[series_id].failure_type,
                "internal_failure_code": execution_failures[-1] if execution_failures else None,
            }
        assert status["state"] == "completed", status
        assert status["task_id"] == TASK_ID
        assert status["config"]["certification"] == {
            "eligible": False,
            "reason": "demo_provider",
        }

        evaluation = await service.evaluation(series_id)
        assert evaluation["series_id"] == series_id
        assert len(evaluation["legs"]) == 2
        assert all(leg["run"]["task_id"] == TASK_ID for leg in evaluation["legs"])
        assert all(leg["projection_sha256"] for leg in evaluation["legs"])
        assert "position_mt" not in repr(evaluation)
        aggregates = [
            leg["evaluation"]["metrics"]["participant_aggregates"]["value"]
            for leg in evaluation["legs"]
        ]
        assert sum(
            participant["resources_gathered"]
            for leg in aggregates
            for participant in leg.values()
        ) > 0
        assert sum(
            participant["deposits"] for leg in aggregates for participant in leg.values()
        ) > 0
        assert aggregates[0]["participant_0"] == aggregates[1]["participant_1"]
        assert aggregates[0]["participant_1"] == aggregates[1]["participant_0"]
        assert all(
            participant["fallback_windows"] == 0
            for leg in aggregates
            for participant in leg.values()
        )
        public = await service.replay(series_id)
        assert public.layer == "public"
        assert b"authority_replay" not in public.bundle_bytes
        assert b"observation_json_base64" not in public.bundle_bytes
        # Evidence persistence is deliberately asynchronous and the two-leg resource replay is
        # large enough that canonical validation can take more than the short-game allowance.
        for _ in range(1_200):
            archive = await service.archive_status(series_id)
            if archive["evidence"]["state"] != "saving":
                break
            await asyncio.sleep(0.05)
        assert archive["evidence"]["state"] == "ready"

        for participant_id in ("participant_0", "participant_1"):
            state, frame = await service.participant_frame(series_id, participant_id)
            assert state == "finished"
            assert frame is not None
            assert frame.participant_id == participant_id
            assert frame.leg_index == 1
            assert frame.png.startswith(b"\x89PNG\r\n\x1a\n")

        await service.aclose()
        restarted = default_duel_series_service(
            repository_root=ROOT,
            godot_executable=GODOT,
            godot_project_path=ROOT / "godot",
            gateway_port=port,
            endpoint=ManagedWebSocketEndpoint(),
            provider_timeout_s=5,
            runs_dir=tmp_path,
        )
        try:
            assert (await restarted.status(series_id))["state"] == "completed"
            assert (await restarted.evaluation(series_id))["series_id"] == series_id
            assert (await restarted.replay(series_id)).series_id == series_id
        finally:
            await restarted.aclose()
    finally:
        await service.aclose()
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, 5)
        except asyncio.TimeoutError:
            server.force_exit = True
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
