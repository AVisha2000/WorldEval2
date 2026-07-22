"""Credential-safe local FastAPI surface for live embodiment episodes."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse

from .credentials import SessionCredential
from .crossroads_conquest import CachedCrossroadsShowcase
from .demo_scenarios import demo_scenario
from .duel.service import (
    DuelSeriesEvidenceNotReadyError,
    DuelSeriesNotFoundError,
    DuelSeriesService,
)
from .episode_service import (
    DEMO_PROVIDER,
    EpisodeEvaluationNotReadyError,
    EpisodeNotFoundError,
    EpisodeReplayNotReadyError,
    EpisodeResultNotReadyError,
    EpisodeService,
)
from .labyrinth_run import CachedLabyrinthRun
from .live_labyrinth import (
    DEFAULT_VISION_RANGE_CELLS,
    MAX_FINITE_VISION_RANGE_CELLS,
    MAX_LIVE_PROVIDER_CALLS,
    LiveLabyrinthNotFoundError,
    LiveLabyrinthNotReadyError,
    LiveLabyrinthService,
    LiveMazeEntrant,
)
from .live_runtime import close_provider_adapter, provider_adapter
from .readiness import PilotReadinessStore
from .rts_showcase import CachedRtsShowcase
from .scripted_construction_demo import (
    SCRIPTED_CONSTRUCTION_PROVIDER,
)
from .scripted_solo_demo import is_scripted_solo_demo
from .solo_showcase import CachedSoloShowcase
from .trio_games.common import TRIO_PARTICIPANT_IDS
from .trio_games.service import (
    TrioSeriesNotFoundError,
    TrioSeriesNotReadyError,
    TrioSeriesService,
)

router = APIRouter(tags=["LLM Controller"])
_BODY = Body(...)
_CREATE_FIELDS = frozenset(
    {
        "api_key",
        "maximum_episode_ticks",
        "model",
        "observation_profile",
        "provider",
        "seed",
        "scenario_id",
        "task_id",
    }
)


def _service(request: Request | WebSocket) -> EpisodeService:
    service = getattr(request.app.state, "embodiment_episodes", None)
    if not isinstance(service, EpisodeService):
        raise RuntimeError("Embodiment episode service is not configured")
    return service


def _series_service(request: Request) -> DuelSeriesService:
    service = getattr(request.app.state, "embodiment_series", None)
    if not isinstance(service, DuelSeriesService):
        raise RuntimeError("Embodiment duel series service is not configured")
    return service


def _trio_service(request: Request | WebSocket) -> TrioSeriesService:
    service = getattr(request.app.state, "embodiment_trio_series", None)
    if not isinstance(service, TrioSeriesService):
        raise RuntimeError("Embodiment trio series service is not configured")
    return service


def _live_labyrinth_service(request: Request) -> LiveLabyrinthService:
    service = getattr(request.app.state, "embodiment_live_labyrinth", None)
    if not isinstance(service, LiveLabyrinthService):
        raise RuntimeError("Live labyrinth service is not configured")
    return service


def _readiness(request: Request) -> PilotReadinessStore:
    store = getattr(request.app.state, "embodiment_readiness", None)
    if not isinstance(store, PilotReadinessStore):
        raise RuntimeError("Embodiment readiness store is not configured")
    return store


def _rts_showcase(request: Request) -> CachedRtsShowcase:
    showcase = getattr(request.app.state, "embodiment_rts_showcase", None)
    if not isinstance(showcase, CachedRtsShowcase):
        raise RuntimeError("RTS showcase is not configured")
    return showcase


def _labyrinth_showcase(request: Request) -> CachedLabyrinthRun:
    showcase = getattr(request.app.state, "embodiment_labyrinth_showcase", None)
    if not isinstance(showcase, CachedLabyrinthRun):
        raise RuntimeError("Labyrinth Run showcase is not configured")
    return showcase


def _solo_showcase(request: Request) -> CachedSoloShowcase:
    showcase = getattr(request.app.state, "embodiment_solo_showcase", None)
    if not isinstance(showcase, CachedSoloShowcase):
        raise RuntimeError("Solo showcase is not configured")
    return showcase


def _crossroads_showcase(request: Request) -> CachedCrossroadsShowcase:
    showcase = getattr(request.app.state, "embodiment_crossroads_showcase", None)
    if not isinstance(showcase, CachedCrossroadsShowcase):
        raise HTTPException(status_code=503, detail="Crossroads Conquest showcase is unavailable")
    return showcase


@router.get("/api/embodiment/showcases/rts-skirmish-v0")
async def get_rts_showcase(request: Request, response: Response) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _rts_showcase(request).public_view()


@router.get("/api/embodiment/showcases/rts-skirmish-v0/evaluation")
async def get_rts_showcase_evaluation(request: Request, response: Response) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _rts_showcase(request).public_evaluation()


@router.get("/api/embodiment/showcases/rts-skirmish-v0/video")
async def get_rts_showcase_video(request: Request) -> FileResponse:
    return FileResponse(
        _rts_showcase(request).video_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/embodiment/showcases/trio-maze-race-v0")
async def get_labyrinth_showcase(request: Request, response: Response) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _labyrinth_showcase(request).public_view()


@router.get("/api/embodiment/showcases/trio-maze-race-v0/evaluation")
async def get_labyrinth_showcase_evaluation(
    request: Request, response: Response
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _labyrinth_showcase(request).public_evaluation()


@router.get("/api/embodiment/showcases/trio-maze-race-v0/video")
async def get_labyrinth_showcase_video(request: Request) -> FileResponse:
    return FileResponse(
        _labyrinth_showcase(request).video_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/embodiment/showcases/solo-multi-action-v0")
async def get_solo_showcase(request: Request, response: Response) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600, immutable"
    return _solo_showcase(request).public_view()


@router.get("/api/embodiment/showcases/solo-multi-action-v0/video")
async def get_solo_showcase_video(request: Request) -> FileResponse:
    return FileResponse(
        _solo_showcase(request).video_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "public, max-age=3600, immutable",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/embodiment/showcases/crossroads-conquest-v0")
async def get_crossroads_showcase(request: Request, response: Response) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600, immutable"
    return _crossroads_showcase(request).public_view()


@router.get("/api/embodiment/showcases/crossroads-conquest-v0/evaluation")
async def get_crossroads_showcase_evaluation(
    request: Request, response: Response
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600, immutable"
    return _crossroads_showcase(request).public_evaluation()


@router.get("/api/embodiment/showcases/crossroads-conquest-v0/video")
async def get_crossroads_showcase_video(request: Request) -> FileResponse:
    return FileResponse(
        _crossroads_showcase(request).video_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "public, max-age=3600, immutable",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/embodiment/certification/readiness")
async def get_certification_readiness(request: Request, response: Response) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return _readiness(request).read()


@router.post("/api/embodiment/episodes", status_code=202)
async def create_episode(
    request: Request, response: Response, payload: Any = _BODY
) -> Mapping[str, Any]:
    # Manual validation prevents FastAPI/Pydantic error details from reflecting malformed keys.
    values = _validate_create_payload(payload)
    try:
        created = await _service(request).create(**values)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_embodiment_episode_request"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return created


@router.post("/api/embodiment/maze-races", status_code=202)
async def create_live_maze_race(
    request: Request, response: Response, payload: Any = _BODY
) -> Mapping[str, object]:
    """Start an isolated, concurrent three-controller live maze race."""
    try:
        values = _validate_live_maze_payload(payload)
        credential = SessionCredential(values["api_key"])
        adapters = {
            entrant.participant_id: provider_adapter(values["provider"], credential)
            for entrant in values["entrants"]
        }

        async def cleanup() -> None:
            try:
                await asyncio.gather(
                    *(close_provider_adapter(adapter) for adapter in adapters.values()),
                    return_exceptions=True,
                )
            finally:
                credential.close()

        created = await _live_labyrinth_service(request).create(
            entrants=values["entrants"],
            providers=adapters,
            max_provider_calls=values["max_provider_calls"],
            vision_range_cells=values["vision_range_cells"],
            cleanup=cleanup,
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_live_maze_race_request"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return created


@router.get("/api/embodiment/maze-races/{episode_id}")
async def get_live_maze_race(request: Request, episode_id: str) -> Mapping[str, object]:
    try:
        return await _live_labyrinth_service(request).status(episode_id)
    except LiveLabyrinthNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "live_maze_race_not_found"}) from None


@router.get("/api/embodiment/maze-races/{episode_id}/video")
async def get_live_maze_video(request: Request, episode_id: str) -> Response:
    try:
        path = await _live_labyrinth_service(request).video_path(episode_id)
    except LiveLabyrinthNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "live_maze_race_not_found"}) from None
    if path is None:
        raise HTTPException(status_code=409, detail={"code": "live_maze_video_not_ready"})
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/embodiment/maze-races/{episode_id}/{projection}")
async def get_live_maze_projection(
    request: Request, episode_id: str, projection: str
) -> Mapping[str, object]:
    if projection not in {"result", "evaluation", "replay"}:
        raise HTTPException(status_code=404, detail={"code": "live_maze_projection_not_found"})
    try:
        return await getattr(_live_labyrinth_service(request), projection)(episode_id)
    except LiveLabyrinthNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "live_maze_race_not_found"}) from None
    except LiveLabyrinthNotReadyError:
        raise HTTPException(status_code=409, detail={"code": "live_maze_race_not_ready"}) from None


@router.post("/api/embodiment/maze-races/{episode_id}/cancel")
async def cancel_live_maze_race(request: Request, episode_id: str) -> Mapping[str, object]:
    try:
        return await _live_labyrinth_service(request).cancel(episode_id)
    except LiveLabyrinthNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "live_maze_race_not_found"}) from None


@router.get("/api/embodiment/episodes/{episode_id}")
async def get_episode(request: Request, response: Response, episode_id: str) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _not_found(lambda: _service(request).status(episode_id))


@router.get("/api/embodiment/episodes/{episode_id}/timeline")
async def get_episode_timeline(
    request: Request, response: Response, episode_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    timeline = await _not_found(lambda: _service(request).timeline(episode_id))
    return {"episode_id": episode_id, "events": timeline}


@router.get("/api/embodiment/episodes/{episode_id}/frame")
async def get_episode_frame(request: Request, episode_id: str) -> Response:
    try:
        view = await _service(request).frame(episode_id)
    except EpisodeNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_episode_not_found"}
        ) from None
    headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-State": view.state,
    }
    if view.snapshot is None:
        return Response(status_code=204, headers=headers)
    headers.update(
        {
            "X-Content-SHA256": view.snapshot.sha256,
            "X-Observation-Seq": str(view.snapshot.observation_seq),
        }
    )
    return Response(content=view.snapshot.png, media_type="image/png", headers=headers)


@router.websocket("/api/embodiment/episodes/{episode_id}/preview")
async def stream_episode_preview(websocket: WebSocket, episode_id: str) -> None:
    """Newest-frame-only participant-pixel preview; no JSON is sent on this channel."""
    try:
        token, queue, initial = await _service(websocket).preview_subscription(episode_id)
    except EpisodeNotFoundError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        if initial is not None:
            await websocket.send_bytes(initial.png)
        while True:
            await websocket.send_bytes((await queue.get()).png)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await _service(websocket).unsubscribe_preview(episode_id, token)


@router.websocket("/api/embodiment/episodes/{episode_id}/preview-live")
async def stream_live_episode_preview(websocket: WebSocket, episode_id: str) -> None:
    """Direct Godot JPEG pixels only; canonical PNG snapshot/replay traffic stays separate."""

    try:
        token, queue, initial = await _service(websocket).live_preview_subscription(episode_id)
    except EpisodeNotFoundError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        if initial is not None:
            await websocket.send_bytes(initial.jpeg)
        while True:
            await websocket.send_bytes((await queue.get()).jpeg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await _service(websocket).unsubscribe_live_preview(episode_id, token)


@router.websocket("/api/embodiment/series/{series_id}/participants/{participant_id}/preview-live")
async def stream_live_series_participant_preview(
    websocket: WebSocket, series_id: str, participant_id: str
) -> None:
    """Selected participant JPEG pixels only, with a newest-frame queue depth of one."""

    try:
        token, queue, initial = await _series_service(websocket).live_preview_subscription(
            series_id, participant_id
        )
    except (DuelSeriesNotFoundError, ValueError):
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        if initial is not None:
            await websocket.send_bytes(initial.jpeg)
        while True:
            await websocket.send_bytes((await queue.get()).jpeg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await _series_service(websocket).unsubscribe_live_preview(series_id, participant_id, token)


@router.websocket("/api/embodiment/series/{series_id}/broadcast/preview-live")
async def stream_live_series_broadcast_preview(websocket: WebSocket, series_id: str) -> None:
    """Public RTS presentation JPEGs only; this route never returns a player observation."""

    try:
        token, queue, initial = await _series_service(
            websocket
        ).live_broadcast_preview_subscription(series_id)
    except (DuelSeriesNotFoundError, ValueError):
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        if initial is not None:
            await websocket.send_bytes(initial.jpeg)
        while True:
            await websocket.send_bytes((await queue.get()).jpeg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await _series_service(websocket).unsubscribe_live_broadcast_preview(series_id, token)


@router.websocket(
    "/api/embodiment/trio-series/{series_id}/participants/{participant_id}/preview-live"
)
async def stream_live_trio_participant_preview(
    websocket: WebSocket, series_id: str, participant_id: str
) -> None:
    """Selected trio participant JPEG pixels only, never spectator or opponent-private data."""

    try:
        token, queue, initial = await _trio_service(websocket).live_preview_subscription(
            series_id, participant_id
        )
    except (TrioSeriesNotFoundError, ValueError):
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        if initial is not None:
            await websocket.send_bytes(initial.jpeg)
        while True:
            await websocket.send_bytes((await queue.get()).jpeg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await _trio_service(websocket).unsubscribe_live_preview(series_id, participant_id, token)


@router.get("/api/embodiment/episodes/{episode_id}/result")
async def get_episode_result(
    request: Request, response: Response, episode_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).result(episode_id)
    except EpisodeNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_episode_not_found"}
        ) from None
    except EpisodeResultNotReadyError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_episode_result_not_ready"}
        ) from None


@router.get("/api/embodiment/episodes/{episode_id}/replay")
async def get_episode_replay(request: Request, episode_id: str) -> Response:
    try:
        bundle = await _service(request).replay(episode_id)
    except EpisodeNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_episode_not_found"}
        ) from None
    except EpisodeReplayNotReadyError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_episode_replay_not_ready"}
        ) from None
    return Response(
        content=bundle.bundle_bytes,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-SHA256": bundle.content_sha256},
    )


@router.get("/api/embodiment/episodes/{episode_id}/evaluation")
async def get_episode_evaluation(
    request: Request, response: Response, episode_id: str
) -> Mapping[str, Any]:
    """Return only the hash-bound projection derived from sealed public evidence."""

    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).evaluation(episode_id)
    except EpisodeNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_episode_not_found"}
        ) from None
    except EpisodeEvaluationNotReadyError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_evaluation_not_ready"}
        ) from None


@router.get("/api/embodiment/replays")
async def list_saved_replays(
    request: Request, response: Response, limit: int = Query(default=50, ge=1, le=100)
) -> Mapping[str, Any]:
    """List durable participant-video replays, never their protected authority inputs."""

    response.headers["Cache-Control"] = "no-store"
    replays = await _service(request).saved_replays(limit=limit)
    return {"replays": [replay.public_dict() for replay in replays]}


@router.get("/api/embodiment/replays/{replay_id}")
async def get_saved_replay(
    request: Request, response: Response, replay_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    replay = await _service(request).saved_replay(replay_id)
    if replay is None:
        raise HTTPException(status_code=404, detail={"code": "embodiment_saved_replay_not_found"})
    return replay.public_dict()


@router.get("/api/embodiment/replays/{replay_id}/evaluation")
async def get_saved_replay_evaluation(
    request: Request, response: Response, replay_id: str
) -> Mapping[str, Any]:
    """Load the canonical persisted public evaluation without opening protected evidence."""

    response.headers["Cache-Control"] = "no-store"
    replay = await _service(request).saved_replay(replay_id)
    if replay is None:
        raise HTTPException(status_code=404, detail={"code": "embodiment_saved_replay_not_found"})
    evaluation = await _service(request).saved_replay_evaluation(replay_id)
    if evaluation is None:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_saved_evaluation_unavailable"}
        )
    return evaluation


@router.api_route("/api/embodiment/replays/{replay_id}/video", methods=["GET", "HEAD"])
async def get_saved_replay_video(request: Request, replay_id: str) -> Response:
    """Return only a local participant-pixel MP4; raw replay bytes have no API route."""

    target = await _service(request).saved_replay_video_path(replay_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "embodiment_saved_replay_not_found"})
    return FileResponse(
        target,
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/embodiment/replays/{replay_id}/bundle")
async def get_saved_replay_public_bundle(request: Request, replay_id: str) -> Response:
    """The same public evidence bundle retained beside the participant video."""

    target = await _service(request).saved_replay_public_bundle_path(replay_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "embodiment_saved_replay_not_found"})
    try:
        payload = target.read_bytes()
    except OSError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_saved_replay_not_found"}
        ) from None
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/embodiment/episodes/{episode_id}/cancel")
async def cancel_episode(
    request: Request, response: Response, episode_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _not_found(lambda: _service(request).cancel(episode_id))


@router.post("/api/embodiment/series", status_code=202)
async def create_series(
    request: Request, response: Response, payload: Any = _BODY
) -> Mapping[str, Any]:
    try:
        values = _validate_series_payload(payload)
        created = await _series_service(request).create(**values)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_embodiment_series_request"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return created


@router.get("/api/embodiment/series/{series_id}")
async def get_series(request: Request, response: Response, series_id: str) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _series_service(request).status(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_series_not_found"}
        ) from None


@router.get("/api/embodiment/series/{series_id}/result")
async def get_series_result(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _series_service(request).result(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_series_not_found"}
        ) from None
    except RuntimeError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_series_result_not_ready"}
        ) from None


@router.get("/api/embodiment/series/{series_id}/replay")
async def get_series_replay(request: Request, series_id: str) -> Response:
    try:
        bundle = await _series_service(request).replay(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "embodiment_series_not_found"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except DuelSeriesEvidenceNotReadyError:
        raise HTTPException(
            status_code=409,
            detail={"code": "embodiment_series_evidence_not_ready"},
            headers={"Cache-Control": "no-store"},
        ) from None
    return Response(
        content=bundle.bundle_bytes,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-SHA256": bundle.content_sha256},
    )


@router.get("/api/embodiment/series/{series_id}/timeline")
async def get_series_timeline(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _series_service(request).timeline(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_series_not_found"}
        ) from None
    except DuelSeriesEvidenceNotReadyError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_series_timeline_not_ready"}
        ) from None


@router.get("/api/embodiment/series/{series_id}/evaluation")
async def get_series_evaluation(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _series_service(request).evaluation(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_series_not_found"}
        ) from None
    except DuelSeriesEvidenceNotReadyError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_series_evaluation_not_ready"}
        ) from None


@router.get("/api/embodiment/series/{series_id}/archive")
async def get_series_archive(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    """Return only durable public-evidence and honest native-playback availability."""

    response.headers["Cache-Control"] = "no-store"
    try:
        return await _series_service(request).archive_status(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_series_not_found"}
        ) from None


@router.get("/api/embodiment/series/{series_id}/participants/{participant_id}/frame")
async def get_series_participant_frame(
    request: Request, series_id: str, participant_id: str
) -> Response:
    """Return one selected participant's sanitized pixels; never substitute spectator view."""

    try:
        state, snapshot = await _series_service(request).participant_frame(
            series_id, participant_id
        )
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "embodiment_series_not_found"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"code": "embodiment_series_participant_not_found"},
            headers={"Cache-Control": "no-store"},
        ) from None
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-State": state,
    }
    if snapshot is None:
        return Response(status_code=204, headers=headers)
    headers.update(
        {
            "X-Content-SHA256": snapshot.sha256,
            "X-Leg-Index": str(snapshot.leg_index),
            "X-Observation-Seq": str(snapshot.observation_seq),
            "X-Participant-ID": snapshot.participant_id,
        }
    )
    return Response(content=snapshot.png, media_type="image/png", headers=headers)


@router.get(
    "/api/embodiment/series/{series_id}/legs/{leg_index}/participants/{participant_id}/video"
)
async def get_series_participant_video(
    request: Request, series_id: str, leg_index: int, participant_id: str
) -> FileResponse:
    if leg_index not in (0, 1) or participant_id not in ("participant_0", "participant_1"):
        raise HTTPException(
            status_code=404,
            detail={"code": "embodiment_series_native_replay_not_found"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        path = await _series_service(request).native_video_path(
            series_id, leg_index, participant_id
        )
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "embodiment_series_not_found"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "embodiment_series_native_replay_not_found"},
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/embodiment/series/{series_id}/cancel")
async def cancel_series(request: Request, response: Response, series_id: str) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _series_service(request).cancel(series_id)
    except DuelSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_series_not_found"}
        ) from None


@router.post("/api/embodiment/trio-series", status_code=202)
async def create_trio_series(
    request: Request, response: Response, payload: Any = _BODY
) -> Mapping[str, Any]:
    try:
        created = await _trio_service(request).create(**_validate_trio_series_payload(payload))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_embodiment_trio_series_request"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return created


@router.get("/api/embodiment/trio-series/{series_id}")
async def get_trio_series(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _trio_service(request).status(series_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None


async def _trio_projection(
    request: Request, response: Response, series_id: str, name: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        method = getattr(_trio_service(request), name)
        return await method(series_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None
    except TrioSeriesNotReadyError:
        raise HTTPException(
            status_code=409,
            detail={"code": f"embodiment_trio_series_{name}_not_ready"},
        ) from None


@router.get("/api/embodiment/trio-series/{series_id}/result")
async def get_trio_series_result(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    return await _trio_projection(request, response, series_id, "result")


@router.get("/api/embodiment/trio-series/{series_id}/evaluation")
async def get_trio_series_evaluation(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    return await _trio_projection(request, response, series_id, "evaluation")


@router.get("/api/embodiment/trio-series/{series_id}/timeline")
async def get_trio_series_timeline(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    return await _trio_projection(request, response, series_id, "timeline")


@router.get("/api/embodiment/trio-series/{series_id}/replay")
async def get_trio_series_replay(request: Request, series_id: str) -> Response:
    try:
        bundle = await _trio_service(request).replay(series_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None
    except TrioSeriesNotReadyError:
        raise HTTPException(
            status_code=409, detail={"code": "embodiment_trio_series_replay_not_ready"}
        ) from None
    return Response(
        bundle.bundle_bytes,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-SHA256": bundle.content_sha256},
    )


@router.get("/api/embodiment/trio-series/{series_id}/archive")
async def get_trio_series_archive(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _trio_service(request).archive_status(series_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None


@router.get("/api/embodiment/trio-series/{series_id}/participants/{participant_id}/frame")
async def get_trio_participant_frame(
    request: Request, series_id: str, participant_id: str
) -> Response:
    try:
        state, snapshot = await _trio_service(request).participant_frame(series_id, participant_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_participant_not_found"}
        ) from None
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-State": state,
    }
    if snapshot is None:
        return Response(status_code=204, headers=headers)
    headers.update(
        {
            "X-Content-SHA256": snapshot.sha256,
            "X-Leg-Index": str(snapshot.leg_index),
            "X-Observation-Seq": str(snapshot.observation_seq),
            "X-Participant-ID": snapshot.participant_id,
        }
    )
    return Response(snapshot.png, media_type="image/png", headers=headers)


@router.get(
    "/api/embodiment/trio-series/{series_id}/legs/{leg_index}/participants/{participant_id}/video"
)
async def get_trio_participant_video(
    request: Request, series_id: str, leg_index: int, participant_id: str
) -> FileResponse:
    if leg_index not in (0, 1, 2) or participant_id not in TRIO_PARTICIPANT_IDS:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_native_replay_not_found"}
        )
    try:
        path = await _trio_service(request).native_video_path(series_id, leg_index, participant_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None
    if path is None:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_native_replay_not_found"}
        )
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/embodiment/trio-series/{series_id}/cancel")
async def cancel_trio_series(
    request: Request, response: Response, series_id: str
) -> Mapping[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _trio_service(request).cancel(series_id)
    except TrioSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_trio_series_not_found"}
        ) from None


def _validate_create_payload(payload: Any) -> dict[str, Any]:
    invalid = HTTPException(status_code=422, detail={"code": "invalid_embodiment_episode_request"})
    if not isinstance(payload, dict) or not set(payload) <= _CREATE_FIELDS:
        raise invalid
    required = {"model", "provider", "seed", "task_id"}
    if not required <= set(payload):
        raise invalid
    if any(not isinstance(payload[name], str) or not payload[name] for name in required - {"seed"}):
        raise invalid
    if isinstance(payload["seed"], bool) or not isinstance(payload["seed"], int):
        raise invalid
    maximum = payload.get("maximum_episode_ticks", 1800)
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise invalid
    provider = payload["provider"]
    if provider in (SCRIPTED_CONSTRUCTION_PROVIDER, DEMO_PROVIDER):
        if "api_key" in payload:
            raise invalid
        scenario_id = payload.get("scenario_id")
        if provider == DEMO_PROVIDER:
            if scenario_id is None:
                scenario_id = payload["task_id"]
            if not isinstance(scenario_id, str) or not scenario_id:
                raise invalid
            try:
                scenario = demo_scenario(scenario_id)
            except (TypeError, ValueError):
                raise invalid from None
            if (
                scenario.authority_task_id != payload["task_id"]
                or scenario.provider_model != payload["model"]
            ):
                raise invalid
        elif scenario_id is not None or not is_scripted_solo_demo(
            provider=SCRIPTED_CONSTRUCTION_PROVIDER,
            model=payload["model"],
            task_id=payload["task_id"],
        ):
            raise invalid
        api_key: str | None = None
    else:
        if "scenario_id" in payload:
            raise invalid
        scenario_id = None
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise invalid
    return {
        "api_key": api_key,
        "maximum_episode_ticks": maximum,
        "model": payload["model"],
        "observation_profile": payload.get("observation_profile", "hybrid-visible-v1"),
        "provider": provider,
        "seed": payload["seed"],
        "scenario_id": scenario_id,
        "task_id": payload["task_id"],
    }


def _validate_series_payload(payload: Any) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or not {"entrants", "seed"} <= set(payload)
        or not set(payload) <= {"entrants", "seed", "max_live_provider_calls", "task_id"}
    ):
        raise ValueError("invalid series payload")
    entrants = payload["entrants"]
    if not isinstance(entrants, list) or len(entrants) != 2:
        raise ValueError("invalid series entrants")
    normalized = []
    for entrant in entrants:
        if not isinstance(entrant, dict):
            raise ValueError("invalid series entrant")
        expected = (
            {"provider", "model"}
            if entrant.get("provider") in ("scripted", "demo")
            else {"provider", "model", "api_key"}
        )
        if set(entrant) != expected:
            raise ValueError("invalid series entrant")
        normalized.append(dict(entrant))
    return {
        "entrants": (normalized[0], normalized[1]),
        "seed": payload["seed"],
        "max_live_provider_calls": payload.get("max_live_provider_calls", 2160),
        "task_id": payload.get("task_id", "central-relay-v0"),
    }


def _validate_live_maze_payload(payload: Any) -> dict[str, Any]:
    """Validate the shared-key, three-slot live maze request without echoing a key."""
    allowed = {
        "provider",
        "api_key",
        "entrants",
        "max_provider_calls",
        "vision_range_cells",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid live maze payload")
    provider = payload.get("provider")
    api_key = payload.get("api_key")
    entrants = payload.get("entrants")
    if (
        provider not in {"openai", "anthropic", "gemini"}
        or not isinstance(api_key, str)
        or not api_key
    ):
        raise ValueError("invalid live maze provider")
    if not isinstance(entrants, list) or len(entrants) != 3:
        raise ValueError("invalid live maze entrants")
    defaults = (("Sol", "#ffb454"), ("Terra", "#63d6ff"), ("Luna", "#c6a8ff"))
    normalized: list[LiveMazeEntrant] = []
    for index, (entrant, default) in enumerate(zip(entrants, defaults)):
        if not isinstance(entrant, dict) or set(entrant) != {"display_name", "model"}:
            raise ValueError("invalid live maze entrant")
        name, color = default
        model = entrant.get("model")
        display_name = entrant.get("display_name")
        if not isinstance(model, str) or not model or len(model) > 128:
            raise ValueError("invalid live maze model")
        if display_name != name:
            raise ValueError("live maze entrant order is invalid")
        normalized.append(
            LiveMazeEntrant(
                participant_id=f"participant_{index}",
                entrant_id=f"entrant_{index}",
                display_name=name,
                provider=provider,
                model=model,
                color=color,
            )
        )
    max_provider_calls = payload.get("max_provider_calls", MAX_LIVE_PROVIDER_CALLS)
    if (
        isinstance(max_provider_calls, bool)
        or not isinstance(max_provider_calls, int)
        or not 1 <= max_provider_calls <= MAX_LIVE_PROVIDER_CALLS
    ):
        raise ValueError("invalid live maze call budget")
    vision_range_cells = payload.get("vision_range_cells", DEFAULT_VISION_RANGE_CELLS)
    if vision_range_cells != "infinite" and (
        isinstance(vision_range_cells, bool)
        or not isinstance(vision_range_cells, int)
        or not 1 <= vision_range_cells <= MAX_FINITE_VISION_RANGE_CELLS
    ):
        raise ValueError("invalid live maze vision range")
    return {
        "provider": provider,
        "api_key": api_key,
        "entrants": tuple(normalized),
        "max_provider_calls": max_provider_calls,
        "vision_range_cells": vision_range_cells,
    }


def _validate_trio_series_payload(payload: Any) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or not {"entrants", "seed", "task_id"} <= set(payload)
        or not set(payload) <= {"entrants", "seed", "task_id", "max_provider_calls"}
    ):
        raise ValueError("invalid trio series payload")
    entrants = payload["entrants"]
    if not isinstance(entrants, list) or len(entrants) != 3:
        raise ValueError("invalid trio entrants")
    normalized = []
    for entrant in entrants:
        if not isinstance(entrant, dict) or set(entrant) != {"provider", "model"}:
            raise ValueError("invalid trio entrant")
        normalized.append(dict(entrant))
    return {
        "entrants": (normalized[0], normalized[1], normalized[2]),
        "seed": payload["seed"],
        "task_id": payload["task_id"],
        "max_provider_calls": payload.get("max_provider_calls", 1080),
    }


async def _not_found(call: Any) -> Any:
    try:
        return await call()
    except EpisodeNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "embodiment_episode_not_found"}
        ) from None


__all__ = ["router"]
