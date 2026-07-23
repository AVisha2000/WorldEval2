from __future__ import annotations

import json
from pathlib import Path

import pytest
from genesis_arena.embodiment.protocol import (
    EmbodimentProtocolPackage,
    canonical_json_bytes,
    canonical_sha256,
)
from genesis_arena.embodiment.protocol_registry import EmbodimentProtocolRegistry
from genesis_arena.embodiment.replay import ReplayLedger, ReplayValidationError, verify_replay_bytes

ROOT = Path(__file__).resolve().parents[2]
RTS_PROTOCOL_VERSION = "llm-controller/0.2.0"
RTS_EPISODE_ID = "ep_rts_task_plan_fixture"
RTS_PARTICIPANTS = ("participant_0", "participant_1")


def _observation() -> dict:
    corpus = json.loads(
        (ROOT / "game/embodiment_protocol/conformance/protocol-conformance.v1.json").read_text()
    )
    return next(
        case["instance"] for case in corpus["observation_cases"] if case["expected_schema_valid"]
    )


def _valid_replay() -> tuple[bytes, EmbodimentProtocolPackage]:
    package = EmbodimentProtocolPackage.from_repository(ROOT)
    observation = _observation()
    config = {
        "protocol_version": "llm-controller/0.1.0",
        "episode_id": "ep_conformance",
        "mode": "solo-curriculum-v0",
        "task_id": "orientation-v0",
        "seed": 1,
        "observation_profile": "text-visible-v1",
        "timing_track": "step-locked-v1",
        "maximum_episode_ticks": 100,
        "participant_ids": ["participant_0"],
    }
    state_hash = "b" * 64
    receipt = {
        "action_id": "no_input_0",
        "observation_seq": 0,
        "accepted": False,
        "disposition": "no_input",
        "fallback": "neutral",
        "no_input_reason": "missing",
        "start_tick": 0,
        "end_tick": 10,
        "applied_ticks": 10,
        "codes": [],
        "effects": [],
    }
    terminal = {"ended": True, "outcome": "success", "reason": "beacon_held"}
    terminal_observation = {
        **observation,
        "observation_seq": 1,
        "tick": 10,
        "previous_receipt": receipt,
        "terminal": terminal,
    }
    result = {
        "observations": {"participant_0": terminal_observation},
        "receipts": {"participant_0": receipt},
        "public_events": [],
        "state_hash": state_hash,
        "terminal": terminal,
    }
    window = {
        "episode_id": "ep_conformance",
        "observation_seq": 0,
        "mode": "solo-curriculum-v0",
        "start_tick": 0,
        "duration_ticks": 10,
        "decisions": {
            "participant_0": {
                "disposition": "no_input",
                "action": None,
                "fallback": "neutral",
                "no_input_reason": "missing",
            }
        },
    }
    ledger = ReplayLedger(
        config=config,
        config_sha256=canonical_sha256(config),
        protocol_package_sha256=package.package_sha256,
    )
    ledger.record_initial(observations={"participant_0": observation}, state_hash="a" * 64)
    ledger.record_step(decision_window=window, result=result)
    payload = ledger.seal(final_terminal=result["terminal"], final_state_hash=state_hash)
    return payload, package


def _reseal(value: dict) -> bytes:
    body = {key: child for key, child in value.items() if key != "ledger_sha256"}
    value["ledger_sha256"] = canonical_sha256(body)
    return canonical_json_bytes(value)


def _rts_observation(
    *,
    observation_seq: int,
    tick: int,
    previous_receipt: object,
    terminal: dict,
) -> dict:
    return {
        "protocol_version": RTS_PROTOCOL_VERSION,
        "episode_id": RTS_EPISODE_ID,
        "observation_seq": observation_seq,
        "tick": tick,
        "profile": "text-visible-v1",
        "goal": "Exercise deterministic RTS task-plan replay validation.",
        "remaining_ticks": 1200 - tick,
        "self": {
            "health_percent": 100,
            "energy_percent": 100,
            "facing": "east",
            "contact": "clear",
            "inventory": [],
            "status": [],
        },
        "visible_entities": [],
        "recent_events": [],
        "previous_receipt": previous_receipt,
        "memory": "",
        "terminal": terminal,
    }


def _rts_action(participant_id: str, observation_seq: int) -> dict:
    return {
        "protocol_version": RTS_PROTOCOL_VERSION,
        "episode_id": RTS_EPISODE_ID,
        "observation_seq": observation_seq,
        "action_id": f"fixture_{participant_id}_{observation_seq}",
        "control": {
            "move_x": 0,
            "move_y": 0,
            "look_x": 0,
            "look_y": 0,
            "duration_ticks": 10,
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
        "intent_label": "Deterministic RTS fixture action",
        "memory_update": "",
    }


def _rts_receipt(action_id: str, observation_seq: int) -> dict:
    start_tick = observation_seq * 10
    return {
        "action_id": action_id,
        "observation_seq": observation_seq,
        "accepted": True,
        "disposition": "accepted",
        "fallback": "none",
        "no_input_reason": None,
        "start_tick": start_tick,
        "end_tick": start_tick + 10,
        "applied_ticks": 10,
        "codes": [],
        "effects": [],
    }


def _rts_source_replay(registry: EmbodimentProtocolRegistry) -> bytes:
    """Build a tiny ordinary-controller RTS replay without runs/ or live dependencies."""

    package = registry.package(RTS_PROTOCOL_VERSION)
    config = {
        "protocol_version": RTS_PROTOCOL_VERSION,
        "episode_id": RTS_EPISODE_ID,
        "mode": "model-duel-v0",
        "task_id": "rts-skirmish-v0",
        "seed": 7,
        "observation_profile": "text-visible-v1",
        "timing_track": "step-locked-v1",
        "maximum_episode_ticks": 1200,
        "participant_ids": list(RTS_PARTICIPANTS),
    }
    initial_terminal = {"ended": False, "outcome": "running", "reason": "in_progress"}
    ledger = ReplayLedger(
        config=config,
        config_sha256=canonical_sha256(config),
        protocol_package_sha256=package.package_sha256,
    )
    ledger.record_initial(
        observations={
            participant_id: _rts_observation(
                observation_seq=0,
                tick=0,
                previous_receipt=None,
                terminal=initial_terminal,
            )
            for participant_id in RTS_PARTICIPANTS
        },
        state_hash="a" * 64,
    )

    final_terminal = initial_terminal
    final_state_hash = "a" * 64
    for observation_seq in range(2):
        start_tick = observation_seq * 10
        actions = {
            participant_id: _rts_action(participant_id, observation_seq)
            for participant_id in RTS_PARTICIPANTS
        }
        receipts = {
            participant_id: _rts_receipt(
                actions[participant_id]["action_id"], observation_seq
            )
            for participant_id in RTS_PARTICIPANTS
        }
        final_terminal = (
            initial_terminal
            if observation_seq == 0
            else {"ended": True, "outcome": "win", "reason": "fixture_complete"}
        )
        final_state_hash = ("b" if observation_seq == 0 else "c") * 64
        decision_window = {
            "episode_id": RTS_EPISODE_ID,
            "observation_seq": observation_seq,
            "mode": "model-duel-v0",
            "start_tick": start_tick,
            "duration_ticks": 10,
            "decisions": {
                participant_id: {
                    "disposition": "accepted",
                    "action": actions[participant_id],
                    "fallback": "none",
                    "no_input_reason": None,
                }
                for participant_id in RTS_PARTICIPANTS
            },
        }
        result = {
            "observations": {
                participant_id: _rts_observation(
                    observation_seq=observation_seq + 1,
                    tick=start_tick + 10,
                    previous_receipt=receipts[participant_id],
                    terminal=final_terminal,
                )
                for participant_id in RTS_PARTICIPANTS
            },
            "receipts": receipts,
            "public_events": [],
            "state_hash": final_state_hash,
            "terminal": final_terminal,
        }
        ledger.record_step(decision_window=decision_window, result=result)

    payload = ledger.seal(
        final_terminal=final_terminal,
        final_state_hash=final_state_hash,
    )
    verify_replay_bytes(payload, registry=registry)
    return payload


def _rts_replay_with_task_plan_evidence() -> bytes:
    """Make a schema-valid task-plan-evidence variant of a deterministic RTS replay.

    The generated source is deliberately an ordinary-controller ledger, so this fixture changes
    only the receipt action IDs required by the task executor audit. It lets Python verify the
    extension without a provider, Godot binary, ignored runs/ artifact, or temporary file.
    """

    registry = EmbodimentProtocolRegistry.from_repository(ROOT)
    value = json.loads(_rts_source_replay(registry))
    evidence: list[dict] = []
    for index, step in enumerate(value["steps"]):
        ordinary = step["decision_window"]
        plans = {
            "participant_0": {
                "protocol": "rts-task-plan-v1",
                "episode_id": ordinary["episode_id"],
                "observation_seq": index,
                "intent_label": "Harvest Blue Tree 0",
                "memory_update": "deterministic fixture",
                "assignments": [
                    {"unit_id": "blue_0", "task": "gather", "target_id": "blue_tree_0"}
                ],
            },
            "participant_1": {
                "protocol": "rts-task-plan-v1",
                "episode_id": ordinary["episode_id"],
                "observation_seq": index,
                "intent_label": "Harvest Red Tree 0",
                "memory_update": "deterministic fixture",
                "assignments": [
                    {"unit_id": "red_0", "task": "gather", "target_id": "red_tree_0"}
                ],
            },
        }
        evidence.append(
            {
                field: ordinary[field]
                for field in (
                    "episode_id",
                    "observation_seq",
                    "mode",
                    "start_tick",
                    "duration_ticks",
                )
            }
            | {"plans": plans}
        )
        for participant_id in RTS_PARTICIPANTS:
            action_id = f"task_plan_{participant_id}_{index}"
            step["result"]["receipts"][participant_id]["action_id"] = action_id
            step["result"]["observations"][participant_id]["previous_receipt"][
                "action_id"
            ] = action_id
    value["rts_task_plan_evidence"] = evidence
    return _reseal(value)


def test_replay_is_canonical_schema_valid_and_tamper_evident() -> None:
    payload, package = _valid_replay()
    verify_replay_bytes(payload, package=package)
    changed = json.loads(payload)
    changed["final_state_hash"] = "c" * 64
    with pytest.raises(ReplayValidationError, match="seal"):
        verify_replay_bytes(canonical_json_bytes(changed), package=package)


def test_replay_rejects_cross_episode_window_even_when_resealed() -> None:
    payload, _ = _valid_replay()
    changed = json.loads(payload)
    changed["steps"][0]["decision_window"]["episode_id"] = "ep_other"
    with pytest.raises(ReplayValidationError, match="decision boundary"):
        verify_replay_bytes(_reseal(changed))


def test_replay_rejects_participant_mismatch_even_when_resealed() -> None:
    payload, _ = _valid_replay()
    changed = json.loads(payload)
    changed["initial_observations"]["participant_other"] = changed["initial_observations"].pop(
        "participant_0"
    )
    with pytest.raises(ReplayValidationError, match="initial boundary"):
        verify_replay_bytes(_reseal(changed))


def test_replay_rejects_tick_gap_even_when_resealed() -> None:
    payload, _ = _valid_replay()
    changed = json.loads(payload)
    changed["steps"][0]["decision_window"]["start_tick"] = 1
    with pytest.raises(ReplayValidationError, match="decision boundary"):
        verify_replay_bytes(_reseal(changed))


def test_replay_rejects_receipt_mismatch_even_when_resealed() -> None:
    payload, _ = _valid_replay()
    changed = json.loads(payload)
    changed["steps"][0]["result"]["receipts"]["participant_0"]["observation_seq"] = 1
    changed["steps"][0]["result"]["observations"]["participant_0"]["previous_receipt"][
        "observation_seq"
    ] = 1
    with pytest.raises(ReplayValidationError, match="receipt boundary"):
        verify_replay_bytes(_reseal(changed))


def test_rts_task_plan_evidence_accepts_aligned_task_windows() -> None:
    verified = verify_replay_bytes(
        _rts_replay_with_task_plan_evidence(),
        registry=EmbodimentProtocolRegistry.from_repository(ROOT),
    )
    assert verified["config"]["task_id"] == "rts-skirmish-v0"
    assert len(verified["rts_task_plan_evidence"]) == len(verified["steps"])


def test_rts_task_plan_evidence_rejects_malformed_or_misaligned_windows() -> None:
    value = json.loads(_rts_replay_with_task_plan_evidence())
    value["rts_task_plan_evidence"][0]["plans"]["participant_0"]["assignments"][0][
        "coordinate"
    ] = 1
    with pytest.raises(ReplayValidationError, match="assignment fields"):
        verify_replay_bytes(
            _reseal(value), registry=EmbodimentProtocolRegistry.from_repository(ROOT)
        )

    value = json.loads(_rts_replay_with_task_plan_evidence())
    value["rts_task_plan_evidence"][1]["start_tick"] += 1
    with pytest.raises(ReplayValidationError, match="does not align"):
        verify_replay_bytes(
            _reseal(value), registry=EmbodimentProtocolRegistry.from_repository(ROOT)
        )


def test_rts_task_plan_evidence_is_rejected_for_non_rts_replay() -> None:
    payload, _ = _valid_replay()
    value = json.loads(payload)
    value["rts_task_plan_evidence"] = []
    with pytest.raises(ReplayValidationError, match="not permitted"):
        verify_replay_bytes(_reseal(value))
