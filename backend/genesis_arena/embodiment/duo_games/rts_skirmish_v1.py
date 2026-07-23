"""Pure-Python reference core for the action-driven live RTS authority.

The managed runtime is Godot, but this small deterministic mirror gives API and evaluator
tests a fast way to verify the important invariant: a validated task plan, rather than a
scripted timeline, changes the authority state.  It intentionally exposes aggregates only;
coordinates, plan text, and private memory remain replay-authority details.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from ..protocol import canonical_json_bytes, strict_json_loads
from ..providers.contracts import (
    ProviderAdapter,
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
)

TASK_ID = "rts-skirmish-v1"
PLAN_PROTOCOL = "rts-task-plan-v2"
PARTICIPANTS = ("participant_0", "participant_1")
TASKS = frozenset(
    {
        "gather",
        "return_material",
        "build",
        "train",
        "arm",
        "rally",
        "attack_unit",
        "attack_structure",
        "retreat",
        "hold",
    }
)
_PLAN_KEYS = frozenset(
    {"protocol", "episode_id", "observation_seq", "intent_label", "memory_update", "assignments"}
)
_ASSIGNMENT_KEYS = frozenset({"unit_id", "task", "target_id"})
_BUILD_COSTS = {"barracks": {"wood": 2, "ore": 1}, "tower": {"wood": 1, "ore": 1}}
_TASK_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "protocol",
        "episode_id",
        "observation_seq",
        "intent_label",
        "memory_update",
        "assignments",
    ],
    "properties": {
        "protocol": {"const": PLAN_PROTOCOL},
        "episode_id": {"type": "string"},
        "observation_seq": {"type": "integer", "minimum": 0},
        "intent_label": {"type": "string", "maxLength": 160},
        "memory_update": {"type": "string", "maxLength": 2048},
        "assignments": {"type": "array", "minItems": 1, "maxItems": 3},
    },
}
_TASK_PLAN_SCHEMA_JSON = canonical_json_bytes(_TASK_PLAN_SCHEMA)
_MEMORY_PREFIX = f"{PLAN_PROTOCOL}:"
_NEUTRAL_BUTTONS = {
    "interact": False,
    "primary": False,
    "guard": False,
    "dash": False,
    "ability_1": False,
    "ability_2": False,
    "cycle_item": False,
    "cancel": False,
}


class RtsV1PlanError(ValueError):
    """A plan did not describe a safe, visible, owned command."""


class RtsTaskPlanProvider:
    """Adapt a provider's strict RTS plan into the immutable v2 controller envelope.

    The scheduler remains responsible for concurrent dispatch, credentials, budgets, and audit
    records. This adapter only changes the provider-visible output schema and embeds a validated
    plan in the existing sealed decision transport. Invalid output is a sanitized neutral input.
    """

    def __init__(self, delegate: ProviderAdapter) -> None:
        if not isinstance(delegate, ProviderAdapter):
            raise TypeError("RTS task-plan delegate is invalid")
        self._delegate = delegate
        self.provider_name = delegate.provider_name

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        scratchpad = _task_memory(request.scratchpad_utf8)
        task_request = replace(
            request,
            system_prompt=(
                "Return exactly one rts-task-plan-v2 JSON object. Use only visible owned units "
                "and visible targets; do not emit controller actions or coordinates."
            ),
            action_schema_json=_TASK_PLAN_SCHEMA_JSON,
            scratchpad_utf8=scratchpad,
        )
        result = await self._delegate.request(task_request)
        if result.raw_output is None:
            return result
        try:
            raw_plan = strict_json_loads(result.raw_output)
            raw_plan = _authority_plan_ids(raw_plan)
            validate_task_plan(
                raw_plan,
                episode_id=request.episode_id,
                observation_seq=request.observation_seq,
                participant_id=request.participant_id,
            )
            encoded_plan = canonical_json_bytes(raw_plan)
            memory_update = _MEMORY_PREFIX + encoded_plan.decode("utf-8")
            if len(memory_update.encode("utf-8")) > 2048:
                raise RtsV1PlanError("task_plan_too_large")
            action = {
                "protocol_version": "llm-controller/0.2.0",
                "episode_id": request.episode_id,
                "observation_seq": request.observation_seq,
                "action_id": f"rts_v1_{request.participant_id}_{request.observation_seq}",
                "control": {
                    "move_x": 0,
                    "move_y": 0,
                    "look_x": 0,
                    "look_y": 0,
                    "duration_ticks": 10,
                    "buttons": _NEUTRAL_BUTTONS,
                },
                "intent_label": str(raw_plan["intent_label"]),
                "memory_update": memory_update,
            }
            return ProviderCallResult.success(canonical_json_bytes(action), result.telemetry)
        except (TypeError, ValueError, UnicodeDecodeError):
            return ProviderCallResult.failed(ProviderFailureKind.INVALID_RESPONSE, result.telemetry)

    async def aclose(self) -> None:
        close = getattr(self._delegate, "aclose", None)
        if callable(close):
            value = close()
            if hasattr(value, "__await__"):
                await value


def _task_memory(value: bytes) -> bytes:
    """Extract the participant's private RTS memory, never prior embedded plan text."""
    try:
        text = value.decode("utf-8")
        if not text.startswith(_MEMORY_PREFIX):
            return value
        plan = strict_json_loads(text[len(_MEMORY_PREFIX) :].encode("utf-8"))
        memory = plan.get("memory_update") if isinstance(plan, Mapping) else ""
        return memory.encode("utf-8") if isinstance(memory, str) else b""
    except (UnicodeDecodeError, ValueError):
        return b""


def _authority_plan_ids(value: object) -> object:
    """Translate only provider-visible semantic IDs back to stable authority IDs.

    The model must choose from the IDs in its frozen-v2 observation, all of which begin with
    ``v_``.  Godot task-plan evidence deliberately retains its older authority namespace.  This
    boundary translation never accepts coordinates or an ID that the ordinary validator would
    reject afterwards.
    """

    if not isinstance(value, Mapping):
        return value
    copied = dict(value)
    assignments = copied.get("assignments")
    if not isinstance(assignments, list):
        return copied
    translated: list[object] = []
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            translated.append(assignment)
            continue
        item = dict(assignment)
        for identifier_field in ("unit_id", "target_id"):
            identifier = item.get(identifier_field)
            if isinstance(identifier, str) and identifier.startswith("v_"):
                item[identifier_field] = identifier.removeprefix("v_")
        translated.append(item)
    copied["assignments"] = translated
    return copied


def unit_ids(participant_id: str) -> tuple[str, str, str]:
    prefix = "blue" if participant_id == "participant_0" else "red"
    return (f"{prefix}_0", f"{prefix}_1", f"{prefix}_2")


def visible_target_ids(participant_id: str) -> frozenset[str]:
    rival = "participant_1" if participant_id == "participant_0" else "participant_0"
    resources = {
        f"{unit_ids(participant_id)[0].split('_')[0]}_{kind}_{index}"
        for kind, count in (("tree", 4), ("ore", 3))
        for index in range(count)
    }
    return frozenset(
        {
            "town_hall",
            "barracks",
            "tower",
            "bridge",
            "hold_position",
            "enemy_town_hall",
            "enemy_tower",
            *resources,
            *unit_ids(participant_id),
            *unit_ids(rival),
        }
    )


def validate_task_plan(
    plan: object,
    *,
    episode_id: str,
    observation_seq: int,
    participant_id: str,
    alive_units: tuple[str, ...] | None = None,
) -> tuple[dict[str, object], ...]:
    """Validate the v1 plan without accepting coordinate or hidden-state targeting."""

    if not isinstance(plan, Mapping) or set(plan) != _PLAN_KEYS:
        raise RtsV1PlanError("task_plan_invalid")
    if (
        plan.get("protocol") != PLAN_PROTOCOL
        or plan.get("episode_id") != episode_id
        or type(plan.get("observation_seq")) is not int
        or plan["observation_seq"] != observation_seq
    ):
        raise RtsV1PlanError("task_plan_identity_invalid")
    if not isinstance(plan.get("intent_label"), str) or len(plan["intent_label"].encode()) > 160:
        raise RtsV1PlanError("task_plan_text_invalid")
    if not isinstance(plan.get("memory_update"), str) or len(plan["memory_update"].encode()) > 2048:
        raise RtsV1PlanError("task_plan_text_invalid")
    assignments = plan.get("assignments")
    if not isinstance(assignments, list) or not 1 <= len(assignments) <= 3:
        raise RtsV1PlanError("task_plan_assignment_count_invalid")
    owned = set(unit_ids(participant_id))
    alive = owned if alive_units is None else set(alive_units)
    visible = visible_target_ids(participant_id)
    seen: set[str] = set()
    validated: list[dict[str, object]] = []
    for assignment in assignments:
        if not isinstance(assignment, Mapping) or set(assignment) != _ASSIGNMENT_KEYS:
            raise RtsV1PlanError("task_plan_assignment_invalid")
        unit_id, task, target_id = (
            assignment.get("unit_id"),
            assignment.get("task"),
            assignment.get("target_id"),
        )
        if (
            not isinstance(unit_id, str)
            or unit_id not in owned
            or unit_id not in alive
            or unit_id in seen
        ):
            raise RtsV1PlanError("task_plan_unit_invalid")
        if not isinstance(task, str) or task not in TASKS:
            raise RtsV1PlanError("task_plan_task_invalid")
        if (
            not isinstance(target_id, str)
            or target_id not in visible
            or not _target_matches(participant_id, task, target_id)
        ):
            raise RtsV1PlanError("task_plan_target_invalid")
        seen.add(unit_id)
        validated.append({"unit_id": unit_id, "task": task, "target_id": target_id})
    return tuple(validated)


def _target_matches(participant_id: str, task: str, target_id: str) -> bool:
    prefix = unit_ids(participant_id)[0].split("_")[0]
    rival = "participant_1" if participant_id == "participant_0" else "participant_0"
    if task == "gather":
        return target_id.startswith(f"{prefix}_tree_") or target_id.startswith(f"{prefix}_ore_")
    if task in {"return_material", "retreat"}:
        return target_id == "town_hall"
    if task == "build":
        return target_id in _BUILD_COSTS
    if task in {"train", "arm"}:
        return target_id == "barracks"
    if task == "rally":
        return target_id == "bridge"
    if task == "hold":
        return target_id in {"bridge", "hold_position"}
    if task == "attack_unit":
        return target_id in unit_ids(rival)
    return target_id in {"enemy_town_hall", "enemy_tower"}


def evaluate_rts_skirmish_v1(authority_aggregates: Mapping[str, object]) -> dict[str, object]:
    """Reuse the aggregate-only RTS evaluation shape with the immutable v1 identity."""
    from .rts_skirmish import evaluate_rts_skirmish

    value = evaluate_rts_skirmish(authority_aggregates)
    value["schema_version"] = "rts-skirmish-evaluation/2"
    value["task_id"] = TASK_ID
    return value


@dataclass
class _Unit:
    unit_id: str
    team: str
    health: int = 1000
    alive: bool = True
    role: str = "worker"
    carrying: str = ""
    order: tuple[str, str] = ("hold", "hold_position")


@dataclass
class RtsSkirmishV1Simulation:
    """A deterministic, aggregate-only model used by Python tests and service adapters."""

    episode_id: str
    observation_seq: int = 0
    tick: int = 0
    terminal: dict[str, object] = field(
        default_factory=lambda: {"ended": False, "outcome": "running", "reason": "in_progress"}
    )
    winner_id: str | None = None
    units: dict[str, _Unit] = field(init=False)
    economy: dict[str, dict[str, int]] = field(init=False)
    structures: dict[str, dict[str, int | bool | str]] = field(init=False)
    trained: dict[str, int] = field(init=False)
    central_hold: dict[str, int] = field(init=False)
    last_task_plans: dict[str, tuple[dict[str, object], ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.units = {
            unit_id: _Unit(unit_id, participant_id)
            for participant_id in PARTICIPANTS
            for unit_id in unit_ids(participant_id)
        }
        self.economy = {participant_id: {"wood": 0, "ore": 0} for participant_id in PARTICIPANTS}
        self.structures = {
            participant_id: {
                "town_hall": 2000,
                "barracks": False,
                "tower": False,
                "tower_health": 1200,
            }
            for participant_id in PARTICIPANTS
        }
        self.trained = {participant_id: 0 for participant_id in PARTICIPANTS}
        self.central_hold = {participant_id: 0 for participant_id in PARTICIPANTS}

    def step(self, plans: Mapping[str, object], *, duration_ticks: int = 10) -> None:
        if self.terminal["ended"]:
            return
        accepted: dict[str, tuple[dict[str, object], ...]] = {}
        for participant_id in PARTICIPANTS:
            alive = tuple(
                unit_id for unit_id in unit_ids(participant_id) if self.units[unit_id].alive
            )
            try:
                accepted[participant_id] = validate_task_plan(
                    plans.get(participant_id),
                    episode_id=self.episode_id,
                    observation_seq=self.observation_seq,
                    participant_id=participant_id,
                    alive_units=alive,
                )
            except RtsV1PlanError:
                for unit_id in unit_ids(participant_id):
                    self.units[unit_id].order = ("hold", "hold_position")
            else:
                self.last_task_plans[participant_id] = accepted[participant_id]
                for command in accepted[participant_id]:
                    self.units[str(command["unit_id"])].order = (
                        str(command["task"]),
                        str(command["target_id"]),
                    )
        for _ in range(duration_ticks):
            self._tick()
            if self.terminal["ended"]:
                break
        self.tick += duration_ticks
        self.observation_seq += 1
        self._resolve_terminal()

    def _tick(self) -> None:
        pending_units: dict[str, int] = {}
        pending_structures: dict[tuple[str, str], int] = {}
        for participant_id in PARTICIPANTS:
            for unit_id in unit_ids(participant_id):
                unit = self.units[unit_id]
                if not unit.alive:
                    continue
                task, target = unit.order
                if task == "gather" and not unit.carrying:
                    unit.carrying = "wood" if "tree" in target else "ore"
                elif task == "return_material" and unit.carrying:
                    self.economy[participant_id][unit.carrying] += 1
                    unit.carrying = ""
                elif task == "build":
                    cost = _BUILD_COSTS[target]
                    if not self.structures[participant_id][target] and all(
                        self.economy[participant_id][kind] >= amount
                        for kind, amount in cost.items()
                    ):
                        for kind, amount in cost.items():
                            self.economy[participant_id][kind] -= amount
                        self.structures[participant_id][target] = True
                elif (
                    task in {"train", "arm"}
                    and self.structures[participant_id]["barracks"]
                    and unit.role == "worker"
                ):
                    unit.role = "militia"
                    self.trained[participant_id] += 1
                elif task == "rally":
                    unit.order = ("hold", "bridge")
                elif task == "hold" and target == "bridge":
                    self.central_hold[participant_id] += 1
                elif task == "attack_unit" and unit.role == "militia" and self.units[target].alive:
                    pending_units[target] = pending_units.get(target, 0) + 35
                elif task == "attack_structure" and unit.role == "militia":
                    rival = (
                        "participant_1" if participant_id == "participant_0" else "participant_0"
                    )
                    structure = "town_hall" if target == "enemy_town_hall" else "tower_health"
                    pending_structures[(rival, structure)] = (
                        pending_structures.get((rival, structure), 0) + 25
                    )
        for unit_id, damage in pending_units.items():
            unit = self.units[unit_id]
            unit.health = max(0, unit.health - damage)
            unit.alive = unit.health > 0
        for (participant_id, structure), damage in pending_structures.items():
            self.structures[participant_id][structure] = max(
                0, int(self.structures[participant_id][structure]) - damage
            )

    def _resolve_terminal(self) -> None:
        defeated = [
            participant_id
            for participant_id in PARTICIPANTS
            if int(self.structures[participant_id]["town_hall"]) == 0
        ]
        if len(defeated) == 1:
            self.winner_id = "participant_1" if defeated[0] == "participant_0" else "participant_0"
            self.terminal = {"ended": True, "outcome": "win", "reason": "town_hall_destroyed"}
        elif len(defeated) == 2:
            self.terminal = {
                "ended": True,
                "outcome": "draw",
                "reason": "simultaneous_town_hall_destroyed",
            }
        elif (
            max(self.central_hold.values()) >= 60
            and self.central_hold["participant_0"] != self.central_hold["participant_1"]
        ):
            self.winner_id = max(self.central_hold, key=self.central_hold.get)
            self.terminal = {"ended": True, "outcome": "win", "reason": "central_objective"}

    def authority_aggregates(self) -> dict[str, object]:
        participants: dict[str, dict[str, int]] = {}
        for participant_id in PARTICIPANTS:
            participants[participant_id] = {
                "materials_gathered": sum(self.economy[participant_id].values()),
                "deposits": sum(self.economy[participant_id].values()),
                "barracks_built": int(bool(self.structures[participant_id]["barracks"])),
                "towers_built": int(bool(self.structures[participant_id]["tower"])),
                "units_trained": self.trained[participant_id],
                "central_hold_ticks": self.central_hold[participant_id],
                "town_hall_health": int(self.structures[participant_id]["town_hall"]),
                "living_units": sum(
                    self.units[unit_id].alive for unit_id in unit_ids(participant_id)
                ),
            }
        return {
            "task_id": TASK_ID,
            "completion_tick": self.tick if self.terminal["ended"] else None,
            "terminal_outcome": self.terminal["outcome"],
            "terminal_reason": self.terminal["reason"],
            "winner_id": self.winner_id,
            "participants": participants,
        }


__all__ = [
    "RtsTaskPlanProvider",
    "PARTICIPANTS",
    "PLAN_PROTOCOL",
    "RtsSkirmishV1Simulation",
    "RtsV1PlanError",
    "TASK_ID",
    "TASKS",
    "evaluate_rts_skirmish_v1",
    "unit_ids",
    "validate_task_plan",
    "visible_target_ids",
]
