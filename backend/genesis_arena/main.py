from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .arena.simulation_jobs import (
    ReplayBundle,
    ReplaySummary,
    SimulationJob,
    SimulationJobManager,
    SimulationRequest,
)
from .arena_api import arena_socket
from .config import REPOSITORY_ROOT, Settings
from .duel.api import router as duel_router
from .duel.match_service import default_duel_match_service
from .embodiment.api import router as embodiment_router
from .embodiment.crossroads_conquest import CachedCrossroadsShowcase, CrossroadsShowcaseError
from .embodiment.dashboard import mount_built_dashboard
from .embodiment.duel.live_runtime import default_duel_series_service
from .embodiment.lab.benchmarks import BenchmarkStore
from .embodiment.lab.game_authority import GameAuthorityGateway
from .embodiment.lab.game_runs import GenericLabRunService
from .embodiment.lab.publications import PublicReplayStore
from .embodiment.lab.service import LabRunService
from .embodiment.lab_api import public_router as public_lab_router
from .embodiment.lab_api import router as lab_router
from .embodiment.labyrinth_run import CachedLabyrinthRun
from .embodiment.live_runtime import default_episode_service
from .embodiment.presentation.preview_ingress import (
    InternalParticipantPreviewIngress,
    internal_preview_router,
)
from .embodiment.readiness import PilotReadinessStore
from .embodiment.rts_showcase import CachedRtsShowcase
from .embodiment.solo_showcase import CachedSoloShowcase
from .embodiment.transport import ManagedWebSocketEndpoint
from .embodiment.trio_games.live_runtime import default_trio_series_service
from .lab_auth import LabAuthService, LabAuthSettings
from .lab_auth_api import (
    create_lab_auth_router,
    create_lab_operator_requirement,
    install_lab_auth_exception_handlers,
)
from .models import Observation, SimulationConfig
from .orchestrator import Orchestrator

settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.orchestrator = Orchestrator(settings)
    app.state.simulation_jobs = SimulationJobManager(settings)
    app.state.duel_matches = default_duel_match_service(
        port=settings.port,
        runs_dir=settings.runs_dir,
        godot_executable=settings.godot_executable,
        godot_project_path=settings.godot_project_path,
    )
    app.state.embodiment_gateway = ManagedWebSocketEndpoint()
    app.state.embodiment_preview_ingress = InternalParticipantPreviewIngress()
    app.state.embodiment_readiness = PilotReadinessStore(settings.embodiment_readiness_path)
    # A checked-in authority-verified replay/video is reused for the prominent judge path.
    # Starting a dashboard session therefore never spends time or resources re-running the demo.
    app.state.embodiment_rts_showcase = CachedRtsShowcase.load(REPOSITORY_ROOT)
    app.state.embodiment_labyrinth_showcase = CachedLabyrinthRun.load(REPOSITORY_ROOT)
    app.state.embodiment_solo_showcase = CachedSoloShowcase.load(REPOSITORY_ROOT)
    try:
        app.state.embodiment_crossroads_showcase = CachedCrossroadsShowcase.load(REPOSITORY_ROOT)
    except CrossroadsShowcaseError:
        # Crossroads is an optional future showcase. Its absent cache must never prevent the
        # checked-in Mini RTS golden path or the rest of the local dashboard from starting.
        app.state.embodiment_crossroads_showcase = None
    app.state.embodiment_episodes = default_episode_service(
        repository_root=REPOSITORY_ROOT,
        godot_executable=settings.godot_executable,
        godot_project_path=settings.godot_project_path,
        gateway_port=settings.port,
        endpoint=app.state.embodiment_gateway,
        preview_ingress=app.state.embodiment_preview_ingress,
        provider_timeout_s=settings.decision_timeout_seconds,
        runs_dir=settings.runs_dir,
        ffmpeg_executable=settings.ffmpeg_executable,
    )
    app.state.embodiment_series = default_duel_series_service(
        repository_root=REPOSITORY_ROOT,
        godot_executable=settings.godot_executable,
        godot_project_path=settings.godot_project_path,
        gateway_port=settings.port,
        endpoint=app.state.embodiment_gateway,
        provider_timeout_s=settings.decision_timeout_seconds,
        runs_dir=settings.runs_dir,
        ffmpeg_executable=settings.ffmpeg_executable,
        preview_ingress=app.state.embodiment_preview_ingress,
    )
    app.state.embodiment_trio_series = default_trio_series_service(
        repository_root=REPOSITORY_ROOT,
        godot_executable=settings.godot_executable,
        godot_project_path=settings.godot_project_path,
        gateway_port=settings.port,
        endpoint=app.state.embodiment_gateway,
        provider_timeout_s=settings.decision_timeout_seconds,
        runs_dir=settings.runs_dir,
        ffmpeg_executable=settings.ffmpeg_executable,
        preview_ingress=app.state.embodiment_preview_ingress,
    )
    app.state.lab_game_runs = GenericLabRunService(
        runs_dir=settings.runs_dir,
        authority=GameAuthorityGateway(
            solo=app.state.embodiment_episodes,
            paired=app.state.embodiment_series,
            trio=app.state.embodiment_trio_series,
        ),
    )
    # Live Labyrinth Run is intentionally a separate v1 lifecycle from the cached showcase
    # and the keyless trio demo service.
    from .embodiment.live_labyrinth import LiveLabyrinthService
    from .embodiment.live_labyrinth_media import LiveLabyrinthBroadcastRenderer

    app.state.embodiment_live_labyrinth = LiveLabyrinthService(
        render_video=LiveLabyrinthBroadcastRenderer(
            output_root=settings.runs_dir / "live-labyrinth",
            godot_executable=settings.godot_executable,
            godot_project_path=settings.godot_project_path,
            ffmpeg_executable=settings.ffmpeg_executable,
        )
    )
    # Lab records own only safe contracts/projections.  The live authority remains responsible
    # for provider credentials and private controller state.
    app.state.lab_runs = LabRunService(
        runs_dir=settings.runs_dir,
        live_labyrinth=app.state.embodiment_live_labyrinth,
    )
    app.state.lab_benchmarks = BenchmarkStore(runs_dir=settings.runs_dir)
    app.state.lab_public_replays = PublicReplayStore(runs_dir=settings.runs_dir)
    # The Lab has an explicit loopback-only local mode for the desktop launcher
    # and fails closed if a production magic-link configuration is incomplete.
    # No provider credential is ever part of this identity state.
    app.state.lab_auth = LabAuthService(LabAuthSettings.from_environ())
    try:
        yield
    finally:
        app.state.lab_auth.close()
        await app.state.embodiment_trio_series.aclose()
        await app.state.embodiment_series.aclose()
        await app.state.embodiment_episodes.aclose()
        app.state.embodiment_preview_ingress.close()
        await app.state.duel_matches.aclose()


app = FastAPI(
    title="WorldArena Controller",
    version="0.2.0",
    description=(
        "Validated model-planning bridge for survival_v1 and the simultaneous WorldArena."
    ),
    lifespan=lifespan,
)


def _lab_auth_service(request: Request) -> LabAuthService:
    service = getattr(request.app.state, "lab_auth", None)
    if not isinstance(service, LabAuthService):
        raise RuntimeError("Lab authentication is not configured")
    return service


install_lab_auth_exception_handlers(app)
app.include_router(create_lab_auth_router(_lab_auth_service))
app.include_router(duel_router)
app.include_router(embodiment_router)
app.include_router(
    lab_router,
    dependencies=[Depends(create_lab_operator_requirement(_lab_auth_service))],
)
app.include_router(public_lab_router)
app.include_router(internal_preview_router)


@app.websocket("/ws/embodiment/{ticket}")
async def embodiment_socket(ticket: str, websocket: WebSocket) -> None:
    await app.state.embodiment_gateway.handle(ticket, websocket)


@app.get("/health")
async def health() -> Dict[str, object]:
    orchestrator: Orchestrator = app.state.orchestrator
    return {
        "status": "ok",
        "brain": orchestrator.provider_name,
        "catalog_version": orchestrator.catalog.version,
        "protocols": [
            "genesis-arena/0.1",
            "world-arena/0.2",
            "world-arena/0.3",
            "world-arena/0.4",
        ],
    }


@app.get("/api/catalog")
async def catalog() -> Dict[str, Any]:
    orchestrator: Orchestrator = app.state.orchestrator
    return {
        "version": orchestrator.catalog.version,
        "actions": orchestrator.catalog.actions,
    }


@app.get("/api/state")
async def state() -> Dict[str, object]:
    orchestrator: Orchestrator = app.state.orchestrator
    return orchestrator.state()


def _simulation_jobs() -> SimulationJobManager:
    return app.state.simulation_jobs


@app.post("/api/simulations", response_model=SimulationJob, status_code=202)
async def create_simulation(request: SimulationRequest) -> SimulationJob:
    return _simulation_jobs().create(request)


@app.get("/api/simulations", response_model=list[SimulationJob])
async def list_simulations(limit: int = Query(default=50, ge=1, le=100)) -> list[SimulationJob]:
    return _simulation_jobs().list_jobs(limit)


@app.get("/api/simulations/{job_id}", response_model=SimulationJob)
async def get_simulation(job_id: str) -> SimulationJob:
    try:
        job = _simulation_jobs().get(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="simulation not found") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="simulation not found")
    return job


@app.get("/api/replays", response_model=list[ReplaySummary])
async def list_replays(limit: int = Query(default=50, ge=1, le=100)) -> list[ReplaySummary]:
    return _simulation_jobs().list_replays(limit)


@app.get("/api/replays/{replay_id}", response_model=ReplaySummary)
async def get_replay(replay_id: str) -> ReplaySummary:
    try:
        replay = _simulation_jobs().get_replay(replay_id, full=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="replay not found") from exc
    if replay is None:
        raise HTTPException(status_code=404, detail="replay not found")
    assert isinstance(replay, ReplaySummary)
    return replay


@app.get("/api/replays/{replay_id}/bundle", response_model=ReplayBundle)
async def get_replay_bundle(replay_id: str) -> ReplayBundle:
    try:
        replay = _simulation_jobs().get_replay(replay_id, full=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="replay not found") from exc
    if replay is None:
        raise HTTPException(status_code=404, detail="replay not found")
    assert isinstance(replay, ReplayBundle)
    return replay


@app.websocket("/ws/world")
async def world_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    orchestrator: Orchestrator = app.state.orchestrator
    await websocket.send_json(
        {
            "type": "connected",
            "protocol": "genesis-arena/0.1",
            "brain": orchestrator.provider_name,
        }
    )

    try:
        while True:
            message = json.loads(await websocket.receive_text())
            message_type = message.get("type")

            if message_type == "hello":
                await websocket.send_json(
                    {
                        "type": "ready",
                        "brain": orchestrator.provider_name,
                        "enabled_actions": orchestrator.catalog.enabled_names,
                    }
                )
                continue

            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if message_type == "configure":
                try:
                    config = SimulationConfig.model_validate(message)
                    models = orchestrator.configure(config)
                except (ValidationError, ValueError) as exc:
                    details = exc.errors() if isinstance(exc, ValidationError) else str(exc)
                    await websocket.send_json(
                        {
                            "type": "error",
                            "error": "invalid simulation configuration",
                            "details": details,
                        }
                    )
                    continue
                await websocket.send_json(
                    {"type": "configured", "agents": models, "brain": orchestrator.provider_name}
                )
                continue

            if message_type != "observation":
                await websocket.send_json(
                    {"type": "error", "error": f"unsupported message type: {message_type!r}"}
                )
                continue

            try:
                observation = Observation.model_validate(message)
            except ValidationError as exc:
                await websocket.send_json(
                    {"type": "error", "error": "invalid observation", "details": exc.errors()}
                )
                continue

            await websocket.send_json(
                {
                    "type": "thinking",
                    "agent_id": observation.agent_id,
                    "turn": observation.turn,
                    "brain": orchestrator.provider_for(observation.agent_id),
                }
            )
            command = await orchestrator.decide(observation)
            await websocket.send_json(command.model_dump(mode="json"))
    except (WebSocketDisconnect, json.JSONDecodeError):
        return


@app.websocket("/ws/arena")
async def arena_v1_socket(websocket: WebSocket) -> None:
    """WorldArena v0.3 simultaneous three-faction protocol (with v0.2 support)."""

    await arena_socket(websocket, settings)


# Static presentation is mounted after every API and WebSocket route. A source checkout without a
# completed Vite build remains a valid API server; `pnpm build` makes the local dashboard available
# from the same origin without introducing a second credential-handling process.
mount_built_dashboard(app, REPOSITORY_ROOT / "dashboard" / "dist")


def run() -> None:
    uvicorn.run(
        "genesis_arena.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        # Duel's one-use Godot attachment capability is carried in a WebSocket path. Never allow
        # the server's generic request logger to persist that protected path.
        access_log=False,
    )


if __name__ == "__main__":
    run()
