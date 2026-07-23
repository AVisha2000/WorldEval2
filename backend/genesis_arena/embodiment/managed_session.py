"""Async Python owner for one managed WorldArena authority process."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any, Dict, Mapping

from .contracts import (
    ActionReceipt,
    AsyncEnvironmentSession,
    AuthorityEvent,
    DecisionWindow,
    EpisodeConfig,
    MultiParticipantStepResult,
    ReceiptEffect,
    TerminalState,
    TrioParticipantOutcome,
    TrioPlacementGroup,
    TrioResult,
)
from .managed_process import ManagedLaunchSpec, ManagedProcessHandle, ManagedProcessLauncher
from .protocol import (
    EmbodimentProtocolPackage,
    ProtocolValidationError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from .protocol_registry import EmbodimentProtocolRegistry
from .replay import ReplayLedger, verify_replay_bytes
from .transport import EmbodimentTransportError, ManagedSocket


class ManagedSessionError(RuntimeError):
    """Stable orchestration failure without protected payload content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ManagedWorldArenaSession:
    """One-shot managed session with cached public projections and replay ledger."""

    def __init__(
        self,
        *,
        config: EpisodeConfig,
        launcher: ManagedProcessLauncher,
        launch_spec: ManagedLaunchSpec,
        socket_future: asyncio.Future[ManagedSocket],
        protocol_package: EmbodimentProtocolPackage,
        attachment_timeout_s: float = 10.0,
        step_timeout_s: float = 10.0,
        close_timeout_s: float = 5.0,
    ) -> None:
        if min(attachment_timeout_s, step_timeout_s, close_timeout_s) <= 0:
            raise ValueError("managed session timeouts must be positive")
        config_value = episode_config_as_dict(config)
        try:
            protocol_package.validate("episode-config", config_value)
        except ProtocolValidationError as error:
            raise ValueError("episode config does not match protocol package") from error
        if (
            launch_spec.episode_id != config.episode_id
            or dict(launch_spec.config) != config_value
            or launch_spec.config_sha256 != canonical_sha256(config_value)
            or config.protocol_version != protocol_package.PROTOCOL_VERSION
            or launch_spec.protocol_package_sha256 != protocol_package.package_sha256
        ):
            raise ValueError("launch spec and episode config differ")
        self._config = config
        self._config_value = config_value
        self._launcher = launcher
        self._launch_spec = launch_spec
        self._socket_future = socket_future
        self._package = protocol_package
        self._attachment_timeout_s = attachment_timeout_s
        self._step_timeout_s = step_timeout_s
        self._close_timeout_s = close_timeout_s
        self._process: ManagedProcessHandle | None = None
        self._socket: ManagedSocket | None = None
        self._observations: Dict[str, Mapping[str, Any]] = {}
        self._state_hash: str | None = None
        self._terminal = TerminalState(False, "running", "running")
        self._ledger = ReplayLedger(
            config=config_value,
            config_sha256=launch_spec.config_sha256,
            protocol_package_sha256=launch_spec.protocol_package_sha256,
        )
        self._started = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        # A hybrid observation may retain a valid participant frame reference while Godot advances
        # a deterministic autonomous task.  Cache one immutable, integrity-checked response so
        # callers do not ask the host to serialize the exact same image for every authority tick.
        self._render_cache_key: tuple[str, str, str, str] | None = None
        self._render_cache_png: bytes | None = None

    @property
    def protocol_version(self) -> str:
        return self._package.PROTOCOL_VERSION

    @classmethod
    def from_protocol_registry(
        cls,
        *,
        config: EpisodeConfig,
        launcher: ManagedProcessLauncher,
        launch_spec: ManagedLaunchSpec,
        socket_future: asyncio.Future[ManagedSocket],
        protocol_registry: EmbodimentProtocolRegistry,
        attachment_timeout_s: float = 10.0,
        step_timeout_s: float = 10.0,
        close_timeout_s: float = 5.0,
    ) -> ManagedWorldArenaSession:
        """Select a hash-bound v1/v2 package before constructing a managed session."""

        package = protocol_registry.package_for_launch(
            config.as_dict(), launch_spec.protocol_package_sha256
        )
        return cls(
            config=config,
            launcher=launcher,
            launch_spec=launch_spec,
            socket_future=socket_future,
            protocol_package=package,
            attachment_timeout_s=attachment_timeout_s,
            step_timeout_s=step_timeout_s,
            close_timeout_s=close_timeout_s,
        )

    async def reset(self) -> Mapping[str, Mapping[str, Any]]:
        if self._started or self._closed:
            raise ManagedSessionError("embodiment_session_reset_invalid")
        try:
            self._process = await self._launcher.launch(self._launch_spec)
            self._socket = await asyncio.wait_for(
                asyncio.shield(self._socket_future), self._attachment_timeout_s
            )
            ready = await asyncio.wait_for(
                self._socket.receive(
                    expected_message_type="episode_ready",
                    expected_boundary_hash=self._launch_spec.config_sha256,
                ),
                self._attachment_timeout_s,
            )
            ready_fields = {"capability_status", "observations", "state_hash"}
            if self.protocol_version in ("llm-controller/0.2.0", "llm-controller/0.3.0"):
                ready_fields.add("protocol_package_sha256")
            if set(ready.body) != ready_fields:
                raise ManagedSessionError("embodiment_session_ready_invalid")
            if (
                self.protocol_version in ("llm-controller/0.2.0", "llm-controller/0.3.0")
                and ready.body["protocol_package_sha256"] != self._package.package_sha256
            ):
                raise ManagedSessionError("embodiment_session_ready_invalid")
            self._package.validate("capability-status", ready.body["capability_status"])
            observations = ready.body["observations"]
            state_hash = ready.body["state_hash"]
            self._validate_observations(observations)
            if (
                not isinstance(state_hash, str)
                or len(state_hash) != 64
                or any(character not in "0123456789abcdef" for character in state_hash)
                or ready.boundary_hash != self._launch_spec.config_sha256
            ):
                raise ManagedSessionError("embodiment_session_ready_invalid")
            self._observations = _copy_json(observations)
            self._state_hash = state_hash
            self._ledger.record_initial(observations=observations, state_hash=state_hash)
            self._started = True
            return _copy_json(self._observations)
        except asyncio.CancelledError:
            await asyncio.shield(self.close())
            raise
        except (asyncio.TimeoutError, EmbodimentTransportError, ProtocolValidationError) as error:
            await self.close()
            raise ManagedSessionError("embodiment_session_reset_failed") from error
        except ManagedSessionError:
            await self.close()
            raise
        except Exception as error:
            await self.close()
            raise ManagedSessionError("embodiment_session_reset_failed") from error

    async def step(self, window: DecisionWindow) -> MultiParticipantStepResult:
        self._require_active()
        if self._terminal.ended:
            raise ManagedSessionError("embodiment_session_terminal")
        if window.episode_id != self._config.episode_id or set(window.decisions) != set(
            self._config.participant_ids
        ):
            raise ManagedSessionError("embodiment_session_window_invalid")
        window_value = window.as_dict()
        self._package.validate("decision-window", window_value)
        socket = self._socket
        assert socket is not None and self._state_hash is not None
        try:
            await asyncio.wait_for(
                socket.send(
                    "decision_window",
                    boundary_hash=self._state_hash,
                    body={"window": window_value},
                ),
                self._step_timeout_s,
            )
            frame = await asyncio.wait_for(
                socket.receive(expected_message_type="step_result"), self._step_timeout_s
            )
            if set(frame.body) != {"result"} or not isinstance(frame.body["result"], Mapping):
                raise ManagedSessionError("embodiment_session_result_invalid")
            result_value = frame.body["result"]
            self._package.validate("multi-participant-step-result", result_value)
            if frame.boundary_hash != result_value.get("state_hash"):
                raise ManagedSessionError("embodiment_session_result_boundary_invalid")
            result = _result_from_dict(result_value)
            self._observations = _copy_json(result.observations)
            self._state_hash = result.state_hash
            self._terminal = result.terminal
            self._ledger.record_step(decision_window=window_value, result=result_value)
            if result.terminal.ended:
                replay = self._ledger.seal(
                    final_terminal=result.terminal.as_dict(),
                    final_state_hash=result.state_hash,
                )
                verify_replay_bytes(replay, package=self._package)
            return result
        except asyncio.CancelledError:
            await asyncio.shield(self.close())
            raise
        except asyncio.TimeoutError as error:
            await self.close()
            raise ManagedSessionError("embodiment_session_step_timeout") from error
        except (EmbodimentTransportError, ProtocolValidationError) as error:
            await self.close()
            raise ManagedSessionError("embodiment_session_step_failed") from error
        except ManagedSessionError:
            await self.close()
            raise
        except Exception as error:
            await self.close()
            raise ManagedSessionError("embodiment_session_step_failed") from error

    async def observe(self, participant_id: str) -> Mapping[str, Any]:
        self._require_started()
        if participant_id not in self._observations:
            raise KeyError(participant_id)
        return _copy_json(self._observations[participant_id])

    async def state(self) -> Mapping[str, Any]:
        self._require_started()
        return {
            "episode_id": self._config.episode_id,
            "state_hash": self._state_hash,
            "terminal": self._terminal.as_dict(),
        }

    async def render(
        self,
        participant_id: str,
        sensor_id: str,
        transport_ref: str,
        observation_seq: int,
    ) -> bytes:
        self._require_active()
        observation = self._observations.get(participant_id)
        metadata = observation.get("frame") if isinstance(observation, Mapping) else None
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("sensor_id") != sensor_id
            or metadata.get("transport_ref") != transport_ref
            or observation.get("observation_seq") != observation_seq
        ):
            raise ManagedSessionError("embodiment_render_reference_invalid")
        frame_sha256 = metadata.get("sha256")
        if not isinstance(frame_sha256, str):
            raise ManagedSessionError("embodiment_render_reference_invalid")
        cache_key = (participant_id, sensor_id, transport_ref, frame_sha256)
        if cache_key == self._render_cache_key and self._render_cache_png is not None:
            return self._render_cache_png
        socket = self._socket
        assert socket is not None and self._state_hash is not None
        try:
            await asyncio.wait_for(
                socket.send(
                    "frame_request",
                    boundary_hash=self._state_hash,
                    body={
                        "observation_seq": observation_seq,
                        "participant_id": participant_id,
                        "sensor_id": sensor_id,
                        "transport_ref": transport_ref,
                    },
                ),
                self._step_timeout_s,
            )
            response = await asyncio.wait_for(
                socket.receive(
                    expected_message_type="frame_response",
                    expected_boundary_hash=self._state_hash,
                ),
                self._step_timeout_s,
            )
            if set(response.body) != {
                "metadata",
                "observation_seq",
                "participant_id",
                "png_base64",
            }:
                raise ManagedSessionError("embodiment_render_response_invalid")
            if (
                response.body["metadata"] != metadata
                or response.body["participant_id"] != participant_id
                or response.body["observation_seq"] != observation_seq
                or not isinstance(response.body["png_base64"], str)
            ):
                raise ManagedSessionError("embodiment_render_response_invalid")
            png = base64.b64decode(response.body["png_base64"], validate=True)
            _validate_bound_png(png, metadata)
            self._render_cache_key = cache_key
            self._render_cache_png = png
            return png
        except asyncio.CancelledError:
            raise
        except ManagedSessionError:
            raise
        except Exception as error:
            raise ManagedSessionError("embodiment_render_failed") from error

    @property
    def replay_bytes(self) -> bytes:
        return self._ledger.sealed_bytes

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_owned(), name=f"embodiment-close-{self._config.episode_id}"
            )
        await asyncio.shield(self._close_task)

    async def _close_owned(self) -> None:
        if self._closed:
            return
        self._closed = True
        socket = self._socket
        process = self._process
        try:
            if socket is not None and self._state_hash is not None:
                try:
                    await asyncio.wait_for(
                        socket.send("close_episode", boundary_hash=self._state_hash, body={}),
                        self._close_timeout_s,
                    )
                    await asyncio.wait_for(
                        socket.receive(
                            expected_message_type="episode_closed",
                            expected_boundary_hash=self._state_hash,
                        ),
                        self._close_timeout_s,
                    )
                except Exception:
                    pass
                await socket.close()
            elif not self._socket_future.done():
                self._socket_future.cancel()
        finally:
            self._render_cache_key = None
            self._render_cache_png = None
            if process is not None:
                await process.stop()

    def _validate_observations(self, value: object) -> None:
        if not isinstance(value, Mapping) or set(value) != set(self._config.participant_ids):
            raise ManagedSessionError("embodiment_session_observations_invalid")
        try:
            for observation in value.values():
                self._package.validate("observation", observation)
        except ProtocolValidationError as error:
            raise ManagedSessionError("embodiment_session_observations_invalid") from error

    def _require_started(self) -> None:
        if not self._started:
            raise ManagedSessionError("embodiment_session_not_started")

    def _require_active(self) -> None:
        self._require_started()
        if self._closed:
            raise ManagedSessionError("embodiment_session_closed")


def episode_config_as_dict(config: EpisodeConfig) -> Dict[str, Any]:
    return config.as_dict()


def _copy_json(value: Any) -> Any:
    return strict_json_loads(canonical_json_bytes(value))


def _validate_bound_png(png: bytes, metadata: Mapping[str, Any]) -> None:
    if (
        len(png) < 24
        or png[:8] != b"\x89PNG\r\n\x1a\n"
        or png[12:16] != b"IHDR"
        or int.from_bytes(png[16:20], "big") != 1280
        or int.from_bytes(png[20:24], "big") != 720
        or metadata.get("mime_type") != "image/png"
        or metadata.get("width") != 1280
        or metadata.get("height") != 720
        or hashlib.sha256(png).hexdigest() != metadata.get("sha256")
    ):
        raise ManagedSessionError("embodiment_render_integrity_invalid")


def _result_from_dict(value: Mapping[str, Any]) -> MultiParticipantStepResult:
    receipts = {
        participant_id: _receipt_from_dict(receipt)
        for participant_id, receipt in value["receipts"].items()
    }
    events = tuple(_event_from_dict(event) for event in value["public_events"])
    terminal_value = value["terminal"]
    terminal = TerminalState(
        terminal_value["ended"], terminal_value["outcome"], terminal_value["reason"]
    )
    include_trio_fields = set(value) == {
        "observations", "receipts", "public_events", "state_hash", "terminal", "placements",
        "trio_result",
    }
    placements = (
        tuple(_trio_placement_from_dict(group) for group in value["placements"])
        if include_trio_fields
        else ()
    )
    trio_result = (
        _trio_result_from_dict(value["trio_result"])
        if include_trio_fields and value["trio_result"] is not None
        else None
    )
    return MultiParticipantStepResult(
        observations=_copy_json(value["observations"]),
        receipts=receipts,
        public_events=events,
        state_hash=value["state_hash"],
        terminal=terminal,
        placements=placements,
        trio_result=trio_result,
        include_trio_fields=include_trio_fields,
    )


def _trio_placement_from_dict(value: Mapping[str, Any]) -> TrioPlacementGroup:
    return TrioPlacementGroup(
        value["place"], tuple(value["participant_ids"]), value["tie"], value["basis"]
    )


def _trio_result_from_dict(value: Mapping[str, Any]) -> TrioResult:
    return TrioResult(
        schema_version=value["schema_version"],
        task_id=value["task_id"],
        outcome=value["outcome"],
        reason=value["reason"],
        winner_id=value["winner_id"],
        placements=tuple(_trio_placement_from_dict(group) for group in value["placements"]),
        participant_outcomes={
            participant_id: TrioParticipantOutcome(
                participant_value["participant_id"], participant_value["outcome"],
                participant_value["place"], participant_value["tied"],
                participant_value["eliminated_tick"],
            )
            for participant_id, participant_value in value["participant_outcomes"].items()
        },
    )


def _receipt_from_dict(value: Mapping[str, Any]) -> ActionReceipt:
    return ActionReceipt(
        action_id=value["action_id"],
        observation_seq=value["observation_seq"],
        accepted=value["accepted"],
        start_tick=value["start_tick"],
        end_tick=value["end_tick"],
        applied_ticks=value["applied_ticks"],
        codes=tuple(value["codes"]),
        effects=tuple(
            ReceiptEffect(effect["kind"], effect["value"]) for effect in value["effects"]
        ),
        disposition=value["disposition"],
        fallback=value["fallback"],
        no_input_reason=value["no_input_reason"],
    )


def _event_from_dict(value: Mapping[str, Any]) -> AuthorityEvent:
    return AuthorityEvent(
        event_id=value["event_id"],
        tick=value["tick"],
        kind=value["kind"],
        summary=value["summary"],
        participant_ids=tuple(value["participant_ids"]),
        data=dict(value["data"]),
    )


__all__ = [
    "AsyncEnvironmentSession",
    "ManagedSessionError",
    "ManagedWorldArenaSession",
    "episode_config_as_dict",
]
