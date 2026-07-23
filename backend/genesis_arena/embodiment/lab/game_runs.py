"""Durable Lab cartridges for non-Labyrinth authorities.

Labyrinth has a specialised spectator projection and therefore keeps its existing
``LabRunService``.  This companion service records the same canonical RunContract,
RunState, ReplayProjection, and RaceCartridge shapes for the already-existing solo, paired, and
trio Godot services.

Only the authority kind is durable.  Its source handle is a process-local capability and is
written as ``null``; after a restart, completed cartridges remain replayable while interrupted
runs are honestly marked authority-unavailable and non-resumable.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..protocol import canonical_json_bytes, canonical_sha256, strict_json_loads
from .contracts import (
    LabContractError,
    RaceCartridge,
    ReplayProjection,
    RunContract,
    RunEntrant,
    RunLifecycle,
    RunMode,
    RunState,
    assert_public_projection_safe,
)
from .game_authority import (
    AuthorityFrame,
    AuthorityKind,
    GameAuthorityError,
    GameAuthorityGateway,
    GameAuthorityNotFoundError,
    GameAuthorityNotReadyError,
)
from .games import GameCatalogError, game_spec

GENERIC_LAB_RUN_RECORD_SCHEMA_VERSION = "worldeval/lab-game-run-record/1"
_DIRECTORY_NAME = "lab-games"
_RECORD_FILE = "record.json"
_CONTRACT_FILE = "contract.json"
_STATE_FILE = "state.json"
_PROJECTION_FILE = "projection.json"
_CARTRIDGE_FILE = "cartridge.json"
_CURRENT_FILE = "current.json"
_COMMITS_DIRECTORY = "commits"
_GENERATIONS_DIRECTORY = "generations"
_GENERATION_POINTER_SCHEMA_VERSION = "worldeval/lab-game-run-generation-pointer/1"
_GENERATION_COMMIT_SCHEMA_VERSION = "worldeval/lab-game-run-generation-commit/1"
_GENERATION_NAME = re.compile(r"^gen-[0-9]{20}-[0-9a-f]{16}$")
_GENERATIONS_TO_KEEP = 3
_BUNDLE_FILES = frozenset(
    (
        _RECORD_FILE,
        _CONTRACT_FILE,
        _STATE_FILE,
        _PROJECTION_FILE,
        _CARTRIDGE_FILE,
    )
)
_TERMINAL = frozenset(
    (
        RunLifecycle.COMPLETED,
        RunLifecycle.FAILED,
        RunLifecycle.CANCELLED,
        RunLifecycle.SEALED,
        RunLifecycle.VERIFIED,
    )
)
_SOURCE_ID_PREFIX = {
    "solo_episode": "ep_",
    "paired_series": "series_",
    "trio_series": "trio_",
}


class GenericLabRunError(RuntimeError):
    """A generic Lab record is malformed or cannot be synchronized safely."""


class GenericLabRunNotFoundError(KeyError):
    """No generic Lab record has this public run id."""


@dataclass(frozen=True)
class AttachedGameRun:
    """Credential-free launch receipt accepted from an existing authority."""

    authority_kind: AuthorityKind
    source_id: str
    game_id: str
    game_version: str
    runtime_version: str
    scenario_id: str | None
    entrants: tuple[RunEntrant, ...]
    seed: int
    budget: Mapping[str, object]
    configuration: Mapping[str, object]
    mode: RunMode = RunMode.EXPLORATORY
    skill_mode: str = "none"
    prompt_sha256: str | None = None

    def __post_init__(self) -> None:
        GameAuthorityGateway.validate_kind(self.authority_kind)
        if (
            not isinstance(self.source_id, str)
            or not self.source_id.startswith(_SOURCE_ID_PREFIX[self.authority_kind])
        ):
            raise GenericLabRunError("game authority identity is invalid")
        try:
            spec = game_spec(self.game_id)
        except GameCatalogError as error:
            raise GenericLabRunError("game is absent from the Lab catalogue") from error
        if self.game_version not in spec.task_ids:
            raise GenericLabRunError("game version is not registered for this game")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or not 0 <= self.seed <= 2_147_483_647
        ):
            raise GenericLabRunError("game seed is invalid")
        if not self.entrants or any(not isinstance(value, RunEntrant) for value in self.entrants):
            raise GenericLabRunError("game entrants are invalid")
        if not spec.participants.minimum <= len(self.entrants) <= spec.participants.maximum:
            raise GenericLabRunError("game entrant count differs from its passport")
        if len({value.entrant_id for value in self.entrants}) != len(self.entrants):
            raise GenericLabRunError("game entrant identities are invalid")
        if not isinstance(self.mode, RunMode):
            raise GenericLabRunError("game run mode is invalid")
        assert_public_projection_safe(self.budget, path="attached_game_run.budget")
        assert_public_projection_safe(
            self.configuration, path="attached_game_run.configuration"
        )


@dataclass
class _GenericRecord:
    contract: RunContract
    state: RunState
    projection: ReplayProjection
    cartridge: RaceCartridge
    authority_kind: AuthorityKind
    source_id: str | None
    authority_available: bool
    created_at_epoch_ms: int


class GenericLabRunService:
    """Persist and synchronize safe cartridges for solo, paired, and trio games."""

    def __init__(self, *, runs_dir: Path, authority: GameAuthorityGateway) -> None:
        if not isinstance(authority, GameAuthorityGateway):
            raise TypeError("generic Lab authority gateway is invalid")
        self._root = Path(runs_dir) / _DIRECTORY_NAME
        self._assert_no_symlink_components(self._root)
        self._authority = authority
        self._records: dict[str, _GenericRecord] = {}
        self._lock: asyncio.Lock | None = None
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._assert_no_symlink_components(self._root)
        if not self._root.is_dir():
            raise GenericLabRunError("generic Lab storage root is invalid")
        try:
            os.chmod(self._root, 0o700)
        except OSError:
            pass
        self._load()

    def _service_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def attach(self, launch: AttachedGameRun) -> Mapping[str, Any]:
        """Create a canonical Lab identity after an existing authority accepted a run."""

        if not isinstance(launch, AttachedGameRun):
            raise TypeError("attached game launch is invalid")
        run_id = f"run_game_{secrets.token_hex(12)}"
        contract = RunContract.create(
            run_id=run_id,
            game_id=launch.game_id,
            game_version=launch.game_version,
            mode=launch.mode,
            entrants=launch.entrants,
            seed_policy={"kind": "fixed_seed", "seed": launch.seed},
            budget=launch.budget,
            configuration={
                **dict(launch.configuration),
                "authority_kind": launch.authority_kind,
            },
            runtime_version=launch.runtime_version,
            scenario_id=launch.scenario_id,
            skill_mode=launch.skill_mode,
            prompt_sha256=launch.prompt_sha256,
        )
        state = RunState.create(
            run_id=run_id,
            contract_sha256=contract.contract_sha256,
            status=RunLifecycle.QUEUED,
        )
        projection = self._status_projection(contract, state)
        record = _GenericRecord(
            contract=contract,
            state=state,
            projection=projection,
            cartridge=self._cartridge(contract, state, projection),
            authority_kind=launch.authority_kind,
            source_id=launch.source_id,
            authority_available=True,
            created_at_epoch_ms=int(time.time() * 1_000),
        )
        async with self._service_lock():
            self._records[run_id] = record
            try:
                self._persist(record)
            except Exception:
                self._records.pop(run_id, None)
                raise
        return self._public_record(record)

    async def list_runs(self) -> list[Mapping[str, Any]]:
        async with self._service_lock():
            records = sorted(
                self._records.values(),
                key=lambda item: (item.created_at_epoch_ms, item.contract.run_id),
                reverse=True,
            )
        for record in records:
            await self._synchronize(record)
        return [self._public_record(record) for record in records]

    async def get_run(self, run_id: str) -> Mapping[str, Any]:
        record = await self._get(run_id)
        await self._synchronize(record)
        return self._public_record(record)

    async def projection(self, run_id: str) -> Mapping[str, Any]:
        record = await self._get(run_id)
        await self._synchronize(record)
        return dict(record.projection.as_dict())

    async def frame(self, run_id: str, *, participant_id: str) -> AuthorityFrame:
        record = await self._get(run_id)
        await self._synchronize(record)
        if (
            not record.authority_available
            or record.source_id is None
            or record.state.status in {RunLifecycle.DRAFT, RunLifecycle.FAILED}
        ):
            return AuthorityFrame("unavailable", None, None)
        try:
            return await self._authority.frame(
                record.authority_kind,
                record.source_id,
                participant_id=participant_id,
            )
        except GameAuthorityNotFoundError:
            record.authority_available = False
            self._persist(record)
            return AuthorityFrame("unavailable", None, None)

    async def cancel(self, run_id: str) -> Mapping[str, Any]:
        record = await self._get(run_id)
        if record.state.status in _TERMINAL:
            return self._public_record(record)
        if not record.authority_available or record.source_id is None:
            raise GenericLabRunError("game authority is unavailable")
        try:
            await self._authority.cancel(record.authority_kind, record.source_id)
        except GameAuthorityNotFoundError as error:
            record.authority_available = False
            self._persist(record)
            raise GenericLabRunError("game authority is unavailable") from error
        await self._synchronize(record)
        return self._public_record(record)

    async def seal(self, run_id: str) -> Mapping[str, Any]:
        """Freeze one completed public cartridge without claiming benchmark eligibility."""

        record = await self._get(run_id)
        await self._synchronize(record)
        async with self._service_lock():
            record = self._record_or_raise(run_id)
            if record.state.status in {RunLifecycle.SEALED, RunLifecycle.VERIFIED}:
                self._revalidate_durable_evidence(record)
                return self._public_record(record)
            if record.state.status is not RunLifecycle.COMPLETED:
                raise GenericLabRunError("only a completed game run can be sealed")
            replacement = self._evidence_lifecycle_replacement(
                record,
                RunLifecycle.SEALED,
            )
            self._commit_evidence_replacement(replacement)
            self._records[run_id] = replacement
            return self._public_record(replacement)

    async def verify(self, run_id: str) -> Mapping[str, Any]:
        """Verify the canonical durable bindings of one sealed public cartridge."""

        await self._get(run_id)
        async with self._service_lock():
            record = self._record_or_raise(run_id)
            if record.state.status is RunLifecycle.VERIFIED:
                self._revalidate_durable_evidence(record)
                return self._public_record(record)
            if record.state.status is not RunLifecycle.SEALED:
                raise GenericLabRunError("only a sealed game run can be verified")
            self._assert_evidence_bindings(record)
            replacement = self._evidence_lifecycle_replacement(
                record,
                RunLifecycle.VERIFIED,
            )
            self._commit_evidence_replacement(replacement)
            self._records[run_id] = replacement
            return self._public_record(replacement)

    async def clone(
        self, run_id: str, *, changes: Mapping[str, object]
    ) -> Mapping[str, Any]:
        """Create an immutable lineage-linked draft without implying resume support."""

        if not isinstance(changes, Mapping):
            raise GenericLabRunError("generic Lab clone changes are invalid")
        async with self._service_lock():
            parent = self._record_or_raise(run_id)
            normalized_changes = dict(changes)
            if "configuration" in normalized_changes:
                requested = normalized_changes["configuration"]
                if not isinstance(requested, Mapping):
                    raise GenericLabRunError(
                        "generic Lab clone configuration is invalid"
                    )
                configuration = dict(parent.contract.configuration)
                configuration.update(requested)
                normalized_changes["configuration"] = configuration
            clone = parent.contract.clone(
                run_id=f"run_game_{secrets.token_hex(12)}",
                changes=normalized_changes,
            )
            state = RunState.create(
                run_id=clone.run_id,
                contract_sha256=clone.contract_sha256,
                status=RunLifecycle.DRAFT,
            )
            projection = self._status_projection(clone, state)
            record = _GenericRecord(
                contract=clone,
                state=state,
                projection=projection,
                cartridge=self._cartridge(clone, state, projection),
                authority_kind=parent.authority_kind,
                source_id=None,
                authority_available=False,
                created_at_epoch_ms=int(time.time() * 1_000),
            )
            self._records[clone.run_id] = record
            try:
                self._persist(record)
            except Exception:
                self._records.pop(clone.run_id, None)
                raise
            return self._public_record(record)

    async def reserve_draft_launch(self, run_id: str) -> RunContract:
        """Atomically reserve a frozen draft before any new authority is created."""

        async with self._service_lock():
            record = self._record_or_raise(run_id)
            if (
                record.state.status is not RunLifecycle.DRAFT
                or record.source_id is not None
                or record.authority_available
            ):
                raise GenericLabRunError("generic Lab run is not an unlaunched draft")
            queued_state = record.state.transition(RunLifecycle.QUEUED)
            queued_projection = self._status_projection(record.contract, queued_state)
            queued = _GenericRecord(
                contract=record.contract,
                state=queued_state,
                projection=queued_projection,
                cartridge=self._cartridge(
                    record.contract, queued_state, queued_projection
                ),
                authority_kind=record.authority_kind,
                source_id=None,
                authority_available=False,
                created_at_epoch_ms=record.created_at_epoch_ms,
            )
            self._persist(queued)
            self._records[run_id] = queued
            return queued.contract

    async def inspect_draft_launch(self, run_id: str) -> RunContract:
        """Read one still-unlaunched draft for pre-reservation compatibility checks."""

        async with self._service_lock():
            record = self._record_or_raise(run_id)
            if (
                record.state.status is not RunLifecycle.DRAFT
                or record.source_id is not None
                or record.authority_available
            ):
                raise GenericLabRunError("generic Lab run is not an unlaunched draft")
            return record.contract

    async def attach_reserved_authority(
        self, run_id: str, *, source_id: str
    ) -> Mapping[str, Any]:
        """Attach one newly created authority to an atomically reserved draft."""

        async with self._service_lock():
            record = self._record_or_raise(run_id)
            expected_prefix = _SOURCE_ID_PREFIX[record.authority_kind]
            if (
                record.state.status is not RunLifecycle.QUEUED
                or record.source_id is not None
                or record.authority_available
                or not isinstance(source_id, str)
                or not source_id.startswith(expected_prefix)
            ):
                raise GenericLabRunError("generic Lab run is not awaiting an authority")
            activated = _GenericRecord(
                contract=record.contract,
                state=record.state,
                projection=record.projection,
                cartridge=record.cartridge,
                authority_kind=record.authority_kind,
                source_id=source_id,
                authority_available=True,
                created_at_epoch_ms=record.created_at_epoch_ms,
            )
            self._persist(activated)
            self._records[run_id] = activated
            return self._public_record(activated)

    async def fail_reserved_draft_launch(self, run_id: str) -> None:
        """Record a stable failure without provider or transport detail."""

        async with self._service_lock():
            record = self._record_or_raise(run_id)
            if record.state.status is RunLifecycle.FAILED:
                return
            if (
                record.state.status is not RunLifecycle.QUEUED
                or record.source_id is not None
                or record.authority_available
            ):
                raise GenericLabRunError("generic Lab run is not a reserved draft")
            state = record.state.transition(
                RunLifecycle.FAILED,
                failure_code="game_authority_launch_unavailable",
            )
            projection = ReplayProjection.create(
                run_id=record.contract.run_id,
                contract_sha256=record.contract.contract_sha256,
                status=state.status,
                sequence=state.checkpoint_sequence,
                snapshot=self._base_snapshot(record.contract),
            )
            failed = _GenericRecord(
                contract=record.contract,
                state=state,
                projection=projection,
                cartridge=self._cartridge(record.contract, state, projection),
                authority_kind=record.authority_kind,
                source_id=None,
                authority_available=False,
                created_at_epoch_ms=record.created_at_epoch_ms,
            )
            self._persist(failed)
            self._records[run_id] = failed

    async def _get(self, run_id: str) -> _GenericRecord:
        async with self._service_lock():
            return self._record_or_raise(run_id)

    def _record_or_raise(self, run_id: str) -> _GenericRecord:
        try:
            return self._records[run_id]
        except KeyError as error:
            raise GenericLabRunNotFoundError(run_id) from error

    async def _synchronize(self, record: _GenericRecord) -> None:
        if (
            not record.authority_available
            or record.source_id is None
            or record.state.status in _TERMINAL
            or record.state.status is RunLifecycle.DRAFT
        ):
            return
        try:
            status = await self._authority.status(record.authority_kind, record.source_id)
        except GameAuthorityNotFoundError:
            record.authority_available = False
            self._persist(record)
            return
        except GameAuthorityError as error:
            raise GenericLabRunError("game authority status is invalid") from error

        target = RunLifecycle(status.state)
        if target is RunLifecycle.COMPLETED:
            try:
                terminal = await self._authority.terminal_projection(
                    record.authority_kind, record.source_id
                )
            except GameAuthorityNotReadyError:
                return
            except GameAuthorityError as error:
                raise GenericLabRunError("game authority evidence is invalid") from error
            result_body = {
                "events": list(terminal.events),
                "snapshot": terminal.snapshot,
            }
            self._transition(
                record,
                RunLifecycle.COMPLETED,
                result_sha256=canonical_sha256(result_body),
            )
            record.projection = ReplayProjection.create(
                run_id=record.contract.run_id,
                contract_sha256=record.contract.contract_sha256,
                status=record.state.status,
                sequence=max(1, record.state.checkpoint_sequence),
                snapshot={
                    **self._base_snapshot(record.contract),
                    "authority": status.public_dict(),
                    "terminal": dict(terminal.snapshot),
                },
                events=terminal.events,
            )
            record.cartridge = self._cartridge(
                record.contract, record.state, record.projection
            )
            self._persist(record)
            return
        if target in {RunLifecycle.FAILED, RunLifecycle.CANCELLED}:
            self._transition(
                record,
                target,
                failure_code=status.failure_code
                or (
                    "game_authority_cancelled"
                    if target is RunLifecycle.CANCELLED
                    else "game_authority_execution_failed"
                ),
            )
        elif target in {RunLifecycle.QUEUED, RunLifecycle.RUNNING}:
            self._transition(record, target)
        else:
            raise GenericLabRunError("game authority lifecycle is invalid")
        record.projection = ReplayProjection.create(
            run_id=record.contract.run_id,
            contract_sha256=record.contract.contract_sha256,
            status=record.state.status,
            sequence=record.state.checkpoint_sequence,
            snapshot={
                **self._base_snapshot(record.contract),
                "authority": status.public_dict(),
            },
        )
        record.cartridge = self._cartridge(
            record.contract, record.state, record.projection
        )
        self._persist(record)

    @staticmethod
    def _transition(
        record: _GenericRecord,
        target: RunLifecycle,
        *,
        result_sha256: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        current = record.state.status
        if current is target:
            return
        if current is RunLifecycle.QUEUED and target is RunLifecycle.COMPLETED:
            record.state = record.state.transition(RunLifecycle.RUNNING)
            current = RunLifecycle.RUNNING
        if current is RunLifecycle.QUEUED and target is RunLifecycle.RUNNING:
            record.state = record.state.transition(RunLifecycle.RUNNING)
            return
        if current is RunLifecycle.RUNNING and target is RunLifecycle.COMPLETED:
            record.state = record.state.transition(
                RunLifecycle.COMPLETED,
                checkpoint_sequence=max(1, record.state.checkpoint_sequence),
                result_sha256=result_sha256,
            )
            return
        if current in {RunLifecycle.QUEUED, RunLifecycle.RUNNING} and target in {
            RunLifecycle.FAILED,
            RunLifecycle.CANCELLED,
        }:
            record.state = record.state.transition(target, failure_code=failure_code)
            return
        raise GenericLabRunError("game authority lifecycle transition is invalid")

    def _evidence_lifecycle_replacement(
        self, record: _GenericRecord, target: RunLifecycle
    ) -> _GenericRecord:
        state = record.state.transition(target)
        projection_body = record.projection.as_dict()
        projection = ReplayProjection.create(
            run_id=record.contract.run_id,
            contract_sha256=record.contract.contract_sha256,
            status=state.status,
            sequence=state.checkpoint_sequence,
            snapshot=projection_body["snapshot"],
            events=projection_body["events"],
        )
        return _GenericRecord(
            contract=record.contract,
            state=state,
            projection=projection,
            cartridge=self._cartridge(record.contract, state, projection),
            authority_kind=record.authority_kind,
            source_id=record.source_id,
            authority_available=record.authority_available,
            created_at_epoch_ms=record.created_at_epoch_ms,
        )

    def _commit_evidence_replacement(self, replacement: _GenericRecord) -> None:
        """Persist and read back a replacement before it may become the live record."""

        try:
            self._persist(replacement)
            persisted = self._read(self._directory(replacement.contract.run_id))
            self._assert_persisted_evidence_matches(replacement, persisted)
        except (GenericLabRunError, LabContractError, OSError, TypeError, ValueError) as error:
            raise GenericLabRunError("generic Lab evidence could not be persisted") from error

    def _revalidate_durable_evidence(self, record: _GenericRecord) -> None:
        """Make idempotent sealed/verified reads prove the durable bundle still matches."""

        try:
            persisted = self._read(self._directory(record.contract.run_id))
            self._assert_persisted_evidence_matches(record, persisted)
        except (GenericLabRunError, LabContractError, OSError, TypeError, ValueError) as error:
            raise GenericLabRunError("generic Lab durable evidence is unavailable") from error

    @classmethod
    def _assert_persisted_evidence_matches(
        cls,
        expected: _GenericRecord,
        persisted: _GenericRecord,
    ) -> None:
        cls._assert_evidence_bindings(persisted)
        if (
            persisted.contract != expected.contract
            or persisted.state != expected.state
            or persisted.projection != expected.projection
            or persisted.cartridge != expected.cartridge
            or persisted.authority_kind != expected.authority_kind
            or persisted.created_at_epoch_ms != expected.created_at_epoch_ms
        ):
            raise GenericLabRunError("generic Lab durable evidence differs")

    @staticmethod
    def _assert_evidence_bindings(record: _GenericRecord) -> None:
        contract = RunContract.from_dict(record.contract.as_dict())
        state = RunState.from_dict(record.state.as_dict())
        projection = ReplayProjection.from_dict(record.projection.as_dict())
        cartridge = RaceCartridge.from_dict(record.cartridge.as_dict())
        if (
            contract.contract_sha256 != state.contract_sha256
            or state.run_id != contract.run_id
            or projection.run_id != contract.run_id
            or projection.contract_sha256 != contract.contract_sha256
            or projection.status is not state.status
            or projection.sequence != state.checkpoint_sequence
            or cartridge.state != state
            or cartridge.public_projection != projection
        ):
            raise GenericLabRunError("generic Lab evidence bindings differ")

    @classmethod
    def _status_projection(
        cls, contract: RunContract, state: RunState
    ) -> ReplayProjection:
        return ReplayProjection.create(
            run_id=contract.run_id,
            contract_sha256=contract.contract_sha256,
            status=state.status,
            sequence=state.checkpoint_sequence,
            snapshot=cls._base_snapshot(contract),
        )

    @staticmethod
    def _base_snapshot(contract: RunContract) -> dict[str, object]:
        body = contract.as_dict()
        return {
            "game": {
                "game_id": body["game_id"],
                "game_version": body["game_version"],
                "mode": body["mode"],
                "scenario_id": body["scenario_id"],
            },
            "entrants": list(body["entrants"]),
            "configuration": dict(body["configuration"]),
        }

    @staticmethod
    def _cartridge(
        contract: RunContract, state: RunState, projection: ReplayProjection
    ) -> RaceCartridge:
        return RaceCartridge.create(
            cartridge_id=f"cartridge_{contract.run_id}",
            contract=contract,
            state=state,
            public_projection=projection,
        )

    def _directory(self, run_id: str) -> Path:
        return self._root / run_id

    def _persist(self, record: _GenericRecord) -> None:
        self._assert_no_symlink_components(self._root)
        directory = self._directory(record.contract.run_id)
        if directory.is_symlink():
            raise GenericLabRunError(
                "generic Lab run directory cannot be a symbolic link"
            )
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise GenericLabRunError("generic Lab run directory is invalid")
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        generations = directory / _GENERATIONS_DIRECTORY
        if generations.is_symlink():
            raise GenericLabRunError(
                "generic Lab generations directory cannot be a symbolic link"
            )
        generations.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(generations, 0o700)
        except OSError:
            pass
        commits = directory / _COMMITS_DIRECTORY
        if commits.is_symlink():
            raise GenericLabRunError(
                "generic Lab commits directory cannot be a symbolic link"
            )
        commits.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(commits, 0o700)
        except OSError:
            pass
        manifest = {
            "authority_kind": record.authority_kind,
            "cartridge_sha256": record.cartridge.cartridge_sha256,
            "contract_sha256": record.contract.contract_sha256,
            "created_at_epoch_ms": record.created_at_epoch_ms,
            "projection_sha256": record.projection.projection_sha256,
            "run_id": record.contract.run_id,
            "schema_version": GENERIC_LAB_RUN_RECORD_SCHEMA_VERSION,
            # Process-local authority capabilities are never written to durable artifacts.
            "source_id": None,
            "state_sha256": record.state.state_sha256,
        }
        assert_public_projection_safe(manifest, path="generic_lab_run.manifest")
        generation_name = self._new_generation_name(generations)
        staging = generations / f".{generation_name}.tmp"
        generation = generations / generation_name
        staging.mkdir(mode=0o700)
        try:
            self._atomic_write(
                staging / _RECORD_FILE, canonical_json_bytes(manifest)
            )
            self._atomic_write(
                staging / _CONTRACT_FILE, record.contract.canonical_bytes
            )
            self._atomic_write(staging / _STATE_FILE, record.state.canonical_bytes)
            self._atomic_write(
                staging / _PROJECTION_FILE, record.projection.canonical_bytes
            )
            self._atomic_write(
                staging / _CARTRIDGE_FILE, record.cartridge.canonical_bytes
            )
            self._fsync_directory(staging)
            os.replace(staging, generation)
            self._fsync_directory(generations)
            pointer = {
                "generation": generation_name,
                "run_id": record.contract.run_id,
                "schema_version": _GENERATION_POINTER_SCHEMA_VERSION,
            }
            assert_public_projection_safe(
                pointer, path="generic_lab_run.generation_pointer"
            )
            self._atomic_write(
                directory / _CURRENT_FILE, canonical_json_bytes(pointer)
            )
            self._fsync_directory(directory)
            self._fsync_directory(self._root)
            # This hash-bound receipt is deliberately created only after the pointer and
            # its parent directories are durable. A generation without this receipt may
            # be complete, but it never crossed the recoverable commit boundary.
            commit = {
                "authority_kind": record.authority_kind,
                "cartridge_sha256": record.cartridge.cartridge_sha256,
                "contract_sha256": record.contract.contract_sha256,
                "created_at_epoch_ms": record.created_at_epoch_ms,
                "generation": generation_name,
                "projection_sha256": record.projection.projection_sha256,
                "record_sha256": canonical_sha256(manifest),
                "run_id": record.contract.run_id,
                "schema_version": _GENERATION_COMMIT_SCHEMA_VERSION,
                "state_sha256": record.state.state_sha256,
            }
            assert_public_projection_safe(
                commit, path="generic_lab_run.generation_commit"
            )
            self._atomic_write(
                commits / f"{generation_name}.json",
                canonical_json_bytes(commit),
            )
            self._fsync_directory(commits)
            self._fsync_directory(directory)
            self._fsync_directory(self._root)
        except BaseException:
            # A process termination can leave either this hidden staging directory or a
            # complete but not-yet-pointed-to generation. Both are intentionally ignored
            # while the previous pointer remains authoritative.
            self._discard_staging(staging)
            raise
        self._prune_generations(
            generations,
            commits=commits,
            current_generation=generation_name,
        )

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(directory, flags)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise GenericLabRunError("generic Lab storage directory is invalid")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _assert_no_symlink_components(path: Path) -> None:
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current /= component
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise GenericLabRunError(
                    "generic Lab storage path cannot contain symbolic links"
                )
            if current != absolute and not stat.S_ISDIR(metadata.st_mode):
                raise GenericLabRunError("generic Lab storage ancestor is invalid")

    @staticmethod
    def _new_generation_name(generations: Path) -> str:
        previous_ordinals = [
            int(candidate.name[4:24])
            for candidate in generations.iterdir()
            if _GENERATION_NAME.fullmatch(candidate.name) is not None
        ]
        ordinal = max(
            time.time_ns(),
            max(previous_ordinals, default=-1) + 1,
        )
        return f"gen-{ordinal:020d}-{secrets.token_hex(8)}"

    @staticmethod
    def _discard_staging(staging: Path) -> None:
        try:
            if staging.is_symlink() or not staging.is_dir():
                return
            children = list(staging.iterdir())
            if any(child.is_symlink() or not child.is_file() for child in children):
                return
            for child in children:
                child.unlink()
            staging.rmdir()
        except OSError:
            pass

    @classmethod
    def _prune_generations(
        cls,
        generations: Path,
        *,
        commits: Path,
        current_generation: str,
    ) -> None:
        """Retain the committed generation and two predecessors for bounded recovery."""

        try:
            if commits.is_symlink() or not commits.is_dir():
                return
            candidates = sorted(
                (
                    candidate
                    for candidate in generations.iterdir()
                    if _GENERATION_NAME.fullmatch(candidate.name) is not None
                ),
                key=lambda candidate: candidate.name,
                reverse=True,
            )
            committed_names = {
                receipt.name[:-5]
                for receipt in commits.iterdir()
                if receipt.name.endswith(".json")
                and _GENERATION_NAME.fullmatch(receipt.name[:-5]) is not None
                and not receipt.is_symlink()
                and receipt.is_file()
            }
            predecessors = [
                candidate
                for candidate in candidates
                if candidate.name < current_generation
                and candidate.name in committed_names
            ][: _GENERATIONS_TO_KEEP - 1]
            keep = {current_generation, *(candidate.name for candidate in predecessors)}
            changed = False
            for candidate in candidates:
                if candidate.name in keep or candidate.is_symlink() or not candidate.is_dir():
                    continue
                children = list(candidate.iterdir())
                if (
                    {child.name for child in children} != _BUNDLE_FILES
                    or any(child.is_symlink() or not child.is_file() for child in children)
                ):
                    continue
                for child in children:
                    child.unlink()
                candidate.rmdir()
                changed = True
            for receipt in commits.iterdir():
                generation_name = (
                    receipt.name[:-5] if receipt.name.endswith(".json") else ""
                )
                if (
                    generation_name in keep
                    or _GENERATION_NAME.fullmatch(generation_name) is None
                    or receipt.is_symlink()
                    or not receipt.is_file()
                ):
                    continue
                receipt.unlink()
                changed = True
            if changed:
                cls._fsync_directory(generations)
                cls._fsync_directory(commits)
        except OSError:
            # Pruning is bounded housekeeping after the new pointer is already durable.
            # A failure leaves extra independently verifiable generations, not a torn run.
            pass

    def _load(self) -> None:
        directories = self._root.iterdir() if self._root.exists() else ()
        for directory in sorted(directories, key=lambda value: value.name):
            if directory.name.startswith("."):
                continue
            if directory.is_symlink():
                if directory.name.startswith("run_game_"):
                    raise GenericLabRunError(
                        "generic Lab saved run cannot be a symbolic link"
                    )
                continue
            if not directory.is_dir():
                continue
            if not self._has_durable_candidate(directory):
                # An interrupted first write may leave only a hidden staging directory.
                # It was never committed and must not prevent other saved runs loading.
                continue
            try:
                record = self._read(directory)
            except (
                GenericLabRunError,
                LabContractError,
                OSError,
                TypeError,
                ValueError,
            ) as error:
                raise GenericLabRunError(
                    f"generic Lab saved run {directory.name!r} is unavailable"
                ) from error
            self._records[record.contract.run_id] = record

    def _read(self, directory: Path) -> _GenericRecord:
        self._assert_no_symlink_components(directory)
        if directory.is_symlink() or not directory.is_dir():
            raise GenericLabRunError("generic Lab run directory is invalid")
        generations = self._generation_directories(directory)
        commits = self._commit_receipts(directory)
        legacy_present = any((directory / name).exists() for name in _BUNDLE_FILES)
        pointer_path = directory / _CURRENT_FILE
        pointer_present = pointer_path.exists() or pointer_path.is_symlink()
        pointer_generation: str | None = None
        if pointer_present:
            try:
                pointer_generation = self._read_pointer(
                    pointer_path, run_id=directory.name
                )
            except (GenericLabRunError, OSError, ValueError):
                pointer_generation = None

        candidates: list[tuple[Path, bool]]
        if pointer_generation is not None:
            pointed = generations.get(pointer_generation)
            candidates = [] if pointed is None else [(pointed, False)]
            candidates.extend(
                (value, True)
                for name, value in sorted(
                    generations.items(), key=lambda item: item[0], reverse=True
                )
                if name < pointer_generation and name in commits
            )
        elif pointer_present:
            # A damaged pointer cannot prove that a newer complete generation crossed
            # the commit boundary. Only hash-bound post-pointer receipts are eligible.
            candidates = [
                (value, True)
                for name, value in sorted(
                    generations.items(), key=lambda item: item[0], reverse=True
                )
                if name in commits
            ]
        elif legacy_present:
            # A flat v1 bundle predates generation pointers. It remains authoritative
            # until a future persist atomically publishes a pointer.
            try:
                return self._read_bundle(
                    directory,
                    run_id=directory.name,
                    legacy=True,
                )
            except (
                GenericLabRunError,
                LabContractError,
                OSError,
                TypeError,
                ValueError,
            ):
                candidates = [
                    (value, True)
                    for name, value in sorted(
                        generations.items(), key=lambda item: item[0], reverse=True
                    )
                    if name in commits
                ]
        else:
            # A complete directory without either an atomic pointer or a post-pointer
            # commit receipt is only staged data and must never be promoted.
            candidates = [
                (value, True)
                for name, value in sorted(
                    generations.items(), key=lambda item: item[0], reverse=True
                )
                if name in commits
            ]

        for candidate, requires_receipt in candidates:
            try:
                return (
                    self._read_committed_bundle(
                        candidate,
                        receipt=commits[candidate.name],
                        run_id=directory.name,
                    )
                    if requires_receipt
                    else self._read_bundle(
                        candidate,
                        run_id=directory.name,
                        legacy=False,
                    )
                )
            except (
                GenericLabRunError,
                LabContractError,
                OSError,
                TypeError,
                ValueError,
            ):
                continue
        if legacy_present:
            return self._read_bundle(
                directory,
                run_id=directory.name,
                legacy=True,
            )
        raise GenericLabRunError("generic Lab run has no valid durable generation")

    def _read_bundle(
        self,
        directory: Path,
        *,
        run_id: str,
        legacy: bool,
    ) -> _GenericRecord:
        self._assert_bundle_directory(directory, legacy=legacy)
        manifest = self._read_json(directory / _RECORD_FILE)
        if set(manifest) != {
            "authority_kind",
            "cartridge_sha256",
            "contract_sha256",
            "created_at_epoch_ms",
            "projection_sha256",
            "run_id",
            "schema_version",
            "source_id",
            "state_sha256",
        } or manifest.get("schema_version") != GENERIC_LAB_RUN_RECORD_SCHEMA_VERSION:
            raise GenericLabRunError("generic Lab run manifest is invalid")
        assert_public_projection_safe(manifest, path="generic_lab_run.manifest")
        authority_kind = GameAuthorityGateway.validate_kind(manifest.get("authority_kind"))
        if manifest.get("source_id") is not None:
            raise GenericLabRunError("generic Lab run persisted a source capability")
        created_at = manifest.get("created_at_epoch_ms")
        if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
            raise GenericLabRunError("generic Lab run creation time is invalid")
        contract = RunContract.from_dict(self._read_json(directory / _CONTRACT_FILE))
        state = RunState.from_dict(self._read_json(directory / _STATE_FILE))
        projection = ReplayProjection.from_dict(self._read_json(directory / _PROJECTION_FILE))
        cartridge = RaceCartridge.from_dict(self._read_json(directory / _CARTRIDGE_FILE))
        if (
            manifest["run_id"] != contract.run_id
            or run_id != contract.run_id
            or manifest["contract_sha256"] != contract.contract_sha256
            or manifest["state_sha256"] != state.state_sha256
            or manifest["projection_sha256"] != projection.projection_sha256
            or manifest["cartridge_sha256"] != cartridge.cartridge_sha256
            or state.run_id != contract.run_id
            or projection.run_id != contract.run_id
            or cartridge.state != state
        ):
            raise GenericLabRunError("generic Lab run artifact bindings differ")
        record = _GenericRecord(
            contract=contract,
            state=state,
            projection=projection,
            cartridge=cartridge,
            authority_kind=authority_kind,
            source_id=None,
            authority_available=False,
            created_at_epoch_ms=created_at,
        )
        self._assert_evidence_bindings(record)
        return record

    def _read_committed_bundle(
        self,
        directory: Path,
        *,
        receipt: Path,
        run_id: str,
    ) -> _GenericRecord:
        record = self._read_bundle(directory, run_id=run_id, legacy=False)
        commit = self._read_json(receipt)
        expected_fields = {
            "authority_kind",
            "cartridge_sha256",
            "contract_sha256",
            "created_at_epoch_ms",
            "generation",
            "projection_sha256",
            "record_sha256",
            "run_id",
            "schema_version",
            "state_sha256",
        }
        if set(commit) != expected_fields:
            raise GenericLabRunError("generic Lab generation commit is invalid")
        assert_public_projection_safe(
            commit, path="generic_lab_run.generation_commit"
        )
        manifest = self._read_json(directory / _RECORD_FILE)
        if (
            commit.get("schema_version") != _GENERATION_COMMIT_SCHEMA_VERSION
            or commit.get("generation") != directory.name
            or commit.get("run_id") != run_id
            or commit.get("authority_kind") != record.authority_kind
            or commit.get("created_at_epoch_ms") != record.created_at_epoch_ms
            or commit.get("record_sha256") != canonical_sha256(manifest)
            or commit.get("contract_sha256") != record.contract.contract_sha256
            or commit.get("state_sha256") != record.state.state_sha256
            or commit.get("projection_sha256")
            != record.projection.projection_sha256
            or commit.get("cartridge_sha256")
            != record.cartridge.cartridge_sha256
        ):
            raise GenericLabRunError("generic Lab generation commit bindings differ")
        return record

    @staticmethod
    def _has_durable_candidate(directory: Path) -> bool:
        if (directory / _CURRENT_FILE).exists() or (
            directory / _CURRENT_FILE
        ).is_symlink():
            return True
        if any((directory / name).exists() for name in _BUNDLE_FILES):
            return True
        commits = directory / _COMMITS_DIRECTORY
        if commits.is_symlink():
            return True
        if commits.is_dir() and any(
            value.name.endswith(".json")
            and _GENERATION_NAME.fullmatch(value.name[:-5]) is not None
            for value in commits.iterdir()
        ):
            return True
        generations = directory / _GENERATIONS_DIRECTORY
        return generations.is_symlink()

    def _generation_directories(self, directory: Path) -> dict[str, Path]:
        generations = directory / _GENERATIONS_DIRECTORY
        if not generations.exists() and not generations.is_symlink():
            return {}
        if generations.is_symlink() or not generations.is_dir():
            raise GenericLabRunError("generic Lab generations directory is invalid")
        result: dict[str, Path] = {}
        for candidate in generations.iterdir():
            if _GENERATION_NAME.fullmatch(candidate.name) is None:
                continue
            if candidate.is_symlink() or not candidate.is_dir():
                raise GenericLabRunError("generic Lab generation is invalid")
            result[candidate.name] = candidate
        return result

    def _commit_receipts(self, directory: Path) -> dict[str, Path]:
        commits = directory / _COMMITS_DIRECTORY
        if not commits.exists() and not commits.is_symlink():
            return {}
        if commits.is_symlink() or not commits.is_dir():
            raise GenericLabRunError("generic Lab commits directory is invalid")
        result: dict[str, Path] = {}
        for receipt in commits.iterdir():
            generation = receipt.name[:-5] if receipt.name.endswith(".json") else ""
            if _GENERATION_NAME.fullmatch(generation) is None:
                continue
            if receipt.is_symlink() or not receipt.is_file():
                raise GenericLabRunError("generic Lab generation commit is invalid")
            result[generation] = receipt
        return result

    def _read_pointer(self, path: Path, *, run_id: str) -> str:
        pointer = self._read_json(path)
        if set(pointer) != {"generation", "run_id", "schema_version"}:
            raise GenericLabRunError("generic Lab generation pointer is invalid")
        assert_public_projection_safe(
            pointer, path="generic_lab_run.generation_pointer"
        )
        generation = pointer.get("generation")
        if (
            pointer.get("schema_version") != _GENERATION_POINTER_SCHEMA_VERSION
            or pointer.get("run_id") != run_id
            or not isinstance(generation, str)
            or _GENERATION_NAME.fullmatch(generation) is None
        ):
            raise GenericLabRunError("generic Lab generation pointer is invalid")
        return generation

    @staticmethod
    def _assert_bundle_directory(directory: Path, *, legacy: bool) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise GenericLabRunError("generic Lab artifact bundle is invalid")
        names = {
            value.name
            for value in directory.iterdir()
            if not (legacy and value.name.startswith("."))
        }
        allowed = (
            _BUNDLE_FILES
            | frozenset(
                {
                    _COMMITS_DIRECTORY,
                    _CURRENT_FILE,
                    _GENERATIONS_DIRECTORY,
                }
            )
            if legacy
            else _BUNDLE_FILES
        )
        invalid = (
            names != allowed
            if not legacy
            else not _BUNDLE_FILES.issubset(names) or not names <= allowed
        )
        if invalid:
            raise GenericLabRunError("generic Lab artifact bundle is invalid")

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError as error:
            raise GenericLabRunError("generic Lab artifact is missing") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise GenericLabRunError("generic Lab artifact cannot be a symbolic link")
        limit = 8 * 1024 * 1024
        if metadata.st_size > limit:
            raise GenericLabRunError("generic Lab artifact is too large")
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise GenericLabRunError("generic Lab artifact is invalid")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = -1
                payload = source.read(limit + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(payload) > limit:
            raise GenericLabRunError("generic Lab artifact is too large")
        value = strict_json_loads(payload)
        if not isinstance(value, Mapping):
            raise GenericLabRunError("generic Lab artifact is invalid")
        return value

    @staticmethod
    def _public_record(record: _GenericRecord) -> Mapping[str, Any]:
        summary = {
            "authority_available": record.authority_available,
            "authority_kind": record.authority_kind,
            "created_at_epoch_ms": record.created_at_epoch_ms,
            "replay_available": record.state.status
            in {RunLifecycle.COMPLETED, RunLifecycle.SEALED, RunLifecycle.VERIFIED},
            "resume_supported": False,
            "run_id": record.contract.run_id,
            "video_available": False,
        }
        assert_public_projection_safe(summary, path="generic_lab_run.summary")
        return {
            **summary,
            "cartridge": record.cartridge.as_dict(),
            "contract": record.contract.as_dict(),
            "state": record.state.as_dict(),
        }


__all__ = [
    "AttachedGameRun",
    "GENERIC_LAB_RUN_RECORD_SCHEMA_VERSION",
    "GenericLabRunError",
    "GenericLabRunNotFoundError",
    "GenericLabRunService",
]
