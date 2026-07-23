"""Retry-free live-model orchestration for one solo embodiment episode."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Tuple

from .artifacts import EpisodeArtifactRecorder, EpisodeBundles
from .construction_task_provider import TASK_PROMPT
from .contracts import (
    AsyncEnvironmentSession,
    ControllerAction,
    ControllerButtons,
    ControllerState,
    DecisionWindow,
    EpisodeConfig,
)
from .protocol import (
    EmbodimentProtocolPackage,
    ProtocolValidationError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from .providers.contracts import (
    ProviderAdapter,
    ProviderAuditRecord,
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
    provider_capabilities,
)
from .scratchpad import EpisodeScratchpad
from .scripted_solo_demo import is_scripted_solo_demo

_MAX_INPUT_BYTES = 8_388_608
_MAX_OUTPUT_BYTES = 4_096
_REALTIME_AUTHORITY_TICK_NS = 100_000_000


class LiveSoloError(RuntimeError):
    """Stable live-runner failure that contains no provider payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LiveSoloOutcome:
    episode_id: str
    terminal: Mapping[str, Any]
    final_state_hash: str
    windows: int
    provider_failures: int
    bundles: EpisodeBundles | None

    def public_result(self) -> Mapping[str, Any]:
        return {
            "episode_id": self.episode_id,
            "final_state_hash": self.final_state_hash,
            "provider_failures": self.provider_failures,
            "terminal": dict(self.terminal),
            "windows": self.windows,
        }


@dataclass(frozen=True)
class _ActionAttempt:
    action: ControllerAction | None
    result: ProviderCallResult
    frame: bytes | None
    raw_output: bytes | None
    request: ProviderRequest
    adapter_audits: Tuple[ProviderAuditRecord, ...]
    parsing_disposition: str
    task_continuation: bool


class LiveSoloRunner:
    """Drive an async authority session through exactly one provider call per boundary."""

    def __init__(
        self,
        *,
        config: EpisodeConfig,
        session: AsyncEnvironmentSession,
        provider: ProviderAdapter,
        model: str,
        system_prompt: str,
        protocol_package: EmbodimentProtocolPackage,
        provider_timeout_s: float = 45.0,
        fallback_duration_ticks: int = 10,
        frame_publisher: Callable[[str, int, bytes], Awaitable[None]] | None = None,
        progress_publisher: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> None:
        if config.mode != "solo-curriculum-v0" or len(config.participant_ids) != 1:
            raise ValueError("LiveSoloRunner requires a solo episode")
        if not isinstance(provider_timeout_s, (int, float)) or provider_timeout_s <= 0:
            raise ValueError("provider_timeout_s must be positive")
        if (
            isinstance(fallback_duration_ticks, bool)
            or not isinstance(fallback_duration_ticks, int)
            or not 1 <= fallback_duration_ticks <= 20
        ):
            raise ValueError("fallback_duration_ticks must be from 1 to 20")
        self.config = config
        self.session = session
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.package = protocol_package
        self.provider_timeout_s = float(provider_timeout_s)
        self.fallback_duration_ticks = fallback_duration_ticks
        self.frame_publisher = frame_publisher
        self.progress_publisher = progress_publisher
        # Hybrid observations may deliberately reuse the same participant-visible frame while a
        # deterministic Construction executor advances.  Keep exactly one immutable image so a
        # repeated transport_ref cannot trigger another frame socket round-trip or browser PNG
        # publication.  The cache key includes the advertised digest as defence-in-depth.
        self._cached_frame_key: tuple[str, str, str, str] | None = None
        self._cached_frame_png: bytes | None = None

    async def run(self, *, cancel_event: asyncio.Event | None = None) -> LiveSoloOutcome:
        cancel = cancel_event or asyncio.Event()
        participant_id = self.config.participant_ids[0]
        scratchpad = EpisodeScratchpad()
        recorder = EpisodeArtifactRecorder(self.config.episode_id, protocol_package=self.package)
        run_settings: dict[str, Any] = {
            "certification_eligible": False,
            "run_class": (
                "demo"
                if self.provider.provider_name == "demo"
                else "scripted"
                if self.provider.provider_name == "scripted"
                else "live"
            ),
        }
        try:
            capabilities = provider_capabilities(self.provider.provider_name)
        except ValueError:
            # Golden/injected test adapters predate the production provider registry. They remain
            # sealable, but no transport capability claim is invented for them.
            capabilities = None
        if capabilities is not None:
            # Evidence keys deliberately avoid credential/header aliases rejected by the
            # artifact disclosure scanner. The values are the same immutable transport flags.
            run_settings["provider_capabilities"] = {
                "credential_required": capabilities.requires_credential,
                "networked": capabilities.is_networked,
                "provider_name": capabilities.provider_name,
            }
        policy_lock = getattr(self.provider, "policy_lock", None)
        if policy_lock is not None:
            as_dict = getattr(policy_lock, "as_dict", None)
            sha256 = getattr(policy_lock, "sha256", None)
            if not callable(as_dict) or not isinstance(sha256, str):
                raise LiveSoloError("embodiment_demo_policy_lock_invalid")
            run_settings["demo_policy_lock"] = as_dict()
            run_settings["demo_policy_lock_sha256"] = sha256
        recorder.freeze_run_configuration(
            provider=self.provider.provider_name,
            model=self.model,
            settings={
                "action_schema_sha256": hashlib.sha256(
                    canonical_json_bytes(self.package.schema("controller-action"))
                ).hexdigest(),
                "fallback_duration_ticks": self.fallback_duration_ticks,
                "max_input_bytes": _MAX_INPUT_BYTES,
                "max_output_bytes": _MAX_OUTPUT_BYTES,
                "observation_profile": self.config.observation_profile,
                "provider_timeout_ms": _deadline_budget_ms(self.provider_timeout_s),
                "system_prompt_sha256": hashlib.sha256(
                    self.system_prompt.encode("utf-8")
                ).hexdigest(),
                **run_settings,
            },
        )
        windows = 0
        failures = 0
        consecutive_failures = 0
        try:
            observations = await self.session.reset()
            observation = observations[participant_id]
            state = await self.session.state()
            state_hash = _state_hash(state)
            recorder.record_boundary(
                observation_seq=0,
                state_hash=state_hash,
                observations=observations,
                terminal=observation["terminal"],
            )
            await self._publish_progress(observation)
            while not observation["terminal"]["ended"]:
                if cancel.is_set():
                    raise asyncio.CancelledError
                authority_tick_started_ns = time.monotonic_ns()
                request_scratchpad = scratchpad.utf8
                attempt = await self._request_action(
                    participant_id=participant_id,
                    observation=observation,
                    scratchpad=scratchpad,
                )
                action = attempt.action
                result = attempt.result
                if result.failure is not None:
                    failures += 1
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                reason = None
                if action is None:
                    reason = (
                        "timeout" if result.failure == ProviderFailureKind.TIMEOUT else "invalid"
                    )
                window = DecisionWindow.finalize(
                    episode_id=self.config.episode_id,
                    observation_seq=observation["observation_seq"],
                    mode=self.config.mode,
                    start_tick=observation["tick"],
                    participant_ids=self.config.participant_ids,
                    actions={participant_id: action} if action is not None else {},
                    failure_reasons={} if reason is None else {participant_id: reason},
                    duration_ticks=(
                        action.control.duration_ticks
                        if action is not None
                        else self.fallback_duration_ticks
                    ),
                )
                step = await self.session.step(window)
                windows += 1
                # Credential-free solo demos advance one authority tick at a time.  This makes
                # walking, turns, interaction, and neutral combat observable at 10 Hz while
                # preserving immediate authority/replay semantics. Sleep only for the unused
                # portion of the 100 ms budget: a fixed sleep *after* frame/render work makes a
                # nominal real-time demo drift beyond its intended duration.
                if (
                    (
                        self.provider.provider_name == "demo"
                        and self.config.task_id
                        in ("movement-maze-v0", "operator-action-course-v0")
                        or is_scripted_solo_demo(
                            provider=(
                                "scripted"
                                if self.provider.provider_name == "demo"
                                else self.provider.provider_name
                            ),
                            model=self.model,
                            task_id=self.config.task_id,
                        )
                    )
                    and action is not None
                    and not step.terminal.ended
                ):
                    remaining_ns = _REALTIME_AUTHORITY_TICK_NS - (
                        time.monotonic_ns() - authority_tick_started_ns
                    )
                    if remaining_ns > 0:
                        await asyncio.sleep(remaining_ns / 1_000_000_000)
                if action is not None:
                    scratchpad.set(action.memory_update)
                if not attempt.task_continuation:
                    recorder.record_provider_call(
                        observation_seq=observation["observation_seq"],
                        prompt=attempt.request.system_prompt,
                        raw_output=attempt.raw_output,
                        scratchpad_utf8=request_scratchpad,
                        scratchpad_after_utf8=scratchpad.utf8,
                        telemetry={
                            **result.telemetry.as_dict(),
                            "failure": None if result.failure is None else result.failure.value,
                            "provider": self.provider.provider_name,
                        },
                        frame_png=attempt.frame,
                        frame_metadata=(
                            dict(observation["frame"])
                            if isinstance(observation.get("frame"), Mapping)
                            else None
                        ),
                        participant_id=participant_id,
                        provider_evidence=self._provider_evidence(attempt),
                    )
                recorder.record_boundary(
                    observation_seq=observation["observation_seq"] + 1,
                    state_hash=step.state_hash,
                    observations=step.observations,
                    receipts={key: value.as_dict() for key, value in step.receipts.items()},
                    public_events=(event.as_dict() for event in step.public_events),
                    terminal=step.terminal.as_dict(),
                )
                observation = step.observations[participant_id]
                state_hash = step.state_hash
                await self._publish_progress(observation)
                # Every invalid response has already advanced a recorded neutral window. Do not
                # then burn the full episode budget in a tight no-input loop when the provider is
                # clearly unavailable or cannot produce this task contract.
                if consecutive_failures >= 3:
                    raise LiveSoloError("embodiment_provider_unavailable")

            terminal = dict(observation["terminal"])
            terminal_frame = observation.get("frame")
            if isinstance(terminal_frame, Mapping):
                terminal_png = await self._frame_for_observation(
                    participant_id=participant_id,
                    observation=observation,
                )
                if terminal_png is None:
                    raise LiveSoloError("embodiment_terminal_frame_unavailable")
                recorder.record_frame(
                    observation_seq=int(observation["observation_seq"]),
                    participant_id=participant_id,
                    frame_metadata=terminal_frame,
                    frame_png=terminal_png,
                )
            bundles = self._seal_if_replay_available(
                recorder,
                evaluation={
                    "episode_id": self.config.episode_id,
                    "provider_failures": failures,
                    "terminal": terminal,
                    "windows": windows,
                },
            )
            return LiveSoloOutcome(
                self.config.episode_id, terminal, state_hash, windows, failures, bundles
            )
        finally:
            scratchpad.close()
            await self.session.close()

    async def _request_action(
        self,
        *,
        participant_id: str,
        observation: Mapping[str, Any],
        scratchpad: EpisodeScratchpad,
    ) -> _ActionAttempt:
        frame = await self._frame_for_observation(
            participant_id=participant_id,
            observation=observation,
        )
        provider_observation = dict(observation)
        # Scratchpad state is Python-owned. Godot receives memory_update only as protocol input
        # and never owns or persists the episode scratchpad.
        provider_observation["memory"] = scratchpad.text
        deadline = time.monotonic_ns() + int(self.provider_timeout_s * 1_000_000_000)
        request = ProviderRequest(
            episode_id=self.config.episode_id,
            participant_id=participant_id,
            observation_seq=observation["observation_seq"],
            deadline_monotonic_ns=deadline,
            model=self.model,
            system_prompt=self.system_prompt,
            observation_json=canonical_json_bytes(provider_observation),
            action_schema_json=canonical_json_bytes(self.package.schema("controller-action")),
            scratchpad_utf8=scratchpad.utf8,
            frame_png=frame,
            max_input_bytes=_MAX_INPUT_BYTES,
            max_output_bytes=_MAX_OUTPUT_BYTES,
        )
        started = time.monotonic_ns()
        try:
            result = await asyncio.wait_for(
                self.provider.request(request), timeout=self.provider_timeout_s
            )
            if not isinstance(result, ProviderCallResult):
                raise TypeError("provider returned an invalid result type")
        except asyncio.TimeoutError:
            result = ProviderCallResult.failed(
                ProviderFailureKind.TIMEOUT,
                ProviderTelemetry(latency_ms=(time.monotonic_ns() - started) // 1_000_000),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = ProviderCallResult.failed(
                ProviderFailureKind.INTERNAL,
                ProviderTelemetry(latency_ms=(time.monotonic_ns() - started) // 1_000_000),
            )
        task_continuation = result.failure is None and bool(
            getattr(self.provider, "last_request_was_continuation", False)
        )
        adapter_audits = self._drain_adapter_audits(request)
        evidence_request = adapter_audits[0].request if adapter_audits else request
        raw_output = result.raw_output
        evidence_raw_output = (
            adapter_audits[0].result.raw_output if adapter_audits else raw_output
        )
        if raw_output is None:
            return _ActionAttempt(
                None,
                result,
                frame,
                evidence_raw_output,
                evidence_request,
                adapter_audits,
                "not_attempted",
                task_continuation,
            )
        if len(raw_output) > request.max_output_bytes:
            return _ActionAttempt(
                None,
                ProviderCallResult.failed(ProviderFailureKind.OUTPUT_TOO_LARGE, result.telemetry),
                frame,
                evidence_raw_output,
                evidence_request,
                adapter_audits,
                "output_too_large",
                task_continuation,
            )
        try:
            action = parse_controller_action(raw_output, package=self.package)
            if action.episode_id != self.config.episode_id:
                raise ValueError("controller action episode differs")
            if action.observation_seq != observation["observation_seq"]:
                raise ValueError("controller action observation sequence differs")
        except (ProtocolValidationError, TypeError, ValueError, KeyError):
            result = ProviderCallResult.failed(
                ProviderFailureKind.INVALID_RESPONSE, result.telemetry
            )
            action = None
        parsing_disposition = "accepted" if action is not None else "rejected"
        if evidence_request.action_schema_json != request.action_schema_json:
            # A task-planning wrapper may turn a separately schema-checked milestone into a
            # controller action (including a safe local wait for an invalid milestone).  The
            # protected raw evidence is the planner output, not that generated controller JSON,
            # so claiming the runner parsed the raw evidence as a controller action would be
            # misleading.
            parsing_disposition = "not_attempted"
        return _ActionAttempt(
            action,
            result,
            frame,
            evidence_raw_output,
            evidence_request,
            adapter_audits,
            parsing_disposition,
            task_continuation,
        )

    async def _frame_for_observation(
        self,
        *,
        participant_id: str,
        observation: Mapping[str, Any],
    ) -> bytes | None:
        """Return a locally cached participant frame or fetch one at a fresh boundary.

        Godot binds a frame to a content-addressed transport reference.  During a locally
        generated autonomous task the reference is intentionally stable, so requesting it again
        would only consume transport, PNG processing, and browser work.  Semantics in the
        observation remain current; this helper caches pixels only.
        """

        metadata = observation.get("frame")
        if not isinstance(metadata, Mapping):
            return None
        sensor_id = str(metadata["sensor_id"])
        transport_ref = str(metadata["transport_ref"])
        frame_sha256 = str(metadata["sha256"])
        key = (participant_id, sensor_id, transport_ref, frame_sha256)
        if key == self._cached_frame_key and self._cached_frame_png is not None:
            return self._cached_frame_png
        frame = await self.session.render(
            participant_id,
            sensor_id,
            transport_ref,
            int(observation["observation_seq"]),
        )
        self._cached_frame_key = key
        self._cached_frame_png = frame
        await self._publish_frame(participant_id, int(observation["observation_seq"]), frame)
        return frame

    async def _publish_frame(self, participant_id: str, observation_seq: int, frame: bytes) -> None:
        if self.frame_publisher is not None:
            await self.frame_publisher(participant_id, observation_seq, frame)

    async def _publish_progress(self, observation: Mapping[str, Any]) -> None:
        """Expose only monotonic public timing, never observation contents or authority state."""

        if self.progress_publisher is None:
            return
        observation_seq = observation.get("observation_seq")
        tick = observation.get("tick")
        if (
            isinstance(observation_seq, bool)
            or isinstance(tick, bool)
            or not isinstance(observation_seq, int)
            or not isinstance(tick, int)
            or observation_seq < 0
            or tick < 0
        ):
            return
        # The dashboard lifecycle projection is strictly best effort.  It cannot be allowed to
        # delay, reject, or otherwise alter a deterministic authority step.
        try:
            await self.progress_publisher(observation_seq, tick)
        except Exception:
            pass

    def _drain_adapter_audits(self, request: ProviderRequest) -> Tuple[ProviderAuditRecord, ...]:
        audit_log = getattr(self.provider, "audit_log", None)
        drain = getattr(audit_log, "drain_episode", None)
        if not callable(drain):
            return ()
        records = drain(self.config.episode_id)
        if not isinstance(records, tuple) or any(
            not isinstance(record, ProviderAuditRecord) for record in records
        ):
            raise LiveSoloError("embodiment_provider_audit_invalid")
        if len(records) > 1 or any(
            not self._audit_request_matches(request, record.request)
            or record.provider != self.provider.provider_name
            for record in records
        ):
            raise LiveSoloError("embodiment_provider_audit_mismatch")
        return records

    def _audit_request_matches(
        self, controller_request: ProviderRequest, audited_request: ProviderRequest
    ) -> bool:
        if audited_request == controller_request:
            return True
        if self.config.task_id != "construction-v0":
            return False
        expected = replace(
            controller_request,
            system_prompt=TASK_PROMPT,
            action_schema_json=canonical_json_bytes(
                self.package.schema("construction-task-plan")
            ),
        )
        return audited_request == expected

    def _provider_evidence(self, attempt: _ActionAttempt) -> Mapping[str, Any]:
        request = attempt.request
        observation = strict_json_loads(request.observation_json)
        if not isinstance(observation, dict):
            raise LiveSoloError("embodiment_provider_observation_invalid")
        visible_payload = {
            key: value for key, value in observation.items() if key not in ("frame", "memory")
        }
        frame_metadata = observation.get("frame")
        frame_metadata_sha256 = (
            canonical_sha256(frame_metadata) if isinstance(frame_metadata, Mapping) else None
        )
        frame_sha256 = (
            hashlib.sha256(request.frame_png).hexdigest() if request.frame_png is not None else None
        )
        bound = (
            frame_metadata_sha256 is None
            and frame_sha256 is None
            or isinstance(frame_metadata, Mapping)
            and frame_metadata.get("sha256") == frame_sha256
        )
        adapter = attempt.adapter_audits[0] if attempt.adapter_audits else None
        adapter_disposition = None
        adapter_duration_ms = None
        if adapter is not None:
            adapter_disposition = (
                "output" if adapter.result.failure is None else adapter.result.failure.value
            )
            adapter_duration_ms = (
                adapter.completed_monotonic_ns - adapter.started_monotonic_ns
            ) // 1_000_000
        return {
            "provider": self.provider.provider_name,
            "model": request.model,
            "schema_sha256": hashlib.sha256(request.action_schema_json).hexdigest(),
            "max_input_bytes": request.max_input_bytes,
            "max_output_bytes": request.max_output_bytes,
            "deadline_budget_ms": _deadline_budget_ms(self.provider_timeout_s),
            "request_material_sha256": _request_material_sha256(request),
            "parsing_disposition": attempt.parsing_disposition,
            "adapter_audit": {
                "recorded": adapter is not None,
                "duration_ms": adapter_duration_ms,
                "disposition": adapter_disposition,
            },
            "visibility_frame_binding": {
                "bound": bound,
                "participant_id": request.participant_id,
                "profile": observation.get("profile"),
                "observation_sha256": hashlib.sha256(request.observation_json).hexdigest(),
                "visible_payload_sha256": canonical_sha256(visible_payload),
                "frame_metadata_sha256": frame_metadata_sha256,
                "frame_sha256": frame_sha256,
            },
        }

    def _seal_if_replay_available(
        self, recorder: EpisodeArtifactRecorder, *, evaluation: Mapping[str, Any]
    ) -> EpisodeBundles | None:
        try:
            replay = self.session.replay_bytes  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, ValueError):
            return None
        return recorder.seal(authority_replay=replay, evaluation=evaluation)


def parse_controller_action(
    payload: bytes, *, package: EmbodimentProtocolPackage
) -> ControllerAction:
    """Strictly parse one action; never repair or coerce provider output."""

    value = strict_json_loads(payload)
    if not isinstance(value, dict):
        raise ValueError("controller action must be an object")
    package.validate("controller-action", value)
    control = value["control"]
    buttons = control["buttons"]
    return ControllerAction(
        protocol_version=value["protocol_version"],
        episode_id=value["episode_id"],
        observation_seq=value["observation_seq"],
        action_id=value["action_id"],
        control=ControllerState(
            move_x=control["move_x"],
            move_y=control["move_y"],
            look_x=control["look_x"],
            look_y=control["look_y"],
            duration_ticks=control["duration_ticks"],
            buttons=ControllerButtons(**buttons),
            autonomous_task=control.get("autonomous_task"),
        ),
        intent_label=value["intent_label"],
        memory_update=value["memory_update"],
    )


def _state_hash(value: Mapping[str, Any]) -> str:
    state_hash = value.get("state_hash")
    if not isinstance(state_hash, str):
        raise LiveSoloError("embodiment_live_state_invalid")
    return state_hash


def _deadline_budget_ms(provider_timeout_s: float) -> int:
    return max(1, int(provider_timeout_s * 1_000))


def _request_material_sha256(request: ProviderRequest) -> str:
    return canonical_sha256(
        {
            "action_schema_sha256": hashlib.sha256(request.action_schema_json).hexdigest(),
            "frame_sha256": (
                None if request.frame_png is None else hashlib.sha256(request.frame_png).hexdigest()
            ),
            "observation_sha256": hashlib.sha256(request.observation_json).hexdigest(),
            "scratchpad_sha256": hashlib.sha256(request.scratchpad_utf8).hexdigest(),
            "system_prompt_sha256": hashlib.sha256(
                request.system_prompt.encode("utf-8")
            ).hexdigest(),
        }
    )


__all__ = ["LiveSoloError", "LiveSoloOutcome", "LiveSoloRunner", "parse_controller_action"]
