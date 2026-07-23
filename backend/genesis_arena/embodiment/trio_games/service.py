"""Session-only lifecycle plus durable restart support for trio Demo series."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Mapping

from .archive import ArchivedTrioSeries, TrioSeriesArchive
from .common import TRIO_PARTICIPANT_IDS
from .evidence import TrioSeriesEvidence, TrioSeriesEvidenceBundle, TrioSeriesExecution
from .participant_frames import (
    TrioParticipantFrameSnapshot,
    TrioParticipantFrameStore,
    TrioParticipantPreviewChannel,
    TrioParticipantPreviewSnapshot,
)
from .scheduling import TRIO_DEMO_ENTRANTS, TRIO_TASK_IDS, TrioSeriesPlan, build_cyclic_trio_plan
from .series import TrioSeriesResult


class TrioSeriesNotFoundError(KeyError):
    pass


class TrioSeriesNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrioSeriesSpec:
    plan: TrioSeriesPlan
    max_provider_calls: int = 1080

    @property
    def series_id(self) -> str:
        return self.plan.series_id

    def public_dict(self) -> Mapping[str, object]:
        return {
            "certification": {"eligible": False, "reason": "demo_provider"},
            "entrants": [value.as_dict() for value in TRIO_DEMO_ENTRANTS],
            "max_provider_calls": self.max_provider_calls,
            "plan_sha256": self.plan.plan_sha256,
            "protocol_version": "llm-controller/0.3.0",
            "rotations": 3,
            "seed": self.plan.seed,
            "series_id": self.plan.series_id,
            "task_id": self.plan.task_id,
        }


TrioExecutor = Callable[[TrioSeriesSpec, asyncio.Event], Awaitable[TrioSeriesExecution]]


@dataclass
class _Record:
    spec: TrioSeriesSpec
    state: str = "queued"
    result: TrioSeriesResult | None = None
    evidence: TrioSeriesEvidence | None = None
    evaluation: Mapping[str, object] | None = None
    timeline: Mapping[str, object] | None = None
    failure: str | None = None
    failure_type: str | None = field(default=None, repr=False)
    failure_detail: str | None = field(default=None, repr=False)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    archive: ArchivedTrioSeries | None = None
    archive_state: str = "pending"
    archive_failure: str | None = field(default=None, repr=False)
    frames: TrioParticipantFrameStore = field(default_factory=TrioParticipantFrameStore)
    preview: TrioParticipantPreviewChannel = field(
        default_factory=TrioParticipantPreviewChannel
    )


class TrioSeriesService:
    def __init__(
        self, executor: TrioExecutor, *, archive: TrioSeriesArchive | None = None
    ) -> None:
        if not callable(executor):
            raise TypeError("trio executor must be callable")
        self._executor = executor
        self._archive = archive
        self._records: dict[str, _Record] = {}
        self._lock: asyncio.Lock | None = None

    def _service_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def create(
        self,
        *,
        task_id: str,
        seed: int,
        entrants: tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]],
        max_provider_calls: int = 1080,
    ) -> Mapping[str, object]:
        if task_id not in TRIO_TASK_IDS:
            raise ValueError("unsupported trio task")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("trio seed is invalid")
        if (
            isinstance(max_provider_calls, bool)
            or not isinstance(max_provider_calls, int)
            or not 1 <= max_provider_calls <= 1080
        ):
            raise ValueError("trio provider budget is invalid")
        if not isinstance(entrants, tuple) or len(entrants) != 3:
            raise ValueError("exactly three trio Demo entrants are required")
        expected = tuple(
            {"provider": "demo", "model": value.model} for value in TRIO_DEMO_ENTRANTS
        )
        normalized = tuple(dict(value) for value in entrants)
        if normalized != expected or any(set(value) != {"provider", "model"} for value in entrants):
            raise ValueError("trio entrants must be keyless Sol, Luna, and Terra Demo policies")
        series_id = f"trio_{secrets.token_hex(12)}"
        plan = build_cyclic_trio_plan(
            series_id=series_id,
            task_id=task_id,  # type: ignore[arg-type]
            seed=seed,
            schedule_nonce=secrets.token_hex(16),
        )
        record = _Record(TrioSeriesSpec(plan, max_provider_calls))
        async with self._service_lock():
            self._records[series_id] = record
            record.task = asyncio.create_task(self._run(record), name=f"trio-series-{series_id}")
        return self._status(record)

    async def _run(self, record: _Record) -> None:
        record.state = "running"
        try:
            execution = await self._executor(record.spec, record.cancel_event)
            if not isinstance(execution, TrioSeriesExecution):
                raise TypeError("trio executor returned an invalid execution")
            record.result = execution.result
            record.evidence = execution.evidence
            record.evaluation = dict(execution.evaluation)
            record.timeline = _timeline(execution.evidence.public)
            # The verified cyclic result is complete before native participant movies are
            # rendered. Keep the lifecycle truthful while archival continues under its own state.
            record.state = "completed"
            if self._archive is None:
                record.archive_state = "unavailable"
            else:
                record.archive_state = "saving"
                try:
                    record.archive = await asyncio.to_thread(
                        self._archive.save,
                        execution.evidence.public,
                        evaluation=record.evaluation,
                        timeline=record.timeline,
                        result=execution.result.public_dict(),
                        protected=execution.evidence.protected,
                    )
                    record.archive_state = "ready"
                except asyncio.CancelledError:
                    record.archive_state = "unavailable"
                    record.archive_failure = "trio_archive_save_cancelled"
                    raise
                except Exception:
                    # Archival is downstream of an already verified authority result. Never let
                    # an exporter bug strand the public lifecycle in "saving" or rewrite the
                    # completed game as an execution failure. Exception messages remain private.
                    record.archive_state = "unavailable"
                    record.archive_failure = "trio_archive_save_failed"
        except asyncio.CancelledError:
            record.state = "cancelled"
        except Exception as error:
            record.failure_type = type(error).__name__
            record.failure_detail = str(error)
            record.failure = "trio_series_execution_failed"
            record.state = "failed"

    async def status(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            return self._status(record)
        archived = await self._archived(series_id)
        result = await self._projection(series_id, "result")
        if archived is None or result is None:
            raise TrioSeriesNotFoundError(series_id)
        return {
            "archive": archived.public_dict(),
            "archive_state": "ready",
            "certification": {"eligible": False, "reason": "demo_provider"},
            "config": {
                "entrants": [value.as_dict() for value in TRIO_DEMO_ENTRANTS],
                "protocol_version": "llm-controller/0.3.0",
                "rotations": 3,
                "task_id": result["task_id"],
            },
            "failure": None,
            "series_id": series_id,
            "state": "completed",
            "task_id": result["task_id"],
        }

    async def result(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            if record.result is None:
                raise TrioSeriesNotReadyError("trio_series_result_not_ready")
            return record.result.public_dict()
        value = await self._projection(series_id, "result")
        if value is None:
            raise TrioSeriesNotFoundError(series_id)
        return value

    async def evaluation(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            if record.evaluation is None:
                raise TrioSeriesNotReadyError("trio_series_evaluation_not_ready")
            return record.evaluation
        value = await self._projection(series_id, "evaluation")
        if value is None:
            raise TrioSeriesNotFoundError(series_id)
        return value

    async def timeline(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            if record.timeline is None:
                raise TrioSeriesNotReadyError("trio_series_timeline_not_ready")
            return record.timeline
        value = await self._projection(series_id, "timeline")
        if value is None:
            raise TrioSeriesNotFoundError(series_id)
        return value

    async def replay(self, series_id: str) -> TrioSeriesEvidenceBundle:
        record = await self._optional_record(series_id)
        if record is not None:
            if record.evidence is None:
                raise TrioSeriesNotReadyError("trio_series_replay_not_ready")
            return record.evidence.public
        bundle = await asyncio.to_thread(self._archive.replay, series_id) if self._archive else None
        if bundle is None:
            raise TrioSeriesNotFoundError(series_id)
        return bundle

    async def archive_status(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            return self._archive_projection(record)
        archived = await self._archived(series_id)
        if archived is None:
            raise TrioSeriesNotFoundError(series_id)
        return archived.public_dict()

    async def participant_frame(
        self, series_id: str, participant_id: str
    ) -> tuple[str, TrioParticipantFrameSnapshot | None]:
        record = await self._record(series_id)
        snapshot = record.frames.snapshot(participant_id)
        if snapshot is not None:
            state = "live" if record.state in ("queued", "running") else "finished"
        else:
            state = "loading" if record.state in ("queued", "running") else "unavailable"
        return state, snapshot

    async def publish_frame(
        self,
        series_id: str,
        leg_index: int,
        participant_id: str,
        observation_seq: int,
        png: bytes,
    ) -> None:
        record = await self._record(series_id)
        await asyncio.to_thread(
            record.frames.publish, leg_index, participant_id, observation_seq, png
        )

    async def publish_live_preview(
        self, series_id: str, leg_index: int, participant_id: str, sequence: int, jpeg: bytes
    ) -> bool:
        record = await self._optional_record(series_id)
        return False if record is None else await record.preview.publish(
            leg_index, participant_id, sequence, jpeg
        )

    async def live_preview_subscription(
        self, series_id: str, participant_id: str
    ) -> tuple[
        int,
        asyncio.Queue[TrioParticipantPreviewSnapshot],
        TrioParticipantPreviewSnapshot | None,
    ]:
        record = await self._record(series_id)
        return record.preview.subscribe(participant_id)

    async def unsubscribe_live_preview(
        self, series_id: str, participant_id: str, token: int
    ) -> None:
        record = await self._optional_record(series_id)
        if record is not None:
            record.preview.unsubscribe(participant_id, token)

    async def native_video_path(
        self, series_id: str, leg_index: int, participant_id: str
    ) -> Path | None:
        if self._archive is None:
            return None
        if await self._archived(series_id) is None:
            if await self._optional_record(series_id) is None:
                raise TrioSeriesNotFoundError(series_id)
            return None
        return await asyncio.to_thread(
            self._archive.video_path, series_id, leg_index, participant_id
        )

    async def cancel(self, series_id: str) -> Mapping[str, object]:
        record = await self._record(series_id)
        if record.state in ("queued", "running"):
            record.cancel_event.set()
            if record.task is not None:
                record.task.cancel()
        return self._status(record)

    async def aclose(self) -> None:
        records = tuple(self._records.values())
        for record in records:
            if record.task is not None and not record.task.done():
                record.task.cancel()
        if records:
            await asyncio.gather(
                *(record.task for record in records if record.task is not None),
                return_exceptions=True,
            )
        for record in records:
            record.frames.close()
            record.preview.close()

    def _status(self, record: _Record) -> Mapping[str, object]:
        value = {
            **record.spec.public_dict(),
            "archive": self._archive_projection(record),
            "archive_state": record.archive_state,
            "failure": record.failure,
            "state": record.state,
        }
        value["config"] = record.spec.public_dict()
        return value

    @staticmethod
    def _archive_projection(record: _Record) -> Mapping[str, object]:
        if record.archive is not None:
            return record.archive.public_dict()
        # The authority result is already sealed while archive.save renders nine isolated
        # participant movies.  Expose only lifecycle labels here: no unpublished hashes,
        # pixels, protected evidence, or render internals cross the public boundary.
        native_replay: Mapping[str, object]
        if record.archive_state == "saving":
            native_replay = {"state": "saving"}
        else:
            native_replay = {
                "state": "unavailable",
                "reason": "participant_video_not_recorded",
            }
        return {
            "evidence": {"state": record.archive_state},
            "native_replay": native_replay,
        }

    async def _record(self, series_id: str) -> _Record:
        value = await self._optional_record(series_id)
        if value is None:
            raise TrioSeriesNotFoundError(series_id)
        return value

    async def _optional_record(self, series_id: str) -> _Record | None:
        async with self._service_lock():
            return self._records.get(series_id)

    async def _archived(self, series_id: str) -> ArchivedTrioSeries | None:
        return await asyncio.to_thread(self._archive.get, series_id) if self._archive else None

    async def _projection(
        self, series_id: str, name: str
    ) -> Mapping[str, object] | None:
        return (
            await asyncio.to_thread(self._archive.projection, series_id, name)
            if self._archive
            else None
        )


def _timeline(bundle: TrioSeriesEvidenceBundle) -> Mapping[str, object]:
    from ..protocol import strict_json_loads

    events = []
    legs = []
    for leg_index, leg in enumerate(bundle.legs):
        parsed = strict_json_loads(leg.read("public_events"))
        parsed_receipts = strict_json_loads(leg.read("receipts"))
        if not isinstance(parsed, list):
            raise ValueError("trio public timeline evidence is invalid")
        if not isinstance(parsed_receipts, list):
            raise ValueError("trio public receipt evidence is invalid")
        for event in parsed:
            events.append(
                {
                    "kind": event["kind"],
                    "leg_index": leg_index,
                    "participant_ids": event["participant_ids"],
                    "summary": event["summary"],
                    "tick": event["tick"],
                }
            )
        safe_receipts = []
        for window in parsed_receipts:
            participants = window.get("participants") if isinstance(window, dict) else None
            if not isinstance(participants, dict) or set(participants) != set(
                TRIO_PARTICIPANT_IDS
            ) or any(
                not isinstance(participants[participant_id], dict)
                for participant_id in TRIO_PARTICIPANT_IDS
            ):
                raise ValueError("trio public receipt participants are invalid")
            safe_receipts.append(
                {
                    "observation_seq": window["observation_seq"],
                    "participants": {
                        participant_id: {
                            "action_id": participants[participant_id]["action_id"],
                            "applied_ticks": participants[participant_id]["applied_ticks"],
                            "codes": participants[participant_id]["codes"],
                            "disposition": participants[participant_id]["disposition"],
                        }
                        for participant_id in TRIO_PARTICIPANT_IDS
                    },
                }
            )
        legs.append({"leg_index": leg_index, "receipts": safe_receipts})
    return {"events": events, "legs": legs, "series_id": bundle.series_id}


__all__ = [
    "TrioExecutor",
    "TrioSeriesNotFoundError",
    "TrioSeriesNotReadyError",
    "TrioSeriesService",
    "TrioSeriesSpec",
]
