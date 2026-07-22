"""Frozen specifications and deterministic schedules for the Labyrinth benchmark."""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Mapping, Sequence, Union

from ..live_labyrinth import (
    MAZE_NAVIGATION_SKILL_ID,
    labyrinth_protocol_prompt_sha256,
    maze_navigation_skill_sha256,
)
from ..maze_maps import MazeMapSpec, generate_maze_map
from ..protocol import canonical_json_bytes, strict_json_loads

BENCHMARK_VERSION = "worldarena/labyrinth-benchmark/1"
MAP_SUITE_VERSION = "worldarena/labyrinth-map-suite/1"
SCHEDULE_VERSION = "worldarena/labyrinth-benchmark-schedule/1"
RESULT_VERSION = "worldarena/labyrinth-benchmark-race-result/1"
ANALYSIS_VERSION = "worldarena/labyrinth-benchmark-analysis/1"

DIFFICULTIES = ("easy", "medium", "hard", "memory_stress")
VISION_DEPTHS = (1, 2, 4, 8, "infinite")
PARTICIPANTS = ("participant_0", "participant_1", "participant_2")
PILOT_MAPS_PER_DIFFICULTY = 3
MAIN_MAPS_PER_DIFFICULTY = 10
PILOT_REPETITIONS = 2
BASELINE_REPETITIONS = 3
SKILL_REPETITIONS = 3
BOOTSTRAP_ITERATIONS = 10_000
DEFAULT_BOOTSTRAP_SEED = 5_605_604
FROZEN_MAP_INDEX_PATH = Path(__file__).with_name("frozen-map-index.json")

VisionDepth = Union[int, Literal["infinite"]]
Phase = Literal["pilot", "baseline", "skill"]
SkillMode = Literal["none", "maze-navigation-v1"]

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BenchmarkSpecError(ValueError):
    """A benchmark specification, map suite, or schedule is invalid."""


@dataclass(frozen=True)
class BenchmarkModelSpec:
    model_id: str
    display_name: str
    provider_model: str
    color: str
    marker: str
    line_style: str

    def __post_init__(self) -> None:
        for name in ("model_id", "provider_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
                raise BenchmarkSpecError(f"benchmark {name} is invalid")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise BenchmarkSpecError("benchmark model display name is invalid")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color):
            raise BenchmarkSpecError("benchmark model color is invalid")
        if self.marker not in {"circle", "square", "triangle"}:
            raise BenchmarkSpecError("benchmark model marker is invalid")
        if self.line_style not in {"solid", "dashed", "dotted"}:
            raise BenchmarkSpecError("benchmark model line style is invalid")

    def as_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "provider_model": self.provider_model,
            "color": self.color,
            "marker": self.marker,
            "line_style": self.line_style,
        }

    @classmethod
    def from_dict(cls, value: object) -> BenchmarkModelSpec:
        fields = {
            "model_id",
            "display_name",
            "provider_model",
            "color",
            "marker",
            "line_style",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise BenchmarkSpecError("benchmark model fields are invalid")
        return cls(**value)


DEFAULT_MODELS = (
    BenchmarkModelSpec(
        "sol", "Sol", "gpt-5.6-sol", "#C78B00", "circle", "solid"
    ),
    BenchmarkModelSpec(
        "terra", "Terra", "gpt-5.6-terra", "#087E8B", "square", "dashed"
    ),
    BenchmarkModelSpec(
        "luna", "Luna", "gpt-5.6-luna", "#7651B5", "triangle", "dotted"
    ),
)


@dataclass(frozen=True)
class MazeSuiteManifest:
    suite: Literal["pilot", "main"]
    maps: tuple[MazeMapSpec, ...]
    manifest_sha256: str = ""
    schema_version: str = MAP_SUITE_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MAP_SUITE_VERSION or self.suite not in {"pilot", "main"}:
            raise BenchmarkSpecError("maze suite identity is invalid")
        expected_count = len(DIFFICULTIES) * (
            PILOT_MAPS_PER_DIFFICULTY if self.suite == "pilot" else MAIN_MAPS_PER_DIFFICULTY
        )
        if not isinstance(self.maps, tuple) or len(self.maps) != expected_count:
            raise BenchmarkSpecError("maze suite map count is invalid")
        if tuple(sorted(self.maps, key=lambda item: item.map_id)) != self.maps:
            raise BenchmarkSpecError("maze suite maps are not canonical")
        if len({item.map_id for item in self.maps}) != len(self.maps):
            raise BenchmarkSpecError("maze suite map ids are not unique")
        expected_per_difficulty = expected_count // len(DIFFICULTIES)
        for difficulty in DIFFICULTIES:
            if sum(item.difficulty == difficulty for item in self.maps) != expected_per_difficulty:
                raise BenchmarkSpecError("maze suite difficulty balance is invalid")
        expected_hash = hashlib.sha256(canonical_json_bytes(self._hash_body())).hexdigest()
        if self.manifest_sha256:
            if self.manifest_sha256 != expected_hash:
                raise BenchmarkSpecError("maze suite hash differs")
        else:
            object.__setattr__(self, "manifest_sha256", expected_hash)

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "suite": self.suite,
            "maps": [item.as_dict() for item in self.maps],
        }

    def as_dict(self) -> dict[str, object]:
        return {**self._hash_body(), "manifest_sha256": self.manifest_sha256}

    @classmethod
    def from_dict(cls, value: object) -> MazeSuiteManifest:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "suite",
            "maps",
            "manifest_sha256",
        }:
            raise BenchmarkSpecError("maze suite fields are invalid")
        maps = value["maps"]
        if not isinstance(maps, list):
            raise BenchmarkSpecError("maze suite maps are invalid")
        return cls(
            schema_version=value["schema_version"],
            suite=value["suite"],
            maps=tuple(MazeMapSpec.from_dict(item) for item in maps),
            manifest_sha256=value["manifest_sha256"],
        )


def generate_map_suite(suite: Literal["pilot", "main"]) -> MazeSuiteManifest:
    """Generate the frozen, disjoint seed suite for one benchmark stage."""

    if suite not in {"pilot", "main"}:
        raise BenchmarkSpecError("maze suite name is invalid")
    count = PILOT_MAPS_PER_DIFFICULTY if suite == "pilot" else MAIN_MAPS_PER_DIFFICULTY
    family = 1 if suite == "pilot" else 2
    frozen = _load_frozen_map_index()
    maps = []
    for difficulty_index, difficulty in enumerate(DIFFICULTIES, start=1):
        for map_index in range(1, count + 1):
            # Explicit decimal families are easy to audit and stay stable across Python versions.
            seed = family * 1_000_000 + difficulty_index * 10_000 + map_index
            map_id = f"{suite}-{difficulty.replace('_', '-')}-{map_index:02d}"
            generated = generate_maze_map(seed=seed, difficulty=difficulty, map_id=map_id)
            expected = frozen[suite].get(map_id)
            if expected != (seed, difficulty, generated.map_sha256):
                raise BenchmarkSpecError("generated maze differs from frozen seed/hash index")
            maps.append(generated)
    manifest = MazeSuiteManifest(suite, tuple(sorted(maps, key=lambda item: item.map_id)))
    if manifest.manifest_sha256 != frozen[f"{suite}_manifest_sha256"]:
        raise BenchmarkSpecError("generated maze suite differs from frozen manifest hash")
    return manifest


@lru_cache(maxsize=1)
def _load_frozen_map_index() -> Mapping[str, object]:
    try:
        payload = FROZEN_MAP_INDEX_PATH.read_bytes()
    except OSError as error:
        raise BenchmarkSpecError("frozen maze index is unavailable") from error
    canonical_payload = payload[:-1] if payload.endswith(b"\n") else payload
    try:
        value = strict_json_loads(canonical_payload)
    except ValueError as error:
        raise BenchmarkSpecError("frozen maze index is invalid") from error
    if (
        not isinstance(value, Mapping)
        or canonical_json_bytes(value) != canonical_payload
        or value.get("schema_version") != "worldarena/labyrinth-frozen-map-index/1"
        or set(value)
        != {
            "schema_version",
            "pilot_manifest_sha256",
            "main_manifest_sha256",
            "pilot",
            "main",
        }
    ):
        raise BenchmarkSpecError("frozen maze index is not canonical")
    output: dict[str, object] = {
        "pilot_manifest_sha256": value["pilot_manifest_sha256"],
        "main_manifest_sha256": value["main_manifest_sha256"],
    }
    for suite in ("pilot", "main"):
        rows = value[suite]
        if not isinstance(rows, list):
            raise BenchmarkSpecError("frozen maze index rows are invalid")
        parsed: dict[str, tuple[int, str, str]] = {}
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {
                "map_id",
                "seed",
                "difficulty",
                "map_sha256",
            }:
                raise BenchmarkSpecError("frozen maze index row is invalid")
            parsed[str(row["map_id"])] = (
                int(row["seed"]),
                str(row["difficulty"]),
                str(row["map_sha256"]),
            )
        output[suite] = parsed
    return output


@dataclass(frozen=True)
class LabyrinthBenchmarkSpec:
    season_id: str
    pilot_manifest_sha256: str
    main_manifest_sha256: str
    protocol_prompt_sha256: str
    skill_id: str
    skill_sha256: str
    models: tuple[BenchmarkModelSpec, ...] = DEFAULT_MODELS
    vision_depths: tuple[VisionDepth, ...] = VISION_DEPTHS
    provider: str = "openai"
    service_tier: str = "default"
    reasoning_effort: str = "low"
    pilot_repetitions: int = PILOT_REPETITIONS
    baseline_repetitions: int = BASELINE_REPETITIONS
    skill_repetitions: int = SKILL_REPETITIONS
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED
    bootstrap_iterations: int = BOOTSTRAP_ITERATIONS
    per_participant_budget_rule: str = "two_times_undirected_edges"
    maximum_ticks_rule: str = "four_times_participant_decision_budget"
    schema_version: str = BENCHMARK_VERSION
    spec_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != BENCHMARK_VERSION:
            raise BenchmarkSpecError("benchmark spec version is invalid")
        if not isinstance(self.season_id, str) or _SAFE_ID.fullmatch(self.season_id) is None:
            raise BenchmarkSpecError("benchmark season id is invalid")
        for name in (
            "pilot_manifest_sha256",
            "main_manifest_sha256",
            "protocol_prompt_sha256",
            "skill_sha256",
        ):
            if not isinstance(getattr(self, name), str) or _SHA256.fullmatch(
                getattr(self, name)
            ) is None:
                raise BenchmarkSpecError(f"benchmark {name} is invalid")
        if self.skill_id != MAZE_NAVIGATION_SKILL_ID:
            raise BenchmarkSpecError("benchmark skill id is invalid")
        if (
            self.provider != "openai"
            or self.service_tier != "default"
            or self.reasoning_effort != "low"
        ):
            raise BenchmarkSpecError("benchmark provider configuration is invalid")
        if self.models != DEFAULT_MODELS:
            raise BenchmarkSpecError("benchmark model snapshots are invalid")
        if self.vision_depths != VISION_DEPTHS:
            raise BenchmarkSpecError("benchmark vision depths are invalid")
        expected_integers = {
            "pilot_repetitions": PILOT_REPETITIONS,
            "baseline_repetitions": BASELINE_REPETITIONS,
            "skill_repetitions": SKILL_REPETITIONS,
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        }
        for name, expected in expected_integers.items():
            if getattr(self, name) != expected:
                raise BenchmarkSpecError(f"benchmark {name} is invalid")
        if isinstance(self.bootstrap_seed, bool) or not isinstance(self.bootstrap_seed, int):
            raise BenchmarkSpecError("benchmark bootstrap seed is invalid")
        if self.per_participant_budget_rule != "two_times_undirected_edges":
            raise BenchmarkSpecError("benchmark decision budget rule is invalid")
        if self.maximum_ticks_rule != "four_times_participant_decision_budget":
            raise BenchmarkSpecError("benchmark maximum ticks rule is invalid")
        expected_hash = hashlib.sha256(canonical_json_bytes(self._hash_body())).hexdigest()
        if self.spec_sha256:
            if self.spec_sha256 != expected_hash:
                raise BenchmarkSpecError("benchmark spec hash differs")
        else:
            object.__setattr__(self, "spec_sha256", expected_hash)

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "season_id": self.season_id,
            "pilot_manifest_sha256": self.pilot_manifest_sha256,
            "main_manifest_sha256": self.main_manifest_sha256,
            "protocol_prompt_sha256": self.protocol_prompt_sha256,
            "skill_id": self.skill_id,
            "skill_sha256": self.skill_sha256,
            "models": [item.as_dict() for item in self.models],
            "vision_depths": list(self.vision_depths),
            "provider": self.provider,
            "service_tier": self.service_tier,
            "reasoning_effort": self.reasoning_effort,
            "pilot_repetitions": self.pilot_repetitions,
            "baseline_repetitions": self.baseline_repetitions,
            "skill_repetitions": self.skill_repetitions,
            "bootstrap_seed": self.bootstrap_seed,
            "bootstrap_iterations": self.bootstrap_iterations,
            "per_participant_budget_rule": self.per_participant_budget_rule,
            "maximum_ticks_rule": self.maximum_ticks_rule,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self._hash_body(), "spec_sha256": self.spec_sha256}

    @classmethod
    def from_dict(cls, value: object) -> LabyrinthBenchmarkSpec:
        fields = {
            "schema_version",
            "season_id",
            "pilot_manifest_sha256",
            "main_manifest_sha256",
            "protocol_prompt_sha256",
            "skill_id",
            "skill_sha256",
            "models",
            "vision_depths",
            "provider",
            "service_tier",
            "reasoning_effort",
            "pilot_repetitions",
            "baseline_repetitions",
            "skill_repetitions",
            "bootstrap_seed",
            "bootstrap_iterations",
            "per_participant_budget_rule",
            "maximum_ticks_rule",
            "spec_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise BenchmarkSpecError("benchmark spec fields are invalid")
        models = value["models"]
        visions = value["vision_depths"]
        if not isinstance(models, list) or not isinstance(visions, list):
            raise BenchmarkSpecError("benchmark spec sequences are invalid")
        arguments = dict(value)
        arguments["models"] = tuple(BenchmarkModelSpec.from_dict(item) for item in models)
        arguments["vision_depths"] = tuple(visions)
        return cls(**arguments)


def build_benchmark_spec(
    season_id: str, pilot: MazeSuiteManifest, main: MazeSuiteManifest
) -> LabyrinthBenchmarkSpec:
    if pilot.suite != "pilot" or main.suite != "main":
        raise BenchmarkSpecError("benchmark map suites are reversed")
    return LabyrinthBenchmarkSpec(
        season_id=season_id,
        pilot_manifest_sha256=pilot.manifest_sha256,
        main_manifest_sha256=main.manifest_sha256,
        protocol_prompt_sha256=labyrinth_protocol_prompt_sha256(),
        skill_id=MAZE_NAVIGATION_SKILL_ID,
        skill_sha256=maze_navigation_skill_sha256(),
    )


@dataclass(frozen=True)
class ScheduledRace:
    race_id: str
    phase: Phase
    map_id: str
    map_sha256: str
    difficulty: str
    vision_depth: VisionDepth
    repetition: int
    skill_mode: SkillMode
    seats: tuple[str, str, str]
    participant_call_budget: int
    maximum_ticks: int

    def __post_init__(self) -> None:
        if not isinstance(self.race_id, str) or _SAFE_ID.fullmatch(self.race_id) is None:
            raise BenchmarkSpecError("scheduled race id is invalid")
        if self.phase not in {"pilot", "baseline", "skill"}:
            raise BenchmarkSpecError("scheduled race phase is invalid")
        if not isinstance(self.map_id, str) or _SAFE_ID.fullmatch(self.map_id) is None:
            raise BenchmarkSpecError("scheduled map id is invalid")
        if not isinstance(self.map_sha256, str) or _SHA256.fullmatch(self.map_sha256) is None:
            raise BenchmarkSpecError("scheduled map hash is invalid")
        if self.difficulty not in DIFFICULTIES or self.vision_depth not in VISION_DEPTHS:
            raise BenchmarkSpecError("scheduled race condition is invalid")
        if isinstance(self.repetition, bool) or not isinstance(self.repetition, int):
            raise BenchmarkSpecError("scheduled repetition is invalid")
        if self.skill_mode not in {"none", MAZE_NAVIGATION_SKILL_ID}:
            raise BenchmarkSpecError("scheduled skill mode is invalid")
        if set(self.seats) != {item.model_id for item in DEFAULT_MODELS}:
            raise BenchmarkSpecError("scheduled seat rotation is invalid")
        if (
            isinstance(self.participant_call_budget, bool)
            or not isinstance(self.participant_call_budget, int)
            or self.participant_call_budget < 1
            or self.maximum_ticks != 4 * self.participant_call_budget
        ):
            raise BenchmarkSpecError("scheduled race budgets are invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "race_id": self.race_id,
            "phase": self.phase,
            "map_id": self.map_id,
            "map_sha256": self.map_sha256,
            "difficulty": self.difficulty,
            "vision_depth": self.vision_depth,
            "repetition": self.repetition,
            "skill_mode": self.skill_mode,
            "seats": {
                participant_id: self.seats[index]
                for index, participant_id in enumerate(PARTICIPANTS)
            },
            "participant_call_budget": self.participant_call_budget,
            "maximum_ticks": self.maximum_ticks,
        }

    @classmethod
    def from_dict(cls, value: object) -> ScheduledRace:
        fields = {
            "race_id",
            "phase",
            "map_id",
            "map_sha256",
            "difficulty",
            "vision_depth",
            "repetition",
            "skill_mode",
            "seats",
            "participant_call_budget",
            "maximum_ticks",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise BenchmarkSpecError("scheduled race fields are invalid")
        seats = value["seats"]
        if not isinstance(seats, Mapping) or tuple(seats) != PARTICIPANTS:
            raise BenchmarkSpecError("scheduled race seats are invalid")
        arguments = dict(value)
        arguments["seats"] = tuple(seats[item] for item in PARTICIPANTS)
        return cls(**arguments)


@dataclass(frozen=True)
class BenchmarkSchedule:
    season_id: str
    phase: Phase
    spec_sha256: str
    map_manifest_sha256: str
    races: tuple[ScheduledRace, ...]
    execution_order_seed: int
    selected_vision_depth: VisionDepth | None = None
    schema_version: str = SCHEDULE_VERSION
    schedule_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEDULE_VERSION:
            raise BenchmarkSpecError("benchmark schedule version is invalid")
        if not isinstance(self.season_id, str) or _SAFE_ID.fullmatch(self.season_id) is None:
            raise BenchmarkSpecError("benchmark schedule season id is invalid")
        if self.phase not in {"pilot", "baseline", "skill"}:
            raise BenchmarkSpecError("benchmark schedule phase is invalid")
        if (
            isinstance(self.execution_order_seed, bool)
            or not isinstance(self.execution_order_seed, int)
            or self.execution_order_seed < 0
        ):
            raise BenchmarkSpecError("benchmark execution order seed is invalid")
        if _SHA256.fullmatch(self.spec_sha256) is None or _SHA256.fullmatch(
            self.map_manifest_sha256
        ) is None:
            raise BenchmarkSpecError("benchmark schedule binding is invalid")
        expected_counts = {"pilot": 120, "baseline": 600, "skill": 120}
        if len(self.races) != expected_counts[self.phase]:
            raise BenchmarkSpecError("benchmark schedule race count is invalid")
        if any(item.phase != self.phase for item in self.races):
            raise BenchmarkSpecError("benchmark schedule contains another phase")
        if len({item.race_id for item in self.races}) != len(self.races):
            raise BenchmarkSpecError("benchmark schedule race ids are not unique")
        if self.phase == "skill":
            if self.selected_vision_depth not in VISION_DEPTHS or any(
                item.vision_depth != self.selected_vision_depth for item in self.races
            ):
                raise BenchmarkSpecError("skill schedule selected depth is invalid")
        elif self.selected_vision_depth is not None:
            raise BenchmarkSpecError("baseline schedule cannot select a skill depth")
        self._validate_factorial_shape()
        canonical = sorted(
            self.races,
            key=lambda item: (
                item.map_id,
                VISION_DEPTHS.index(item.vision_depth),
                item.repetition,
            ),
        )
        random.Random(self.execution_order_seed).shuffle(canonical)
        if tuple(canonical) != self.races:
            raise BenchmarkSpecError("benchmark execution order differs")
        expected_hash = hashlib.sha256(canonical_json_bytes(self._hash_body())).hexdigest()
        if self.schedule_sha256:
            if self.schedule_sha256 != expected_hash:
                raise BenchmarkSpecError("benchmark schedule hash differs")
        else:
            object.__setattr__(self, "schedule_sha256", expected_hash)

    def _validate_factorial_shape(self) -> None:
        map_count = 12 if self.phase == "pilot" else 40
        repetitions = {
            "pilot": PILOT_REPETITIONS,
            "baseline": BASELINE_REPETITIONS,
            "skill": SKILL_REPETITIONS,
        }[self.phase]
        visions: tuple[VisionDepth, ...] = (
            (self.selected_vision_depth,)
            if self.phase == "skill"
            else VISION_DEPTHS
        )
        map_ids = tuple(sorted({item.map_id for item in self.races}))
        if len(map_ids) != map_count:
            raise BenchmarkSpecError("benchmark schedule map balance is invalid")
        expected_maps_per_difficulty = map_count // len(DIFFICULTIES)
        map_difficulties = {
            map_id: {item.difficulty for item in self.races if item.map_id == map_id}
            for map_id in map_ids
        }
        if any(len(values) != 1 for values in map_difficulties.values()) or any(
            sum(next(iter(values)) == difficulty for values in map_difficulties.values())
            != expected_maps_per_difficulty
            for difficulty in DIFFICULTIES
        ):
            raise BenchmarkSpecError("benchmark schedule difficulty balance is invalid")
        model_ids = tuple(item.model_id for item in DEFAULT_MODELS)
        for map_index, map_id in enumerate(map_ids):
            rows = [item for item in self.races if item.map_id == map_id]
            if len(rows) != len(visions) * repetitions:
                raise BenchmarkSpecError("benchmark per-map race count is invalid")
            if len({item.map_sha256 for item in rows}) != 1 or len(
                {item.participant_call_budget for item in rows}
            ) != 1:
                raise BenchmarkSpecError("benchmark per-map bindings differ")
            actual_cells = {(item.vision_depth, item.repetition) for item in rows}
            expected_cells = {
                (vision, repetition)
                for vision in visions
                for repetition in range(1, repetitions + 1)
            }
            if actual_cells != expected_cells or len(actual_cells) != len(rows):
                raise BenchmarkSpecError("benchmark factorial cells are invalid")
            for row in rows:
                expected_skill_mode = (
                    MAZE_NAVIGATION_SKILL_ID if self.phase == "skill" else "none"
                )
                vision_label = "inf" if row.vision_depth == "infinite" else str(row.vision_depth)
                expected_race_id = (
                    f"{self.phase}-{row.map_id}-v{vision_label}-r{row.repetition}"
                )
                if row.skill_mode != expected_skill_mode or row.race_id != expected_race_id:
                    raise BenchmarkSpecError("benchmark race identity or skill condition differs")
                canonical_vision_index = VISION_DEPTHS.index(row.vision_depth)
                rotation = (
                    map_index + canonical_vision_index + row.repetition - 1
                ) % len(model_ids)
                expected_seats = tuple(
                    model_ids[(seat + rotation) % len(model_ids)]
                    for seat in range(len(PARTICIPANTS))
                )
                if row.seats != expected_seats:
                    raise BenchmarkSpecError("benchmark seat rotation differs")

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "season_id": self.season_id,
            "phase": self.phase,
            "spec_sha256": self.spec_sha256,
            "map_manifest_sha256": self.map_manifest_sha256,
            "selected_vision_depth": self.selected_vision_depth,
            "execution_order_seed": self.execution_order_seed,
            "races": [item.as_dict() for item in self.races],
        }

    def as_dict(self) -> dict[str, object]:
        return {**self._hash_body(), "schedule_sha256": self.schedule_sha256}

    @classmethod
    def from_dict(cls, value: object) -> BenchmarkSchedule:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "season_id",
            "phase",
            "spec_sha256",
            "map_manifest_sha256",
            "selected_vision_depth",
            "execution_order_seed",
            "races",
            "schedule_sha256",
        }:
            raise BenchmarkSpecError("benchmark schedule fields are invalid")
        races = value["races"]
        if not isinstance(races, list):
            raise BenchmarkSpecError("benchmark schedule races are invalid")
        arguments = dict(value)
        arguments["races"] = tuple(ScheduledRace.from_dict(item) for item in races)
        return cls(**arguments)


def build_schedule(
    spec: LabyrinthBenchmarkSpec,
    maps: MazeSuiteManifest,
    phase: Phase,
    *,
    selected_vision_depth: VisionDepth | None = None,
) -> BenchmarkSchedule:
    """Build the exact pilot, baseline, or post-baseline skill schedule."""

    if phase == "pilot":
        if maps.suite != "pilot":
            raise BenchmarkSpecError("pilot schedule needs pilot maps")
        repetitions = spec.pilot_repetitions
        visions: Sequence[VisionDepth] = spec.vision_depths
        skill_mode: SkillMode = "none"
    elif phase == "baseline":
        if maps.suite != "main":
            raise BenchmarkSpecError("baseline schedule needs main maps")
        repetitions = spec.baseline_repetitions
        visions = spec.vision_depths
        skill_mode = "none"
    elif phase == "skill":
        if maps.suite != "main" or selected_vision_depth not in VISION_DEPTHS:
            raise BenchmarkSpecError("skill schedule needs main maps and a selected depth")
        repetitions = spec.skill_repetitions
        visions = (selected_vision_depth,)
        skill_mode = MAZE_NAVIGATION_SKILL_ID
    else:
        raise BenchmarkSpecError("benchmark phase is invalid")

    races = []
    for map_index, maze in enumerate(maps.maps):
        for vision in visions:
            canonical_vision_index = VISION_DEPTHS.index(vision)
            for repetition in range(1, repetitions + 1):
                # Phase-independent rotation lets every skill episode pair with the exact same
                # participant seat in its selected-depth baseline episode.
                rotation = (
                    map_index + canonical_vision_index + repetition - 1
                ) % len(spec.models)
                seats = tuple(
                    spec.models[(seat + rotation) % len(spec.models)].model_id
                    for seat in range(len(PARTICIPANTS))
                )
                vision_label = "inf" if vision == "infinite" else str(vision)
                race_id = f"{phase}-{maze.map_id}-v{vision_label}-r{repetition}"
                races.append(
                    ScheduledRace(
                        race_id=race_id,
                        phase=phase,
                        map_id=maze.map_id,
                        map_sha256=maze.map_sha256,
                        difficulty=maze.difficulty,
                        vision_depth=vision,
                        repetition=repetition,
                        skill_mode=skill_mode,
                        seats=seats,
                        participant_call_budget=maze.participant_call_budget,
                        maximum_ticks=4 * maze.participant_call_budget,
                    )
                )
    execution_order_seed = schedule_order_seed(
        spec.bootstrap_seed,
        phase,
        selected_vision_depth=selected_vision_depth,
    )
    random.Random(execution_order_seed).shuffle(races)
    return BenchmarkSchedule(
        season_id=spec.season_id,
        phase=phase,
        spec_sha256=spec.spec_sha256,
        map_manifest_sha256=maps.manifest_sha256,
        races=tuple(races),
        execution_order_seed=execution_order_seed,
        selected_vision_depth=selected_vision_depth if phase == "skill" else None,
    )


def schedule_order_seed(
    bootstrap_seed: int, phase: Phase, *, selected_vision_depth: VisionDepth | None = None
) -> int:
    if phase not in {"pilot", "baseline", "skill"}:
        raise BenchmarkSpecError("benchmark phase is invalid")
    material = canonical_json_bytes(
        ["labyrinth-execution-order-v1", bootstrap_seed, phase, selected_vision_depth]
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:6], "big")


__all__ = [
    "ANALYSIS_VERSION",
    "BASELINE_REPETITIONS",
    "BENCHMARK_VERSION",
    "BOOTSTRAP_ITERATIONS",
    "BenchmarkModelSpec",
    "BenchmarkSchedule",
    "BenchmarkSpecError",
    "DEFAULT_MODELS",
    "DIFFICULTIES",
    "LabyrinthBenchmarkSpec",
    "MAIN_MAPS_PER_DIFFICULTY",
    "MAP_SUITE_VERSION",
    "MazeSuiteManifest",
    "PARTICIPANTS",
    "PILOT_MAPS_PER_DIFFICULTY",
    "RESULT_VERSION",
    "SCHEDULE_VERSION",
    "ScheduledRace",
    "VISION_DEPTHS",
    "VisionDepth",
    "build_benchmark_spec",
    "build_schedule",
    "generate_map_suite",
    "schedule_order_seed",
]
