"""Deterministic visible-only Demo policies and safe evaluation for relay control."""

from __future__ import annotations

from typing import Any, Mapping

from ..demo_provider import DemoPolicyLock, DemoProvider
from ..providers.contracts import ProviderRequest
from .common import (
    DuoFixtureMode,
    DuoPolicySpec,
    build_demo_provider,
    direct_action,
    frozen_specs,
    interact_control,
    move_or_turn_toward,
    neutral_control,
    parse_visible_observation,
    participant_window_projection,
    select_visible_entity,
    validate_completion_tick,
    validate_match_outcomes,
    validate_two_participant_summaries,
)

RELAY_CONTROL_SCENARIO_ID = "duo-relay-control-v0"
RELAY_CONTROL_MODELS = frozen_specs(
    {
        "relay-controller-alpha-v1": DuoPolicySpec(
            RELAY_CONTROL_SCENARIO_ID,
            "relay-controller-alpha-visible-v1",
            "relay-controller-alpha-v1",
            "pressure",
        ),
        "relay-controller-bravo-v1": DuoPolicySpec(
            RELAY_CONTROL_SCENARIO_ID,
            "relay-controller-bravo-visible-v1",
            "relay-controller-bravo-v1",
            "guard",
        ),
    }
)

_RELAY_FIELDS = frozenset(
    ("completion_tick", "terminal_outcome", "terminal_reason", "participants")
)


def build_relay_control_demo_provider(
    *,
    model: str,
    participant_id: str,
    seed: int,
    decision_budget: int,
    fixture_mode: DuoFixtureMode = "valid",
) -> DemoProvider:
    try:
        spec = RELAY_CONTROL_MODELS[model]
    except KeyError as error:
        raise ValueError("unsupported relay-control Demo model") from error
    return build_demo_provider(
        spec=spec,
        participant_id=participant_id,
        seed=seed,
        decision_budget=decision_budget,
        behavior=_relay_control_behavior,
        fixture_mode=fixture_mode,
    )


def _relay_control_behavior(
    request: ProviderRequest, lock: DemoPolicyLock, call_index: int
) -> bytes:
    spec = RELAY_CONTROL_MODELS.get(request.model)
    if (
        spec is None
        or lock.scenario_id != RELAY_CONTROL_SCENARIO_ID
        or lock.policy_id != spec.policy_id
    ):
        raise ValueError("relay-control policy lock is incompatible")
    entities = parse_visible_observation(request)
    relay = select_visible_entity(
        entities,
        kinds=("relay",),
        required_affordance="capture",
        states=frozenset(("uncontrolled", "self_holding", "rival_holding")),
    )

    if relay is None:
        control = neutral_control()
        intent = "Demo: wait for a visible relay target"
    elif spec.variant == "guard" and relay["state"] != "self_holding":
        # The deterministic Demo matchup uses one pressure policy and one yielding guard
        # policy.  Without that asymmetry both mirrored operators reach the relay on the
        # same authority tick and contest it forever, which demonstrates no useful
        # control behavior.  Seat rotation still gives each participant one pressure leg.
        control = neutral_control()
        intent = "Demo: hold outside the relay to avoid a contest"
    elif relay["bearing"] != "front":
        control = move_or_turn_toward(relay)
        intent = "Demo: face the visible relay"
    elif relay["distance"] == "touching":
        control = interact_control()
        intent = "Demo: control the visible relay"
    else:
        control = move_or_turn_toward(relay)
        intent = "Demo: approach the visible relay"
    return direct_action(
        request,
        call_index=call_index,
        action_prefix="relay",
        control=control,
        intent=intent,
    )


def evaluate_relay_control(authority_aggregates: Mapping[str, Any]) -> dict[str, Any]:
    """Project match-wide control aggregates without private occupancy or transforms."""

    if not isinstance(authority_aggregates, Mapping) or set(authority_aggregates) != _RELAY_FIELDS:
        raise ValueError("relay-control authority aggregates have invalid fields")
    completion_tick = validate_completion_tick(authority_aggregates["completion_tick"])
    summaries = validate_two_participant_summaries(
        authority_aggregates["participants"], extra_fields=frozenset(("control_ticks",))
    )
    terminal_outcome = authority_aggregates["terminal_outcome"]
    terminal_reason = authority_aggregates["terminal_reason"]
    validate_match_outcomes(summaries, terminal_outcome)
    if not isinstance(terminal_reason, str) or terminal_reason not in {
        "hold_target",
        "time_limit",
        "simultaneous_terminal",
        "void",
    }:
        raise ValueError("relay-control terminal reason is invalid")
    if terminal_outcome == "win" and (terminal_reason != "hold_target" or completion_tick is None):
        raise ValueError("winning relay completion is inconsistent")
    if terminal_outcome == "draw" and terminal_reason not in {
        "time_limit",
        "simultaneous_terminal",
    }:
        raise ValueError("drawn relay completion is inconsistent")
    if terminal_outcome == "void" and terminal_reason != "void":
        raise ValueError("void relay completion is inconsistent")

    participants, symmetry = participant_window_projection(
        summaries, extra_fields=("control_ticks",)
    )
    first_control = summaries[0][1]["control_ticks"]
    second_control = summaries[1][1]["control_ticks"]
    symmetry.update(
        {
            "control_tick_delta": abs(first_control - second_control),
            "total_control_ticks": first_control + second_control,
        }
    )
    return {
        "schema_version": "duo-relay-control-evaluation/1",
        "scope": "duo_game",
        "task_id": RELAY_CONTROL_SCENARIO_ID,
        "completion": {
            "tick": completion_tick,
            "outcome": terminal_outcome,
            "reason": terminal_reason,
        },
        "participants": participants,
        "symmetry": symmetry,
    }


__all__ = [
    "RELAY_CONTROL_MODELS",
    "RELAY_CONTROL_SCENARIO_ID",
    "build_relay_control_demo_provider",
    "evaluate_relay_control",
]
