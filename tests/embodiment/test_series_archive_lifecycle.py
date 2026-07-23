from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import genesis_arena.embodiment.trio_games.service as trio_service_module
import pytest
from genesis_arena.embodiment.duel.evidence import DuelSeriesExecution
from genesis_arena.embodiment.duel.service import DuelSeriesService
from genesis_arena.embodiment.trio_games.evidence import TrioSeriesExecution
from genesis_arena.embodiment.trio_games.scheduling import TRIO_DEMO_ENTRANTS
from genesis_arena.embodiment.trio_games.service import TrioSeriesService


class _SlowFailingArchive:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def save(self, *_args: object, **_kwargs: object) -> None:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("test archive release was not signalled")
        raise OSError("bounded archive fixture failure")


class _SlowSuccessfulArchive(_SlowFailingArchive):
    def save(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("test archive release was not signalled")
        return SimpleNamespace(
            public_dict=lambda: {
                "evidence": {"state": "ready"},
                "native_replay": {
                    "reason": "participant_video_not_configured",
                    "state": "unavailable",
                },
            }
        )


@pytest.mark.asyncio
async def test_duo_authority_finishes_while_native_archive_is_still_saving() -> None:
    archive = _SlowFailingArchive()

    async def execute(spec, _credentials, _cancel_event):
        execution = object.__new__(DuelSeriesExecution)
        object.__setattr__(execution, "result", SimpleNamespace(public_dict=lambda: {}))
        object.__setattr__(
            execution,
            "evidence",
            SimpleNamespace(
                public=SimpleNamespace(series_id=spec.series_id),
                protected=SimpleNamespace(),
            ),
        )
        return execution

    service = DuelSeriesService(execute, archive=archive)  # type: ignore[arg-type]
    service._evaluation_projection = lambda _record: {}  # type: ignore[method-assign]
    service._timeline_projection = lambda _record: {}  # type: ignore[method-assign]
    created = await service.create(
        entrants=(
            {"provider": "demo", "model": "duelist-alpha-v1"},
            {"provider": "demo", "model": "duelist-bravo-v1"},
        ),
        seed=17,
    )
    try:
        assert await asyncio.to_thread(archive.started.wait, 1)
        status = await service.status(created["series_id"])
        assert status["state"] == "completed"
        assert status["archive"]["evidence"]["state"] == "saving"
        assert status["archive"]["native_replay"] == {"state": "saving"}
        assert await service.archive_status(created["series_id"]) == status["archive"]
        cancelled = await service.cancel(created["series_id"])
        assert cancelled["state"] == "completed"
        assert cancelled["archive"]["evidence"]["state"] == "saving"
        archive.release.set()
        record = service._records[created["series_id"]]
        assert record.task is not None
        await record.task
        terminal = await service.status(created["series_id"])
        assert terminal["state"] == "completed"
        assert terminal["archive"]["evidence"]["state"] == "unavailable"
    finally:
        archive.release.set()
        await service.aclose()


@pytest.mark.asyncio
async def test_duo_projection_failure_cannot_rewrite_completed_authority_result() -> None:
    archive = _SlowFailingArchive()

    async def execute(spec, _credentials, _cancel_event):
        execution = object.__new__(DuelSeriesExecution)
        object.__setattr__(execution, "result", SimpleNamespace(public_dict=lambda: {}))
        object.__setattr__(
            execution,
            "evidence",
            SimpleNamespace(
                public=SimpleNamespace(series_id=spec.series_id),
                protected=SimpleNamespace(),
            ),
        )
        return execution

    service = DuelSeriesService(execute, archive=archive)  # type: ignore[arg-type]

    def fail_projection(_record):
        raise ValueError("protected projection detail")

    service._evaluation_projection = fail_projection  # type: ignore[method-assign]
    created = await service.create(
        entrants=(
            {"provider": "demo", "model": "duelist-alpha-v1"},
            {"provider": "demo", "model": "duelist-bravo-v1"},
        ),
        seed=19,
    )
    try:
        record = service._records[created["series_id"]]
        assert record.task is not None
        await record.task
        status = await service.status(created["series_id"])
        assert status["state"] == "completed"
        assert status["failure"] is None
        assert status["archive"]["evidence"]["state"] == "unavailable"
        assert "protected projection detail" not in repr(status)
        assert archive.started.is_set() is False
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_duo_shutdown_awaits_archive_after_credentials_are_erased() -> None:
    archive = _SlowSuccessfulArchive()

    async def execute(spec, credentials, _cancel_event):
        assert credentials
        assert all(not credential.closed for credential in credentials.values())
        execution = object.__new__(DuelSeriesExecution)
        object.__setattr__(execution, "result", SimpleNamespace(public_dict=lambda: {}))
        object.__setattr__(
            execution,
            "evidence",
            SimpleNamespace(
                public=SimpleNamespace(series_id=spec.series_id),
                protected=SimpleNamespace(),
            ),
        )
        return execution

    service = DuelSeriesService(execute, archive=archive)  # type: ignore[arg-type]
    service._evaluation_projection = lambda _record: {}  # type: ignore[method-assign]
    service._timeline_projection = lambda _record: {}  # type: ignore[method-assign]
    created = await service.create(
        entrants=(
            {
                "api_key": "fixture-credential-alpha",
                "model": "model-alpha",
                "provider": "openai",
            },
            {
                "api_key": "fixture-credential-bravo",
                "model": "model-bravo",
                "provider": "openai",
            },
        ),
        seed=29,
    )

    assert await asyncio.to_thread(archive.started.wait, 1)
    record = service._records[created["series_id"]]
    assert record.state == "completed"
    assert all(credential.closed for credential in record.credentials.values())

    close_task = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    assert close_task.done() is False
    archive.release.set()
    await close_task

    status = await service.status(created["series_id"])
    assert status["state"] == "completed"
    assert status["archive"]["evidence"]["state"] == "ready"


@pytest.mark.asyncio
async def test_trio_authority_finishes_while_native_archive_is_still_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _SlowFailingArchive()
    monkeypatch.setattr(trio_service_module, "_timeline", lambda _bundle: {})

    async def execute(_spec, _cancel_event):
        execution = object.__new__(TrioSeriesExecution)
        object.__setattr__(execution, "result", SimpleNamespace(public_dict=lambda: {}))
        object.__setattr__(
            execution,
            "evidence",
            SimpleNamespace(public=SimpleNamespace(), protected=SimpleNamespace()),
        )
        object.__setattr__(execution, "evaluation", {})
        return execution

    service = TrioSeriesService(execute, archive=archive)  # type: ignore[arg-type]
    created = await service.create(
        task_id="trio-relay-v0",
        seed=23,
        entrants=tuple(
            {"provider": "demo", "model": entrant.model}
            for entrant in TRIO_DEMO_ENTRANTS
        ),
    )
    try:
        assert await asyncio.to_thread(archive.started.wait, 1)
        status = await service.status(created["series_id"])
        assert status["state"] == "completed"
        assert status["archive_state"] == "saving"
        assert status["archive"] == {
            "evidence": {"state": "saving"},
            "native_replay": {"state": "saving"},
        }
        assert await service.archive_status(created["series_id"]) == status["archive"]
        archive.release.set()
        record = service._records[created["series_id"]]
        assert record.task is not None
        await record.task
        terminal = await service.status(created["series_id"])
        assert terminal["state"] == "completed"
        assert terminal["archive_state"] == "unavailable"
        assert terminal["archive"] == {
            "evidence": {"state": "unavailable"},
            "native_replay": {
                "state": "unavailable",
                "reason": "participant_video_not_recorded",
            },
        }
        assert record.archive_failure == "trio_archive_save_failed"
    finally:
        archive.release.set()
        await service.aclose()
