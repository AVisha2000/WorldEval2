"""Process-local lifecycle owner for live solo embodiment episodes."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from .artifacts import EpisodeArtifactBundle, EpisodeBundles
from .control_games.movement_maze_demo import MOVEMENT_MAZE_SCENARIO_ID
from .control_games.operator_action_course_demo import OPERATOR_ACTION_COURSE_SCENARIO_ID
from .credentials import InMemoryCredentialStore, SessionCredential
from .demo_provider import DemoPolicyLock
from .demo_scenarios import demo_scenario, demo_scenario_fixture_bytes
from .evaluation_projection import EvaluationProjection, build_solo_evaluation_projection
from .live_solo import LiveSoloError, LiveSoloOutcome
from .presentation import (
    ParticipantFrameSnapshot,
    ParticipantFrameStore,
    ParticipantLivePreviewHub,
    ParticipantLivePreviewSnapshot,
    ParticipantLivePreviewStore,
    ParticipantPreviewHub,
    sanitize_participant_jpeg,
    sanitize_participant_png,
)
from .protocol import strict_json_loads
from .replay_archive import SavedReplay, SavedReplayArchive
from .scripted_construction_demo import (
    SCRIPTED_CONSTRUCTION_PROVIDER,
)
from .scripted_solo_demo import is_scripted_solo_demo

DEMO_PROVIDER = "demo"
_PROVIDERS = frozenset(
    ("openai", "anthropic", "gemini", SCRIPTED_CONSTRUCTION_PROVIDER, DEMO_PROVIDER)
)
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_SAFE_TASK = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROTOCOL_V2_CONTROL_TASKS = frozenset(
    (MOVEMENT_MAZE_SCENARIO_ID, OPERATOR_ACTION_COURSE_SCENARIO_ID)
)


class EpisodeServiceError(RuntimeError):
    code = "embodiment_episode_service_error"

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or self.code)
        self.code = code or self.code


class EpisodeNotFoundError(EpisodeServiceError):
    code = "embodiment_episode_not_found"


class EpisodeResultNotReadyError(EpisodeServiceError):
    code = "embodiment_episode_result_not_ready"


class EpisodeReplayNotReadyError(EpisodeServiceError):
    code = "embodiment_episode_replay_not_ready"


class EpisodeEvaluationNotReadyError(EpisodeServiceError):
    code = "embodiment_evaluation_not_ready"


@dataclass(frozen=True)
class EpisodeRunSpec:
    episode_id: str
    provider: str
    model: str
    task_id: str
    seed: int
    maximum_episode_ticks: int = 1800
    observation_profile: str = "hybrid-visible-v1"
    demo_policy_lock: DemoPolicyLock | None = None
    scenario_id: str | None = None

    def __post_init__(self) -> None:
        if not self.episode_id.startswith("ep_"):
            raise ValueError("episode_id is invalid")
        if self.provider not in _PROVIDERS:
            raise ValueError("provider is unsupported")
        if self.provider == SCRIPTED_CONSTRUCTION_PROVIDER and not is_scripted_solo_demo(
            provider=self.provider, model=self.model, task_id=self.task_id
        ):
            raise ValueError("scripted provider is reserved for the solo curriculum demos")
        if self.provider == DEMO_PROVIDER:
            if self.scenario_id is None:
                object.__setattr__(self, "scenario_id", self.task_id)
            scenario = demo_scenario(self.scenario_id)
            if (
                scenario.authority_task_id != self.task_id
                or scenario.provider_model != self.model
                or scenario.episode_tick_budget != self.maximum_episode_ticks
            ):
                raise ValueError("demo provider model/task combination is unsupported")
            if (
                not isinstance(self.demo_policy_lock, DemoPolicyLock)
                or self.demo_policy_lock.scenario_id != self.scenario_id
                or self.demo_policy_lock.policy_id != scenario.policy_id
                or self.demo_policy_lock.seed != self.seed
                or self.demo_policy_lock.participant_id != "participant_0"
                or self.demo_policy_lock.model != self.model
                or self.demo_policy_lock.total_decision_budget
                != scenario.total_decision_budget
            ):
                raise ValueError("demo policy lock does not match the episode")
        elif self.demo_policy_lock is not None or self.scenario_id is not None:
            raise ValueError("demo identity is reserved for demo episodes")
        if self.observation_profile != "hybrid-visible-v1":
            raise ValueError("only hybrid-visible-v1 is selectable for live episodes")
        if _SAFE_MODEL.fullmatch(self.model) is None or _SAFE_TASK.fullmatch(self.task_id) is None:
            raise ValueError("model or task is invalid")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed is invalid")
        if (
            isinstance(self.maximum_episode_ticks, bool)
            or not isinstance(self.maximum_episode_ticks, int)
            or not 1 <= self.maximum_episode_ticks <= 18_000
        ):
            raise ValueError("maximum_episode_ticks is invalid")

    def public_dict(self) -> Mapping[str, Any]:
        value: dict[str, Any] = {
            "certification_eligible": False,
            "episode_id": self.episode_id,
            "maximum_episode_ticks": self.maximum_episode_ticks,
            "model": self.model,
            "observation_profile": self.observation_profile,
            "provider": self.provider,
            "protocol_version": self.protocol_version,
            "run_class": self.run_class,
            "seed": self.seed,
            "task_id": self.task_id,
        }
        if self.demo_policy_lock is not None:
            value["scenario_id"] = self.scenario_id
            value["evaluation_profile_id"] = demo_scenario(
                self.scenario_id or ""
            ).evaluation_profile_id
            value["demo_policy_lock"] = self.demo_policy_lock.as_dict()
            value["demo_policy_lock_sha256"] = self.demo_policy_lock.sha256
        return value

    @property
    def run_class(self) -> str:
        if self.provider == DEMO_PROVIDER:
            return "demo"
        if self.provider == SCRIPTED_CONSTRUCTION_PROVIDER:
            return "scripted"
        return "live"

    @property
    def protocol_version(self) -> str:
        if self.provider == DEMO_PROVIDER and self.scenario_id is not None:
            return demo_scenario(self.scenario_id).protocol_version
        if self.task_id in _PROTOCOL_V2_CONTROL_TASKS:
            return "llm-controller/0.2.0"
        return "llm-controller/0.1.0"


EpisodeExecutor = Callable[
    [
        EpisodeRunSpec,
        Optional[SessionCredential],
        asyncio.Event,
        Callable[[str, int, bytes], Awaitable[None]],
        Callable[[int, int], Awaitable[None]],
    ],
    Awaitable[LiveSoloOutcome],
]


@dataclass(frozen=True)
class EpisodeFrameView:
    state: str
    snapshot: ParticipantFrameSnapshot | None


@dataclass
class _EpisodeRecord:
    spec: EpisodeRunSpec
    state: str = "queued"
    failure: str | None = None
    outcome: LiveSoloOutcome | None = None
    public_bundle: EpisodeArtifactBundle | None = None
    protected_bundle: EpisodeArtifactBundle | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    timeline: list[Mapping[str, Any]] = field(default_factory=list)
    frames: ParticipantFrameStore = field(default_factory=ParticipantFrameStore)
    preview: ParticipantPreviewHub = field(default_factory=ParticipantPreviewHub)
    # Direct Godot ingress is intentionally isolated from the canonical snapshot/replay preview
    # path above.  It carries only best-effort participant pixels for the live dashboard.
    live_preview_frames: ParticipantLivePreviewStore = field(
        default_factory=ParticipantLivePreviewStore
    )
    live_preview: ParticipantLivePreviewHub = field(default_factory=ParticipantLivePreviewHub)
    live_preview_pump: _ParticipantLivePreviewPump | None = None
    progress_observation_seq: int | None = None
    progress_tick: int | None = None
    replay_state: str | None = None
    saved_replay: SavedReplay | None = None
    replay_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class _QueuedParticipantFrame:
    participant_id: str
    observation_seq: int
    png: bytes


class _ParticipantFramePump:
    """Move expensive browser-only PNG sanitation off the authority runner.

    A single newest-only slot is intentional: presentation frames cannot influence authority,
    provider inputs, scores, or replay verification.  Under load it is always better to discard a
    stale preview than to delay the next deterministic authority tick.
    """

    def __init__(
        self,
        record: _EpisodeRecord,
        *,
        frames: ParticipantFrameStore,
        preview: ParticipantPreviewHub,
        channel: str,
    ) -> None:
        self._record = record
        self._frames = frames
        self._preview = preview
        self._queue: asyncio.Queue[_QueuedParticipantFrame | None] = asyncio.Queue(maxsize=1)
        self._closed = False
        self._worker = asyncio.create_task(
            self._run(), name=f"{channel}-{record.spec.episode_id}"
        )

    async def publish(self, participant_id: str, observation_seq: int, png: bytes) -> bool:
        if self._closed:
            return False
        item = _QueuedParticipantFrame(participant_id, observation_seq, png)
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(item)
        return True

    async def finish(self) -> None:
        if self._closed:
            await self._worker
            return
        self._closed = True
        await self._queue.join()
        await self._queue.put(None)
        await self._worker

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                sanitized = await asyncio.to_thread(sanitize_participant_png, item.png)
                self._frames.publish_sanitized(item.participant_id, item.observation_seq, sanitized)
                snapshot = self._frames.snapshot()
                if snapshot is not None:
                    self._preview.publish(snapshot)
            except Exception:
                # Presentation is strictly an unscored, local projection.  A malformed frame is
                # never forwarded, but its failure must not alter authority/replay outcomes.
                pass
            finally:
                self._queue.task_done()


@dataclass(frozen=True)
class _QueuedLivePreview:
    participant_id: str
    sequence: int
    jpeg: bytes


class _ParticipantLivePreviewPump:
    """Sanitize newest-only JPEG presentation frames outside deterministic authority."""

    def __init__(self, record: _EpisodeRecord) -> None:
        self._record = record
        self._queue: asyncio.Queue[_QueuedLivePreview | None] = asyncio.Queue(maxsize=1)
        self._closed = False
        self._worker = asyncio.create_task(
            self._run(), name=f"live-participant-preview-{record.spec.episode_id}"
        )

    async def publish(self, participant_id: str, sequence: int, jpeg: bytes) -> bool:
        if self._closed:
            return False
        item = _QueuedLivePreview(participant_id, sequence, jpeg)
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(item)
        return True

    async def finish(self) -> None:
        if self._closed:
            await self._worker
            return
        self._closed = True
        await self._queue.join()
        await self._queue.put(None)
        await self._worker

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                sanitized = await asyncio.to_thread(sanitize_participant_jpeg, item.jpeg)
                self._record.live_preview_frames.publish_sanitized(
                    item.participant_id, item.sequence, sanitized
                )
                snapshot = self._record.live_preview_frames.snapshot()
                if snapshot is not None:
                    self._record.live_preview.publish(snapshot)
            except Exception:
                # Preview failures are unscored drops and cannot alter episode state.
                pass
            finally:
                self._queue.task_done()


class EpisodeService:
    """Run injected episode executors and expose only sanitized public projections."""

    def __init__(
        self,
        executor: EpisodeExecutor,
        *,
        credentials: InMemoryCredentialStore | None = None,
        replay_archive: SavedReplayArchive | None = None,
    ) -> None:
        self._executor = executor
        self._credentials = credentials or InMemoryCredentialStore()
        self._replay_archive = replay_archive
        self._records: Dict[str, _EpisodeRecord] = {}
        # Construction is synchronous (FastAPI setup and test factories both create services
        # before an event loop is running).  Python 3.9 eagerly asks for a current loop when an
        # asyncio.Lock is constructed, so bind the lock lazily on the first async operation.
        self._lock: asyncio.Lock | None = None

    def _service_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def create(
        self,
        *,
        provider: str,
        model: str,
        task_id: str,
        seed: int,
        api_key: str | None = None,
        maximum_episode_ticks: int = 1800,
        observation_profile: str = "hybrid-visible-v1",
        scenario_id: str | None = None,
    ) -> Mapping[str, Any]:
        episode_id = f"ep_live_{secrets.token_hex(12)}"
        if provider == DEMO_PROVIDER:
            scenario_id = task_id if scenario_id is None else scenario_id
            scenario = demo_scenario(scenario_id)
            if scenario.authority_task_id != task_id or scenario.provider_model != model:
                raise ValueError("demo scenario does not match task/model")
            maximum_episode_ticks = scenario.episode_tick_budget
        elif scenario_id is not None:
            raise ValueError("scenario_id is reserved for demo episodes")
        demo_policy_lock = (
            _demo_policy_lock(
                model=model,
                task_id=task_id,
                scenario_id=scenario_id or task_id,
                seed=seed,
            )
            if provider == DEMO_PROVIDER
            else None
        )
        spec = EpisodeRunSpec(
            episode_id=episode_id,
            provider=provider,
            model=model,
            task_id=task_id,
            seed=seed,
            maximum_episode_ticks=maximum_episode_ticks,
            observation_profile=observation_profile,
            demo_policy_lock=demo_policy_lock,
            scenario_id=scenario_id,
        )
        credential: SessionCredential | None = None
        if provider in (SCRIPTED_CONSTRUCTION_PROVIDER, DEMO_PROVIDER):
            if api_key is not None:
                raise ValueError("credential-free provider does not accept an API key")
        else:
            if not isinstance(api_key, str) or not api_key:
                raise ValueError("provider API key is required")
            ref = self._credentials.put(episode_id, provider, api_key)
            credential = self._credentials.get(ref)
        record = _EpisodeRecord(spec=spec)
        record.timeline.append({"kind": "episode_queued", "sequence": 0})
        async with self._service_lock():
            self._records[episode_id] = record
            record.task = asyncio.create_task(
                self._execute(record, credential),
                name=f"embodiment-episode-{episode_id}",
            )
        return self._status(record)

    async def _execute(
        self, record: _EpisodeRecord, credential: SessionCredential | None
    ) -> None:
        record.state = "running"
        record.timeline.append({"kind": "episode_started", "sequence": 1})
        frame_pump = _ParticipantFramePump(
            record,
            frames=record.frames,
            preview=record.preview,
            channel="participant-frame",
        )
        live_preview_pump = _ParticipantLivePreviewPump(record)
        record.live_preview_pump = live_preview_pump

        async def publish_frame(participant_id: str, observation_seq: int, png: bytes) -> None:
            await frame_pump.publish(participant_id, observation_seq, png)

        async def publish_progress(observation_seq: int, tick: int) -> None:
            # This is an allow-listed lifecycle projection only.  It is not evidence, a replay
            # input, a participant observation, or a source of authority.  Ignore malformed or
            # non-monotonic reports so presentation code cannot make dashboard time go backwards.
            if (
                isinstance(observation_seq, bool)
                or isinstance(tick, bool)
                or not isinstance(observation_seq, int)
                or not isinstance(tick, int)
                or observation_seq < 0
                or tick < 0
            ):
                return
            if (
                record.progress_observation_seq is not None
                and observation_seq < record.progress_observation_seq
            ):
                return
            if record.progress_tick is not None and tick < record.progress_tick:
                return
            record.progress_observation_seq = observation_seq
            record.progress_tick = tick

        try:
            outcome = await self._executor(
                record.spec, credential, record.cancel_event, publish_frame, publish_progress
            )
            if not isinstance(outcome, LiveSoloOutcome):
                raise TypeError("episode executor returned an invalid outcome")
            if outcome.bundles is None:
                raise RuntimeError("episode evidence was not sealed")
            await frame_pump.finish()
            record.outcome = outcome
            record.public_bundle = outcome.bundles.public
            record.protected_bundle = outcome.bundles.protected
            self._append_public_evidence(record, outcome.bundles.public)
            record.state = "completed"
            record.timeline.append(
                {
                    "kind": "episode_completed",
                    "outcome": outcome.terminal["outcome"],
                    "sequence": len(record.timeline),
                }
            )
            self._start_replay_archive(record, outcome.bundles)
        except asyncio.CancelledError:
            record.state = "cancelled"
            record.timeline.append({"kind": "episode_cancelled", "sequence": len(record.timeline)})
        except LiveSoloError as error:
            record.state = "failed"
            record.failure = error.code
            record.timeline.append(
                {
                    "code": record.failure,
                    "kind": "episode_failed",
                    "sequence": len(record.timeline),
                }
            )
        except Exception as error:
            record.state = "failed"
            candidate = getattr(error, "code", None)
            record.failure = (
                candidate
                if isinstance(candidate, str)
                and re.fullmatch(r"embodiment_[a-z0-9_]{1,95}", candidate)
                else "embodiment_episode_execution_failed"
            )
            record.timeline.append(
                {
                    "code": record.failure,
                    "kind": "episode_failed",
                    "sequence": len(record.timeline),
                }
            )
        finally:
            await frame_pump.finish()
            await live_preview_pump.finish()
            record.live_preview_pump = None
            self._credentials.discard_episode(record.spec.episode_id)

    async def status(self, episode_id: str) -> Mapping[str, Any]:
        return self._status(await self._record(episode_id))

    async def timeline(self, episode_id: str) -> tuple[Mapping[str, Any], ...]:
        record = await self._record(episode_id)
        return tuple(dict(event) for event in record.timeline)

    async def result(self, episode_id: str) -> Mapping[str, Any]:
        record = await self._record(episode_id)
        if record.state not in ("completed", "cancelled", "failed"):
            raise EpisodeResultNotReadyError()
        value: dict[str, Any] = dict(self._status(record))
        value["result"] = None if record.outcome is None else record.outcome.public_result()
        return value

    async def frame(self, episode_id: str) -> EpisodeFrameView:
        record = await self._record(episode_id)
        snapshot = record.frames.snapshot()
        if record.state in ("completed", "cancelled", "failed"):
            state = "finished"
        elif snapshot is None:
            state = "loading"
        else:
            state = "live"
        return EpisodeFrameView(state, snapshot)

    async def preview_subscription(
        self, episode_id: str
    ) -> tuple[int, asyncio.Queue[ParticipantFrameSnapshot], ParticipantFrameSnapshot | None]:
        record = await self._record(episode_id)
        token, queue = record.preview.subscribe()
        return token, queue, record.frames.snapshot()

    async def unsubscribe_preview(self, episode_id: str, token: int) -> None:
        record = await self._record(episode_id)
        record.preview.unsubscribe(token)

    async def publish_live_preview(
        self, episode_id: str, participant_id: str, sequence: int, jpeg: bytes
    ) -> bool:
        """Queue a signed Godot ingress frame without touching canonical frame/replay state."""

        record = await self._record(episode_id)
        pump = record.live_preview_pump
        if pump is None:
            return False
        return await pump.publish(participant_id, sequence, jpeg)

    async def live_preview_subscription(
        self, episode_id: str
    ) -> tuple[
        int,
        asyncio.Queue[ParticipantLivePreviewSnapshot],
        ParticipantLivePreviewSnapshot | None,
    ]:
        """Subscribe to direct Godot presentation pixels only, never canonical frame traffic."""

        record = await self._record(episode_id)
        token, queue = record.live_preview.subscribe()
        return token, queue, record.live_preview_frames.snapshot()

    async def unsubscribe_live_preview(self, episode_id: str, token: int) -> None:
        record = await self._record(episode_id)
        record.live_preview.unsubscribe(token)

    async def replay(self, episode_id: str) -> EpisodeArtifactBundle:
        record = await self._record(episode_id)
        if record.public_bundle is None:
            raise EpisodeReplayNotReadyError()
        return record.public_bundle

    async def evaluation(self, episode_id: str) -> Mapping[str, Any]:
        record = await self._record(episode_id)
        if record.public_bundle is None or record.outcome is None:
            raise EpisodeEvaluationNotReadyError()
        return _evaluation_projection(record).as_dict()

    async def saved_replays(self, *, limit: int = 50) -> tuple[SavedReplay, ...]:
        """List only completed participant-video replays from the local archive."""

        if self._replay_archive is None:
            return ()
        return await asyncio.to_thread(self._replay_archive.list, limit=limit)

    async def saved_replay(self, replay_id: str) -> SavedReplay | None:
        if self._replay_archive is None:
            return None
        return await asyncio.to_thread(self._replay_archive.get, replay_id)

    async def saved_replay_video_path(self, replay_id: str):
        """Trusted router helper; raw replays are deliberately not available here."""

        if self._replay_archive is None:
            return None
        return await asyncio.to_thread(self._replay_archive.video_path, replay_id)

    async def saved_replay_public_bundle_path(self, replay_id: str):
        if self._replay_archive is None:
            return None
        return await asyncio.to_thread(self._replay_archive.public_bundle_path, replay_id)

    async def saved_replay_evaluation(self, replay_id: str) -> Mapping[str, Any] | None:
        if self._replay_archive is None:
            return None
        return await asyncio.to_thread(self._replay_archive.evaluation, replay_id)

    async def protected_bundle(self, episode_id: str) -> EpisodeArtifactBundle:
        """Return protected evidence to trusted local certification code, never the API router."""

        record = await self._record(episode_id)
        if record.protected_bundle is None:
            raise EpisodeReplayNotReadyError()
        return record.protected_bundle

    async def cancel(self, episode_id: str) -> Mapping[str, Any]:
        record = await self._record(episode_id)
        if record.state in ("queued", "running"):
            record.cancel_event.set()
            if record.task is not None:
                record.task.cancel()
                try:
                    await record.task
                except asyncio.CancelledError:
                    pass
        return self._status(record)

    async def aclose(self) -> None:
        async with self._service_lock():
            records = tuple(self._records.values())
        for record in records:
            if record.task is not None and not record.task.done():
                record.cancel_event.set()
                record.task.cancel()
        await asyncio.gather(
            *(record.task for record in records if record.task is not None),
            return_exceptions=True,
        )
        # Let a completed user-visible replay finish rendering during graceful shutdown instead
        # of leaving a durable partial archive.  The archive writes only through a private staging
        # directory and finalizes atomically.
        await asyncio.gather(
            *(record.replay_task for record in records if record.replay_task is not None),
            return_exceptions=True,
        )
        for record in records:
            record.frames.close()
            record.preview.close()
            record.live_preview_frames.close()
            record.live_preview.close()
        self._credentials.close()

    async def _record(self, episode_id: str) -> _EpisodeRecord:
        async with self._service_lock():
            record = self._records.get(episode_id)
        if record is None:
            raise EpisodeNotFoundError()
        return record

    @staticmethod
    def _status(record: _EpisodeRecord) -> Mapping[str, Any]:
        value: dict[str, Any] = {
            "certification_eligible": False,
            "config": record.spec.public_dict(),
            "episode_id": record.spec.episode_id,
            "failure": record.failure,
            "run_class": record.spec.run_class,
            "state": record.state,
        }
        if record.progress_observation_seq is not None and record.progress_tick is not None:
            value["progress"] = {
                "authority_tick": record.progress_tick,
                "observation_seq": record.progress_observation_seq,
            }
        if record.replay_state is not None:
            replay: dict[str, str] = {"state": record.replay_state}
            if record.saved_replay is not None:
                replay["replay_id"] = record.saved_replay.replay_id
            value["replay"] = replay
        return value

    def _start_replay_archive(self, record: _EpisodeRecord, bundles: EpisodeBundles) -> None:
        """Start presentation-only archival after the authority outcome has sealed."""

        if self._replay_archive is None or not _is_archivable_solo_demo(record.spec):
            return
        try:
            projection = _evaluation_projection(record)
        except Exception:
            record.replay_state = "unavailable"
            return
        record.replay_state = "saving"
        record.replay_task = asyncio.create_task(
            self._archive_completed(record, bundles, projection),
            name=f"embodiment-replay-archive-{record.spec.episode_id}",
        )

    async def _archive_completed(
        self,
        record: _EpisodeRecord,
        bundles: EpisodeBundles,
        evaluation: EvaluationProjection,
    ) -> None:
        archive = self._replay_archive
        if archive is None:
            return
        try:
            saved = await archive.save(record.spec, bundles, evaluation=evaluation)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never allow optional local presentation output to change a sealed authority result,
            # and never reflect renderer/process output to a browser route.
            record.replay_state = "unavailable"
            return
        record.saved_replay = saved
        record.replay_state = "ready"

    @staticmethod
    def _append_public_evidence(record: _EpisodeRecord, bundle: EpisodeArtifactBundle) -> None:
        for role, kind in (("receipts", "action_receipts"), ("public_events", "authority_event")):
            try:
                values = strict_json_loads(bundle.read(role))
            except Exception:
                continue
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, Mapping):
                    record.timeline.append(
                        {"kind": kind, "sequence": len(record.timeline), "value": dict(value)}
                    )


__all__ = [
    "DEMO_PROVIDER",
    "EpisodeExecutor",
    "EpisodeFrameView",
    "EpisodeEvaluationNotReadyError",
    "EpisodeNotFoundError",
    "EpisodeReplayNotReadyError",
    "EpisodeResultNotReadyError",
    "EpisodeRunSpec",
    "EpisodeService",
    "EpisodeServiceError",
    "demo_fixture_bytes",
]


def _evaluation_projection(record: _EpisodeRecord) -> EvaluationProjection:
    if record.public_bundle is None or record.outcome is None:
        raise EpisodeEvaluationNotReadyError()
    public = EpisodeArtifactBundle.verify(record.public_bundle.bundle_bytes)
    if public.layer != "public":
        raise EpisodeEvaluationNotReadyError()
    values = {
        role: strict_json_loads(public.read(role))
        for role in ("evaluation", "public_events", "receipts", "replay_summary")
    }
    run: dict[str, Any] = {
        "certification_eligible": False,
        "episode_id": record.spec.episode_id,
        "run_class": record.spec.run_class,
        "task_id": record.spec.task_id,
    }
    if record.spec.scenario_id is not None:
        scenario = demo_scenario(record.spec.scenario_id)
        run.update(
            {
                "evaluation_profile_id": scenario.evaluation_profile_id,
                "scenario_id": scenario.scenario_id,
            }
        )
    return build_solo_evaluation_projection(
        evaluation=values["evaluation"],
        replay_summary=values["replay_summary"],
        run_spec=run,
        result=record.outcome.public_result(),
        receipts=values["receipts"],
        public_events=values["public_events"],
    )


def _demo_policy_lock(
    *, model: str, task_id: str, scenario_id: str, seed: int
) -> DemoPolicyLock:
    """Freeze the deterministic solo fixture without adding scenario data to ProviderRequest."""

    scenario = demo_scenario(scenario_id)
    if scenario.authority_task_id != task_id or scenario.provider_model != model:
        raise ValueError("demo scenario does not match task/model")
    fixture = demo_scenario_fixture_bytes(scenario_id)

    return DemoPolicyLock(
        scenario_id=scenario_id,
        policy_id=scenario.policy_id,
        fixture_sha256=hashlib.sha256(fixture).hexdigest(),
        seed=seed,
        participant_id="participant_0",
        model=model,
        total_decision_budget=scenario.total_decision_budget,
    )


def demo_fixture_bytes(
    *,
    model: str,
    task_id: str,
    scenario_id: str | None = None,
    policy_source_sha256: str | None = None,
) -> bytes:
    """Compatibility wrapper for catalog-bound deterministic fixture material."""

    resolved_id = task_id if scenario_id is None else scenario_id
    scenario = demo_scenario(resolved_id)
    if scenario.authority_task_id != task_id or scenario.provider_model != model:
        raise ValueError("demo scenario does not match task/model")
    digests = None
    if policy_source_sha256 is not None:
        if re.fullmatch(r"[0-9a-f]{64}", policy_source_sha256) is None:
            raise ValueError("demo policy source digest is invalid")
        if len(scenario.policy_source_ids) != 1:
            raise ValueError("single-source fixture compatibility is unavailable")
        digests = {scenario.policy_source_ids[0]: policy_source_sha256}
    return demo_scenario_fixture_bytes(resolved_id, policy_source_sha256=digests)


def _is_archivable_solo_demo(spec: EpisodeRunSpec) -> bool:
    if spec.provider == DEMO_PROVIDER:
        try:
            scenario = demo_scenario(spec.scenario_id or "")
        except (TypeError, ValueError):
            return False
        return (
            scenario.authority_task_id == spec.task_id
            and scenario.provider_model == spec.model
        )
    provider = (
        SCRIPTED_CONSTRUCTION_PROVIDER if spec.provider == DEMO_PROVIDER else spec.provider
    )
    return is_scripted_solo_demo(
        provider=provider,
        model=spec.model,
        task_id=spec.task_id,
    )
