"""Deterministic statistics and tabular exports for Labyrinth benchmark seasons."""

from __future__ import annotations

import csv
import hashlib
import io
import random
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..protocol import canonical_json_bytes
from .artifacts import (
    BenchmarkArtifactError,
    BenchmarkArtifactStore,
    atomic_write,
    atomic_write_json,
)
from .spec import (
    ANALYSIS_VERSION,
    BOOTSTRAP_ITERATIONS,
    DIFFICULTIES,
    VISION_DEPTHS,
    LabyrinthBenchmarkSpec,
    VisionDepth,
)


class BenchmarkAnalysisError(RuntimeError):
    """A season is incomplete or statistically inconsistent."""


_METRICS: tuple[tuple[str, str, Callable[[Mapping[str, Any]], bool]], ...] = (
    ("completion_basis_points", "basis_points", lambda _: True),
    ("charged_calls", "calls", lambda _: True),
    ("path_efficiency_basis_points", "basis_points", lambda row: bool(row["completed"])),
    ("input_tokens", "tokens", lambda row: bool(row["token_telemetry_complete"])),
    ("output_tokens", "tokens", lambda row: bool(row["token_telemetry_complete"])),
    ("total_tokens", "tokens", lambda row: bool(row["token_telemetry_complete"])),
    ("cached_input_tokens", "tokens", lambda row: bool(row["token_telemetry_complete"])),
    (
        "cache_write_tokens",
        "tokens",
        lambda row: bool(row["cache_write_telemetry_complete"]),
    ),
    ("latency_ms", "milliseconds", lambda row: bool(row["latency_telemetry_complete"])),
    ("cells_per_call_milli", "milli_cells_per_call", lambda _: True),
    ("corridor_command_basis_points", "basis_points", lambda _: True),
    ("peak_memory_bytes", "bytes", lambda _: True),
    ("memory_evictions", "evictions", lambda _: True),
    ("invalid_decision_basis_points", "basis_points", lambda _: True),
    ("wait_basis_points", "basis_points", lambda _: True),
    ("repeated_cell_basis_points", "basis_points", lambda _: True),
    (
        "recovery_basis_points",
        "basis_points",
        lambda row: int(row["recovery_opportunities"]) > 0,
    ),
)


def flatten_results(results: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Flatten one row per model episode and reject duplicated factorial cells."""

    output = []
    identities = set()
    for result in results:
        episodes = result.get("episodes")
        if not isinstance(episodes, list):
            raise BenchmarkAnalysisError("benchmark result episodes are invalid")
        for episode in episodes:
            if not isinstance(episode, Mapping):
                raise BenchmarkAnalysisError("benchmark result episode is invalid")
            identity = (
                result.get("phase"),
                result.get("map_id"),
                result.get("vision_depth"),
                result.get("repetition"),
                episode.get("model_id"),
                episode.get("participant_id"),
            )
            if identity in identities:
                raise BenchmarkAnalysisError("benchmark episode cell is duplicated")
            identities.add(identity)
            completed = episode.get("completed") is True
            output.append(
                {
                    "phase": result["phase"],
                    "map_id": result["map_id"],
                    "map_sha256": result["map_sha256"],
                    "difficulty": result["difficulty"],
                    "vision_depth": result["vision_depth"],
                    "repetition": result["repetition"],
                    "skill_mode": result["skill_mode"],
                    "participant_call_budget": result["participant_call_budget"],
                    "participant_id": episode["participant_id"],
                    "model_id": episode["model_id"],
                    "provider_model": episode["provider_model"],
                    "completed": completed,
                    "completion_basis_points": 10_000 if completed else 0,
                    "finish_tick": episode["finish_tick"],
                    "calls": episode["calls"],
                    "charged_calls": episode["charged_calls"],
                    "distance_cells": episode["distance_cells"],
                    "shortest_path_cells": episode["shortest_path_cells"],
                    "path_efficiency_basis_points": episode[
                        "path_efficiency_basis_points"
                    ],
                    "repeated_corridor_cells": episode["repeated_corridor_cells"],
                    "repeated_cell_basis_points": episode["repeated_cell_basis_points"],
                    "invalid_decisions": episode["invalid_decisions"],
                    "invalid_decision_basis_points": episode[
                        "invalid_decision_basis_points"
                    ],
                    "waiting_windows": episode["waiting_windows"],
                    "wait_basis_points": episode["wait_basis_points"],
                    "corridor_command_basis_points": episode[
                        "corridor_command_basis_points"
                    ],
                    "cells_per_call_milli": episode["cells_per_call_milli"],
                    "backtrack_commands": episode["backtrack_commands"],
                    "successful_backtracks": episode["successful_backtracks"],
                    "backtrack_success_basis_points": episode[
                        "backtrack_success_basis_points"
                    ],
                    "recovery_opportunities": episode["recovery_opportunities"],
                    "successful_recoveries": episode["successful_recoveries"],
                    "recovery_basis_points": episode["recovery_basis_points"],
                    "input_tokens": episode["input_tokens"],
                    "output_tokens": episode["output_tokens"],
                    "cached_input_tokens": episode["cached_input_tokens"],
                    "cache_write_tokens": episode["cache_write_tokens"],
                    "total_tokens": episode["total_tokens"],
                    "token_telemetry_complete": episode["token_telemetry_complete"],
                    "cache_write_telemetry_complete": episode[
                        "cache_write_telemetry_complete"
                    ],
                    "latency_ms": episode["latency_ms"],
                    "latency_telemetry_complete": episode[
                        "latency_telemetry_complete"
                    ],
                    "peak_memory_bytes": episode["peak_memory_bytes"],
                    "memory_evictions": episode["memory_evictions"],
                    "provider_failures": dict(episode["provider_failures"]),
                }
            )
    return tuple(
        sorted(
            output,
            key=lambda row: (
                str(row["phase"]),
                str(row["difficulty"]),
                str(row["map_id"]),
                VISION_DEPTHS.index(row["vision_depth"]),
                int(row["repetition"]),
                str(row["model_id"]),
            ),
        )
    )


def _median_fraction(values: Sequence[int]) -> Fraction:
    ordered = sorted(values)
    if not ordered:
        raise BenchmarkAnalysisError("benchmark median has no values")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Fraction(ordered[middle], 1)
    return Fraction(ordered[middle - 1] + ordered[middle], 2)


def select_best_vision_depth(rows: Sequence[Mapping[str, Any]]) -> VisionDepth:
    """Apply the frozen completion/calls/tokens/shallow-depth selection rule."""

    baseline = [row for row in rows if row.get("phase") == "baseline"]
    if not baseline:
        raise BenchmarkAnalysisError("baseline results are required for depth selection")
    expected_per_depth = 40 * 3 * 3
    ranking = []
    for vision_index, vision in enumerate(VISION_DEPTHS):
        selected = [row for row in baseline if row.get("vision_depth") == vision]
        if len(selected) != expected_per_depth:
            raise BenchmarkAnalysisError("baseline depth cell is incomplete")
        completion = Fraction(sum(bool(row["completed"]) for row in selected), len(selected))
        calls = _median_fraction([int(row["charged_calls"]) for row in selected])
        # Missing token telemetry is never rewarded with an artificial zero.
        tokens = _median_fraction(
            [
                int(row["total_tokens"])
                if row["token_telemetry_complete"]
                else 9_007_199_254_740_991
                for row in selected
            ]
        )
        ranking.append((-completion, calls, tokens, vision_index, vision))
    ranking.sort(key=lambda item: item[:-1])
    return ranking[0][-1]


def _percentile(values: Sequence[float], percentile_basis_points: int) -> float:
    if not values:
        raise BenchmarkAnalysisError("bootstrap distribution is empty")
    ordered = sorted(values)
    if not 0 <= percentile_basis_points <= 10_000:
        raise BenchmarkAnalysisError("bootstrap percentile is invalid")
    rank = Fraction(percentile_basis_points, 10_000) * (len(ordered) - 1)
    lower = rank.numerator // rank.denominator
    upper = min(len(ordered) - 1, lower + 1)
    fraction = float(rank - lower)
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def hierarchical_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    value_key: str,
    *,
    seed: int,
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> Mapping[str, int]:
    """Resample map seeds first and repetitions within each sampled map."""

    if not rows or iterations < 1:
        raise BenchmarkAnalysisError("bootstrap requires rows and iterations")
    by_map: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        value = row.get(value_key)
        map_id = row.get("map_id")
        if isinstance(value, bool) or not isinstance(value, int) or not isinstance(map_id, str):
            raise BenchmarkAnalysisError("bootstrap row is invalid")
        by_map[map_id].append(value)
    map_ids = tuple(sorted(by_map))
    if not map_ids or any(not values for values in by_map.values()):
        raise BenchmarkAnalysisError("bootstrap map hierarchy is invalid")
    generator = random.Random(seed)
    distribution = []
    for _ in range(iterations):
        total = 0
        count = 0
        for _map_index in range(len(map_ids)):
            map_id = map_ids[generator.randrange(len(map_ids))]
            values = by_map[map_id]
            for _repetition in range(len(values)):
                total += values[generator.randrange(len(values))]
                count += 1
        distribution.append(total / count)
    point = sum(int(row[value_key]) for row in rows) / len(rows)
    return {
        "point": round(point),
        "lower": round(_percentile(distribution, 250)),
        "upper": round(_percentile(distribution, 9750)),
        "map_count": len(map_ids),
        "sample_size": len(rows),
    }


def _group_seed(base_seed: int, parts: Sequence[object]) -> int:
    digest = hashlib.sha256(canonical_json_bytes([str(item) for item in parts])).digest()
    return base_seed ^ int.from_bytes(digest[:8], "big")


def _aggregate_groups(
    rows: Sequence[Mapping[str, Any]], spec: LabyrinthBenchmarkSpec
) -> list[dict[str, object]]:
    grouped: dict[tuple[object, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                row["phase"],
                row["skill_mode"],
                row["difficulty"],
                row["vision_depth"],
                row["model_id"],
            )
        ].append(row)
    output = []
    for group_key, group_rows in sorted(grouped.items(), key=lambda item: tuple(map(str, item[0]))):
        for metric, unit, predicate in _METRICS:
            metric_rows = [row for row in group_rows if predicate(row)]
            if not metric_rows:
                estimate: Mapping[str, int | None] = {
                    "point": None,
                    "lower": None,
                    "upper": None,
                    "map_count": 0,
                    "sample_size": 0,
                }
            else:
                estimate = hierarchical_bootstrap(
                    metric_rows,
                    metric,
                    seed=_group_seed(spec.bootstrap_seed, (*group_key, metric)),
                    iterations=spec.bootstrap_iterations,
                )
            output.append(
                {
                    "phase": group_key[0],
                    "skill_mode": group_key[1],
                    "difficulty": group_key[2],
                    "vision_depth": group_key[3],
                    "model_id": group_key[4],
                    "metric": metric,
                    "unit": unit,
                    "available": bool(metric_rows),
                    **estimate,
                    "censored_count": sum(
                        not bool(row["completed"]) for row in group_rows
                    ),
                    "missing_count": len(group_rows) - len(metric_rows),
                }
            )
    return output


def _paired_skill_effects(
    rows: Sequence[Mapping[str, Any]],
    selected_depth: VisionDepth,
    spec: LabyrinthBenchmarkSpec,
) -> list[dict[str, object]]:
    baseline = {
        (
            row["map_id"],
            row["repetition"],
            row["model_id"],
            row["participant_id"],
        ): row
        for row in rows
        if row["phase"] == "baseline" and row["vision_depth"] == selected_depth
    }
    skilled = [row for row in rows if row["phase"] == "skill"]
    if not skilled:
        return []
    pairs = []
    for skill in skilled:
        key = (
            skill["map_id"],
            skill["repetition"],
            skill["model_id"],
            skill["participant_id"],
        )
        base = baseline.get(key)
        if base is None:
            raise BenchmarkAnalysisError("skill episode has no seat-matched baseline")
        pairs.append(
            {
                "map_id": skill["map_id"],
                "difficulty": skill["difficulty"],
                "model_id": skill["model_id"],
                "completion_delta_basis_points": skill["completion_basis_points"]
                - base["completion_basis_points"],
                "charged_calls_delta": skill["charged_calls"] - base["charged_calls"],
                "total_tokens_delta": skill["total_tokens"] - base["total_tokens"],
                "path_efficiency_delta_basis_points": (
                    skill["path_efficiency_basis_points"]
                    - base["path_efficiency_basis_points"]
                ),
                "both_completed": bool(skill["completed"] and base["completed"]),
                "token_telemetry_complete": bool(
                    skill["token_telemetry_complete"]
                    and base["token_telemetry_complete"]
                ),
            }
        )
    expected_pairs = 40 * 3 * 3
    if len(pairs) != expected_pairs:
        raise BenchmarkAnalysisError("skill pairing is incomplete")
    metrics = (
        ("completion_delta_basis_points", "basis_points", lambda _: True),
        ("charged_calls_delta", "calls", lambda _: True),
        (
            "total_tokens_delta",
            "tokens",
            lambda row: bool(row["token_telemetry_complete"]),
        ),
        (
            "path_efficiency_delta_basis_points",
            "basis_points",
            lambda row: bool(row["both_completed"]),
        ),
    )
    output = []
    for difficulty in DIFFICULTIES:
        for model_id in ("sol", "terra", "luna"):
            group = [
                row
                for row in pairs
                if row["difficulty"] == difficulty and row["model_id"] == model_id
            ]
            for metric, unit, predicate in metrics:
                metric_group = [row for row in group if predicate(row)]
                if not metric_group:
                    estimate: Mapping[str, int | None] = {
                        "point": None,
                        "lower": None,
                        "upper": None,
                        "map_count": 0,
                        "sample_size": 0,
                    }
                else:
                    estimate = hierarchical_bootstrap(
                        metric_group,
                        metric,
                        seed=_group_seed(
                            spec.bootstrap_seed, ("paired", difficulty, model_id, metric)
                        ),
                        iterations=spec.bootstrap_iterations,
                    )
                output.append(
                    {
                        "difficulty": difficulty,
                        "model_id": model_id,
                        "metric": metric,
                        "unit": unit,
                        "available": bool(metric_group),
                        **estimate,
                        "missing_pair_count": len(group) - len(metric_group),
                    }
                )
    return output


def _model_skill_comparisons(
    rows: Sequence[Mapping[str, Any]], selected_depth: VisionDepth
) -> list[dict[str, object]]:
    skilled_luna = [row for row in rows if row["phase"] == "skill" and row["model_id"] == "luna"]
    if not skilled_luna:
        return []
    output = []
    for opponent in ("sol", "terra"):
        baseline = [
            row
            for row in rows
            if row["phase"] == "baseline"
            and row["vision_depth"] == selected_depth
            and row["model_id"] == opponent
        ]
        if len(baseline) != len(skilled_luna):
            raise BenchmarkAnalysisError("model skill comparison is incomplete")
        luna_completion = sum(bool(row["completed"]) for row in skilled_luna)
        opponent_completion = sum(bool(row["completed"]) for row in baseline)
        luna_calls = _median_fraction([int(row["charged_calls"]) for row in skilled_luna])
        opponent_calls = _median_fraction([int(row["charged_calls"]) for row in baseline])
        luna_tokens = _median_fraction([int(row["total_tokens"]) for row in skilled_luna])
        opponent_tokens = _median_fraction([int(row["total_tokens"]) for row in baseline])
        tokens_complete = all(
            bool(row["token_telemetry_complete"]) for row in (*skilled_luna, *baseline)
        )
        if luna_completion != opponent_completion:
            matches = luna_completion > opponent_completion
        elif luna_calls != opponent_calls:
            matches = luna_calls < opponent_calls
        else:
            matches = tokens_complete and luna_tokens <= opponent_tokens
        output.append(
            {
                "skilled_model_id": "luna",
                "baseline_model_id": opponent,
                "sample_size_each": len(baseline),
                "skilled_completion_count": luna_completion,
                "baseline_completion_count": opponent_completion,
                "skilled_median_charged_calls_x2": round(luna_calls * 2),
                "baseline_median_charged_calls_x2": round(opponent_calls * 2),
                "skilled_median_total_tokens_x2": round(luna_tokens * 2),
                "baseline_median_total_tokens_x2": round(opponent_tokens * 2),
                "token_telemetry_complete": tokens_complete,
                "matches_or_exceeds": matches,
            }
        )
    return output


def analyze_store(store: BenchmarkArtifactStore) -> Mapping[str, Any]:
    spec = store.load_specification()
    results = store.iter_results()
    phase_counts = Counter(str(result.get("phase")) for result in results)
    if dict(phase_counts) != {"pilot": 120, "baseline": 600, "skill": 120}:
        raise BenchmarkAnalysisError("complete 120/600/120 season is required")
    rows = flatten_results(results)
    baseline_rows = [row for row in rows if row["phase"] == "baseline"]
    if len(baseline_rows) != 600 * 3:
        raise BenchmarkAnalysisError("complete 600-race baseline is required")
    selected_depth = select_best_vision_depth(rows)
    skill_schedule = store.load_schedule("skill")
    if skill_schedule.selected_vision_depth != selected_depth or any(
        row["phase"] == "skill" and row["vision_depth"] != selected_depth for row in rows
    ):
        raise BenchmarkAnalysisError("skill season depth differs from objective selection")
    groups = _aggregate_groups(rows, spec)
    paired = _paired_skill_effects(rows, selected_depth, spec)
    failure_counts: Counter[str] = Counter()
    for row in rows:
        failure_counts.update(row["provider_failures"])
    schedule_hashes = {}
    for phase in ("pilot", "baseline", "skill"):
        try:
            schedule_hashes[phase] = store.load_schedule(phase).schedule_sha256
        except (BenchmarkArtifactError, OSError):
            continue
    infrastructure_voids = store.load_state().get("infrastructure_voids_by_phase")
    if not isinstance(infrastructure_voids, Mapping):
        raise BenchmarkAnalysisError("infrastructure void counts are unavailable")
    phase_totals: dict[str, Mapping[str, object]] = {}
    for phase in ("pilot", "baseline", "skill", "season"):
        phase_rows = rows if phase == "season" else [row for row in rows if row["phase"] == phase]
        phase_results = (
            results
            if phase == "season"
            else [result for result in results if result["phase"] == phase]
        )
        missing_tokens = sum(not bool(row["token_telemetry_complete"]) for row in phase_rows)
        missing_latency = sum(
            not bool(row["latency_telemetry_complete"]) for row in phase_rows
        )
        phase_totals[phase] = {
            "race_results": len(phase_results),
            "model_episodes": len(phase_rows),
            "api_calls": sum(int(row["calls"]) for row in phase_rows),
            "budget_charged_calls": sum(int(row["charged_calls"]) for row in phase_rows),
            "input_tokens": sum(int(row["input_tokens"]) for row in phase_rows),
            "output_tokens": sum(int(row["output_tokens"]) for row in phase_rows),
            "total_tokens": sum(int(row["total_tokens"]) for row in phase_rows),
            "token_telemetry_complete": missing_tokens == 0,
            "token_telemetry_missing_episodes": missing_tokens,
            "provider_latency_ms": sum(int(row["latency_ms"]) for row in phase_rows),
            "latency_telemetry_complete": missing_latency == 0,
            "latency_telemetry_missing_episodes": missing_latency,
            "elapsed_wall_time_ms": sum(int(result["wall_time_ms"]) for result in phase_results),
        }
    body: dict[str, Any] = {
        "schema_version": ANALYSIS_VERSION,
        "season_id": store.season_id,
        "spec_sha256": spec.spec_sha256,
        "schedule_sha256s": schedule_hashes,
        "frozen_hashes": {
            "pilot_manifest_sha256": spec.pilot_manifest_sha256,
            "main_manifest_sha256": spec.main_manifest_sha256,
            "protocol_prompt_sha256": spec.protocol_prompt_sha256,
            "skill_sha256": spec.skill_sha256,
        },
        "selected_vision_depth": selected_depth,
        "selection_rule": [
            "highest_completion_rate",
            "lowest_median_budget_charged_calls",
            "lowest_median_total_tokens",
            "shallower_vision_depth",
        ],
        "bootstrap": {
            "method": "hierarchical_map_then_repetition",
            "confidence_basis_points": 9500,
            "iterations": spec.bootstrap_iterations,
            "seed": spec.bootstrap_seed,
        },
        "sample_counts": {
            "race_results": len(results),
            "model_episodes": len(rows),
            "completed_model_episodes": sum(bool(row["completed"]) for row in rows),
            "censored_model_episodes": sum(not bool(row["completed"]) for row in rows),
            "pilot_races": phase_counts["pilot"],
            "pilot_model_episodes": sum(row["phase"] == "pilot" for row in rows),
            "baseline_races": phase_counts["baseline"],
            "baseline_model_episodes": sum(row["phase"] == "baseline" for row in rows),
            "skill_races": phase_counts["skill"],
            "skill_model_episodes": sum(row["phase"] == "skill" for row in rows),
        },
        "phase_totals": phase_totals,
        "infrastructure_voids_by_phase": dict(infrastructure_voids),
        "provider_failures": dict(sorted(failure_counts.items())),
        "groups": groups,
        "paired_skill_effects": paired,
        "model_skill_comparisons": _model_skill_comparisons(rows, selected_depth),
    }
    body["analysis_sha256"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    atomic_write_json(store.analysis_path, body)
    _write_group_csv(store.aggregate_csv_path, groups)
    _write_group_csv(store.paired_csv_path, paired)
    return body


def _write_group_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    atomic_write(path, output.getvalue().encode("utf-8"))


__all__ = [
    "BenchmarkAnalysisError",
    "analyze_store",
    "flatten_results",
    "hierarchical_bootstrap",
    "select_best_vision_depth",
]
