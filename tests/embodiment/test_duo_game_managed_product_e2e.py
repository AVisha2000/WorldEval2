from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from genesis_arena.embodiment.api import router
from genesis_arena.embodiment.duel.live_runtime import default_duel_series_service
from genesis_arena.embodiment.duo_games.catalog import DUO_GAME_CATALOG
from genesis_arena.embodiment.transport import ManagedWebSocketEndpoint

ROOT = Path(__file__).resolve().parents[2]
GODOT = Path("/Applications/Godot.app/Contents/MacOS/Godot")


@pytest.mark.skipif(not GODOT.is_file(), reason="pinned local Godot build is unavailable")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_id",
    (
        "duo-checkpoint-race-v0",
        "duo-relay-control-v0",
        "duo-spar-v0",
        "rts-skirmish-v0",
    ),
)
async def test_api_demo_provider_managed_v2_sealed_archive_survives_restart(
    tmp_path: Path, task_id: str
) -> None:
    endpoint = ManagedWebSocketEndpoint()
    app = FastAPI()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    service = default_duel_series_service(
        repository_root=ROOT,
        godot_executable=GODOT,
        godot_project_path=ROOT / "godot",
        gateway_port=port,
        endpoint=endpoint,
        provider_timeout_s=5,
        runs_dir=tmp_path,
    )
    app.state.embodiment_series = service
    app.include_router(router)

    @app.websocket("/ws/embodiment/{ticket}")
    async def attach(ticket: str, websocket: WebSocket) -> None:
        await endpoint.handle(ticket, websocket)

    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0)
    game = DUO_GAME_CATALOG[task_id]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.post(
                "/api/embodiment/series",
                json={
                    "task_id": task_id,
                    "seed": 2718,
                    "max_live_provider_calls": 2160,
                    "entrants": [
                        {"provider": "demo", "model": game.models[0]},
                        {"provider": "demo", "model": game.models[1]},
                    ],
                },
            )
            assert response.status_code == 202
            assert "api_key" not in response.text
            series_id = response.json()["series_id"]
            # RTS seals two full 1,200-tick legs and captures both participant frames at every
            # boundary.  On a modest development machine that is valid but can exceed the
            # one-minute allowance used by the shorter arena games.
            poll_attempts = 720 if task_id == "rts-skirmish-v0" else 240
            for _ in range(poll_attempts):
                status_response = await client.get(f"/api/embodiment/series/{series_id}")
                assert status_response.status_code == 200
                status = status_response.json()
                if status["state"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.25)
            if status["state"] != "completed":
                status["internal_failure_type"] = service._records[series_id].failure_type
            assert status["state"] == "completed", status
            assert status["task_id"] == task_id
            assert status["config"]["task_id"] == task_id

            evaluation = (await client.get(f"/api/embodiment/series/{series_id}/evaluation")).json()
            assert evaluation["series_id"] == series_id
            assert len(evaluation["legs"]) == 2
            assert all(leg["run"]["task_id"] == task_id for leg in evaluation["legs"])
            assert all(leg["projection_sha256"] for leg in evaluation["legs"])
            assert "position_mt" not in repr(evaluation)
            if task_id == "duo-relay-control-v0":
                aggregates = [
                    leg["evaluation"]["metrics"]["participant_aggregates"]["value"]
                    for leg in evaluation["legs"]
                ]
                assert sum(
                    participant["control_ticks"]
                    for leg in aggregates
                    for participant in leg.values()
                ) > 0
                assert all(
                    leg["result"]["terminal"]["reason"] == "hold_target"
                    for leg in evaluation["legs"]
                )

            for participant_id in ("participant_0", "participant_1"):
                frame = await client.get(
                    f"/api/embodiment/series/{series_id}/participants/{participant_id}/frame"
                )
                assert frame.status_code == 200
                assert frame.headers["content-type"] == "image/png"
                assert frame.headers["x-participant-id"] == participant_id
                assert frame.headers["x-leg-index"] == "1"
                assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")
                assert b"prompt" not in frame.content

            replay = await client.get(f"/api/embodiment/series/{series_id}/replay")
            assert replay.status_code == 200
            assert replay.headers["x-content-sha256"]
            assert b"observation_json_base64" not in replay.content
            archive_poll_attempts = 1_200 if task_id == "rts-skirmish-v0" else 80
            archive_path = f"/api/embodiment/series/{series_id}/archive"
            # Archive rendering can keep this test alive for minutes. Use a dedicated
            # no-keepalive client so polling never depends on the long-lived launch socket after
            # Uvicorn's normal keep-alive close; every HTTP failure still fails the test.
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                limits=httpx.Limits(max_keepalive_connections=0),
                timeout=30,
            ) as archive_client:
                for _ in range(archive_poll_attempts):
                    archive_response = await archive_client.get(archive_path)
                    assert archive_response.status_code == 200
                    archive = archive_response.json()
                    if archive["evidence"]["state"] != "saving":
                        break
                    await asyncio.sleep(0.05)
            assert archive["evidence"]["state"] == "ready"
            assert archive["native_replay"] == {
                "state": "unavailable",
                "reason": "participant_video_not_configured",
            }

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
            restarted_status = await restarted.status(series_id)
            assert restarted_status["state"] == "completed"
            assert restarted_status["task_id"] == task_id
            assert (await restarted.evaluation(series_id))["series_id"] == series_id
            assert (await restarted.replay(series_id)).series_id == series_id
        finally:
            await restarted.aclose()
    finally:
        await service.aclose()
        server.should_exit = True
        await server_task
        listener.close()
