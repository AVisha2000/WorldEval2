from __future__ import annotations

import pytest
from genesis_arena.embodiment.duo_games.rts_skirmish_v1 import (
    PLAN_PROTOCOL,
    RtsSkirmishV1Simulation,
    RtsTaskPlanProvider,
    RtsV1PlanError,
    validate_task_plan,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, strict_json_loads
from genesis_arena.embodiment.providers.contracts import (
    ProviderCallResult,
    ProviderRequest,
    ProviderTelemetry,
)


def _plan(episode_id: str, sequence: int, *assignments: tuple[str, str, str]) -> dict[str, object]:
    return {
        "protocol": PLAN_PROTOCOL,
        "episode_id": episode_id,
        "observation_seq": sequence,
        "intent_label": "visible RTS command",
        "memory_update": "",
        "assignments": [
            {"unit_id": unit_id, "task": task, "target_id": target_id}
            for unit_id, task, target_id in assignments
        ],
    }


def test_v1_plan_rejects_coordinate_and_enemy_resource_targets() -> None:
    valid = _plan("ep_rts_v1", 0, ("blue_0", "gather", "blue_tree_0"))
    assert validate_task_plan(
        valid, episode_id="ep_rts_v1", observation_seq=0, participant_id="participant_0"
    )
    invalid = _plan("ep_rts_v1", 0, ("blue_0", "gather", "red_tree_0"))
    invalid["position_mt"] = [1, 2]
    with pytest.raises(RtsV1PlanError):
        validate_task_plan(
            invalid, episode_id="ep_rts_v1", observation_seq=0, participant_id="participant_0"
        )


def test_v1_valid_plans_drive_economy_build_and_training() -> None:
    game = RtsSkirmishV1Simulation("ep_rts_v1")
    for target in ("blue_tree_0", "blue_tree_1", "blue_ore_0"):
        game.step(
            {
                "participant_0": _plan(
                    game.episode_id, game.observation_seq, ("blue_0", "gather", target)
                ),
                "participant_1": _plan(
                    game.episode_id, game.observation_seq, ("red_0", "hold", "hold_position")
                ),
            }
        )
        game.step(
            {
                "participant_0": _plan(
                    game.episode_id,
                    game.observation_seq,
                    ("blue_0", "return_material", "town_hall"),
                ),
                "participant_1": _plan(
                    game.episode_id, game.observation_seq, ("red_0", "hold", "hold_position")
                ),
            }
        )
    game.step(
        {
            "participant_0": _plan(
                game.episode_id, game.observation_seq, ("blue_0", "build", "barracks")
            ),
            "participant_1": _plan(
                game.episode_id, game.observation_seq, ("red_0", "hold", "hold_position")
            ),
        }
    )
    game.step(
        {
            "participant_0": _plan(
                game.episode_id, game.observation_seq, ("blue_0", "train", "barracks")
            ),
            "participant_1": _plan(
                game.episode_id, game.observation_seq, ("red_0", "hold", "hold_position")
            ),
        }
    )
    aggregate = game.authority_aggregates()["participants"]["participant_0"]
    assert aggregate["barracks_built"] == 1
    assert aggregate["units_trained"] == 1
    assert aggregate["deposits"] == 0  # construction spent the three deposited resources


def test_v1_different_valid_commands_produce_different_authoritative_outcomes() -> None:
    attacker = RtsSkirmishV1Simulation("ep_rts_v1")
    defender = RtsSkirmishV1Simulation("ep_rts_v1")
    for game, command in ((attacker, "bridge"), (defender, "hold_position")):
        for _ in range(6):
            game.step(
                {
                    "participant_0": _plan(
                        game.episode_id, game.observation_seq, ("blue_0", "hold", command)
                    ),
                    "participant_1": _plan(
                        game.episode_id, game.observation_seq, ("red_0", "hold", "hold_position")
                    ),
                }
            )
    assert (
        attacker.authority_aggregates()["participants"]["participant_0"]["central_hold_ticks"] == 60
    )
    assert (
        defender.authority_aggregates()["participants"]["participant_0"]["central_hold_ticks"] == 0
    )
    assert attacker.terminal["reason"] == "central_objective"


@pytest.mark.asyncio
async def test_live_provider_adapter_embeds_only_a_validated_task_plan() -> None:
    plan = _plan("ep_rts_v1", 0, ("v_blue_0", "gather", "v_blue_tree_0"))

    class FakeProvider:
        provider_name = "openai"

        async def request(self, _request: ProviderRequest) -> ProviderCallResult:
            return ProviderCallResult.success(
                canonical_json_bytes(plan), ProviderTelemetry(latency_ms=1)
            )

    adapter = RtsTaskPlanProvider(FakeProvider())
    request = ProviderRequest(
        episode_id="ep_rts_v1",
        participant_id="participant_0",
        observation_seq=0,
        deadline_monotonic_ns=1_000_000_000,
        model="gpt-5.6-sol",
        system_prompt="test",
        observation_json=canonical_json_bytes(
            {"episode_id": "ep_rts_v1", "observation_seq": 0, "profile": "text-visible-v1"}
        ),
        action_schema_json=canonical_json_bytes({"type": "object"}),
        scratchpad_utf8=b"",
        max_output_bytes=4096,
    )
    result = await adapter.request(request)
    assert result.raw_output is not None
    action = strict_json_loads(result.raw_output)
    assert action["control"]["duration_ticks"] == 10
    assert action["memory_update"].startswith("rts-task-plan-v2:")
    embedded = strict_json_loads(action["memory_update"].removeprefix("rts-task-plan-v2:").encode())
    assert embedded["assignments"] == [
        {"unit_id": "blue_0", "task": "gather", "target_id": "blue_tree_0"}
    ]
