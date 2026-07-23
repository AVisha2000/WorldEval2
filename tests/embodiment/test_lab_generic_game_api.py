from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient
from genesis_arena.embodiment.lab.game_authority import GameAuthorityGateway
from genesis_arena.embodiment.lab.game_runs import GenericLabRunService
from genesis_arena.embodiment.lab.publications import PublicReplayStore
from genesis_arena.embodiment.lab_api import public_router, router


@dataclass(frozen=True)
class _EmptyFrame:
    state: str = "loading"
    snapshot: None = None


class _SoloAuthority:
    def __init__(self) -> None:
        self.created: list[Mapping[str, object]] = []
        self.cancelled: list[str] = []
        self.state = "queued"

    async def create(self, **kwargs: object) -> Mapping[str, object]:
        self.created.append(dict(kwargs))
        return {"episode_id": f"ep_live_generic_{len(self.created)}", "state": "queued"}

    async def status(self, episode_id: str) -> Mapping[str, object]:
        return {"episode_id": episode_id, "failure": None, "state": self.state}

    async def result(self, episode_id: str) -> Mapping[str, object]:
        return {"result": {"completion": 1, "score": 1}}

    async def evaluation(self, episode_id: str) -> Mapping[str, object]:
        return {"metrics": {"completion": 1}}

    async def timeline(self, episode_id: str) -> tuple[Mapping[str, object], ...]:
        return ({"kind": "episode_completed", "sequence": 1},)

    async def replay(self, episode_id: str) -> object:
        raise RuntimeError("not ready")

    async def frame(self, episode_id: str) -> _EmptyFrame:
        return _EmptyFrame()

    async def cancel(self, episode_id: str) -> Mapping[str, object]:
        self.cancelled.append(episode_id)
        return {"episode_id": episode_id, "failure": None, "state": "cancelled"}


class _SeriesAuthority:
    def __init__(self, *, trio: bool = False) -> None:
        self.created: list[Mapping[str, object]] = []
        self.cancelled: list[str] = []
        self._trio = trio

    async def create(self, **kwargs: object) -> Mapping[str, object]:
        self.created.append(dict(kwargs))
        prefix = "trio" if self._trio else "series"
        return {"series_id": f"{prefix}_generic_{len(self.created)}", "state": "queued"}

    async def status(self, series_id: str) -> Mapping[str, object]:
        return {"failure": None, "series_id": series_id, "state": "queued"}

    async def result(self, series_id: str) -> Mapping[str, object]:
        raise RuntimeError("not ready")

    async def evaluation(self, series_id: str) -> Mapping[str, object]:
        raise RuntimeError("not ready")

    async def timeline(self, series_id: str) -> Mapping[str, object]:
        return {"events": [], "series_id": series_id}

    async def replay(self, series_id: str) -> object:
        raise RuntimeError("not ready")

    async def participant_frame(
        self, series_id: str, participant_id: str
    ) -> tuple[str, None]:
        return "loading", None

    async def cancel(self, series_id: str) -> Mapping[str, object]:
        self.cancelled.append(series_id)
        return {"failure": None, "series_id": series_id, "state": "cancelled"}


def _app(tmp_path: Path) -> tuple[FastAPI, _SoloAuthority, _SeriesAuthority, _SeriesAuthority]:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = FastAPI()
    solo = _SoloAuthority()
    paired = _SeriesAuthority()
    trio = _SeriesAuthority(trio=True)
    app.state.embodiment_episodes = solo
    app.state.embodiment_series = paired
    app.state.embodiment_trio_series = trio
    app.state.lab_game_runs = GenericLabRunService(
        runs_dir=tmp_path,
        authority=GameAuthorityGateway(solo=solo, paired=paired, trio=trio),
    )
    app.state.lab_public_replays = PublicReplayStore(runs_dir=tmp_path)
    app.include_router(router)
    app.include_router(public_router)
    return app, solo, paired, trio


def test_generic_game_api_launches_demo_and_live_without_persisting_key(
    tmp_path: Path,
) -> None:
    app, solo, _paired, _trio = _app(tmp_path)
    with TestClient(app) as client:
        demo = client.post(
            "/api/lab/runs/games/movement-maze",
            json={"mode": "demo", "seed": 11},
        )
        assert demo.status_code == 202
        assert demo.json()["contract"]["game_id"] == "movement-maze"
        assert demo.json()["contract"]["mode"] == "demo"
        assert solo.created[0]["provider"] == "demo"
        assert solo.created[0]["scenario_id"] == "movement-maze-v0"

        live = client.post(
            "/api/lab/runs/games/solo-construction",
            json={
                "api_key": "fixture-session-key",
                "mode": "live",
                "models": ["gpt-5.6-terra"],
                "provider": "openai",
                "seed": 12,
            },
        )
        assert live.status_code == 202
        body = live.json()
        assert body["contract"]["entrants"][0]["model_id"] == "gpt-5.6-terra"
        assert body["contract"]["configuration"]["runtime_profile_id"] == (
            "solo.construction.live"
        )
        assert solo.created[1]["api_key"] == "fixture-session-key"
        assert "fixture-session-key" not in live.text

    artifact_root = tmp_path / "lab-games"
    assert artifact_root.is_dir()
    for path in artifact_root.rglob("*.json"):
        assert "fixture-session-key" not in path.read_text(encoding="utf-8")


def test_generic_game_api_routes_paired_and_trio_demos_to_existing_services(
    tmp_path: Path,
) -> None:
    app, _solo, paired, trio = _app(tmp_path)
    with TestClient(app) as client:
        checkpoint = client.post(
            "/api/lab/runs/games/checkpoint-race",
            json={"mode": "demo", "seed": 3},
        )
        assert checkpoint.status_code == 202
        assert paired.created[0]["task_id"] == "duo-checkpoint-race-v0"
        assert paired.created[0]["entrants"][0]["provider"] == "demo"

        relay = client.post(
            "/api/lab/runs/games/trio-relay",
            json={"mode": "demo", "seed": 4},
        )
        assert relay.status_code == 202
        assert trio.created[0]["task_id"] == "trio-relay-v0"
        assert len(trio.created[0]["entrants"]) == 3


def test_generic_game_api_seals_then_verifies_completed_cartridge(
    tmp_path: Path,
) -> None:
    app, solo, _paired, _trio = _app(tmp_path)
    with TestClient(app) as client:
        launched = client.post(
            "/api/lab/runs/games/movement-maze",
            json={"mode": "demo", "seed": 9},
        )
        run_id = launched.json()["run_id"]
        premature = client.post(f"/api/lab/runs/{run_id}/seal")
        solo.state = "completed"
        completed = client.get(f"/api/lab/runs/{run_id}")
        sealed = client.post(f"/api/lab/runs/{run_id}/seal")
        verified = client.post(f"/api/lab/runs/{run_id}/verify")

    assert premature.status_code == 409
    assert completed.json()["state"]["status"] == "completed"
    assert sealed.status_code == 200
    assert sealed.json()["state"]["status"] == "sealed"
    assert verified.status_code == 200
    assert verified.json()["state"]["status"] == "verified"
    assert verified.headers["cache-control"] == "no-store"


def test_generic_game_api_explicitly_publishes_then_unpublishes_safe_replay(
    tmp_path: Path,
) -> None:
    app, solo, _paired, _trio = _app(tmp_path)
    with TestClient(app) as client:
        launched = client.post(
            "/api/lab/runs/games/movement-maze",
            json={"mode": "demo", "seed": 9},
        )
        run_id = launched.json()["run_id"]
        solo.state = "completed"
        completed = client.get(f"/api/lab/runs/{run_id}")
        published = client.post(f"/api/lab/runs/{run_id}/publish")
        slug = published.json()["publication_slug"]
        listed = client.get("/api/public/games/movement-maze/replays")
        replay = client.get(f"/api/public/games/movement-maze/replays/{slug}")
        wrong_game = client.get(f"/api/public/games/interaction/replays/{slug}")
        removed = client.delete(f"/api/lab/publications/{slug}")
        missing = client.get(f"/api/public/games/movement-maze/replays/{slug}")

    assert completed.json()["state"]["status"] == "completed"
    assert published.status_code == 201
    assert run_id not in published.text
    assert listed.status_code == 200
    assert listed.headers["x-robots-tag"] == "noindex, nofollow"
    assert [item["publication_slug"] for item in listed.json()["publications"]] == [slug]
    assert replay.status_code == 200
    assert "api_key" not in replay.text
    assert "authority_available" not in replay.text
    assert wrong_game.status_code == 404
    assert removed.status_code == 204
    assert missing.status_code == 404


def test_generic_game_api_fails_closed_for_credentials_and_unavailable_modes(
    tmp_path: Path,
) -> None:
    app, solo, paired, trio = _app(tmp_path)
    with TestClient(app) as client:
        demo_with_key = client.post(
            "/api/lab/runs/games/movement-maze",
            json={"api_key": "must-not-be-used", "mode": "demo"},
        )
        assert demo_with_key.status_code == 422
        assert "must-not-be-used" not in demo_with_key.text

        wrong_roster = client.post(
            "/api/lab/runs/games/mini-rts",
            json={
                "api_key": "must-not-be-used",
                "mode": "live",
                "models": ["only-one-model"],
                "provider": "openai",
            },
        )
        assert wrong_roster.status_code == 422
        assert "must-not-be-used" not in wrong_roster.text

        blocked = client.post(
            "/api/lab/runs/games/crossroads-conquest",
            json={"mode": "live", "models": ["a", "b", "c"]},
        )
        assert blocked.status_code == 422
        assert blocked.json() == {
            "detail": {"code": "invalid_lab_game_run_request"}
        }
        secret_model = "sk-proj-" + "a" * 24
        pasted_model_key = client.post(
            "/api/lab/runs/games/movement-maze",
            json={
                "api_key": "ephemeral-session-key",
                "mode": "live",
                "models": [secret_model],
                "provider": "openai",
            },
        )
        assert pasted_model_key.status_code == 422
        assert secret_model not in pasted_model_key.text
    assert not solo.created
    assert not paired.created
    assert not trio.created


def test_generic_game_clone_rejects_credential_shaped_model_before_persisting(
    tmp_path: Path,
) -> None:
    app, _solo, _paired, _trio = _app(tmp_path)
    secret_model = "sk-proj-" + "b" * 24
    with TestClient(app) as client:
        source = client.post(
            "/api/lab/runs/games/movement-maze",
            json={"mode": "demo", "seed": 4},
        ).json()
        clone = client.post(
            f"/api/lab/runs/{source['run_id']}/clone",
            json={
                "changes": {
                    "entrants": [
                        {
                            "display_name": "Agent",
                            "entrant_id": "entrant_0",
                            "model_id": secret_model,
                            "provider": "demo",
                        }
                    ]
                }
            },
        )

    assert clone.status_code == 422
    assert secret_model not in clone.text
    assert all(
        secret_model.encode("utf-8") not in path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    )


def test_generic_game_clone_launch_is_fresh_parent_linked_and_atomic(
    tmp_path: Path,
) -> None:
    app, solo, _paired, _trio = _app(tmp_path)
    with TestClient(app) as client:
        source = client.post(
            "/api/lab/runs/games/solo-construction",
            json={
                "api_key": "first-session-key",
                "mode": "live",
                "models": ["gpt-5.6-terra"],
                "provider": "openai",
                "seed": 22,
            },
        ).json()
        draft_response = client.post(
            f"/api/lab/runs/{source['run_id']}/clone",
            json={"changes": {"configuration": {}}},
        )
        assert draft_response.status_code == 201
        draft = draft_response.json()
        assert draft["state"]["status"] == "draft"
        assert (
            draft["contract"]["parent_contract_sha256"]
            == source["contract"]["contract_sha256"]
        )

        launched = client.post(
            f"/api/lab/runs/{draft['run_id']}/launch",
            json={"api_key": "second-session-key"},
        )
        assert launched.status_code == 202
        assert launched.json()["run_id"] == draft["run_id"]
        assert launched.json()["contract"]["contract_sha256"] == (
            draft["contract"]["contract_sha256"]
        )
        assert solo.created[-1]["api_key"] == "second-session-key"
        assert "second-session-key" not in launched.text

        stale = client.post(
            f"/api/lab/runs/{draft['run_id']}/launch",
            json={"api_key": "third-session-key"},
        )
        assert stale.status_code == 422
        assert "third-session-key" not in stale.text


def test_generic_demo_clone_relaunch_requires_no_credential(tmp_path: Path) -> None:
    app, _solo, paired, _trio = _app(tmp_path)
    with TestClient(app) as client:
        source = client.post(
            "/api/lab/runs/games/checkpoint-race",
            json={"mode": "demo", "seed": 8},
        ).json()
        draft = client.post(
            f"/api/lab/runs/{source['run_id']}/clone",
            json={"changes": {"configuration": {}}},
        ).json()
        launched = client.post(
            f"/api/lab/runs/{draft['run_id']}/launch",
            json={},
        )
    assert launched.status_code == 202
    assert launched.json()["contract"]["mode"] == "demo"
    assert len(paired.created) == 2
    assert paired.created[-1]["entrants"][0]["provider"] == "demo"


def test_runtime_manifest_is_path_independent_and_hash_bound(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/lab/runtime-manifest")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["manifest_sha256"]) == 64
    assert payload["bindings"]
    lowered = response.text.casefold()
    assert "/users/" not in lowered
    assert "\\users\\" not in lowered
    assert "api_key" not in lowered


def test_sandbox_manifest_and_custom_recipe_remain_non_authoritative(
    tmp_path: Path,
) -> None:
    app, *_ = _app(tmp_path)
    with TestClient(app) as client:
        manifest_response = client.get("/api/lab/sandbox")
        assert manifest_response.status_code == 200
        manifest = manifest_response.json()
        assert len(manifest["primitives"]) == 10
        assert manifest["recipes"][0]["executable"] is True

        draft = client.post(
            "/api/lab/sandbox/recipes/draft",
            json={
                "primitive_ids": ["orientation", "movement"],
                "recipe_id": "movement-practice-draft",
                "summary": "A small movement and orientation composition for review.",
                "title": "Movement practice",
            },
        )
        assert draft.status_code == 201
        assert draft.json()["executable"] is False
        assert draft.json()["authority_binding"] is None

        missing_dependency = client.post(
            "/api/lab/sandbox/recipes/draft",
            json={
                "primitive_ids": ["movement"],
                "recipe_id": "invalid-draft",
                "summary": "This omits the required orientation primitive.",
                "title": "Invalid",
            },
        )
        assert missing_dependency.status_code == 422
