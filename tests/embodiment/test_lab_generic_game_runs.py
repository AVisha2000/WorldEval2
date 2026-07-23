from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest
from genesis_arena.embodiment.lab.contracts import RunEntrant, RunMode
from genesis_arena.embodiment.lab.game_authority import (
    GameAuthorityError,
    GameAuthorityGateway,
)
from genesis_arena.embodiment.lab.game_runs import (
    AttachedGameRun,
    GenericLabRunError,
    GenericLabRunService,
)


@dataclass(frozen=True)
class _FrameSnapshot:
    png: bytes
    sha256: str


@dataclass(frozen=True)
class _FrameView:
    state: str
    snapshot: _FrameSnapshot | None


class _Solo:
    def __init__(self) -> None:
        self.state = "queued"
        self.cancelled = False
        self.result_value: Mapping[str, object] = {"result": {"outcome": "success", "score": 7}}
        self.evaluation_value: Mapping[str, object] = {
            "metrics": {"completion": 1, "efficiency_percent": 80}
        }
        self.timeline_value: tuple[Mapping[str, object], ...] = (
            {"kind": "episode_started", "sequence": 1},
            {"kind": "episode_completed", "sequence": 2},
        )
        self.png = b"\x89PNG\r\n\x1a\n" + b"safe-frame"

    async def status(self, source_id: str) -> Mapping[str, Any]:
        return {
            "episode_id": source_id,
            "failure": None,
            "progress": {"authority_tick": 12, "observation_seq": 3},
            "replay": {"state": "ready"},
            "state": self.state,
        }

    async def result(self, source_id: str) -> Mapping[str, object]:
        return {
            **self.result_value,
            "episode_id": source_id,
            "replay": {"replay_id": "replay_solo_fixture"},
        }

    async def evaluation(self, source_id: str) -> Mapping[str, object]:
        return {
            **self.evaluation_value,
            "episode_id": source_id,
        }

    async def timeline(self, source_id: str) -> tuple[Mapping[str, object], ...]:
        return self.timeline_value

    async def replay(self, source_id: str) -> object:
        return object()

    async def frame(self, source_id: str) -> _FrameView:
        return _FrameView("live", _FrameSnapshot(self.png, hashlib.sha256(self.png).hexdigest()))

    async def cancel(self, source_id: str) -> Mapping[str, Any]:
        self.cancelled = True
        self.state = "cancelled"
        return await self.status(source_id)


class _UnusedSeries:
    async def status(self, source_id: str) -> Mapping[str, object]:
        raise AssertionError("unused")

    async def result(self, source_id: str) -> Mapping[str, object]:
        raise AssertionError("unused")

    async def evaluation(self, source_id: str) -> Mapping[str, object]:
        raise AssertionError("unused")

    async def timeline(self, source_id: str) -> Mapping[str, object]:
        raise AssertionError("unused")

    async def replay(self, source_id: str) -> object:
        raise AssertionError("unused")

    async def participant_frame(self, source_id: str, participant_id: str) -> tuple[str, object]:
        raise AssertionError("unused")

    async def cancel(self, source_id: str) -> Mapping[str, object]:
        raise AssertionError("unused")


class _ActualShapedSeries(_UnusedSeries):
    async def result(self, source_id: str) -> Mapping[str, object]:
        return {
            "result": {"outcome": "win"},
            "series_id": source_id,
        }

    async def evaluation(self, source_id: str) -> Mapping[str, object]:
        return {
            "metrics": {"completion": 1},
            "references": {"replay_id": f"replay_{source_id}"},
            "series_id": source_id,
        }

    async def timeline(self, source_id: str) -> Mapping[str, object]:
        return {
            "events": [
                {
                    "episode_id": f"ep_{source_id}",
                    "kind": "finish",
                    "source_id": source_id,
                }
            ],
            "series_id": source_id,
        }


def _gateway(solo: _Solo) -> GameAuthorityGateway:
    unused = _UnusedSeries()
    return GameAuthorityGateway(solo=solo, paired=unused, trio=unused)


def _launch() -> AttachedGameRun:
    return AttachedGameRun(
        authority_kind="solo_episode",
        source_id="ep_live_fixture",
        game_id="solo-construction",
        game_version="construction-v0",
        runtime_version="godot-llm-controller-0.1.0",
        scenario_id=None,
        entrants=(
            RunEntrant(
                entrant_id="entrant_0",
                model_id="gpt-5.6-terra",
                provider="openai",
                display_name="Builder",
            ),
        ),
        seed=17,
        budget={"maximum_ticks": 600, "scope": "episode"},
        configuration={
            "interface_profile": "hybrid-visible-v1",
            "task_family": "construction",
        },
        mode=RunMode.EXPLORATORY,
    )


def _run_directory(tmp_path: Path, run_id: object) -> Path:
    return tmp_path / "lab-games" / str(run_id)


def _current_generation(tmp_path: Path, run_id: object) -> Path:
    directory = _run_directory(tmp_path, run_id)
    pointer = json.loads((directory / "current.json").read_text(encoding="utf-8"))
    return directory / "generations" / pointer["generation"]


@pytest.mark.asyncio
async def test_generic_game_run_seals_safe_cartridge_and_survives_restart(
    tmp_path: Path,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    assert created["state"]["status"] == "queued"
    assert created["authority_available"] is True
    assert created["contract"]["configuration"]["authority_kind"] == "solo_episode"

    solo.state = "completed"
    completed = await service.get_run(run_id)
    assert completed["state"]["status"] == "completed"
    assert completed["replay_available"] is True
    projection = await service.projection(run_id)
    assert projection["snapshot"]["terminal"]["result"]["result"]["outcome"] == "success"
    assert projection["snapshot"]["terminal"]["evaluation"]["metrics"]["completion"] == 1
    assert [event["kind"] for event in projection["events"]] == [
        "episode_started",
        "episode_completed",
    ]

    sealed = await service.seal(run_id)
    assert sealed["state"]["status"] == "sealed"
    assert sealed["replay_available"] is True
    verified = await service.verify(run_id)
    assert verified["state"]["status"] == "verified"
    assert verified["cartridge"]["public_projection"]["status"] == "verified"

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable = await reloaded.get_run(run_id)
    assert durable["state"]["status"] == "verified"
    assert durable["authority_available"] is False
    assert durable["replay_available"] is True
    generation = _current_generation(tmp_path, run_id)
    assert (generation / "cartridge.json").is_file()
    assert (
        _run_directory(tmp_path, run_id)
        / "commits"
        / f"{generation.name}.json"
    ).is_file()
    assert len(
        [
            path
            for path in generation.parent.iterdir()
            if path.name.startswith("gen-")
        ]
    ) == 3
    assert set(path.name for path in generation.glob("*.json")) == {
        "cartridge.json",
        "contract.json",
        "projection.json",
        "record.json",
        "state.json",
    }
    assert not any(
        b"ep_live_fixture" in path.read_bytes()
        for path in _run_directory(tmp_path, run_id).rglob("*.json")
    )
    assert not any(
        b"replay_solo_fixture" in path.read_bytes()
        for path in _run_directory(tmp_path, run_id).rglob("*.json")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authority_kind", "source_id"),
    (
        ("paired_series", "series_actual_fixture"),
        ("trio_series", "trio_actual_fixture"),
    ),
)
async def test_terminal_projection_strips_actual_authority_handles(
    authority_kind: str,
    source_id: str,
) -> None:
    series = _ActualShapedSeries()
    gateway = GameAuthorityGateway(solo=_Solo(), paired=series, trio=series)

    projection = await gateway.terminal_projection(authority_kind, source_id)  # type: ignore[arg-type]

    assert projection.snapshot["result"]["result"]["outcome"] == "win"
    assert projection.snapshot["evaluation"]["metrics"]["completion"] == 1
    assert projection.events == ({"kind": "finish"},)

    def keys(value: object) -> set[str]:
        if isinstance(value, Mapping):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, (list, tuple)):
            return set().union(*(keys(child) for child in value))
        return set()

    assert keys({"snapshot": projection.snapshot, "events": projection.events}).isdisjoint(
        {"episode_id", "replay_id", "series_id", "source_id"}
    )


@pytest.mark.asyncio
async def test_generic_game_run_rejects_sealing_before_completion(tmp_path: Path) -> None:
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    created = await service.attach(_launch())
    with pytest.raises(GenericLabRunError, match="completed"):
        await service.seal(created["run_id"])
    with pytest.raises(GenericLabRunError, match="sealed"):
        await service.verify(created["run_id"])


@pytest.mark.asyncio
async def test_generic_evidence_lifecycle_swaps_only_after_durable_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = str(created["run_id"])
    solo.state = "completed"
    assert (await service.get_run(run_id))["state"]["status"] == "completed"

    original_persist = service._persist

    def fail_sealed(record: Any) -> None:
        if record.state.status.value == "sealed":
            raise OSError("fixture seal persistence failure")
        original_persist(record)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_persist", fail_sealed)
    with pytest.raises(GenericLabRunError, match="could not be persisted"):
        await service.seal(run_id)
    assert (await service.get_run(run_id))["state"]["status"] == "completed"

    monkeypatch.setattr(service, "_persist", original_persist)
    assert (await service.seal(run_id))["state"]["status"] == "sealed"

    def fail_verified(record: Any) -> None:
        if record.state.status.value == "verified":
            raise OSError("fixture verify persistence failure")
        original_persist(record)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_persist", fail_verified)
    with pytest.raises(GenericLabRunError, match="could not be persisted"):
        await service.verify(run_id)
    assert (await service.get_run(run_id))["state"]["status"] == "sealed"

    monkeypatch.setattr(service, "_persist", original_persist)
    assert (await service.verify(run_id))["state"]["status"] == "verified"

    original_read = service._read
    readbacks = 0

    def observe_readback(directory: Path) -> Any:
        nonlocal readbacks
        readbacks += 1
        return original_read(directory)

    monkeypatch.setattr(service, "_read", observe_readback)
    assert (await service.verify(run_id))["state"]["status"] == "verified"
    assert readbacks == 1


@pytest.mark.asyncio
async def test_generic_game_run_live_frame_cancel_and_clone(tmp_path: Path) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    frame = await service.frame(created["run_id"], participant_id="participant_0")
    assert frame.state == "live"
    assert frame.png == solo.png

    clone = await service.clone(
        created["run_id"],
        changes={"configuration": created["contract"]["configuration"]},
    )
    assert clone["state"]["status"] == "draft"
    assert clone["contract"]["parent_contract_sha256"] == created["contract"]["contract_sha256"]
    assert clone["authority_available"] is False

    cancelled = await service.cancel(created["run_id"])
    assert solo.cancelled is True
    assert cancelled["state"]["status"] == "cancelled"
    assert cancelled["state"]["failure_code"] == "game_authority_cancelled"

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable_clone = await reloaded.get_run(clone["run_id"])
    assert durable_clone["state"]["status"] == "draft"
    assert durable_clone["contract"]["parent_contract_sha256"] == (
        created["contract"]["contract_sha256"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_file",
    (
        "record.json",
        "contract.json",
        "state.json",
        "projection.json",
        "cartridge.json",
    ),
)
async def test_restart_keeps_previous_generation_after_partial_bundle_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_file: str,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    previous = _current_generation(tmp_path, run_id)
    original_write = service._atomic_write

    def fail_during_bundle(path: Path, payload: bytes) -> None:
        if path.name == failure_file and path.parent.name.startswith(".gen-"):
            raise OSError("fixture crash during generation write")
        original_write(path, payload)

    monkeypatch.setattr(service, "_atomic_write", fail_during_bundle)
    solo.state = "running"
    with pytest.raises(OSError, match="generation write"):
        await service.get_run(run_id)

    assert _current_generation(tmp_path, run_id) == previous
    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable = await reloaded.get_run(run_id)
    assert durable["run_id"] == run_id
    assert durable["state"]["status"] == "queued"


@pytest.mark.asyncio
async def test_restart_ignores_complete_uncommitted_generation_when_pointer_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    previous = _current_generation(tmp_path, run_id)
    original_write = service._atomic_write

    def fail_pointer(path: Path, payload: bytes) -> None:
        if path.name == "current.json":
            raise OSError("fixture crash before pointer swap")
        original_write(path, payload)

    monkeypatch.setattr(service, "_atomic_write", fail_pointer)
    solo.state = "running"
    with pytest.raises(OSError, match="pointer swap"):
        await service.get_run(run_id)

    assert _current_generation(tmp_path, run_id) == previous
    generations = _run_directory(tmp_path, run_id) / "generations"
    assert len([path for path in generations.iterdir() if path.name.startswith("gen-")]) == 2
    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    assert (await reloaded.get_run(run_id))["state"]["status"] == "queued"


@pytest.mark.asyncio
async def test_restart_recovers_prior_generation_instead_of_accepting_protected_current(
    tmp_path: Path,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    previous = _current_generation(tmp_path, run_id)

    solo.state = "running"
    assert (await service.get_run(run_id))["state"]["status"] == "running"
    current = _current_generation(tmp_path, run_id)
    assert current != previous
    (current / "projection.json").write_text(
        '{"navigation_memory":{"secret":"must-not-load"}}',
        encoding="utf-8",
    )

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable = await reloaded.get_run(run_id)
    assert durable["state"]["status"] == "queued"
    assert "navigation_memory" not in str(await reloaded.projection(run_id))


@pytest.mark.asyncio
async def test_restart_recovers_newest_valid_generation_when_pointer_is_corrupt(
    tmp_path: Path,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    solo.state = "running"
    assert (await service.get_run(run_id))["state"]["status"] == "running"
    newest = _current_generation(tmp_path, run_id)
    (_run_directory(tmp_path, run_id) / "current.json").write_bytes(b'{"broken":true}')

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable = await reloaded.get_run(run_id)
    assert durable["state"]["status"] == "running"
    assert newest.is_dir()


@pytest.mark.asyncio
async def test_restart_reports_committed_run_when_every_generation_is_corrupt(
    tmp_path: Path,
) -> None:
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    created = await service.attach(_launch())
    (_current_generation(tmp_path, created["run_id"]) / "record.json").write_bytes(
        b'{"schema_version":"corrupt"}'
    )

    with pytest.raises(GenericLabRunError, match="saved run .* is unavailable"):
        GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))


@pytest.mark.asyncio
async def test_restart_ignores_never_committed_first_generation_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    original_write = service._atomic_write

    def fail_first_state(path: Path, payload: bytes) -> None:
        if path.name == "state.json":
            raise OSError("fixture first generation interruption")
        original_write(path, payload)

    monkeypatch.setattr(service, "_atomic_write", fail_first_state)
    with pytest.raises(OSError, match="first generation"):
        await service.attach(_launch())

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    assert await reloaded.list_runs() == []


@pytest.mark.asyncio
async def test_restart_does_not_promote_first_generation_when_initial_pointer_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    original_write = service._atomic_write

    def fail_initial_pointer(path: Path, payload: bytes) -> None:
        if path.name == "current.json":
            raise OSError("fixture initial pointer interruption")
        original_write(path, payload)

    monkeypatch.setattr(service, "_atomic_write", fail_initial_pointer)
    with pytest.raises(OSError, match="initial pointer"):
        await service.attach(_launch())

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    assert await reloaded.list_runs() == []


@pytest.mark.asyncio
async def test_corrupt_pointer_never_promotes_newer_uncommitted_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solo = _Solo()
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    directory = _run_directory(tmp_path, run_id)
    committed = _current_generation(tmp_path, run_id)
    original_write = service._atomic_write

    def fail_pointer(path: Path, payload: bytes) -> None:
        if path.name == "current.json":
            raise OSError("fixture pointer interruption")
        original_write(path, payload)

    monkeypatch.setattr(service, "_atomic_write", fail_pointer)
    solo.state = "running"
    with pytest.raises(OSError, match="pointer interruption"):
        await service.get_run(run_id)

    generations = sorted(
        path
        for path in (directory / "generations").iterdir()
        if path.name.startswith("gen-")
    )
    assert len(generations) == 2
    uncommitted = generations[-1]
    assert uncommitted != committed
    assert not (directory / "commits" / f"{uncommitted.name}.json").exists()
    (directory / "current.json").write_bytes(b'{"broken":true}')

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable = await reloaded.get_run(run_id)
    assert durable["state"]["status"] == "queued"
    assert durable["state"]["status"] != "running"


@pytest.mark.asyncio
async def test_restart_loads_legacy_flat_v1_bundle(tmp_path: Path) -> None:
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    created = await service.attach(_launch())
    run_id = created["run_id"]
    directory = _run_directory(tmp_path, run_id)
    generation = _current_generation(tmp_path, run_id)
    for name in (
        "record.json",
        "contract.json",
        "state.json",
        "projection.json",
        "cartridge.json",
    ):
        (directory / name).write_bytes((generation / name).read_bytes())
    (directory / "current.json").unlink()
    shutil.rmtree(directory / "generations")

    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    durable = await reloaded.get_run(run_id)
    assert durable["run_id"] == run_id
    assert durable["state"]["status"] == "queued"


def test_generic_store_rejects_symlinked_storage_root(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (runs_dir / "lab-games").symlink_to(outside, target_is_directory=True)

    with pytest.raises(GenericLabRunError, match="symbolic links"):
        GenericLabRunService(runs_dir=runs_dir, authority=_gateway(_Solo()))


def test_generic_store_rejects_symlinked_storage_ancestor(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    with pytest.raises(GenericLabRunError, match="symbolic links"):
        GenericLabRunService(
            runs_dir=linked / "nested",
            authority=_gateway(_Solo()),
        )


@pytest.mark.asyncio
async def test_restart_rejects_symlinked_generation_artifact(tmp_path: Path) -> None:
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    created = await service.attach(_launch())
    state = _current_generation(tmp_path, created["run_id"]) / "state.json"
    outside = tmp_path / "outside-state.json"
    outside.write_bytes(state.read_bytes())
    state.unlink()
    state.symlink_to(outside)

    with pytest.raises(GenericLabRunError, match="saved run .* is unavailable"):
        GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))


@pytest.mark.asyncio
async def test_terminal_projection_fails_closed_when_all_sections_are_protected(
    tmp_path: Path,
) -> None:
    solo = _Solo()
    solo.result_value = {"prompt": "private"}
    solo.evaluation_value = {"navigation_memory": {"secret": "private"}}
    solo.state = "completed"
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())
    with pytest.raises(GenericLabRunError, match="evidence is invalid"):
        await service.get_run(created["run_id"])
    reloaded = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(_Solo()))
    assert (await reloaded.projection(created["run_id"]))["status"] == "queued"


@pytest.mark.asyncio
async def test_terminal_projection_renames_only_typed_legacy_public_telemetry(
    tmp_path: Path,
) -> None:
    solo = _Solo()
    solo.result_value = {
        "config": {"observation_profile": "hybrid-visible-v1"},
        "progress": {"observation_seq": 9},
        "result": {"outcome": "success"},
    }
    solo.evaluation_value = {
        "references": {"receipts": [{"observation_sequence": 9}]},
        "metrics": {"completion": 1},
    }
    solo.timeline_value = ({"kind": "decision", "observation_seq": 9, "sequence": 1},)
    solo.state = "completed"
    service = GenericLabRunService(runs_dir=tmp_path, authority=_gateway(solo))
    created = await service.attach(_launch())

    projection = await service.projection(created["run_id"])
    terminal = projection["snapshot"]["terminal"]
    assert terminal["result"]["config"] == {"sensor_profile": "hybrid-visible-v1"}
    assert terminal["result"]["progress"] == {"decision_sequence": 9}
    assert terminal["evaluation"]["references"]["receipts"] == [{"decision_sequence": 9}]
    assert projection["events"] == [{"decision_sequence": 9, "kind": "decision", "sequence": 1}]
    serialized = str(projection)
    assert "observation" not in serialized
    assert "prompt" not in serialized


def test_attached_game_run_rejects_wrong_participant_count_and_source_kind() -> None:
    with pytest.raises(GenericLabRunError, match="entrant count"):
        AttachedGameRun(
            **{
                **_launch().__dict__,
                "game_id": "checkpoint-race",
                "game_version": "duo-checkpoint-race-v0",
            }
        )
    with pytest.raises(GameAuthorityError, match="kind"):
        AttachedGameRun(
            **{
                **_launch().__dict__,
                "authority_kind": "unknown",
            }
        )
