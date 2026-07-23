from __future__ import annotations

from pathlib import Path

import pytest
from genesis_arena.embodiment.protocol import ProtocolValidationError
from genesis_arena.embodiment.protocol_registry import EmbodimentProtocolRegistry

ROOT = Path(__file__).resolve().parents[2]
V1 = "llm-controller/0.1.0"
V2 = "llm-controller/0.2.0"
DUO_TASKS = (
    "duo-checkpoint-race-v0",
    "duo-relay-control-v0",
    "duo-spar-v0",
    "duo-resource-relay-v0",
    "rts-skirmish-v0",
)


@pytest.fixture(scope="module")
def registry() -> EmbodimentProtocolRegistry:
    return EmbodimentProtocolRegistry.from_repository(ROOT)


def config(task_id: str, *, mode: str = "model-duel-v0") -> dict[str, object]:
    return {
        "protocol_version": V2,
        "episode_id": "ep_duo_protocol_v2",
        "mode": mode,
        "task_id": task_id,
        "seed": 31,
        "observation_profile": "hybrid-visible-v1",
        "timing_track": "step-locked-v1",
        "maximum_episode_ticks": 1200,
        "participant_ids": ["participant_0", "participant_1"],
    }


@pytest.mark.parametrize("task_id", DUO_TASKS)
@pytest.mark.parametrize("mode", ("scripted-duel-v0", "model-duel-v0"))
def test_v2_accepts_exact_duo_game_contracts(
    registry: EmbodimentProtocolRegistry, task_id: str, mode: str
) -> None:
    registry.validate(V2, "episode-config", config(task_id, mode=mode))


@pytest.mark.parametrize("task_id", DUO_TASKS)
def test_duo_games_fail_closed_on_wrong_mode_seats_horizon_and_v1(
    registry: EmbodimentProtocolRegistry, task_id: str
) -> None:
    value = config(task_id)
    for invalid in (
        {**value, "mode": "solo-curriculum-v0"},
        {**value, "participant_ids": ["participant_0"]},
        {**value, "participant_ids": ["participant_1", "participant_0"]},
        {**value, "maximum_episode_ticks": 1199},
    ):
        with pytest.raises(ProtocolValidationError):
            registry.validate(V2, "episode-config", invalid)
    with pytest.raises(ProtocolValidationError):
        registry.validate(V1, "episode-config", {**value, "protocol_version": V1})


def test_duo_decision_window_requires_two_seats_and_fixed_ten_ticks(
    registry: EmbodimentProtocolRegistry,
) -> None:
    decision = {
        "disposition": "no_input",
        "action": None,
        "fallback": "neutral",
        "no_input_reason": "missing",
    }
    window = {
        "episode_id": "ep_duo_protocol_v2",
        "observation_seq": 0,
        "mode": "model-duel-v0",
        "start_tick": 0,
        "duration_ticks": 10,
        "decisions": {
            "participant_0": decision,
            "participant_1": decision,
        },
    }
    registry.validate(V2, "decision-window", window)
    with pytest.raises(ProtocolValidationError):
        registry.validate(V2, "decision-window", {**window, "duration_ticks": 9})
    with pytest.raises(ProtocolValidationError):
        registry.validate(
            V2,
            "decision-window",
            {**window, "decisions": {"participant_0": decision}},
        )


def test_manifest_and_capability_contract_claim_exact_integrated_duo_surface(
    registry: EmbodimentProtocolRegistry,
) -> None:
    package = registry.package(V2)
    package.verify_lock()
    manifest = package.manifest
    assert manifest["capabilities"] == {
        "implemented_modes": [
            "solo-curriculum-v0",
            "scripted-duel-v0",
            "model-duel-v0",
        ],
        "implemented_observation_profiles": [
            "text-visible-v1",
            "hybrid-visible-v1",
        ],
        "implemented_tasks": [
            "movement-maze-v0",
            "operator-action-course-v0",
            *DUO_TASKS,
        ],
        "certified_modes": [],
        "certified_observation_profiles": [],
        "scored_observation_profiles": [],
    }
    package.validate("capability-status", manifest["capabilities"])
    assert [item["id"] for item in manifest["curriculum"]][-len(DUO_TASKS) :] == list(DUO_TASKS)
    # The additive package must not move or rewrite the frozen 0.1 identity.
    assert registry.package(V1).package_sha256 == (
        "ddfc8998dfe33c0bb68aff31f78118a227792f4d568bd438d732c3d3abe0c34d"
    )


def test_duo_terminal_summary_is_flat_typed_and_rejects_hidden_geometry(
    registry: EmbodimentProtocolRegistry,
) -> None:
    summary = {
        "event_id": "evt_83_9",
        "tick": 83,
        "kind": "duo_participant_summary",
        "summary": "Duo participant summary.",
        "participant_ids": ["participant_0"],
        "data": {
            "task_id": "duo-relay-control-v0",
            "completion_tick": 83,
            "terminal_outcome": "win",
            "terminal_reason": "hold_target",
            "participant_id": "participant_0",
            "outcome": "win",
            "decision_windows": 9,
            "accepted_windows": 9,
            "fallback_windows": 0,
            "checkpoints_reached": 0,
            "control_ticks": 60,
            "hits_landed": 0,
            "hits_received": 0,
            "knockouts": 0,
        },
    }
    registry.validate(V2, "authority-event", summary)
    with pytest.raises(ProtocolValidationError):
        registry.validate(
            V2,
            "authority-event",
            {
                **summary,
                "data": {
                    **summary["data"],
                    "position_mt": {"x": 0, "y": 1200},
                },
            },
        )
