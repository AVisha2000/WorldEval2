"""Evidence-first, provider-free benchmark registry for WorldEval Lab.

The interactive Lab deliberately keeps exploratory runs separate from benchmark evidence.  This
module accepts only an already-verified public cartridge, binds it to a frozen recipe, stores a
small allow-listed aggregate, and derives comparable per-game leaderboards.  It never accepts or
persists a credential, prompt, observation, decision trace, raw provider material, or private
agent memory.

This is a product-facing registry/store rather than a second benchmark executor.  The larger
Labyrinth benchmark runner remains the authority for its schedule and raw protected evidence.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..live_labyrinth import (
    DEFAULT_VISION_RANGE_CELLS,
    LIVE_TASK_ID,
    MAX_CORRIDOR_COMMAND_CELLS,
    MAX_LIVE_PROVIDER_CALLS,
    labyrinth_protocol_prompt_sha256,
)
from ..maze_maps import default_maze_map_spec
from ..protocol import canonical_json_bytes, canonical_sha256, strict_json_loads
from .contracts import (
    LabContractError,
    RaceCartridge,
    RunContract,
    RunLifecycle,
    RunMode,
    RunState,
    assert_public_projection_safe,
)
from .games import GAME_CATALOG, GameCatalogError

BENCHMARK_RECIPE_SCHEMA_VERSION = "worldeval/lab-benchmark-recipe/1"
VERIFIED_BENCHMARK_RESULT_SCHEMA_VERSION = "worldeval/lab-verified-benchmark-result/1"
MODEL_PROFILE_SCHEMA_VERSION = "worldeval/lab-model-profile/1"
SEASON_PROGRESS_SCHEMA_VERSION = "worldeval/lab-benchmark-season-progress/1"
BENCHMARK_STORE_SCHEMA_VERSION = "worldeval/lab-benchmark-store/1"

_STORE_DIRECTORY = "lab-benchmarks"
_STORE_FILE = "store.json"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECIPE_FIELDS = frozenset(
    {
        "allowed_providers",
        "budget",
        "budget_sha256",
        "configuration",
        "configuration_sha256",
        "game_id",
        "game_version",
        "map_id",
        "map_sha256",
        "metric_ids",
        "minimum_samples",
        "mode",
        "participant_count",
        "prompt_sha256",
        "ranking_metric",
        "recipe_id",
        "runtime_version",
        "scenario_id",
        "schema_version",
        "seed_policy",
        "skill_mode",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "authority_result_sha256",
        "contract_sha256",
        "participants",
        "recipe_id",
        "recipe_sha256",
        "replay_projection_sha256",
        "run_id",
        "schema_version",
    }
)
_PARTICIPANT_RESULT_FIELDS = frozenset({"entrant_id", "metrics", "model_id", "provider"})
_PROGRESS_FIELDS = frozenset(
    {
        "api_calls",
        "completed_runs",
        "elapsed_ms",
        "estimated_cost_microunits",
        "failed_runs",
        "infrastructure_voids",
        "input_tokens",
        "output_tokens",
        "planned_runs",
        "queued_runs",
        "recipe_id",
        "running_runs",
        "schema_version",
        "verified_results",
    }
)
_STORE_FIELDS = frozenset(
    {
        "progress",
        "recipe_registry_sha256",
        "results",
        "schema_version",
    }
)
_CANDIDATE_RUN_FIELDS = frozenset(
    {
        "authority_available",
        "cartridge",
        "contract",
        "created_at_epoch_ms",
        "replay_available",
        "resume_supported",
        "run_id",
        "state",
        "video_available",
    }
)
_METRIC_IDS = (
    "budget_charged_calls",
    "completion_basis_points",
    "input_tokens",
    "invalid_action_rate_basis_points",
    "latency_ms",
    "output_tokens",
    "path_efficiency_basis_points",
    "recovery_rate_basis_points",
)
_BASIS_POINT_METRICS = frozenset(
    {
        "completion_basis_points",
        "invalid_action_rate_basis_points",
        "path_efficiency_basis_points",
        "recovery_rate_basis_points",
    }
)


class LabBenchmarkError(ValueError):
    """A recipe, public candidate, or benchmark artifact is invalid or incomparable."""


def _identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise LabBenchmarkError(f"{label} is invalid")
    return value


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise LabBenchmarkError(f"{label} is invalid")
    return value


def _nonnegative(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LabBenchmarkError(f"{label} is invalid")
    return value


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LabBenchmarkError(f"{label} fields differ")
    return value


def _canonical_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LabBenchmarkError(f"{label} is invalid")
    try:
        assert_public_projection_safe(value, path=label)
        copied = strict_json_loads(canonical_json_bytes(value))
    except (LabContractError, TypeError, ValueError) as error:
        raise LabBenchmarkError(f"{label} is not safe canonical public JSON") from error
    if not isinstance(copied, Mapping):  # pragma: no cover - canonical JSON preserves objects.
        raise LabBenchmarkError(f"{label} is invalid")
    return copied


def _safe_public_copy(value: object, *, label: str) -> dict[str, Any]:
    try:
        assert_public_projection_safe(value, path=label)
        copied = strict_json_loads(canonical_json_bytes(value))
    except (LabContractError, TypeError, ValueError) as error:
        raise LabBenchmarkError(f"{label} is not safe canonical public JSON") from error
    if not isinstance(copied, dict):  # pragma: no cover - canonical JSON preserves objects.
        raise LabBenchmarkError(f"{label} is invalid")
    return copied


@dataclass(frozen=True, init=False)
class BenchmarkRecipe:
    """One immutable, hash-bound condition under which results are comparable."""

    _canonical_body: bytes
    recipe_sha256: str

    @classmethod
    def create(
        cls,
        *,
        recipe_id: str,
        game_id: str,
        game_version: str,
        mode: RunMode | str,
        runtime_version: str,
        scenario_id: str | None,
        map_id: str | None,
        map_sha256: str | None,
        seed_policy: Mapping[str, object],
        budget: Mapping[str, object],
        configuration: Mapping[str, object],
        skill_mode: str,
        prompt_sha256: str,
        allowed_providers: Sequence[str],
        participant_count: int,
        metric_ids: Sequence[str],
        ranking_metric: str,
        minimum_samples: int = 3,
    ) -> BenchmarkRecipe:
        body = {
            "allowed_providers": list(allowed_providers),
            "budget": dict(budget),
            "budget_sha256": canonical_sha256(budget),
            "configuration": dict(configuration),
            "configuration_sha256": canonical_sha256(configuration),
            "game_id": game_id,
            "game_version": game_version,
            "map_id": map_id,
            "map_sha256": map_sha256,
            "metric_ids": list(metric_ids),
            "minimum_samples": minimum_samples,
            "mode": mode.value if isinstance(mode, RunMode) else mode,
            "participant_count": participant_count,
            "prompt_sha256": prompt_sha256,
            "ranking_metric": ranking_metric,
            "recipe_id": recipe_id,
            "runtime_version": runtime_version,
            "scenario_id": scenario_id,
            "schema_version": BENCHMARK_RECIPE_SCHEMA_VERSION,
            "seed_policy": dict(seed_policy),
            "skill_mode": skill_mode,
        }
        return cls._from_body(body)

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> BenchmarkRecipe:
        body = _validate_recipe_body(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical_json_bytes(body))
        object.__setattr__(instance, "recipe_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> BenchmarkRecipe:
        parsed = _exact_mapping(
            value,
            fields=_RECIPE_FIELDS | frozenset({"recipe_sha256"}),
            label="benchmark recipe",
        )
        recipe = cls._from_body({name: parsed[name] for name in _RECIPE_FIELDS})
        if parsed["recipe_sha256"] != recipe.recipe_sha256:
            raise LabBenchmarkError("benchmark recipe fingerprint differs")
        return recipe

    @property
    def recipe_id(self) -> str:
        return str(self._body()["recipe_id"])

    @property
    def game_id(self) -> str:
        return str(self._body()["game_id"])

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(self._body()["metric_ids"])

    @property
    def ranking_metric(self) -> str:
        return str(self._body()["ranking_metric"])

    @property
    def minimum_samples(self) -> int:
        return int(self._body()["minimum_samples"])

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "recipe_sha256": self.recipe_sha256}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def validate_candidate(self, contract: RunContract) -> tuple[Mapping[str, Any], ...]:
        """Return verified entrant bindings only if the complete frozen condition matches."""

        body = contract.as_dict()
        recipe = self._body()
        for field_name in (
            "game_id",
            "game_version",
            "mode",
            "runtime_version",
            "scenario_id",
            "map_id",
            "map_sha256",
            "skill_mode",
            "prompt_sha256",
        ):
            if body.get(field_name) != recipe[field_name]:
                raise LabBenchmarkError("candidate run does not match the benchmark recipe")
        for field_name in ("seed_policy", "budget", "configuration"):
            if canonical_json_bytes(body.get(field_name)) != canonical_json_bytes(
                recipe[field_name]
            ):
                raise LabBenchmarkError("candidate run does not match the benchmark recipe")
        if (
            body.get("configuration_sha256") != recipe["configuration_sha256"]
            or canonical_sha256(body.get("budget")) != recipe["budget_sha256"]
        ):
            raise LabBenchmarkError("candidate run does not match the benchmark recipe")
        entrants = body.get("entrants")
        if not isinstance(entrants, list) or len(entrants) != recipe["participant_count"]:
            raise LabBenchmarkError("candidate entrant count differs from benchmark recipe")
        allowed_providers = set(recipe["allowed_providers"])
        if any(
            not isinstance(entrant, Mapping) or entrant.get("provider") not in allowed_providers
            for entrant in entrants
        ):
            raise LabBenchmarkError("candidate provider differs from benchmark recipe")
        return tuple(entrants)

    def _body(self) -> Mapping[str, Any]:
        return strict_json_loads(self._canonical_body)


def _validate_recipe_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _exact_mapping(value, fields=_RECIPE_FIELDS, label="benchmark recipe")
    if parsed["schema_version"] != BENCHMARK_RECIPE_SCHEMA_VERSION:
        raise LabBenchmarkError("benchmark recipe schema is unsupported")
    try:
        game = GAME_CATALOG.game(_identifier(parsed["game_id"], label="recipe game id"))
    except GameCatalogError as error:
        raise LabBenchmarkError("benchmark recipe game is not registered") from error
    game_version = _identifier(parsed["game_version"], label="recipe game version")
    if game_version not in game.task_ids:
        raise LabBenchmarkError("benchmark recipe game version is not registered")
    try:
        mode = RunMode(parsed["mode"])
    except (TypeError, ValueError) as error:
        raise LabBenchmarkError("benchmark recipe mode is invalid") from error
    if mode is not RunMode.SEALED_BENCHMARK:
        raise LabBenchmarkError("benchmark recipe mode must be sealed_benchmark")
    recipe_id = _identifier(parsed["recipe_id"], label="recipe id")
    runtime_version = _identifier(parsed["runtime_version"], label="recipe runtime version")
    scenario_id = parsed["scenario_id"]
    if scenario_id is not None:
        scenario_id = _identifier(scenario_id, label="recipe scenario id")
    map_id = parsed["map_id"]
    if map_id is not None:
        map_id = _identifier(map_id, label="recipe map id")
    map_sha256 = parsed["map_sha256"]
    if map_sha256 is not None:
        map_sha256 = _sha256(map_sha256, label="recipe map hash")
    seed_policy = _canonical_mapping(parsed["seed_policy"], label="benchmark_recipe.seed_policy")
    budget = _canonical_mapping(parsed["budget"], label="benchmark_recipe.budget")
    configuration = _canonical_mapping(
        parsed["configuration"], label="benchmark_recipe.configuration"
    )
    if parsed["budget_sha256"] != canonical_sha256(budget):
        raise LabBenchmarkError("benchmark recipe budget fingerprint differs")
    if parsed["configuration_sha256"] != canonical_sha256(configuration):
        raise LabBenchmarkError("benchmark recipe configuration fingerprint differs")
    allowed_providers = parsed["allowed_providers"]
    if (
        not isinstance(allowed_providers, list)
        or not allowed_providers
        or allowed_providers != sorted(set(allowed_providers))
    ):
        raise LabBenchmarkError("benchmark recipe providers are invalid")
    normalized_providers = [
        _identifier(value, label="recipe provider") for value in allowed_providers
    ]
    participant_count = parsed["participant_count"]
    if (
        isinstance(participant_count, bool)
        or not isinstance(participant_count, int)
        or participant_count != game.participants.minimum
        or participant_count != game.participants.maximum
    ):
        raise LabBenchmarkError("benchmark recipe participant count is invalid")
    metric_ids = parsed["metric_ids"]
    if (
        not isinstance(metric_ids, list)
        or not metric_ids
        or metric_ids != sorted(set(metric_ids))
        or any(metric_id not in _METRIC_IDS for metric_id in metric_ids)
    ):
        raise LabBenchmarkError("benchmark recipe metrics are invalid")
    ranking_metric = parsed["ranking_metric"]
    if ranking_metric not in metric_ids:
        raise LabBenchmarkError("benchmark recipe ranking metric is invalid")
    minimum_samples = parsed["minimum_samples"]
    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, int)
        or not 1 <= minimum_samples <= 10_000
    ):
        raise LabBenchmarkError("benchmark recipe minimum samples is invalid")
    return {
        "allowed_providers": normalized_providers,
        "budget": dict(budget),
        "budget_sha256": _sha256(parsed["budget_sha256"], label="recipe budget hash"),
        "configuration": dict(configuration),
        "configuration_sha256": _sha256(
            parsed["configuration_sha256"], label="recipe configuration hash"
        ),
        "game_id": game.id,
        "game_version": game_version,
        "map_id": map_id,
        "map_sha256": map_sha256,
        "metric_ids": list(metric_ids),
        "minimum_samples": minimum_samples,
        "mode": mode.value,
        "participant_count": participant_count,
        "prompt_sha256": _sha256(parsed["prompt_sha256"], label="recipe prompt hash"),
        "ranking_metric": ranking_metric,
        "recipe_id": recipe_id,
        "runtime_version": runtime_version,
        "scenario_id": scenario_id,
        "schema_version": BENCHMARK_RECIPE_SCHEMA_VERSION,
        "seed_policy": dict(seed_policy),
        "skill_mode": _identifier(parsed["skill_mode"], label="recipe skill mode"),
    }


@dataclass(frozen=True)
class SeasonProgress:
    """Safe numeric season counters, intentionally without raw run evidence."""

    recipe_id: str
    planned_runs: int = 0
    queued_runs: int = 0
    running_runs: int = 0
    completed_runs: int = 0
    verified_results: int = 0
    failed_runs: int = 0
    infrastructure_voids: int = 0
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_microunits: int = 0
    elapsed_ms: int = 0
    schema_version: str = SEASON_PROGRESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEASON_PROGRESS_SCHEMA_VERSION:
            raise LabBenchmarkError("season progress schema is unsupported")
        _identifier(self.recipe_id, label="season progress recipe id")
        for name in _PROGRESS_FIELDS - {"recipe_id", "schema_version"}:
            _nonnegative(getattr(self, name), label=f"season progress {name}")

    def as_dict(self) -> dict[str, object]:
        return {
            "api_calls": self.api_calls,
            "completed_runs": self.completed_runs,
            "elapsed_ms": self.elapsed_ms,
            "estimated_cost_microunits": self.estimated_cost_microunits,
            "failed_runs": self.failed_runs,
            "infrastructure_voids": self.infrastructure_voids,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "planned_runs": self.planned_runs,
            "queued_runs": self.queued_runs,
            "recipe_id": self.recipe_id,
            "running_runs": self.running_runs,
            "schema_version": self.schema_version,
            "verified_results": self.verified_results,
        }

    @classmethod
    def from_dict(cls, value: object) -> SeasonProgress:
        parsed = _exact_mapping(value, fields=_PROGRESS_FIELDS, label="season progress")
        return cls(**dict(parsed))  # type: ignore[arg-type]


@dataclass(frozen=True, init=False)
class VerifiedBenchmarkResult:
    """Small verified aggregate derived from one already-verified public cartridge."""

    _canonical_body: bytes
    verified_result_sha256: str

    @classmethod
    def from_candidate(
        cls,
        *,
        recipe: BenchmarkRecipe,
        candidate_run: Mapping[str, object],
        participant_metrics: Sequence[Mapping[str, object]],
    ) -> VerifiedBenchmarkResult:
        contract, state, cartridge = _parse_verified_candidate(candidate_run)
        entrants = recipe.validate_candidate(contract)
        return cls._from_body(
            {
                "authority_result_sha256": state.as_dict()["result_sha256"],
                "contract_sha256": contract.contract_sha256,
                "participants": _bind_participant_metrics(
                    entrants=entrants,
                    participant_metrics=participant_metrics,
                    recipe=recipe,
                    budget=contract.as_dict()["budget"],
                ),
                "recipe_id": recipe.recipe_id,
                "recipe_sha256": recipe.recipe_sha256,
                "replay_projection_sha256": cartridge.public_projection.projection_sha256,
                "run_id": contract.run_id,
                "schema_version": VERIFIED_BENCHMARK_RESULT_SCHEMA_VERSION,
            }
        )

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> VerifiedBenchmarkResult:
        body = _validate_verified_result_body(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical_json_bytes(body))
        object.__setattr__(instance, "verified_result_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> VerifiedBenchmarkResult:
        parsed = _exact_mapping(
            value,
            fields=_RESULT_FIELDS | frozenset({"verified_result_sha256"}),
            label="verified benchmark result",
        )
        result = cls._from_body({name: parsed[name] for name in _RESULT_FIELDS})
        if parsed["verified_result_sha256"] != result.verified_result_sha256:
            raise LabBenchmarkError("verified benchmark result fingerprint differs")
        return result

    @property
    def deduplication_key(self) -> tuple[str, str]:
        body = self._body()
        return str(body["contract_sha256"]), str(body["authority_result_sha256"])

    @property
    def recipe_id(self) -> str:
        return str(self._body()["recipe_id"])

    @property
    def recipe_sha256(self) -> str:
        return str(self._body()["recipe_sha256"])

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "verified_result_sha256": self.verified_result_sha256}

    def _body(self) -> Mapping[str, Any]:
        return strict_json_loads(self._canonical_body)


def _parse_verified_candidate(
    candidate_run: Mapping[str, object],
) -> tuple[RunContract, RunState, RaceCartridge]:
    if not isinstance(candidate_run, Mapping) or not {"contract", "state", "cartridge"} <= set(
        candidate_run
    ):
        raise LabBenchmarkError("benchmark candidate run is invalid")
    if set(candidate_run) - _CANDIDATE_RUN_FIELDS:
        raise LabBenchmarkError("benchmark candidate run contains unsupported material")
    for key, value in candidate_run.items():
        if key not in {"contract", "state", "cartridge"}:
            _safe_public_copy({key: value}, label="benchmark_candidate.summary")
    try:
        contract = RunContract.from_dict(candidate_run["contract"])
        state = RunState.from_dict(candidate_run["state"])
        cartridge = RaceCartridge.from_dict(candidate_run["cartridge"])
    except (LabContractError, KeyError, TypeError, ValueError) as error:
        raise LabBenchmarkError("benchmark candidate cartridge is invalid") from error
    state_body = state.as_dict()
    result_sha256 = state_body.get("result_sha256")
    if (
        state.status is not RunLifecycle.VERIFIED
        or not isinstance(result_sha256, str)
        or state.contract_sha256 != contract.contract_sha256
        or cartridge.state != state
        or cartridge.public_projection.status is not RunLifecycle.VERIFIED
        or cartridge.public_projection.contract_sha256 != contract.contract_sha256
        or cartridge.public_projection.run_id != contract.run_id
    ):
        raise LabBenchmarkError("benchmark candidate is not a verified sealed result")
    if candidate_run.get("run_id", contract.run_id) != contract.run_id:
        raise LabBenchmarkError("benchmark candidate run identity differs")
    return contract, state, cartridge


def _bind_participant_metrics(
    *,
    entrants: Sequence[Mapping[str, Any]],
    participant_metrics: Sequence[Mapping[str, object]],
    recipe: BenchmarkRecipe,
    budget: object,
) -> list[dict[str, object]]:
    if not isinstance(participant_metrics, Sequence) or isinstance(
        participant_metrics, (str, bytes)
    ):
        raise LabBenchmarkError("benchmark participant metrics are invalid")
    if not isinstance(budget, Mapping):
        raise LabBenchmarkError("benchmark candidate budget is invalid")
    budget_limit = budget.get("participant_call_budget")
    if isinstance(budget_limit, bool) or not isinstance(budget_limit, int) or budget_limit < 1:
        raise LabBenchmarkError("benchmark candidate budget is invalid")
    incoming: dict[str, Mapping[str, object]] = {}
    for entry in participant_metrics:
        if not isinstance(entry, Mapping) or set(entry) != {"entrant_id", "metrics"}:
            raise LabBenchmarkError("benchmark participant metric fields differ")
        entrant_id = _identifier(entry.get("entrant_id"), label="benchmark entrant id")
        if entrant_id in incoming or not isinstance(entry.get("metrics"), Mapping):
            raise LabBenchmarkError("benchmark participant metrics are invalid")
        incoming[entrant_id] = entry
    if set(incoming) != {entrant["entrant_id"] for entrant in entrants}:
        raise LabBenchmarkError("benchmark participant metrics do not match the run entrants")
    bound: list[dict[str, object]] = []
    for entrant in entrants:
        entrant_id = str(entrant["entrant_id"])
        metric_values = incoming[entrant_id]["metrics"]
        if set(metric_values) != set(recipe.metric_ids):
            raise LabBenchmarkError("benchmark participant metric set differs from recipe")
        metrics: dict[str, int] = {}
        for metric_id in recipe.metric_ids:
            metric_value = _nonnegative(metric_values[metric_id], label=f"metric {metric_id}")
            if metric_id in _BASIS_POINT_METRICS and metric_value > 10_000:
                raise LabBenchmarkError("benchmark basis-point metric is invalid")
            if metric_id == "budget_charged_calls" and metric_value > budget_limit:
                raise LabBenchmarkError("benchmark call metric exceeds the frozen budget")
            metrics[metric_id] = metric_value
        bound.append(
            {
                "entrant_id": entrant_id,
                "metrics": metrics,
                "model_id": _identifier(entrant["model_id"], label="benchmark model id"),
                "provider": _identifier(entrant["provider"], label="benchmark provider"),
            }
        )
    return bound


def _validate_verified_result_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _exact_mapping(value, fields=_RESULT_FIELDS, label="verified benchmark result")
    if parsed["schema_version"] != VERIFIED_BENCHMARK_RESULT_SCHEMA_VERSION:
        raise LabBenchmarkError("verified benchmark result schema is unsupported")
    participants = parsed["participants"]
    if not isinstance(participants, list) or not participants:
        raise LabBenchmarkError("verified benchmark participants are invalid")
    normalized_participants: list[dict[str, object]] = []
    previous_entrant_id = ""
    for participant in participants:
        entry = _exact_mapping(
            participant, fields=_PARTICIPANT_RESULT_FIELDS, label="verified benchmark participant"
        )
        entrant_id = _identifier(entry["entrant_id"], label="benchmark entrant id")
        if entrant_id <= previous_entrant_id:
            raise LabBenchmarkError("verified benchmark participants are not sorted")
        previous_entrant_id = entrant_id
        metrics = entry["metrics"]
        if not isinstance(metrics, Mapping) or not metrics:
            raise LabBenchmarkError("verified benchmark participant metrics are invalid")
        normalized_metrics = {
            _identifier(metric_id, label="benchmark metric id"): _nonnegative(
                metric_value, label="benchmark metric"
            )
            for metric_id, metric_value in metrics.items()
        }
        if any(
            metric_id in _BASIS_POINT_METRICS and value > 10_000
            for metric_id, value in normalized_metrics.items()
        ):
            raise LabBenchmarkError("verified benchmark basis-point metric is invalid")
        normalized_participants.append(
            {
                "entrant_id": entrant_id,
                "metrics": dict(sorted(normalized_metrics.items())),
                "model_id": _identifier(entry["model_id"], label="benchmark model id"),
                "provider": _identifier(entry["provider"], label="benchmark provider"),
            }
        )
    body = {
        "authority_result_sha256": _sha256(
            parsed["authority_result_sha256"], label="authority result hash"
        ),
        "contract_sha256": _sha256(parsed["contract_sha256"], label="contract hash"),
        "participants": normalized_participants,
        "recipe_id": _identifier(parsed["recipe_id"], label="recipe id"),
        "recipe_sha256": _sha256(parsed["recipe_sha256"], label="recipe hash"),
        "replay_projection_sha256": _sha256(
            parsed["replay_projection_sha256"], label="replay projection hash"
        ),
        "run_id": _identifier(parsed["run_id"], label="run id"),
        "schema_version": VERIFIED_BENCHMARK_RESULT_SCHEMA_VERSION,
    }
    _safe_public_copy(body, label="verified_benchmark_result")
    return body


def _validate_result_against_recipe(
    result: VerifiedBenchmarkResult, recipe: BenchmarkRecipe
) -> None:
    """Reject stored aggregates that could not have come from this frozen recipe.

    ``VerifiedBenchmarkResult`` deliberately excludes the full run contract, map, replay, and
    provider material.  This second, narrow validation keeps the persisted projection bound to
    the public dimensions which remain available after that redaction.
    """

    body = result.as_dict()
    if body["recipe_id"] != recipe.recipe_id or body["recipe_sha256"] != recipe.recipe_sha256:
        raise LabBenchmarkError("benchmark result recipe binding differs")
    participants = body["participants"]
    if (
        not isinstance(participants, list)
        or len(participants) != recipe._body()["participant_count"]
    ):
        raise LabBenchmarkError("benchmark result participant count differs from recipe")
    budget = recipe._body()["budget"]
    if not isinstance(budget, Mapping):  # Validated while constructing the recipe.
        raise LabBenchmarkError("benchmark recipe budget is invalid")
    call_budget = budget.get("participant_call_budget")
    if isinstance(call_budget, bool) or not isinstance(call_budget, int) or call_budget < 1:
        raise LabBenchmarkError("benchmark recipe budget is invalid")
    allowed_providers = set(recipe._body()["allowed_providers"])
    expected_metric_ids = set(recipe.metric_ids)
    entrant_ids: set[str] = set()
    for participant in participants:
        if not isinstance(participant, Mapping):  # Validated while constructing the result.
            raise LabBenchmarkError("benchmark result participant is invalid")
        entrant_id = participant.get("entrant_id")
        provider = participant.get("provider")
        metrics = participant.get("metrics")
        if (
            not isinstance(entrant_id, str)
            or entrant_id in entrant_ids
            or provider not in allowed_providers
            or not isinstance(metrics, Mapping)
            or set(metrics) != expected_metric_ids
        ):
            raise LabBenchmarkError("benchmark result does not match the recipe")
        entrant_ids.add(entrant_id)
        calls = metrics.get("budget_charged_calls")
        if isinstance(calls, bool) or not isinstance(calls, int) or calls > call_budget:
            raise LabBenchmarkError("benchmark result call metric exceeds the frozen budget")


@dataclass(frozen=True)
class ModelProfile:
    """Cross-game evidence summary with no synthetic universal model score."""

    model_id: str
    provider: str
    games: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        _identifier(self.model_id, label="model profile model id")
        _identifier(self.provider, label="model profile provider")
        if not isinstance(self.games, tuple):
            raise LabBenchmarkError("model profile games are invalid")
        _safe_public_copy(self.as_dict(), label="model_profile")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": MODEL_PROFILE_SCHEMA_VERSION,
            "model_id": self.model_id,
            "provider": self.provider,
            "games": [dict(game) for game in self.games],
            "universal_score": "not_available",
        }


def _frozen_labyrinth_recipe() -> BenchmarkRecipe:
    map_spec = default_maze_map_spec()
    effective_budget = min(map_spec.participant_call_budget, MAX_LIVE_PROVIDER_CALLS)
    return BenchmarkRecipe.create(
        recipe_id="labyrinth-interactive-v1",
        game_id="labyrinth-run",
        game_version=LIVE_TASK_ID,
        mode=RunMode.SEALED_BENCHMARK,
        runtime_version="worldeval-labyrinth-live-v1",
        scenario_id="live-labyrinth",
        map_id=map_spec.map_id,
        map_sha256=map_spec.map_sha256,
        seed_policy={"kind": "frozen_map", "map_sha256": map_spec.map_sha256},
        budget={
            "maximum_ticks": 4 * effective_budget,
            "participant_call_budget": effective_budget,
            "scope": "per_participant",
        },
        configuration={
            "corridor_commands": True,
            "max_corridor_cells": MAX_CORRIDOR_COMMAND_CELLS,
            "vision_occlusion": "straight_line_walls",
            "vision_range_cells": DEFAULT_VISION_RANGE_CELLS,
        },
        skill_mode="none",
        prompt_sha256=labyrinth_protocol_prompt_sha256(),
        allowed_providers=("openai",),
        participant_count=3,
        metric_ids=_METRIC_IDS,
        ranking_metric="completion_basis_points",
        minimum_samples=3,
    )


LABYRINTH_INTERACTIVE_RECIPE = _frozen_labyrinth_recipe()
BENCHMARK_RECIPES: Mapping[str, BenchmarkRecipe] = MappingProxyType(
    {LABYRINTH_INTERACTIVE_RECIPE.recipe_id: LABYRINTH_INTERACTIVE_RECIPE}
)
BENCHMARK_RECIPE_REGISTRY_SHA256 = canonical_sha256(
    {recipe_id: recipe.as_dict() for recipe_id, recipe in BENCHMARK_RECIPES.items()}
)


class BenchmarkStore:
    """Small, atomically persisted index of verified aggregate benchmark evidence."""

    def __init__(
        self,
        *,
        runs_dir: Path,
        recipes: Mapping[str, BenchmarkRecipe] = BENCHMARK_RECIPES,
    ) -> None:
        if not isinstance(recipes, Mapping) or not recipes:
            raise LabBenchmarkError("benchmark recipe registry is invalid")
        if set(recipes) != {recipe.recipe_id for recipe in recipes.values()} or any(
            not isinstance(recipe, BenchmarkRecipe) for recipe in recipes.values()
        ):
            raise LabBenchmarkError("benchmark recipe registry is invalid")
        self._recipes = MappingProxyType(dict(recipes))
        self._recipe_registry_sha256 = canonical_sha256(
            {recipe_id: recipe.as_dict() for recipe_id, recipe in self._recipes.items()}
        )
        self._root = Path(runs_dir) / _STORE_DIRECTORY
        self._path = self._root / _STORE_FILE
        self._lock = threading.RLock()
        self._results: dict[tuple[str, str], VerifiedBenchmarkResult] = {}
        self._progress: dict[str, SeasonProgress] = {
            recipe_id: SeasonProgress(recipe_id=recipe_id) for recipe_id in self._recipes
        }
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self._root, 0o700)
        except OSError:
            pass
        self._load()

    @property
    def root(self) -> Path:
        return self._root

    def accept(
        self,
        *,
        recipe_id: str,
        candidate_run: Mapping[str, object],
        participant_metrics: Sequence[Mapping[str, object]],
    ) -> Mapping[str, Any]:
        """Validate and atomically retain a single verified aggregate, never raw run material."""

        recipe = self._recipe(recipe_id)
        result = VerifiedBenchmarkResult.from_candidate(
            recipe=recipe,
            candidate_run=candidate_run,
            participant_metrics=participant_metrics,
        )
        _validate_result_against_recipe(result, recipe)
        key = result.deduplication_key
        with self._lock:
            existing = self._results.get(key)
            if existing is not None:
                if existing.verified_result_sha256 != result.verified_result_sha256:
                    raise LabBenchmarkError("benchmark contract/result pair conflicts")
                return existing.as_dict()
            self._results[key] = result
            try:
                self._persist()
            except Exception:
                self._results.pop(key, None)
                raise
        return result.as_dict()

    def set_progress(self, progress: SeasonProgress) -> Mapping[str, object]:
        """Persist safe aggregate progress supplied by an authoritative season runner."""

        if not isinstance(progress, SeasonProgress):
            raise LabBenchmarkError("benchmark season progress is invalid")
        self._recipe(progress.recipe_id)
        with self._lock:
            previous = self._progress[progress.recipe_id]
            self._progress[progress.recipe_id] = progress
            try:
                self._persist()
            except Exception:
                self._progress[progress.recipe_id] = previous
                raise
        return progress.as_dict()

    def results(self, *, recipe_id: str | None = None) -> list[Mapping[str, Any]]:
        if recipe_id is not None:
            self._recipe(recipe_id)
        with self._lock:
            values = [
                result
                for result in self._results.values()
                if recipe_id is None or result.recipe_id == recipe_id
            ]
        return [
            result.as_dict() for result in sorted(values, key=lambda item: item.deduplication_key)
        ]

    def leaderboard(self, *, recipe_id: str) -> Mapping[str, object]:
        recipe = self._recipe(recipe_id)
        with self._lock:
            results = [result for result in self._results.values() if result.recipe_id == recipe_id]
        buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for result in results:
            for participant in result.as_dict()["participants"]:
                key = (str(participant["provider"]), str(participant["model_id"]))
                buckets.setdefault(key, []).append(participant)
        rows: list[dict[str, object]] = []
        for (provider, model_id), participants in buckets.items():
            metrics = {
                metric_id: sum(
                    int(participant["metrics"][metric_id]) for participant in participants
                )
                // len(participants)
                for metric_id in recipe.metric_ids
            }
            ranking_values = [
                int(participant["metrics"][recipe.ranking_metric]) for participant in participants
            ]
            rows.append(
                {
                    "model_id": model_id,
                    "provider": provider,
                    "sample_count": len(participants),
                    "metrics": metrics,
                    "uncertainty": _deterministic_uncertainty(
                        ranking_values, minimum_samples=recipe.minimum_samples
                    ),
                }
            )
        rows.sort(
            key=lambda row: (
                -int(row["metrics"][recipe.ranking_metric]),
                int(row["metrics"].get("budget_charged_calls", 0)),
                int(row["metrics"].get("input_tokens", 0))
                + int(row["metrics"].get("output_tokens", 0)),
                str(row["provider"]),
                str(row["model_id"]),
            )
        )
        for index, row in enumerate(rows, start=1):
            row["rank"] = index
        payload = {
            "schema_version": "worldeval/lab-benchmark-leaderboard/1",
            "game_id": recipe.game_id,
            "recipe_id": recipe.recipe_id,
            "recipe_sha256": recipe.recipe_sha256,
            "comparison_scope": "same_recipe_only",
            "ranking_metric": recipe.ranking_metric,
            "sample_requirement": recipe.minimum_samples,
            "rows": rows,
            "uncertainty": {
                "method": "deterministic-observed-range-v1",
                "available": bool(rows)
                and all(row["sample_count"] >= recipe.minimum_samples for row in rows),
                "sample_count": sum(int(row["sample_count"]) for row in rows),
            },
        }
        return _safe_public_copy(payload, label="benchmark_leaderboard")

    def model_profiles(self) -> list[Mapping[str, object]]:
        profiles: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for recipe_id, recipe in self._recipes.items():
            leaderboard = self.leaderboard(recipe_id=recipe_id)
            for row in leaderboard["rows"]:
                key = (str(row["provider"]), str(row["model_id"]))
                profiles.setdefault(key, []).append(
                    {
                        "game_id": recipe.game_id,
                        "recipe_id": recipe.recipe_id,
                        "recipe_sha256": recipe.recipe_sha256,
                        "comparison_scope": "same_recipe_only",
                        "sample_count": row["sample_count"],
                        "metrics": row["metrics"],
                        "uncertainty": row["uncertainty"],
                    }
                )
        return [
            ModelProfile(
                model_id=model_id,
                provider=provider,
                games=tuple(
                    sorted(games, key=lambda item: (str(item["game_id"]), str(item["recipe_id"])))
                ),
            ).as_dict()
            for (provider, model_id), games in sorted(profiles.items())
        ]

    def game_status(self, game_id: str) -> Mapping[str, object]:
        try:
            GAME_CATALOG.game(game_id)
        except GameCatalogError as error:
            raise LabBenchmarkError("benchmark game is not registered") from error
        recipes = [recipe for recipe in self._recipes.values() if recipe.game_id == game_id]
        if not recipes:
            return {
                "game_id": game_id,
                "season_state": "not_available",
                "recipes": [],
                "leaderboards": [],
                "model_profiles": [],
                "message": "No verified benchmark recipe is available for this game.",
            }
        recipe_statuses = []
        for recipe in sorted(recipes, key=lambda item: item.recipe_id):
            result_count = len(self.results(recipe_id=recipe.recipe_id))
            recipe_statuses.append(
                {
                    "recipe": recipe.as_dict(),
                    "season_progress": self._progress[recipe.recipe_id].as_dict(),
                    "verified_result_count": result_count,
                    "leaderboard": self.leaderboard(recipe_id=recipe.recipe_id),
                }
            )
        total_results = sum(item["verified_result_count"] for item in recipe_statuses)
        return _safe_public_copy(
            {
                "game_id": game_id,
                "season_state": "not_started" if total_results == 0 else "results_available",
                "recipes": recipe_statuses,
                "leaderboards": [item["leaderboard"] for item in recipe_statuses],
                "model_profiles": [
                    profile
                    for profile in self.model_profiles()
                    if any(game["game_id"] == game_id for game in profile["games"])
                ],
            },
            label="benchmark_game_status",
        )

    def _recipe(self, recipe_id: str) -> BenchmarkRecipe:
        try:
            return self._recipes[_identifier(recipe_id, label="benchmark recipe id")]
        except KeyError as error:
            raise LabBenchmarkError("benchmark recipe is not registered") from error

    def _store_body(self) -> dict[str, object]:
        return {
            "progress": [
                self._progress[recipe_id].as_dict() for recipe_id in sorted(self._progress)
            ],
            "recipe_registry_sha256": self._recipe_registry_sha256,
            "results": [
                result.as_dict()
                for result in sorted(
                    self._results.values(), key=lambda item: item.deduplication_key
                )
            ],
            "schema_version": BENCHMARK_STORE_SCHEMA_VERSION,
        }

    def _persist(self) -> None:
        body = self._store_body()
        _safe_public_copy(body, label="benchmark_store")
        payload = canonical_json_bytes(body)
        temporary = self._path.with_name(f".{self._path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _load(self) -> None:
        if not self._path.exists():
            return
        if self._path.is_symlink():
            raise LabBenchmarkError("benchmark store cannot be a symbolic link")
        try:
            payload = self._path.read_bytes()
            if len(payload) > 16 * 1024 * 1024:
                raise LabBenchmarkError("benchmark store is too large")
            value = strict_json_loads(payload)
        except (OSError, TypeError, ValueError) as error:
            raise LabBenchmarkError("benchmark store cannot be read") from error
        parsed = _exact_mapping(value, fields=_STORE_FIELDS, label="benchmark store")
        if (
            parsed["schema_version"] != BENCHMARK_STORE_SCHEMA_VERSION
            or parsed["recipe_registry_sha256"] != self._recipe_registry_sha256
        ):
            raise LabBenchmarkError("benchmark store registry differs")
        raw_progress = parsed["progress"]
        raw_results = parsed["results"]
        if not isinstance(raw_progress, list) or not isinstance(raw_results, list):
            raise LabBenchmarkError("benchmark store records are invalid")
        progress_items = [SeasonProgress.from_dict(item) for item in raw_progress]
        progress = {item.recipe_id: item for item in progress_items}
        if len(progress) != len(progress_items):
            raise LabBenchmarkError("benchmark store contains duplicate season progress")
        if set(progress) != set(self._recipes):
            raise LabBenchmarkError("benchmark store progress differs from recipes")
        results: dict[tuple[str, str], VerifiedBenchmarkResult] = {}
        for raw_result in raw_results:
            result = VerifiedBenchmarkResult.from_dict(raw_result)
            recipe = self._recipe(result.recipe_id)
            if result.recipe_sha256 != recipe.recipe_sha256:
                raise LabBenchmarkError("benchmark result recipe binding differs")
            _validate_result_against_recipe(result, recipe)
            key = result.deduplication_key
            if key in results:
                raise LabBenchmarkError("benchmark store contains a duplicate result")
            results[key] = result
        self._progress = progress
        self._results = results


def _deterministic_uncertainty(values: Sequence[int], *, minimum_samples: int) -> dict[str, object]:
    if not values:
        return {
            "method": "deterministic-observed-range-v1",
            "available": False,
            "minimum_samples": minimum_samples,
            "sample_count": 0,
            "lower": None,
            "upper": None,
        }
    available = len(values) >= minimum_samples
    return {
        "method": "deterministic-observed-range-v1",
        "available": available,
        "minimum_samples": minimum_samples,
        "sample_count": len(values),
        "lower": min(values) if available else None,
        "upper": max(values) if available else None,
    }


__all__ = [
    "BENCHMARK_RECIPES",
    "BENCHMARK_RECIPE_REGISTRY_SHA256",
    "BENCHMARK_RECIPE_SCHEMA_VERSION",
    "BENCHMARK_STORE_SCHEMA_VERSION",
    "LABYRINTH_INTERACTIVE_RECIPE",
    "MODEL_PROFILE_SCHEMA_VERSION",
    "SEASON_PROGRESS_SCHEMA_VERSION",
    "VERIFIED_BENCHMARK_RESULT_SCHEMA_VERSION",
    "BenchmarkRecipe",
    "BenchmarkStore",
    "LabBenchmarkError",
    "ModelProfile",
    "SeasonProgress",
    "VerifiedBenchmarkResult",
]
