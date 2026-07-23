"""Canonical, safe-to-project contracts for the WorldEval Lab.

This module deliberately contains no filesystem, provider, FastAPI, or Godot dependency.  A
future service can persist the canonical bytes, keep authority checkpoints in private storage,
and expose only :class:`ReplayProjection` / :class:`RaceCartridge` values to a browser.

The contracts use the repository's restricted RFC-8785 JSON profile.  They are immutable from a
caller's perspective: the stored representation is canonical bytes and every public mapping is
re-decoded before it is returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from ..protocol import (
    ProtocolValidationError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)

RUN_CONTRACT_SCHEMA_VERSION = "worldeval/lab-run-contract/1"
RUN_STATE_SCHEMA_VERSION = "worldeval/lab-run-state/1"
REPLAY_PROJECTION_SCHEMA_VERSION = "worldeval/lab-replay-projection/1"
RACE_CARTRIDGE_SCHEMA_VERSION = "worldeval/lab-race-cartridge/1"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_PATTERNS = (
    re.compile(rb"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    re.compile(rb"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{12,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

# This intentionally mirrors the benchmark artifact boundary, while adding ``observation`` and
# plural forms because projections are browser-visible rather than merely report-safe.
_PROTECTED_KEYS = frozenset(
    {
        "api_key",
        "api_keys",
        "authorization",
        "bearer_token",
        "chain_of_thought",
        "credential",
        "credentials",
        "episode_memory",
        "hidden_state",
        "instructions",
        "navigation_memory",
        "observation",
        "observations",
        "observation_json",
        "prompt",
        "prompts",
        "prompt_text",
        "provider_request",
        "provider_response",
        "raw_input",
        "raw_output",
        "raw_request",
        "raw_response",
        "scratchpad",
        "scratchpad_update",
        "secret",
        "secrets",
        "system_prompt",
        "user_prompt",
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
    "observation",
    "prompt",
    "providerrequest",
    "providerresponse",
    "rawinput",
    "rawoutput",
    "rawrequest",
    "rawresponse",
    "scratchpad",
    "secret",
)


class LabContractError(ValueError):
    """A Lab contract is malformed, unsafe to persist, or internally inconsistent."""


class RunMode(str, Enum):
    """The evidence posture selected when a run is launched."""

    DEMO = "demo"
    EXPLORATORY = "exploratory"
    SEALED_BENCHMARK = "sealed_benchmark"


class RunLifecycle(str, Enum):
    """Authority-owned lifecycle for one run; terminal states cannot be reopened."""

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SEALED = "sealed"
    VERIFIED = "verified"

    def can_transition_to(self, target: RunLifecycle) -> bool:
        """Return whether an authority state may move to ``target``."""

        if not isinstance(target, RunLifecycle):
            raise TypeError("target must be a RunLifecycle")
        return target in _LIFECYCLE_TRANSITIONS[self]


_LIFECYCLE_TRANSITIONS = {
    RunLifecycle.DRAFT: frozenset({RunLifecycle.QUEUED, RunLifecycle.CANCELLED}),
    RunLifecycle.QUEUED: frozenset(
        {RunLifecycle.RUNNING, RunLifecycle.CANCELLED, RunLifecycle.FAILED}
    ),
    RunLifecycle.RUNNING: frozenset(
        {
            RunLifecycle.CHECKPOINTED,
            RunLifecycle.COMPLETED,
            RunLifecycle.CANCELLED,
            RunLifecycle.FAILED,
        }
    ),
    RunLifecycle.CHECKPOINTED: frozenset(
        {
            RunLifecycle.RUNNING,
            RunLifecycle.COMPLETED,
            RunLifecycle.CANCELLED,
            RunLifecycle.FAILED,
        }
    ),
    RunLifecycle.COMPLETED: frozenset({RunLifecycle.SEALED}),
    RunLifecycle.FAILED: frozenset(),
    RunLifecycle.CANCELLED: frozenset(),
    RunLifecycle.SEALED: frozenset({RunLifecycle.VERIFIED}),
    RunLifecycle.VERIFIED: frozenset(),
}


def _normalise_key(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").casefold()


def _is_protected_key(value: str) -> bool:
    normalized = _normalise_key(value)
    compact = normalized.replace("_", "")
    return normalized in _PROTECTED_KEYS or any(
        fragment in compact for fragment in _PROTECTED_KEY_FRAGMENTS
    )


def _assert_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise LabContractError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _assert_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise LabContractError(f"{name} is invalid")
    return value


def _assert_optional_identifier(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _assert_identifier(name, value)


def _assert_optional_sha256(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _assert_sha256(name, value)


def _assert_nonnegative(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LabContractError(f"{name} must be a non-negative integer")
    return value


def _assert_exact_fields(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LabContractError(f"{label} fields differ")
    return value


def assert_public_projection_safe(value: object, *, path: str = "$") -> None:
    """Reject protected controller material at arbitrary nesting.

    Hash-only bindings such as ``prompt_sha256`` are deliberately allowed, but must be actual
    lower-case SHA-256 values.  This makes a public contract able to bind a private prompt or
    memory policy without ever transporting it.
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise LabContractError(f"non-string key at {path}")
            normalized = _normalise_key(key)
            if normalized.endswith("_sha256"):
                _assert_sha256(f"digest field at {path}.{key}", child)
            elif _is_protected_key(key):
                raise LabContractError(f"protected Lab field at {path}.{key}")
            assert_public_projection_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_public_projection_safe(child, path=f"{path}[{index}]")
        return
    if isinstance(value, bytes):
        raise LabContractError(f"binary material is forbidden at {path}")
    if value is None or isinstance(value, (str, int, bool)):
        if isinstance(value, str):
            encoded = value.encode("utf-8", errors="strict")
            if any(pattern.search(encoded) for pattern in _SECRET_PATTERNS):
                raise LabContractError(f"credential-like material is forbidden at {path}")
        return
    raise LabContractError(f"unsupported public value at {path}")


def _canonical_safe_copy(value: object, *, label: str) -> Any:
    """Validate and turn a caller-owned JSON value into an independent canonical copy."""

    assert_public_projection_safe(value, path=label)
    try:
        return strict_json_loads(canonical_json_bytes(value))
    except (ProtocolValidationError, TypeError, ValueError) as error:
        raise LabContractError(f"{label} is not canonical JSON") from error


def _as_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LabContractError(f"{label} must be an object")
    copied = _canonical_safe_copy(value, label=label)
    if not isinstance(copied, Mapping):  # Defensive: canonical JSON preserves objects.
        raise LabContractError(f"{label} must be an object")
    return copied


@dataclass(frozen=True)
class RunEntrant:
    """One comparable model seat in a run contract."""

    entrant_id: str
    model_id: str
    provider: str = "openai"
    display_name: str | None = None

    def __post_init__(self) -> None:
        _assert_identifier("entrant_id", self.entrant_id)
        _assert_identifier("model_id", self.model_id)
        _assert_identifier("provider", self.provider)
        if self.display_name is not None:
            if not isinstance(self.display_name, str) or not self.display_name:
                raise LabContractError("display_name is invalid")
            _canonical_safe_copy(self.display_name, label="entrant.display_name")

    def as_dict(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "entrant_id": self.entrant_id,
            "model_id": self.model_id,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunEntrant:
        parsed = _assert_exact_fields(
            value,
            frozenset({"display_name", "entrant_id", "model_id", "provider"}),
            "run entrant",
        )
        return cls(
            entrant_id=parsed["entrant_id"],
            model_id=parsed["model_id"],
            provider=parsed["provider"],
            display_name=parsed["display_name"],
        )


_RUN_CONTRACT_FIELDS = frozenset(
    {
        "budget",
        "configuration",
        "configuration_sha256",
        "entrants",
        "game_id",
        "game_version",
        "lineage_diff",
        "map_id",
        "map_sha256",
        "mode",
        "parent_contract_sha256",
        "prompt_sha256",
        "run_id",
        "runtime_version",
        "scenario_id",
        "schema_version",
        "seed_policy",
        "skill_mode",
    }
)
_CLONEABLE_CONTRACT_FIELDS = frozenset(
    {
        "budget",
        "configuration",
        "entrants",
        "game_id",
        "game_version",
        "map_id",
        "map_sha256",
        "mode",
        "prompt_sha256",
        "runtime_version",
        "scenario_id",
        "seed_policy",
        "skill_mode",
    }
)


@dataclass(frozen=True, init=False)
class RunContract:
    """Frozen, cloneable run configuration with a canonical SHA-256 fingerprint."""

    _canonical_body: bytes
    contract_sha256: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        game_id: str,
        game_version: str,
        mode: RunMode | str,
        entrants: Sequence[RunEntrant],
        seed_policy: Mapping[str, object],
        budget: Mapping[str, object],
        configuration: Mapping[str, object],
        runtime_version: str,
        scenario_id: str | None = None,
        map_id: str | None = None,
        map_sha256: str | None = None,
        skill_mode: str = "none",
        prompt_sha256: str | None = None,
    ) -> RunContract:
        """Create a root contract with no parent lineage.

        ``configuration`` may contain game-specific safe settings.  Its digest is stored in the
        contract automatically, while raw prompts and credentials are rejected before hashing.
        """

        if not isinstance(entrants, Sequence) or isinstance(entrants, (str, bytes)):
            raise LabContractError("entrants must be a sequence")
        parsed_entrants = []
        for entrant in entrants:
            if not isinstance(entrant, RunEntrant):
                raise TypeError("entrants must contain RunEntrant values")
            parsed_entrants.append(entrant)
        safe_seed_policy = _as_mapping(seed_policy, label="run_contract.seed_policy")
        safe_budget = _as_mapping(budget, label="run_contract.budget")
        safe_configuration = _as_mapping(configuration, label="run_contract.configuration")
        sorted_entrants = sorted(parsed_entrants, key=lambda item: item.entrant_id)
        body: dict[str, object] = {
            "budget": safe_budget,
            "configuration": safe_configuration,
            "configuration_sha256": canonical_sha256(safe_configuration),
            "entrants": [entrant.as_dict() for entrant in sorted_entrants],
            "game_id": game_id,
            "game_version": game_version,
            "lineage_diff": {},
            "map_id": map_id,
            "map_sha256": map_sha256,
            "mode": mode.value if isinstance(mode, RunMode) else mode,
            "parent_contract_sha256": None,
            "prompt_sha256": prompt_sha256,
            "run_id": run_id,
            "runtime_version": runtime_version,
            "scenario_id": scenario_id,
            "schema_version": RUN_CONTRACT_SCHEMA_VERSION,
            "seed_policy": safe_seed_policy,
            "skill_mode": skill_mode,
        }
        return cls._from_body(body)

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> RunContract:
        body = _validate_run_contract_body(value)
        canonical = canonical_json_bytes(body)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical)
        object.__setattr__(instance, "contract_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> RunContract:
        parsed = _assert_exact_fields(
            value, _RUN_CONTRACT_FIELDS | frozenset({"contract_sha256"}), "run contract"
        )
        body = {name: parsed[name] for name in _RUN_CONTRACT_FIELDS}
        instance = cls._from_body(body)
        if parsed["contract_sha256"] != instance.contract_sha256:
            raise LabContractError("run contract fingerprint differs")
        return instance

    @property
    def canonical_body(self) -> bytes:
        """Canonical hash input, excluding the self-referential fingerprint field."""

        return bytes(self._canonical_body)

    @property
    def canonical_bytes(self) -> bytes:
        """Canonical complete serialization, including ``contract_sha256``."""

        return canonical_json_bytes(self.as_dict())

    @property
    def run_id(self) -> str:
        return str(self._body()["run_id"])

    @property
    def mode(self) -> RunMode:
        return RunMode(self._body()["mode"])

    @property
    def configuration(self) -> Mapping[str, Any]:
        return self._body()["configuration"]

    @property
    def lineage_diff(self) -> Mapping[str, Any]:
        return self._body()["lineage_diff"]

    @property
    def parent_contract_sha256(self) -> str | None:
        value = self._body()["parent_contract_sha256"]
        return None if value is None else str(value)

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "contract_sha256": self.contract_sha256}

    def clone(self, *, run_id: str, changes: Mapping[str, object]) -> RunContract:
        """Return a new parent-linked contract with a public configuration diff.

        A clone never mutates its parent.  Only launch-configuration fields may change; lineage
        and canonical fingerprints are generated by this method rather than accepted from callers.
        """

        _assert_identifier("run_id", run_id)
        if run_id == self.run_id:
            raise LabContractError("a clone must use a new run_id")
        if not isinstance(changes, Mapping):
            raise LabContractError("clone changes must be an object")
        unknown = set(changes) - _CLONEABLE_CONTRACT_FIELDS
        if unknown:
            raise LabContractError("clone changes contain immutable contract fields")
        if any(not isinstance(key, str) for key in changes):
            raise LabContractError("clone changes contain a non-string field")

        previous = self._body()
        candidate: dict[str, object] = {**previous, "run_id": run_id}
        for name, child in changes.items():
            candidate[name] = child
        candidate["configuration"] = _as_mapping(
            candidate["configuration"], label="run_contract.configuration"
        )
        candidate["configuration_sha256"] = canonical_sha256(candidate["configuration"])
        candidate["parent_contract_sha256"] = self.contract_sha256
        candidate["lineage_diff"] = {}
        normalized_candidate = _validate_run_contract_body(candidate)

        diff: dict[str, object] = {}
        for name in sorted(changes):
            before = previous[name]
            after = normalized_candidate[name]
            if canonical_json_bytes(before) != canonical_json_bytes(after):
                diff[name] = {"from": before, "to": after}
        normalized_candidate["lineage_diff"] = diff
        return self._from_body(normalized_candidate)

    def mutate(self, *, run_id: str, changes: Mapping[str, object]) -> RunContract:
        """Alias for :meth:`clone` used by mutation-oriented API handlers."""

        return self.clone(run_id=run_id, changes=changes)

    def _body(self) -> Mapping[str, Any]:
        return strict_json_loads(self._canonical_body)


def _validate_run_contract_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _assert_exact_fields(value, _RUN_CONTRACT_FIELDS, "run contract")
    if parsed["schema_version"] != RUN_CONTRACT_SCHEMA_VERSION:
        raise LabContractError("run contract schema_version is unsupported")
    try:
        mode = RunMode(parsed["mode"])
    except (TypeError, ValueError) as error:
        raise LabContractError("run contract mode is invalid") from error

    raw_entrants = parsed["entrants"]
    if not isinstance(raw_entrants, list) or not raw_entrants:
        raise LabContractError("run contract entrants are invalid")
    entrants = tuple(RunEntrant.from_dict(item) for item in raw_entrants)
    entrant_ids = tuple(item.entrant_id for item in entrants)
    if len(entrant_ids) != len(set(entrant_ids)) or tuple(sorted(entrant_ids)) != entrant_ids:
        raise LabContractError("run contract entrants are not uniquely sorted")

    seed_policy = _as_mapping(parsed["seed_policy"], label="run_contract.seed_policy")
    budget = _as_mapping(parsed["budget"], label="run_contract.budget")
    configuration = _as_mapping(parsed["configuration"], label="run_contract.configuration")
    configuration_sha256 = _assert_sha256("configuration_sha256", parsed["configuration_sha256"])
    if configuration_sha256 != canonical_sha256(configuration):
        raise LabContractError("run contract configuration fingerprint differs")
    lineage_diff = _as_mapping(parsed["lineage_diff"], label="run_contract.lineage_diff")
    parent_contract_sha256 = _assert_optional_sha256(
        "parent_contract_sha256", parsed["parent_contract_sha256"]
    )
    if parent_contract_sha256 is None and lineage_diff:
        raise LabContractError("root run contract cannot contain a lineage diff")

    return {
        "budget": budget,
        "configuration": configuration,
        "configuration_sha256": configuration_sha256,
        "entrants": [item.as_dict() for item in entrants],
        "game_id": _assert_identifier("game_id", parsed["game_id"]),
        "game_version": _assert_identifier("game_version", parsed["game_version"]),
        "lineage_diff": lineage_diff,
        "map_id": _assert_optional_identifier("map_id", parsed["map_id"]),
        "map_sha256": _assert_optional_sha256("map_sha256", parsed["map_sha256"]),
        "mode": mode.value,
        "parent_contract_sha256": parent_contract_sha256,
        "prompt_sha256": _assert_optional_sha256("prompt_sha256", parsed["prompt_sha256"]),
        "run_id": _assert_identifier("run_id", parsed["run_id"]),
        "runtime_version": _assert_identifier("runtime_version", parsed["runtime_version"]),
        "scenario_id": _assert_optional_identifier("scenario_id", parsed["scenario_id"]),
        "schema_version": RUN_CONTRACT_SCHEMA_VERSION,
        "seed_policy": seed_policy,
        "skill_mode": _assert_identifier("skill_mode", parsed["skill_mode"]),
    }


_RUN_STATE_FIELDS = frozenset(
    {
        "checkpoint_sequence",
        "contract_sha256",
        "failure_code",
        "result_sha256",
        "run_id",
        "schema_version",
        "status",
    }
)


@dataclass(frozen=True, init=False)
class RunState:
    """Canonical authority lifecycle state, separated from an immutable run contract."""

    _canonical_body: bytes
    state_sha256: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        contract_sha256: str,
        status: RunLifecycle | str = RunLifecycle.DRAFT,
        checkpoint_sequence: int = 0,
        result_sha256: str | None = None,
        failure_code: str | None = None,
    ) -> RunState:
        return cls._from_body(
            {
                "checkpoint_sequence": checkpoint_sequence,
                "contract_sha256": contract_sha256,
                "failure_code": failure_code,
                "result_sha256": result_sha256,
                "run_id": run_id,
                "schema_version": RUN_STATE_SCHEMA_VERSION,
                "status": status.value if isinstance(status, RunLifecycle) else status,
            }
        )

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> RunState:
        body = _validate_run_state_body(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical_json_bytes(body))
        object.__setattr__(instance, "state_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> RunState:
        parsed = _assert_exact_fields(
            value,
            _RUN_STATE_FIELDS | frozenset({"state_sha256"}),
            "run state",
        )
        instance = cls._from_body({name: parsed[name] for name in _RUN_STATE_FIELDS})
        if parsed["state_sha256"] != instance.state_sha256:
            raise LabContractError("run state fingerprint differs")
        return instance

    @property
    def run_id(self) -> str:
        return str(self._body()["run_id"])

    @property
    def contract_sha256(self) -> str:
        return str(self._body()["contract_sha256"])

    @property
    def status(self) -> RunLifecycle:
        return RunLifecycle(self._body()["status"])

    @property
    def checkpoint_sequence(self) -> int:
        return int(self._body()["checkpoint_sequence"])

    def transition(
        self,
        target: RunLifecycle | str,
        *,
        checkpoint_sequence: int | None = None,
        result_sha256: str | None = None,
        failure_code: str | None = None,
    ) -> RunState:
        """Return the next legal lifecycle state without mutating this state."""

        try:
            target_status = target if isinstance(target, RunLifecycle) else RunLifecycle(target)
        except (TypeError, ValueError) as error:
            raise LabContractError("target run status is invalid") from error
        if not self.status.can_transition_to(target_status):
            raise LabContractError(
                f"illegal run lifecycle transition: {self.status.value}->{target_status.value}"
            )

        next_checkpoint = (
            self.checkpoint_sequence if checkpoint_sequence is None else checkpoint_sequence
        )
        _assert_nonnegative("checkpoint_sequence", next_checkpoint)
        if next_checkpoint < self.checkpoint_sequence:
            raise LabContractError("checkpoint sequence cannot decrease")
        if (
            target_status is RunLifecycle.CHECKPOINTED
            and next_checkpoint <= self.checkpoint_sequence
        ):
            raise LabContractError("checkpointed state must advance the checkpoint sequence")

        previous = self._body()
        next_result = result_sha256
        if target_status in {RunLifecycle.SEALED, RunLifecycle.VERIFIED} and next_result is None:
            next_result = previous["result_sha256"]
        return self.create(
            run_id=self.run_id,
            contract_sha256=self.contract_sha256,
            status=target_status,
            checkpoint_sequence=next_checkpoint,
            result_sha256=next_result,
            failure_code=failure_code,
        )

    @property
    def canonical_body(self) -> bytes:
        return bytes(self._canonical_body)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "state_sha256": self.state_sha256}

    def _body(self) -> Mapping[str, Any]:
        return strict_json_loads(self._canonical_body)


def _validate_run_state_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _assert_exact_fields(value, _RUN_STATE_FIELDS, "run state")
    if parsed["schema_version"] != RUN_STATE_SCHEMA_VERSION:
        raise LabContractError("run state schema_version is unsupported")
    try:
        status = RunLifecycle(parsed["status"])
    except (TypeError, ValueError) as error:
        raise LabContractError("run state status is invalid") from error
    checkpoint_sequence = _assert_nonnegative("checkpoint_sequence", parsed["checkpoint_sequence"])
    result_sha256 = _assert_optional_sha256("result_sha256", parsed["result_sha256"])
    failure_code = _assert_optional_identifier("failure_code", parsed["failure_code"])

    if status is RunLifecycle.CHECKPOINTED and checkpoint_sequence == 0:
        raise LabContractError("checkpointed state requires a checkpoint sequence")
    if status in {RunLifecycle.COMPLETED, RunLifecycle.SEALED, RunLifecycle.VERIFIED}:
        if result_sha256 is None:
            raise LabContractError("terminal successful state requires a result fingerprint")
    elif result_sha256 is not None:
        raise LabContractError("only successful terminal states may contain a result fingerprint")
    if status in {RunLifecycle.FAILED, RunLifecycle.CANCELLED}:
        if failure_code is None:
            raise LabContractError("failed or cancelled state requires a failure_code")
    elif failure_code is not None:
        raise LabContractError("only failed or cancelled states may contain a failure_code")

    return {
        "checkpoint_sequence": checkpoint_sequence,
        "contract_sha256": _assert_sha256("contract_sha256", parsed["contract_sha256"]),
        "failure_code": failure_code,
        "result_sha256": result_sha256,
        "run_id": _assert_identifier("run_id", parsed["run_id"]),
        "schema_version": RUN_STATE_SCHEMA_VERSION,
        "status": status.value,
    }


_REPLAY_PROJECTION_FIELDS = frozenset(
    {
        "contract_sha256",
        "events",
        "run_id",
        "schema_version",
        "sequence",
        "snapshot",
        "status",
    }
)


@dataclass(frozen=True, init=False)
class ReplayProjection:
    """Strictly public browser/Godot state for one safe replay sequence."""

    _canonical_body: bytes
    projection_sha256: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        contract_sha256: str,
        status: RunLifecycle | str,
        sequence: int,
        snapshot: Mapping[str, object],
        events: Sequence[Mapping[str, object]] = (),
    ) -> ReplayProjection:
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            raise LabContractError("replay events must be a sequence")
        return cls._from_body(
            {
                "contract_sha256": contract_sha256,
                "events": [dict(event) for event in events],
                "run_id": run_id,
                "schema_version": REPLAY_PROJECTION_SCHEMA_VERSION,
                "sequence": sequence,
                "snapshot": dict(snapshot),
                "status": status.value if isinstance(status, RunLifecycle) else status,
            }
        )

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> ReplayProjection:
        body = _validate_replay_projection_body(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical_json_bytes(body))
        object.__setattr__(instance, "projection_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> ReplayProjection:
        parsed = _assert_exact_fields(
            value,
            _REPLAY_PROJECTION_FIELDS | frozenset({"projection_sha256"}),
            "replay projection",
        )
        instance = cls._from_body({name: parsed[name] for name in _REPLAY_PROJECTION_FIELDS})
        if parsed["projection_sha256"] != instance.projection_sha256:
            raise LabContractError("replay projection fingerprint differs")
        return instance

    @property
    def run_id(self) -> str:
        return str(self._body()["run_id"])

    @property
    def contract_sha256(self) -> str:
        return str(self._body()["contract_sha256"])

    @property
    def status(self) -> RunLifecycle:
        return RunLifecycle(self._body()["status"])

    @property
    def sequence(self) -> int:
        return int(self._body()["sequence"])

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "projection_sha256": self.projection_sha256}

    @property
    def canonical_body(self) -> bytes:
        return bytes(self._canonical_body)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def _body(self) -> Mapping[str, Any]:
        return strict_json_loads(self._canonical_body)


def _validate_replay_projection_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _assert_exact_fields(value, _REPLAY_PROJECTION_FIELDS, "replay projection")
    if parsed["schema_version"] != REPLAY_PROJECTION_SCHEMA_VERSION:
        raise LabContractError("replay projection schema_version is unsupported")
    try:
        status = RunLifecycle(parsed["status"])
    except (TypeError, ValueError) as error:
        raise LabContractError("replay projection status is invalid") from error
    snapshot = _as_mapping(parsed["snapshot"], label="replay_projection.snapshot")
    raw_events = parsed["events"]
    if not isinstance(raw_events, list):
        raise LabContractError("replay projection events must be an array")
    events = []
    for index, event in enumerate(raw_events):
        events.append(_as_mapping(event, label=f"replay_projection.events[{index}]"))
    return {
        "contract_sha256": _assert_sha256("contract_sha256", parsed["contract_sha256"]),
        "events": events,
        "run_id": _assert_identifier("run_id", parsed["run_id"]),
        "schema_version": REPLAY_PROJECTION_SCHEMA_VERSION,
        "sequence": _assert_nonnegative("sequence", parsed["sequence"]),
        "snapshot": snapshot,
        "status": status.value,
    }


_RACE_CARTRIDGE_FIELDS = frozenset(
    {
        "authority_checkpoint_sha256",
        "cartridge_id",
        "contract_sha256",
        "public_projection",
        "schema_version",
        "state",
    }
)


@dataclass(frozen=True, init=False)
class RaceCartridge:
    """A resumable run envelope with an opaque private checkpoint binding.

    The private authority checkpoint is intentionally represented only by a digest.  The actual
    checkpoint belongs in trusted storage and is never accepted by this transportable type.
    """

    _canonical_body: bytes
    cartridge_sha256: str

    @classmethod
    def create(
        cls,
        *,
        cartridge_id: str,
        contract: RunContract,
        state: RunState,
        public_projection: ReplayProjection,
        authority_checkpoint_sha256: str | None = None,
    ) -> RaceCartridge:
        if not isinstance(contract, RunContract):
            raise TypeError("contract must be a RunContract")
        if not isinstance(state, RunState):
            raise TypeError("state must be a RunState")
        if not isinstance(public_projection, ReplayProjection):
            raise TypeError("public_projection must be a ReplayProjection")
        return cls._from_body(
            {
                "authority_checkpoint_sha256": authority_checkpoint_sha256,
                "cartridge_id": cartridge_id,
                "contract_sha256": contract.contract_sha256,
                "public_projection": public_projection.as_dict(),
                "schema_version": RACE_CARTRIDGE_SCHEMA_VERSION,
                "state": state.as_dict(),
            }
        )

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> RaceCartridge:
        body = _validate_race_cartridge_body(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical_json_bytes(body))
        object.__setattr__(instance, "cartridge_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> RaceCartridge:
        parsed = _assert_exact_fields(
            value,
            _RACE_CARTRIDGE_FIELDS | frozenset({"cartridge_sha256"}),
            "race cartridge",
        )
        instance = cls._from_body({name: parsed[name] for name in _RACE_CARTRIDGE_FIELDS})
        if parsed["cartridge_sha256"] != instance.cartridge_sha256:
            raise LabContractError("race cartridge fingerprint differs")
        return instance

    @property
    def canonical_body(self) -> bytes:
        return bytes(self._canonical_body)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    @property
    def cartridge_id(self) -> str:
        return str(self._body()["cartridge_id"])

    @property
    def state(self) -> RunState:
        return RunState.from_dict(self._body()["state"])

    @property
    def public_projection(self) -> ReplayProjection:
        return ReplayProjection.from_dict(self._body()["public_projection"])

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "cartridge_sha256": self.cartridge_sha256}

    def _body(self) -> Mapping[str, Any]:
        return strict_json_loads(self._canonical_body)


def _validate_race_cartridge_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _assert_exact_fields(value, _RACE_CARTRIDGE_FIELDS, "race cartridge")
    if parsed["schema_version"] != RACE_CARTRIDGE_SCHEMA_VERSION:
        raise LabContractError("race cartridge schema_version is unsupported")
    state = RunState.from_dict(parsed["state"])
    projection = ReplayProjection.from_dict(parsed["public_projection"])
    contract_sha256 = _assert_sha256("contract_sha256", parsed["contract_sha256"])
    authority_checkpoint_sha256 = _assert_optional_sha256(
        "authority_checkpoint_sha256", parsed["authority_checkpoint_sha256"]
    )
    if state.contract_sha256 != contract_sha256 or projection.contract_sha256 != contract_sha256:
        raise LabContractError("race cartridge contract binding differs")
    if state.run_id != projection.run_id:
        raise LabContractError("race cartridge run binding differs")
    if state.status is not projection.status or state.checkpoint_sequence != projection.sequence:
        raise LabContractError("race cartridge public lifecycle differs")
    if state.status is RunLifecycle.CHECKPOINTED and authority_checkpoint_sha256 is None:
        raise LabContractError("checkpointed race cartridge requires a private checkpoint digest")
    return {
        "authority_checkpoint_sha256": authority_checkpoint_sha256,
        "cartridge_id": _assert_identifier("cartridge_id", parsed["cartridge_id"]),
        "contract_sha256": contract_sha256,
        "public_projection": projection.as_dict(),
        "schema_version": RACE_CARTRIDGE_SCHEMA_VERSION,
        "state": state.as_dict(),
    }


__all__ = [
    "RACE_CARTRIDGE_SCHEMA_VERSION",
    "REPLAY_PROJECTION_SCHEMA_VERSION",
    "RUN_CONTRACT_SCHEMA_VERSION",
    "RUN_STATE_SCHEMA_VERSION",
    "LabContractError",
    "RaceCartridge",
    "ReplayProjection",
    "RunContract",
    "RunEntrant",
    "RunLifecycle",
    "RunMode",
    "RunState",
    "assert_public_projection_safe",
]
