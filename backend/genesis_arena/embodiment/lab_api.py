"""Safe, auth-agnostic HTTP surface for the WorldEval Lab vertical slice.

Authentication is intentionally mounted outside this router.  The router has no credential store:
an API key received at launch is converted immediately into an episode-local ``SessionCredential``
for the existing live authority, then is excluded from every Lab contract, artifact, and response.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .credentials import SessionCredential
from .demo_scenarios import demo_scenario
from .duo_games.catalog import duo_game
from .lab.benchmarks import BenchmarkStore, LabBenchmarkError
from .lab.contracts import LabContractError, RunContract, RunEntrant, RunMode
from .lab.game_runs import (
    AttachedGameRun,
    GenericLabRunError,
    GenericLabRunNotFoundError,
    GenericLabRunService,
)
from .lab.games import GameCatalogError, game_spec
from .lab.publications import (
    PublicReplayNotFoundError,
    PublicReplayPublicationError,
    PublicReplayStore,
    PublicReplayStoreError,
)
from .lab.runtime_registry import (
    GameRuntimeModeUnavailableError,
    GameRuntimeProfile,
    GameRuntimeRegistryError,
    resolve_runtime_launch,
    runtime_profile,
    safe_runtime_manifest,
)
from .lab.sandbox import (
    SandboxManifestError,
    draft_sandbox_recipe,
    sandbox_manifest,
)
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
from .trio_games.scheduling import TRIO_DEMO_ENTRANTS

router = APIRouter(prefix="/api/lab", tags=["WorldEval Lab"])
public_router = APIRouter(prefix="/api/public/games", tags=["WorldEval public game guides"])
_BODY = Body(...)
_LABYRINTH_PROVIDERS = frozenset(("openai", "anthropic", "gemini"))
_LABYRINTH_SKILL_MODES = frozenset(("none", "maze-navigation-v1"))
_GENERIC_SESSION_PROVIDERS = frozenset(("openai", "anthropic", "gemini"))
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
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


def _generic_lab_runs(request: Request) -> GenericLabRunService:
    service = getattr(request.app.state, "lab_game_runs", None)
    if not isinstance(service, GenericLabRunService):
        raise RuntimeError("WorldEval generic Lab run service is not configured")
    return service


def _optional_generic_lab_runs(request: Request) -> GenericLabRunService | None:
    service = getattr(request.app.state, "lab_game_runs", None)
    if service is None:
        # Compatibility for narrow test/embedded apps that mount the Labyrinth-only router.
        return None
    if not isinstance(service, GenericLabRunService):
        raise RuntimeError("WorldEval generic Lab run service is invalid")
    return service


def _public_replays(request: Request) -> PublicReplayStore:
    store = getattr(request.app.state, "lab_public_replays", None)
    if not isinstance(store, PublicReplayStore):
        raise RuntimeError("WorldEval public replay store is not configured")
    return store


def _benchmark_store(request: Request) -> BenchmarkStore:
    store = getattr(request.app.state, "lab_benchmarks", None)
    if not isinstance(store, BenchmarkStore):
        raise RuntimeError("WorldEval benchmark store is not configured")
    return store


def _optional_benchmark_store(request: Request) -> BenchmarkStore | None:
    store = getattr(request.app.state, "lab_benchmarks", None)
    if store is None:
        return None
    if not isinstance(store, BenchmarkStore):
        raise RuntimeError("WorldEval benchmark store is invalid")
    return store


def _generic_authority_service(request: Request, authority_kind: str) -> Any:
    state_name = {
        "solo_episode": "embodiment_episodes",
        "paired_series": "embodiment_series",
        "trio_series": "embodiment_trio_series",
    }.get(authority_kind)
    if state_name is None:
        raise RuntimeError("WorldEval game authority is unsupported")
    service = getattr(request.app.state, state_name, None)
    if service is None or not callable(getattr(service, "create", None)):
        raise RuntimeError("WorldEval game authority is not configured")
    return service


def _is_generic_run_id(run_id: str) -> bool:
    return isinstance(run_id, str) and run_id.startswith("run_game_")


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


@router.get("/runtime-manifest")
async def get_runtime_manifest(response: Response) -> Mapping[str, object]:
    """Publish admitted logical runtime profiles without paths or authority handles."""

    response.headers["Cache-Control"] = "no-store"
    return safe_runtime_manifest()


@router.get("/sandbox")
async def get_sandbox_manifest(response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return sandbox_manifest()


@router.post("/sandbox/recipes/draft", status_code=201)
async def create_sandbox_recipe_draft(
    response: Response, payload: Any = _BODY
) -> Mapping[str, object]:
    """Compose safe draft metadata; this never grants executable game authority."""

    if not isinstance(payload, dict) or set(payload) != {
        "primitive_ids",
        "recipe_id",
        "summary",
        "title",
    }:
        raise HTTPException(status_code=422, detail={"code": "invalid_sandbox_recipe"})
    try:
        recipe = draft_sandbox_recipe(
            recipe_id=payload["recipe_id"],
            title=payload["title"],
            summary=payload["summary"],
            primitive_ids=payload["primitive_ids"],
        )
    except (SandboxManifestError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "invalid_sandbox_recipe"}) from None
    response.headers["Cache-Control"] = "no-store"
    return recipe.public_dict()


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
    try:
        game_spec(game_id)
    except GameCatalogError:
        raise HTTPException(status_code=404, detail={"code": "public_game_not_found"}) from None
    store = _optional_benchmark_store(request)
    if store is not None:
        try:
            return await asyncio.to_thread(store.game_status, game_id)
        except LabBenchmarkError:
            raise HTTPException(
                status_code=503, detail={"code": "public_benchmark_store_unavailable"}
            ) from None
    if game_id == "labyrinth-run":
        return _lab_runs(request).benchmark_status()
    return {
        "game_id": game_id,
        "season_state": "not_available",
        "verified_results": [],
        "message": "No verified benchmark season has been published for this game.",
    }


@public_router.get("/{game_id}/replays")
async def list_public_game_replays(
    request: Request, game_id: str, response: Response
) -> Mapping[str, object]:
    """List only replays that an operator explicitly published for this game."""

    try:
        game_spec(game_id)
        projection = await asyncio.to_thread(
            _public_replays(request).public_game_projection, game_id
        )
    except GameCatalogError:
        raise HTTPException(status_code=404, detail={"code": "public_game_not_found"}) from None
    except (PublicReplayPublicationError, PublicReplayStoreError):
        raise HTTPException(
            status_code=503, detail={"code": "public_replay_store_unavailable"}
        ) from None
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return projection


@public_router.get("/{game_id}/replays/{publication_slug}")
async def get_public_game_replay(
    request: Request,
    game_id: str,
    publication_slug: str,
    response: Response,
) -> Mapping[str, object]:
    """Resolve one unlisted safe replay without exposing its private run identity."""

    try:
        game_spec(game_id)
        publication = await asyncio.to_thread(_public_replays(request).get, publication_slug)
    except GameCatalogError:
        raise HTTPException(status_code=404, detail={"code": "public_game_not_found"}) from None
    except (PublicReplayNotFoundError, PublicReplayPublicationError):
        raise HTTPException(status_code=404, detail={"code": "public_replay_not_found"}) from None
    except PublicReplayStoreError:
        raise HTTPException(
            status_code=503, detail={"code": "public_replay_store_unavailable"}
        ) from None
    if publication.get("game_id") != game_id:
        raise HTTPException(status_code=404, detail={"code": "public_replay_not_found"})
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return publication


@router.get("/runs")
async def list_runs(request: Request, response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    generic_service = _optional_generic_lab_runs(request)
    if generic_service is None:
        labyrinth = await _lab_runs(request).list_runs()
        generic: list[Mapping[str, Any]] = []
    else:
        labyrinth, generic = await asyncio.gather(
            _lab_runs(request).list_runs(),
            generic_service.list_runs(),
        )
    combined = [*labyrinth, *generic]
    combined.sort(
        key=lambda item: (
            item.get("created_at_epoch_ms", 0),
            item.get("run_id", ""),
        ),
        reverse=True,
    )
    return {"runs": combined}


@router.get("/runs/{run_id}")
async def get_run(request: Request, response: Response, run_id: str) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        if _is_generic_run_id(run_id):
            return await _generic_lab_runs(request).get_run(run_id)
        return await _lab_runs(request).get_run(run_id)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None


@router.get("/runs/{run_id}/projection")
async def get_run_projection(
    request: Request, response: Response, run_id: str
) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        if _is_generic_run_id(run_id):
            return await _generic_lab_runs(request).projection(run_id)
        return await _lab_runs(request).projection(run_id)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None


@router.get("/runs/{run_id}/frame")
async def get_run_frame(
    request: Request,
    run_id: str,
    participant: str = "participant_0",
) -> Response:
    """Return a sanitized participant PNG for a generic live authority.

    Labyrinth uses its richer JSON spectator feed; every other authority stays behind the same
    authenticated Lab run identity and never exposes its process-local episode or series id.
    """

    if not _is_generic_run_id(run_id):
        raise HTTPException(status_code=409, detail={"code": "lab_run_frame_uses_spectator"})
    if not isinstance(participant, str) or participant not in {
        "participant_0",
        "participant_1",
        "participant_2",
    }:
        raise HTTPException(status_code=422, detail={"code": "invalid_lab_participant"})
    try:
        frame = await _generic_lab_runs(request).frame(run_id, participant_id=participant)
    except GenericLabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except GenericLabRunError:
        raise HTTPException(status_code=422, detail={"code": "invalid_lab_run_frame"}) from None
    headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-State": frame.state,
    }
    if frame.png is None:
        return Response(status_code=204, headers=headers)
    headers["X-Content-SHA256"] = frame.sha256 or ""
    return Response(content=frame.png, media_type="image/png", headers=headers)


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
        if _is_generic_run_id(run_id):
            # Generic games expose sanitized participant pixels through /frame.  Keep the shared
            # polling contract stable without fabricating spatial observer state.
            await _generic_lab_runs(request).get_run(run_id)
            cursor = _spectator_cursor(after)
            return {
                "schema_version": "worldeval/lab-live-spectator-feed/1",
                "run_id": run_id,
                "cursor": cursor,
                "reset_required": False,
                "frames": [],
            }
        return await _lab_runs(request).spectator(run_id, after_sequence=_spectator_cursor(after))
    except ValueError:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_spectator_cursor"}
        ) from None
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except LabRunError:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_spectator_feed"}
        ) from None


@router.get("/runs/{run_id}/video")
async def get_run_video(request: Request, run_id: str) -> Response:
    if _is_generic_run_id(run_id):
        try:
            await _generic_lab_runs(request).get_run(run_id)
        except GenericLabRunNotFoundError:
            raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
        raise HTTPException(status_code=409, detail={"code": "lab_run_video_not_ready"})
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


@router.post("/runs/{run_id}/cancel")
async def cancel_lab_run(request: Request, response: Response, run_id: str) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        if _is_generic_run_id(run_id):
            return await _generic_lab_runs(request).cancel(run_id)
        return await _lab_runs(request).cancel(run_id)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (LabRunError, GenericLabRunError):
        raise HTTPException(
            status_code=409, detail={"code": "lab_run_cancel_unavailable"}
        ) from None


@router.post("/runs/{run_id}/seal")
async def seal_lab_run(request: Request, response: Response, run_id: str) -> Mapping[str, object]:
    """Freeze a completed cartridge; benchmark admission remains separate."""

    response.headers["Cache-Control"] = "no-store"
    try:
        if _is_generic_run_id(run_id):
            return await _generic_lab_runs(request).seal(run_id)
        return await _lab_runs(request).seal(run_id)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (LabRunError, GenericLabRunError):
        raise HTTPException(status_code=409, detail={"code": "lab_run_seal_unavailable"}) from None


@router.post("/runs/{run_id}/verify")
async def verify_lab_run(request: Request, response: Response, run_id: str) -> Mapping[str, object]:
    """Validate the durable canonical bindings of one sealed cartridge."""

    response.headers["Cache-Control"] = "no-store"
    try:
        if _is_generic_run_id(run_id):
            return await _generic_lab_runs(request).verify(run_id)
        return await _lab_runs(request).verify(run_id)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (LabRunError, GenericLabRunError):
        raise HTTPException(
            status_code=409, detail={"code": "lab_run_verify_unavailable"}
        ) from None


@router.post("/runs/{run_id}/benchmark", status_code=201)
async def submit_lab_run_to_benchmark(
    request: Request,
    response: Response,
    run_id: str,
    payload: Any = _BODY,
) -> Mapping[str, object]:
    """Admit authority-derived metrics from one verified, recipe-compatible cartridge."""

    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"recipe_id"}
        or not isinstance(payload.get("recipe_id"), str)
    ):
        raise HTTPException(status_code=422, detail={"code": "invalid_lab_benchmark_submission"})
    try:
        if _is_generic_run_id(run_id):
            record = await _generic_lab_runs(request).get_run(run_id)
        else:
            record = await _lab_runs(request).get_run(run_id)
        cartridge = record.get("cartridge")
        projection = cartridge.get("public_projection") if isinstance(cartridge, Mapping) else None
        snapshot = projection.get("snapshot") if isinstance(projection, Mapping) else None
        participant_metrics = (
            snapshot.get("benchmark_metrics") if isinstance(snapshot, Mapping) else None
        )
        if not isinstance(participant_metrics, list):
            raise LabBenchmarkError("verified run has no authority-derived benchmark metrics")
        accepted = await asyncio.to_thread(
            _benchmark_store(request).accept,
            recipe_id=payload["recipe_id"],
            candidate_run=record,
            participant_metrics=participant_metrics,
        )
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except LabBenchmarkError:
        raise HTTPException(
            status_code=409, detail={"code": "lab_benchmark_submission_unavailable"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return accepted


@router.post("/runs/{run_id}/publish", status_code=201)
async def publish_lab_run(
    request: Request, response: Response, run_id: str
) -> Mapping[str, object]:
    """Create an explicit unlisted publication from a safe completed cartridge."""

    try:
        if _is_generic_run_id(run_id):
            record = await _generic_lab_runs(request).get_run(run_id)
        else:
            record = await _lab_runs(request).get_run(run_id)
        publication = await asyncio.to_thread(_public_replays(request).publish, record)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (PublicReplayPublicationError, PublicReplayStoreError):
        raise HTTPException(
            status_code=409, detail={"code": "lab_run_publication_unavailable"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return publication


@router.delete("/publications/{publication_slug}", status_code=204)
async def unpublish_lab_replay(request: Request, publication_slug: str) -> Response:
    """Remove an explicit public link while keeping its recoverable private tombstone."""

    try:
        removed = await asyncio.to_thread(_public_replays(request).unpublish, publication_slug)
    except (PublicReplayPublicationError, PublicReplayStoreError):
        raise HTTPException(
            status_code=409, detail={"code": "lab_replay_unpublish_unavailable"}
        ) from None
    if not removed:
        raise HTTPException(status_code=404, detail={"code": "public_replay_not_found"})
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


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


@router.post("/runs/games/{game_id}", status_code=202)
async def create_generic_game_run(
    request: Request,
    response: Response,
    game_id: str,
    payload: Any = _BODY,
) -> Mapping[str, object]:
    """Launch an admitted existing Godot authority through one Lab contract boundary."""

    try:
        values = _validate_generic_game_launch(game_id, payload)
        authority_service, launch = await _start_generic_game_authority(request, values)
    except (
        GameCatalogError,
        GameRuntimeModeUnavailableError,
        GameRuntimeRegistryError,
        LabContractError,
        GenericLabRunError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_game_run_request"}
        ) from None
    except Exception:
        raise HTTPException(
            status_code=503, detail={"code": "lab_game_launch_unavailable"}
        ) from None

    try:
        record = await _generic_lab_runs(request).attach(launch)
    except Exception:
        # The existing service owns any session credential after create succeeds.  Ensure a
        # persistence failure cannot leave an untracked provider authority consuming calls.
        try:
            await authority_service.cancel(launch.source_id)
        except Exception:
            pass
        raise HTTPException(
            status_code=503, detail={"code": "lab_game_launch_unavailable"}
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return record


@router.post("/runs/{run_id}/clone", status_code=201)
async def clone_run(
    request: Request, response: Response, run_id: str, payload: Any = _BODY
) -> Mapping[str, object]:
    try:
        changes = _validate_clone_payload(payload)
        if _is_generic_run_id(run_id):
            record = await _generic_lab_runs(request).clone(run_id, changes=changes)
        else:
            record = await _lab_runs(request).clone(run_id, changes=changes)
    except (LabRunNotFoundError, GenericLabRunNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (
        LabContractError,
        LabRunError,
        GenericLabRunError,
        TypeError,
        ValueError,
    ):
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

    if _is_generic_run_id(run_id):
        record = await _launch_generic_draft_run(request, run_id, payload)
        response.headers["Cache-Control"] = "no-store"
        return record

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


@router.get("/benchmarks/model-profiles")
async def benchmark_model_profiles(request: Request, response: Response) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return {"profiles": await asyncio.to_thread(_benchmark_store(request).model_profiles)}


@router.get("/benchmarks/{game_id}")
async def benchmark_status(
    request: Request, response: Response, game_id: str
) -> Mapping[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        game_spec(game_id)
    except GameCatalogError:
        raise HTTPException(status_code=404, detail={"code": "lab_game_not_found"}) from None
    store = _optional_benchmark_store(request)
    if store is not None:
        try:
            return await asyncio.to_thread(store.game_status, game_id)
        except LabBenchmarkError:
            raise HTTPException(
                status_code=503, detail={"code": "lab_benchmark_store_unavailable"}
            ) from None
    if game_id == "labyrinth-run":
        return _lab_runs(request).benchmark_status()
    return {
        "game_id": game_id,
        "season_state": "not_available",
        "verified_results": [],
        "message": "No verified benchmark recipe is available for this game.",
    }


def _validated_models(value: object, *, count: int) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) != count
        or any(
            not isinstance(model, str) or _SAFE_MODEL.fullmatch(model) is None for model in value
        )
    ):
        raise ValueError("Lab game models are invalid")
    models = tuple(value)
    # Model identifiers are persisted into contracts and public run receipts. Validate them
    # before an authority/provider sees the requested model so a credential accidentally pasted
    # into this field can never become a durable or browser-visible artifact.
    for index, model in enumerate(models):
        RunEntrant(entrant_id=f"entrant_{index}", model_id=model)
    return models


def _generic_launch_values_from_contract(contract: RunContract, payload: object) -> dict[str, Any]:
    """Reconstruct a fresh launch only from a compatible frozen draft contract."""

    body = contract.as_dict()
    configuration = body.get("configuration")
    if not isinstance(configuration, Mapping):
        raise ValueError("Generic Lab draft configuration is invalid")
    profile_id = configuration.get("runtime_profile_id")
    if not isinstance(profile_id, str):
        raise ValueError("Generic Lab draft runtime profile is invalid")
    profile = runtime_profile(profile_id)
    if not profile.launchable or profile.mode not in {"live", "demo"}:
        raise ValueError("Generic Lab draft runtime profile is not launchable")
    if (
        body.get("game_version") != profile.canonical_task_id
        or body.get("scenario_id")
        != (profile.canonical_scenario_id if profile.mode == "demo" else None)
        or body.get("runtime_version") != f"godot-{profile.protocol_version.replace('/', '-')}"
        or configuration.get("authority_kind") != profile.authority_kind
        or configuration.get("protocol_version") != profile.protocol_version
    ):
        raise ValueError("Generic Lab draft authority binding differs")
    try:
        resolved = resolve_runtime_launch(str(body.get("game_id")), profile.mode)
    except GameRuntimeRegistryError as error:
        raise ValueError("Generic Lab draft game binding is unavailable") from error
    if resolved.profile_id != profile.profile_id:
        raise ValueError("Generic Lab draft profile is no longer admitted")
    expected_mode = "demo" if profile.mode == "demo" else "exploratory"
    if body.get("mode") != expected_mode:
        raise ValueError("Generic Lab draft evidence mode differs")

    seed_policy = body.get("seed_policy")
    if (
        not isinstance(seed_policy, Mapping)
        or set(seed_policy) != {"kind", "seed"}
        or seed_policy.get("kind") != "fixed_seed"
    ):
        raise ValueError("Generic Lab draft seed policy is invalid")
    seed = seed_policy.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
        raise ValueError("Generic Lab draft seed is invalid")

    raw_entrants = body.get("entrants")
    if not isinstance(raw_entrants, list) or len(raw_entrants) != profile.participant_count:
        raise ValueError("Generic Lab draft entrants are invalid")
    entrants = tuple(RunEntrant.from_dict(value) for value in raw_entrants)
    providers = {entrant.provider for entrant in entrants}
    models = tuple(entrant.model_id for entrant in entrants)
    if profile.mode == "demo":
        if payload != {} or providers != {"demo"}:
            raise ValueError("Generic Demo draft is credential-free")
        provider = "demo"
        api_key = None
    else:
        if not isinstance(payload, dict) or set(payload) != {"api_key"}:
            raise ValueError("Generic live draft requires one session credential")
        if len(providers) != 1:
            raise ValueError("Generic Lab draft providers differ")
        provider = next(iter(providers))
        if provider not in _GENERIC_SESSION_PROVIDERS or provider not in profile.providers:
            raise ValueError("Generic Lab draft provider is unavailable")
        api_key = _validate_session_api_key(payload["api_key"])

    budget = body.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("Generic Lab draft budget is invalid")
    if profile.authority_kind == "solo_episode":
        if set(budget) != {"maximum_ticks", "scope"} or budget.get("scope") != "episode":
            raise ValueError("Generic solo draft budget is invalid")
        maximum_ticks = budget.get("maximum_ticks")
        if (
            isinstance(maximum_ticks, bool)
            or not isinstance(maximum_ticks, int)
            or not 1 <= maximum_ticks <= 18_000
        ):
            raise ValueError("Generic solo draft budget is invalid")
        max_provider_calls = 2_160
    else:
        rotations = 3 if profile.authority_kind == "trio_series" else 2
        limit = 1_080 if rotations == 3 else 2_160
        if (
            set(budget) != {"maximum_provider_calls", "rotations", "scope"}
            or budget.get("scope") != "series"
            or budget.get("rotations") != rotations
        ):
            raise ValueError("Generic series draft budget is invalid")
        max_provider_calls = budget.get("maximum_provider_calls")
        if (
            isinstance(max_provider_calls, bool)
            or not isinstance(max_provider_calls, int)
            or not 1 <= max_provider_calls <= limit
        ):
            raise ValueError("Generic series draft budget is invalid")
        maximum_ticks = 0
    return {
        "api_key": api_key,
        "game_id": body["game_id"],
        "max_provider_calls": max_provider_calls,
        "maximum_ticks": maximum_ticks,
        "mode": profile.mode,
        "models": models,
        "profile": profile,
        "provider": provider,
        "seed": seed,
    }


async def _launch_generic_draft_run(
    request: Request, run_id: str, payload: object
) -> Mapping[str, object]:
    runs = _generic_lab_runs(request)
    try:
        inspected = await runs.inspect_draft_launch(run_id)
        values = _generic_launch_values_from_contract(inspected, payload)
        contract = await runs.reserve_draft_launch(run_id)
        if contract.contract_sha256 != inspected.contract_sha256:
            raise GenericLabRunError("generic Lab draft changed during reservation")
    except GenericLabRunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "lab_run_not_found"}) from None
    except (
        GameRuntimeRegistryError,
        GenericLabRunError,
        LabContractError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_lab_run_launch_request"}
        ) from None

    authority_service: Any | None = None
    source_id: str | None = None
    try:
        authority_service, launch = await _start_generic_game_authority(request, values)
        source_id = launch.source_id
        return await runs.attach_reserved_authority(run_id, source_id=source_id)
    except Exception:
        if authority_service is not None and source_id is not None:
            try:
                await authority_service.cancel(source_id)
            except Exception:
                pass
        try:
            await runs.fail_reserved_draft_launch(run_id)
        except Exception:
            pass
        raise HTTPException(
            status_code=503, detail={"code": "lab_game_launch_unavailable"}
        ) from None


def _validate_generic_game_launch(game_id: str, payload: object) -> dict[str, Any]:
    """Validate one uniform launch request without reflecting credential material."""

    allowed = {
        "api_key",
        "max_provider_calls",
        "maximum_ticks",
        "mode",
        "models",
        "provider",
        "seed",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Lab game launch fields are invalid")
    game_spec(game_id)
    mode = payload.get("mode")
    if mode not in {"live", "demo"}:
        raise ValueError("Lab game launch mode is invalid")
    profile = resolve_runtime_launch(game_id, mode)
    if profile.authority_kind not in {
        "solo_episode",
        "paired_series",
        "trio_series",
    }:
        raise ValueError("Lab game authority is not admitted through this endpoint")
    if profile.canonical_task_id is None:
        raise ValueError("Lab game task is invalid")
    seed = payload.get("seed", 7)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
        raise ValueError("Lab game seed is invalid")

    if mode == "demo":
        if set(payload).intersection({"api_key", "provider", "models"}):
            raise ValueError("Demo launch is frozen and credential-free")
        provider = "demo"
        api_key = None
        if profile.authority_kind == "solo_episode":
            scenario = demo_scenario(profile.canonical_scenario_id or "")
            models = (scenario.provider_model,)
            maximum_ticks = scenario.episode_tick_budget
        elif profile.authority_kind == "paired_series":
            models = duo_game(profile.canonical_task_id).models
            if len(models) != 2:
                raise ValueError("Paired Demo policy is unavailable")
            maximum_ticks = duo_game(profile.canonical_task_id).maximum_episode_ticks
        else:
            models = tuple(entrant.model for entrant in TRIO_DEMO_ENTRANTS)
            maximum_ticks = 0
    else:
        provider = payload.get("provider", "openai")
        if provider not in _GENERIC_SESSION_PROVIDERS or provider not in profile.providers:
            raise ValueError("Lab game provider is invalid")
        api_key = _validate_session_api_key(payload.get("api_key"))
        models = _validated_models(payload.get("models"), count=profile.participant_count)
        maximum_ticks = payload.get("maximum_ticks", 1_800)
        if profile.authority_kind == "solo_episode" and (
            isinstance(maximum_ticks, bool)
            or not isinstance(maximum_ticks, int)
            or not 1 <= maximum_ticks <= 18_000
        ):
            raise ValueError("Lab game tick budget is invalid")
    max_provider_calls = payload.get(
        "max_provider_calls",
        1_080 if profile.authority_kind == "trio_series" else 2_160,
    )
    maximum_call_limit = 1_080 if profile.authority_kind == "trio_series" else 2_160
    if profile.authority_kind != "solo_episode" and (
        isinstance(max_provider_calls, bool)
        or not isinstance(max_provider_calls, int)
        or not 1 <= max_provider_calls <= maximum_call_limit
    ):
        raise ValueError("Lab game provider-call budget is invalid")
    if profile.authority_kind == "solo_episode" and "max_provider_calls" in payload:
        raise ValueError("Solo game does not accept a series call budget")
    if profile.authority_kind != "solo_episode" and "maximum_ticks" in payload:
        raise ValueError("Series game does not accept a solo tick budget")
    return {
        "api_key": api_key,
        "game_id": game_id,
        "max_provider_calls": max_provider_calls,
        "maximum_ticks": maximum_ticks,
        "mode": mode,
        "models": models,
        "profile": profile,
        "provider": provider,
        "seed": seed,
    }


async def _start_generic_game_authority(
    request: Request, values: Mapping[str, Any]
) -> tuple[Any, AttachedGameRun]:
    profile = values.get("profile")
    if not isinstance(profile, GameRuntimeProfile) or profile.canonical_task_id is None:
        raise ValueError("Lab game runtime profile is invalid")
    authority = _generic_authority_service(request, profile.authority_kind)
    provider = str(values["provider"])
    models = tuple(values["models"])
    seed = int(values["seed"])
    task_id = profile.canonical_task_id

    if profile.authority_kind == "solo_episode":
        created = await authority.create(
            provider=provider,
            model=models[0],
            task_id=task_id,
            seed=seed,
            api_key=values["api_key"],
            maximum_episode_ticks=values["maximum_ticks"],
            scenario_id=profile.canonical_scenario_id if values["mode"] == "demo" else None,
        )
        source_id = created.get("episode_id") if isinstance(created, Mapping) else None
        entrants = (
            RunEntrant(
                entrant_id="entrant_0",
                model_id=models[0],
                provider=provider,
                display_name="Agent",
            ),
        )
        budget = {
            "maximum_ticks": values["maximum_ticks"],
            "scope": "episode",
        }
        configuration: dict[str, object] = {
            "interface_profile": "hybrid-visible-v1",
            "protocol_version": profile.protocol_version,
            "runtime_profile_id": profile.profile_id,
        }
    elif profile.authority_kind == "paired_series":
        authority_entrants = tuple(
            (
                {"provider": provider, "model": model}
                if provider == "demo"
                else {
                    "provider": provider,
                    "model": model,
                    "api_key": values["api_key"],
                }
            )
            for model in models
        )
        created = await authority.create(
            entrants=authority_entrants,
            seed=seed,
            max_live_provider_calls=values["max_provider_calls"],
            task_id=task_id,
        )
        source_id = created.get("series_id") if isinstance(created, Mapping) else None
        entrants = tuple(
            RunEntrant(
                entrant_id=f"entrant_{index}",
                model_id=model,
                provider=provider,
                display_name=("Alpha", "Bravo")[index],
            )
            for index, model in enumerate(models)
        )
        budget = {
            "maximum_provider_calls": values["max_provider_calls"],
            "rotations": 2,
            "scope": "series",
        }
        configuration = {
            "protocol_version": profile.protocol_version,
            "runtime_profile_id": profile.profile_id,
            "seat_rotation": "symmetric-two-leg",
        }
    else:
        authority_entrants = tuple(
            {"provider": "demo", "model": entrant.model} for entrant in TRIO_DEMO_ENTRANTS
        )
        created = await authority.create(
            task_id=task_id,
            seed=seed,
            entrants=authority_entrants,
            max_provider_calls=values["max_provider_calls"],
        )
        source_id = created.get("series_id") if isinstance(created, Mapping) else None
        entrants = tuple(
            RunEntrant(
                entrant_id=entrant.entrant_id,
                model_id=entrant.model,
                provider="demo",
                display_name=entrant.display_name,
            )
            for entrant in TRIO_DEMO_ENTRANTS
        )
        budget = {
            "maximum_provider_calls": values["max_provider_calls"],
            "rotations": 3,
            "scope": "series",
        }
        configuration = {
            "protocol_version": profile.protocol_version,
            "runtime_profile_id": profile.profile_id,
            "seat_rotation": "cyclic-three-leg",
        }

    if not isinstance(source_id, str):
        raise RuntimeError("Lab game authority identity is invalid")
    launch = AttachedGameRun(
        authority_kind=profile.authority_kind,
        source_id=source_id,
        game_id=str(values["game_id"]),
        game_version=task_id,
        runtime_version=f"godot-{profile.protocol_version.replace('/', '-')}",
        scenario_id=profile.canonical_scenario_id if values["mode"] == "demo" else None,
        entrants=entrants,
        seed=seed,
        budget=budget,
        configuration=configuration,
        mode=RunMode.DEMO if values["mode"] == "demo" else RunMode.EXPLORATORY,
    )
    return authority, launch


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
