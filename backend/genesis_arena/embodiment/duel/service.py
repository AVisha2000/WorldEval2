"""Session-only lifecycle service for symmetric paired model duels."""

from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Mapping, Optional, Tuple

from ..baselines import BASELINE_TIERS
from ..credentials import SessionCredential
from ..duo_games.catalog import CENTRAL_RELAY_TASK_ID, duo_game
from ..duo_games.rts_skirmish_v1 import TASK_ID as RTS_SKIRMISH_V1_TASK_ID
from ..evaluation_projection import build_paired_duel_leg_evaluation_projection
from ..managed_session import ManagedSessionError
from ..protocol import canonical_sha256, strict_json_loads
from .archive import ArchivedDuelSeries, DuelSeriesArchive
from .contracts import DuelEntrant, PairedDuelResult
from .evidence import DuelSeriesEvidenceBundle, DuelSeriesExecution
from .participant_frames import (
    DuelBroadcastPreviewChannel,
    DuelBroadcastPreviewSnapshot,
    DuelParticipantFrameSnapshot,
    DuelParticipantPreviewChannel,
    DuelParticipantPreviewSnapshot,
)

_PROVIDERS = frozenset(("openai", "anthropic", "gemini", "scripted", "demo"))
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


class DuelSeriesNotFoundError(KeyError):
    pass


class DuelSeriesEvidenceNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DuelSeriesSpec:
    series_id: str
    entrants: Tuple[DuelEntrant, DuelEntrant]
    seed: int
    schedule_nonce: str
    max_live_provider_calls: int = 2160
    task_id: str = CENTRAL_RELAY_TASK_ID

    def __post_init__(self) -> None:
        duo_game(self.task_id)

    @property
    def mode(self) -> str:
        return (
            "scripted-duel-v0"
            if any(entrant.provider == "scripted" for entrant in self.entrants)
            else "model-duel-v0"
        )

    @property
    def is_demo(self) -> bool:
        return all(entrant.provider == "demo" for entrant in self.entrants)

    @property
    def certification_eligible(self) -> bool:
        return not self.is_demo

    def public_dict(self) -> Mapping[str, object]:
        return {
            "certification": {
                "eligible": self.certification_eligible,
                "reason": None if self.certification_eligible else "demo_provider",
            },
            "entrants": [entrant.as_dict() for entrant in self.entrants],
            "max_live_provider_calls": self.max_live_provider_calls,
            "schedule_nonce": self.schedule_nonce,
            "seed": self.seed,
            "series_id": self.series_id,
            "task_id": self.task_id,
        }


SeriesExecutor = Callable[
    [
        DuelSeriesSpec,
        Mapping[str, SessionCredential],
        asyncio.Event,
    ],
    Awaitable[DuelSeriesExecution],
]
ParticipantFrameReader = Callable[
    [str, str], Optional[DuelParticipantFrameSnapshot]  # noqa: UP045 - Python 3.9 alias RHS
]


@dataclass
class _Record:
    spec: DuelSeriesSpec
    credentials: Mapping[str, SessionCredential] = field(repr=False)
    state: str = "queued"
    result: PairedDuelResult | None = None
    public_evidence: DuelSeriesEvidenceBundle | None = None
    protected_evidence: DuelSeriesEvidenceBundle | None = None
    failure: str | None = None
    failure_type: str | None = field(default=None, repr=False)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    archive: ArchivedDuelSeries | None = None
    archive_state: str = "pending"
    live_preview: DuelParticipantPreviewChannel = field(
        default_factory=DuelParticipantPreviewChannel
    )
    live_broadcast_preview: DuelBroadcastPreviewChannel = field(
        default_factory=DuelBroadcastPreviewChannel
    )


class DuelSeriesService:
    def __init__(
        self,
        executor: SeriesExecutor,
        *,
        archive: DuelSeriesArchive | None = None,
        participant_frame_reader: ParticipantFrameReader | None = None,
    ) -> None:
        self._executor = executor
        self._archive = archive
        self._participant_frame_reader = participant_frame_reader
        self._records: dict[str, _Record] = {}
        self._lock: asyncio.Lock | None = None

    def _service_lock(self) -> asyncio.Lock:
        """Bind the coordination lock only from an active async service call."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def create(
        self,
        *,
        entrants: tuple[Mapping[str, object], Mapping[str, object]],
        seed: int,
        max_live_provider_calls: int = 2160,
        task_id: str = CENTRAL_RELAY_TASK_ID,
    ) -> Mapping[str, object]:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed is invalid")
        if not isinstance(entrants, tuple) or len(entrants) != 2:
            raise ValueError("exactly two entrants are required")
        if (
            isinstance(max_live_provider_calls, bool)
            or not isinstance(max_live_provider_calls, int)
            or not 1 <= max_live_provider_calls <= 2160
        ):
            raise ValueError("max_live_provider_calls is invalid")
        series_id = f"series_{secrets.token_hex(12)}"
        public_entrants = []
        credential_values: list[str | None] = []
        for index, value in enumerate(entrants):
            if not isinstance(value, Mapping):
                raise ValueError("entrant shape is invalid")
            provider = value.get("provider")
            model = value.get("model")
            expected_fields = (
                {"provider", "model"}
                if provider in ("scripted", "demo")
                else {
                    "provider",
                    "model",
                    "api_key",
                }
            )
            if set(value) != expected_fields:
                raise ValueError("entrant shape is invalid")
            key = value.get("api_key")
            if (
                provider not in _PROVIDERS
                or not isinstance(model, str)
                or _MODEL.fullmatch(model) is None
            ):
                raise ValueError("entrant provider or model is invalid")
            if provider == "scripted" and model not in BASELINE_TIERS:
                raise ValueError("scripted baseline tier is invalid")
            if provider == "demo" and model not in duo_game(task_id).models:
                raise ValueError("demo policy is invalid for the selected duo task")
            if provider not in ("scripted", "demo") and (not isinstance(key, str) or not key):
                raise ValueError("entrant credential is invalid")
            public_entrants.append(DuelEntrant(f"entrant_{index}", str(provider), model))
            credential_values.append(key)

        if sum(entrant.provider == "scripted" for entrant in public_entrants) > 1:
            raise ValueError("at most one scripted entrant is allowed")
        demo_count = sum(entrant.provider == "demo" for entrant in public_entrants)
        if demo_count not in (0, 2):
            raise ValueError("demo series require exactly two demo entrants")
        game = duo_game(task_id)
        if task_id not in (CENTRAL_RELAY_TASK_ID, RTS_SKIRMISH_V1_TASK_ID) and demo_count != 2:
            raise ValueError("additive duo games require exactly two Demo entrants")
        if demo_count == 2 and tuple(entrant.model for entrant in public_entrants) != game.models:
            raise ValueError("Demo entrants must match the selected duo game policy pair")

        credentials: dict[str, SessionCredential] = {}
        task_owns_credentials = False
        try:
            for entrant, value in zip(public_entrants, credential_values):
                if value is not None:
                    credentials[entrant.entrant_id] = SessionCredential(value)
            spec = DuelSeriesSpec(
                series_id,
                (public_entrants[0], public_entrants[1]),
                seed,
                secrets.token_hex(16),
                max_live_provider_calls,
                task_id,
            )
            record = _Record(spec, credentials)
            async with self._service_lock():
                self._records[series_id] = record
                record.task = asyncio.create_task(
                    self._run(record), name=f"duel-series-{series_id}"
                )
                task_owns_credentials = True
            return self._status(record)
        finally:
            if not task_owns_credentials:
                for credential in credentials.values():
                    credential.close()

    async def _run(self, record: _Record) -> None:
        record.state = "running"
        try:
            execution = await self._executor(record.spec, record.credentials, record.cancel_event)
            if not isinstance(execution, DuelSeriesExecution):
                raise TypeError("duel series executor returned an invalid execution")
            if (
                execution.evidence is not None
                and execution.evidence.public.series_id != record.spec.series_id
            ):
                raise ValueError("duel series evidence belongs to a different series")
            # The executor owns provider use only through the authority result. Evidence
            # projection and native archival can take minutes and must never extend credential
            # retention beyond gameplay.
            for credential in record.credentials.values():
                credential.close()
            record.result = execution.result
            if execution.evidence is not None:
                record.public_evidence = execution.evidence.public
                record.protected_evidence = execution.evidence.protected
                # Authority has already sealed at this point. Native participant movies can take
                # minutes to render, so expose the finished game immediately and report archival
                # progress independently instead of leaving the browser in a false "running"
                # state after gameplay has ended.
                record.state = "completed"
                if self._archive is not None:
                    record.archive_state = "saving"
                    try:
                        evaluation = self._evaluation_projection(record)
                        timeline = self._timeline_projection(record)
                        record.archive = await asyncio.to_thread(
                            self._archive.save,
                            record.public_evidence,
                            evaluation=evaluation,
                            timeline=timeline,
                            protected_bundle=record.protected_evidence,
                        )
                        record.archive_state = "ready"
                    except Exception:
                        # A durable-export failure does not rewrite an already verified authority
                        # result. Projection and exporter details can contain protected material,
                        # so the public lifecycle reports only the bounded unavailable state.
                        record.archive_state = "unavailable"
                else:
                    record.archive_state = "unavailable"
            else:
                record.state = "completed"
        except asyncio.CancelledError:
            if record.state == "completed":
                # Shutdown may cancel a task that is only persisting non-authoritative media.
                # Never rewrite the already sealed gameplay result.
                if record.archive_state == "saving":
                    record.archive_state = "unavailable"
            else:
                record.state = "cancelled"
        except Exception as error:
            # Keep only the exception class for local diagnostics. Messages can contain provider
            # or transport material and must never enter status, logs, evidence, or archives.
            if isinstance(error, ManagedSessionError):
                cause = error.__cause__
                cause_code = getattr(cause, "code", None)
                record.failure_type = (
                    f"{error.code}:{cause_code}"
                    if isinstance(cause_code, str)
                    else f"{error.code}:{type(cause).__name__}"
                    if cause is not None
                    else error.code
                )
            else:
                record.failure_type = type(error).__name__
            record.failure = "duel_series_execution_failed"
            record.state = "failed"
        finally:
            for credential in record.credentials.values():
                credential.close()

    async def status(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            return self._status(record)
        bundle = await asyncio.to_thread(self._archive.replay, series_id) if self._archive else None
        archived = await self._archived(series_id)
        if archived is None or bundle is None:
            raise DuelSeriesNotFoundError(series_id)
        return _archived_status(bundle, archived)

    async def result(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is None:
            bundle = (
                await asyncio.to_thread(self._archive.replay, series_id) if self._archive else None
            )
            archived = await self._archived(series_id)
            if archived is None or bundle is None:
                raise DuelSeriesNotFoundError(series_id)
            return _archived_result(bundle, archived)
        if record.state not in ("completed", "failed", "cancelled"):
            raise RuntimeError("duel_series_result_not_ready")
        value = dict(self._status(record))
        value["result"] = None if record.result is None else asdict(record.result)
        return value

    async def replay(self, series_id: str) -> DuelSeriesEvidenceBundle:
        record = await self._optional_record(series_id)
        if record is not None:
            if record.public_evidence is None:
                raise DuelSeriesEvidenceNotReadyError("duel_series_evidence_not_ready")
            return record.public_evidence
        archived = await self._archived(series_id)
        bundle = await asyncio.to_thread(self._archive.replay, series_id) if self._archive else None
        if archived is None or bundle is None:
            raise DuelSeriesNotFoundError(series_id)
        return bundle

    async def evaluation(self, series_id: str) -> Mapping[str, object]:
        """Return strict browser-safe, authority-derived projections for both sealed legs."""

        record = await self._optional_record(series_id)
        if record is None:
            archived = await self._archived(series_id)
            value = (
                await asyncio.to_thread(self._archive.evaluation, series_id)
                if self._archive
                else None
            )
            if archived is None or value is None:
                raise DuelSeriesNotFoundError(series_id)
            return value
        if record.public_evidence is None:
            raise DuelSeriesEvidenceNotReadyError("duel_series_evaluation_not_ready")
        return self._evaluation_projection(record)

    def _evaluation_projection(self, record: _Record) -> Mapping[str, object]:
        legs = tuple(
            (
                _leg_evaluation_projection(record, leg, index)
                if record.spec.task_id == "central-relay-v0"
                else _duo_game_leg_evaluation_projection(record, leg, index)
            )
            for index, leg in enumerate(record.public_evidence.legs)
        )
        return {
            "certification": {
                "eligible": record.spec.certification_eligible,
                "reason": None if record.spec.certification_eligible else "demo_provider",
            },
            "legs": list(legs),
            "series_id": record.spec.series_id,
        }

    async def timeline(self, series_id: str) -> Mapping[str, object]:
        """Return receipt and event labels only; observations and pixels never cross this route."""

        record = await self._optional_record(series_id)
        if record is None:
            archived = await self._archived(series_id)
            value = (
                await asyncio.to_thread(self._archive.timeline, series_id)
                if self._archive
                else None
            )
            if archived is None or value is None:
                raise DuelSeriesNotFoundError(series_id)
            return value
        if record.public_evidence is None:
            raise DuelSeriesEvidenceNotReadyError("duel_series_timeline_not_ready")
        return self._timeline_projection(record)

    def _timeline_projection(self, record: _Record) -> Mapping[str, object]:
        legs = []
        for index, leg in enumerate(record.public_evidence.legs):
            summary = _object(leg.read("replay_summary"), "replay summary")
            receipts = _list(leg.read("receipts"), "receipts")
            events = _list(leg.read("public_events"), "public events")
            legs.append(
                {
                    "episode_id": summary["episode_id"],
                    "events": [_safe_event(value) for value in events],
                    "leg_index": index,
                    "receipts": [_safe_receipt_window(value) for value in receipts],
                }
            )
        return {"legs": legs, "series_id": record.spec.series_id}

    async def archive_status(self, series_id: str) -> Mapping[str, object]:
        record = await self._optional_record(series_id)
        if record is not None:
            return self._archive_projection(record)
        archived = await self._archived(series_id)
        if archived is None:
            raise DuelSeriesNotFoundError(series_id)
        return archived.public_dict()

    async def native_video_path(self, series_id: str, leg_index: int, participant_id: str):
        if self._archive is None:
            return None
        if (
            await self._optional_record(series_id) is None
            and await self._archived(series_id) is None
        ):
            raise DuelSeriesNotFoundError(series_id)
        return await asyncio.to_thread(
            self._archive.video_path, series_id, leg_index, participant_id
        )

    async def participant_frame(
        self, series_id: str, participant_id: str
    ) -> tuple[str, DuelParticipantFrameSnapshot | None]:
        if participant_id not in ("participant_0", "participant_1"):
            raise ValueError("duel frame participant is invalid")
        record = await self._optional_record(series_id)
        if record is None:
            if await self._archived(series_id) is not None:
                return "unavailable", None
            raise DuelSeriesNotFoundError(series_id)
        if self._participant_frame_reader is None:
            state = (
                "finished" if record.state in ("completed", "failed", "cancelled") else "loading"
            )
            return state, None
        snapshot = self._participant_frame_reader(series_id, participant_id)
        state = "finished" if record.state in ("completed", "failed", "cancelled") else "live"
        return state if snapshot is not None else "loading", snapshot

    async def publish_live_preview(
        self,
        series_id: str,
        leg_index: int,
        participant_id: str,
        sequence: int,
        jpeg: bytes,
    ) -> bool:
        record = await self._record(series_id)
        if record.state not in ("queued", "running"):
            return False
        return await record.live_preview.publish(leg_index, participant_id, sequence, jpeg)

    async def live_preview_subscription(
        self, series_id: str, participant_id: str
    ) -> tuple[
        int,
        asyncio.Queue[DuelParticipantPreviewSnapshot],
        DuelParticipantPreviewSnapshot | None,
    ]:
        record = await self._record(series_id)
        return record.live_preview.subscribe(participant_id)

    async def unsubscribe_live_preview(
        self, series_id: str, participant_id: str, token: int
    ) -> None:
        record = await self._record(series_id)
        record.live_preview.unsubscribe(participant_id, token)

    async def publish_live_broadcast_preview(
        self, series_id: str, leg_index: int, sequence: int, jpeg: bytes
    ) -> bool:
        """Publish the approved RTS broadcast camera pixels, never a player projection."""

        record = await self._record(series_id)
        if record.spec.task_id not in {
            "rts-skirmish-v0",
            RTS_SKIRMISH_V1_TASK_ID,
        } or record.state not in (
            "queued",
            "running",
        ):
            return False
        return await record.live_broadcast_preview.publish(leg_index, sequence, jpeg)

    async def live_broadcast_preview_subscription(
        self, series_id: str
    ) -> tuple[
        int,
        asyncio.Queue[DuelBroadcastPreviewSnapshot],
        DuelBroadcastPreviewSnapshot | None,
    ]:
        record = await self._record(series_id)
        if record.spec.task_id not in {"rts-skirmish-v0", RTS_SKIRMISH_V1_TASK_ID}:
            raise ValueError("broadcast preview is unavailable for this task")
        return record.live_broadcast_preview.subscribe()

    async def unsubscribe_live_broadcast_preview(self, series_id: str, token: int) -> None:
        record = await self._record(series_id)
        record.live_broadcast_preview.unsubscribe(token)

    async def protected_bundle(self, series_id: str) -> DuelSeriesEvidenceBundle:
        """Return protected pair evidence only to trusted local certification code."""

        record = await self._record(series_id)
        if record.protected_evidence is None:
            raise DuelSeriesEvidenceNotReadyError("duel_series_evidence_not_ready")
        return record.protected_evidence

    async def cancel(self, series_id: str) -> Mapping[str, object]:
        record = await self._record(series_id)
        if (
            record.state in {"queued", "running"}
            and record.task is not None
            and not record.task.done()
        ):
            record.cancel_event.set()
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
        return self._status(record)

    async def aclose(self) -> None:
        async with self._service_lock():
            records = tuple(self._records.values())
        for record in records:
            if (
                record.state in {"queued", "running"}
                and record.task is not None
                and not record.task.done()
            ):
                record.cancel_event.set()
        # Executors are required to honor the cancellation event at their bounded decision
        # boundary. Awaiting instead of cancelling prevents asyncio.to_thread archive work from
        # continuing after the live service has reported it unavailable.
        await asyncio.gather(
            *(record.task for record in records if record.task is not None),
            return_exceptions=True,
        )
        for record in records:
            for credential in record.credentials.values():
                credential.close()
            record.live_preview.close()
            record.live_broadcast_preview.close()

    async def _record(self, series_id: str) -> _Record:
        async with self._service_lock():
            record = self._records.get(series_id)
        if record is None:
            raise DuelSeriesNotFoundError(series_id)
        return record

    async def _optional_record(self, series_id: str) -> _Record | None:
        async with self._service_lock():
            return self._records.get(series_id)

    async def _archived(self, series_id: str) -> ArchivedDuelSeries | None:
        if self._archive is None:
            return None
        return await asyncio.to_thread(self._archive.get, series_id)

    @staticmethod
    def _status(record: _Record) -> Mapping[str, object]:
        return {
            "archive": DuelSeriesService._archive_projection(record),
            "config": record.spec.public_dict(),
            "failure": record.failure,
            "participant_streams": {
                "state": "unavailable",
                "reason": "paired_preview_not_connected",
                "participant_ids": ["participant_0", "participant_1"],
            },
            "series_id": record.spec.series_id,
            "state": record.state,
            "task_id": record.spec.task_id,
        }

    @staticmethod
    def _archive_projection(record: _Record) -> Mapping[str, object]:
        if record.archive is not None:
            return record.archive.public_dict()
        evidence_state = record.archive_state
        # Saving is an honest, public lifecycle state: verified authority evidence has sealed,
        # but the atomic public archive (including participant-only movies) is still being
        # assembled.  Do not call the movie unavailable until the archive reaches a terminal
        # state; doing so makes a finished match look like it lost its replay.
        native_replay: Mapping[str, object]
        if evidence_state == "saving":
            native_replay = {"state": "saving"}
        else:
            native_replay = {
                "state": "unavailable",
                "reason": "participant_video_not_recorded",
            }
        return {
            "evidence": {"state": evidence_state},
            "native_replay": native_replay,
        }


def _leg_evaluation_projection(record: _Record, leg, leg_index: int) -> Mapping[str, object]:
    summary = _object(leg.read("replay_summary"), "replay summary")
    evaluation = _object(leg.read("evaluation"), "evaluation")
    receipts = _list(leg.read("receipts"), "receipts")
    events = _list(leg.read("public_events"), "public events")
    lock = summary.get("fairness_lock")
    settings = summary.get("call_settings")
    leg_plan = summary.get("leg_plan")
    if (
        not isinstance(lock, Mapping)
        or not isinstance(settings, Mapping)
        or not isinstance(leg_plan, Mapping)
    ):
        raise ValueError("duel evaluation frozen material is invalid")
    entrants = lock.get("entrants")
    if not isinstance(entrants, list):
        raise ValueError("duel evaluation entrants are invalid")
    provider_failures = 0
    for window in receipts:
        if not isinstance(window, Mapping) or not isinstance(window.get("participants"), Mapping):
            raise ValueError("duel evaluation receipts are invalid")
        provider_failures += sum(
            isinstance(value, Mapping) and value.get("accepted") is False
            for value in window["participants"].values()
        )
    certification = summary.get("certification")
    if not isinstance(certification, Mapping) or not isinstance(
        certification.get("eligible"), bool
    ):
        raise ValueError("duel certification status is invalid")
    expected_reason = None if record.spec.certification_eligible else "demo_provider"
    if (
        certification["eligible"] != record.spec.certification_eligible
        or certification.get("reason") != expected_reason
    ):
        raise ValueError("duel certification status differs from the series lock")
    projection = build_paired_duel_leg_evaluation_projection(
        evaluation=evaluation,
        replay_summary={
            "episode_id": summary["episode_id"],
            "final_state_hash": summary["final_state_hash"],
            "frozen_configuration": {
                "config_sha256": canonical_sha256(leg_plan),
                "model_sha256": canonical_sha256(entrants),
                "protocol_package_sha256": lock["protocol_sha256"],
                "provider_sha256": canonical_sha256(
                    [value["adapter_sha256"] for value in entrants]
                ),
                "settings_sha256": canonical_sha256(settings),
            },
            "terminal": summary["terminal"],
        },
        run_spec={
            "certification_eligible": certification["eligible"],
            "episode_id": summary["episode_id"],
            "run_class": "demo_paired_duel" if record.spec.is_demo else "paired_duel",
            "task_id": record.spec.task_id,
        },
        result={
            "episode_id": summary["episode_id"],
            "final_state_hash": summary["final_state_hash"],
            "provider_failures": provider_failures,
            "terminal": summary["terminal"],
            "windows": len(receipts),
        },
        receipts=receipts,
        public_events=events,
    )
    value = projection.as_dict()
    if value["evaluation"]["leg_index"] != leg_index:
        raise ValueError("duel evaluation leg index differs")
    return value


def _duo_game_leg_evaluation_projection(
    record: _Record, leg, leg_index: int
) -> Mapping[str, object]:
    """Wrap the strict game evaluator in the established browser projection envelope."""

    summary = _object(leg.read("replay_summary"), "replay summary")
    evaluation = _object(leg.read("evaluation"), "evaluation")
    receipts = _list(leg.read("receipts"), "receipts")
    events = _list(leg.read("public_events"), "public events")
    if evaluation.get("task_id") != record.spec.task_id:
        raise ValueError("duo game evaluation task differs")
    provider_failures = 0
    for window in receipts:
        if not isinstance(window, Mapping) or not isinstance(window.get("participants"), Mapping):
            raise ValueError("duo game evaluation receipts are invalid")
        provider_failures += sum(
            isinstance(value, Mapping) and value.get("accepted") is False
            for value in window["participants"].values()
        )
    completion = evaluation.get("completion")
    participants = evaluation.get("participants")
    symmetry = evaluation.get("symmetry")
    if not all(isinstance(value, Mapping) for value in (completion, participants, symmetry)):
        raise ValueError("duo game evaluation aggregates are invalid")
    terminal = summary.get("terminal")
    if not isinstance(terminal, Mapping):
        raise ValueError("duo game terminal is invalid")
    base = {
        "evaluation": {
            "metrics": {
                "completion_tick": {"state": "supported", "value": completion.get("tick")},
                "participant_aggregates": {"state": "supported", "value": participants},
                "seat_symmetry": {"state": "supported", "value": symmetry},
                "task_success": {
                    "state": "supported",
                    "value": completion.get("outcome") == "win",
                },
            }
        },
        "references": {
            "public_event_count": len(events),
            "receipt_window_count": len(receipts),
        },
        "result": {
            "final_state_hash": summary["final_state_hash"],
            "provider_failures": provider_failures,
            "terminal": dict(terminal),
            "windows": len(receipts),
        },
        "run": {
            "certification_eligible": False,
            "episode_id": summary["episode_id"],
            "run_class": "demo_paired_duo_game",
            "task_id": record.spec.task_id,
        },
        "schema_version": "llm-controller/evaluation-projection/1.0.0",
        "scope": "paired_duel_leg",
        "state": "supported",
    }
    return {**base, "projection_sha256": canonical_sha256(base)}


def _object(payload: bytes, name: str) -> Mapping[str, object]:
    value = strict_json_loads(payload)
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _list(payload: bytes, name: str) -> list[object]:
    value = strict_json_loads(payload)
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _safe_event(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("public event is invalid")
    fields = ("event_id", "kind", "participant_ids", "summary", "tick")
    if any(name not in value for name in fields):
        raise ValueError("public event fields are invalid")
    return {name: value[name] for name in fields}


def _safe_receipt_window(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"observation_seq", "participants"}:
        raise ValueError("receipt window is invalid")
    participants = value["participants"]
    if not isinstance(participants, Mapping) or set(participants) != {
        "participant_0",
        "participant_1",
    }:
        raise ValueError("receipt participants are invalid")
    allowed = (
        "accepted",
        "action_id",
        "applied_ticks",
        "disposition",
        "fallback",
        "no_input_reason",
    )
    safe = {}
    for participant_id, receipt in participants.items():
        if not isinstance(receipt, Mapping) or any(name not in receipt for name in allowed):
            raise ValueError("participant receipt is invalid")
        safe[participant_id] = {name: receipt[name] for name in allowed}
    return {"observation_seq": value["observation_seq"], "participants": safe}


def _archived_status(
    bundle: DuelSeriesEvidenceBundle, archived: ArchivedDuelSeries
) -> Mapping[str, object]:
    summary = _object(bundle.legs[0].read("replay_summary"), "replay summary")
    lock = _object_from_value(summary.get("fairness_lock"), "fairness lock")
    entrants = lock.get("entrants")
    if not isinstance(entrants, list):
        raise ValueError("archived entrants are invalid")
    task_id = _archived_task_id(bundle)
    certification = summary.get("certification")
    if not isinstance(certification, Mapping):
        raise ValueError("archived certification is invalid")
    return {
        "archive": archived.public_dict(),
        "config": {
            "certification": dict(certification),
            "entrants": [
                {
                    "entrant_id": value["entrant_id"],
                    "provider": value["provider"],
                    "model": value["model"],
                }
                for value in entrants
                if isinstance(value, Mapping)
            ],
            "seed": lock.get("seed"),
            "series_id": bundle.series_id,
            "task_id": task_id,
        },
        "failure": None,
        "participant_streams": {
            "state": "unavailable",
            "reason": "archived_series",
            "participant_ids": ["participant_0", "participant_1"],
        },
        "series_id": bundle.series_id,
        "state": "completed",
        "task_id": task_id,
    }


def _archived_result(
    bundle: DuelSeriesEvidenceBundle, archived: ArchivedDuelSeries
) -> Mapping[str, object]:
    status = dict(_archived_status(bundle, archived))
    summary = _object(bundle.legs[0].read("replay_summary"), "replay summary")
    pair = _object_from_value(summary.get("pair_result"), "pair result")
    status["result"] = {
        "draws": pair.get("draws"),
        "entrant_wins": pair.get("entrant_wins"),
        "plan_sha256": bundle.plan_sha256,
        "status": pair.get("status"),
        "winner_entrant_id": pair.get("winner_entrant_id"),
    }
    return status


def _archived_task_id(bundle: DuelSeriesEvidenceBundle) -> str:
    task_ids = []
    for leg in bundle.legs:
        summary = _object(leg.read("replay_summary"), "replay summary")
        evaluation = _object(leg.read("evaluation"), "evaluation")
        task_id = evaluation.get("task_id")
        if not isinstance(task_id, str):
            # Frozen Central Relay evaluations predate a task_id field.
            task_id = CENTRAL_RELAY_TASK_ID
        task_ids.append(task_id)
        if summary.get("episode_id") is None:
            raise ValueError("archived replay summary is invalid")
    if task_ids[0] != task_ids[1]:
        raise ValueError("archived series task identities differ")
    return task_ids[0]


def _object_from_value(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


__all__ = [
    "DuelSeriesEvidenceNotReadyError",
    "DuelSeriesNotFoundError",
    "DuelSeriesService",
    "DuelSeriesSpec",
    "SeriesExecutor",
    "ParticipantFrameReader",
]
