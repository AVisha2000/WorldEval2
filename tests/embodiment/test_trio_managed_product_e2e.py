from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from genesis_arena.embodiment.api import router
from genesis_arena.embodiment.presentation.preview_ingress import (
    InternalParticipantPreviewIngress,
    internal_preview_router,
)
from genesis_arena.embodiment.transport import ManagedWebSocketEndpoint
from genesis_arena.embodiment.trio_games.live_runtime import default_trio_series_service
from genesis_arena.embodiment.trio_games.scheduling import TRIO_DEMO_ENTRANTS

ROOT = Path(__file__).resolve().parents[2]
GODOT = Path("/Applications/Godot.app/Contents/MacOS/Godot")
PARTICIPANTS = ("participant_0", "participant_1", "participant_2")
ENTRANTS = [
    {"provider": "demo", "model": entrant.model} for entrant in TRIO_DEMO_ENTRANTS
]


@pytest.mark.skipif(not GODOT.is_file(), reason="pinned local Godot build is unavailable")
@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", ("trio-relay-v0", "trio-free-for-all-v0"))
async def test_api_demo_trio_managed_v3_three_leg_archive_survives_restart(
    tmp_path: Path, task_id: str
) -> None:
    endpoint = ManagedWebSocketEndpoint()
    preview_ingress = InternalParticipantPreviewIngress()
    app = FastAPI()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    service = default_trio_series_service(
        repository_root=ROOT,
        godot_executable=GODOT,
        godot_project_path=ROOT / "godot",
        gateway_port=port,
        endpoint=endpoint,
        provider_timeout_s=5,
        runs_dir=tmp_path,
        preview_ingress=preview_ingress,
    )
    app.state.embodiment_trio_series = service
    app.state.embodiment_preview_ingress = preview_ingress
    app.include_router(router)
    app.include_router(internal_preview_router)

    @app.websocket("/ws/embodiment/{ticket}")
    async def attach(ticket: str, websocket: WebSocket) -> None:
        await endpoint.handle(ticket, websocket)

    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0)

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.post(
                "/api/embodiment/trio-series",
                json={
                    "task_id": task_id,
                    "seed": 314159,
                    "max_provider_calls": 1080,
                    "entrants": ENTRANTS,
                },
            )
            assert response.status_code == 202
            assert "api_key" not in response.text
            series_id = response.json()["series_id"]
            status: dict[str, object] = {}
            for _ in range(480):
                status_response = await client.get(
                    f"/api/embodiment/trio-series/{series_id}"
                )
                assert status_response.status_code == 200
                status = status_response.json()
                if status["state"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.25)
            if status.get("state") != "completed":
                status["internal_failure_type"] = service._records[series_id].failure_type
                status["internal_failure_detail"] = service._records[
                    series_id
                ].failure_detail
            assert status["state"] == "completed", status
            assert status["task_id"] == task_id
            assert status["protocol_version"] == "llm-controller/0.3.0"
            assert status["rotations"] == 3

            result_response = await client.get(
                f"/api/embodiment/trio-series/{series_id}/result"
            )
            assert result_response.status_code == 200
            result = result_response.json()
            assert result["task_id"] == task_id
            assert len(result["legs"]) == 3
            assert [leg["leg_index"] for leg in result["legs"]] == [0, 1, 2]
            assert all(leg["terminal"]["placements"] for leg in result["legs"])
            assert all(leg["replay_sha256"] for leg in result["legs"])

            evaluation_response = await client.get(
                f"/api/embodiment/trio-series/{series_id}/evaluation"
            )
            assert evaluation_response.status_code == 200
            evaluation = evaluation_response.json()
            assert evaluation["scope"] == "trio_game_series"
            assert evaluation["series"]["leg_count"] == 3
            assert evaluation["series"]["seat_rotations_complete"] is True
            assert evaluation["cyclic_normalization"][
                "each_entrant_uses_each_seat_once"
            ] is True
            assert set(evaluation["entrants"]) == {"sol", "luna", "terra"}

            timeline_response = await client.get(
                f"/api/embodiment/trio-series/{series_id}/timeline"
            )
            assert timeline_response.status_code == 200
            timeline = timeline_response.json()
            assert timeline["series_id"] == series_id
            assert timeline["events"]
            assert {event["leg_index"] for event in timeline["events"]} <= {0, 1, 2}

            for participant_id in PARTICIPANTS:
                frame = await client.get(
                    f"/api/embodiment/trio-series/{series_id}/participants/"
                    f"{participant_id}/frame"
                )
                assert frame.status_code == 200
                assert frame.headers["content-type"] == "image/png"
                assert frame.headers["x-frame-state"] == "finished"
                assert frame.headers["x-participant-id"] == participant_id
                assert frame.headers["x-leg-index"] == "2"
                assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")

            replay = await client.get(
                f"/api/embodiment/trio-series/{series_id}/replay"
            )
            assert replay.status_code == 200
            assert replay.headers["x-content-sha256"]
            for protected in (
                b"observation_json_base64",
                b"raw_output_base64",
                b"system_prompt",
                b"api_key",
                b"position_mt",
                b"spectator",
            ):
                assert protected not in replay.content

            # Sealing three legs includes asynchronous evidence hashing and durable archive
            # publication. Allow the same one-minute completion budget used by managed runs.
            for _ in range(1_200):
                archive_response = await client.get(
                    f"/api/embodiment/trio-series/{series_id}/archive"
                )
                assert archive_response.status_code == 200
                archive = archive_response.json()
                if archive["evidence"]["state"] != "saving":
                    break
                await asyncio.sleep(0.05)
            assert archive["evidence"]["state"] == "ready"
            assert archive["evaluation"]["state"] == "ready"
            assert archive["timeline"]["state"] == "ready"
            assert archive["result"]["state"] == "ready"
            assert archive["native_replay"] == {
                "state": "unavailable",
                "reason": "participant_video_not_configured",
            }
            for leg_index in (0, 1, 2):
                for participant_id in PARTICIPANTS:
                    native = await client.get(
                        f"/api/embodiment/trio-series/{series_id}/legs/{leg_index}/"
                        f"participants/{participant_id}/video"
                    )
                    assert native.status_code == 404

        await service.aclose()
        restarted = default_trio_series_service(
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
            assert (await restarted.result(series_id))["task_id"] == task_id
            assert (await restarted.evaluation(series_id))["task_id"] == task_id
            assert (await restarted.timeline(series_id))["series_id"] == series_id
            assert (await restarted.replay(series_id)).series_id == series_id
        finally:
            await restarted.aclose()
    finally:
        await service.aclose()
        preview_ingress.close()
        server.should_exit = True
        await server_task
        listener.close()
