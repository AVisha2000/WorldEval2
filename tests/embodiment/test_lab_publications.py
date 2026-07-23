from __future__ import annotations

import json
import os
import re
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

import pytest
from genesis_arena.embodiment.lab.contracts import (
    RaceCartridge,
    ReplayProjection,
    RunContract,
    RunEntrant,
    RunLifecycle,
    RunMode,
    RunState,
)
from genesis_arena.embodiment.lab.publications import (
    PUBLIC_GAME_REPLAYS_SCHEMA_VERSION,
    PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION,
    PublicReplayNotFoundError,
    PublicReplayPublication,
    PublicReplayPublicationError,
    PublicReplayStore,
    PublicReplayStoreError,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes


def _contract(
    *,
    run_id: str = "run_private_operator_handle_001",
    seed: int = 42,
) -> RunContract:
    return RunContract.create(
        run_id=run_id,
        game_id="labyrinth-run",
        game_version="trio-maze-race-v1",
        mode=RunMode.EXPLORATORY,
        entrants=(
            RunEntrant("entrant_0", "gpt-5.6-sol", display_name="Sol"),
            RunEntrant("entrant_1", "gpt-5.6-terra", display_name="Terra"),
            RunEntrant("entrant_2", "gpt-5.6-luna", display_name="Luna"),
        ),
        seed_policy={"kind": "fixed", "seed": seed},
        budget={"participant_call_budget": 216, "scope": "per_participant"},
        configuration={"skill_mode": "none", "vision_depth": 4},
        runtime_version="worldeval-labyrinth-live-v1",
        scenario_id="live-labyrinth",
        map_id="pilot-medium-01",
        map_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )


def _state(contract: RunContract, status: RunLifecycle) -> RunState:
    state = RunState.create(run_id=contract.run_id, contract_sha256=contract.contract_sha256)
    if status is RunLifecycle.DRAFT:
        return state
    state = state.transition(RunLifecycle.QUEUED)
    if status is RunLifecycle.QUEUED:
        return state
    state = state.transition(RunLifecycle.RUNNING)
    if status is RunLifecycle.RUNNING:
        return state
    state = state.transition(
        RunLifecycle.COMPLETED,
        checkpoint_sequence=1,
        result_sha256="c" * 64,
    )
    if status is RunLifecycle.COMPLETED:
        return state
    state = state.transition(RunLifecycle.SEALED)
    if status is RunLifecycle.SEALED:
        return state
    return state.transition(RunLifecycle.VERIFIED)


def _public_run_record(
    *,
    status: RunLifecycle = RunLifecycle.COMPLETED,
    replay_available: bool | None = None,
    run_id: str = "run_private_operator_handle_001",
    seed: int = 42,
) -> Mapping[str, Any]:
    contract = _contract(run_id=run_id, seed=seed)
    state = _state(contract, status)
    projection = ReplayProjection.create(
        run_id=contract.run_id,
        contract_sha256=contract.contract_sha256,
        status=status,
        sequence=state.checkpoint_sequence,
        snapshot={
            "map": {"map_id": "pilot-medium-01", "map_sha256": "a" * 64},
            "result": {"reason": "all_racers_finished", "winner_id": "participant_0"},
            "racers": [
                {
                    "finished": True,
                    "participant_id": "participant_0",
                    "path": [[1, 1], [2, 1]],
                }
            ],
        },
        events=(
            {
                "kind": "move",
                "movement_mode": "corridor",
                "participant_id": "participant_0",
                "tick": 8,
            },
        ),
    )
    cartridge = RaceCartridge.create(
        cartridge_id=f"cartridge_{contract.run_id}",
        contract=contract,
        state=state,
        public_projection=projection,
    )
    if replay_available is None:
        replay_available = status in {
            RunLifecycle.COMPLETED,
            RunLifecycle.SEALED,
            RunLifecycle.VERIFIED,
        }
    return {
        "authority_available": False,
        "cartridge": cartridge.as_dict(),
        "contract": contract.as_dict(),
        "created_at_epoch_ms": 1_722_000_000_000,
        "replay_available": replay_available,
        "resume_supported": False,
        "run_id": contract.run_id,
        "state": state.as_dict(),
        "video_available": True,
    }


def _publication_create(**overrides: object) -> PublicReplayPublication:
    values: dict[str, object] = {
        "publication_slug": "pub_" + "A" * 32,
        "game_id": "labyrinth-run",
        "game_version": "trio-maze-race-v1",
        "contract_sha256": "a" * 64,
        "lifecycle_status": RunLifecycle.COMPLETED,
        "state_sha256": "b" * 64,
        "result_sha256": "c" * 64,
        "projection_sha256": "d" * 64,
        "cartridge_sha256": "e" * 64,
        "replay_sequence": 1,
        "replay_snapshot": {"winner_id": "participant_0"},
        "replay_events": ({"kind": "finish", "tick": 8},),
        "published_at_epoch_ms": 1_722_000_001_000,
        "method_label": "explicit_unlisted_link",
    }
    values.update(overrides)
    return PublicReplayPublication.create(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "status",
    (RunLifecycle.COMPLETED, RunLifecycle.SEALED, RunLifecycle.VERIFIED),
)
def test_completed_sealed_and_verified_public_runs_can_be_published(
    tmp_path: Path, status: RunLifecycle
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )

    published = store.publish(_public_run_record(status=status))

    assert published["schema_version"] == PUBLIC_REPLAY_PUBLICATION_SCHEMA_VERSION
    assert published["publication_slug"] == "pub_" + "A" * 32
    assert published["game_id"] == "labyrinth-run"
    assert published["game_version"] == "trio-maze-race-v1"
    assert published["lifecycle_status"] == status.value
    assert published["published_at_epoch_ms"] == 1_722_000_001_000
    assert published["method_label"] == "explicit_unlisted_link"
    assert published["replay"]["snapshot"]["result"]["winner_id"] == "participant_0"
    assert published["replay"]["events"][0]["kind"] == "move"
    assert "run_id" not in published
    assert "contract" not in published
    assert "authority_available" not in published
    assert "video_available" not in published
    assert PublicReplayPublication.from_dict(published).as_dict() == published

    artifact = (
        tmp_path / "lab-publications" / "publications" / f"{published['publication_slug']}.json"
    )
    assert artifact.is_file()
    assert stat.S_IMODE(artifact.stat().st_mode) & 0o077 == 0


@pytest.mark.parametrize(
    ("status", "replay_available"),
    (
        (RunLifecycle.DRAFT, False),
        (RunLifecycle.QUEUED, True),
        (RunLifecycle.RUNNING, True),
        (RunLifecycle.COMPLETED, False),
    ),
)
def test_draft_in_flight_and_replay_unavailable_runs_are_rejected(
    tmp_path: Path, status: RunLifecycle, replay_available: bool
) -> None:
    store = PublicReplayStore(runs_dir=tmp_path)
    with pytest.raises(PublicReplayPublicationError):
        store.publish(_public_run_record(status=status, replay_available=replay_available))
    assert store.list() == []


def test_publish_revalidates_every_canonical_binding(tmp_path: Path) -> None:
    store = PublicReplayStore(runs_dir=tmp_path)
    record = dict(_public_run_record())

    other_contract = RunContract.create(
        run_id="run_other_private_handle",
        game_id="labyrinth-run",
        game_version="trio-maze-race-v1",
        mode=RunMode.EXPLORATORY,
        entrants=(RunEntrant("entrant_0", "gpt-5.6-sol"),),
        seed_policy={"kind": "fixed", "seed": 7},
        budget={"calls": 10},
        configuration={"vision_depth": 1},
        runtime_version="worldeval-labyrinth-live-v1",
    )
    record["contract"] = other_contract.as_dict()
    with pytest.raises(PublicReplayPublicationError, match="bindings"):
        store.publish(record)

    fingerprint_tamper = dict(_public_run_record())
    cartridge = json.loads(json.dumps(fingerprint_tamper["cartridge"]))
    cartridge["cartridge_sha256"] = "f" * 64
    fingerprint_tamper["cartridge"] = cartridge
    with pytest.raises(PublicReplayPublicationError, match="canonical artifacts"):
        store.publish(fingerprint_tamper)


@pytest.mark.parametrize(
    "unsafe_snapshot",
    (
        {"navigation_memory": {"position": [2, 3]}},
        {"nested": {"rawResponse": "private model material"}},
        {"nested": {"source_id": "ep_private_source"}},
        {"nested": {"series_id": "series_private_source"}},
        {"nested": {"replay_id": "replay_private_source"}},
        {"active_controls": {"cancel": "/private/cancel"}},
        {"label": "ghp_abcdefghijklmnopqrstuvwxyz123456"},
    ),
)
def test_nested_protected_or_credential_material_is_rejected(
    unsafe_snapshot: Mapping[str, object],
) -> None:
    with pytest.raises(PublicReplayPublicationError):
        _publication_create(replay_snapshot=unsafe_snapshot)


def test_run_record_cannot_smuggle_private_source_fields(tmp_path: Path) -> None:
    record = dict(_public_run_record())
    record["source_id"] = "ep_private_source"
    with pytest.raises(PublicReplayPublicationError, match="fields differ"):
        PublicReplayStore(runs_dir=tmp_path).publish(record)


def test_publish_is_idempotent_by_cartridge_hash_and_reloadable(tmp_path: Path) -> None:
    tokens = iter(("A" * 32, "B" * 32))
    clocks = iter((1_722_000_001_000, 1_722_000_002_000))
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: next(tokens),
        clock_ms=lambda: next(clocks),
    )
    record = _public_run_record()

    first = store.publish(record)
    second = store.publish(record, method_label="curated_example")

    assert second == first
    assert len(store.list()) == 1
    assert first["method_label"] == "explicit_unlisted_link"
    reloaded = PublicReplayStore(runs_dir=tmp_path)
    assert reloaded.get(str(first["publication_slug"])) == first
    assert reloaded.list() == [first]


def test_two_preloaded_store_instances_preserve_sequential_distinct_publications(
    tmp_path: Path,
) -> None:
    first_store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )
    stale_second_store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "B" * 32,
    )

    first = first_store.publish(_public_run_record())
    second = stale_second_store.publish(
        _public_run_record(run_id="run_private_operator_handle_002", seed=43)
    )

    assert {item["publication_slug"] for item in first_store.list()} == {
        first["publication_slug"],
        second["publication_slug"],
    }
    assert {item["publication_slug"] for item in PublicReplayStore(runs_dir=tmp_path).list()} == {
        first["publication_slug"],
        second["publication_slug"],
    }


def test_two_store_instances_preserve_concurrent_distinct_publications(
    tmp_path: Path,
) -> None:
    first_store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )
    second_store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "B" * 32,
    )
    barrier = threading.Barrier(2)

    def publish(
        store: PublicReplayStore,
        record: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        barrier.wait()
        return store.publish(record)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(publish, first_store, _public_run_record())
        second_future = executor.submit(
            publish,
            second_store,
            _public_run_record(run_id="run_private_operator_handle_002", seed=43),
        )
        published = (first_future.result(), second_future.result())

    reloaded = PublicReplayStore(runs_dir=tmp_path)
    assert {item["publication_slug"] for item in reloaded.list()} == {
        item["publication_slug"] for item in published
    }
    assert len(reloaded.list()) == 2


def test_two_store_instances_make_concurrent_same_cartridge_publish_idempotent(
    tmp_path: Path,
) -> None:
    first_store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )
    second_store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "B" * 32,
    )
    barrier = threading.Barrier(2)
    record = _public_run_record()

    def publish(store: PublicReplayStore) -> Mapping[str, Any]:
        barrier.wait()
        return store.publish(record)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(publish, first_store)
        second_future = executor.submit(publish, second_store)
        first = first_future.result()
        second = second_future.result()

    assert first == second
    assert PublicReplayStore(runs_dir=tmp_path).list() == [first]


def test_unlisted_slug_is_random_shaped_and_independent_of_private_handles(
    tmp_path: Path,
) -> None:
    store = PublicReplayStore(runs_dir=tmp_path)
    published = store.publish(_public_run_record())
    slug = str(published["publication_slug"])

    assert re.fullmatch(r"pub_[A-Za-z0-9_-]{32}", slug)
    assert "run_private" not in slug
    assert "operator" not in slug
    assert "ep_" not in slug


def test_list_and_public_game_projection_filter_only_safe_publications(
    tmp_path: Path,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())

    assert store.list(game_id="other-game") == []
    assert store.list(game_id="labyrinth-run") == [published]
    projection = store.public_game_projection("labyrinth-run")
    assert projection == {
        "game_id": "labyrinth-run",
        "publications": [published],
        "schema_version": PUBLIC_GAME_REPLAYS_SCHEMA_VERSION,
    }


def test_unpublish_removes_only_active_index_and_keeps_recoverable_publication(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "lab" / "run_private_operator_handle_001" / "record.json"
    source_artifact.parent.mkdir(parents=True)
    source_artifact.write_text("source-run-must-survive", encoding="utf-8")
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())
    slug = str(published["publication_slug"])

    assert store.unpublish(slug) is True
    assert store.unpublish(slug) is False
    with pytest.raises(PublicReplayNotFoundError):
        store.get(slug)
    assert store.list() == []
    assert source_artifact.read_text(encoding="utf-8") == "source-run-must-survive"
    tombstones = list((tmp_path / "lab-publications" / ".unpublished").glob(f"{slug}.*.json"))
    assert len(tombstones) == 1
    assert PublicReplayStore(runs_dir=tmp_path).list() == []


def test_unpublish_tombstones_are_pruned_to_configured_count_bound(
    tmp_path: Path,
) -> None:
    token_sequence = iter(f"{index:032d}" for index in range(5))
    clock_sequence = iter(range(1_722_000_001_000, 1_722_000_001_020))
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: next(clock_sequence),
        token_factory=lambda: next(token_sequence),
        max_tombstone_count=2,
    )

    for _ in range(5):
        published = store.publish(_public_run_record())
        assert store.unpublish(str(published["publication_slug"])) is True

    tombstone_root = tmp_path / "lab-publications" / ".unpublished"
    tombstones = sorted(tombstone_root.glob("*.json"))
    assert len(tombstones) == 2
    assert all(path.is_file() and not path.is_symlink() for path in tombstones)
    assert (
        len(
            PublicReplayStore(
                runs_dir=tmp_path,
                max_tombstone_count=2,
            ).list()
        )
        == 0
    )
    assert len(list(tombstone_root.glob("*.json"))) == 2


def test_unpublish_tombstones_are_pruned_to_configured_byte_bound(
    tmp_path: Path,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
        max_tombstone_count=10,
        max_tombstone_bytes=1,
    )
    published = store.publish(_public_run_record())

    assert store.unpublish(str(published["publication_slug"])) is True

    tombstone_root = tmp_path / "lab-publications" / ".unpublished"
    assert sum(path.stat().st_size for path in tombstone_root.glob("*.json")) <= 1
    assert list(tombstone_root.glob("*.json")) == []
    assert (
        PublicReplayStore(
            runs_dir=tmp_path,
            max_tombstone_count=10,
            max_tombstone_bytes=1,
        ).list()
        == []
    )


def test_restart_completes_publish_crash_between_artifact_and_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )

    def crash_before_index(_publications: object) -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_write_index", crash_before_index)
    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        store.publish(_public_run_record())

    root = tmp_path / "lab-publications"
    assert (root / ".transaction.json").is_file()
    assert (root / "publications" / ("pub_" + "A" * 32 + ".json")).is_file()

    recovered = PublicReplayStore(runs_dir=tmp_path)

    assert [item["publication_slug"] for item in recovered.list()] == [
        "pub_" + "A" * 32
    ]
    assert not (root / ".transaction.json").exists()
    assert PublicReplayStore(runs_dir=tmp_path).list() == recovered.list()


def test_restart_completes_unpublish_crash_between_tombstone_and_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())
    slug = str(published["publication_slug"])

    def crash_before_index(_publications: object) -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_write_index", crash_before_index)
    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        store.unpublish(slug)

    root = tmp_path / "lab-publications"
    assert (root / ".transaction.json").is_file()
    assert not (root / "publications" / f"{slug}.json").exists()
    assert len(list((root / ".unpublished").glob(f"{slug}.*.json"))) == 1

    recovered = PublicReplayStore(runs_dir=tmp_path)

    assert recovered.list() == []
    assert not (root / ".transaction.json").exists()
    assert len(list((root / ".unpublished").glob(f"{slug}.*.json"))) == 1
    assert PublicReplayStore(runs_dir=tmp_path).list() == []


def test_restart_finalizes_publish_when_crash_leaves_committed_index_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )

    def crash_before_journal_clear() -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_clear_transaction", crash_before_journal_clear)
    with pytest.raises(KeyboardInterrupt):
        store.publish(_public_run_record())

    recovered = PublicReplayStore(runs_dir=tmp_path)

    assert [item["publication_slug"] for item in recovered.list()] == [
        "pub_" + "A" * 32
    ]
    assert not (tmp_path / "lab-publications" / ".transaction.json").exists()


def test_restart_finalizes_unpublish_when_crash_leaves_committed_index_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())

    def crash_before_journal_clear() -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_clear_transaction", crash_before_journal_clear)
    with pytest.raises(KeyboardInterrupt):
        store.unpublish(str(published["publication_slug"]))

    recovered = PublicReplayStore(runs_dir=tmp_path)

    assert recovered.list() == []
    assert not (tmp_path / "lab-publications" / ".transaction.json").exists()


def test_publish_recovery_rejects_tampered_orphan_even_with_valid_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )

    def crash_before_index(_publications: object) -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_write_index", crash_before_index)
    with pytest.raises(KeyboardInterrupt):
        store.publish(_public_run_record())

    artifact = (
        tmp_path
        / "lab-publications"
        / "publications"
        / ("pub_" + "A" * 32 + ".json")
    )
    tampered = json.loads(artifact.read_text(encoding="utf-8"))
    tampered["method_label"] = "tampered"
    artifact.write_bytes(canonical_json_bytes(tampered))

    with pytest.raises(PublicReplayStoreError, match="publication is invalid"):
        PublicReplayStore(runs_dir=tmp_path)


def test_unpublish_recovery_rejects_tampered_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        clock_ms=lambda: 1_722_000_001_000,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())

    def crash_before_index(_publications: object) -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_write_index", crash_before_index)
    with pytest.raises(KeyboardInterrupt):
        store.unpublish(str(published["publication_slug"]))

    tombstone = next((tmp_path / "lab-publications" / ".unpublished").glob("*.json"))
    tampered = json.loads(tombstone.read_text(encoding="utf-8"))
    tampered["published_at_epoch_ms"] += 1
    tombstone.write_bytes(canonical_json_bytes(tampered))

    with pytest.raises(PublicReplayStoreError, match="publication is invalid"):
        PublicReplayStore(runs_dir=tmp_path)


def test_recovery_rejects_corrupt_transaction_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )

    def crash_before_index(_publications: object) -> None:
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(store, "_write_index", crash_before_index)
    with pytest.raises(KeyboardInterrupt):
        store.publish(_public_run_record())

    journal = tmp_path / "lab-publications" / ".transaction.json"
    corrupt = json.loads(journal.read_text(encoding="utf-8"))
    corrupt["after_index_sha256"] = "0" * 64
    journal.write_bytes(canonical_json_bytes(corrupt))

    with pytest.raises(PublicReplayStoreError, match="transaction is invalid"):
        PublicReplayStore(runs_dir=tmp_path)


def test_publication_reload_fails_closed_for_symlinked_artifact(tmp_path: Path) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())
    artifact = (
        tmp_path / "lab-publications" / "publications" / f"{published['publication_slug']}.json"
    )
    outside = tmp_path / "outside.json"
    outside.write_bytes(artifact.read_bytes())
    artifact.unlink()
    artifact.symlink_to(outside)

    with pytest.raises(PublicReplayStoreError):
        PublicReplayStore(runs_dir=tmp_path)


def test_store_root_symlink_and_partial_index_corruption_fail_closed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    symlink_runs = tmp_path / "symlink-runs"
    symlink_runs.mkdir()
    (symlink_runs / "lab-publications").symlink_to(target, target_is_directory=True)
    with pytest.raises(PublicReplayStoreError, match="symbolic link"):
        PublicReplayStore(runs_dir=symlink_runs)

    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )
    store.publish(_public_run_record())
    index = tmp_path / "lab-publications" / "index.json"
    index.write_bytes(b'{"schema_version":')
    with pytest.raises(PublicReplayStoreError):
        PublicReplayStore(runs_dir=tmp_path)


def test_atomic_write_replace_false_is_race_safe_no_clobber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "no-clobber.json"
    barrier = threading.Barrier(2)
    original_link = os.link

    def synchronized_link(
        source: os.PathLike[str],
        destination: os.PathLike[str],
        *,
        follow_symlinks: bool = True,
    ) -> None:
        barrier.wait()
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", synchronized_link)

    def write(payload: bytes) -> object:
        try:
            PublicReplayStore._atomic_write(target, payload, replace=False)
        except PublicReplayStoreError as error:
            return error
        return payload

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(write, b'{"winner":"first"}')
        second_future = executor.submit(write, b'{"winner":"second"}')
        outcomes = (first_future.result(), second_future.result())

    successes = [outcome for outcome in outcomes if isinstance(outcome, bytes)]
    failures = [
        outcome for outcome in outcomes if isinstance(outcome, PublicReplayStoreError)
    ]
    assert len(successes) == 1
    assert len(failures) == 1
    assert target.read_bytes() == successes[0]


def test_orphaned_partial_publication_fails_closed(tmp_path: Path) -> None:
    PublicReplayStore(runs_dir=tmp_path)
    orphan = tmp_path / "lab-publications" / "publications" / ("pub_" + "Z" * 32 + ".json")
    orphan.write_bytes(_publication_create(publication_slug="pub_" + "Z" * 32).canonical_bytes)
    with pytest.raises(PublicReplayStoreError, match="index and artifacts differ"):
        PublicReplayStore(runs_dir=tmp_path)


def test_publication_artifacts_pass_full_protected_material_scan(tmp_path: Path) -> None:
    store = PublicReplayStore(
        runs_dir=tmp_path,
        token_factory=lambda: "A" * 32,
    )
    published = store.publish(_public_run_record())
    payload = canonical_json_bytes(published).lower()

    for forbidden in (
        b"api_key",
        b"authority_available",
        b"authorization",
        b"chain_of_thought",
        b"credential",
        b"episode_id",
        b"navigation_memory",
        b"observation",
        b"password",
        b"prompt_text",
        b"provider_response",
        b"raw_response",
        b"run_private_operator_handle",
        b"scratchpad",
        b"source_id",
        b"system_prompt",
    ):
        assert forbidden not in payload
    root = tmp_path / "lab-publications"
    all_active_bytes = b"".join(
        path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".unpublished" not in path.parts
    ).lower()
    assert b"sk-proj-" not in all_active_bytes
    assert b"ghp_" not in all_active_bytes


def test_publication_hash_detects_tampering() -> None:
    publication = _publication_create()
    tampered = dict(publication.as_dict())
    tampered["method_label"] = "other_method"
    with pytest.raises(PublicReplayPublicationError, match="fingerprint"):
        PublicReplayPublication.from_dict(tampered)


def test_publication_store_never_follows_index_symlink(tmp_path: Path) -> None:
    root = tmp_path / "lab-publications"
    (root / "publications").mkdir(parents=True)
    (root / ".unpublished").mkdir()
    external_index = tmp_path / "external-index.json"
    external_index.write_text("{}", encoding="utf-8")
    os.symlink(external_index, root / "index.json")

    with pytest.raises(PublicReplayStoreError, match="symbolic link"):
        PublicReplayStore(runs_dir=tmp_path)
