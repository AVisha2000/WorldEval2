"""Durable, explicitly published, sanitized replay projections.

Lab run records are private operator artifacts even when their replay projection is safe for a
browser.  This module provides the separate publication boundary needed by unlisted public game
pages:

* callers must present a complete, successful Lab run record;
* all canonical contract/state/cartridge bindings are re-validated;
* only the replay snapshot/events and evidence fingerprints cross the boundary;
* a fresh, unguessable slug is generated independently of run and authority handles; and
* the active publication index and publication files are committed atomically.

The store deliberately has no FastAPI or run-service dependency.  A product route can pass the
public mapping returned by either Lab run service without giving this module access to private
authority state.
"""

from __future__ import annotations

import fcntl
import os
import re
import secrets
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..protocol import (
    ProtocolValidationError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from .contracts import (
    LabContractError,
    RaceCartridge,
    ReplayProjection,
    RunContract,
    RunLifecycle,
    RunState,
    assert_public_projection_safe,
)

PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION = "worldeval/public-replay-publication/1"
PUBLIC_REPLAY_INDEX_SCHEMA_VERSION = "worldeval/public-replay-index/1"
PUBLIC_GAME_REPLAYS_SCHEMA_VERSION = "worldeval/public-game-replays/1"
_PUBLIC_REPLAY_TRANSACTION_SCHEMA_VERSION = "worldeval/public-replay-transaction/1"

_DIRECTORY_NAME = "lab-publications"
_PUBLICATIONS_DIRECTORY_NAME = "publications"
_UNPUBLISHED_DIRECTORY_NAME = ".unpublished"
_INDEX_FILE_NAME = "index.json"
_LOCK_FILE_NAME = ".store.lock"
_TRANSACTION_FILE_NAME = ".transaction.json"
_PUBLICATION_SUFFIX = ".json"
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_TOMBSTONE_COUNT = 64
_DEFAULT_MAX_TOMBSTONE_BYTES = 64 * 1024 * 1024
_PUBLICATION_SLUG = re.compile(r"^pub_[A-Za-z0-9_-]{32}$")
_TOMBSTONE_FILE = re.compile(
    r"^(pub_[A-Za-z0-9_-]{32})\.([0-9]+)\.([0-9a-f]{12})\.json$"
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHOD_LABEL = re.compile(r"^[a-z][a-z0-9._-]{0,79}$")
_ALLOWED_RUN_RECORD_FIELDS = frozenset(
    {
        "authority_available",
        "authority_kind",
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
_REQUIRED_RUN_RECORD_FIELDS = frozenset(
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
_SUCCESSFUL_PUBLICATION_STATES = frozenset(
    {
        RunLifecycle.COMPLETED,
        RunLifecycle.SEALED,
        RunLifecycle.VERIFIED,
    }
)
_PUBLICATION_FIELDS = frozenset(
    {
        "cartridge_sha256",
        "contract_sha256",
        "game_id",
        "game_version",
        "lifecycle_status",
        "method_label",
        "projection_sha256",
        "publication_slug",
        "published_at_epoch_ms",
        "replay",
        "result_sha256",
        "schema_version",
        "state_sha256",
    }
)
_REPLAY_FIELDS = frozenset({"events", "sequence", "snapshot"})
_INDEX_FIELDS = frozenset({"index_sha256", "publications", "schema_version"})
_INDEX_ENTRY_FIELDS = frozenset(
    {
        "cartridge_sha256",
        "game_id",
        "publication_sha256",
        "publication_slug",
        "published_at_epoch_ms",
    }
)
_TRANSACTION_FIELDS = frozenset(
    {
        "after_index_sha256",
        "before_index_sha256",
        "operation",
        "publication_sha256",
        "publication_slug",
        "schema_version",
        "tombstone_name",
        "transaction_sha256",
    }
)
_TRANSACTION_OPERATIONS = frozenset({"publish", "unpublish"})

# ``assert_public_projection_safe`` is the common Lab boundary.  Publication has a few additional
# exclusions because it is internet-facing rather than merely browser-facing.
_PUBLICATION_ONLY_PROTECTED_KEYS = frozenset(
    {
        "access_token",
        "active_controls",
        "api_token",
        "authority_available",
        "cancel_url",
        "client_secret",
        "control_url",
        "controls",
        "episode_id",
        "password",
        "private_key",
        "refresh_token",
        "replay_id",
        "resume_url",
        "run_id",
        "session_key",
        "series_id",
        "source_id",
        "token",
    }
)
_ADDITIONAL_SECRET_PATTERNS = (
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)


class PublicReplayPublicationError(ValueError):
    """A requested public replay publication is malformed or unsafe."""


class PublicReplayStoreError(RuntimeError):
    """The durable public replay store is unavailable or corrupt."""


class PublicReplayNotFoundError(KeyError):
    """No active publication has the requested unlisted slug."""


def _normalise_key(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").casefold()


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise PublicReplayPublicationError(f"{name} is invalid")
    return value


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PublicReplayPublicationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_nonnegative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicReplayPublicationError(f"{name} must be a non-negative integer")
    return value


def _require_slug(value: object) -> str:
    if not isinstance(value, str) or _PUBLICATION_SLUG.fullmatch(value) is None:
        raise PublicReplayPublicationError("publication_slug is invalid")
    return value


def _require_method_label(value: object) -> str:
    if not isinstance(value, str) or _METHOD_LABEL.fullmatch(value) is None:
        raise PublicReplayPublicationError("method_label is invalid")
    return value


def _exact_mapping(value: object, fields: frozenset[str], *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PublicReplayPublicationError(f"{label} fields differ")
    if any(not isinstance(key, str) for key in value):
        raise PublicReplayPublicationError(f"{label} contains a non-string key")
    return value


def _canonical_copy(value: object, *, label: str) -> Any:
    try:
        assert_public_projection_safe(value, path=label)
        return strict_json_loads(canonical_json_bytes(value))
    except (LabContractError, ProtocolValidationError, TypeError, ValueError) as error:
        raise PublicReplayPublicationError(f"{label} is not safe canonical JSON") from error


def _assert_publication_material_safe(value: object, *, path: str) -> None:
    """Apply the stricter internet-public replay boundary recursively."""

    try:
        assert_public_projection_safe(value, path=path)
    except LabContractError as error:
        raise PublicReplayPublicationError(str(error)) from error
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PublicReplayPublicationError(f"non-string key at {path}")
            if _normalise_key(key) in _PUBLICATION_ONLY_PROTECTED_KEYS:
                raise PublicReplayPublicationError(f"protected publication field at {path}.{key}")
            _assert_publication_material_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_publication_material_safe(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        encoded = value.encode("utf-8", errors="strict")
        if any(pattern.search(encoded) for pattern in _ADDITIONAL_SECRET_PATTERNS):
            raise PublicReplayPublicationError(f"credential-like material is forbidden at {path}")


@dataclass(frozen=True, init=False)
class PublicReplayPublication:
    """Canonical, hash-bound content served by one unlisted public replay URL."""

    _canonical_body: bytes
    publication_sha256: str

    @classmethod
    def create(
        cls,
        *,
        publication_slug: str,
        game_id: str,
        game_version: str,
        contract_sha256: str,
        lifecycle_status: RunLifecycle | str,
        state_sha256: str,
        result_sha256: str,
        projection_sha256: str,
        cartridge_sha256: str,
        replay_sequence: int,
        replay_snapshot: Mapping[str, object],
        replay_events: Sequence[Mapping[str, object]],
        published_at_epoch_ms: int,
        method_label: str,
    ) -> PublicReplayPublication:
        if not isinstance(replay_snapshot, Mapping):
            raise PublicReplayPublicationError("replay_snapshot must be an object")
        if not isinstance(replay_events, Sequence) or isinstance(replay_events, (str, bytes)):
            raise PublicReplayPublicationError("replay_events must be an array")
        if any(not isinstance(event, Mapping) for event in replay_events):
            raise PublicReplayPublicationError("replay_events must contain objects")
        body: Mapping[str, object] = {
            "cartridge_sha256": cartridge_sha256,
            "contract_sha256": contract_sha256,
            "game_id": game_id,
            "game_version": game_version,
            "lifecycle_status": (
                lifecycle_status.value
                if isinstance(lifecycle_status, RunLifecycle)
                else lifecycle_status
            ),
            "method_label": method_label,
            "projection_sha256": projection_sha256,
            "publication_slug": publication_slug,
            "published_at_epoch_ms": published_at_epoch_ms,
            "replay": {
                "events": [dict(event) for event in replay_events],
                "sequence": replay_sequence,
                "snapshot": dict(replay_snapshot),
            },
            "result_sha256": result_sha256,
            "schema_version": PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION,
            "state_sha256": state_sha256,
        }
        return cls._from_body(body)

    @classmethod
    def _from_body(cls, value: Mapping[str, object]) -> PublicReplayPublication:
        body = _validate_publication_body(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_body", canonical_json_bytes(body))
        object.__setattr__(instance, "publication_sha256", canonical_sha256(body))
        return instance

    @classmethod
    def from_dict(cls, value: object) -> PublicReplayPublication:
        if not isinstance(value, Mapping):
            raise PublicReplayPublicationError("public replay publication must be an object")
        parsed = _exact_mapping(
            value,
            _PUBLICATION_FIELDS | frozenset({"publication_sha256"}),
            label="public replay publication",
        )
        instance = cls._from_body({field: parsed[field] for field in _PUBLICATION_FIELDS})
        if parsed["publication_sha256"] != instance.publication_sha256:
            raise PublicReplayPublicationError("publication fingerprint differs")
        return instance

    @property
    def canonical_body(self) -> bytes:
        return bytes(self._canonical_body)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    @property
    def publication_slug(self) -> str:
        return str(self._body()["publication_slug"])

    @property
    def cartridge_sha256(self) -> str:
        return str(self._body()["cartridge_sha256"])

    @property
    def game_id(self) -> str:
        return str(self._body()["game_id"])

    @property
    def published_at_epoch_ms(self) -> int:
        return int(self._body()["published_at_epoch_ms"])

    def as_dict(self) -> Mapping[str, Any]:
        return {**self._body(), "publication_sha256": self.publication_sha256}

    def _body(self) -> Mapping[str, Any]:
        value = strict_json_loads(self._canonical_body)
        if not isinstance(value, Mapping):  # Defensive: canonical body is always an object.
            raise PublicReplayPublicationError("publication body is invalid")
        return value


def _validate_publication_body(value: Mapping[str, object]) -> dict[str, object]:
    parsed = _exact_mapping(value, _PUBLICATION_FIELDS, label="public replay publication")
    if parsed["schema_version"] != PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION:
        raise PublicReplayPublicationError("publication schema_version is unsupported")
    try:
        lifecycle = RunLifecycle(parsed["lifecycle_status"])
    except (TypeError, ValueError) as error:
        raise PublicReplayPublicationError("lifecycle_status is invalid") from error
    if lifecycle not in _SUCCESSFUL_PUBLICATION_STATES:
        raise PublicReplayPublicationError("publication lifecycle is not successful")

    replay = _exact_mapping(parsed["replay"], _REPLAY_FIELDS, label="publication replay")
    snapshot = replay["snapshot"]
    events = replay["events"]
    if not isinstance(snapshot, Mapping):
        raise PublicReplayPublicationError("publication replay snapshot is invalid")
    if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
        raise PublicReplayPublicationError("publication replay events are invalid")
    safe_snapshot = _canonical_copy(snapshot, label="publication.replay.snapshot")
    safe_events = _canonical_copy(events, label="publication.replay.events")
    _assert_publication_material_safe(safe_snapshot, path="publication.replay.snapshot")
    _assert_publication_material_safe(safe_events, path="publication.replay.events")

    body = {
        "cartridge_sha256": _require_sha256("cartridge_sha256", parsed["cartridge_sha256"]),
        "contract_sha256": _require_sha256("contract_sha256", parsed["contract_sha256"]),
        "game_id": _require_identifier("game_id", parsed["game_id"]),
        "game_version": _require_identifier("game_version", parsed["game_version"]),
        "lifecycle_status": lifecycle.value,
        "method_label": _require_method_label(parsed["method_label"]),
        "projection_sha256": _require_sha256("projection_sha256", parsed["projection_sha256"]),
        "publication_slug": _require_slug(parsed["publication_slug"]),
        "published_at_epoch_ms": _require_nonnegative_int(
            "published_at_epoch_ms", parsed["published_at_epoch_ms"]
        ),
        "replay": {
            "events": safe_events,
            "sequence": _require_nonnegative_int("replay.sequence", replay["sequence"]),
            "snapshot": safe_snapshot,
        },
        "result_sha256": _require_sha256("result_sha256", parsed["result_sha256"]),
        "schema_version": PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION,
        "state_sha256": _require_sha256("state_sha256", parsed["state_sha256"]),
    }
    # This scan covers future field additions too.  Digest fields are valid by construction.
    try:
        assert_public_projection_safe(body, path="public_replay_publication")
    except LabContractError as error:
        raise PublicReplayPublicationError(str(error)) from error
    return body


@dataclass(frozen=True)
class _ValidatedRun:
    contract: RunContract
    state: RunState
    projection: ReplayProjection
    cartridge: RaceCartridge


def _validated_public_run_record(value: object) -> _ValidatedRun:
    if not isinstance(value, Mapping):
        raise PublicReplayPublicationError("Lab run record must be an object")
    fields = set(value)
    if (
        not _REQUIRED_RUN_RECORD_FIELDS.issubset(fields)
        or not fields.issubset(_ALLOWED_RUN_RECORD_FIELDS)
        or any(not isinstance(key, str) for key in value)
    ):
        raise PublicReplayPublicationError("Lab run record fields differ")
    for flag in (
        "authority_available",
        "replay_available",
        "resume_supported",
        "video_available",
    ):
        if not isinstance(value[flag], bool):
            raise PublicReplayPublicationError(f"Lab run record {flag} is invalid")
    if value["replay_available"] is not True:
        raise PublicReplayPublicationError("Lab run replay is not available")
    _require_nonnegative_int("Lab run created_at_epoch_ms", value["created_at_epoch_ms"])
    _require_identifier("Lab run run_id", value["run_id"])
    if "authority_kind" in value:
        _require_identifier("Lab run authority_kind", value["authority_kind"])

    try:
        contract = RunContract.from_dict(value["contract"])
        state = RunState.from_dict(value["state"])
        cartridge = RaceCartridge.from_dict(value["cartridge"])
        projection = cartridge.public_projection
    except (LabContractError, TypeError, ValueError) as error:
        raise PublicReplayPublicationError("Lab run canonical artifacts are invalid") from error

    if (
        value["run_id"] != contract.run_id
        or state.run_id != contract.run_id
        or projection.run_id != contract.run_id
        or state.contract_sha256 != contract.contract_sha256
        or projection.contract_sha256 != contract.contract_sha256
        or cartridge.as_dict()["contract_sha256"] != contract.contract_sha256
        or cartridge.state != state
        or cartridge.public_projection != projection
    ):
        raise PublicReplayPublicationError("Lab run canonical artifact bindings differ")
    if state.status not in _SUCCESSFUL_PUBLICATION_STATES:
        raise PublicReplayPublicationError("Lab run is not completed, sealed, or verified")
    if projection.status is not state.status:
        raise PublicReplayPublicationError("Lab run public lifecycle differs")
    state_body = state.as_dict()
    if not isinstance(state_body["result_sha256"], str):
        raise PublicReplayPublicationError("Lab run has no result evidence")

    # The typed projection already uses the common scanner; run it once more at this explicit
    # internet-public boundary and apply the stricter publication exclusions.
    projection_body = projection.as_dict()
    _assert_publication_material_safe(projection_body["snapshot"], path="Lab run replay snapshot")
    _assert_publication_material_safe(projection_body["events"], path="Lab run replay events")
    return _ValidatedRun(contract, state, projection, cartridge)


class PublicReplayStore:
    """Atomic active index for explicitly published, unlisted replay projections."""

    def __init__(
        self,
        *,
        runs_dir: Path,
        clock_ms: Callable[[], int] | None = None,
        token_factory: Callable[[], str] | None = None,
        max_tombstone_count: int = _DEFAULT_MAX_TOMBSTONE_COUNT,
        max_tombstone_bytes: int = _DEFAULT_MAX_TOMBSTONE_BYTES,
    ) -> None:
        if (
            isinstance(max_tombstone_count, bool)
            or not isinstance(max_tombstone_count, int)
            or max_tombstone_count < 0
        ):
            raise PublicReplayStoreError("max_tombstone_count must be a non-negative integer")
        if (
            isinstance(max_tombstone_bytes, bool)
            or not isinstance(max_tombstone_bytes, int)
            or max_tombstone_bytes < 0
        ):
            raise PublicReplayStoreError("max_tombstone_bytes must be a non-negative integer")
        self._root = Path(runs_dir) / _DIRECTORY_NAME
        self._publications_root = self._root / _PUBLICATIONS_DIRECTORY_NAME
        self._unpublished_root = self._root / _UNPUBLISHED_DIRECTORY_NAME
        self._index_path = self._root / _INDEX_FILE_NAME
        self._lock_path = self._root / _LOCK_FILE_NAME
        self._transaction_path = self._root / _TRANSACTION_FILE_NAME
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._max_tombstone_count = max_tombstone_count
        self._max_tombstone_bytes = max_tombstone_bytes
        self._lock = threading.RLock()
        self._by_slug: dict[str, PublicReplayPublication] = {}
        self._by_cartridge: dict[str, PublicReplayPublication] = {}
        self._prepare_directories()
        with self._filesystem_lock():
            self._reload_from_disk()

    def publish(
        self,
        run_record: Mapping[str, object],
        *,
        method_label: str = "explicit_unlisted_link",
    ) -> Mapping[str, Any]:
        """Explicitly publish one completed public run, idempotently by cartridge hash."""

        validated = _validated_public_run_record(run_record)
        method = _require_method_label(method_label)
        cartridge_sha256 = validated.cartridge.cartridge_sha256
        with self._lock, self._filesystem_lock():
            self._reload_from_disk()
            existing = self._by_cartridge.get(cartridge_sha256)
            if existing is not None:
                return existing.as_dict()
            slug = self._new_slug()
            published_at = _require_nonnegative_int("publication clock", self._clock_ms())
            projection_body = validated.projection.as_dict()
            state_body = validated.state.as_dict()
            contract_body = validated.contract.as_dict()
            publication = PublicReplayPublication.create(
                publication_slug=slug,
                game_id=str(contract_body["game_id"]),
                game_version=str(contract_body["game_version"]),
                contract_sha256=validated.contract.contract_sha256,
                lifecycle_status=validated.state.status,
                state_sha256=validated.state.state_sha256,
                result_sha256=str(state_body["result_sha256"]),
                projection_sha256=validated.projection.projection_sha256,
                cartridge_sha256=cartridge_sha256,
                replay_sequence=validated.projection.sequence,
                replay_snapshot=projection_body["snapshot"],
                replay_events=projection_body["events"],
                published_at_epoch_ms=published_at,
                method_label=method,
            )
            publication_path = self._publication_path(slug)
            next_by_slug = {**self._by_slug, slug: publication}
            transaction = self._create_transaction(
                operation="publish",
                publication=publication,
                before_publications=self._by_slug.values(),
                after_publications=next_by_slug.values(),
                tombstone_name=None,
            )
            self._write_transaction(transaction)
            try:
                self._atomic_write(publication_path, publication.canonical_bytes, replace=False)
                self._write_index(next_by_slug.values())
                self._clear_transaction()
            except Exception:
                # Resolve any ambiguous ``os.replace`` outcome from disk.  If storage remains
                # unavailable, the hash-bound journal is deliberately retained for restart.
                try:
                    self._reload_from_disk()
                except Exception as recovery_error:
                    raise PublicReplayStoreError(
                        "publication transaction requires restart recovery"
                    ) from recovery_error
                raise
            self._by_slug = next_by_slug
            self._by_cartridge[cartridge_sha256] = publication
            return publication.as_dict()

    def get(self, publication_slug: str) -> Mapping[str, Any]:
        slug = _require_slug(publication_slug)
        with self._lock, self._filesystem_lock():
            self._reload_from_disk()
            try:
                return self._by_slug[slug].as_dict()
            except KeyError as error:
                raise PublicReplayNotFoundError(slug) from error

    def list(self, *, game_id: str | None = None) -> list[Mapping[str, Any]]:
        if game_id is not None:
            _require_identifier("game_id", game_id)
        with self._lock, self._filesystem_lock():
            self._reload_from_disk()
            values = [
                publication
                for publication in self._by_slug.values()
                if game_id is None or publication.game_id == game_id
            ]
            values.sort(
                key=lambda item: (item.published_at_epoch_ms, item.publication_slug),
                reverse=True,
            )
            return [value.as_dict() for value in values]

    def public_game_projection(self, game_id: str) -> Mapping[str, Any]:
        """Return the safe replay list consumed by one public game field-guide page."""

        identifier = _require_identifier("game_id", game_id)
        payload: Mapping[str, object] = {
            "game_id": identifier,
            "publications": self.list(game_id=identifier),
            "schema_version": PUBLIC_GAME_REPLAYS_SCHEMA_VERSION,
        }
        try:
            assert_public_projection_safe(payload, path="public_game_replays")
        except LabContractError as error:
            raise PublicReplayStoreError("public game replay projection is unsafe") from error
        return _canonical_copy(payload, label="public_game_replays")

    def unpublish(self, publication_slug: str) -> bool:
        """Remove an active publication while retaining a recoverable publication tombstone."""

        slug = _require_slug(publication_slug)
        with self._lock, self._filesystem_lock():
            self._reload_from_disk()
            publication = self._by_slug.get(slug)
            if publication is None:
                return False
            source = self._publication_path(slug)
            unpublished_at = _require_nonnegative_int("publication clock", self._clock_ms())
            tombstone = self._unpublished_root / (
                f"{slug}.{unpublished_at}.{secrets.token_hex(6)}{_PUBLICATION_SUFFIX}"
            )
            if source.is_symlink() or not source.is_file():
                raise PublicReplayStoreError("active publication artifact is unavailable")
            next_by_slug = dict(self._by_slug)
            next_by_slug.pop(slug)
            transaction = self._create_transaction(
                operation="unpublish",
                publication=publication,
                before_publications=self._by_slug.values(),
                after_publications=next_by_slug.values(),
                tombstone_name=tombstone.name,
            )
            self._write_transaction(transaction)
            try:
                os.replace(source, tombstone)
                self._fsync_directory(self._publications_root)
                self._fsync_directory(self._unpublished_root)
                self._write_index(next_by_slug.values())
                self._clear_transaction()
            except Exception:
                # Resolve either the old or new durable index from the journal.  Recovery completes
                # an already-started unpublish rather than briefly re-exposing the public artifact.
                try:
                    self._reload_from_disk()
                except Exception as recovery_error:
                    raise PublicReplayStoreError(
                        "unpublish transaction requires restart recovery"
                    ) from recovery_error
                raise
            self._by_slug = next_by_slug
            self._by_cartridge.pop(publication.cartridge_sha256, None)
            self._prune_tombstones()
            return True

    def _reload_from_disk(self) -> None:
        """Recover and reload while the caller holds the filesystem lock."""

        self._recover_transaction()
        self._load()
        self._prune_tombstones()

    def _create_transaction(
        self,
        *,
        operation: str,
        publication: PublicReplayPublication,
        before_publications: Iterable[PublicReplayPublication],
        after_publications: Iterable[PublicReplayPublication],
        tombstone_name: str | None,
    ) -> Mapping[str, object]:
        before_index = self._index_payload(before_publications)
        after_index = self._index_payload(after_publications)
        body: Mapping[str, object] = {
            "after_index_sha256": after_index["index_sha256"],
            "before_index_sha256": before_index["index_sha256"],
            "operation": operation,
            "publication_sha256": publication.publication_sha256,
            "publication_slug": publication.publication_slug,
            "schema_version": _PUBLIC_REPLAY_TRANSACTION_SCHEMA_VERSION,
            "tombstone_name": tombstone_name,
        }
        transaction = {**body, "transaction_sha256": canonical_sha256(body)}
        # Validate our own durable recovery instruction before it can affect the filesystem.
        _validate_transaction(transaction)
        return transaction

    def _write_transaction(self, transaction: Mapping[str, object]) -> None:
        if self._transaction_path.exists() or self._transaction_path.is_symlink():
            raise PublicReplayStoreError("another public replay transaction is pending")
        self._atomic_write(
            self._transaction_path,
            canonical_json_bytes(transaction),
            replace=False,
        )

    def _clear_transaction(self) -> None:
        if self._transaction_path.is_symlink():
            raise PublicReplayStoreError("public replay transaction cannot be a symbolic link")
        try:
            self._transaction_path.unlink()
            self._fsync_directory(self._root)
        except OSError as error:
            raise PublicReplayStoreError(
                "public replay transaction could not be cleared"
            ) from error

    def _recover_transaction(self) -> None:
        """Resolve one hash-bound publish/unpublish crash window.

        A mismatched directory is repaired only when a canonical journal proves the exact
        publication hash, prior index hash, next index hash, and (for unpublish) tombstone name.
        Unjournaled or ambiguous artifacts remain a hard startup failure in :meth:`_load`.
        """

        if self._transaction_path.is_symlink():
            raise PublicReplayStoreError("public replay transaction cannot be a symbolic link")
        if not self._transaction_path.exists():
            return
        if not self._index_path.exists() or self._index_path.is_symlink():
            raise PublicReplayStoreError("public replay transaction index is unavailable")

        try:
            transaction = _validate_transaction(self._read_json(self._transaction_path))
        except (PublicReplayPublicationError, PublicReplayStoreError) as error:
            raise PublicReplayStoreError("public replay transaction is invalid") from error
        try:
            index = self._read_json(self._index_path)
            entries = _validate_index(index)
        except (PublicReplayPublicationError, PublicReplayStoreError) as error:
            raise PublicReplayStoreError("public replay transaction index is invalid") from error

        current_index_sha256 = str(index["index_sha256"])
        before_index_sha256 = str(transaction["before_index_sha256"])
        after_index_sha256 = str(transaction["after_index_sha256"])
        if current_index_sha256 not in {before_index_sha256, after_index_sha256}:
            raise PublicReplayStoreError("public replay transaction index binding differs")

        operation = str(transaction["operation"])
        slug = str(transaction["publication_slug"])
        publication_sha256 = str(transaction["publication_sha256"])
        if operation == "publish":
            self._discard_publish_staging_files(slug)
        active_files = self._active_publication_files()
        active_path = self._publication_path(slug)
        active_exists = active_path.name in active_files

        if operation == "publish":
            self._recover_publish_transaction(
                entries=entries,
                active_files=active_files,
                active_exists=active_exists,
                slug=slug,
                publication_sha256=publication_sha256,
                current_index_sha256=current_index_sha256,
                before_index_sha256=before_index_sha256,
                after_index_sha256=after_index_sha256,
            )
        else:
            tombstone_name = transaction["tombstone_name"]
            assert isinstance(tombstone_name, str)
            self._recover_unpublish_transaction(
                entries=entries,
                active_files=active_files,
                active_exists=active_exists,
                slug=slug,
                publication_sha256=publication_sha256,
                tombstone_name=tombstone_name,
                current_index_sha256=current_index_sha256,
                before_index_sha256=before_index_sha256,
                after_index_sha256=after_index_sha256,
            )
        self._clear_transaction()

    def _discard_publish_staging_files(self, slug: str) -> None:
        """Discard only an uncommitted atomic-write temporary proven by this journal."""

        temporary_pattern = re.compile(
            rf"\.{re.escape(slug)}{re.escape(_PUBLICATION_SUFFIX)}\."
            rf"[0-9a-f]{{16}}\.tmp"
        )
        discarded = False
        for path in self._publications_root.iterdir():
            if temporary_pattern.fullmatch(path.name) is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise PublicReplayStoreError("public replay transaction staging is invalid")
            try:
                path.unlink()
            except OSError as error:
                raise PublicReplayStoreError(
                    "public replay transaction staging is unavailable"
                ) from error
            discarded = True
        if discarded:
            self._fsync_directory(self._publications_root)

    def _recover_publish_transaction(
        self,
        *,
        entries: Sequence[Mapping[str, object]],
        active_files: Mapping[str, Path],
        active_exists: bool,
        slug: str,
        publication_sha256: str,
        current_index_sha256: str,
        before_index_sha256: str,
        after_index_sha256: str,
    ) -> None:
        if current_index_sha256 == before_index_sha256:
            if not active_exists:
                # The journal committed but publication staging never became active.
                self._validate_active_index_bindings(entries, active_files)
                return
            indexed_files = {
                name: active_files[name]
                for name in self._indexed_names(entries)
                if name in active_files
            }
            self._validate_active_index_bindings(entries, indexed_files)
            publication = self._read_bound_publication(
                self._publication_path(slug),
                slug=slug,
                publication_sha256=publication_sha256,
            )
            if any(
                entry["publication_slug"] == slug
                or entry["cartridge_sha256"] == publication.cartridge_sha256
                for entry in entries
            ):
                raise PublicReplayStoreError("public replay publish transaction conflicts")
            next_entries = [*entries, self._index_entry(publication)]
            if self._index_payload_from_entries(next_entries)["index_sha256"] != after_index_sha256:
                raise PublicReplayStoreError("public replay publish transaction binding differs")
            expected_names = {
                *self._indexed_names(entries),
                f"{slug}{_PUBLICATION_SUFFIX}",
            }
            if set(active_files) != expected_names:
                raise PublicReplayStoreError("public replay publish artifacts differ")
            self._write_index_entries(next_entries)
            return

        if not active_exists:
            raise PublicReplayStoreError("committed public replay publication is unavailable")
        self._validate_active_index_bindings(entries, active_files)
        matching = [entry for entry in entries if entry["publication_slug"] == slug]
        if len(matching) != 1:
            raise PublicReplayStoreError("committed public replay publication binding is missing")
        publication = self._read_bound_publication(
            self._publication_path(slug),
            slug=slug,
            publication_sha256=publication_sha256,
        )
        if self._index_entry(publication) != matching[0]:
            raise PublicReplayStoreError("committed public replay publication binding differs")
        prior_entries = [entry for entry in entries if entry["publication_slug"] != slug]
        if self._index_payload_from_entries(prior_entries)["index_sha256"] != before_index_sha256:
            raise PublicReplayStoreError("public replay publish prior index binding differs")

    def _recover_unpublish_transaction(
        self,
        *,
        entries: Sequence[Mapping[str, object]],
        active_files: Mapping[str, Path],
        active_exists: bool,
        slug: str,
        publication_sha256: str,
        tombstone_name: str,
        current_index_sha256: str,
        before_index_sha256: str,
        after_index_sha256: str,
    ) -> None:
        tombstone = self._unpublished_root / tombstone_name
        if tombstone.is_symlink():
            raise PublicReplayStoreError("public replay tombstone cannot be a symbolic link")
        tombstone_exists = tombstone.is_file()
        if active_exists == tombstone_exists:
            raise PublicReplayStoreError("public replay unpublish artifacts are ambiguous")

        if current_index_sha256 == before_index_sha256:
            if active_exists:
                # The journal committed but the publication was never moved.
                self._validate_active_index_bindings(entries, active_files)
                publication = self._read_bound_publication(
                    self._publication_path(slug),
                    slug=slug,
                    publication_sha256=publication_sha256,
                )
                matching = [entry for entry in entries if entry["publication_slug"] == slug]
                if len(matching) != 1 or self._index_entry(publication) != matching[0]:
                    raise PublicReplayStoreError("public replay unpublish binding differs")
                next_entries = [entry for entry in entries if entry["publication_slug"] != slug]
                if (
                    self._index_payload_from_entries(next_entries)["index_sha256"]
                    != after_index_sha256
                ):
                    raise PublicReplayStoreError("public replay unpublish next index differs")
                return

            publication = self._read_bound_publication(
                tombstone,
                slug=slug,
                publication_sha256=publication_sha256,
            )
            matching = [entry for entry in entries if entry["publication_slug"] == slug]
            if len(matching) != 1 or self._index_entry(publication) != matching[0]:
                raise PublicReplayStoreError("public replay unpublish tombstone binding differs")
            next_entries = [entry for entry in entries if entry["publication_slug"] != slug]
            if self._index_payload_from_entries(next_entries)["index_sha256"] != after_index_sha256:
                raise PublicReplayStoreError("public replay unpublish next index differs")
            self._validate_active_index_bindings(next_entries, active_files)
            self._write_index_entries(next_entries)
            return

        if active_exists or not tombstone_exists:
            raise PublicReplayStoreError("committed public replay unpublish artifacts differ")
        self._validate_active_index_bindings(entries, active_files)
        publication = self._read_bound_publication(
            tombstone,
            slug=slug,
            publication_sha256=publication_sha256,
        )
        if any(
            entry["publication_slug"] == slug
            or entry["cartridge_sha256"] == publication.cartridge_sha256
            for entry in entries
        ):
            raise PublicReplayStoreError("committed public replay unpublish conflicts")
        prior_entries = [*entries, self._index_entry(publication)]
        if self._index_payload_from_entries(prior_entries)["index_sha256"] != before_index_sha256:
            raise PublicReplayStoreError("public replay unpublish prior index binding differs")

    @contextmanager
    def _filesystem_lock(self) -> Iterator[None]:
        """Serialize recovery and mutations across store instances and worker processes."""

        if self._lock_path.is_symlink():
            raise PublicReplayStoreError("public replay store lock cannot be a symbolic link")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self._lock_path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                os.close(descriptor)
                descriptor = None
                raise PublicReplayStoreError("public replay store lock is invalid")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise PublicReplayStoreError("public replay store lock is unavailable") from error
        try:
            yield
        finally:
            assert descriptor is not None
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _prepare_directories(self) -> None:
        for directory in (self._root, self._publications_root, self._unpublished_root):
            if directory.is_symlink():
                raise PublicReplayStoreError("public replay store cannot use a symbolic link")
            try:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                if directory.is_symlink() or not directory.is_dir():
                    raise PublicReplayStoreError("public replay store directory is invalid")
                os.chmod(directory, 0o700)
            except OSError as error:
                raise PublicReplayStoreError(
                    "public replay store directory is unavailable"
                ) from error

    def _load(self) -> None:
        with self._lock:
            if self._index_path.is_symlink():
                raise PublicReplayStoreError("public replay index cannot be a symbolic link")
            active_files = self._active_publication_files()
            if not self._index_path.exists():
                if active_files:
                    raise PublicReplayStoreError("public replay index is missing")
                self._write_index(())
                self._by_slug = {}
                self._by_cartridge = {}
                return
            index = self._read_json(self._index_path)
            try:
                entries = _validate_index(index)
            except PublicReplayPublicationError as error:
                raise PublicReplayStoreError("public replay index is invalid") from error
            by_slug, by_cartridge = self._validate_active_index_bindings(entries, active_files)
            self._by_slug = by_slug
            self._by_cartridge = by_cartridge

    def _active_publication_files(self) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for path in self._publications_root.iterdir():
            if path.is_symlink() or not path.is_file():
                raise PublicReplayStoreError("public replay artifact entry is invalid")
            if not path.name.endswith(_PUBLICATION_SUFFIX):
                raise PublicReplayStoreError("public replay artifact filename is invalid")
            slug = path.name[: -len(_PUBLICATION_SUFFIX)]
            try:
                _require_slug(slug)
            except PublicReplayPublicationError as error:
                raise PublicReplayStoreError(
                    "public replay artifact filename is invalid"
                ) from error
            files[path.name] = path
        return files

    def _prune_tombstones(self) -> None:
        """Keep recoverable unpublished artifacts within deterministic count and byte bounds."""

        tombstones: list[tuple[int, str, Path, int]] = []
        try:
            paths = tuple(self._unpublished_root.iterdir())
        except OSError as error:
            raise PublicReplayStoreError("public replay tombstones are unavailable") from error
        for path in paths:
            match = _TOMBSTONE_FILE.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or match is None:
                raise PublicReplayStoreError("public replay tombstone entry is invalid")
            try:
                size = path.stat().st_size
            except OSError as error:
                raise PublicReplayStoreError("public replay tombstone is unavailable") from error
            if size < 0 or size > _MAX_ARTIFACT_BYTES:
                raise PublicReplayStoreError("public replay tombstone is invalid")
            tombstones.append((int(match.group(2)), path.name, path, size))

        tombstones.sort(key=lambda item: (item[0], item[1]))
        total_bytes = sum(item[3] for item in tombstones)
        removed = False
        while tombstones and (
            len(tombstones) > self._max_tombstone_count
            or total_bytes > self._max_tombstone_bytes
        ):
            _, _, path, size = tombstones.pop(0)
            try:
                path.unlink()
            except OSError as error:
                raise PublicReplayStoreError(
                    "public replay tombstone could not be pruned"
                ) from error
            total_bytes -= size
            removed = True
        if removed:
            self._fsync_directory(self._unpublished_root)

    @staticmethod
    def _indexed_names(entries: Sequence[Mapping[str, object]]) -> set[str]:
        return {
            f"{entry['publication_slug']}{_PUBLICATION_SUFFIX}"
            for entry in entries
        }

    def _validate_active_index_bindings(
        self,
        entries: Sequence[Mapping[str, object]],
        active_files: Mapping[str, Path],
    ) -> tuple[
        dict[str, PublicReplayPublication],
        dict[str, PublicReplayPublication],
    ]:
        if set(active_files) != self._indexed_names(entries):
            raise PublicReplayStoreError("public replay index and artifacts differ")
        by_slug: dict[str, PublicReplayPublication] = {}
        by_cartridge: dict[str, PublicReplayPublication] = {}
        for entry in entries:
            slug = str(entry["publication_slug"])
            publication = self._read_bound_publication(
                active_files[f"{slug}{_PUBLICATION_SUFFIX}"],
                slug=slug,
                publication_sha256=str(entry["publication_sha256"]),
            )
            if self._index_entry(publication) != entry:
                raise PublicReplayStoreError("public replay artifact binding differs")
            if publication.cartridge_sha256 in by_cartridge:
                raise PublicReplayStoreError("duplicate public replay cartridge binding")
            by_slug[slug] = publication
            by_cartridge[publication.cartridge_sha256] = publication
        return by_slug, by_cartridge

    def _read_bound_publication(
        self,
        path: Path,
        *,
        slug: str,
        publication_sha256: str,
    ) -> PublicReplayPublication:
        try:
            publication = PublicReplayPublication.from_dict(self._read_json(path))
        except PublicReplayPublicationError as error:
            raise PublicReplayStoreError("public replay publication is invalid") from error
        if (
            publication.publication_slug != slug
            or publication.publication_sha256 != publication_sha256
        ):
            raise PublicReplayStoreError("public replay artifact binding differs")
        return publication

    def _new_slug(self) -> str:
        for _ in range(128):
            token = self._token_factory()
            slug = f"pub_{token}"
            if _PUBLICATION_SLUG.fullmatch(slug) and slug not in self._by_slug:
                return slug
        raise PublicReplayStoreError("could not allocate an unlisted publication slug")

    def _publication_path(self, slug: str) -> Path:
        return self._publications_root / f"{_require_slug(slug)}{_PUBLICATION_SUFFIX}"

    @staticmethod
    def _index_entry(publication: PublicReplayPublication) -> Mapping[str, object]:
        return {
            "cartridge_sha256": publication.cartridge_sha256,
            "game_id": publication.game_id,
            "publication_sha256": publication.publication_sha256,
            "publication_slug": publication.publication_slug,
            "published_at_epoch_ms": publication.published_at_epoch_ms,
        }

    @classmethod
    def _index_payload(
        cls,
        publications: Iterable[PublicReplayPublication],
    ) -> Mapping[str, object]:
        return cls._index_payload_from_entries(
            [cls._index_entry(publication) for publication in publications]
        )

    @staticmethod
    def _index_payload_from_entries(
        entries: Iterable[Mapping[str, object]],
    ) -> Mapping[str, object]:
        publications = sorted(
            (dict(entry) for entry in entries),
            key=lambda item: str(item["publication_slug"]),
        )
        body: Mapping[str, object] = {
            "publications": publications,
            "schema_version": PUBLIC_REPLAY_INDEX_SCHEMA_VERSION,
        }
        return {**body, "index_sha256": canonical_sha256(body)}

    def _write_index(self, publications: Iterable[PublicReplayPublication]) -> None:
        index = self._index_payload(publications)
        self._atomic_write(self._index_path, canonical_json_bytes(index), replace=True)

    def _write_index_entries(self, entries: Iterable[Mapping[str, object]]) -> None:
        index = self._index_payload_from_entries(entries)
        self._atomic_write(self._index_path, canonical_json_bytes(index), replace=True)

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        if path.is_symlink():
            raise PublicReplayStoreError("public replay artifact cannot be a symbolic link")
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_ARTIFACT_BYTES:
                raise PublicReplayStoreError("public replay artifact is invalid")
            payload = b""
            while len(payload) <= _MAX_ARTIFACT_BYTES:
                chunk = os.read(descriptor, min(64 * 1024, _MAX_ARTIFACT_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload += chunk
            if len(payload) > _MAX_ARTIFACT_BYTES:
                raise PublicReplayStoreError("public replay artifact is too large")
        except OSError as error:
            raise PublicReplayStoreError("public replay artifact is unavailable") from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            value = strict_json_loads(payload)
        except (ProtocolValidationError, TypeError, ValueError) as error:
            raise PublicReplayStoreError("public replay artifact JSON is invalid") from error
        if not isinstance(value, Mapping):
            raise PublicReplayStoreError("public replay artifact is invalid")
        return value

    @classmethod
    def _atomic_write(cls, path: Path, payload: bytes, *, replace: bool) -> None:
        if len(payload) > _MAX_ARTIFACT_BYTES:
            raise PublicReplayStoreError("public replay artifact is too large")
        if path.is_symlink() or (not replace and path.exists()):
            raise PublicReplayStoreError("public replay artifact target is invalid")
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if replace:
                os.replace(temporary, path)
            else:
                # ``Path.exists`` above is only an early diagnostic.  The hard-link operation is
                # the atomic no-clobber commit and cannot replace a target created by another
                # worker after that check.
                os.link(temporary, path, follow_symlinks=False)
                temporary.unlink()
            os.chmod(path, 0o600)
            cls._fsync_directory(path.parent)
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise PublicReplayStoreError("public replay artifact could not be committed") from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor: int | None = None
        try:
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            os.fsync(descriptor)
        except OSError as error:
            raise PublicReplayStoreError(
                "public replay directory could not be synchronized"
            ) from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _validate_transaction(value: object) -> Mapping[str, object]:
    parsed = _exact_mapping(value, _TRANSACTION_FIELDS, label="public replay transaction")
    if parsed["schema_version"] != _PUBLIC_REPLAY_TRANSACTION_SCHEMA_VERSION:
        raise PublicReplayStoreError("public replay transaction schema is unsupported")
    operation = parsed["operation"]
    if not isinstance(operation, str) or operation not in _TRANSACTION_OPERATIONS:
        raise PublicReplayStoreError("public replay transaction operation is invalid")
    slug = _require_slug(parsed["publication_slug"])
    before_index_sha256 = _require_sha256(
        "before_index_sha256", parsed["before_index_sha256"]
    )
    after_index_sha256 = _require_sha256(
        "after_index_sha256", parsed["after_index_sha256"]
    )
    if before_index_sha256 == after_index_sha256:
        raise PublicReplayStoreError("public replay transaction index transition is invalid")
    tombstone_name = parsed["tombstone_name"]
    if operation == "publish":
        if tombstone_name is not None:
            raise PublicReplayStoreError("publish transaction tombstone is invalid")
    elif (
        not isinstance(tombstone_name, str)
        or Path(tombstone_name).name != tombstone_name
        or re.fullmatch(
            rf"{re.escape(slug)}\.[0-9]+\.[0-9a-f]{{12}}{re.escape(_PUBLICATION_SUFFIX)}",
            tombstone_name,
        )
        is None
    ):
        raise PublicReplayStoreError("unpublish transaction tombstone is invalid")
    body: Mapping[str, object] = {
        "after_index_sha256": after_index_sha256,
        "before_index_sha256": before_index_sha256,
        "operation": operation,
        "publication_sha256": _require_sha256(
            "publication_sha256", parsed["publication_sha256"]
        ),
        "publication_slug": slug,
        "schema_version": _PUBLIC_REPLAY_TRANSACTION_SCHEMA_VERSION,
        "tombstone_name": tombstone_name,
    }
    transaction_sha256 = _require_sha256(
        "transaction_sha256", parsed["transaction_sha256"]
    )
    if transaction_sha256 != canonical_sha256(body):
        raise PublicReplayStoreError("public replay transaction fingerprint differs")
    return {**body, "transaction_sha256": transaction_sha256}


def _validate_index(value: object) -> list[Mapping[str, object]]:
    parsed = _exact_mapping(value, _INDEX_FIELDS, label="public replay index")
    if parsed["schema_version"] != PUBLIC_REPLAY_INDEX_SCHEMA_VERSION:
        raise PublicReplayStoreError("public replay index schema is unsupported")
    publications = parsed["publications"]
    if not isinstance(publications, list):
        raise PublicReplayStoreError("public replay index publications are invalid")
    body = {
        "publications": publications,
        "schema_version": PUBLIC_REPLAY_INDEX_SCHEMA_VERSION,
    }
    if parsed["index_sha256"] != canonical_sha256(body):
        raise PublicReplayStoreError("public replay index fingerprint differs")
    entries: list[Mapping[str, object]] = []
    previous_slug = ""
    cartridges: set[str] = set()
    for raw in publications:
        try:
            entry = _exact_mapping(raw, _INDEX_ENTRY_FIELDS, label="public replay index entry")
            slug = _require_slug(entry["publication_slug"])
            cartridge = _require_sha256("cartridge_sha256", entry["cartridge_sha256"])
            normalized = {
                "cartridge_sha256": cartridge,
                "game_id": _require_identifier("game_id", entry["game_id"]),
                "publication_sha256": _require_sha256(
                    "publication_sha256", entry["publication_sha256"]
                ),
                "publication_slug": slug,
                "published_at_epoch_ms": _require_nonnegative_int(
                    "published_at_epoch_ms", entry["published_at_epoch_ms"]
                ),
            }
        except PublicReplayPublicationError as error:
            raise PublicReplayStoreError("public replay index entry is invalid") from error
        if slug <= previous_slug or cartridge in cartridges:
            raise PublicReplayStoreError("public replay index ordering or uniqueness differs")
        previous_slug = slug
        cartridges.add(cartridge)
        entries.append(normalized)
    return entries


__all__ = [
    "PUBLIC_GAME_REPLAYS_SCHEMA_VERSION",
    "PUBLIC_REPLAY_INDEX_SCHEMA_VERSION",
    "PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION",
    "PublicReplayNotFoundError",
    "PublicReplayPublication",
    "PublicReplayPublicationError",
    "PublicReplayStore",
    "PublicReplayStoreError",
]
