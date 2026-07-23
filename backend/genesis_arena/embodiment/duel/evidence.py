"""Canonical public/protected evidence for independently verified paired duels."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from ..artifacts import (
    PROTECTED_LAYER,
    PUBLIC_LAYER,
    EpisodeArtifact,
    EpisodeArtifactBundle,
    EpisodeArtifactError,
    verify_offline_replay,
)
from ..duo_games.catalog import CENTRAL_RELAY_TASK_ID, duo_game
from ..evaluation import evaluate_paired_duel_replays
from ..protocol import (
    EmbodimentProtocolPackage,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from ..providers.contracts import (
    ProviderAuditRecord,
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
)
from ..series import ModelLock, SeriesLock
from .contracts import (
    DuelLegPlan,
    DuelLegVerification,
    PairedDuelPlan,
    PairedDuelResult,
    SeatAssignment,
)

SERIES_EVIDENCE_SCHEMA_VERSION = "llm-controller/paired-duel-evidence/1.0.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class VerifiedLegMaterial:
    """Replay and provider records captured only after independent leg verification."""

    replay_bytes: bytes
    provider_audits: Tuple[ProviderAuditRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.replay_bytes, bytes):
            raise TypeError("replay_bytes must be immutable bytes")
        if not isinstance(self.provider_audits, tuple) or any(
            not isinstance(record, ProviderAuditRecord) for record in self.provider_audits
        ):
            raise TypeError("provider_audits must be a tuple of ProviderAuditRecord values")


@dataclass(frozen=True)
class DuelSeriesEvidenceBundle:
    """Canonical two-leg wrapper around standard episode artifact bundles."""

    layer: str
    series_id: str
    plan_sha256: str
    fairness_lock_sha256: str
    content_sha256: str
    bundle_bytes: bytes
    legs: Tuple[EpisodeArtifactBundle, EpisodeArtifactBundle]

    @classmethod
    def create(
        cls,
        *,
        layer: str,
        series_id: str,
        plan_sha256: str,
        fairness_lock_sha256: str,
        legs: Tuple[EpisodeArtifactBundle, EpisodeArtifactBundle],
    ) -> DuelSeriesEvidenceBundle:
        if layer not in (PUBLIC_LAYER, PROTECTED_LAYER):
            raise EpisodeArtifactError("paired evidence layer is invalid")
        if not isinstance(series_id, str) or not series_id:
            raise EpisodeArtifactError("paired evidence series_id is invalid")
        for name, value in (
            ("plan_sha256", plan_sha256),
            ("fairness_lock_sha256", fairness_lock_sha256),
        ):
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise EpisodeArtifactError(f"paired evidence {name} is invalid")
        if not isinstance(legs, tuple) or len(legs) != 2:
            raise EpisodeArtifactError("paired evidence requires exactly two leg bundles")
        if any(not isinstance(leg, EpisodeArtifactBundle) or leg.layer != layer for leg in legs):
            raise EpisodeArtifactError("paired evidence leg layers differ")
        body = {
            "fairness_lock_sha256": fairness_lock_sha256,
            "layer": layer,
            "legs": [strict_json_loads(leg.bundle_bytes) for leg in legs],
            "plan_sha256": plan_sha256,
            "schema_version": SERIES_EVIDENCE_SCHEMA_VERSION,
            "series_id": series_id,
        }
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        encoded = canonical_json_bytes({**body, "content_sha256": digest})
        return cls(layer, series_id, plan_sha256, fairness_lock_sha256, digest, encoded, legs)

    @classmethod
    def verify(cls, payload: bytes) -> DuelSeriesEvidenceBundle:
        try:
            value = strict_json_loads(payload)
        except Exception as error:
            raise EpisodeArtifactError("paired evidence JSON is invalid") from error
        if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
            raise EpisodeArtifactError("paired evidence is not canonical")
        if set(value) != {
            "content_sha256",
            "fairness_lock_sha256",
            "layer",
            "legs",
            "plan_sha256",
            "schema_version",
            "series_id",
        }:
            raise EpisodeArtifactError("paired evidence fields differ")
        body = {key: child for key, child in value.items() if key != "content_sha256"}
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        if (
            value["schema_version"] != SERIES_EVIDENCE_SCHEMA_VERSION
            or value["content_sha256"] != digest
            or not isinstance(value["legs"], list)
            or len(value["legs"]) != 2
        ):
            raise EpisodeArtifactError("paired evidence integrity differs")
        legs = tuple(
            EpisodeArtifactBundle.verify(canonical_json_bytes(child)) for child in value["legs"]
        )
        return cls.create(
            layer=value["layer"],
            series_id=value["series_id"],
            plan_sha256=value["plan_sha256"],
            fairness_lock_sha256=value["fairness_lock_sha256"],
            legs=(legs[0], legs[1]),
        )


@dataclass(frozen=True)
class PairedDuelEvidence:
    public: DuelSeriesEvidenceBundle
    protected: DuelSeriesEvidenceBundle

    def __post_init__(self) -> None:
        if self.public.layer != PUBLIC_LAYER or self.protected.layer != PROTECTED_LAYER:
            raise EpisodeArtifactError("paired evidence layers are invalid")
        public_identity = (
            self.public.series_id,
            self.public.plan_sha256,
            self.public.fairness_lock_sha256,
        )
        protected_identity = (
            self.protected.series_id,
            self.protected.plan_sha256,
            self.protected.fairness_lock_sha256,
        )
        if public_identity != protected_identity:
            raise EpisodeArtifactError("public and protected pair identities differ")


@dataclass(frozen=True)
class DuelSeriesExecution:
    """Service return value that cannot attach aggregate evidence to an invalid pair."""

    result: PairedDuelResult
    evidence: PairedDuelEvidence | None

    def __post_init__(self) -> None:
        if not isinstance(self.result, PairedDuelResult):
            raise TypeError("result must be PairedDuelResult")
        if self.result.status == "complete":
            if not isinstance(self.evidence, PairedDuelEvidence):
                raise ValueError("complete pairs require sealed two-leg evidence")
            if self.evidence.public.plan_sha256 != self.result.plan_sha256:
                raise ValueError("paired evidence belongs to a different result")
        elif self.evidence is not None:
            raise ValueError("invalid pairs must not expose aggregate evidence")


def build_paired_duel_evidence(
    *,
    plan: PairedDuelPlan,
    result: PairedDuelResult,
    materials: Tuple[VerifiedLegMaterial, VerifiedLegMaterial],
    protocol_package: EmbodimentProtocolPackage,
) -> PairedDuelEvidence:
    """Seal both leg layers only for a complete, matching, independently verified pair."""

    if not isinstance(plan, PairedDuelPlan) or not isinstance(result, PairedDuelResult):
        raise TypeError("paired evidence requires typed plan and result")
    if result.status != "complete" or result.rerun_required:
        raise EpisodeArtifactError("aggregate evidence requires a complete pair")
    if result.plan_sha256 != plan.plan_sha256:
        raise EpisodeArtifactError("paired result belongs to a different plan")
    if not isinstance(materials, tuple) or len(materials) != 2:
        raise EpisodeArtifactError("paired evidence requires exactly two verified materials")

    replays = []
    serialized_audits = []
    for index, material in enumerate(materials):
        leg_result = result.legs[index]
        replay = verify_offline_replay_bytes(material.replay_bytes, protocol_package)
        _verify_replay_identity(plan, leg_result, replay, material.replay_bytes)
        audits = _validated_leg_audits(plan, leg_result.plan, replay, material.provider_audits)
        replays.append(replay)
        serialized_audits.append(audits)

    participant_to_entrant = tuple(
        {
            assignment.participant_id: assignment.entrant_id
            for assignment in result.legs[index].plan.assignments
        }
        for index in (0, 1)
    )
    task_ids = tuple(replay["config"].get("task_id") for replay in replays)
    if task_ids[0] != task_ids[1]:
        raise EpisodeArtifactError("paired replay tasks differ")
    if task_ids[0] == CENTRAL_RELAY_TASK_ID:
        evaluations = evaluate_paired_duel_replays(
            replays=(replays[0], replays[1]),
            provider_audits=(materials[0].provider_audits, materials[1].provider_audits),
            participant_to_entrant=(participant_to_entrant[0], participant_to_entrant[1]),
            entrant_ids=(plan.entrants[0].entrant_id, plan.entrants[1].entrant_id),
            entrant_wins=result.entrant_wins,
            draws=result.draws,
            winner_entrant_id=result.winner_entrant_id,
            replay_verified=(True, True),
        )
    else:
        evaluations = (
            _evaluate_duo_game_replay(replays[0]),
            _evaluate_duo_game_replay(replays[1]),
        )

    public_legs = []
    protected_legs = []
    for index, material in enumerate(materials):
        leg_result = result.legs[index]
        replay = replays[index]
        checkpoints, receipts, events = _public_replay_projection(replay)
        summary = _leg_summary(plan, result, index, replay, evaluations[index])
        public_legs.append(
            EpisodeArtifactBundle.create(
                PUBLIC_LAYER,
                (
                    EpisodeArtifact.json("checkpoints", checkpoints),
                    EpisodeArtifact.json("evaluation", summary["evaluation"]),
                    EpisodeArtifact.json("public_events", events),
                    EpisodeArtifact.json("receipts", receipts),
                    EpisodeArtifact.json("replay_summary", summary),
                ),
            )
        )
        protected_legs.append(
            EpisodeArtifactBundle.create(
                PROTECTED_LAYER,
                (
                    EpisodeArtifact("authority_replay", "application/json", material.replay_bytes),
                    EpisodeArtifact.json(
                        "telemetry",
                        {
                            "call_settings": plan.settings.as_dict(),
                            "fairness_lock": plan.fairness_lock.as_dict(),
                            "fairness_lock_sha256": plan.fairness_lock.lock_sha256,
                            "leg_plan": leg_result.plan.as_dict(),
                            "leg_plan_sha256": leg_result.plan.plan_sha256,
                            "paired_plan_sha256": plan.plan_sha256,
                            "provider_audits": serialized_audits[index],
                            "series_id": plan.series_id,
                            "verification": _verification_dict(leg_result.verification),
                        },
                    ),
                ),
            )
        )
    public = DuelSeriesEvidenceBundle.create(
        layer=PUBLIC_LAYER,
        series_id=plan.series_id,
        plan_sha256=plan.plan_sha256,
        fairness_lock_sha256=plan.fairness_lock.lock_sha256,
        legs=(public_legs[0], public_legs[1]),
    )
    protected = DuelSeriesEvidenceBundle.create(
        layer=PROTECTED_LAYER,
        series_id=plan.series_id,
        plan_sha256=plan.plan_sha256,
        fairness_lock_sha256=plan.fairness_lock.lock_sha256,
        legs=(protected_legs[0], protected_legs[1]),
    )
    return PairedDuelEvidence(public, protected)


def verify_offline_paired_duel(
    protected_bundle: bytes,
    *,
    package: EmbodimentProtocolPackage,
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Verify both retained authority replays and all typed audit records offline."""

    pair = DuelSeriesEvidenceBundle.verify(protected_bundle)
    if pair.layer != PROTECTED_LAYER:
        raise EpisodeArtifactError("offline paired replay requires protected evidence")
    verified = []
    episode_ids = set()
    for leg_index, leg_bundle in enumerate(pair.legs):
        replay = verify_offline_replay(leg_bundle.bundle_bytes, package=package)
        metadata = strict_json_loads(leg_bundle.read("telemetry"))
        if not isinstance(metadata, dict) or set(metadata) != {
            "call_settings",
            "fairness_lock",
            "fairness_lock_sha256",
            "leg_plan",
            "leg_plan_sha256",
            "paired_plan_sha256",
            "provider_audits",
            "series_id",
            "verification",
        }:
            raise EpisodeArtifactError("protected leg metadata fields differ")
        lock = _typed_fairness_lock(metadata["fairness_lock"])
        leg_plan = _typed_leg_plan(metadata["leg_plan"])
        verification = _typed_verification(metadata["verification"])
        if (
            metadata["series_id"] != pair.series_id
            or metadata["paired_plan_sha256"] != pair.plan_sha256
            or metadata["fairness_lock_sha256"] != pair.fairness_lock_sha256
            or lock.lock_sha256 != pair.fairness_lock_sha256
            or leg_plan.plan_sha256 != metadata["leg_plan_sha256"]
            or leg_plan.leg_index != leg_index
            or leg_plan.episode_id != replay["config"].get("episode_id")
            or leg_plan.mode != replay["config"].get("mode")
            or leg_plan.mode != _mode_from_lock(lock)
            or leg_plan.fairness_lock_sha256 != lock.lock_sha256
            or verification.plan_sha256 != leg_plan.plan_sha256
            or verification.replay_sha256
            != hashlib.sha256(leg_bundle.read("authority_replay")).hexdigest()
            or verification.terminal_state_sha256 != replay["final_state_hash"]
            or not verification.complete
            or not verification.verified
            or verification.outcome == "void"
        ):
            raise EpisodeArtifactError("protected leg identity differs")
        _deserialize_audits(
            metadata["provider_audits"],
            leg_plan,
            lock,
            metadata["call_settings"],
            expected_windows=len(replay["steps"]),
        )
        episode_id = replay["config"]["episode_id"]
        if episode_id in episode_ids:
            raise EpisodeArtifactError("paired evidence repeats one episode")
        episode_ids.add(episode_id)
        verified.append(replay)
    return verified[0], verified[1]


def verify_offline_replay_bytes(
    replay_bytes: bytes, package: EmbodimentProtocolPackage
) -> Mapping[str, Any]:
    """Verify raw replay bytes through the same protected-bundle verifier contract."""

    bundle = EpisodeArtifactBundle.create(
        PROTECTED_LAYER,
        (EpisodeArtifact("authority_replay", "application/json", replay_bytes),),
    )
    return verify_offline_replay(bundle.bundle_bytes, package=package)


def _verify_replay_identity(plan, leg_result, replay, replay_bytes) -> None:
    verification = leg_result.verification
    if (
        replay["config"].get("episode_id") != leg_result.plan.episode_id
        or replay["config"].get("mode") != leg_result.plan.mode
        or hashlib.sha256(replay_bytes).hexdigest() != verification.replay_sha256
        or replay["final_state_hash"] != verification.terminal_state_sha256
        or verification.plan_sha256 != leg_result.plan.plan_sha256
        or leg_result.plan.fairness_lock_sha256 != plan.fairness_lock.lock_sha256
        or not verification.complete
        or not verification.verified
        or verification.outcome == "void"
    ):
        raise EpisodeArtifactError("verified replay identity differs from the leg result")


def _mode_from_lock(lock: SeriesLock) -> str:
    scripted = sum(entrant.provider == "scripted" for entrant in lock.entrants)
    if scripted > 1:
        raise EpisodeArtifactError("protected fairness lock has too many scripted entrants")
    return "scripted-duel-v0" if scripted == 1 else "model-duel-v0"


def _public_replay_projection(replay):
    first_terminal = next(iter(replay["initial_observations"].values()))["terminal"]
    checkpoints = [
        {
            "observation_seq": 0,
            "state_hash": replay["initial_state_hash"],
            "terminal": first_terminal,
        }
    ]
    receipts = []
    events = []
    for index, step in enumerate(replay["steps"]):
        result = step["result"]
        checkpoints.append(
            {
                "observation_seq": index + 1,
                "state_hash": result["state_hash"],
                "terminal": result["terminal"],
            }
        )
        receipts.append({"observation_seq": index, "participants": result["receipts"]})
        events.extend(result["public_events"])
    return checkpoints, receipts, events


def _leg_summary(plan, result, index, replay, evaluation):
    leg = result.legs[index]
    demo = all(entrant.provider == "demo" for entrant in plan.entrants)
    return {
        "call_settings": plan.settings.as_dict(),
        "certification": {
            "eligible": not demo,
            "reason": "demo_provider" if demo else None,
        },
        "episode_id": leg.plan.episode_id,
        "evaluation": evaluation,
        "fairness_lock": plan.fairness_lock.as_dict(),
        "fairness_lock_sha256": plan.fairness_lock.lock_sha256,
        "final_state_hash": replay["final_state_hash"],
        "leg_plan": leg.plan.as_dict(),
        "leg_plan_sha256": leg.plan.plan_sha256,
        "paired_plan_sha256": plan.plan_sha256,
        "pair_result": {
            "draws": result.draws,
            "entrant_wins": list(result.entrant_wins),
            "status": result.status,
            "winner_entrant_id": result.winner_entrant_id,
        },
        "series_id": plan.series_id,
        "terminal": replay["final_terminal"],
        "verification": _verification_dict(leg.verification),
    }


def _evaluate_duo_game_replay(replay: Mapping[str, Any]) -> Mapping[str, Any]:
    """Derive a strict browser-safe game evaluation from terminal typed public events."""

    config = replay.get("config")
    if not isinstance(config, Mapping) or not isinstance(config.get("task_id"), str):
        raise EpisodeArtifactError("duo replay task identity is invalid")
    task_id = config["task_id"]
    game = duo_game(task_id)
    if not game.is_managed_v2 or game.evaluator is None:
        raise EpisodeArtifactError("duo replay task is not an additive game")
    events = [
        event
        for step in replay.get("steps", [])
        for event in step.get("result", {}).get("public_events", [])
        if isinstance(event, Mapping)
    ]
    completed_kind = {
        "rts-skirmish-v0": "rts_skirmish_completed",
        "rts-skirmish-v1": "rts_skirmish_v1_completed",
    }.get(task_id, "duo_game_completed")
    completed = [event for event in events if event.get("kind") == completed_kind]
    expected_summary_kind = {
        "duo-resource-relay-v0": "duo_resource_relay_participant_summary",
        "rts-skirmish-v0": "rts_skirmish_participant_summary",
        "rts-skirmish-v1": "rts_skirmish_v1_participant_summary",
    }.get(task_id, "duo_participant_summary")
    summaries = [event for event in events if event.get("kind") == expected_summary_kind]
    if len(completed) != 1 or len(summaries) != 2:
        raise EpisodeArtifactError("duo replay terminal summaries are missing or duplicated")
    completion = completed[0].get("data")
    if not isinstance(completion, Mapping) or completion.get("task_id") != task_id:
        raise EpisodeArtifactError("duo replay completion summary differs")
    participants: dict[str, Mapping[str, Any]] = {}
    for event in summaries:
        data = event.get("data")
        ids = event.get("participant_ids")
        if (
            not isinstance(data, Mapping)
            or data.get("task_id") != task_id
            or not isinstance(ids, list)
            or len(ids) != 1
            or data.get("participant_id") != ids[0]
            or ids[0] in participants
        ):
            raise EpisodeArtifactError("duo replay participant summary differs")
        common = {
            "outcome": data.get("outcome"),
            "decision_windows": data.get("decision_windows"),
            "fallback_windows": data.get("fallback_windows"),
        }
        if task_id == "duo-checkpoint-race-v0":
            common["checkpoints_reached"] = data.get("checkpoints_reached")
        elif task_id == "duo-relay-control-v0":
            common["control_ticks"] = data.get("control_ticks")
        elif task_id == "duo-spar-v0":
            common.update(
                {
                    "hits_landed": data.get("hits_landed"),
                    "hits_received": data.get("hits_received"),
                    "knockouts": data.get("knockouts"),
                }
            )
        elif task_id == "duo-resource-relay-v0":
            common.update(
                {
                    field: data.get(field)
                    for field in (
                        "resources_gathered",
                        "deposits",
                        "objective_score",
                        "builds_completed",
                        "defend_ticks",
                        "hits_landed",
                        "hits_received",
                        "knockouts",
                        "resources_dropped",
                        "dash_uses",
                        "guard_ticks",
                    )
                }
            )
        elif task_id in {"rts-skirmish-v0", "rts-skirmish-v1"}:
            common.update(
                {
                    field: data.get(field)
                    for field in (
                        "materials_gathered",
                        "deposits",
                        "barracks_built",
                        "towers_built",
                        "units_trained",
                        "central_hold_ticks",
                        "town_hall_damage_dealt",
                        "town_hall_damage_received",
                        "hits_landed",
                        "hits_received",
                        "knockouts",
                    )
                }
            )
        participants[str(ids[0])] = common
    aggregates: dict[str, Any] = {
        "completion_tick": completion.get("completion_tick"),
        "terminal_outcome": completion.get("terminal_outcome"),
        "terminal_reason": completion.get("terminal_reason"),
        "participants": participants,
    }
    if task_id == "duo-checkpoint-race-v0":
        # Four ordered markers are frozen by the v2 checkpoint-race authority artifact.
        aggregates["checkpoint_total"] = 4
    elif task_id == "duo-resource-relay-v0":
        aggregates["objective_target"] = 300
    try:
        return game.evaluator(aggregates)
    except (TypeError, ValueError) as error:
        raise EpisodeArtifactError("duo replay authority aggregates are invalid") from error


def _verification_dict(verification):
    return {
        "complete": verification.complete,
        "outcome": verification.outcome,
        "plan_sha256": verification.plan_sha256,
        "replay_sha256": verification.replay_sha256,
        "terminal_state_sha256": verification.terminal_state_sha256,
        "verified": verification.verified,
        "winner_participant_id": verification.winner_participant_id,
    }


def _validated_leg_audits(plan, leg_plan, replay, records):
    assignments = {value.participant_id: value for value in leg_plan.assignments}
    entrants = {value.entrant_id: value for value in plan.entrants}
    expected = {
        (observation_seq, participant_id)
        for observation_seq in range(len(replay["steps"]))
        for participant_id in assignments
    }
    actual = {(record.request.observation_seq, record.request.participant_id) for record in records}
    if actual != expected or len(actual) != len(records):
        raise EpisodeArtifactError("provider audits do not cover every participant boundary")
    values = []
    for record in sorted(
        records, key=lambda value: (value.request.observation_seq, value.request.participant_id)
    ):
        assignment = assignments.get(record.request.participant_id)
        if assignment is None:
            raise EpisodeArtifactError("provider audit participant is not assigned")
        entrant = entrants[assignment.entrant_id]
        if (
            record.request.episode_id != leg_plan.episode_id
            or record.provider != entrant.provider
            or record.request.model != entrant.model
        ):
            raise EpisodeArtifactError("provider audit identity differs from the leg assignment")
        values.append(_serialize_audit(record, assignment.entrant_id))
    return values


def _serialize_audit(record: ProviderAuditRecord, entrant_id: str) -> Mapping[str, Any]:
    request = record.request
    result = record.result
    return {
        "completed_monotonic_ns": record.completed_monotonic_ns,
        "entrant_id": entrant_id,
        "provider": record.provider,
        "request": {
            "action_schema_json_base64": base64.b64encode(request.action_schema_json).decode(
                "ascii"
            ),
            "deadline_monotonic_ns": request.deadline_monotonic_ns,
            "episode_id": request.episode_id,
            "frame_png_base64": (
                None
                if request.frame_png is None
                else base64.b64encode(request.frame_png).decode("ascii")
            ),
            "max_input_bytes": request.max_input_bytes,
            "max_output_bytes": request.max_output_bytes,
            "model": request.model,
            "observation_json_base64": base64.b64encode(request.observation_json).decode("ascii"),
            "observation_seq": request.observation_seq,
            "participant_id": request.participant_id,
            "scratchpad_utf8_base64": base64.b64encode(request.scratchpad_utf8).decode("ascii"),
            "system_prompt": request.system_prompt,
        },
        "result": {
            "failure": None if result.failure is None else result.failure.value,
            "raw_output_base64": (
                None
                if result.raw_output is None
                else base64.b64encode(result.raw_output).decode("ascii")
            ),
            "telemetry": result.telemetry.as_dict(),
        },
        "started_monotonic_ns": record.started_monotonic_ns,
    }


def _deserialize_audits(
    values: Any,
    leg_plan: DuelLegPlan,
    lock: SeriesLock,
    call_settings: Any,
    *,
    expected_windows: int,
) -> None:
    if not isinstance(values, list):
        raise EpisodeArtifactError("provider audits are invalid")
    assignments = {value.participant_id: value.entrant_id for value in leg_plan.assignments}
    entrants = {value.entrant_id: value for value in lock.entrants}
    if not isinstance(call_settings, dict) or set(call_settings) != {
        "action_schema_sha256",
        "max_input_bytes",
        "max_output_bytes",
        "system_prompt_sha256",
        "timeout_ms",
    }:
        raise EpisodeArtifactError("protected call settings fields differ")
    if (
        call_settings["max_input_bytes"] != lock.max_input_bytes
        or call_settings["max_output_bytes"] != lock.max_output_bytes
        or call_settings["timeout_ms"] != lock.deadline_ms
    ):
        raise EpisodeArtifactError("protected call settings differ from the fairness lock")
    seen = set()
    for value in values:
        record, entrant_id = _deserialize_audit(value)
        key = (record.request.observation_seq, record.request.participant_id)
        entrant = entrants.get(entrant_id)
        request = record.request
        if (
            key in seen
            or assignments.get(request.participant_id) != entrant_id
            or entrant is None
            or request.episode_id != leg_plan.episode_id
            or record.provider != entrant.provider
            or request.model != entrant.model
            or request.max_input_bytes != lock.max_input_bytes
            or request.max_output_bytes != lock.max_output_bytes
            or request.max_input_bytes != call_settings["max_input_bytes"]
            or request.max_output_bytes != call_settings["max_output_bytes"]
            or canonical_sha256(strict_json_loads(request.action_schema_json))
            != call_settings["action_schema_sha256"]
            or canonical_sha256({"prompt": request.system_prompt})
            != call_settings["system_prompt_sha256"]
        ):
            raise EpisodeArtifactError("serialized provider audit assignment differs")
        seen.add(key)
    expected = {
        (observation_seq, participant_id)
        for observation_seq in range(expected_windows)
        for participant_id in assignments
    }
    if seen != expected:
        raise EpisodeArtifactError("serialized provider audits do not cover both participants")


def _typed_fairness_lock(value: Any) -> SeriesLock:
    if not isinstance(value, dict):
        raise EpisodeArtifactError("protected fairness lock is invalid")
    selected = dict(value)
    entrants = selected.get("entrants")
    if not isinstance(entrants, list) or len(entrants) != 2:
        raise EpisodeArtifactError("protected fairness entrants are invalid")
    try:
        selected["entrants"] = tuple(ModelLock(**entrant) for entrant in entrants)
        return SeriesLock(**selected)
    except Exception as error:
        raise EpisodeArtifactError("protected fairness lock is invalid") from error


def _typed_leg_plan(value: Any) -> DuelLegPlan:
    if not isinstance(value, dict):
        raise EpisodeArtifactError("protected leg plan is invalid")
    selected = dict(value)
    assignments = selected.get("assignments")
    if not isinstance(assignments, list) or len(assignments) != 2:
        raise EpisodeArtifactError("protected leg assignments are invalid")
    try:
        selected["assignments"] = tuple(SeatAssignment(**item) for item in assignments)
        return DuelLegPlan(**selected)
    except Exception as error:
        raise EpisodeArtifactError("protected leg plan is invalid") from error


def _typed_verification(value: Any) -> DuelLegVerification:
    if not isinstance(value, dict):
        raise EpisodeArtifactError("protected verification is invalid")
    try:
        return DuelLegVerification(**value)
    except Exception as error:
        raise EpisodeArtifactError("protected verification is invalid") from error


def _deserialize_audit(value: Any) -> tuple[ProviderAuditRecord, str]:
    if not isinstance(value, dict) or set(value) != {
        "completed_monotonic_ns",
        "entrant_id",
        "provider",
        "request",
        "result",
        "started_monotonic_ns",
    }:
        raise EpisodeArtifactError("serialized provider audit fields differ")
    request = value["request"]
    result = value["result"]
    if not isinstance(request, dict) or set(request) != {
        "action_schema_json_base64",
        "deadline_monotonic_ns",
        "episode_id",
        "frame_png_base64",
        "max_input_bytes",
        "max_output_bytes",
        "model",
        "observation_json_base64",
        "observation_seq",
        "participant_id",
        "scratchpad_utf8_base64",
        "system_prompt",
    }:
        raise EpisodeArtifactError("serialized provider request fields differ")
    if not isinstance(result, dict) or set(result) != {
        "failure",
        "raw_output_base64",
        "telemetry",
    }:
        raise EpisodeArtifactError("serialized provider result fields differ")
    telemetry = result["telemetry"]
    if not isinstance(telemetry, dict) or set(telemetry) != {
        "cache_write_tokens",
        "cached_input_tokens",
        "input_tokens",
        "latency_ms",
        "output_tokens",
        "request_id_sha256",
    }:
        raise EpisodeArtifactError("serialized provider telemetry fields differ")
    try:
        frame = (
            None
            if request["frame_png_base64"] is None
            else base64.b64decode(request["frame_png_base64"], validate=True)
        )
        provider_request = ProviderRequest(
            episode_id=request["episode_id"],
            participant_id=request["participant_id"],
            observation_seq=request["observation_seq"],
            deadline_monotonic_ns=request["deadline_monotonic_ns"],
            model=request["model"],
            system_prompt=request["system_prompt"],
            observation_json=base64.b64decode(request["observation_json_base64"], validate=True),
            action_schema_json=base64.b64decode(
                request["action_schema_json_base64"], validate=True
            ),
            scratchpad_utf8=base64.b64decode(request["scratchpad_utf8_base64"], validate=True),
            frame_png=frame,
            max_input_bytes=request["max_input_bytes"],
            max_output_bytes=request["max_output_bytes"],
        )
        provider_telemetry = ProviderTelemetry(**telemetry)
        failure = None if result["failure"] is None else ProviderFailureKind(result["failure"])
        raw_output = (
            None
            if result["raw_output_base64"] is None
            else base64.b64decode(result["raw_output_base64"], validate=True)
        )
        provider_result = ProviderCallResult(raw_output, failure, provider_telemetry)
        record = ProviderAuditRecord(
            provider=value["provider"],
            request=provider_request,
            result=provider_result,
            started_monotonic_ns=value["started_monotonic_ns"],
            completed_monotonic_ns=value["completed_monotonic_ns"],
        )
    except Exception as error:
        raise EpisodeArtifactError("serialized provider audit is invalid") from error
    if not isinstance(value["entrant_id"], str) or not value["entrant_id"]:
        raise EpisodeArtifactError("serialized provider audit entrant is invalid")
    return record, value["entrant_id"]


__all__ = [
    "DuelSeriesEvidenceBundle",
    "DuelSeriesExecution",
    "PairedDuelEvidence",
    "SERIES_EVIDENCE_SCHEMA_VERSION",
    "VerifiedLegMaterial",
    "build_paired_duel_evidence",
    "verify_offline_paired_duel",
]
