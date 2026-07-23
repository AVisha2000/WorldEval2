from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from genesis_arena.embodiment.lab.contracts import LabContractError, RunEntrant, RunMode
from genesis_arena.embodiment.lab.service import LabRunError, LabRunService
from genesis_arena.embodiment.lab_api import public_router, router
from genesis_arena.embodiment.live_labyrinth import (
    MAX_LIVE_SPECTATOR_FRAMES,
    MAX_LIVE_SPECTATOR_PATH_CELLS,
    LiveLabyrinthNotFoundError,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes


class FakeLiveLabyrinth:
    def __init__(self) -> None:
        self.statuses: dict[str, Mapping[str, object]] = {}
        self.replays: dict[str, Mapping[str, Any]] = {}
        self.video_paths: dict[str, Path | None] = {}
        self.observers: dict[str, Mapping[str, Any] | None] = {}
        self.spectator_feeds: dict[str, Mapping[str, Any]] = {}
        self.created: list[Mapping[str, object]] = []
        self.cancelled: list[str] = []
        self.cleanup = None
        self.cleanups: list[Any] = []
        self.create_error: Exception | None = None
        self._create_count = 0

    async def create(self, **kwargs: object) -> Mapping[str, object]:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(dict(kwargs))
        self.cleanup = kwargs["cleanup"]
        self.cleanups.append(kwargs["cleanup"])
        self._create_count += 1
        episode_id = f"ep_labyrinth_fake_{self._create_count:03d}"
        self.statuses[episode_id] = {"state": "queued", "failure": None}
        return {"episode_id": episode_id}

    async def status(self, episode_id: str) -> Mapping[str, object]:
        try:
            return self.statuses[episode_id]
        except KeyError as error:
            raise LiveLabyrinthNotFoundError(episode_id) from error

    async def replay(self, episode_id: str) -> Mapping[str, Any]:
        return self.replays[episode_id]

    async def benchmark_metrics(self, episode_id: str) -> tuple[Mapping[str, object], ...]:
        if episode_id not in self.replays:
            raise KeyError(episode_id)
        return tuple(
            {
                "entrant_id": f"entrant_{index}",
                "metrics": {
                    "budget_charged_calls": 1,
                    "completion_basis_points": 10_000,
                    "input_tokens": 10 + index,
                    "invalid_action_rate_basis_points": 0,
                    "latency_ms": 20 + index,
                    "output_tokens": 5 + index,
                    "path_efficiency_basis_points": 9_000,
                    "recovery_rate_basis_points": 0,
                },
            }
            for index in range(3)
        )

    async def video_path(self, episode_id: str) -> Path | None:
        return self.video_paths.get(episode_id)

    async def observer(self, episode_id: str) -> Mapping[str, Any] | None:
        return self.observers.get(episode_id)

    async def spectator_frames(
        self, episode_id: str, *, after_sequence: int = 0
    ) -> Mapping[str, Any]:
        feed = self.spectator_feeds.get(episode_id)
        if feed is None:
            return {"cursor": after_sequence, "reset_required": False, "frames": []}
        if feed["reset_required"]:
            return feed
        return {
            "cursor": feed["cursor"],
            "reset_required": False,
            "frames": [frame for frame in feed["frames"] if frame["sequence"] > after_sequence],
        }

    async def cancel(self, episode_id: str) -> Mapping[str, object]:
        self.cancelled.append(episode_id)
        self.statuses[episode_id] = {
            "episode_id": episode_id,
            "failure": "lab_run_cancelled",
            "state": "cancelled",
        }
        return {"episode_id": episode_id, "state": "cancelled"}


def _entrants() -> tuple[RunEntrant, ...]:
    return (
        RunEntrant("entrant_0", "gpt-5.6-sol", display_name="Sol"),
        RunEntrant("entrant_1", "gpt-5.6-terra", display_name="Terra"),
        RunEntrant("entrant_2", "gpt-5.6-luna", display_name="Luna"),
    )


def _lab_service_for_client(tmp_path: Path, source: FakeLiveLabyrinth) -> LabRunService:
    """Construct the async-lock service inside a disposable event loop for sync TestClient tests."""

    async def create() -> LabRunService:
        return LabRunService(runs_dir=tmp_path, live_labyrinth=source)

    return asyncio.run(create())


def _safe_replay() -> Mapping[str, Any]:
    return {
        "schema_version": "worldarena/live-labyrinth-run-replay/1",
        "task_id": "trio-maze-race-v1",
        "map": {"map_id": "trio-maze-race-v0", "map_sha256": "a" * 64},
        "racers": [
            {"participant_id": "participant_0", "path": [[7, 13], [8, 13]]},
            {"participant_id": "participant_1", "path": [[7, 13]]},
            {"participant_id": "participant_2", "path": [[7, 13]]},
        ],
        "events": [
            {
                "tick": 10,
                "participant_id": "participant_0",
                "kind": "move",
                "choice": "forward",
                "movement_mode": "single_cell",
            }
        ],
        "result": {"winner_id": "participant_0", "reason": "all_racers_finished"},
    }


def _public_fast_start_mp4() -> bytes:
    """A small container-shaped public fixture for Lab's strict archive boundary.

    Browser playback is not under test here; the service verifies only the minimal fast-start
    container shape required before it may preserve the authority's already-rendered public MP4.
    """

    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"moov" + b"mdat" + b"\x00" * 1_024


def _write_public_fast_start_mp4(path: Path) -> Path:
    path.write_bytes(_public_fast_start_mp4())
    return path


@pytest.mark.asyncio
async def test_labyrinth_lab_run_cancel_uses_private_authority_handle(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_cancel_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    cancelled = await service.cancel(created["run_id"])
    assert source.cancelled == ["ep_labyrinth_cancel_001"]
    assert cancelled["state"]["status"] == "cancelled"
    assert cancelled["state"]["failure_code"] == "lab_run_cancelled"
    assert "ep_labyrinth_cancel_001" not in str(cancelled)


@pytest.mark.asyncio
async def test_labyrinth_lab_run_does_not_invent_cancellation_after_authority_sealed(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    episode_id = "ep_labyrinth_cancel_too_late"

    async def too_late_cancel(requested_episode_id: str) -> Mapping[str, object]:
        source.cancelled.append(requested_episode_id)
        return {"episode_id": requested_episode_id, "state": "running"}

    source.cancel = too_late_cancel  # type: ignore[method-assign]
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id=episode_id,
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    source.statuses[episode_id] = {"state": "running", "failure": None}
    assert (await service.get_run(created["run_id"]))["state"]["status"] == "running"

    pending = await service.cancel(created["run_id"])

    assert pending["state"]["status"] == "running"
    assert source.cancelled == [episode_id]
    source.replays[episode_id] = _safe_replay()
    source.statuses[episode_id] = {"state": "completed", "failure": None}
    completed = await service.get_run(created["run_id"])
    assert completed["state"]["status"] == "completed"
    assert completed["state"]["failure_code"] is None


def _safe_spectator_frame(
    contract: Mapping[str, Any], *, sequence: int, tick: int, provider_calls: int = 3
) -> Mapping[str, Any]:
    names = ("Sol", "Terra", "Luna")
    colors = ("#fbbf24", "#34d399", "#a78bfa")
    return {
        "sequence": sequence,
        "frame": {
            "schema_version": "worldarena/live-labyrinth-observer/1",
            "status": "running",
            "tick": tick,
            "provider_calls": provider_calls,
            "map": {
                "map_id": contract["map_id"],
                "map_sha256": contract["map_sha256"],
                "rows": ["###", "#.#", "###"],
                "start": [1, 1],
                "exit": [1, 1],
            },
            "racers": [
                {
                    "participant_id": f"participant_{index}",
                    "entrant_id": f"entrant_{index}",
                    "display_name": names[index],
                    "color": colors[index],
                    "position": [1, 1],
                    "path": [[1, 1]],
                    "visible_cells": [[1, 1]],
                    "provider_calls": 1,
                    "finished": False,
                }
                for index in range(3)
            ],
        },
    }


@pytest.mark.asyncio
async def test_lab_run_service_persists_only_safe_public_contract_projection_and_cartridge(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_service_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = created["run_id"]
    assert created["resume_supported"] is False
    assert created["replay_available"] is False
    assert created["state"]["status"] == "queued"
    assert created["contract"]["budget"]["scope"] == "per_participant"
    assert created["contract"]["budget"]["participant_call_budget"] < 450
    assert created["cartridge"]["public_projection"]["status"] == "queued"

    run_directory = tmp_path / "lab" / run_id
    expected = {
        "record.json",
        "contract.json",
        "state.json",
        "projection.json",
        "cartridge.json",
    }
    assert {path.name for path in run_directory.iterdir()} == expected
    for path in run_directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
        text = path.read_text(encoding="utf-8").casefold()
        for protected in (
            '"api_key"',
            '"scratchpad"',
            '"navigation_memory"',
            '"raw_output"',
        ):
            assert protected not in text

    source.statuses["ep_labyrinth_service_001"] = {"state": "running", "failure": None}
    source.observers["ep_labyrinth_service_001"] = {
        "schema_version": "worldarena/live-labyrinth-observer/1",
        "status": "running",
        "tick": 4,
        "provider_calls": 3,
        "map": {
            "map_id": "trio-maze-race-v0",
            "map_sha256": "a" * 64,
            "rows": ["###", "#.#", "###"],
            "start": [1, 1],
            "exit": [1, 1],
        },
        "racers": [
            {
                "participant_id": "participant_0",
                "position": [1, 1],
                "path": [[1, 1]],
                "visible_cells": [[1, 1]],
                "provider_calls": 1,
                "finished": False,
            }
        ],
    }
    running = await service.get_run(run_id)
    assert running["state"]["status"] == "running"
    assert running["authority_available"] is True
    running_projection = await service.projection(run_id)
    assert running_projection["snapshot"]["arena"]["tick"] == 4
    assert "navigation_memory" not in str(running_projection).casefold()

    source.statuses["ep_labyrinth_service_001"] = {"state": "completed", "failure": None}
    source.replays["ep_labyrinth_service_001"] = _safe_replay()
    completed = await service.get_run(run_id)
    projection = await service.projection(run_id)
    assert completed["state"]["status"] == "completed"
    assert completed["replay_available"] is True
    assert projection["events"] == _safe_replay()["events"]
    assert completed["cartridge"]["authority_checkpoint_sha256"] is None

    sealed = await service.seal(run_id)
    assert sealed["state"]["status"] == "sealed"
    assert sealed["replay_available"] is True
    verified = await service.verify(run_id)
    assert verified["state"]["status"] == "verified"
    assert verified["cartridge"]["public_projection"]["status"] == "verified"
    verified_projection = await service.projection(run_id)

    reloaded = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    loaded = await reloaded.get_run(run_id)
    assert loaded["state"]["status"] == "verified"
    assert loaded["authority_available"] is False
    reloaded_projection = await reloaded.projection(run_id)
    assert reloaded_projection["projection_sha256"] == verified_projection["projection_sha256"]


@pytest.mark.asyncio
async def test_labyrinth_evidence_lifecycle_swaps_only_after_durable_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    episode_id = "ep_labyrinth_transactional_evidence_001"
    created = await service.create_live_labyrinth(
        episode_id=episode_id,
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = str(created["run_id"])
    source.statuses[episode_id] = {"state": "completed", "failure": None}
    source.replays[episode_id] = _safe_replay()
    assert (await service.get_run(run_id))["state"]["status"] == "completed"

    original_persist = service._persist

    def fail_sealed(record: Any) -> None:
        if record.state.status.value == "sealed":
            raise OSError("fixture seal persistence failure")
        original_persist(record)

    monkeypatch.setattr(service, "_persist", fail_sealed)
    with pytest.raises(LabRunError, match="could not be persisted"):
        await service.seal(run_id)
    assert (await service.get_run(run_id))["state"]["status"] == "completed"

    monkeypatch.setattr(service, "_persist", original_persist)
    assert (await service.seal(run_id))["state"]["status"] == "sealed"

    def fail_verified(record: Any) -> None:
        if record.state.status.value == "verified":
            raise OSError("fixture verify persistence failure")
        original_persist(record)

    monkeypatch.setattr(service, "_persist", fail_verified)
    with pytest.raises(LabRunError, match="could not be persisted"):
        await service.verify(run_id)
    assert (await service.get_run(run_id))["state"]["status"] == "sealed"

    monkeypatch.setattr(service, "_persist", original_persist)
    assert (await service.verify(run_id))["state"]["status"] == "verified"

    original_readback = service._read_durable_record
    readbacks = 0

    def observe_readback(directory: Path) -> Any:
        nonlocal readbacks
        readbacks += 1
        return original_readback(directory)

    monkeypatch.setattr(service, "_read_durable_record", observe_readback)
    assert (await service.verify(run_id))["state"]["status"] == "verified"
    assert readbacks == 1


@pytest.mark.asyncio
async def test_completed_labyrinth_archives_a_public_video_and_reloads_without_episode_state(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    episode_id = "ep_labyrinth_video_archive_001"
    created = await service.create_live_labyrinth(
        episode_id=episode_id,
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = str(created["run_id"])
    source.statuses[episode_id] = {
        "state": "completed",
        "failure": None,
        "video": {"state": "ready"},
    }
    source.replays[episode_id] = _safe_replay()
    authority_video = _write_public_fast_start_mp4(tmp_path / "authority-public.mp4")
    source.video_paths[episode_id] = authority_video

    completed = await service.get_run(run_id)
    archived = tmp_path / "lab" / run_id / "labyrinth-run-broadcast.mp4"
    assert completed["state"]["status"] == "completed"
    assert completed["video_available"] is True
    assert await service.video_path(run_id) == archived
    assert archived.read_bytes() == authority_video.read_bytes()
    assert stat.S_IMODE(archived.stat().st_mode) == 0o600

    # The renderer handle and its source location stay process-only even though the public MP4
    # itself is now durable.  The fixed file name also prevents source path disclosure.
    rendered = canonical_json_bytes(completed).decode("utf-8")
    assert episode_id not in rendered
    assert str(authority_video) not in rendered
    for artifact in (tmp_path / "lab" / run_id).glob("*.json"):
        text = artifact.read_text(encoding="utf-8").casefold()
        for protected in (episode_id, "api_key", "raw_output", "navigation_memory", "scratchpad"):
            assert protected not in text

    # A fresh service intentionally has no authority episode, yet can serve only the archived
    # public MP4 by run id.
    reloaded = LabRunService(runs_dir=tmp_path)
    loaded = await reloaded.get_run(run_id)
    assert loaded["authority_available"] is False
    assert loaded["video_available"] is True
    assert await reloaded.video_path(run_id) == archived


@pytest.mark.asyncio
async def test_archived_labyrinth_video_survives_seal_verify_and_each_restart(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    episode_id = "ep_labyrinth_video_evidence_lifecycle_001"
    created = await service.create_live_labyrinth(
        episode_id=episode_id,
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = str(created["run_id"])
    source.statuses[episode_id] = {
        "state": "completed",
        "failure": None,
        "video": {"state": "ready"},
    }
    source.replays[episode_id] = _safe_replay()
    source.video_paths[episode_id] = _write_public_fast_start_mp4(
        tmp_path / "lifecycle-source.mp4"
    )
    archive = tmp_path / "lab" / run_id / "labyrinth-run-broadcast.mp4"

    assert (await service.get_run(run_id))["video_available"] is True
    sealed = await service.seal(run_id)
    assert sealed["state"]["status"] == "sealed"
    assert sealed["video_available"] is True
    assert await service.video_path(run_id) == archive

    after_seal_restart = LabRunService(runs_dir=tmp_path)
    sealed_reloaded = await after_seal_restart.get_run(run_id)
    assert sealed_reloaded["state"]["status"] == "sealed"
    assert sealed_reloaded["video_available"] is True
    assert await after_seal_restart.video_path(run_id) == archive

    verified = await after_seal_restart.verify(run_id)
    assert verified["state"]["status"] == "verified"
    assert verified["video_available"] is True
    assert await after_seal_restart.video_path(run_id) == archive

    after_verify_restart = LabRunService(runs_dir=tmp_path)
    verified_reloaded = await after_verify_restart.get_run(run_id)
    assert verified_reloaded["state"]["status"] == "verified"
    assert verified_reloaded["video_available"] is True
    assert await after_verify_restart.video_path(run_id) == archive


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("missing", "malformed", "protected", "symlink"))
async def test_completed_labyrinth_video_archive_fails_closed_without_fake_content(
    tmp_path: Path, kind: str
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    episode_id = f"ep_labyrinth_video_{kind}_001"
    created = await service.create_live_labyrinth(
        episode_id=episode_id,
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = str(created["run_id"])
    source.statuses[episode_id] = {
        "state": "completed",
        "failure": None,
        "video": {"state": "ready"},
    }
    source.replays[episode_id] = _safe_replay()
    if kind == "malformed":
        malformed = (tmp_path / "not-public.mp4").resolve()
        malformed.write_bytes(b"not-an-mp4")
        source.video_paths[episode_id] = malformed
    elif kind == "protected":
        protected = (tmp_path / "protected-source.mp4").resolve()
        protected.write_bytes(_public_fast_start_mp4() + b"navigation_memory")
        source.video_paths[episode_id] = protected
    elif kind == "symlink":
        public_source = _write_public_fast_start_mp4(tmp_path / "public-source.mp4")
        symlink = tmp_path / "untrusted-source.mp4"
        symlink.symlink_to(public_source)
        source.video_paths[episode_id] = symlink

    completed = await service.get_run(run_id)
    archive = tmp_path / "lab" / run_id / "labyrinth-run-broadcast.mp4"
    assert completed["state"]["status"] == "completed"
    assert completed["replay_available"] is True
    assert completed["video_available"] is False
    assert await service.video_path(run_id) is None
    assert not archive.exists()
    assert not list(archive.parent.glob(".labyrinth-run-broadcast.mp4.*.tmp"))

    reloaded = LabRunService(runs_dir=tmp_path)
    assert (await reloaded.get_run(run_id))["video_available"] is False
    assert await reloaded.video_path(run_id) is None


@pytest.mark.asyncio
async def test_completed_labyrinth_rejects_a_corrupted_archived_video_after_reload(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    episode_id = "ep_labyrinth_video_corrupt_001"
    created = await service.create_live_labyrinth(
        episode_id=episode_id,
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = str(created["run_id"])
    source.statuses[episode_id] = {
        "state": "completed",
        "failure": None,
        "video": {"state": "ready"},
    }
    source.replays[episode_id] = _safe_replay()
    source.video_paths[episode_id] = _write_public_fast_start_mp4(tmp_path / "public-source.mp4")
    assert (await service.get_run(run_id))["video_available"] is True

    archive = tmp_path / "lab" / run_id / "labyrinth-run-broadcast.mp4"
    archive.write_bytes(b"corrupted")
    archive.chmod(0o600)
    reloaded = LabRunService(runs_dir=tmp_path)
    loaded = await reloaded.get_run(run_id)
    assert loaded["state"]["status"] == "completed"
    assert loaded["replay_available"] is True
    assert loaded["video_available"] is False
    assert await reloaded.video_path(run_id) is None


def test_lab_video_route_serves_only_the_durable_archived_mp4_by_run_id(tmp_path: Path) -> None:
    source = FakeLiveLabyrinth()
    service = _lab_service_for_client(tmp_path, source)
    episode_id = "ep_labyrinth_video_http_001"

    async def create_completed_run() -> str:
        created = await service.create_live_labyrinth(
            episode_id=episode_id,
            entrants=_entrants(),
            provider_call_budget=450,
            vision_range_cells=4,
            skill_mode="none",
        )
        source.statuses[episode_id] = {
            "state": "completed",
            "failure": None,
            "video": {"state": "ready"},
        }
        source.replays[episode_id] = _safe_replay()
        source.video_paths[episode_id] = _write_public_fast_start_mp4(
            tmp_path / "public-source.mp4"
        )
        await service.get_run(str(created["run_id"]))
        return str(created["run_id"])

    run_id = asyncio.run(create_completed_run())
    app = FastAPI()
    # Simulate Uvicorn restart: the HTTP layer receives a new service with no retained episode.
    app.state.lab_runs = _lab_service_for_client(tmp_path, FakeLiveLabyrinth())
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(f"/api/lab/runs/{run_id}/video")
        unavailable = client.get("/api/lab/runs/run_labyrinth_unknown/video")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["cache-control"] == "no-store"
    assert response.content == _public_fast_start_mp4()
    assert episode_id.encode("utf-8") not in response.content
    assert unavailable.status_code == 404
    assert unavailable.json() == {"detail": {"code": "lab_run_not_found"}}


def test_lab_video_route_survives_seal_verify_and_service_restart(tmp_path: Path) -> None:
    source = FakeLiveLabyrinth()
    service = _lab_service_for_client(tmp_path, source)
    episode_id = "ep_labyrinth_video_http_lifecycle_001"

    async def create_completed_run() -> str:
        created = await service.create_live_labyrinth(
            episode_id=episode_id,
            entrants=_entrants(),
            provider_call_budget=450,
            vision_range_cells=4,
            skill_mode="none",
        )
        source.statuses[episode_id] = {
            "state": "completed",
            "failure": None,
            "video": {"state": "ready"},
        }
        source.replays[episode_id] = _safe_replay()
        source.video_paths[episode_id] = _write_public_fast_start_mp4(
            tmp_path / "http-lifecycle-source.mp4"
        )
        await service.get_run(str(created["run_id"]))
        return str(created["run_id"])

    run_id = asyncio.run(create_completed_run())
    app = FastAPI()
    app.state.lab_runs = service
    app.include_router(router)
    with TestClient(app) as client:
        sealed = client.post(f"/api/lab/runs/{run_id}/seal")
        sealed_video = client.get(f"/api/lab/runs/{run_id}/video")
        verified = client.post(f"/api/lab/runs/{run_id}/verify")
        verified_video = client.get(f"/api/lab/runs/{run_id}/video")

    assert sealed.status_code == 200
    assert sealed.json()["state"]["status"] == "sealed"
    assert sealed.json()["video_available"] is True
    assert sealed_video.status_code == 200
    assert sealed_video.content == _public_fast_start_mp4()
    assert verified.status_code == 200
    assert verified.json()["state"]["status"] == "verified"
    assert verified.json()["video_available"] is True
    assert verified_video.status_code == 200
    assert verified_video.content == _public_fast_start_mp4()

    restarted_app = FastAPI()
    restarted_app.state.lab_runs = _lab_service_for_client(tmp_path, FakeLiveLabyrinth())
    restarted_app.include_router(router)
    with TestClient(restarted_app) as client:
        reloaded = client.get(f"/api/lab/runs/{run_id}")
        restarted_video = client.get(f"/api/lab/runs/{run_id}/video")

    assert reloaded.status_code == 200
    assert reloaded.json()["state"]["status"] == "verified"
    assert reloaded.json()["video_available"] is True
    assert restarted_video.status_code == 200
    assert restarted_video.headers["content-type"].startswith("video/mp4")
    assert restarted_video.content == _public_fast_start_mp4()


@pytest.mark.asyncio
async def test_lab_spectator_feed_is_cursored_safe_and_never_exposes_episode_identity(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_spectator_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    run_id = str(created["run_id"])
    contract = created["contract"]
    source.statuses["ep_labyrinth_spectator_001"] = {"state": "running", "failure": None}
    source.spectator_feeds["ep_labyrinth_spectator_001"] = {
        "cursor": 2,
        "reset_required": False,
        "frames": [
            _safe_spectator_frame(contract, sequence=1, tick=4),
            _safe_spectator_frame(contract, sequence=2, tick=8),
        ],
    }

    feed = await service.spectator(run_id, after_sequence=0)
    assert set(feed) == {"schema_version", "run_id", "cursor", "reset_required", "frames"}
    assert feed["schema_version"] == "worldeval/lab-live-spectator-feed/1"
    assert feed["run_id"] == run_id
    assert feed["cursor"] == 2
    assert [item["sequence"] for item in feed["frames"]] == [1, 2]
    rendered = canonical_json_bytes(feed).decode("utf-8").casefold()
    for protected in (
        "ep_labyrinth_spectator_001",
        "navigation_memory",
        "scratchpad",
        "raw_output",
    ):
        assert protected not in rendered
    assert '"provider"' not in rendered
    assert '"model"' not in rendered

    feed["frames"][0]["frame"]["map"]["rows"][0] = "tampered"
    after_one = await service.spectator(run_id, after_sequence=1)
    assert after_one["reset_required"] is False
    assert [item["sequence"] for item in after_one["frames"]] == [2]
    assert after_one["frames"][0]["frame"]["map"]["rows"][0] == "###"

    source.spectator_feeds["ep_labyrinth_spectator_001"] = {
        "cursor": 6,
        "reset_required": True,
        "frames": [_safe_spectator_frame(contract, sequence=6, tick=24)],
    }
    overflowed = await service.spectator(run_id, after_sequence=0)
    assert overflowed["reset_required"] is True
    assert [item["sequence"] for item in overflowed["frames"]] == [6]

    source.statuses["ep_labyrinth_spectator_001"] = {
        "state": "completed",
        "failure": None,
    }
    source.replays["ep_labyrinth_spectator_001"] = _safe_replay()
    source.spectator_feeds["ep_labyrinth_spectator_001"] = {
        "cursor": 7,
        "reset_required": False,
        "frames": [_safe_spectator_frame(contract, sequence=7, tick=28)],
    }
    completed = await service.spectator(run_id, after_sequence=6)
    assert completed["reset_required"] is False
    assert [item["sequence"] for item in completed["frames"]] == [7]
    assert (await service.get_run(run_id))["state"]["status"] == "completed"


@pytest.mark.asyncio
async def test_lab_spectator_rejects_private_or_provider_frame_material(tmp_path: Path) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_spectator_private_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    contract = created["contract"]
    unsafe = _safe_spectator_frame(contract, sequence=1, tick=4)
    unsafe_frame = unsafe["frame"]
    assert isinstance(unsafe_frame, dict)
    unsafe_frame["racers"][0]["provider"] = "must-not-reach-browser"
    source.statuses["ep_labyrinth_spectator_private_001"] = {
        "state": "running",
        "failure": None,
    }
    source.spectator_feeds["ep_labyrinth_spectator_private_001"] = {
        "cursor": 1,
        "reset_required": False,
        "frames": [unsafe],
    }
    with pytest.raises(LabRunError, match="spectator racer fields"):
        await service.spectator(str(created["run_id"]))

    wrong_run_frame = _safe_spectator_frame(contract, sequence=1, tick=4)
    wrong_run_frame["frame"]["racers"][0]["entrant_id"] = "entrant_from_other_run"
    source.spectator_feeds["ep_labyrinth_spectator_private_001"] = {
        "cursor": 1,
        "reset_required": False,
        "frames": [wrong_run_frame],
    }
    with pytest.raises(LabRunError, match="differs from the run contract"):
        await service.spectator(str(created["run_id"]))


@pytest.mark.asyncio
async def test_lab_spectator_rejects_an_unbounded_live_trail(tmp_path: Path) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_spectator_trail_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    contract = created["contract"]
    oversized = _safe_spectator_frame(contract, sequence=1, tick=4)
    oversized_frame = oversized["frame"]
    assert isinstance(oversized_frame, dict)
    oversized_frame["racers"][0]["path"] = [
        [1, 1] for _ in range(MAX_LIVE_SPECTATOR_PATH_CELLS + 1)
    ]
    source.statuses["ep_labyrinth_spectator_trail_001"] = {
        "state": "running",
        "failure": None,
    }
    source.spectator_feeds["ep_labyrinth_spectator_trail_001"] = {
        "cursor": 1,
        "reset_required": False,
        "frames": [oversized],
    }

    with pytest.raises(LabRunError, match="spectator racer geometry"):
        await service.spectator(str(created["run_id"]))


@pytest.mark.asyncio
async def test_lab_spectator_rejects_oversized_or_off_map_source_geometry(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_spectator_bounds_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    contract = created["contract"]
    source.statuses["ep_labyrinth_spectator_bounds_001"] = {
        "state": "running",
        "failure": None,
    }
    source.spectator_feeds["ep_labyrinth_spectator_bounds_001"] = {
        "cursor": MAX_LIVE_SPECTATOR_FRAMES + 1,
        "reset_required": False,
        "frames": [
            _safe_spectator_frame(contract, sequence=index + 1, tick=index + 1)
            for index in range(MAX_LIVE_SPECTATOR_FRAMES + 1)
        ],
    }
    with pytest.raises(LabRunError, match="spectator feed is too large"):
        await service.spectator(str(created["run_id"]))

    off_map = _safe_spectator_frame(contract, sequence=1, tick=1)
    off_map_frame = off_map["frame"]
    assert isinstance(off_map_frame, dict)
    off_map_frame["racers"][0]["visible_cells"] = [[0, 0]]
    source.spectator_feeds["ep_labyrinth_spectator_bounds_001"] = {
        "cursor": 1,
        "reset_required": False,
        "frames": [off_map],
    }
    with pytest.raises(LabRunError, match="spectator racer geometry"):
        await service.spectator(str(created["run_id"]))


@pytest.mark.asyncio
async def test_lab_clone_is_draft_lineage_and_cannot_write_protected_configuration(
    tmp_path: Path,
) -> None:
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=FakeLiveLabyrinth())
    root = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_clone_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
        mode=RunMode.EXPLORATORY,
    )
    clone = await service.clone(
        root["run_id"], changes={"configuration": {"vision_range_cells": 8}}
    )
    assert clone["state"]["status"] == "draft"
    assert "episode_id" not in clone
    assert clone["resume_supported"] is False
    assert clone["contract"]["parent_contract_sha256"] == root["contract"]["contract_sha256"]
    assert clone["contract"]["lineage_diff"]["configuration"]["to"]["vision_range_cells"] == 8

    with pytest.raises(LabContractError, match="protected"):
        await service.clone(
            root["run_id"], changes={"configuration": {"navigation_memory": "private"}}
        )


@pytest.mark.asyncio
async def test_exact_clone_reserves_once_then_attaches_a_fresh_live_episode(
    tmp_path: Path,
) -> None:
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=FakeLiveLabyrinth())
    root = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_clone_launch_root",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    clone = await service.clone(root["run_id"], changes={"configuration": {}})
    clone_id = str(clone["run_id"])
    clone_contract = clone["contract"]
    assert clone_contract["lineage_diff"] == {}

    first, second = await asyncio.gather(
        service.reserve_draft_labyrinth_launch(clone_id),
        service.reserve_draft_labyrinth_launch(clone_id),
        return_exceptions=True,
    )
    reservations = [value for value in (first, second) if not isinstance(value, Exception)]
    failures = [value for value in (first, second) if isinstance(value, Exception)]
    assert len(reservations) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], LabRunError)
    launch = reservations[0]
    assert launch.provider == "openai"
    assert launch.map_spec.map_id == "trio-maze-race-v0"

    attached = await service.attach_draft_labyrinth_episode(
        clone_id, episode_id="ep_labyrinth_clone_launch_fresh"
    )
    assert attached["state"]["status"] == "queued"
    assert attached["authority_available"] is True
    assert attached["contract"] == clone_contract
    assert attached["resume_supported"] is False
    manifest = (tmp_path / "lab" / clone_id / "record.json").read_text(encoding="utf-8")
    assert "ep_labyrinth_clone_launch_fresh" not in manifest


@pytest.mark.asyncio
async def test_draft_launch_rejects_mutated_map_provider_and_configuration(
    tmp_path: Path,
) -> None:
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=FakeLiveLabyrinth())
    root = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_clone_reject_root",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    invalid_entrants = [entrant.as_dict() for entrant in _entrants()]
    invalid_entrants[0]["provider"] = "unsupported-provider"
    clones = (
        await service.clone(
            root["run_id"],
            changes={"map_id": "unapproved-map"},
        ),
        await service.clone(root["run_id"], changes={"entrants": invalid_entrants}),
        await service.clone(
            root["run_id"], changes={"configuration": {"corridor_commands": False}}
        ),
    )
    for clone in clones:
        with pytest.raises(LabRunError, match="draft"):
            await service.reserve_draft_labyrinth_launch(str(clone["run_id"]))
        assert (await service.get_run(str(clone["run_id"])))["state"]["status"] == "draft"


@pytest.mark.asyncio
async def test_lab_rejects_private_upstream_replay_before_writing_a_projection(
    tmp_path: Path,
) -> None:
    source = FakeLiveLabyrinth()
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=source)
    run = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_private_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    source.statuses["ep_labyrinth_private_001"] = {"state": "completed", "failure": None}
    source.replays["ep_labyrinth_private_001"] = {
        **_safe_replay(),
        "navigation_memory": {"do_not": "publish"},
    }
    with pytest.raises(LabContractError, match="protected"):
        await service.get_run(run["run_id"])
    state = (tmp_path / "lab" / run["run_id"] / "state.json").read_text(encoding="utf-8")
    assert '"status":"queued"' in state


def test_lab_api_catalogue_launch_and_clone_never_echo_the_session_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeLiveLabyrinth()
    app = FastAPI()
    app.state.embodiment_live_labyrinth = source
    app.state.lab_runs = _lab_service_for_client(tmp_path, source)
    app.include_router(router)

    class FakeAdapter:
        provider_name = "openai"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "genesis_arena.embodiment.lab_api.provider_adapter",
        lambda _provider, _credential: FakeAdapter(),
    )
    secret = "session-key-must-never-be-returned"
    payload = {
        "provider": "openai",
        "api_key": secret,
        "entrants": [
            {"display_name": "Sol", "model": "gpt-5.6-sol"},
            {"display_name": "Terra", "model": "gpt-5.6-terra"},
            {"display_name": "Luna", "model": "gpt-5.6-luna"},
        ],
        "skill_mode": "none",
    }
    with TestClient(app) as client:
        catalogue = client.get("/api/lab/games")
        detail = client.get("/api/lab/games/labyrinth-run")
        launched = client.post("/api/lab/runs/labyrinth", json=payload)
        assert catalogue.status_code == 200
        assert detail.status_code == 200
        assert launched.status_code == 202
        assert catalogue.json()["catalog_sha256"]
        assert detail.json()["id"] == "labyrinth-run"
        assert secret not in launched.text
        assert "api_key" not in launched.text
        run_id = launched.json()["run_id"]
        listed = client.get("/api/lab/runs")
        projection = client.get(f"/api/lab/runs/{run_id}/projection")
        spectator = client.get(f"/api/lab/runs/{run_id}/spectator?after=0")
        invalid_spectator = client.get(f"/api/lab/runs/{run_id}/spectator?after={secret}")
        clone = client.post(
            f"/api/lab/runs/{run_id}/clone",
            json={"changes": {"configuration": {"vision_range_cells": 8}}},
        )
        bad = client.post("/api/lab/runs/labyrinth", json={**payload, "unknown": secret})
    assert listed.status_code == 200
    assert projection.status_code == 200
    assert spectator.status_code == 200
    assert spectator.headers["cache-control"] == "no-store"
    assert set(spectator.json()) == {
        "schema_version",
        "run_id",
        "cursor",
        "reset_required",
        "frames",
    }
    assert "episode_id" not in spectator.text
    assert invalid_spectator.status_code == 422
    assert secret not in invalid_spectator.text
    assert clone.status_code == 201
    assert bad.status_code == 422 and secret not in bad.text
    assert listed.json()["runs"][0]["run_id"] == run_id
    assert projection.json()["status"] == "queued"
    assert clone.json()["state"]["status"] == "draft"
    assert source.created and source.created[0]["skill_mode"] == "none"
    assert source.cleanup is not None
    asyncio.run(source.cleanup())
    for path in (tmp_path / "lab").rglob("*.json"):
        assert secret not in path.read_text(encoding="utf-8")


def test_lab_api_launches_an_exact_clone_once_with_a_fresh_ephemeral_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeLiveLabyrinth()
    app = FastAPI()
    app.state.embodiment_live_labyrinth = source
    app.state.lab_runs = _lab_service_for_client(tmp_path, source)
    app.include_router(router)

    class FakeAdapter:
        provider_name = "openai"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "genesis_arena.embodiment.lab_api.provider_adapter",
        lambda _provider, _credential: FakeAdapter(),
    )
    session_key = "draft-launch-key-must-never-be-returned"
    root_payload = {
        "provider": "openai",
        "api_key": session_key,
        "entrants": [
            {"display_name": "Sol", "model": "gpt-5.6-sol"},
            {"display_name": "Terra", "model": "gpt-5.6-terra"},
            {"display_name": "Luna", "model": "gpt-5.6-luna"},
        ],
        "skill_mode": "none",
    }
    with TestClient(app) as client:
        root = client.post("/api/lab/runs/labyrinth", json=root_payload)
        assert root.status_code == 202
        root_id = root.json()["run_id"]

        non_openai_entrants = root.json()["contract"]["entrants"]
        for entrant in non_openai_entrants:
            entrant["provider"] = "anthropic"
        non_openai = client.post(
            f"/api/lab/runs/{root_id}/clone",
            json={"changes": {"entrants": non_openai_entrants}},
        )
        assert non_openai.status_code == 201
        rejected_provider = client.post(
            f"/api/lab/runs/{non_openai.json()['run_id']}/launch",
            json={"api_key": session_key},
        )
        assert rejected_provider.status_code == 422
        assert source.created and len(source.created) == 1
        assert (
            client.get(f"/api/lab/runs/{non_openai.json()['run_id']}").json()["state"]["status"]
            == "draft"
        )

        clone = client.post(
            f"/api/lab/runs/{root_id}/clone",
            json={"changes": {"configuration": {}}},
        )
        assert clone.status_code == 201
        clone_id = clone.json()["run_id"]
        clone_contract = clone.json()["contract"]
        assert clone_contract["lineage_diff"] == {}
        invalid_shape = client.post(
            f"/api/lab/runs/{clone_id}/launch",
            json={"api_key": session_key, "provider": "openai"},
        )
        launched = client.post(f"/api/lab/runs/{clone_id}/launch", json={"api_key": session_key})
        duplicate = client.post(f"/api/lab/runs/{clone_id}/launch", json={"api_key": session_key})

    assert invalid_shape.status_code == 422
    assert launched.status_code == 202
    assert launched.headers["cache-control"] == "no-store"
    assert launched.json()["state"]["status"] == "queued"
    assert launched.json()["authority_available"] is True
    assert launched.json()["contract"] == clone_contract
    assert launched.json()["resume_supported"] is False
    assert duplicate.status_code == 422
    assert len(source.created) == 2
    clone_launch = source.created[-1]
    assert clone_launch["map_spec"].map_id == "trio-maze-race-v0"
    assert (
        clone_launch["participant_call_budget"]
        == clone_contract["budget"]["participant_call_budget"]
    )
    for response in (invalid_shape, launched, duplicate, rejected_provider):
        assert session_key not in response.text
        assert "api_key" not in response.text
    for cleanup in source.cleanups:
        asyncio.run(cleanup())
    for path in (tmp_path / "lab").rglob("*.json"):
        assert session_key not in path.read_text(encoding="utf-8")


def test_lab_api_marks_a_reserved_clone_failed_when_authority_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeLiveLabyrinth()
    app = FastAPI()
    app.state.embodiment_live_labyrinth = source
    app.state.lab_runs = _lab_service_for_client(tmp_path, source)
    app.include_router(router)

    class FakeAdapter:
        provider_name = "openai"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "genesis_arena.embodiment.lab_api.provider_adapter",
        lambda _provider, _credential: FakeAdapter(),
    )
    session_key = "authority-start-key-must-never-be-returned"
    payload = {
        "provider": "openai",
        "api_key": session_key,
        "entrants": [
            {"display_name": "Sol", "model": "gpt-5.6-sol"},
            {"display_name": "Terra", "model": "gpt-5.6-terra"},
            {"display_name": "Luna", "model": "gpt-5.6-luna"},
        ],
    }
    with TestClient(app) as client:
        root = client.post("/api/lab/runs/labyrinth", json=payload)
        clone = client.post(
            f"/api/lab/runs/{root.json()['run_id']}/clone",
            json={"changes": {"configuration": {}}},
        )
        clone_id = clone.json()["run_id"]
        source.create_error = RuntimeError("private authority failure detail")
        failed = client.post(f"/api/lab/runs/{clone_id}/launch", json={"api_key": session_key})
        saved = client.get(f"/api/lab/runs/{clone_id}")

    assert failed.status_code == 503
    assert failed.json() == {"detail": {"code": "labyrinth_launch_unavailable"}}
    assert "private authority failure detail" not in failed.text
    assert session_key not in failed.text
    assert saved.json()["state"]["status"] == "failed"
    assert saved.json()["state"]["failure_code"] == "labyrinth_episode_unavailable"
    assert saved.json()["authority_available"] is False
    assert source.cancelled == []
    for cleanup in source.cleanups:
        asyncio.run(cleanup())
    for path in (tmp_path / "lab").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert session_key not in text
        assert "private authority failure detail" not in text


def test_lab_api_cancels_a_fresh_authority_when_clone_attachment_cannot_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeLiveLabyrinth()
    app = FastAPI()
    app.state.embodiment_live_labyrinth = source
    service = _lab_service_for_client(tmp_path, source)
    app.state.lab_runs = service
    app.include_router(router)

    class FakeAdapter:
        provider_name = "openai"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "genesis_arena.embodiment.lab_api.provider_adapter",
        lambda _provider, _credential: FakeAdapter(),
    )
    session_key = "persistence-failure-key-must-never-be-returned"
    payload = {
        "provider": "openai",
        "api_key": session_key,
        "entrants": [
            {"display_name": "Sol", "model": "gpt-5.6-sol"},
            {"display_name": "Terra", "model": "gpt-5.6-terra"},
            {"display_name": "Luna", "model": "gpt-5.6-luna"},
        ],
    }
    with TestClient(app) as client:
        root = client.post("/api/lab/runs/labyrinth", json=payload)
        clone = client.post(
            f"/api/lab/runs/{root.json()['run_id']}/clone",
            json={"changes": {"configuration": {}}},
        )
        clone_id = clone.json()["run_id"]
        original_persist = service._persist

        def fail_only_attachment(record: object) -> None:
            episode_id = getattr(record, "episode_id", None)
            if episode_id is not None:
                raise OSError("private persistence failure detail")
            original_persist(record)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "_persist", fail_only_attachment)
        failed = client.post(f"/api/lab/runs/{clone_id}/launch", json={"api_key": session_key})
        saved = client.get(f"/api/lab/runs/{clone_id}")

    assert failed.status_code == 503
    assert "private persistence failure detail" not in failed.text
    assert session_key not in failed.text
    assert source.cancelled == ["ep_labyrinth_fake_002"]
    assert saved.json()["state"]["status"] == "failed"
    assert saved.json()["authority_available"] is False
    for cleanup in source.cleanups:
        asyncio.run(cleanup())


@pytest.mark.asyncio
async def test_durable_lab_manifest_never_retains_the_live_episode_handle(tmp_path: Path) -> None:
    service = LabRunService(runs_dir=tmp_path, live_labyrinth=FakeLiveLabyrinth())
    created = await service.create_live_labyrinth(
        episode_id="ep_labyrinth_manifest_001",
        entrants=_entrants(),
        provider_call_budget=450,
        vision_range_cells=4,
        skill_mode="none",
    )
    manifest = (tmp_path / "lab" / str(created["run_id"]) / "record.json").read_text(
        encoding="utf-8"
    )
    assert '"episode_id":null' in manifest
    assert "ep_labyrinth_manifest_001" not in manifest


def test_unlisted_public_game_guide_exposes_no_team_run_material(tmp_path: Path) -> None:
    async def make_service() -> LabRunService:
        return LabRunService(runs_dir=tmp_path)

    app = FastAPI()
    app.state.lab_runs = asyncio.run(make_service())
    app.include_router(public_router)

    with TestClient(app) as client:
        catalogue = client.get("/api/public/games")
        guide = client.get("/api/public/games/labyrinth-run")
        benchmark = client.get("/api/public/games/labyrinth-run/benchmark")
        spectator = client.get("/api/public/games/labyrinth-run/spectator")

    assert catalogue.status_code == 200
    assert catalogue.headers["x-robots-tag"] == "noindex, nofollow"
    assert catalogue.json()["games"]
    assert guide.status_code == 200
    assert guide.headers["x-robots-tag"] == "noindex, nofollow"
    assert guide.json()["id"] == "labyrinth-run"
    assert "api_key" not in guide.text
    assert benchmark.status_code == 200
    assert benchmark.json()["verified_results"] == []
    assert "run_id" not in benchmark.text
    assert spectator.status_code == 404
