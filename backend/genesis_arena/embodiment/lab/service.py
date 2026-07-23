"""Credential-free Lab run records bound to the live Labyrinth authority.

The Lab product needs a stable, browser-safe identity for a run that is separate from an
ephemeral provider episode.  This module creates that identity from the versioned Lab contracts,
stores only public projections, and delegates all gameplay authority to ``LiveLabyrinthService``.

There is intentionally no resume implementation here.  The current live Labyrinth executor has
no atomic authority checkpoint interface, so every record advertises ``resume_supported=False``.
Completed replays remain durable; interrupted live episodes remain an honest non-resumable record.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from ..live_labyrinth import (
    LIVE_TASK_ID,
    MAX_CORRIDOR_COMMAND_CELLS,
    MAX_LIVE_PROVIDER_CALLS,
    MAX_LIVE_SPECTATOR_FRAMES,
    MAX_LIVE_SPECTATOR_PATH_CELLS,
    LiveLabyrinthNotFoundError,
    LiveLabyrinthNotReadyError,
    VisionRange,
    compose_labyrinth_system_prompt,
    labyrinth_protocol_prompt_sha256,
    maze_navigation_skill_sha256,
    normalize_vision_range,
)
from ..maze_maps import MazeMapSpec, default_maze_map_spec
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
from .games import GAME_CATALOG, GameCatalogError

LAB_RUN_RECORD_SCHEMA_VERSION = "worldeval/lab-run-record/1"
_RUNS_DIRECTORY_NAME = "lab"
_RUN_RECORD_FILE = "record.json"
_CONTRACT_FILE = "contract.json"
_STATE_FILE = "state.json"
_PROJECTION_FILE = "projection.json"
_CARTRIDGE_FILE = "cartridge.json"
_VIDEO_FILE = "labyrinth-run-broadcast.mp4"
# A native 1080p maze replay can be materially larger than its JSON cartridge, but it should
# still have a fixed upper bound before the Lab copies it into its private artifact store.  The
# current Movie Maker renderer produces a much smaller file; this limit is intentionally a
# defensive ceiling rather than a rendering target.
_MAX_ARCHIVED_VIDEO_BYTES = 512 * 1024 * 1024
_MIN_ARCHIVED_VIDEO_BYTES = 1_024
_VIDEO_COPY_CHUNK_BYTES = 64 * 1024
_VIDEO_PROTECTED_TEXT = frozenset(
    (
        b"api_key",
        b"authorization",
        b"navigation_memory",
        b"provider_request",
        b"provider_response",
        b"raw_output",
        b"scratchpad",
        b"system_prompt",
        b"user_prompt",
    )
)
_VIDEO_SECRET_PATTERN = re.compile(rb"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}")
_VIDEO_PROTECTED_TAIL_BYTES = max(
    max(len(marker) for marker in _VIDEO_PROTECTED_TEXT) - 1,
    63,
)
_LIVE_SKILL_MODES = frozenset(("none", "maze-navigation-v1"))
# The current Lab composer accepts only an explicitly labelled OpenAI session key.  Saved
# contracts from other provider paths stay inspectable/cloneable, but cannot be launched through
# this endpoint until the UI collects and labels those provider-specific credentials correctly.
_DRAFT_LAUNCH_PROVIDER = "openai"
_LIVE_LABYRINTH_RUNTIME_VERSION = "worldeval-labyrinth-live-v1"
_LIVE_LABYRINTH_SCENARIO_ID = "live-labyrinth"
_LIVE_LABYRINTH_SEATS = (
    ("entrant_0", "Sol"),
    ("entrant_1", "Terra"),
    ("entrant_2", "Luna"),
)
_UPSTREAM_TO_LIFECYCLE = {
    "queued": RunLifecycle.QUEUED,
    "running": RunLifecycle.RUNNING,
    "completed": RunLifecycle.COMPLETED,
    "failed": RunLifecycle.FAILED,
    "cancelled": RunLifecycle.CANCELLED,
}
_STABLE_FAILURE_CODES = frozenset(
    (
        "live_labyrinth_cleanup_failed",
        "live_labyrinth_execution_failed",
        "live_provider_credential_rejected",
        "live_provider_rate_limited",
        "live_provider_timed_out",
        "live_provider_unavailable",
        "labyrinth_episode_unavailable",
    )
)
LIVE_SPECTATOR_FEED_SCHEMA_VERSION = "worldeval/lab-live-spectator-feed/1"
_LIVE_SPECTATOR_FEED_FIELDS = frozenset(
    {"schema_version", "run_id", "cursor", "reset_required", "frames"}
)
_LIVE_SPECTATOR_SOURCE_FIELDS = frozenset({"cursor", "reset_required", "frames"})
_LIVE_SPECTATOR_FRAME_FIELDS = frozenset({"sequence", "frame"})
_LIVE_OBSERVER_SCHEMA_VERSION = "worldarena/live-labyrinth-observer/1"
_LIVE_OBSERVER_FIELDS = frozenset(
    {"schema_version", "status", "tick", "provider_calls", "map", "racers"}
)
_LIVE_OBSERVER_MAP_FIELDS = frozenset({"map_id", "map_sha256", "rows", "start", "exit"})
_LIVE_OBSERVER_RACER_FIELDS = frozenset(
    {
        "participant_id",
        "entrant_id",
        "display_name",
        "color",
        "position",
        "path",
        "visible_cells",
        "provider_calls",
        "finished",
    }
)
_MAX_LIVE_SPECTATOR_MAP_CELLS = 4_096


class LabRunError(RuntimeError):
    """A Lab record cannot be created, read, or synchronized safely."""


class LabRunNotFoundError(KeyError):
    """The requested Lab run has no known public record."""


class LiveLabyrinthProjectionSource(Protocol):
    """The public subset of the existing live authority used by Lab records."""

    async def status(self, episode_id: str) -> Mapping[str, object]: ...

    async def replay(self, episode_id: str) -> Mapping[str, Any]: ...

    async def video_path(self, episode_id: str) -> Path | None: ...

    async def observer(self, episode_id: str) -> Mapping[str, Any] | None: ...

    async def spectator_frames(
        self, episode_id: str, *, after_sequence: int = 0
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class DraftLabyrinthLaunch:
    """Credential-free execution values extracted from one compatible draft contract.

    This is deliberately a *fresh* execution description.  It has no checkpoint or episode
    identity, so callers cannot accidentally present a clone launch as a resume operation.
    """

    entrants: tuple[RunEntrant, ...]
    map_spec: MazeMapSpec
    max_provider_calls: int
    provider: str
    skill_mode: str
    vision_range_cells: VisionRange


@dataclass
class _LabRunRecord:
    contract: RunContract
    state: RunState
    projection: ReplayProjection
    cartridge: RaceCartridge
    episode_id: str | None
    created_at_epoch_ms: int
    upstream_available: bool
    video_state: str = "unavailable"


def _safe_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LabRunError(f"{label} is invalid")
    copied = strict_json_loads(canonical_json_bytes(value))
    if not isinstance(copied, dict):  # Defensive: canonical JSON preserves objects.
        raise LabRunError(f"{label} is invalid")
    assert_public_projection_safe(copied, path=label)
    return copied


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LabRunError(f"{label} is invalid")
    return value


def _safe_spectator_frame(value: object, *, record: _LabRunRecord) -> dict[str, Any]:
    """Accept only the pre-existing, provider-free observer v1 frame shape."""

    frame = _safe_mapping(value, label="live_labyrinth.spectator.frame")
    if set(frame) != _LIVE_OBSERVER_FIELDS:
        raise LabRunError("live labyrinth spectator frame fields are invalid")
    if frame.get("schema_version") != _LIVE_OBSERVER_SCHEMA_VERSION:
        raise LabRunError("live labyrinth spectator frame schema is invalid")
    if frame.get("status") not in {"queued", "running"}:
        raise LabRunError("live labyrinth spectator frame state is invalid")
    _nonnegative_int(frame.get("tick"), label="live labyrinth spectator frame tick")
    _nonnegative_int(frame.get("provider_calls"), label="live labyrinth spectator frame calls")

    map_value = frame.get("map")
    contract = record.contract.as_dict()
    if (
        not isinstance(map_value, Mapping)
        or set(map_value) != _LIVE_OBSERVER_MAP_FIELDS
        or map_value.get("map_id") != contract["map_id"]
        or map_value.get("map_sha256") != contract["map_sha256"]
        or not isinstance(map_value.get("rows"), list)
        or not map_value["rows"]
        or any(not isinstance(row, str) for row in map_value["rows"])
    ):
        raise LabRunError("live labyrinth spectator map is invalid")
    rows = map_value["rows"]
    if (
        len(rows) > _MAX_LIVE_SPECTATOR_MAP_CELLS
        or not rows[0]
        or any(
            not row
            or len(row) != len(rows[0])
            or len(row) > _MAX_LIVE_SPECTATOR_MAP_CELLS
            or any(cell not in {"#", ".", "S", "E"} for cell in row)
            for row in rows
        )
        or len(rows) * len(rows[0]) > _MAX_LIVE_SPECTATOR_MAP_CELLS
    ):
        raise LabRunError("live labyrinth spectator map geometry is invalid")
    passable_cells = {
        (x, y) for y, row in enumerate(rows) for x, cell in enumerate(row) if cell != "#"
    }

    def require_passable_cell(cell: object) -> tuple[int, int]:
        if (
            not isinstance(cell, list)
            or len(cell) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in cell)
        ):
            raise LabRunError("live labyrinth spectator racer geometry is invalid")
        position = (cell[0], cell[1])
        if position not in passable_cells:
            raise LabRunError("live labyrinth spectator racer geometry is invalid")
        return position

    for endpoint in (map_value.get("start"), map_value.get("exit")):
        require_passable_cell(endpoint)

    racers = frame.get("racers")
    if not isinstance(racers, list) or len(racers) != 3:
        raise LabRunError("live labyrinth spectator racers are invalid")
    contract_entrants = contract.get("entrants")
    if not isinstance(contract_entrants, list) or len(contract_entrants) != len(racers):
        raise LabRunError("live labyrinth spectator entrant contract is invalid")
    participant_ids: set[str] = set()
    calls = 0
    for index, racer in enumerate(racers):
        if not isinstance(racer, Mapping) or set(racer) != _LIVE_OBSERVER_RACER_FIELDS:
            raise LabRunError("live labyrinth spectator racer fields are invalid")
        participant_id = racer.get("participant_id")
        if (
            not isinstance(participant_id, str)
            or not participant_id
            or participant_id in participant_ids
        ):
            raise LabRunError("live labyrinth spectator racer identity is invalid")
        participant_ids.add(participant_id)
        if any(
            not isinstance(racer.get(name), str) or not racer[name]
            for name in ("entrant_id", "display_name", "color")
        ):
            raise LabRunError("live labyrinth spectator racer identity is invalid")
        contract_entrant = contract_entrants[index]
        if (
            not isinstance(contract_entrant, Mapping)
            or participant_id != f"participant_{index}"
            or racer.get("entrant_id") != contract_entrant.get("entrant_id")
            or racer.get("display_name") != contract_entrant.get("display_name")
        ):
            raise LabRunError("live labyrinth spectator racer differs from the run contract")
        position = require_passable_cell(racer.get("position"))
        path = racer.get("path")
        visible_cells = racer.get("visible_cells")
        if (
            not isinstance(path, list)
            or not path
            or len(path) > MAX_LIVE_SPECTATOR_PATH_CELLS
            or not isinstance(visible_cells, list)
            or len(visible_cells) > len(passable_cells)
        ):
            raise LabRunError("live labyrinth spectator racer geometry is invalid")
        path_cells = [require_passable_cell(cell) for cell in path]
        for cell in visible_cells:
            require_passable_cell(cell)
        if path_cells[-1] != position:
            raise LabRunError("live labyrinth spectator racer position is invalid")
        racer_calls = _nonnegative_int(
            racer.get("provider_calls"), label="live labyrinth spectator racer calls"
        )
        if not isinstance(racer.get("finished"), bool):
            raise LabRunError("live labyrinth spectator racer finish is invalid")
        calls += racer_calls
    if calls != frame["provider_calls"]:
        raise LabRunError("live labyrinth spectator call accounting is invalid")
    return frame


def _safe_spectator_source_feed(
    value: object, *, after_sequence: int, record: _LabRunRecord
) -> dict[str, Any]:
    """Validate a transient upstream cursor feed before adding the browser run identity."""

    feed = _safe_mapping(value, label="live_labyrinth.spectator")
    if set(feed) != _LIVE_SPECTATOR_SOURCE_FIELDS:
        raise LabRunError("live labyrinth spectator feed fields are invalid")
    cursor = _nonnegative_int(feed.get("cursor"), label="live labyrinth spectator cursor")
    reset_required = feed.get("reset_required")
    frames = feed.get("frames")
    if not isinstance(reset_required, bool) or not isinstance(frames, list):
        raise LabRunError("live labyrinth spectator feed is invalid")
    if len(frames) > MAX_LIVE_SPECTATOR_FRAMES:
        raise LabRunError("live labyrinth spectator feed is too large")
    safe_frames: list[dict[str, Any]] = []
    previous_sequence = 0
    for entry in frames:
        if not isinstance(entry, Mapping) or set(entry) != _LIVE_SPECTATOR_FRAME_FIELDS:
            raise LabRunError("live labyrinth spectator entry is invalid")
        sequence = _nonnegative_int(
            entry.get("sequence"), label="live labyrinth spectator sequence"
        )
        if sequence == 0 or sequence <= previous_sequence or sequence > cursor:
            raise LabRunError("live labyrinth spectator sequence is invalid")
        previous_sequence = sequence
        safe_frames.append(
            {
                "sequence": sequence,
                "frame": _safe_spectator_frame(entry.get("frame"), record=record),
            }
        )
    if reset_required and len(safe_frames) != 1:
        raise LabRunError("live labyrinth spectator reset is invalid")
    if not reset_required and any(frame["sequence"] <= after_sequence for frame in safe_frames):
        raise LabRunError("live labyrinth spectator cursor is invalid")
    return {"cursor": cursor, "reset_required": reset_required, "frames": safe_frames}


def _safe_live_failure_code(value: object) -> str:
    if isinstance(value, str) and value in _STABLE_FAILURE_CODES:
        return value
    return "live_labyrinth_execution_failed"


def _effective_budget(map_spec: MazeMapSpec, max_provider_calls: int) -> int:
    if isinstance(max_provider_calls, bool) or not isinstance(max_provider_calls, int):
        raise LabRunError("Labyrinth call budget is invalid")
    if not 1 <= max_provider_calls <= MAX_LIVE_PROVIDER_CALLS:
        raise LabRunError("Labyrinth call budget is invalid")
    return min(map_spec.participant_call_budget, max_provider_calls)


class LabRunService:
    """Persist public-safe Labyrinth run records without retaining credentials.

    ``LiveLabyrinthService`` owns providers, credentials, and private evidence.  This service
    knows only an upstream episode identifier and records the allow-listed public state needed by
    the Lab UI.  It is deliberately usable with a fake source in tests.
    """

    def __init__(
        self,
        *,
        runs_dir: Path,
        live_labyrinth: LiveLabyrinthProjectionSource | None = None,
    ) -> None:
        self._root = Path(runs_dir) / _RUNS_DIRECTORY_NAME
        self._live_labyrinth = live_labyrinth
        self._records: dict[str, _LabRunRecord] = {}
        self._lock = asyncio.Lock()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self._root, 0o700)
        except OSError:
            pass
        self._load_durable_records()

    @property
    def runs_dir(self) -> Path:
        """The private on-host directory that contains safe Lab artifacts."""

        return self._root

    async def create_live_labyrinth(
        self,
        *,
        episode_id: str,
        entrants: Sequence[RunEntrant],
        provider_call_budget: int,
        vision_range_cells: VisionRange,
        skill_mode: str,
        mode: RunMode | str = RunMode.EXPLORATORY,
        map_spec: MazeMapSpec | None = None,
    ) -> Mapping[str, Any]:
        """Create a canonical Lab contract after the authority accepted an episode.

        No credential, adapter, request body, prompt text, scratchpad, or observation crosses
        this boundary.  The API route owns those transient values and supplies only the public
        episode identifier and declared configuration.
        """

        if not isinstance(episode_id, str) or not episode_id.startswith("ep_"):
            raise LabRunError("Labyrinth episode identity is invalid")
        if skill_mode not in _LIVE_SKILL_MODES:
            raise LabRunError("Labyrinth skill mode is invalid")
        parsed_mode = self._parse_mode(mode)
        normalized_map = default_maze_map_spec() if map_spec is None else map_spec
        if not isinstance(normalized_map, MazeMapSpec):
            raise LabRunError("Labyrinth map is invalid")
        try:
            normalized_vision = normalize_vision_range(vision_range_cells)
        except ValueError as error:
            raise LabRunError("Labyrinth vision range is invalid") from error
        effective_budget = _effective_budget(normalized_map, provider_call_budget)
        normalized_entrants = self._normalize_entrants(entrants)
        run_id = f"run_labyrinth_{secrets.token_hex(12)}"
        configuration: dict[str, object] = {
            "corridor_commands": True,
            "max_corridor_cells": MAX_CORRIDOR_COMMAND_CELLS,
            "vision_occlusion": "straight_line_walls",
            "vision_range_cells": normalized_vision,
        }
        if skill_mode == "maze-navigation-v1":
            configuration["skill_sha256"] = maze_navigation_skill_sha256()
        contract = RunContract.create(
            run_id=run_id,
            game_id="labyrinth-run",
            game_version=LIVE_TASK_ID,
            mode=parsed_mode,
            entrants=normalized_entrants,
            seed_policy={"kind": "frozen_map", "map_sha256": normalized_map.map_sha256},
            budget={
                "maximum_ticks": 4 * effective_budget,
                "participant_call_budget": effective_budget,
                "scope": "per_participant",
            },
            configuration=configuration,
            runtime_version="worldeval-labyrinth-live-v1",
            scenario_id="live-labyrinth",
            map_id=normalized_map.map_id,
            map_sha256=normalized_map.map_sha256,
            skill_mode=skill_mode,
            prompt_sha256=self._prompt_sha256(skill_mode),
        )
        state = RunState.create(
            run_id=contract.run_id,
            contract_sha256=contract.contract_sha256,
            status=RunLifecycle.QUEUED,
        )
        record = self._new_record(
            contract=contract,
            state=state,
            episode_id=episode_id,
            created_at_epoch_ms=int(time.time() * 1000),
            upstream_available=True,
        )
        async with self._lock:
            self._records[contract.run_id] = record
            self._persist(record)
        return self._public_record(record)

    async def clone(self, run_id: str, *, changes: Mapping[str, object]) -> Mapping[str, Any]:
        """Make a separate draft contract with a public, immutable lineage diff."""

        if not isinstance(changes, Mapping):
            raise LabRunError("Lab run clone changes are invalid")
        async with self._lock:
            parent = self._record_or_raise(run_id)
            normalized_changes = dict(changes)
            parent_body = parent.contract.as_dict()
            skill_mode = normalized_changes.get("skill_mode", parent_body["skill_mode"])
            if skill_mode not in _LIVE_SKILL_MODES:
                raise LabRunError("Labyrinth skill mode is invalid")
            if "configuration" in normalized_changes:
                requested_configuration = normalized_changes["configuration"]
                if not isinstance(requested_configuration, Mapping):
                    raise LabRunError("Lab run clone configuration is invalid")
                merged_configuration = dict(parent.contract.configuration)
                merged_configuration.update(requested_configuration)
                normalized_changes["configuration"] = merged_configuration
            if "skill_mode" in normalized_changes:
                normalized_changes["prompt_sha256"] = self._prompt_sha256(str(skill_mode))
                configuration = dict(
                    normalized_changes.get("configuration", parent.contract.configuration)
                )
                if skill_mode == "maze-navigation-v1":
                    configuration["skill_sha256"] = maze_navigation_skill_sha256()
                else:
                    configuration.pop("skill_sha256", None)
                normalized_changes["configuration"] = configuration
            clone = parent.contract.clone(
                run_id=f"run_labyrinth_{secrets.token_hex(12)}", changes=normalized_changes
            )
            state = RunState.create(
                run_id=clone.run_id,
                contract_sha256=clone.contract_sha256,
                status=RunLifecycle.DRAFT,
            )
            record = self._new_record(
                contract=clone,
                state=state,
                episode_id=None,
                created_at_epoch_ms=int(time.time() * 1000),
                upstream_available=False,
            )
            self._records[clone.run_id] = record
            self._persist(record)
            return self._public_record(record)

    async def reserve_draft_labyrinth_launch(self, run_id: str) -> DraftLabyrinthLaunch:
        """Reserve one compatible draft clone for a new live Labyrinth episode.

        A draft is marked queued and persisted while the service lock is held before any provider
        adapter or live authority is constructed.  That makes a second concurrent launch reject
        deterministically, while preserving the clone's immutable contract and lineage.
        """

        async with self._lock:
            record = self._record_or_raise(run_id)
            if (
                record.state.status is not RunLifecycle.DRAFT
                or record.episode_id is not None
                or record.upstream_available
            ):
                raise LabRunError("Lab run is not an unlaunched draft")
            launch = self._draft_labyrinth_launch(record.contract)
            queued = self._replacement_record(
                record,
                state=record.state.transition(RunLifecycle.QUEUED),
                episode_id=None,
                upstream_available=False,
            )
            # Persist the replacement before publishing it to the in-memory index.  No live
            # authority exists yet, so a write failure cannot orphan a provider episode.
            self._persist(queued)
            self._records[run_id] = queued
            return launch

    async def attach_draft_labyrinth_episode(
        self, run_id: str, *, episode_id: str
    ) -> Mapping[str, Any]:
        """Attach a freshly-created authority episode to an already-reserved clone.

        The contract is validated again at this boundary.  A failed persistence attempt leaves
        the in-memory record unmodified so the API layer can cancel the newly-created authority
        and mark the queued draft failed without ever retaining its episode handle durably.
        """

        if not isinstance(episode_id, str) or not episode_id.startswith("ep_"):
            raise LabRunError("Labyrinth episode identity is invalid")
        async with self._lock:
            record = self._record_or_raise(run_id)
            if (
                record.state.status is not RunLifecycle.QUEUED
                or record.episode_id is not None
                or record.upstream_available
            ):
                raise LabRunError("Lab run is not awaiting a fresh authority episode")
            self._draft_labyrinth_launch(record.contract)
            activated = self._replacement_record(
                record,
                state=record.state,
                episode_id=episode_id,
                upstream_available=True,
            )
            self._persist(activated)
            self._records[run_id] = activated
            return self._public_record(activated)

    async def fail_reserved_draft_labyrinth_launch(self, run_id: str) -> None:
        """Safely terminate an authority-start failure after a draft reservation.

        The failure reason is intentionally fixed and public-safe.  Provider exceptions and
        credentials remain in the API/authority boundary and never cross into a cartridge.
        """

        async with self._lock:
            record = self._record_or_raise(run_id)
            if record.state.status is RunLifecycle.FAILED:
                return
            if (
                record.state.status is not RunLifecycle.QUEUED
                or record.episode_id is not None
                or record.upstream_available
            ):
                raise LabRunError("Lab run is not a reserved draft launch")
            failed = self._replacement_record(
                record,
                state=record.state.transition(
                    RunLifecycle.FAILED,
                    failure_code="labyrinth_episode_unavailable",
                ),
                episode_id=None,
                upstream_available=False,
            )
            self._persist(failed)
            self._records[run_id] = failed

    async def list_runs(self) -> list[Mapping[str, Any]]:
        """Return safe summaries ordered by creation time, newest first."""

        async with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda item: (item.created_at_epoch_ms, item.contract.run_id),
                reverse=True,
            )
        for record in records:
            await self._synchronize(record)
        return [self._public_record(record) for record in records]

    async def get_run(self, run_id: str) -> Mapping[str, Any]:
        record = await self._get_record(run_id)
        await self._synchronize(record)
        return self._public_record(record)

    async def projection(self, run_id: str) -> Mapping[str, Any]:
        record = await self._get_record(run_id)
        await self._synchronize(record)
        return dict(record.projection.as_dict())

    async def spectator(self, run_id: str, *, after_sequence: int = 0) -> Mapping[str, Any]:
        """Return a transient, authenticated-only feed of safe live observer frames.

        The run id is the only identity this product layer returns.  The upstream episode handle
        stays process-private, and this feed is intentionally not persisted into a cartridge or
        replay; the sealed replay remains the authoritative durable record.
        """

        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise LabRunError("Lab spectator cursor is invalid")
        record = await self._get_record(run_id)
        await self._synchronize(record)
        source_feed: dict[str, Any] = {
            "cursor": after_sequence,
            "reset_required": False,
            "frames": [],
        }
        if (
            record.episode_id is not None
            and record.upstream_available
            and self._live_labyrinth is not None
        ):
            source = getattr(self._live_labyrinth, "spectator_frames", None)
            if callable(source):
                try:
                    upstream_feed = await source(record.episode_id, after_sequence=after_sequence)
                except LiveLabyrinthNotFoundError:
                    record.upstream_available = False
                    self._persist(record)
                else:
                    try:
                        source_feed = _safe_spectator_source_feed(
                            upstream_feed,
                            after_sequence=after_sequence,
                            record=record,
                        )
                    except LabContractError as error:
                        raise LabRunError("Live labyrinth spectator feed is invalid") from error
        response = {
            "schema_version": LIVE_SPECTATOR_FEED_SCHEMA_VERSION,
            "run_id": record.contract.run_id,
            **source_feed,
        }
        try:
            return _safe_mapping(response, label="lab_run.spectator")
        except LabContractError as error:
            raise LabRunError("Lab spectator feed is invalid") from error

    async def video_path(self, run_id: str) -> Path | None:
        """Return a verified, durable public MP4 for one completed Lab run.

        The upstream episode handle is intentionally process-local, so this method never serves
        its transient renderer path directly.  A completed run is replayable after a process
        restart only when its checked public broadcast was atomically copied into the run's own
        private artifact directory.
        """

        record = await self._get_record(run_id)
        await self._synchronize(record)
        archived = await asyncio.to_thread(self._archived_video_path, record)
        if archived is None and record.video_state == "ready":
            # A later filesystem change cannot turn an archived video into a stale browser claim.
            # This in-memory status is only a cache; reload revalidates the durable artifact.
            record.video_state = "unavailable"
        return archived

    def benchmark_status(self) -> Mapping[str, object]:
        """Be explicit that no Lab-run result is verified until a recipe is implemented."""

        return {
            "game_id": "labyrinth-run",
            "season_state": "not_started",
            "verified_results": [],
            "message": (
                "No verified Labyrinth benchmark season has been published. "
                "Exploratory Lab runs are intentionally excluded from rankings."
            ),
        }

    async def _get_record(self, run_id: str) -> _LabRunRecord:
        async with self._lock:
            return self._record_or_raise(run_id)

    def _record_or_raise(self, run_id: str) -> _LabRunRecord:
        try:
            return self._records[run_id]
        except KeyError as error:
            raise LabRunNotFoundError(run_id) from error

    async def _synchronize(self, record: _LabRunRecord) -> None:
        """Mirror the current public upstream lifecycle without inventing authority state."""

        if (
            record.episode_id is None
            or not record.upstream_available
            or self._live_labyrinth is None
            or record.state.status in {RunLifecycle.FAILED, RunLifecycle.CANCELLED}
        ):
            return
        try:
            status = await self._live_labyrinth.status(record.episode_id)
        except LiveLabyrinthNotFoundError:
            # A process restart loses the in-memory authority.  Preserve the durable public
            # record but never present it as resumable or silently fabricate a terminal result.
            record.upstream_available = False
            self._persist(record)
            return
        if not isinstance(status, Mapping):
            raise LabRunError("Live Labyrinth returned an invalid public status")
        self._update_video_state(record, status)
        target = _UPSTREAM_TO_LIFECYCLE.get(status.get("state"))
        if target is None:
            raise LabRunError("Live Labyrinth returned an invalid lifecycle state")
        if record.state.status is RunLifecycle.COMPLETED and target is RunLifecycle.COMPLETED:
            # Rendering can finish after the authoritative replay is sealed. Preserve the immutable
            # result while allowing the browser to discover and archive a newly-ready native Godot
            # broadcast.  The private episode capability is never persisted with the archive.
            await self._archive_completed_video(record, status=status)
            self._persist(record)
            return
        if target is RunLifecycle.COMPLETED:
            await self._complete_from_upstream(record)
            await self._archive_completed_video(record, status=status)
            return
        if target in {RunLifecycle.FAILED, RunLifecycle.CANCELLED}:
            failure_code = _safe_live_failure_code(status.get("failure"))
            self._advance_lifecycle(record, target, failure_code=failure_code)
            self._persist(record)
            return
        self._advance_lifecycle(record, target)
        self._refresh_projection(record, observer=await self._observer_frame(record))
        self._persist(record)

    async def _complete_from_upstream(self, record: _LabRunRecord) -> None:
        if self._live_labyrinth is None or record.episode_id is None:
            raise LabRunError("Live Labyrinth authority is unavailable")
        try:
            replay = await self._live_labyrinth.replay(record.episode_id)
        except LiveLabyrinthNotReadyError:
            return
        safe_replay = _safe_mapping(replay, label="live_labyrinth.replay")
        events = safe_replay.get("events")
        if not isinstance(events, list) or any(not isinstance(item, Mapping) for item in events):
            raise LabRunError("Live Labyrinth replay events are invalid")
        # The upstream episode identifier is an internal authority handle.  The Lab's public
        # route can serve replay/video by run id, so it never needs to leak a handle for the
        # lower-level live-episode endpoints.
        snapshot = {
            key: value for key, value in safe_replay.items() if key not in {"episode_id", "events"}
        }
        result_sha256 = canonical_sha256(safe_replay)
        self._advance_lifecycle(
            record,
            RunLifecycle.COMPLETED,
            result_sha256=result_sha256,
            checkpoint_sequence=max(1, record.state.checkpoint_sequence),
        )
        record.projection = ReplayProjection.create(
            run_id=record.contract.run_id,
            contract_sha256=record.contract.contract_sha256,
            status=record.state.status,
            sequence=record.state.checkpoint_sequence,
            snapshot=snapshot,
            events=tuple(dict(item) for item in events),
        )
        record.cartridge = RaceCartridge.create(
            cartridge_id=f"cartridge_{record.contract.run_id}",
            contract=record.contract,
            state=record.state,
            public_projection=record.projection,
        )
        self._persist(record)

    async def _archive_completed_video(
        self, record: _LabRunRecord, *, status: Mapping[str, object]
    ) -> None:
        """Copy one authority-produced public MP4 into its durable run cartridge.

        The live authority owns the source renderer and exposes no replay path or video bytes to
        the browser.  Lab only accepts its path after the authority reports a completed renderer
        as ready, validates it while copying, and stores the fixed public MP4 filename beneath
        the run's private artifact directory.  A failed archive is deliberately non-fatal to the
        completed authority result: the safe JSON replay remains available and this method never
        invents a substitute video.
        """

        if record.state.status is not RunLifecycle.COMPLETED:
            return
        if record.video_state == "ready":
            return
        if await asyncio.to_thread(self._archived_video_path, record) is not None:
            record.video_state = "ready"
            return
        video = status.get("video")
        if not isinstance(video, Mapping) or video.get("state") != "ready":
            return
        if (
            record.episode_id is None
            or not record.upstream_available
            or self._live_labyrinth is None
        ):
            return

        # Do not advertise upstream readiness until the artifact is fully and safely committed.
        record.video_state = "saving"
        try:
            source_path = await self._live_labyrinth.video_path(record.episode_id)
            if not isinstance(source_path, Path):
                raise LabRunError("Live Labyrinth returned no video path")
            await asyncio.to_thread(
                self._copy_public_video,
                source_path,
                self._video_artifact_path(record),
            )
        except LiveLabyrinthNotFoundError:
            # The completed replay is still authoritative and durable, but the just-rendered
            # process-local file can no longer be recovered after an authority restart.
            record.upstream_available = False
            record.video_state = "unavailable"
        except Exception:
            # Renderer and filesystem detail is not browser-safe.  The only observable result is
            # an unavailable optional video; no fake replay or private error is persisted.
            record.video_state = "unavailable"
        else:
            record.video_state = "ready"

    def _advance_lifecycle(
        self,
        record: _LabRunRecord,
        target: RunLifecycle,
        *,
        result_sha256: str | None = None,
        failure_code: str | None = None,
        checkpoint_sequence: int | None = None,
    ) -> None:
        """Apply only legal transitions, filling an elided queued/running edge honestly."""

        current = record.state.status
        if current is target:
            return
        if current is RunLifecycle.DRAFT:
            # Draft clones have no upstream authority and are never synchronized.
            raise LabRunError("A draft Lab run cannot receive an upstream lifecycle update")
        if current is RunLifecycle.QUEUED and target is RunLifecycle.COMPLETED:
            record.state = record.state.transition(RunLifecycle.RUNNING)
            current = record.state.status
        if current is RunLifecycle.QUEUED and target is RunLifecycle.RUNNING:
            record.state = record.state.transition(RunLifecycle.RUNNING)
            return
        if current is RunLifecycle.RUNNING and target is RunLifecycle.COMPLETED:
            record.state = record.state.transition(
                RunLifecycle.COMPLETED,
                checkpoint_sequence=checkpoint_sequence,
                result_sha256=result_sha256,
            )
            return
        if current in {RunLifecycle.QUEUED, RunLifecycle.RUNNING} and target in {
            RunLifecycle.FAILED,
            RunLifecycle.CANCELLED,
        }:
            record.state = record.state.transition(target, failure_code=failure_code)
            return
        if current is target:
            return
        raise LabRunError("Live Labyrinth returned an impossible lifecycle transition")

    def _new_record(
        self,
        *,
        contract: RunContract,
        state: RunState,
        episode_id: str | None,
        created_at_epoch_ms: int,
        upstream_available: bool,
    ) -> _LabRunRecord:
        projection = self._status_projection(contract, state)
        cartridge = RaceCartridge.create(
            cartridge_id=f"cartridge_{contract.run_id}",
            contract=contract,
            state=state,
            public_projection=projection,
        )
        return _LabRunRecord(
            contract=contract,
            state=state,
            projection=projection,
            cartridge=cartridge,
            episode_id=episode_id,
            created_at_epoch_ms=created_at_epoch_ms,
            upstream_available=upstream_available,
        )

    def _replacement_record(
        self,
        record: _LabRunRecord,
        *,
        state: RunState,
        episode_id: str | None,
        upstream_available: bool,
    ) -> _LabRunRecord:
        """Build a whole replacement record for an atomic lifecycle hand-off.

        ``_persist`` writes only canonical public artifacts.  Constructing the replacement first
        avoids mutating the indexed record if that persistence step fails, which is especially
        important when the caller must cancel a just-created live authority afterward.
        """

        replacement = self._new_record(
            contract=record.contract,
            state=state,
            episode_id=episode_id,
            created_at_epoch_ms=record.created_at_epoch_ms,
            upstream_available=upstream_available,
        )
        replacement.video_state = record.video_state
        return replacement

    @staticmethod
    def _draft_labyrinth_launch(contract: RunContract) -> DraftLabyrinthLaunch:
        """Extract only a strictly compatible fresh-live configuration from a draft.

        Cloning deliberately permits a broader set of safe contract mutations for inspection and
        comparison.  Launching is narrower: this current authority can only execute the fixed
        three-seat Labyrinth protocol on its immutable default map with the exact configuration
        it knows how to enforce.  A clone that falls outside that compatibility envelope remains
        a valid saved draft, but is not executable through this endpoint.
        """

        body = contract.as_dict()
        map_spec = default_maze_map_spec()
        if (
            body.get("game_id") != "labyrinth-run"
            or body.get("game_version") != LIVE_TASK_ID
            or body.get("runtime_version") != _LIVE_LABYRINTH_RUNTIME_VERSION
            or body.get("scenario_id") != _LIVE_LABYRINTH_SCENARIO_ID
            or body.get("map_id") != map_spec.map_id
            or body.get("map_sha256") != map_spec.map_sha256
            or body.get("seed_policy") != {"kind": "frozen_map", "map_sha256": map_spec.map_sha256}
        ):
            raise LabRunError("Lab draft is not compatible with live Labyrinth")

        skill_mode = body.get("skill_mode")
        if skill_mode not in _LIVE_SKILL_MODES:
            raise LabRunError("Labyrinth skill mode is invalid")
        configuration = body.get("configuration")
        if not isinstance(configuration, Mapping):
            raise LabRunError("Labyrinth draft configuration is invalid")
        try:
            vision_range_cells = normalize_vision_range(configuration.get("vision_range_cells"))
        except ValueError as error:
            raise LabRunError("Labyrinth vision range is invalid") from error
        expected_configuration: dict[str, object] = {
            "corridor_commands": True,
            "max_corridor_cells": MAX_CORRIDOR_COMMAND_CELLS,
            "vision_occlusion": "straight_line_walls",
            "vision_range_cells": vision_range_cells,
        }
        if skill_mode == "maze-navigation-v1":
            expected_configuration["skill_sha256"] = maze_navigation_skill_sha256()
        if dict(configuration) != expected_configuration:
            raise LabRunError("Labyrinth draft configuration is not executable")
        if body.get("prompt_sha256") != LabRunService._prompt_sha256(str(skill_mode)):
            raise LabRunError("Labyrinth draft prompt binding is invalid")

        budget = body.get("budget")
        if not isinstance(budget, Mapping) or set(budget) != {
            "maximum_ticks",
            "participant_call_budget",
            "scope",
        }:
            raise LabRunError("Labyrinth draft budget is invalid")
        participant_call_budget = budget.get("participant_call_budget")
        if (
            isinstance(participant_call_budget, bool)
            or not isinstance(participant_call_budget, int)
            or not 1
            <= participant_call_budget
            <= min(map_spec.participant_call_budget, MAX_LIVE_PROVIDER_CALLS)
            or budget.get("maximum_ticks") != 4 * participant_call_budget
            or budget.get("scope") != "per_participant"
        ):
            raise LabRunError("Labyrinth draft budget is not executable")

        raw_entrants = body.get("entrants")
        if not isinstance(raw_entrants, list):
            raise LabRunError("Labyrinth draft entrants are invalid")
        try:
            entrants = tuple(RunEntrant.from_dict(value) for value in raw_entrants)
        except LabContractError as error:
            raise LabRunError("Labyrinth draft entrants are invalid") from error
        if tuple((entrant.entrant_id, entrant.display_name) for entrant in entrants) != (
            _LIVE_LABYRINTH_SEATS
        ):
            raise LabRunError("Labyrinth draft entrants are not the live roster")
        providers = {entrant.provider for entrant in entrants}
        if providers != {_DRAFT_LAUNCH_PROVIDER} or any(
            len(entrant.model_id) > 128 for entrant in entrants
        ):
            raise LabRunError("Labyrinth draft provider or model is invalid")
        provider = next(iter(providers))
        return DraftLabyrinthLaunch(
            entrants=entrants,
            map_spec=map_spec,
            max_provider_calls=participant_call_budget,
            provider=provider,
            skill_mode=str(skill_mode),
            vision_range_cells=vision_range_cells,
        )

    def _refresh_projection(
        self, record: _LabRunRecord, *, observer: Mapping[str, object] | None = None
    ) -> None:
        record.projection = self._status_projection(
            record.contract, record.state, observer=observer
        )
        record.cartridge = RaceCartridge.create(
            cartridge_id=f"cartridge_{record.contract.run_id}",
            contract=record.contract,
            state=record.state,
            public_projection=record.projection,
        )

    @staticmethod
    def _status_projection(
        contract: RunContract,
        state: RunState,
        *,
        observer: Mapping[str, object] | None = None,
    ) -> ReplayProjection:
        body = contract.as_dict()
        configuration = body["configuration"]
        snapshot = {
            "game": {
                "game_id": body["game_id"],
                "game_version": body["game_version"],
                "mode": body["mode"],
            },
            "map": {
                "map_id": body["map_id"],
                "map_sha256": body["map_sha256"],
            },
            "racers": list(body["entrants"]),
            "vision": {
                "occlusion": configuration["vision_occlusion"],
                "range_cells": configuration["vision_range_cells"],
            },
        }
        if observer is not None:
            snapshot["arena"] = dict(observer)
        return ReplayProjection.create(
            run_id=contract.run_id,
            contract_sha256=contract.contract_sha256,
            status=state.status,
            sequence=state.checkpoint_sequence,
            snapshot=snapshot,
        )

    async def _observer_frame(self, record: _LabRunRecord) -> Mapping[str, object] | None:
        if self._live_labyrinth is None or record.episode_id is None:
            return None
        observer = getattr(self._live_labyrinth, "observer", None)
        if not callable(observer):
            # A narrow compatibility bridge for existing test doubles and old authorities. It
            # produces no invented spatial data; callers keep the static safe projection instead.
            return None
        try:
            value = await observer(record.episode_id)
        except LiveLabyrinthNotFoundError:
            return None
        if value is None:
            return None
        return _safe_mapping(value, label="live_labyrinth.observer")

    @staticmethod
    def _update_video_state(record: _LabRunRecord, status: Mapping[str, object]) -> None:
        # Once a video is archived, later source-renderer state is irrelevant: the durable
        # cartridge is the only browser-serving path and survives the authority process.
        if record.video_state == "ready":
            return
        video = status.get("video")
        if not isinstance(video, Mapping):
            return
        state = video.get("state")
        if state in {"unavailable", "saving"}:
            record.video_state = state
        elif state == "ready":
            # Upstream readiness alone is not public availability.  The archive step must finish
            # first, otherwise a post-restart run would advertise an unrecoverable episode path.
            record.video_state = "saving"

    @staticmethod
    def _parse_mode(value: RunMode | str) -> RunMode:
        try:
            return value if isinstance(value, RunMode) else RunMode(value)
        except (TypeError, ValueError) as error:
            raise LabRunError("Lab run mode is invalid") from error

    @staticmethod
    def _normalize_entrants(entrants: Sequence[RunEntrant]) -> tuple[RunEntrant, ...]:
        if not isinstance(entrants, Sequence) or isinstance(entrants, (str, bytes)):
            raise LabRunError("Labyrinth entrants are invalid")
        normalized = tuple(entrants)
        if len(normalized) != 3 or any(not isinstance(value, RunEntrant) for value in normalized):
            raise LabRunError("Labyrinth entrants are invalid")
        if len({value.entrant_id for value in normalized}) != len(normalized):
            raise LabRunError("Labyrinth entrant identities are invalid")
        return normalized

    @staticmethod
    def _prompt_sha256(skill_mode: str) -> str:
        if skill_mode == "none":
            return labyrinth_protocol_prompt_sha256()
        prompt = compose_labyrinth_system_prompt(skill_mode=skill_mode)
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def _record_directory(self, run_id: str) -> Path:
        return self._root / run_id

    def _persist(self, record: _LabRunRecord) -> None:
        directory = self._record_directory(record.contract.run_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        record_body = {
            "cartridge_sha256": record.cartridge.cartridge_sha256,
            "contract_sha256": record.contract.contract_sha256,
            "created_at_epoch_ms": record.created_at_epoch_ms,
            # Episode handles are process-only authority capabilities. A durable Lab cartridge
            # cannot resume a v1 executor yet, so retaining a handle after restart adds risk with
            # no user-visible benefit. Keep the fixed manifest field for schema stability but
            # persist only null.
            "episode_id": None,
            "projection_sha256": record.projection.projection_sha256,
            "run_id": record.contract.run_id,
            "schema_version": LAB_RUN_RECORD_SCHEMA_VERSION,
            "state_sha256": record.state.state_sha256,
        }
        # The typed contracts below validate their own optional digest fields (for example a
        # root contract's ``parent_contract_sha256: null``).  Applying the generic projection
        # scanner to them would incorrectly reject those schema-authorized nulls.
        self._atomic_write(
            directory / _RUN_RECORD_FILE,
            canonical_json_bytes(_safe_mapping(record_body, label="lab_artifact.record.json")),
        )
        self._atomic_write(directory / _CONTRACT_FILE, record.contract.canonical_bytes)
        self._atomic_write(directory / _STATE_FILE, record.state.canonical_bytes)
        self._atomic_write(directory / _PROJECTION_FILE, record.projection.canonical_bytes)
        self._atomic_write(directory / _CARTRIDGE_FILE, record.cartridge.canonical_bytes)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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

    def _video_artifact_path(self, record: _LabRunRecord) -> Path:
        return self._record_directory(record.contract.run_id) / _VIDEO_FILE

    def _archived_video_path(self, record: _LabRunRecord) -> Path | None:
        """Return a strict archive path only for a completed public run."""

        if record.state.status is not RunLifecycle.COMPLETED:
            return None
        return self._validated_public_video_path(self._video_artifact_path(record))

    def _copy_public_video(self, source_path: Path, destination: Path) -> None:
        """Atomically copy a bounded fast-start MP4 from the live authority.

        Paths are never persisted.  The source descriptor is opened without following symlinks
        where the platform supports it, checked as a regular bounded MP4 while it is copied, then
        renamed into the run directory only after the full payload has been fsynced.
        """

        if (
            not source_path.is_absolute()
            or source_path.suffix.lower() != ".mp4"
            or destination.name != _VIDEO_FILE
            or destination.parent.parent != self._root
        ):
            raise LabRunError("Live Labyrinth video path is invalid")
        directory = destination.parent
        try:
            directory_stat = directory.lstat()
        except OSError as error:
            raise LabRunError("Lab video artifact directory is unavailable") from error
        if directory.is_symlink() or not stat.S_ISDIR(directory_stat.st_mode):
            raise LabRunError("Lab video artifact directory is invalid")

        source_descriptor: int | None = None
        destination_descriptor: int | None = None
        temporary: Path | None = None
        try:
            source_descriptor = os.open(source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            source_stat = os.fstat(source_descriptor)
            self._validate_video_stat(source_stat, private=False)

            temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
            destination_descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            self._copy_checked_mp4(
                source_descriptor,
                expected_size=source_stat.st_size,
                destination_descriptor=destination_descriptor,
            )
            current_source_stat = os.fstat(source_descriptor)
            if (
                current_source_stat.st_dev != source_stat.st_dev
                or current_source_stat.st_ino != source_stat.st_ino
                or current_source_stat.st_size != source_stat.st_size
            ):
                raise LabRunError("Live Labyrinth video changed while archiving")
            os.fsync(destination_descriptor)
            os.close(destination_descriptor)
            destination_descriptor = None
            os.replace(temporary, destination)
            temporary = None
            os.chmod(destination, 0o600)
            self._fsync_directory(directory)
        except OSError as error:
            raise LabRunError("Live Labyrinth video archive is unavailable") from error
        finally:
            if destination_descriptor is not None:
                try:
                    os.close(destination_descriptor)
                except OSError:
                    pass
            if source_descriptor is not None:
                try:
                    os.close(source_descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

        if self._validated_public_video_path(destination) is None:
            raise LabRunError("Live Labyrinth video archive failed verification")

    @staticmethod
    def _validate_video_stat(metadata: os.stat_result, *, private: bool) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not _MIN_ARCHIVED_VIDEO_BYTES <= metadata.st_size <= _MAX_ARCHIVED_VIDEO_BYTES
            or (private and stat.S_IMODE(metadata.st_mode) != 0o600)
        ):
            raise LabRunError("Labyrinth video file is invalid")

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Labyrinth video write failed")
            view = view[written:]

    @classmethod
    def _copy_checked_mp4(
        cls,
        source_descriptor: int,
        *,
        expected_size: int,
        destination_descriptor: int | None,
    ) -> None:
        """Stream one MP4 while checking the public fast-start container markers."""

        copied = 0
        header = bytearray()
        previous = b""
        protected_tail = b""
        moov_offset: int | None = None
        mdat_offset: int | None = None
        while True:
            chunk = os.read(source_descriptor, _VIDEO_COPY_CHUNK_BYTES)
            if not chunk:
                break
            protected_window = protected_tail + chunk.lower()
            if any(marker in protected_window for marker in _VIDEO_PROTECTED_TEXT) or (
                _VIDEO_SECRET_PATTERN.search(protected_window) is not None
            ):
                raise LabRunError("Live Labyrinth video contains protected material")
            protected_tail = protected_window[-_VIDEO_PROTECTED_TAIL_BYTES:]
            if destination_descriptor is not None:
                cls._write_all(destination_descriptor, chunk)
            if len(header) < 32:
                header.extend(chunk[: 32 - len(header)])
            window = previous + chunk
            window_offset = copied - len(previous)
            if moov_offset is None:
                index = window.find(b"moov")
                if index >= 0:
                    moov_offset = window_offset + index
            if mdat_offset is None:
                index = window.find(b"mdat")
                if index >= 0:
                    mdat_offset = window_offset + index
            previous = window[-3:]
            copied += len(chunk)
            if copied > expected_size:
                raise LabRunError("Live Labyrinth video changed while archiving")
        if (
            copied != expected_size
            or b"ftyp" not in bytes(header)
            or moov_offset is None
            or mdat_offset is None
            or moov_offset >= mdat_offset
        ):
            raise LabRunError("Live Labyrinth video is not a public fast-start MP4")

    @classmethod
    def _validated_public_video_path(cls, path: Path) -> Path | None:
        """Fail closed when a durable video is missing, altered, public, or malformed."""

        descriptor: int | None = None
        try:
            metadata = path.lstat()
            if path.is_symlink():
                return None
            cls._validate_video_stat(metadata, private=True)
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
                return None
            cls._validate_video_stat(opened, private=True)
            cls._copy_checked_mp4(
                descriptor,
                expected_size=opened.st_size,
                destination_descriptor=None,
            )
        except (LabRunError, OSError):
            return None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return path

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor: int | None = None
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError as error:
            raise LabRunError("Lab video artifact directory is unavailable") from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _load_durable_records(self) -> None:
        directories = self._root.iterdir() if self._root.exists() else ()
        for directory in sorted(directories, key=lambda path: path.name):
            if directory.is_symlink() or not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                record = self._read_durable_record(directory)
            except (LabContractError, LabRunError, OSError, ValueError):
                # Never turn an unreadable old artifact into a fabricated run.  New records use
                # atomic writes, so a partial directory is safely ignored until inspected.
                continue
            self._records[record.contract.run_id] = record

    def _read_durable_record(self, directory: Path) -> _LabRunRecord:
        manifest = _safe_mapping(
            self._read_json_artifact(directory / _RUN_RECORD_FILE),
            label="lab_artifact.record.json",
        )
        expected_manifest = {
            "cartridge_sha256",
            "contract_sha256",
            "created_at_epoch_ms",
            "episode_id",
            "projection_sha256",
            "run_id",
            "schema_version",
            "state_sha256",
        }
        if (
            set(manifest) != expected_manifest
            or manifest.get("schema_version") != LAB_RUN_RECORD_SCHEMA_VERSION
        ):
            raise LabRunError("Lab run manifest is invalid")
        contract = RunContract.from_dict(self._read_json_artifact(directory / _CONTRACT_FILE))
        state = RunState.from_dict(self._read_json_artifact(directory / _STATE_FILE))
        projection = ReplayProjection.from_dict(
            self._read_json_artifact(directory / _PROJECTION_FILE)
        )
        cartridge = RaceCartridge.from_dict(self._read_json_artifact(directory / _CARTRIDGE_FILE))
        if (
            manifest["run_id"] != contract.run_id
            or manifest["contract_sha256"] != contract.contract_sha256
            or manifest["state_sha256"] != state.state_sha256
            or manifest["projection_sha256"] != projection.projection_sha256
            or manifest["cartridge_sha256"] != cartridge.cartridge_sha256
            or state.run_id != contract.run_id
            or projection.run_id != contract.run_id
            or cartridge.state != state
        ):
            raise LabRunError("Lab run artifact bindings differ")
        created_at = manifest["created_at_epoch_ms"]
        episode_id = manifest["episode_id"]
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, int)
            or created_at < 0
            or (
                episode_id is not None
                and (not isinstance(episode_id, str) or not episode_id.startswith("ep_"))
            )
        ):
            raise LabRunError("Lab run manifest lifecycle fields are invalid")
        # The process-local live executor cannot recover an in-flight episode after a restart.
        # Explicitly discard even a legacy non-null handle rather than turning a stale on-disk
        # artifact into a capability. Completed public cartridges remain replayable from their
        # saved projection.
        record = _LabRunRecord(
            contract=contract,
            state=state,
            projection=projection,
            cartridge=cartridge,
            episode_id=None,
            created_at_epoch_ms=created_at,
            upstream_available=False,
        )
        # The artifact itself is the only durable video capability.  Validate it during reload so
        # a stale, partial, symlinked, or malformed file cannot make a post-restart run claim a
        # replay video that the local process cannot safely serve.
        if self._archived_video_path(record) is not None:
            record.video_state = "ready"
        return record

    @staticmethod
    def _read_json_artifact(path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise LabRunError("Lab artifact cannot be a symbolic link")
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024:
            raise LabRunError("Lab artifact is too large")
        value = strict_json_loads(data)
        if not isinstance(value, dict):
            raise LabRunError("Lab artifact is invalid")
        return value

    @staticmethod
    def _public_record(record: _LabRunRecord) -> Mapping[str, Any]:
        summary = {
            "authority_available": record.upstream_available,
            "created_at_epoch_ms": record.created_at_epoch_ms,
            "replay_available": record.state.status is RunLifecycle.COMPLETED,
            "resume_supported": False,
            "run_id": record.contract.run_id,
            "video_available": record.video_state == "ready",
        }
        # The three typed values below have their own strict schema validation, including
        # schema-authorized nullable digest references.  The generic scanner is applied only to
        # the non-typed summary to avoid treating those deliberate nulls as malformed hashes.
        assert_public_projection_safe(summary, path="lab_run.public_record.summary")
        payload = {
            **summary,
            "cartridge": record.cartridge.as_dict(),
            "contract": record.contract.as_dict(),
            "state": record.state.as_dict(),
        }
        return payload


def labyrinth_game_catalogue() -> Mapping[str, object]:
    """Return the immutable game catalogue after asserting Labyrinth is genuinely launchable."""

    try:
        game = GAME_CATALOG.game("labyrinth-run")
    except GameCatalogError as error:  # Defensive: this is a product configuration failure.
        raise LabRunError("Labyrinth is absent from the Lab game catalogue") from error
    if game.readiness != "live_ready":
        raise LabRunError("Labyrinth is not live-ready")
    return GAME_CATALOG.public_dict()


__all__ = [
    "LAB_RUN_RECORD_SCHEMA_VERSION",
    "LIVE_SPECTATOR_FEED_SCHEMA_VERSION",
    "DraftLabyrinthLaunch",
    "LabRunError",
    "LabRunNotFoundError",
    "LabRunService",
    "LiveLabyrinthProjectionSource",
    "labyrinth_game_catalogue",
]
