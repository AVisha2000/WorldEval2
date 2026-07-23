"""Safe, auth-agnostic HTTP surface for the WorldEval Lab vertical slice.

Authentication is intentionally mounted outside this router.  The router has no credential store:
an API key received at launch is converted immediately into an episode-local ``SessionCredential``
for the existing live authority, then is excluded from every Lab contract, artifact, and response.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .credentials import SessionCredential
from .lab.contracts import LabContractError, RunEntrant, RunMode
from .lab.games import GameCatalogError, game_spec
from .lab.service import (
    DraftLabyrinthLaunch,
    LabRunError,
    LabRunNotFoundError,
    LabRunService,
    labyrinth_game_catalogue,
)
from .live_labyrinth import (
    DEFAULT_VISION_RANGE_CELLS,
    MAX_FINITE_VISION_RANGE_CELLS,
    MAX_LIVE_PROVIDER_CALLS,
    LiveMazeEntrant,
)
from .live_runtime import close_provider_adapter, provider_adapter
from .maze_maps import MazeMapSpec

router = APIRouter(prefix="/api/lab", tags=["WorldEval Lab"])
public_router = APIRouter(prefix="/api/public/games", tags=["WorldEval public game guides"])
_BODY = Body(...)
_LABYRINTH_PROVIDERS = frozenset(("openai", "anthropic", "gemini"))
_LABYRINTH_SKILL_MODES = frozenset(("none", "maze-navigation-v1"))
_CLONE_CHANGE_FIELDS = frozenset(
    (
        "budget",
        "configuration",
        "entrants",
        "game_id",
        "game_version",
        "map_id",
        "map_sha256",
        "mode",
        "runtime_version",
        "scenario_id",
        "seed_policy",
        "skill_mode",
    )
)


@dataclass(frozen=True)
class _StartedLiveLabyrinthEpisode:
    """An authority episode whose credential cleanup is now owned by the live service."""

    episode_id: str
    live: Any


def _lab_runs(request: Request) -> LabRunService:
    service = getattr(request.app.state, "lab_runs", None)
    if not isinstance(service, LabRunService):
        raise RuntimeError("WorldEval Lab run service is not configured")
    return service


def _live_labyrinth(request: Request) -> Any:
    service = getattr(request.app.state, "embodiment_live_labyrinth", None)
    if service is None or not callable(getattr(service, "create", None)):
        raise RuntimeError("Live Labyrinth service is not configured")
    return service


def _validate_session_api_key(value: object) -> str:
    """Validate a launch-only credential without ever serializing or reflecting it."""

    if not isinstance(value, str) or not value:
        raise ValueError("Labyrinth provider credentials are invalid")
    if len(value.encode("utf-8")) > 16_384 or "\x00" in value:
        raise ValueError("Labyrinth provider credentials are invalid")
    return value


def _live_entrants_from_contract(launch: DraftLabyrinthLaunch) -> tuple[LiveMazeEntrant, ...]:
    """Attach the fixed live seats to an already-validated frozen contract roster."""

    colors = ("#ffb454", "#63d6ff", "#c6a8ff")
    return tuple(
        LiveMazeEntrant(
            participant_id=f"participant_{index}",
            entrant_id=entrant.entrant_id,
            display_name=entrant.display_name or "",
            provider=launch.provider,
            model=entrant.model_id,
            color=colors[index],
        )
        for index, entrant in enumerate(launch.entrants)
    )


async def _start_live_labyrinth_episode(
    request: Request,
    *,
    api_key: str,
    entrants: tuple[LiveMazeEntrant, ...],
    provider: str,
    max_provider_calls: int,
    vision_range_cells: Any,
    skill_mode: str,
    map_spec: MazeMapSpec | None = None,
    participant_call_budget: int | None = None,
) -> _StartedLiveLabyrinthEpisode:
    """Start a live authority with a session-only credential and no persistent key material.

    Once ``create`` succeeds, the authority owns the cleanup callback and therefore the adapters
    and credential.  Any failure before that point closes the local objects immediately.
    """

    credential: SessionCredential | None = None
    adapters: dict[str, object] = {}
    authority_owns_cleanup = False
    try:
        live = _live_labyrinth(request)
        credential = SessionCredential(api_key)
        adapters = {
            entrant.participant_id: provider_adapter(provider, credential) for entrant in entrants
        }

        async def cleanup() -> None:
            try:
                await asyncio.gather(
                    *(close_provider_adapter(adapter) for adapter in adapters.values()),
                    return_exceptions=True,
                )
            finally:
                credential.close()

        created = await live.create(
            entrants=entrants,
            providers=adapters,
            max_provider_calls=max_provider_calls,
            vision_range_cells=vision_range_cells,
            map_spec=map_spec,
            participant_call_budget=participant_call_budget,
            skill_mode=skill_mode,
            cleanup=cleanup,
        )
        # ``create`` has accepted the cleanup callback even if an invalid test double returns a
        # malformed public response.  Do not close adapters underneath a possible authority task.
        authority_owns_cleanup = True
        episode_id = created.get("episode_id") if isinstance(created, Mapping) else None
        if not isinstance(episode_id, str) or not episode_id.startswith("ep_"):
            if isinstance(episode_id, str):
                try:
                    await live.cancel(episode_id)
                except Exception:
                    pass
            raise LabRunError("Labyrinth episode identity is invalid")
        return _StartedLiveLabyrinthEpisode(episode_id=episode_id, live=live)
    finally:
        if credential is not None and not authority_owns_cleanup:
            await asyncio.gather(
                *(close_provider_adapter(adapter) for adapter in adapters.values()),
                return_exceptions=True,
            )
            credential.close()


async def _cancel_live_labyrinth_episode(started: _StartedLiveLabyrinthEpisode) -> None:
    """Best-effort cancellation for an authority whose Lab record could not be retained."""

    try:
        await started.live.cancel(started.episode_id)
    except Exception:
        # The caller still records a safe local failure; transport details must not escape.
        return


def _spectator_cursor(value: Optional[str]) -> int:  # noqa: UP045
    """Parse a cursor without reflecting arbitrary query input in a validation response."""

    if value is None:
        return 0
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > 10
    ):
        raise ValueError("Lab spectator cursor is invalid")
    cursor = int(value)
    if cursor > 2_147_483_647:
        raise ValueError("Lab spectator cursor is invalid")
    return cursor


@router.get("/games")
async def list_games(response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return labyrinth_game_catalogue()


@router.get("/games/{game_id}")
async def get_game(game_id: str, response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return game_spec(game_id).public_dict()
    except GameCatalogError:
        raise HTTPException(status_code=404, detail={"code": "lab_game_not_found"}) from None


@public_router.get("")
async def list_public_games(response: Response) -> Mapping[str, object]:
    """List the same safe guide catalogue without exposing Team Lab run material."""

    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return labyrinth_game_catalogue()


@public_router.get("/{game_id}")
async def get_public_game(game_id: str, response: Response) -> Mapping[str, object]:
    """Serve an unlisted, read-only game guide with no operator material."""

    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    try:
        return game_spec(game_id).public_dict()
    except GameCatalogError:
        raise HTTPException(status_code=404, detail={"code": "public_game_not_found"}) from None


@public_router.get("/{game_id}/benchmark")
async def get_public_game_benchmark(
    request: Request, game_id: str, response: Response
) -> Mapping[str, object]:
    """Expose only verified-result posture, never exploratory run records."""

    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if game_id != "labyrinth-run":
        try:
            game_spec(game_id)
        except GameCatalogError:
            raise HTTPException(status_code=404, detail={"code": "public_game_not_found"}) from None
        return {
            "game_id": game_id,
            "season_state": "not_available",
            "verified_results": [],
            "message": "No verified benchmark season has been published for this game.",
        }
    return _lab_runs(request).benchmark_status()


@router.get("/runs")
async def list_runs(request: Request, response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return {"runs": await _lab_runs(request).list_runs()}


@router.get("/runs/{run_id}")
async def get_run(request: Request, response: Response, run_id: str) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _lab_runs(request).get_run(run_id)
    except LabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None


@router.get("/runs/{run_id}/projection")
async def get_run_projection(
    request: Request, response: Response, run_id: str
) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _lab_runs(request).projection(run_id)
    except LabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None


@router.get("/runs/{run_id}/spectator")
async def get_run_spectator(
    request: Request,
    response: Response,
    run_id: str,
    after: Optional[str] = None,  # noqa: UP045
) -> Mapping[str, object]:
    """Serve recent safe observer frames to an authenticated Lab spectator only."""

    response.headers["Cache-Control"] = "no-store"
    try:
        return await _lab_runs(request).spectator(run_id, after_sequence=_spectator_cursor(after))
    except ValueError:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_spectator_cursor"}
        ) from None
    except LabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except LabRunError:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_spectator_feed"}
        ) from None


@router.get("/runs/{run_id}/video")
async def get_run_video(request: Request, run_id: str) -> Response:
    try:
        path = await _lab_runs(request).video_path(run_id)
    except LabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    if path is None:
        raise HTTPException(status_code=409, detail={"code": "lab_run_video_not_ready"})
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/runs/labyrinth", status_code=202)
async def create_labyrinth_run(
    request: Request, response: Response, payload: Any = _BODY
) -> Mapping[str, object]:
    """Launch a three-seat live race while keeping the supplied credential ephemeral."""

    try:
        values = _validate_labyrinth_launch(payload)
    except (LabContractError, LabRunError, TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_labyrinth_run_request"}
        ) from None

    try:
        started = await _start_live_labyrinth_episode(
            request,
            api_key=values["api_key"],
            entrants=values["live_entrants"],
            provider=values["provider"],
            max_provider_calls=values["max_provider_calls"],
            vision_range_cells=values["vision_range_cells"],
            skill_mode=values["skill_mode"],
        )
        try:
            record = await _lab_runs(request).create_live_labyrinth(
                episode_id=started.episode_id,
                entrants=values["contract_entrants"],
                provider_call_budget=values["max_provider_calls"],
                vision_range_cells=values["vision_range_cells"],
                skill_mode=values["skill_mode"],
                mode=values["mode"],
            )
        except Exception:
            # Do not leave an untracked provider episode alive when contract persistence fails.
            await _cancel_live_labyrinth_episode(started)
            raise
    except Exception:
        # A provider transport cannot expose request details, credential fragments, or raw output.
        raise HTTPException(
            status_code=503, detail={"code": "labyrinth_launch_unavailable"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return record


@router.post("/runs/{run_id}/clone", status_code=201)
async def clone_run(
    request: Request, response: Response, run_id: str, payload: Any = _BODY
) -> Mapping[str, object]:
    try:
        changes = _validate_clone_payload(payload)
        record = await _lab_runs(request).clone(run_id, changes=changes)
    except LabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (LabContractError, LabRunError, TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_run_clone_request"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return record


@router.post("/runs/{run_id}/launch", status_code=202)
async def launch_draft_run(
    request: Request, response: Response, run_id: str, payload: Any = _BODY
) -> Mapping[str, object]:
    """Start one fresh authority episode from a compatible draft clone.

    This is intentionally not a resume API: the draft's immutable contract and parent lineage are
    retained, while a new process-local live episode receives a newly supplied session credential.
    """

    try:
        api_key = _validate_draft_launch_payload(payload)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_run_launch_request"}
        ) from None

    runs = _lab_runs(request)
    try:
        launch = await runs.reserve_draft_labyrinth_launch(run_id)
    except LabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (LabContractError, LabRunError, TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_run_launch_request"}
        ) from None

    try:
        started = await _start_live_labyrinth_episode(
            request,
            api_key=api_key,
            entrants=_live_entrants_from_contract(launch),
            provider=launch.provider,
            max_provider_calls=launch.max_provider_calls,
            vision_range_cells=launch.vision_range_cells,
            map_spec=launch.map_spec,
            participant_call_budget=launch.max_provider_calls,
            skill_mode=launch.skill_mode,
        )
        try:
            record = await runs.attach_draft_labyrinth_episode(
                run_id, episode_id=started.episode_id
            )
        except Exception:
            # The authority has accepted cleanup ownership, but no durable Lab record can point
            # at it.  Ask the authority to stop before recording the clone's safe failure state.
            await _cancel_live_labyrinth_episode(started)
            raise
    except Exception:
        try:
            await runs.fail_reserved_draft_labyrinth_launch(run_id)
        except Exception:
            # A persistence outage is already represented by the generic response below.  Never
            # surface a filesystem, provider, or credential detail through this browser route.
            pass
        raise HTTPException(
            status_code=503, detail={"code": "labyrinth_launch_unavailable"}
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return record


@router.get("/benchmarks/labyrinth-run")
async def labyrinth_benchmark_status(request: Request, response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return _lab_runs(request).benchmark_status()


def _validate_labyrinth_launch(payload: object) -> dict[str, Any]:
    allowed = {
        "api_key",
        "entrants",
        "max_provider_calls",
        "mode",
        "provider",
        "skill_mode",
        "vision_range_cells",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Labyrinth launch fields are invalid")
    provider = payload.get("provider")
    api_key = _validate_session_api_key(payload.get("api_key"))
    if provider not in _LABYRINTH_PROVIDERS:
        raise ValueError("Labyrinth provider credentials are invalid")
    skill_mode = payload.get("skill_mode", "none")
    if skill_mode not in _LABYRINTH_SKILL_MODES:
        raise ValueError("Labyrinth skill mode is invalid")
    try:
        mode = RunMode(payload.get("mode", RunMode.EXPLORATORY.value))
    except (TypeError, ValueError) as error:
        raise ValueError("Labyrinth run mode is invalid") from error
    max_provider_calls = payload.get("max_provider_calls", MAX_LIVE_PROVIDER_CALLS)
    if (
        isinstance(max_provider_calls, bool)
        or not isinstance(max_provider_calls, int)
        or not 1 <= max_provider_calls <= MAX_LIVE_PROVIDER_CALLS
    ):
        raise ValueError("Labyrinth call budget is invalid")
    vision_range_cells = payload.get("vision_range_cells", DEFAULT_VISION_RANGE_CELLS)
    if vision_range_cells != "infinite" and (
        isinstance(vision_range_cells, bool)
        or not isinstance(vision_range_cells, int)
        or not 1 <= vision_range_cells <= MAX_FINITE_VISION_RANGE_CELLS
    ):
        raise ValueError("Labyrinth vision range is invalid")
    entrants = payload.get("entrants")
    if not isinstance(entrants, list) or len(entrants) != 3:
        raise ValueError("Labyrinth entrants are invalid")
    defaults = (("Sol", "#ffb454"), ("Terra", "#63d6ff"), ("Luna", "#c6a8ff"))
    live_entrants: list[LiveMazeEntrant] = []
    contract_entrants: list[RunEntrant] = []
    for index, (entrant, defaults_for_seat) in enumerate(zip(entrants, defaults)):
        if not isinstance(entrant, dict) or set(entrant) != {"display_name", "model"}:
            raise ValueError("Labyrinth entrant fields are invalid")
        display_name, color = defaults_for_seat
        if entrant.get("display_name") != display_name:
            raise ValueError("Labyrinth entrant order is invalid")
        model = entrant.get("model")
        if not isinstance(model, str) or not model or len(model) > 128 or "\x00" in model:
            raise ValueError("Labyrinth model is invalid")
        live_entrants.append(
            LiveMazeEntrant(
                participant_id=f"participant_{index}",
                entrant_id=f"entrant_{index}",
                display_name=display_name,
                provider=provider,
                model=model,
                color=color,
            )
        )
        contract_entrants.append(
            RunEntrant(
                entrant_id=f"entrant_{index}",
                model_id=model,
                provider=provider,
                display_name=display_name,
            )
        )
    return {
        "api_key": api_key,
        "contract_entrants": tuple(contract_entrants),
        "live_entrants": tuple(live_entrants),
        "max_provider_calls": max_provider_calls,
        "mode": mode,
        "provider": provider,
        "skill_mode": skill_mode,
        "vision_range_cells": vision_range_cells,
    }


def _validate_draft_launch_payload(payload: object) -> str:
    """Accept only a fresh, ephemeral session key for a saved draft launch."""

    if not isinstance(payload, dict) or set(payload) != {"api_key"}:
        raise ValueError("Lab run launch payload is invalid")
    return _validate_session_api_key(payload["api_key"])


def _validate_clone_payload(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"changes"}:
        raise ValueError("Lab run clone payload is invalid")
    changes = payload["changes"]
    if not isinstance(changes, dict) or not changes or set(changes) - _CLONE_CHANGE_FIELDS:
        raise ValueError("Lab run clone changes are invalid")
    return dict(changes)


__all__ = ["public_router", "router"]
