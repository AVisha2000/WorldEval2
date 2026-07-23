from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Mapping

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from genesis_arena.embodiment.lab.benchmarks import (
    LABYRINTH_INTERACTIVE_RECIPE,
    BenchmarkStore,
    LabBenchmarkError,
    SeasonProgress,
)
from genesis_arena.embodiment.lab.contracts import (
    RaceCartridge,
    ReplayProjection,
    RunContract,
    RunEntrant,
    RunLifecycle,
    RunMode,
    RunState,
)
from genesis_arena.embodiment.lab.service import LabRunService
from genesis_arena.embodiment.lab_api import public_router, router
from genesis_arena.embodiment.protocol import canonical_sha256


def _entrants() -> tuple[RunEntrant, ...]:
    return (
        RunEntrant("entrant_0", "gpt-5.6-sol", provider="openai", display_name="Sol"),
        RunEntrant("entrant_1", "gpt-5.6-terra", provider="openai", display_name="Terra"),
        RunEntrant("entrant_2", "gpt-5.6-luna", provider="openai", display_name="Luna"),
    )


def _candidate(
    *,
    run_suffix: str = "001",
    lifecycle: RunLifecycle = RunLifecycle.VERIFIED,
    configuration: Mapping[str, object] | None = None,
    snapshot: Mapping[str, object] | None = None,
) -> dict[str, object]:
    recipe = LABYRINTH_INTERACTIVE_RECIPE.as_dict()
    contract = RunContract.create(
        run_id=f"run_labyrinth_benchmark_{run_suffix}",
        game_id=str(recipe["game_id"]),
        game_version=str(recipe["game_version"]),
        mode=RunMode.SEALED_BENCHMARK,
        entrants=_entrants(),
        seed_policy=recipe["seed_policy"],
        budget=recipe["budget"],
        configuration=recipe["configuration"] if configuration is None else configuration,
        runtime_version=str(recipe["runtime_version"]),
        scenario_id=str(recipe["scenario_id"]),
        map_id=str(recipe["map_id"]),
        map_sha256=str(recipe["map_sha256"]),
        skill_mode=str(recipe["skill_mode"]),
        prompt_sha256=str(recipe["prompt_sha256"]),
    )
    state = RunState.create(
        run_id=contract.run_id,
        contract_sha256=contract.contract_sha256,
        status=lifecycle,
        result_sha256=f"{int(run_suffix):064x}",
    )
    projection = ReplayProjection.create(
        run_id=contract.run_id,
        contract_sha256=contract.contract_sha256,
        status=lifecycle,
        sequence=0,
        snapshot={"result": "verified-safe"} if snapshot is None else snapshot,
    )
    cartridge = RaceCartridge.create(
        cartridge_id=f"cartridge_{contract.run_id}",
        contract=contract,
        state=state,
        public_projection=projection,
    )
    return {
        "run_id": contract.run_id,
        "contract": contract.as_dict(),
        "state": state.as_dict(),
        "cartridge": cartridge.as_dict(),
        "authority_available": False,
        "replay_available": True,
        "resume_supported": False,
        "video_available": False,
        "created_at_epoch_ms": 1,
    }


def _metrics(
    *, sol_completion: int = 9_000, terra_completion: int = 8_000, luna_completion: int = 7_000
) -> list[dict[str, object]]:
    def entry(entrant_id: str, completion: int, calls: int) -> dict[str, object]:
        return {
            "entrant_id": entrant_id,
            "metrics": {
                "budget_charged_calls": calls,
                "completion_basis_points": completion,
                "input_tokens": 1_000 + calls,
                "invalid_action_rate_basis_points": 100,
                "latency_ms": 200 + calls,
                "output_tokens": 300 + calls,
                "path_efficiency_basis_points": 8_000,
                "recovery_rate_basis_points": 200,
            },
        }

    return (
        entry("entrant_0", sol_completion, 100),
        entry("entrant_1", terra_completion, 120),
        entry("entrant_2", luna_completion, 140),
    )


def test_store_rejects_unsealed_or_incompatible_candidates(tmp_path: Path) -> None:
    store = BenchmarkStore(runs_dir=tmp_path)
    with pytest.raises(LabBenchmarkError, match="not a verified"):
        store.accept(
            recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
            candidate_run=_candidate(lifecycle=RunLifecycle.COMPLETED),
            participant_metrics=_metrics(),
        )

    configuration = dict(LABYRINTH_INTERACTIVE_RECIPE.as_dict()["configuration"])
    configuration["vision_range_cells"] = 8
    with pytest.raises(LabBenchmarkError, match="does not match"):
        store.accept(
            recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
            candidate_run=_candidate(run_suffix="002", configuration=configuration),
            participant_metrics=_metrics(),
        )
    assert store.results() == []


def test_store_accepts_only_safe_verified_aggregate_and_is_idempotent(tmp_path: Path) -> None:
    store = BenchmarkStore(runs_dir=tmp_path)
    candidate = _candidate()
    accepted = store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=candidate,
        participant_metrics=_metrics(),
    )
    repeated = store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=candidate,
        participant_metrics=_metrics(),
    )
    assert accepted == repeated
    assert len(store.results()) == 1
    assert accepted["authority_result_sha256"] == candidate["state"]["result_sha256"]
    rendered = (tmp_path / "lab-benchmarks" / "store.json").read_text(encoding="utf-8")
    for forbidden in (
        "api_key",
        "prompt",
        "raw_output",
        "navigation_memory",
        "scratchpad",
        "episode_id",
    ):
        assert forbidden not in rendered
    assert not list((tmp_path / "lab-benchmarks").glob("*.tmp"))


def test_http_admission_uses_only_authority_derived_cartridge_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(
        snapshot={
            "benchmark_metrics": list(_metrics()),
            "result": "verified-safe",
        }
    )
    run_id = str(candidate["run_id"])
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    service = LabRunService(runs_dir=tmp_path)

    async def get_run(requested_run_id: str) -> Mapping[str, object]:
        if requested_run_id != run_id:
            raise AssertionError("unexpected run")
        return candidate

    monkeypatch.setattr(service, "get_run", get_run)
    app = FastAPI()
    app.state.lab_runs = service
    app.state.lab_benchmarks = BenchmarkStore(runs_dir=tmp_path)
    app.include_router(router)
    app.include_router(public_router)

    with TestClient(app) as client:
        submitted = client.post(
            f"/api/lab/runs/{run_id}/benchmark",
            json={"recipe_id": LABYRINTH_INTERACTIVE_RECIPE.recipe_id},
        )
        repeated = client.post(
            f"/api/lab/runs/{run_id}/benchmark",
            json={"recipe_id": LABYRINTH_INTERACTIVE_RECIPE.recipe_id},
        )
        injection = client.post(
            f"/api/lab/runs/{run_id}/benchmark",
            json={
                "recipe_id": LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
                "participant_metrics": _metrics(sol_completion=10_000),
            },
        )
        public = client.get("/api/public/games/labyrinth-run/benchmark")
        private = client.get("/api/lab/benchmarks/labyrinth-run")
        profiles = client.get("/api/lab/benchmarks/model-profiles")

    assert submitted.status_code == 201
    assert repeated.status_code == 201
    assert submitted.json() == repeated.json()
    assert injection.status_code == 422
    assert public.status_code == 200
    assert public.json()["season_state"] == "results_available"
    assert public.json()["leaderboards"][0]["rows"][0]["model_id"] == "gpt-5.6-sol"
    assert private.json() == public.json()
    assert len(profiles.json()["profiles"]) == 3
    assert "universal_score" in profiles.text
    assert "run_id" not in public.text


def test_store_rejects_secret_or_raw_candidate_material_before_persisting(tmp_path: Path) -> None:
    store = BenchmarkStore(runs_dir=tmp_path)
    candidate = _candidate()
    candidate["api_key"] = "sk-proj-not-a-real-secret-but-still-forbidden-123456789"
    with pytest.raises(LabBenchmarkError):
        store.accept(
            recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
            candidate_run=candidate,
            participant_metrics=_metrics(),
        )
    assert store.results() == []
    assert not (tmp_path / "lab-benchmarks" / "store.json").exists()


def test_store_persists_atomically_and_reloads_verified_evidence(tmp_path: Path) -> None:
    store = BenchmarkStore(runs_dir=tmp_path)
    store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=_candidate(run_suffix="003"),
        participant_metrics=_metrics(),
    )
    progress = SeasonProgress(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        planned_runs=840,
        completed_runs=1,
        verified_results=1,
        api_calls=300,
        input_tokens=3_000,
        output_tokens=900,
        estimated_cost_microunits=123_456,
        elapsed_ms=2_000,
    )
    store.set_progress(progress)
    reloaded = BenchmarkStore(runs_dir=tmp_path)
    assert reloaded.results() == store.results()
    status = reloaded.game_status("labyrinth-run")
    assert status["season_state"] == "results_available"
    assert status["recipes"][0]["season_progress"] == progress.as_dict()
    assert (tmp_path / "lab-benchmarks" / "store.json").stat().st_mode & 0o077 == 0


def test_reload_rejects_a_tampered_aggregate_that_no_longer_matches_its_recipe(
    tmp_path: Path,
) -> None:
    store = BenchmarkStore(runs_dir=tmp_path)
    store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=_candidate(run_suffix="007"),
        participant_metrics=_metrics(),
    )
    store_path = tmp_path / "lab-benchmarks" / "store.json"
    persisted = json.loads(store_path.read_text(encoding="utf-8"))
    persisted["results"][0]["participants"][0]["provider"] = "anthropic"
    result_body = {
        key: value
        for key, value in persisted["results"][0].items()
        if key != "verified_result_sha256"
    }
    persisted["results"][0]["verified_result_sha256"] = canonical_sha256(result_body)
    store_path.write_text(json.dumps(persisted), encoding="utf-8")

    with pytest.raises(LabBenchmarkError, match="does not match the recipe"):
        BenchmarkStore(runs_dir=tmp_path)


def test_leaderboards_are_recipe_comparable_and_profiles_have_no_universal_score(
    tmp_path: Path,
) -> None:
    store = BenchmarkStore(runs_dir=tmp_path)
    store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=_candidate(run_suffix="004"),
        participant_metrics=_metrics(sol_completion=8_000, terra_completion=9_000),
    )
    store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=_candidate(run_suffix="005"),
        participant_metrics=_metrics(sol_completion=8_500, terra_completion=9_200),
    )
    store.accept(
        recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id,
        candidate_run=_candidate(run_suffix="006"),
        participant_metrics=_metrics(sol_completion=8_300, terra_completion=9_100),
    )
    leaderboard = store.leaderboard(recipe_id=LABYRINTH_INTERACTIVE_RECIPE.recipe_id)
    assert leaderboard["comparison_scope"] == "same_recipe_only"
    assert leaderboard["rows"][0]["model_id"] == "gpt-5.6-terra"
    assert leaderboard["rows"][0]["sample_count"] == 3
    assert leaderboard["rows"][0]["uncertainty"]["available"] is True
    profiles = store.model_profiles()
    assert profiles and all(profile["universal_score"] == "not_available" for profile in profiles)
    assert all(
        game["comparison_scope"] == "same_recipe_only"
        for profile in profiles
        for game in profile["games"]
    )
    unavailable = store.game_status("central-relay")
    assert unavailable["season_state"] == "not_available"
    assert unavailable["leaderboards"] == []
