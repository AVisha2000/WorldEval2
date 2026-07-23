from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from genesis_arena.embodiment.construction_task_provider import (
    TASK_PROMPT,
    ConstructionTaskProvider,
)
from genesis_arena.embodiment.contracts import (
    ActionReceipt,
    CapabilityStatus,
    EpisodeConfig,
    MultiParticipantStepResult,
    TerminalState,
)
from genesis_arena.embodiment.demo_provider import DemoPolicyLock, DemoProvider
from genesis_arena.embodiment.live_solo import LiveSoloError, LiveSoloRunner
from genesis_arena.embodiment.protocol import (
    EmbodimentProtocolPackage,
    canonical_json_bytes,
    canonical_sha256,
)
from genesis_arena.embodiment.providers.contracts import (
    InMemoryProviderAuditLog,
    ProviderAuditRecord,
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
)
from genesis_arena.embodiment.replay import ReplayLedger
from genesis_arena.embodiment.scripted_construction_demo import (
    ScriptedConstructionDemoProvider,
)

ROOT = Path(__file__).resolve().parents[2]


def _observation(episode_id: str, *, seq: int, tick: int, ended: bool) -> dict:
    terminal = (
        {"ended": True, "outcome": "failure", "reason": "time_limit"}
        if ended
        else {"ended": False, "outcome": "running", "reason": "running"}
    )
    return {
        "protocol_version": "llm-controller/0.1.0",
        "episode_id": episode_id,
        "observation_seq": seq,
        "tick": tick,
        "profile": "text-visible-v1",
        "goal": "Advance time safely.",
        "remaining_ticks": 10 if not ended else 0,
        "self": {
            "health_percent": 100,
            "energy_percent": 100,
            "facing": "north",
            "contact": "clear",
            "inventory": [],
            "status": [],
        },
        "visible_entities": [],
        "recent_events": [],
        "previous_receipt": None,
        "memory": "",
        "terminal": terminal,
    }


def _frame_png(sequence: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1280).to_bytes(4, "big")
        + (720).to_bytes(4, "big")
        + bytes((sequence,))
    )


def _hybrid_observation(episode_id: str, *, seq: int, tick: int, ended: bool, png: bytes) -> dict:
    value = _observation(episode_id, seq=seq, tick=tick, ended=ended)
    value["profile"] = "hybrid-visible-v1"
    value["frame"] = {
        "height": 720,
        "mime_type": "image/png",
        "sensor_id": "operator-follow-v1",
        "sha256": hashlib.sha256(png).hexdigest(),
        "transport_ref": f"frame:participant_0.{seq}",
        "width": 1280,
    }
    return value


class _InvalidProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.calls = 0
        self.audit_log = InMemoryProviderAuditLog()
        self.session_credential_canary = "canary-session-credential-8472"
        self.deadline_canary = None

    async def request(self, request):
        self.calls += 1
        self.deadline_canary = request.deadline_monotonic_ns
        result = ProviderCallResult.success(b'{"not":"an action"}', ProviderTelemetry(1))
        self.audit_log.record(
            ProviderAuditRecord(
                provider=self.provider_name,
                request=request,
                result=result,
                started_monotonic_ns=1_000_000,
                completed_monotonic_ns=2_000_000,
            )
        )
        return result


class _Session:
    def __init__(
        self,
        config: EpisodeConfig,
        package: EmbodimentProtocolPackage,
        frames: dict[int, bytes] | None = None,
    ) -> None:
        self.episode_id = config.episode_id
        self.window = None
        self.closed = False
        self._frames = frames
        self._initial = (
            _observation(self.episode_id, seq=0, tick=0, ended=False)
            if frames is None
            else _hybrid_observation(self.episode_id, seq=0, tick=0, ended=False, png=frames[0])
        )
        self._ledger = ReplayLedger(
            config.as_dict(), canonical_sha256(config.as_dict()), package.package_sha256
        )
        self.replay_bytes = None

    async def reset(self):
        observations = {"participant_0": self._initial}
        self._ledger.record_initial(observations=observations, state_hash="1" * 64)
        return observations

    async def state(self):
        return {"state_hash": "1" * 64}

    async def step(self, window):
        self.window = window
        decision = window.decisions["participant_0"]
        end_tick = window.start_tick + window.duration_ticks
        if decision.disposition == "accepted":
            assert decision.action is not None
            receipt = ActionReceipt(
                action_id=decision.action.action_id,
                observation_seq=window.observation_seq,
                accepted=True,
                start_tick=window.start_tick,
                end_tick=end_tick,
                applied_ticks=window.duration_ticks,
                codes=("accepted",),
            )
        else:
            receipt = ActionReceipt(
                action_id="no_input_participant_0_0",
                observation_seq=window.observation_seq,
                accepted=False,
                start_tick=window.start_tick,
                end_tick=end_tick,
                applied_ticks=window.duration_ticks,
                codes=("no_input",),
                disposition="no_input",
                fallback="neutral",
                no_input_reason=decision.no_input_reason or "invalid",
            )
        final_observation = (
            _observation(self.episode_id, seq=1, tick=end_tick, ended=True)
            if self._frames is None
            else _hybrid_observation(
                self.episode_id,
                seq=1,
                tick=end_tick,
                ended=True,
                png=self._frames[1],
            )
        )
        final_observation["previous_receipt"] = receipt.as_dict()
        result = MultiParticipantStepResult(
            observations={"participant_0": final_observation},
            receipts={"participant_0": receipt},
            public_events=(),
            state_hash="2" * 64,
            terminal=TerminalState(True, "failure", "time_limit"),
        )
        self._ledger.record_step(decision_window=window.as_dict(), result=result.as_dict())
        self.replay_bytes = self._ledger.seal(
            final_terminal=result.terminal.as_dict(), final_state_hash=result.state_hash
        )
        return result

    async def close(self):
        self.closed = True

    async def render(self, participant_id, sensor_id, transport_ref, observation_seq):
        assert participant_id == "participant_0"
        assert sensor_id == "operator-follow-v1"
        assert transport_ref == f"frame:participant_0.{observation_seq}"
        assert self._frames is not None
        return self._frames[observation_seq]


class _TaskPlanProvider:
    provider_name = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def request(self, request):
        self.calls += 1
        return ProviderCallResult.success(
            canonical_json_bytes(
                {
                    "protocol_version": "llm-controller/0.1.0",
                    "episode_id": request.episode_id,
                    "observation_seq": request.observation_seq,
                    "task_id": "wait",
                    "intent_label": "hold position",
                    "memory_update": "",
                }
            ),
            ProviderTelemetry(1),
        )


class _TamperedTaskAuditProvider(_TaskPlanProvider):
    provider_name = "demo"

    def __init__(self) -> None:
        super().__init__()
        self.audit_log = InMemoryProviderAuditLog()

    async def request(self, request):
        result = await super().request(request)
        self.audit_log.record(
            ProviderAuditRecord(
                provider=self.provider_name,
                request=replace(request, model="tampered-model"),
                result=result,
                started_monotonic_ns=1,
                completed_monotonic_ns=2,
            )
        )
        return result


class _CachedFrameTaskSession:
    """Small authority double with three executor ticks and one reused frame reference."""

    def __init__(self, config: EpisodeConfig, package: EmbodimentProtocolPackage) -> None:
        self.episode_id = config.episode_id
        self.closed = False
        self.render_calls: list[tuple[str, int]] = []
        self._index = 0
        self._shared_png = _frame_png(7)
        self._terminal_png = _frame_png(8)
        self._ledger = ReplayLedger(
            config.as_dict(), canonical_sha256(config.as_dict()), package.package_sha256
        )
        self.replay_bytes = None

    def _observation(self, *, seq: int, ended: bool, terminal: bool = False) -> dict:
        png = self._terminal_png if terminal else self._shared_png
        value = _hybrid_observation(
            self.episode_id,
            seq=seq,
            tick=seq,
            ended=ended,
            png=png,
        )
        value["frame"]["transport_ref"] = (
            "frame:participant_0.terminal" if terminal else "frame:participant_0.shared"
        )
        if ended:
            value["terminal"] = {
                "ended": True,
                "outcome": "success",
                "reason": "objective_complete",
            }
        value["visible_entities"] = [
            {
                "affordances": ["gather"],
                "bearing": "front",
                "distance": "touching",
                "id": "v_resource_1",
                "kind": "resource",
                "state": "available",
            }
        ]
        return value

    async def reset(self):
        observation = self._observation(seq=0, ended=False)
        observations = {"participant_0": observation}
        self._ledger.record_initial(observations=observations, state_hash="1" * 64)
        return observations

    async def state(self):
        return {"state_hash": "1" * 64}

    async def step(self, window):
        self._index += 1
        ended = self._index == 3
        observation = self._observation(seq=self._index, ended=ended, terminal=ended)
        action = window.decisions["participant_0"].action
        assert action is not None
        receipt = ActionReceipt(
            action_id=action.action_id,
            observation_seq=window.observation_seq,
            accepted=True,
            start_tick=window.start_tick,
            end_tick=window.start_tick + 1,
            applied_ticks=1,
            codes=("autonomous_task_complete",) if ended else ("autonomous_task_active",),
        )
        observation["previous_receipt"] = receipt.as_dict()
        terminal = TerminalState(
            ended,
            "success" if ended else "running",
            "objective_complete" if ended else "running",
        )
        result = MultiParticipantStepResult(
            observations={"participant_0": observation},
            receipts={"participant_0": receipt},
            public_events=(),
            state_hash=f"{self._index + 1:064x}",
            terminal=terminal,
        )
        self._ledger.record_step(decision_window=window.as_dict(), result=result.as_dict())
        if ended:
            self.replay_bytes = self._ledger.seal(
                final_terminal=terminal.as_dict(),
                final_state_hash=result.state_hash,
            )
        return result

    async def render(self, participant_id, sensor_id, transport_ref, observation_seq):
        assert participant_id == "participant_0"
        assert sensor_id == "operator-follow-v1"
        self.render_calls.append((transport_ref, observation_seq))
        if transport_ref == "frame:participant_0.shared":
            return self._shared_png
        assert transport_ref == "frame:participant_0.terminal"
        return self._terminal_png

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_invalid_provider_output_records_neutral_window_and_advances_time() -> None:
    config = EpisodeConfig(
        episode_id="ep_live_invalid",
        mode="solo-curriculum-v0",
        task_id="orientation-v0",
        seed=1,
        capability_status=CapabilityStatus(),
    )
    provider = _InvalidProvider()
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    session = _Session(config, package)
    runner = LiveSoloRunner(
        config=config,
        session=session,
        provider=provider,
        model="test-model",
        system_prompt="Return strict JSON.",
        protocol_package=package,
    )
    outcome = await runner.run()
    assert provider.calls == 1
    assert session.window.duration_ticks == 10
    decision = session.window.decisions["participant_0"]
    assert decision.disposition == "no_input"
    assert decision.no_input_reason == "invalid"
    assert outcome.provider_failures == 1
    assert outcome.final_state_hash == "2" * 64
    assert outcome.bundles is not None
    assert provider.audit_log.drain_episode(config.episode_id) == ()

    summary = json.loads(outcome.bundles.public.read("replay_summary"))
    frozen = summary["frozen_configuration"]
    assert frozen["config_sha256"] == canonical_sha256(config.as_dict())
    assert frozen["protocol_package_sha256"] == package.package_sha256
    assert frozen["provider_sha256"] == canonical_sha256({"provider": "openai"})
    assert frozen["model_sha256"] == canonical_sha256({"model": "test-model"})
    assert len(frozen["settings_sha256"]) == 64

    telemetry = json.loads(outcome.bundles.protected.read("telemetry"))
    evidence = telemetry[0]["provider_evidence"]
    assert evidence["provider"] == "openai"
    assert evidence["model"] == "test-model"
    assert evidence["max_input_bytes"] == 8_388_608
    assert evidence["max_output_bytes"] == 4_096
    assert evidence["deadline_budget_ms"] == 45_000
    assert len(evidence["schema_sha256"]) == 64
    assert len(evidence["request_material_sha256"]) == 64
    assert evidence["parsing_disposition"] == "rejected"
    assert evidence["adapter_audit"] == {
        "disposition": "output",
        "duration_ms": 1,
        "recorded": True,
    }
    assert evidence["visibility_frame_binding"]["bound"] is True
    assert evidence["visibility_frame_binding"]["frame_sha256"] is None
    assert len(evidence["visibility_frame_binding"]["observation_sha256"]) == 64

    sealed = outcome.bundles.public.bundle_bytes + outcome.bundles.protected.bundle_bytes
    assert provider.session_credential_canary.encode() not in sealed
    assert str(provider.deadline_canary).encode() not in sealed
    assert b"deadline_monotonic_ns" not in sealed
    assert b"started_monotonic_ns" not in sealed
    assert b"completed_monotonic_ns" not in sealed
    assert session.closed


@pytest.mark.asyncio
async def test_demo_run_integrity_binds_policy_lock_and_non_network_capabilities() -> None:
    config = EpisodeConfig(
        episode_id="ep_demo_evidence",
        mode="solo-curriculum-v0",
        task_id="orientation-v0",
        seed=17,
        capability_status=CapabilityStatus(),
    )
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    lock = DemoPolicyLock(
        scenario_id="orientation-v0",
        policy_id="evidence-demo-v1",
        fixture_sha256="a" * 64,
        seed=17,
        participant_id="participant_0",
        model="evidence-demo-v1",
        total_decision_budget=1,
    )
    provider = DemoProvider(lock, behavior=lambda _request, _lock, _index: b'{"invalid":true}')
    runner = LiveSoloRunner(
        config=config,
        session=_Session(config, package),
        provider=provider,
        model="evidence-demo-v1",
        system_prompt="Return strict JSON.",
        protocol_package=package,
    )

    outcome = await runner.run()

    assert outcome.bundles is not None
    summary = json.loads(outcome.bundles.public.read("replay_summary"))
    expected_settings = {
        "action_schema_sha256": hashlib.sha256(
            canonical_json_bytes(package.schema("controller-action"))
        ).hexdigest(),
        "certification_eligible": False,
        "demo_policy_lock": lock.as_dict(),
        "demo_policy_lock_sha256": lock.sha256,
        "fallback_duration_ticks": 10,
        "max_input_bytes": 8_388_608,
        "max_output_bytes": 4_096,
        "observation_profile": "text-visible-v1",
        "provider_capabilities": {
            "credential_required": False,
            "networked": False,
            "provider_name": "demo",
        },
        "provider_timeout_ms": 45_000,
        "run_class": "demo",
        "system_prompt_sha256": hashlib.sha256(b"Return strict JSON.").hexdigest(),
    }
    frozen = summary["frozen_configuration"]
    assert frozen["provider_sha256"] == canonical_sha256({"provider": "demo"})
    assert frozen["settings_sha256"] == canonical_sha256(expected_settings)
    protected = outcome.bundles.protected.bundle_bytes
    assert b"api_key" not in protected
    assert b"credential" not in protected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_kind",
    ("malformed", "stale", "oversized", "fake_timeout", "budget_exhausted"),
)
async def test_demo_failures_advance_one_recorded_neutral_window(
    fixture_kind: str,
) -> None:
    config = EpisodeConfig(
        episode_id=f"ep_demo_fallback_{fixture_kind}",
        mode="solo-curriculum-v0",
        task_id="orientation-v0",
        seed=23,
        capability_status=CapabilityStatus(),
    )
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    lock = DemoPolicyLock(
        scenario_id="orientation-v0",
        policy_id="failure-demo-v1",
        fixture_sha256="b" * 64,
        seed=23,
        participant_id="participant_0",
        model="failure-demo-v1",
        total_decision_budget=1,
    )

    def behavior(request, _lock, _index):
        if fixture_kind == "malformed":
            return b"{malformed"
        if fixture_kind == "oversized":
            return b"x" * (request.max_output_bytes + 1)
        if fixture_kind == "fake_timeout":
            return ProviderFailureKind.TIMEOUT
        return canonical_json_bytes(
            {
                "protocol_version": "llm-controller/0.1.0",
                "episode_id": request.episode_id,
                "observation_seq": request.observation_seq + 1,
                "action_id": "stale_demo_action",
                "control": {
                    "move_x": 0,
                    "move_y": 0,
                    "look_x": 0,
                    "look_y": 0,
                    "duration_ticks": 1,
                    "buttons": {
                        "interact": False,
                        "primary": False,
                        "guard": False,
                        "dash": False,
                        "ability_1": False,
                        "ability_2": False,
                        "cycle_item": False,
                        "cancel": False,
                    },
                },
                "intent_label": "stale fixture",
                "memory_update": "",
            }
        )

    provider = DemoProvider(lock, behavior=behavior)
    if fixture_kind == "budget_exhausted":
        observation = _observation(config.episode_id, seq=0, tick=0, ended=False)
        await provider.request(
            ProviderRequest(
                episode_id=config.episode_id,
                participant_id="participant_0",
                observation_seq=0,
                deadline_monotonic_ns=1,
                model="failure-demo-v1",
                system_prompt="Return strict JSON.",
                observation_json=canonical_json_bytes(observation),
                action_schema_json=canonical_json_bytes(package.schema("controller-action")),
            )
        )
        provider.audit_log.drain_episode(config.episode_id)

    outcome = await LiveSoloRunner(
        config=config,
        session=_Session(config, package),
        provider=provider,
        model="failure-demo-v1",
        system_prompt="Return strict JSON.",
        protocol_package=package,
    ).run()

    assert outcome.windows == 1
    assert outcome.provider_failures == 1
    assert outcome.bundles is not None
    receipts = json.loads(outcome.bundles.public.read("receipts"))
    assert receipts[0]["participants"]["participant_0"]["disposition"] == "no_input"
    assert receipts[0]["participants"]["participant_0"]["fallback"] == "neutral"
    assert receipts[0]["participants"]["participant_0"]["applied_ticks"] == 10


@pytest.mark.asyncio
async def test_hybrid_runner_publishes_player_frame_at_each_decision_boundary() -> None:
    config = EpisodeConfig(
        episode_id="ep_live_frames",
        mode="solo-curriculum-v0",
        task_id="orientation-v0",
        seed=2,
        observation_profile="hybrid-visible-v1",
        capability_status=CapabilityStatus(implemented_observation_profiles=("hybrid-visible-v1",)),
    )
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    frames = {0: _frame_png(0), 1: _frame_png(1)}
    session = _Session(config, package, frames)
    published: list[tuple[str, int, bytes]] = []

    async def publish(participant_id: str, observation_seq: int, png: bytes) -> None:
        published.append((participant_id, observation_seq, png))

    await LiveSoloRunner(
        config=config,
        session=session,
        provider=_InvalidProvider(),
        model="test-model",
        system_prompt="Return strict JSON.",
        protocol_package=package,
        frame_publisher=publish,
    ).run()

    assert published == [
        ("participant_0", 0, frames[0]),
        ("participant_0", 1, frames[1]),
    ]


@pytest.mark.asyncio
async def test_construction_continuations_reuse_frame_and_skip_duplicate_provider_evidence(
) -> None:
    config = EpisodeConfig(
        episode_id="ep_live_cached_task_frames",
        mode="solo-curriculum-v0",
        task_id="construction-v0",
        seed=3,
        observation_profile="hybrid-visible-v1",
        capability_status=CapabilityStatus(
            implemented_observation_profiles=("hybrid-visible-v1",),
            implemented_tasks=("construction-v0",),
        ),
    )
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    lock = DemoPolicyLock(
        scenario_id="construction-v0",
        policy_id="construction-demo-v1",
        fixture_sha256="c" * 64,
        seed=3,
        participant_id="participant_0",
        model="construction-demo-v1",
        total_decision_budget=4,
    )
    planner = DemoProvider(lock, delegate=ScriptedConstructionDemoProvider())
    session = _CachedFrameTaskSession(config, package)
    published: list[tuple[str, int, bytes]] = []

    async def publish(participant_id: str, observation_seq: int, png: bytes) -> None:
        published.append((participant_id, observation_seq, png))

    outcome = await LiveSoloRunner(
        config=config,
        session=session,
        provider=ConstructionTaskProvider(planner, package),
        model="construction-demo-v1",
        system_prompt="Return strict JSON.",
        protocol_package=package,
        frame_publisher=publish,
    ).run()

    assert outcome.bundles is not None
    assert planner.decision_count == 1
    assert session.render_calls == [
        ("frame:participant_0.shared", 0),
        ("frame:participant_0.terminal", 3),
    ]
    assert [item[:2] for item in published] == [
        ("participant_0", 0),
        ("participant_0", 3),
    ]
    frames = json.loads(outcome.bundles.protected.read("frames"))
    telemetry = json.loads(outcome.bundles.protected.read("telemetry"))
    prompts = json.loads(outcome.bundles.protected.read("prompts"))
    provider_outputs = json.loads(outcome.bundles.protected.read("provider_outputs"))
    assert [frame["observation_seq"] for frame in frames] == [0, 3]
    assert [record["observation_seq"] for record in telemetry] == [0]
    assert prompts == [{"observation_seq": 0, "prompt": TASK_PROMPT}]
    planned = json.loads(base64.b64decode(provider_outputs[0]["raw_output_base64"]))
    assert planned["task_id"] == "gather_materials"
    assert "control" not in planned
    evidence = telemetry[0]["provider_evidence"]
    assert evidence["adapter_audit"]["recorded"] is True
    assert evidence["schema_sha256"] == hashlib.sha256(
        canonical_json_bytes(package.schema("construction-task-plan"))
    ).hexdigest()
    assert session.closed


@pytest.mark.asyncio
async def test_construction_adapter_rejects_tampered_transformed_audit_request() -> None:
    config = EpisodeConfig(
        episode_id="ep_construction_tampered_audit",
        mode="solo-curriculum-v0",
        task_id="construction-v0",
        seed=9,
        observation_profile="hybrid-visible-v1",
        capability_status=CapabilityStatus(
            implemented_observation_profiles=("hybrid-visible-v1",),
            implemented_tasks=("construction-v0",),
        ),
    )
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    session = _CachedFrameTaskSession(config, package)
    provider = ConstructionTaskProvider(_TamperedTaskAuditProvider(), package)

    with pytest.raises(LiveSoloError, match="embodiment_provider_audit_mismatch"):
        await LiveSoloRunner(
            config=config,
            session=session,
            provider=provider,
            model="construction-demo-v1",
            system_prompt="Return strict JSON.",
            protocol_package=package,
        ).run()

    assert session.closed


@pytest.mark.asyncio
async def test_construction_evidence_preserves_invalid_planner_output_not_generated_control(
) -> None:
    config = EpisodeConfig(
        episode_id="ep_construction_invalid_plan_evidence",
        mode="solo-curriculum-v0",
        task_id="construction-v0",
        seed=10,
        observation_profile="hybrid-visible-v1",
        capability_status=CapabilityStatus(
            implemented_observation_profiles=("hybrid-visible-v1",),
            implemented_tasks=("construction-v0",),
        ),
    )
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    lock = DemoPolicyLock(
        scenario_id="construction-v0",
        policy_id="construction-demo-v1",
        fixture_sha256="d" * 64,
        seed=10,
        participant_id="participant_0",
        model="construction-demo-v1",
        total_decision_budget=1,
    )
    malformed = b'{"malformed_plan":true}'
    provider = ConstructionTaskProvider(
        DemoProvider(lock, behavior=lambda *_args: malformed), package
    )
    frames = {0: _frame_png(0), 1: _frame_png(1)}

    outcome = await LiveSoloRunner(
        config=config,
        session=_Session(config, package, frames),
        provider=provider,
        model="construction-demo-v1",
        system_prompt="Return strict JSON.",
        protocol_package=package,
    ).run()

    assert outcome.bundles is not None
    outputs = json.loads(outcome.bundles.protected.read("provider_outputs"))
    assert base64.b64decode(outputs[0]["raw_output_base64"]) == malformed
    telemetry = json.loads(outcome.bundles.protected.read("telemetry"))
    assert telemetry[0]["provider_evidence"]["parsing_disposition"] == "not_attempted"
