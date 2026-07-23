from __future__ import annotations

from pathlib import Path

import pytest
from genesis_arena.embodiment.contracts import ControllerState
from genesis_arena.embodiment.duo_games.relay_control import (
    build_relay_control_demo_provider,
    evaluate_relay_control,
)
from genesis_arena.embodiment.live_solo import parse_controller_action
from genesis_arena.embodiment.protocol import canonical_json_bytes
from genesis_arena.embodiment.protocol_registry import EmbodimentProtocolRegistry
from genesis_arena.embodiment.providers.contracts import ProviderFailureKind, ProviderRequest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = EmbodimentProtocolRegistry.from_repository(ROOT).package("llm-controller/0.2.0")


def _observation(
    *,
    reverse: bool = False,
    relay_state: str = "uncontrolled",
) -> dict[str, object]:
    entities = [
        {
            "id": "v_relay_public",
            "kind": "relay",
            "state": relay_state,
            "bearing": "front",
            "distance": "touching",
            "affordances": ["capture"],
        },
        {
            "id": "v_operator_public",
            "kind": "operator",
            "state": "ready",
            "bearing": "back_left",
            "distance": "far",
            "affordances": [],
        },
    ]
    if reverse:
        entities = [dict(reversed(tuple(entity.items()))) for entity in reversed(entities)]
    return {
        "protocol_version": "llm-controller/0.2.0",
        "episode_id": "ep_duo_relay",
        "observation_seq": 8,
        "profile": "text-visible-v1",
        "visible_entities": entities,
    }


def _request(observation: dict[str, object]) -> ProviderRequest:
    return ProviderRequest(
        episode_id="ep_duo_relay",
        participant_id="participant_0",
        observation_seq=8,
        deadline_monotonic_ns=1,
        model="relay-controller-alpha-v1",
        system_prompt="Use participant-visible relay semantics and return strict JSON.",
        observation_json=canonical_json_bytes(observation),
        action_schema_json=canonical_json_bytes(PACKAGE.schema("controller-action")),
    )


@pytest.mark.asyncio
async def test_relay_policy_is_repeatable_order_invariant_and_holds_ten_ticks() -> None:
    providers = [
        build_relay_control_demo_provider(
            model="relay-controller-alpha-v1",
            participant_id="participant_0",
            seed=19,
            decision_budget=1,
        )
        for _ in range(2)
    ]
    outputs = [
        await providers[0].request(_request(_observation())),
        await providers[1].request(_request(_observation(reverse=True))),
    ]

    assert providers[0].policy_lock == providers[1].policy_lock
    assert outputs[0].raw_output == outputs[1].raw_output
    action = parse_controller_action(outputs[0].raw_output or b"", package=PACKAGE)
    assert action.control.duration_ticks == 10
    assert action.control.buttons.interact is True
    assert "v_relay_public" not in (outputs[0].raw_output or b"").decode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relay_state",
    ("uncontrolled", "self_holding", "rival_holding"),
)
async def test_relay_pressure_policy_accepts_every_authority_relay_state(
    relay_state: str,
) -> None:
    provider = build_relay_control_demo_provider(
        model="relay-controller-alpha-v1",
        participant_id="participant_0",
        seed=19,
        decision_budget=1,
    )

    output = await provider.request(_request(_observation(relay_state=relay_state)))

    action = parse_controller_action(output.raw_output or b"", package=PACKAGE)
    assert action.control.buttons.interact is True


@pytest.mark.asyncio
async def test_relay_guard_policy_yields_before_entering_contested_space() -> None:
    request = _request(_observation(relay_state="rival_holding"))
    request = ProviderRequest(
        episode_id=request.episode_id,
        participant_id=request.participant_id,
        observation_seq=request.observation_seq,
        deadline_monotonic_ns=request.deadline_monotonic_ns,
        model="relay-controller-bravo-v1",
        system_prompt=request.system_prompt,
        observation_json=request.observation_json,
        action_schema_json=request.action_schema_json,
    )
    provider = build_relay_control_demo_provider(
        model="relay-controller-bravo-v1",
        participant_id="participant_0",
        seed=19,
        decision_budget=1,
    )

    output = await provider.request(request)

    action = parse_controller_action(output.raw_output or b"", package=PACKAGE)
    assert action.control == ControllerState.neutral(10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_mode", "failure"),
    (("timeout", ProviderFailureKind.TIMEOUT), ("refused", ProviderFailureKind.REFUSAL)),
)
async def test_relay_failure_fixtures_are_sanitized_and_budget_bound(
    fixture_mode: str, failure: ProviderFailureKind
) -> None:
    provider = build_relay_control_demo_provider(
        model="relay-controller-alpha-v1",
        participant_id="participant_0",
        seed=19,
        decision_budget=1,
        fixture_mode=fixture_mode,  # type: ignore[arg-type]
    )
    assert (await provider.request(_request(_observation()))).failure is failure
    assert (await provider.request(_request(_observation()))).failure is (
        ProviderFailureKind.INVALID_RESPONSE
    )


def _evaluation(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "completion_tick": 600,
        "terminal_outcome": "win",
        "terminal_reason": "hold_target",
        "participants": {
            "participant_0": {
                "outcome": "win",
                "decision_windows": 60,
                "fallback_windows": 1,
                "control_ticks": 310,
            },
            "participant_1": {
                "outcome": "loss",
                "decision_windows": 60,
                "fallback_windows": 3,
                "control_ticks": 170,
            },
        },
    }
    value.update(updates)
    return value


def test_relay_evaluation_is_safe_and_seat_symmetric() -> None:
    result = evaluate_relay_control(_evaluation())
    assert result["symmetry"] == {
        "decision_window_delta": 0,
        "fallback_window_delta": 2,
        "equal_decision_windows": True,
        "control_tick_delta": 140,
        "total_control_ticks": 480,
    }
    assert result["participants"]["participant_0"]["fallback_ratio_per_mille"] == 16
    serialized = repr(result).casefold()
    for forbidden in (
        "position",
        "occupancy",
        "spectator",
        "prompt",
        "raw_output",
        "credential",
        "opponent_observation",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "value",
    (
        _evaluation(hidden_state={}),
        _evaluation(completion_tick=True),
        _evaluation(terminal_reason="time_limit"),
        _evaluation(
            participants={
                "participant_0": {
                    "outcome": "win",
                    "decision_windows": 2,
                    "fallback_windows": 0,
                    "control_ticks": -1,
                },
                "participant_1": {
                    "outcome": "loss",
                    "decision_windows": 2,
                    "fallback_windows": 0,
                    "control_ticks": 1,
                },
            }
        ),
    ),
)
def test_relay_evaluation_rejects_non_allowlisted_or_inconsistent_values(value: object) -> None:
    with pytest.raises(ValueError):
        evaluate_relay_control(value)  # type: ignore[arg-type]
