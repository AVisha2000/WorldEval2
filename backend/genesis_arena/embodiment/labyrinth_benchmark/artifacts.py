"""Resumable, atomic, credential-safe Labyrinth benchmark artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..maze_maps import MazeMapSpec
from ..protocol import canonical_json_bytes, strict_json_loads
from ..providers.contracts import ProviderFailureKind
from .spec import (
    PARTICIPANTS,
    RESULT_VERSION,
    BenchmarkSchedule,
    LabyrinthBenchmarkSpec,
    MazeSuiteManifest,
    schedule_order_seed,
)

_PROTECTED_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "bearer_token",
        "chain_of_thought",
        "credential",
        "credentials",
        "episode_memory",
        "hidden_state",
        "instructions",
        "navigation_memory",
        "observation_json",
        "prompt",
        "prompt_text",
        "provider_request",
        "provider_response",
        "raw_output",
        "scratchpad",
        "scratchpad_update",
        "system_prompt",
    }
)
_SECRET_PATTERNS = (
    re.compile(rb"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(rb"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{12,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "season_id",
        "spec_sha256",
        "status",
        "active_phase",
        "completed_races",
        "infrastructure_voids",
        "infrastructure_voids_by_phase",
        "pending_retry",
        "last_error",
    }
)
_STATE_STATUSES = frozenset(
    {
        "generated",
        "running",
        "pilot_complete",
        "baseline_complete",
        "skill_complete",
        "stopped",
    }
)
_PROTECTED_KEY_FRAGMENTS = (
    "apikey",
    "authorization",
    "bearertoken",
    "chainofthought",
    "credential",
    "episodememory",
    "hiddenstate",
    "instructions",
    "navigationmemory",
    "observationjson",
    "prompt",
    "providerrequest",
    "providerresponse",
    "rawoutput",
    "rawrequest",
    "rawresponse",
    "scratchpad",
)


class BenchmarkArtifactError(RuntimeError):
    """A benchmark artifact cannot be safely written, loaded, or resumed."""


def assert_public_safe(value: object, *, path: str = "$") -> None:
    """Reject protected controller material while permitting hash-only bindings."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise BenchmarkArtifactError(f"non-string artifact key at {path}")
            normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
            normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").casefold()
            compact = normalized.replace("_", "")
            if normalized.endswith("_sha256"):
                if not isinstance(child, str) or re.fullmatch(r"[0-9a-f]{64}", child) is None:
                    raise BenchmarkArtifactError(f"invalid digest field at {path}.{key}")
            elif normalized in _PROTECTED_KEYS or any(
                fragment in compact for fragment in _PROTECTED_KEY_FRAGMENTS
            ):
                raise BenchmarkArtifactError(f"protected benchmark field at {path}.{key}")
            assert_public_safe(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_public_safe(child, path=f"{path}[{index}]")
    elif isinstance(value, bytes):
        raise BenchmarkArtifactError(f"binary material is forbidden at {path}")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise BenchmarkArtifactError(f"unsupported artifact value at {path}")
    if isinstance(value, str):
        scan_secret_bytes(value.encode("utf-8"))


def scan_secret_bytes(payload: bytes) -> None:
    if not isinstance(payload, bytes):
        raise TypeError("artifact payload must be immutable bytes")
    if any(pattern.search(payload) for pattern in _SECRET_PATTERNS):
        raise BenchmarkArtifactError("credential-like material found in benchmark artifact")


def atomic_write(path: Path, payload: bytes, *, immutable: bool = False) -> None:
    """Write one file atomically; immutable files may only be replayed byte-for-byte."""

    path = Path(path)
    scan_secret_bytes(payload)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if immutable:
            try:
                if path.read_bytes() == payload:
                    return
            except OSError as error:
                raise BenchmarkArtifactError("immutable artifact cannot be read") from error
            raise BenchmarkArtifactError(f"immutable artifact differs: {path.name}")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise BenchmarkArtifactError(f"could not atomically write {path.name}") from error


def atomic_write_json(path: Path, value: object, *, immutable: bool = False) -> None:
    assert_public_safe(value)
    atomic_write(path, canonical_json_bytes(value), immutable=immutable)


def load_canonical_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = Path(path).read_bytes()
        scan_secret_bytes(payload)
        value = strict_json_loads(payload)
    except (OSError, ValueError, TypeError) as error:
        raise BenchmarkArtifactError(f"benchmark artifact cannot be loaded: {path.name}") from error
    if not isinstance(value, Mapping) or canonical_json_bytes(value) != payload:
        raise BenchmarkArtifactError(f"benchmark artifact is not canonical: {path.name}")
    assert_public_safe(value)
    return value


def export_curated_report(
    store: BenchmarkArtifactStore, destination: Path
) -> Mapping[str, object]:
    """Export only frozen inputs and aggregate/report artifacts to a commit-safe directory."""

    store.audit_public_tree()
    destination = Path(destination).resolve()
    if destination == store.root or store.root in destination.parents:
        raise BenchmarkArtifactError("curated export must be outside the raw season directory")
    analysis = load_canonical_json(store.analysis_path)
    spec = store.load_specification()
    if analysis.get("season_id") != store.season_id or analysis.get(
        "spec_sha256"
    ) != spec.spec_sha256:
        raise BenchmarkArtifactError("curated analysis binding differs")
    schedule_hashes = analysis.get("schedule_sha256s")
    if not isinstance(schedule_hashes, Mapping) or any(
        schedule_hashes.get(phase) != store.load_schedule(phase).schedule_sha256
        for phase in ("pilot", "baseline", "skill")
    ):
        raise BenchmarkArtifactError("curated schedule bindings differ")
    selected = [
        store.specification_path,
        store.map_manifest_path("pilot"),
        store.map_manifest_path("main"),
        store.schedule_path("pilot"),
        store.schedule_path("baseline"),
        store.schedule_path("skill"),
        store.analysis_path,
        store.aggregate_csv_path,
        store.paired_csv_path,
        store.report_path,
        store.root / "report" / "manifest.json",
        *(sorted((store.root / "report" / "figures").glob("*.png"))),
        *(sorted((store.root / "report" / "figures").glob("*.svg"))),
    ]
    if any(not path.is_file() for path in selected):
        raise BenchmarkArtifactError("complete analysis and report are required for curated export")
    files = []
    for source in selected:
        payload = source.read_bytes()
        scan_secret_bytes(payload)
        if source.suffix == ".json":
            value = strict_json_loads(payload)
            assert_public_safe(value)
            if canonical_json_bytes(value) != payload:
                raise BenchmarkArtifactError("curated JSON source is not canonical")
        relative = source.relative_to(store.root)
        atomic_write(destination / relative, payload, immutable=True)
        files.append(
            {
                "path": relative.as_posix(),
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest: dict[str, object] = {
        "schema_version": "worldarena/labyrinth-benchmark-curated-export/1",
        "season_id": store.season_id,
        "files": files,
        "excluded": ["per-race-results", "runner-state", "pilot-projection"],
    }
    manifest["export_sha256"] = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    atomic_write_json(destination / "curated-manifest.json", manifest, immutable=True)
    return manifest


class BenchmarkArtifactStore:
    """One season directory with immutable inputs and resumable per-race outputs."""

    def __init__(self, output_root: Path, season_id: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", season_id) is None:
            raise BenchmarkArtifactError("benchmark season id is invalid")
        self.output_root = Path(output_root).resolve()
        self.season_id = season_id
        self.root = self.output_root / season_id

    @property
    def specification_path(self) -> Path:
        return self.root / "specification.json"

    def map_manifest_path(self, suite: str) -> Path:
        if suite not in {"pilot", "main"}:
            raise BenchmarkArtifactError("benchmark map suite is invalid")
        return self.root / "maps" / f"{suite}.json"

    def schedule_path(self, phase: str) -> Path:
        if phase not in {"pilot", "baseline", "skill"}:
            raise BenchmarkArtifactError("benchmark phase is invalid")
        return self.root / "schedules" / f"{phase}.json"

    def result_path(self, phase: str, race_id: str) -> Path:
        if phase not in {"pilot", "baseline", "skill"} or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", race_id
        ) is None:
            raise BenchmarkArtifactError("benchmark result identity is invalid")
        return self.root / "results" / phase / f"{race_id}.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def analysis_path(self) -> Path:
        return self.root / "analysis" / "analysis.json"

    @property
    def pilot_projection_path(self) -> Path:
        return self.root / "pilot-projection.json"

    @property
    def aggregate_csv_path(self) -> Path:
        return self.root / "analysis" / "aggregate.csv"

    @property
    def paired_csv_path(self) -> Path:
        return self.root / "analysis" / "paired-skill.csv"

    @property
    def report_path(self) -> Path:
        return self.root / "report" / "index.html"

    def initialize(
        self,
        spec: LabyrinthBenchmarkSpec,
        pilot: MazeSuiteManifest,
        main: MazeSuiteManifest,
        schedules: Iterable[BenchmarkSchedule],
    ) -> None:
        if spec.season_id != self.season_id:
            raise BenchmarkArtifactError("benchmark season identity differs")
        if spec.pilot_manifest_sha256 != pilot.manifest_sha256 or (
            spec.main_manifest_sha256 != main.manifest_sha256
        ):
            raise BenchmarkArtifactError("benchmark spec map bindings differ")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write_json(self.specification_path, spec.as_dict(), immutable=True)
        atomic_write_json(self.map_manifest_path("pilot"), pilot.as_dict(), immutable=True)
        atomic_write_json(self.map_manifest_path("main"), main.as_dict(), immutable=True)
        schedule_list = tuple(schedules)
        for schedule in schedule_list:
            if schedule.season_id != self.season_id or schedule.spec_sha256 != spec.spec_sha256:
                raise BenchmarkArtifactError("benchmark schedule binding differs")
            expected_order_seed = schedule_order_seed(
                spec.bootstrap_seed,
                schedule.phase,
                selected_vision_depth=schedule.selected_vision_depth,
            )
            if schedule.execution_order_seed != expected_order_seed:
                raise BenchmarkArtifactError("benchmark execution order seed differs")
            self._validate_schedule_maps(
                schedule, pilot if schedule.phase == "pilot" else main
            )
            atomic_write_json(
                self.schedule_path(schedule.phase), schedule.as_dict(), immutable=True
            )
        if not self.state_path.exists():
            self.write_state(
                {
                    "schema_version": "worldarena/labyrinth-benchmark-state/1",
                    "season_id": self.season_id,
                    "spec_sha256": spec.spec_sha256,
                    "status": "generated",
                    "active_phase": None,
                    "completed_races": 0,
                    "infrastructure_voids": 0,
                    "infrastructure_voids_by_phase": {
                        "pilot": 0,
                        "baseline": 0,
                        "skill": 0,
                    },
                    "pending_retry": None,
                    "last_error": None,
                }
            )

    def load_specification(self) -> LabyrinthBenchmarkSpec:
        return LabyrinthBenchmarkSpec.from_dict(load_canonical_json(self.specification_path))

    def load_map_manifest(self, suite: str) -> MazeSuiteManifest:
        return MazeSuiteManifest.from_dict(load_canonical_json(self.map_manifest_path(suite)))

    def load_schedule(self, phase: str) -> BenchmarkSchedule:
        schedule = BenchmarkSchedule.from_dict(load_canonical_json(self.schedule_path(phase)))
        spec = self.load_specification()
        if schedule.season_id != self.season_id or schedule.spec_sha256 != spec.spec_sha256:
            raise BenchmarkArtifactError("benchmark loaded schedule binding differs")
        expected_order_seed = schedule_order_seed(
            spec.bootstrap_seed,
            schedule.phase,
            selected_vision_depth=schedule.selected_vision_depth,
        )
        if schedule.execution_order_seed != expected_order_seed:
            raise BenchmarkArtifactError("benchmark execution order seed differs")
        maps = self.load_map_manifest("pilot" if phase == "pilot" else "main")
        self._validate_schedule_maps(schedule, maps)
        return schedule

    def save_schedule(self, schedule: BenchmarkSchedule) -> None:
        spec = self.load_specification()
        if schedule.season_id != self.season_id or schedule.spec_sha256 != spec.spec_sha256:
            raise BenchmarkArtifactError("benchmark schedule binding differs")
        if schedule.execution_order_seed != schedule_order_seed(
            spec.bootstrap_seed,
            schedule.phase,
            selected_vision_depth=schedule.selected_vision_depth,
        ):
            raise BenchmarkArtifactError("benchmark execution order seed differs")
        maps = self.load_map_manifest("pilot" if schedule.phase == "pilot" else "main")
        self._validate_schedule_maps(schedule, maps)
        atomic_write_json(self.schedule_path(schedule.phase), schedule.as_dict(), immutable=True)

    def save_result(self, schedule: BenchmarkSchedule, result: Mapping[str, object]) -> None:
        race_id = self.validate_result(schedule, result)
        atomic_write_json(
            self.result_path(schedule.phase, race_id), result, immutable=True
        )

    def validate_result(
        self, schedule: BenchmarkSchedule, result: Mapping[str, object]
    ) -> str:
        """Validate a new or resumed result against its exact frozen schedule cell."""

        expected = {
            "schema_version",
            "season_id",
            "schedule_sha256",
            "race_id",
            "phase",
            "map_id",
            "map_sha256",
            "difficulty",
            "vision_depth",
            "repetition",
            "skill_mode",
            "participant_call_budget",
            "wall_time_ms",
            "attempt",
            "episodes",
        }
        if set(result) != expected or result.get("schema_version") != RESULT_VERSION:
            raise BenchmarkArtifactError("benchmark race result fields are invalid")
        if not _is_non_negative_int(result.get("wall_time_ms")) or result.get(
            "attempt"
        ) not in {1, 2}:
            raise BenchmarkArtifactError("benchmark race execution metadata is invalid")
        race_id = result.get("race_id")
        if (
            not isinstance(race_id, str)
            or result.get("season_id") != self.season_id
            or result.get("schedule_sha256") != schedule.schedule_sha256
            or result.get("phase") != schedule.phase
        ):
            raise BenchmarkArtifactError("benchmark race result binding differs")
        race = next((item for item in schedule.races if item.race_id == race_id), None)
        if race is None or any(
            result.get(field) != getattr(race, field)
            for field in (
                "map_id",
                "map_sha256",
                "difficulty",
                "vision_depth",
                "repetition",
                "skill_mode",
                "participant_call_budget",
            )
        ):
            raise BenchmarkArtifactError("benchmark result differs from schedule")
        maps = self.load_map_manifest("pilot" if schedule.phase == "pilot" else "main")
        maze = next((item for item in maps.maps if item.map_id == race.map_id), None)
        if maze is None:
            raise BenchmarkArtifactError("benchmark result map is unavailable")
        episodes = result.get("episodes")
        if not isinstance(episodes, list) or len(episodes) != 3:
            raise BenchmarkArtifactError("benchmark result episodes are invalid")
        expected_models = {
            participant_id: race.seats[index]
            for index, participant_id in enumerate(PARTICIPANTS)
        }
        spec_models = {
            item.model_id: item.provider_model for item in self.load_specification().models
        }
        if {item.get("participant_id") for item in episodes if isinstance(item, Mapping)} != set(
            PARTICIPANTS
        ) or {item.get("model_id") for item in episodes if isinstance(item, Mapping)} != set(
            expected_models.values()
        ):
            raise BenchmarkArtifactError("benchmark result episode identities differ")
        for episode in episodes:
            self._validate_episode(
                episode,
                expected_model=expected_models[str(episode.get("participant_id"))],
                expected_provider_model=spec_models[
                    expected_models[str(episode.get("participant_id"))]
                ],
                participant_call_budget=race.participant_call_budget,
                maximum_ticks=race.maximum_ticks,
                maze=maze,
            )
        assert_public_safe(result)
        return race_id

    def load_bound_result(
        self, schedule: BenchmarkSchedule, race_id: str
    ) -> Mapping[str, Any] | None:
        result = self.load_result(schedule.phase, race_id)
        if result is not None:
            if result.get("race_id") != race_id:
                raise BenchmarkArtifactError("benchmark result filename binding differs")
            self.validate_result(schedule, result)
        return result

    def load_result(self, phase: str, race_id: str) -> Mapping[str, Any] | None:
        path = self.result_path(phase, race_id)
        if not path.is_file():
            return None
        return load_canonical_json(path)

    def iter_results(self, phase: str | None = None) -> tuple[Mapping[str, Any], ...]:
        phases = (phase,) if phase is not None else ("pilot", "baseline", "skill")
        output = []
        seen_race_ids: set[str] = set()
        for current in phases:
            directory = self.root / "results" / current
            if not directory.is_dir():
                continue
            schedule = self.load_schedule(current)
            for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
                result = load_canonical_json(path)
                race_id = result.get("race_id")
                if race_id != path.stem or race_id in seen_race_ids:
                    raise BenchmarkArtifactError("benchmark result filename binding differs")
                self.validate_result(schedule, result)
                seen_race_ids.add(str(race_id))
                output.append(result)
        return tuple(output)

    def write_state(self, state: Mapping[str, object]) -> None:
        self._validate_state(state)
        atomic_write_json(self.state_path, state)

    def load_state(self) -> Mapping[str, Any]:
        state = load_canonical_json(self.state_path)
        self._validate_state(state)
        return state

    def _validate_state(self, state: Mapping[str, object]) -> None:
        if set(state) != _STATE_FIELDS or state.get("schema_version") != (
            "worldarena/labyrinth-benchmark-state/1"
        ):
            raise BenchmarkArtifactError("benchmark state fields are invalid")
        spec = self.load_specification()
        if state.get("season_id") != self.season_id or state.get(
            "spec_sha256"
        ) != spec.spec_sha256:
            raise BenchmarkArtifactError("benchmark state binding differs")
        status = state.get("status")
        active_phase = state.get("active_phase")
        if status not in _STATE_STATUSES or active_phase not in {
            None,
            "pilot",
            "baseline",
            "skill",
        }:
            raise BenchmarkArtifactError("benchmark state lifecycle is invalid")
        if (status == "running") != (active_phase is not None):
            if not (status == "stopped" and active_phase is not None):
                raise BenchmarkArtifactError("benchmark state active phase differs")
        counts = state.get("infrastructure_voids_by_phase")
        if not isinstance(counts, Mapping) or set(counts) != {
            "pilot",
            "baseline",
            "skill",
        } or any(not _is_non_negative_int(count) for count in counts.values()):
            raise BenchmarkArtifactError("benchmark state infrastructure counts are invalid")
        infrastructure_voids = state.get("infrastructure_voids")
        if (
            not _is_non_negative_int(state.get("completed_races"))
            or int(state["completed_races"]) > 840
            or not _is_non_negative_int(infrastructure_voids)
            or infrastructure_voids != sum(int(count) for count in counts.values())
        ):
            raise BenchmarkArtifactError("benchmark state counters are invalid")
        error = state.get("last_error")
        if error is not None and (not isinstance(error, str) or not error or len(error) > 200):
            raise BenchmarkArtifactError("benchmark state error is invalid")
        pending = state.get("pending_retry")
        if pending is not None:
            if (
                not isinstance(pending, Mapping)
                or set(pending) != {"phase", "race_id", "retry_started"}
                or pending.get("phase") not in {"pilot", "baseline", "skill"}
                or not isinstance(pending.get("race_id"), str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", pending["race_id"])
                is None
                or not isinstance(pending.get("retry_started"), bool)
            ):
                raise BenchmarkArtifactError("benchmark pending retry is invalid")

    def audit_public_tree(self) -> Mapping[str, object]:
        """Scan every durable artifact and return a safe inventory digest."""

        files = []
        if not self.root.is_dir():
            raise BenchmarkArtifactError("benchmark season does not exist")
        for path in sorted(self.root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            scan_secret_bytes(payload)
            if path.suffix == ".json":
                assert_public_safe(strict_json_loads(payload))
            files.append(
                {
                    "path": path.relative_to(self.root).as_posix(),
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        return {
            "schema_version": "worldarena/labyrinth-benchmark-public-audit/1",
            "season_id": self.season_id,
            "files": files,
        }

    @staticmethod
    def _validate_schedule_maps(
        schedule: BenchmarkSchedule, maps: MazeSuiteManifest
    ) -> None:
        if schedule.map_manifest_sha256 != maps.manifest_sha256:
            raise BenchmarkArtifactError("benchmark schedule map manifest differs")
        lookup = {item.map_id: item for item in maps.maps}
        for race in schedule.races:
            maze = lookup.get(race.map_id)
            if maze is None or (
                race.map_sha256,
                race.difficulty,
                race.participant_call_budget,
                race.maximum_ticks,
            ) != (
                maze.map_sha256,
                maze.difficulty,
                maze.participant_call_budget,
                4 * maze.participant_call_budget,
            ):
                raise BenchmarkArtifactError("benchmark scheduled map binding differs")

    @staticmethod
    def _validate_episode(
        value: object,
        *,
        expected_model: str,
        expected_provider_model: str,
        participant_call_budget: int,
        maximum_ticks: int,
        maze: MazeMapSpec,
    ) -> None:
        fields = {
            "participant_id",
            "model_id",
            "provider_model",
            "completed",
            "finish_tick",
            "calls",
            "charged_calls",
            "distance_cells",
            "path",
            "shortest_path_cells",
            "path_efficiency_basis_points",
            "unique_corridor_cells",
            "repeated_corridor_cells",
            "repeated_cell_basis_points",
            "invalid_decisions",
            "invalid_decision_basis_points",
            "waiting_windows",
            "wait_basis_points",
            "corridor_commands",
            "single_cell_commands",
            "corridor_command_basis_points",
            "cells_moved",
            "cells_per_call_milli",
            "backtrack_commands",
            "successful_backtracks",
            "backtrack_success_basis_points",
            "recovery_opportunities",
            "successful_recoveries",
            "recovery_basis_points",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "total_tokens",
            "token_telemetry_complete",
            "cache_write_telemetry_complete",
            "latency_ms",
            "latency_telemetry_complete",
            "peak_memory_bytes",
            "memory_evictions",
            "provider_failures",
            "decision_trace",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise BenchmarkArtifactError("benchmark result episode fields are invalid")
        if value.get("model_id") != expected_model or value.get(
            "provider_model"
        ) != expected_provider_model:
            raise BenchmarkArtifactError("benchmark result model identity differs")
        if (
            not isinstance(value.get("completed"), bool)
            or not isinstance(value.get("token_telemetry_complete"), bool)
            or not isinstance(value.get("cache_write_telemetry_complete"), bool)
            or not isinstance(value.get("latency_telemetry_complete"), bool)
        ):
            raise BenchmarkArtifactError("benchmark episode boolean fields are invalid")
        completed = bool(value["completed"])
        finish_tick = value.get("finish_tick")
        if (completed and not _is_non_negative_int(finish_tick)) or (
            not completed and finish_tick is not None
        ):
            raise BenchmarkArtifactError("benchmark episode finish state is invalid")
        if completed and int(finish_tick) > maximum_ticks:
            raise BenchmarkArtifactError("benchmark episode finish tick exceeds race limit")
        integer_fields = fields - {
            "participant_id",
            "model_id",
            "provider_model",
            "completed",
            "finish_tick",
            "token_telemetry_complete",
            "cache_write_telemetry_complete",
            "latency_telemetry_complete",
            "provider_failures",
            "decision_trace",
            "path",
        }
        if any(not _is_non_negative_int(value.get(name)) for name in integer_fields):
            raise BenchmarkArtifactError("benchmark episode metric is invalid")
        calls = int(value["calls"])
        if calls > participant_call_budget or value["charged_calls"] != (
            calls if completed else participant_call_budget
        ):
            raise BenchmarkArtifactError("benchmark episode charged calls differ")
        if value["total_tokens"] != value["input_tokens"] + value["output_tokens"]:
            raise BenchmarkArtifactError("benchmark episode token total differs")
        bounded_basis_points = (
            "path_efficiency_basis_points",
            "corridor_command_basis_points",
            "repeated_cell_basis_points",
            "invalid_decision_basis_points",
            "wait_basis_points",
            "backtrack_success_basis_points",
            "recovery_basis_points",
        )
        if value["peak_memory_bytes"] > 2048 or any(
            value[name] > 10_000 for name in bounded_basis_points
        ):
            raise BenchmarkArtifactError("benchmark episode bounded metric is invalid")
        if value["successful_backtracks"] > value["backtrack_commands"] or value[
            "successful_recoveries"
        ] > value["recovery_opportunities"]:
            raise BenchmarkArtifactError("benchmark episode recovery counts differ")
        if value["memory_evictions"] > calls:
            raise BenchmarkArtifactError("benchmark memory compaction count exceeds decisions")
        path = value.get("path")
        graph = maze.graph()
        if (
            not isinstance(path, list)
            or not path
            or any(
                not isinstance(cell, list)
                or len(cell) != 2
                or tuple(cell) not in graph
                for cell in path
            )
        ):
            raise BenchmarkArtifactError("benchmark episode path is invalid")
        cells = [tuple(cell) for cell in path]
        if cells[0] != maze.start or any(
            target not in graph[source] for source, target in zip(cells, cells[1:])
        ):
            raise BenchmarkArtifactError("benchmark episode path is not contiguous")
        if len(cells) - 1 != value["distance_cells"] or len(set(cells)) != value[
            "unique_corridor_cells"
        ] or len(cells) - len(set(cells)) != value["repeated_corridor_cells"]:
            raise BenchmarkArtifactError("benchmark episode path metrics differ")
        if value["shortest_path_cells"] != maze.metrics.shortest_path_cells:
            raise BenchmarkArtifactError("benchmark episode shortest path differs")
        if (completed and cells[-1] != maze.exit) or (not completed and cells[-1] == maze.exit):
            raise BenchmarkArtifactError("benchmark episode path completion differs")
        expected_path_efficiency = (
            0
            if not completed or value["distance_cells"] == 0
            else maze.metrics.shortest_path_cells * 10_000 // value["distance_cells"]
        )
        expected_repeated_rate = (
            0
            if value["distance_cells"] == 0
            else value["repeated_corridor_cells"] * 10_000 // value["distance_cells"]
        )
        if (
            value["path_efficiency_basis_points"] != expected_path_efficiency
            or value["repeated_cell_basis_points"] != expected_repeated_rate
        ):
            raise BenchmarkArtifactError("benchmark episode path-derived metrics differ")
        failures = value.get("provider_failures")
        allowed_failures = {item.value for item in ProviderFailureKind}
        if not isinstance(failures, Mapping) or any(
            key not in allowed_failures or not _is_non_negative_int(count) or count < 1
            for key, count in failures.items()
        ):
            raise BenchmarkArtifactError("benchmark episode provider failures are invalid")
        trace = value.get("decision_trace")
        if not isinstance(trace, list) or len(trace) != calls:
            raise BenchmarkArtifactError("benchmark episode decision trace is invalid")
        trace_failures: dict[str, int] = {}
        for expected_seq, decision in enumerate(trace):
            _validate_decision(decision, expected_seq)
            failure = decision["provider_failure"]
            if failure is not None:
                trace_failures[failure] = trace_failures.get(failure, 0) + 1
        if dict(failures) != trace_failures:
            raise BenchmarkArtifactError("benchmark episode failure counts differ")
        corridor_commands = sum(
            decision["movement_mode"] == "follow_corridor" for decision in trace
        )
        single_cell_commands = calls - corridor_commands
        cells_moved = sum(int(decision["cells_moved"]) for decision in trace)
        invalid_decisions = sum(
            decision["disposition"] in {"invalid", "provider_failure"}
            for decision in trace
        )
        waiting_windows = sum(int(decision["cells_moved"]) == 0 for decision in trace)
        backtrack_commands = sum(decision["passage_choice"] == "back" for decision in trace)
        successful_backtracks = sum(
            decision["passage_choice"] == "back" and int(decision["cells_moved"]) > 0
            for decision in trace
        )
        recovery_opportunities = sum(
            decision["disposition"] in {"invalid", "provider_failure"}
            for decision in trace[:-1]
        )
        successful_recoveries = sum(
            previous["disposition"] in {"invalid", "provider_failure"}
            and current["disposition"] == "accepted"
            and int(current["cells_moved"]) > 0
            for previous, current in zip(trace, trace[1:])
        )
        expected_counts = {
            "corridor_commands": corridor_commands,
            "single_cell_commands": single_cell_commands,
            "cells_moved": cells_moved,
            "invalid_decisions": invalid_decisions,
            "waiting_windows": waiting_windows,
            "backtrack_commands": backtrack_commands,
            "successful_backtracks": successful_backtracks,
            "recovery_opportunities": recovery_opportunities,
            "successful_recoveries": successful_recoveries,
        }
        if any(value[name] != expected for name, expected in expected_counts.items()):
            raise BenchmarkArtifactError("benchmark episode trace-derived counts differ")
        if cells_moved != value["distance_cells"]:
            raise BenchmarkArtifactError("benchmark episode trace and path distances differ")
        expected_rates = {
            "corridor_command_basis_points": _rate(corridor_commands, calls, 10_000),
            "cells_per_call_milli": _rate(cells_moved, calls, 1_000),
            "invalid_decision_basis_points": _rate(invalid_decisions, calls, 10_000),
            "wait_basis_points": _rate(waiting_windows, calls, 10_000),
            "backtrack_success_basis_points": _rate(
                successful_backtracks, backtrack_commands, 10_000
            ),
            "recovery_basis_points": _rate(
                successful_recoveries, recovery_opportunities, 10_000
            ),
        }
        if any(value[name] != expected for name, expected in expected_rates.items()):
            raise BenchmarkArtifactError("benchmark episode trace-derived rates differ")


def _is_non_negative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _rate(numerator: int, denominator: int, scale: int) -> int:
    return 0 if denominator == 0 else numerator * scale // denominator


def _validate_decision(value: object, expected_seq: int) -> None:
    fields = {
        "observation_seq",
        "disposition",
        "passage_choice",
        "movement_mode",
        "max_corridor_cells",
        "cells_moved",
        "stopped_because",
        "provider_failure",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise BenchmarkArtifactError("benchmark safe decision fields are invalid")
    if value.get("observation_seq") != expected_seq:
        raise BenchmarkArtifactError("benchmark safe decision sequence differs")
    if value.get("disposition") not in {"accepted", "invalid", "provider_failure", "wait"}:
        raise BenchmarkArtifactError("benchmark safe decision disposition is invalid")
    if value.get("passage_choice") not in {"left", "forward", "right", "back", "wait"}:
        raise BenchmarkArtifactError("benchmark safe decision passage is invalid")
    if value.get("movement_mode") not in {"single_cell", "follow_corridor"}:
        raise BenchmarkArtifactError("benchmark safe decision movement mode is invalid")
    if not _is_non_negative_int(value.get("cells_moved")) or not _is_non_negative_int(
        value.get("max_corridor_cells")
    ) or not 1 <= value["max_corridor_cells"] <= 256:
        raise BenchmarkArtifactError("benchmark safe decision cell counts are invalid")
    if value["cells_moved"] > value["max_corridor_cells"] or (
        value["movement_mode"] == "single_cell" and value["cells_moved"] > 1
    ):
        raise BenchmarkArtifactError("benchmark safe decision movement bounds differ")
    if value.get("stopped_because") not in {
        "dead_end_reached",
        "exit_reached",
        "junction_reached",
        "max_cells_reached",
        "origin_reached",
        "passage_unavailable",
        "revisited_cell_reached",
        "single_cell_complete",
        "waited",
    }:
        raise BenchmarkArtifactError("benchmark safe decision stop reason is invalid")
    failure = value.get("provider_failure")
    if failure is not None and failure not in {item.value for item in ProviderFailureKind}:
        raise BenchmarkArtifactError("benchmark safe decision failure is invalid")
    if (value["disposition"] == "provider_failure") != (failure is not None):
        raise BenchmarkArtifactError("benchmark safe decision failure binding differs")
    if value["disposition"] in {"invalid", "provider_failure"} and value["cells_moved"] != 0:
        raise BenchmarkArtifactError("benchmark failed decision moved cells")


__all__ = [
    "BenchmarkArtifactError",
    "BenchmarkArtifactStore",
    "assert_public_safe",
    "atomic_write",
    "atomic_write_json",
    "export_curated_report",
    "load_canonical_json",
    "scan_secret_bytes",
]
