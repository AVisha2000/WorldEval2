import asyncio
import hashlib
import struct
import time
import zlib
from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from genesis_arena.embodiment.api import router
from genesis_arena.embodiment.artifacts import (
    PROTECTED_LAYER,
    PUBLIC_LAYER,
    EpisodeArtifact,
    EpisodeArtifactBundle,
    EpisodeBundles,
)
from genesis_arena.embodiment.demo_scenarios import demo_scenario
from genesis_arena.embodiment.duel.evidence import DuelSeriesEvidenceBundle
from genesis_arena.embodiment.duel.participant_frames import DuelParticipantFrameStore
from genesis_arena.embodiment.duel.service import DuelSeriesService
from genesis_arena.embodiment.episode_service import EpisodeService, demo_fixture_bytes
from genesis_arena.embodiment.live_solo import LiveSoloOutcome
from genesis_arena.embodiment.scripted_solo_demo import (
    SCRIPTED_SOLO_MODELS,
    scripted_demo_model,
)
from PIL import Image


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _participant_png(metadata: bytes = b"") -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1280, 720, 8, 6, 0, 0, 0)
    scanlines = b"".join(b"\x00" + b"\x00" * (1280 * 4) for _ in range(720))
    chunks = [_chunk(b"IHDR", ihdr)]
    if metadata:
        chunks.append(_chunk(b"tEXt", b"private\x00" + metadata))
    chunks.extend((_chunk(b"IDAT", zlib.compress(scanlines)), _chunk(b"IEND", b"")))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _participant_jpeg(metadata: bytes = b"") -> bytes:
    def segment(marker: int, data: bytes) -> bytes:
        return b"\xff" + bytes((marker,)) + (len(data) + 2).to_bytes(2, "big") + data

    image = Image.new("RGB", (1280, 720), (29, 83, 137))
    encoded = BytesIO()
    image.save(encoded, format="JPEG", quality=82, subsampling="4:2:0")
    value = encoded.getvalue()
    app = segment(0xE1, metadata) if metadata else b""
    return value[:2] + app + value[2:]


async def _executor(spec, credential, cancel_event, publish_frame, publish_progress):
    if spec.provider in ("scripted", "demo"):
        assert credential is None
    else:
        assert credential is not None
        assert credential.reveal().startswith("sk-")
    await publish_frame(
        "participant_0", 0, _participant_png(b"prompt-and-hidden-state-must-not-leak")
    )
    await publish_progress(1, 1)
    await asyncio.sleep(0)
    unsupported = {"reason": "provider_telemetry_not_recorded", "status": "unsupported"}
    evaluation = {
        "schema_version": "llm-controller/evaluation/1.0.0",
        "scope": "solo",
        "metrics": {
            "task_success": {"status": "supported", "value": True},
            "completion_tick": {"status": "supported", "value": 1},
            "progress_checkpoints_reached": {
                "status": "supported", "value": {"count": 0, "event_kinds": []}
            },
            "valid_action_rate": {
                "status": "supported",
                "value": {"basis_points": 10_000, "denominator": 1, "numerator": 1},
            },
            "controller_changes": {"status": "supported", "value": 0},
            "total_held_ticks": {"status": "supported", "value": 1},
            "path_efficiency": {
                "reason": "shortest_legal_route_not_recorded", "status": "unsupported"
            },
            "unnecessary_collisions": {"status": "supported", "value": 0},
            "interaction_alignment_failures": {"status": "supported", "value": 0},
            "damage_taken": {"status": "supported", "value": 0},
            "recovery_quality": {
                "reason": "normative_recovery_baseline_not_recorded", "status": "unsupported"
            },
            "repeated_ineffective_windows": {
                "status": "supported",
                "value": {"longest_run": 0, "windows_in_repeated_runs": 0},
            },
            "memory_consistency": {
                "reason": "runner_memory_not_in_authority_replay", "status": "unsupported"
            },
            "provider_token_efficiency": unsupported,
            "provider_latency_efficiency": unsupported,
            "deterministic_replay_verification": {"status": "supported", "value": True},
        },
    }
    if spec.scenario_id is not None:
        scenario = demo_scenario(spec.scenario_id)
        evaluation.update(
            {
                "scenario_id": scenario.scenario_id,
                "evaluation_profile_id": scenario.evaluation_profile_id,
            }
        )
    terminal = {"ended": True, "outcome": "success", "reason": "goal_reached"}
    public = EpisodeArtifactBundle.create(
        PUBLIC_LAYER,
        (
            EpisodeArtifact.json("evaluation", evaluation),
            EpisodeArtifact.json("public_events", []),
            EpisodeArtifact.json("receipts", []),
            EpisodeArtifact.json(
                "replay_summary",
                {
                    "episode_id": spec.episode_id,
                    "final_state_hash": "a" * 64,
                    "frozen_configuration": {
                        "config_sha256": "a" * 64,
                        "model_sha256": "a" * 64,
                        "protocol_package_sha256": "a" * 64,
                        "provider_sha256": "a" * 64,
                        "settings_sha256": "a" * 64,
                    },
                    "terminal": terminal,
                },
            ),
        ),
    )
    protected = EpisodeArtifactBundle.create(
        PROTECTED_LAYER, (EpisodeArtifact.json("observations", []),)
    )
    return LiveSoloOutcome(
        spec.episode_id,
        terminal,
        "a" * 64,
        1,
        0,
        EpisodeBundles(public, protected),
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.state.embodiment_episodes = EpisodeService(_executor)

    async def duel_executor(spec, credentials, cancel_event):
        del spec, credentials, cancel_event
        await asyncio.Event().wait()

    app.state.embodiment_series = DuelSeriesService(duel_executor)
    app.include_router(router)
    return app


def _payload(secret: str = "sk-api-never-echo") -> dict:
    return {
        "api_key": secret,
        "model": "test-model",
        "provider": "openai",
        "seed": 7,
        "task_id": "orientation-v0",
    }


def _scripted_demo_payload(task_id: str = "construction-v0") -> dict:
    return {
        "model": scripted_demo_model(task_id),
        "provider": "scripted",
        "seed": 7,
        "task_id": task_id,
        # This represents an older dashboard; the backend must reserve the full demo budget.
        "maximum_episode_ticks": 600,
    }


def _demo_payload(task_id: str = "construction-v0") -> dict:
    return {**_scripted_demo_payload(task_id), "provider": "demo"}


def _public_series_bundle(series_id: str) -> DuelSeriesEvidenceBundle:
    legs = tuple(
        EpisodeArtifactBundle.create(
            PUBLIC_LAYER,
            (EpisodeArtifact.json("evaluation", {"leg_index": index}),),
        )
        for index in (0, 1)
    )
    return DuelSeriesEvidenceBundle.create(
        layer=PUBLIC_LAYER,
        series_id=series_id,
        plan_sha256="a" * 64,
        fairness_lock_sha256="b" * 64,
        legs=(legs[0], legs[1]),
    )


def test_episode_api_lifecycle_and_replay_never_echo_key() -> None:
    secret = "sk-api-never-echo"
    with TestClient(_app()) as client:
        created_response = client.post("/api/embodiment/episodes", json=_payload(secret))
        assert created_response.status_code == 202
        assert created_response.headers["cache-control"] == "no-store"
        assert secret not in created_response.text
        episode_id = created_response.json()["episode_id"]

        for _ in range(20):
            status = client.get(f"/api/embodiment/episodes/{episode_id}")
            if status.json()["state"] == "completed":
                break
        assert status.json()["state"] == "completed"
        assert status.json()["progress"] == {"authority_tick": 1, "observation_seq": 1}
        timeline = client.get(f"/api/embodiment/episodes/{episode_id}/timeline")
        result = client.get(f"/api/embodiment/episodes/{episode_id}/result")
        replay = client.get(f"/api/embodiment/episodes/{episode_id}/replay")
        evaluation = client.get(f"/api/embodiment/episodes/{episode_id}/evaluation")
        frame = client.get(f"/api/embodiment/episodes/{episode_id}/frame")
        assert timeline.status_code == result.status_code == replay.status_code == 200
        assert evaluation.status_code == 200
        assert evaluation.json()["state"] == "supported"
        assert evaluation.json()["projection_sha256"]
        assert set(evaluation.json()) == {
            "evaluation", "projection_sha256", "references", "result", "run",
            "schema_version", "scope", "state",
        }
        assert frame.status_code == 200
        assert frame.headers["content-type"] == "image/png"
        assert frame.headers["cache-control"] == "no-store"
        assert frame.headers["x-frame-state"] == "finished"
        assert frame.headers["x-observation-seq"] == "0"
        assert frame.headers["x-content-sha256"]
        assert b"prompt-and-hidden-state-must-not-leak" not in frame.content
        assert b"tEXt" not in frame.content
        assert replay.headers["x-content-sha256"]
        for response in (status, timeline, result, replay, evaluation, frame):
            assert secret not in response.text


def test_active_episode_evaluation_is_a_typed_not_ready_conflict() -> None:
    async def waiting_executor(spec, credential, cancel_event, publish_frame, publish_progress):
        del spec, credential, publish_frame, publish_progress
        await cancel_event.wait()
        raise asyncio.CancelledError

    app = _app()
    app.state.embodiment_episodes = EpisodeService(waiting_executor)
    with TestClient(app) as client:
        created = client.post("/api/embodiment/episodes", json=_payload())
        episode_id = created.json()["episode_id"]
        response = client.get(f"/api/embodiment/episodes/{episode_id}/evaluation")
        client.post(f"/api/embodiment/episodes/{episode_id}/cancel")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "embodiment_evaluation_not_ready"}}


def test_saved_replay_evaluation_route_returns_only_persisted_projection() -> None:
    projection = {
        "schema_version": "llm-controller/evaluation-projection/1.0.0",
        "projection_sha256": "b" * 64,
        "scope": "solo",
        "state": "supported",
        "run": {"episode_id": "ep_saved_evaluation"},
    }

    class Archive:
        def get(self, replay_id):
            return object() if replay_id == "ep_saved_evaluation" else None

        def evaluation(self, replay_id):
            return projection if replay_id == "ep_saved_evaluation" else None

    app = _app()
    app.state.embodiment_episodes = EpisodeService(_executor, replay_archive=Archive())
    with TestClient(app) as client:
        response = client.get(
            "/api/embodiment/replays/ep_saved_evaluation/evaluation"
        )
        missing = client.get("/api/embodiment/replays/ep_missing/evaluation")
    assert response.status_code == 200
    assert response.json() == projection
    assert missing.status_code == 404


def test_episode_frame_is_loading_without_player_pixels() -> None:
    async def waiting_executor(spec, credential, cancel_event, publish_frame, publish_progress):
        del spec, credential, publish_frame, publish_progress
        await cancel_event.wait()
        raise asyncio.CancelledError

    app = _app()
    app.state.embodiment_episodes = EpisodeService(waiting_executor)
    with TestClient(app) as client:
        created = client.post("/api/embodiment/episodes", json=_payload())
        episode_id = created.json()["episode_id"]
        frame = client.get(f"/api/embodiment/episodes/{episode_id}/frame")
        assert frame.status_code == 204
        assert frame.content == b""
        assert frame.headers["cache-control"] == "no-store"
        assert frame.headers["x-frame-state"] == "loading"
        assert frame.headers["x-content-type-options"] == "nosniff"
        client.post(f"/api/embodiment/episodes/{episode_id}/cancel")


def test_live_preview_websocket_is_isolated_from_canonical_frame_fallback() -> None:
    private_marker = b"prompt-raw-output-hidden-state-must-not-reach-browser"
    service: EpisodeService

    async def live_preview_executor(
        spec, credential, cancel_event, publish_frame, publish_progress
    ):
        del credential, publish_frame, publish_progress
        accepted = await service.publish_live_preview(
            spec.episode_id, "participant_0", 3, _participant_jpeg(private_marker)
        )
        assert accepted
        await cancel_event.wait()
        raise asyncio.CancelledError

    app = _app()
    service = EpisodeService(live_preview_executor)
    app.state.embodiment_episodes = service
    with TestClient(app) as client:
        created = client.post("/api/embodiment/episodes", json=_payload())
        episode_id = created.json()["episode_id"]
        with client.websocket_connect(
            f"/api/embodiment/episodes/{episode_id}/preview-live"
        ) as websocket:
            pixels = websocket.receive_bytes()
        canonical_frame = client.get(f"/api/embodiment/episodes/{episode_id}/frame")
        client.post(f"/api/embodiment/episodes/{episode_id}/cancel")

    assert pixels.startswith(b"\xff\xd8") and pixels.endswith(b"\xff\xd9")
    assert private_marker not in pixels
    assert b"\xff\xe1" not in pixels
    # Direct ingress pixels must never replace the canonical snapshot/final-frame fallback.
    assert canonical_frame.status_code == 204
    assert canonical_frame.headers["x-frame-state"] == "loading"


def test_invalid_create_payload_cannot_reflect_credentials() -> None:
    secret = "sk-malformed-must-not-return"
    payload = _payload(secret)
    payload["unexpected"] = {"authorization": secret}
    with TestClient(_app()) as client:
        response = client.post("/api/embodiment/episodes", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_embodiment_episode_request"}}
    assert secret not in response.text


def test_scripted_construction_demo_requires_no_key_and_never_stores_one() -> None:
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/episodes", json=_scripted_demo_payload())
        assert created.status_code == 202
        assert created.headers["cache-control"] == "no-store"
        body = created.json()
        assert body["config"] == {
            "certification_eligible": False,
            "episode_id": body["episode_id"],
            "maximum_episode_ticks": 600,
            "model": "construction-demo-v1",
            "observation_profile": "hybrid-visible-v1",
            "provider": "scripted",
            "protocol_version": "llm-controller/0.1.0",
            "run_class": "scripted",
            "seed": 7,
            "task_id": "construction-v0",
        }
        # The injected executor completes immediately, but it asserted that it received no
        # SessionCredential.  The service's credential store therefore has no scripted entry.
        assert len(client.app.state.embodiment_episodes._credentials) == 0

    with TestClient(_app()) as client:
        response = client.post(
            "/api/embodiment/episodes",
            json={**_scripted_demo_payload(), "api_key": "sk-must-not-be-accepted"},
        )
        assert response.status_code == 422
        assert "sk-must-not-be-accepted" not in response.text


def test_all_scripted_solo_demos_require_no_key_and_keep_their_task_budget() -> None:
    with TestClient(_app()) as client:
        for task_id, model in SCRIPTED_SOLO_MODELS.items():
            created = client.post("/api/embodiment/episodes", json=_scripted_demo_payload(task_id))
            assert created.status_code == 202
            config = created.json()["config"]
            assert config["provider"] == "scripted"
            assert config["model"] == model
            assert config["task_id"] == task_id
            assert config["maximum_episode_ticks"] == 600
        assert len(client.app.state.embodiment_episodes._credentials) == 0


def test_demo_provider_runs_every_solo_stage_without_key_and_freezes_policy_lock() -> None:
    locks = {}
    with TestClient(_app()) as client:
        for task_id, model in SCRIPTED_SOLO_MODELS.items():
            created = client.post("/api/embodiment/episodes", json=_demo_payload(task_id))
            assert created.status_code == 202
            body = created.json()
            assert body["run_class"] == "demo"
            assert body["certification_eligible"] is False
            config = body["config"]
            assert config["provider"] == "demo"
            assert config["model"] == model
            assert config["task_id"] == task_id
            assert config["run_class"] == "demo"
            assert config["certification_eligible"] is False
            assert config["maximum_episode_ticks"] == 600
            lock = config["demo_policy_lock"]
            assert lock == {
                "fixture_sha256": lock["fixture_sha256"],
                "model": model,
                "participant_id": "participant_0",
                "policy_id": model,
                "scenario_id": task_id,
                "seed": 7,
                "total_decision_budget": config["maximum_episode_ticks"],
            }
            assert len(lock["fixture_sha256"]) == 64
            assert lock["fixture_sha256"] == hashlib.sha256(
                demo_fixture_bytes(model=model, task_id=task_id)
            ).hexdigest()
            assert len(config["demo_policy_lock_sha256"]) == 64
            locks[task_id] = (
                lock,
                config["demo_policy_lock_sha256"],
            )
        assert len(client.app.state.embodiment_episodes._credentials) == 0

    with TestClient(_app()) as client:
        for task_id in SCRIPTED_SOLO_MODELS:
            repeated = client.post(
                "/api/embodiment/episodes", json=_demo_payload(task_id)
            ).json()["config"]
            assert (repeated["demo_policy_lock"], repeated["demo_policy_lock_sha256"]) == locks[
                task_id
            ]


def test_demo_control_games_select_protocol_v2_without_credentials() -> None:
    cases = (
        ("movement-maze-v0", "movement-maze-demo-v1", 200),
        ("operator-action-course-v0", "operator-action-course-demo-v1", 300),
    )
    with TestClient(_app()) as client:
        for task_id, model, tick_budget in cases:
            response = client.post(
                "/api/embodiment/episodes",
                json={
                    "provider": "demo",
                    "model": model,
                    "task_id": task_id,
                    "scenario_id": task_id,
                    "seed": 7,
                    "observation_profile": "hybrid-visible-v1",
                },
            )
            assert response.status_code == 202
            config = response.json()["config"]
            assert config["protocol_version"] == "llm-controller/0.2.0"
            assert config["maximum_episode_ticks"] == tick_budget
            assert config["demo_policy_lock"]["policy_id"] in {
                "movement-maze-visible-v1",
                "operator-action-visible-v1",
            }
        assert len(client.app.state.embodiment_episodes._credentials) == 0


def test_fake_live_provider_selects_control_game_v2_and_keeps_legacy_v1() -> None:
    # ``_app`` uses the local injected executor above: it consumes the in-memory credential but
    # never constructs a network provider, so this covers live (non-Demo) protocol dispatch
    # without making an external request.
    with TestClient(_app()) as client:
        for task_id in ("movement-maze-v0", "operator-action-course-v0"):
            response = client.post(
                "/api/embodiment/episodes",
                json={**_payload(), "task_id": task_id},
            )
            assert response.status_code == 202
            config = response.json()["config"]
            assert config["provider"] == "openai"
            assert config["run_class"] == "live"
            assert config["protocol_version"] == "llm-controller/0.2.0"

        legacy = client.post(
            "/api/embodiment/episodes",
            json={**_payload(), "task_id": "orientation-v0"},
        )
        assert legacy.status_code == 202
        assert legacy.json()["config"]["protocol_version"] == "llm-controller/0.1.0"


def test_demo_provider_rejects_keys_and_invalid_model_task_combinations() -> None:
    invalid_payloads = (
        {**_demo_payload(), "api_key": "must-not-be-consumed"},
        {**_demo_payload(), "model": "balanced-v1"},
        {**_demo_payload(), "task_id": "not-a-solo-stage-v0"},
        {**_demo_payload("orientation-v0"), "model": "construction-demo-v1"},
    )
    with TestClient(_app()) as client:
        for payload in invalid_payloads:
            response = client.post("/api/embodiment/episodes", json=payload)
            assert response.status_code == 422
            assert response.json() == {
                "detail": {"code": "invalid_embodiment_episode_request"}
            }
            assert "must-not-be-consumed" not in response.text
        assert len(client.app.state.embodiment_episodes._credentials) == 0


def test_multi_action_scenario_keeps_construction_authority_and_catalog_horizon() -> None:
    payload = {
        "provider": "demo",
        "model": "construction-demo-v1",
        "scenario_id": "multi-action-demo-v0",
        "task_id": "construction-v0",
        "seed": 19,
        # Caller-controlled horizons cannot silently redefine the catalog scenario.
        "maximum_episode_ticks": 23,
    }
    with TestClient(_app()) as client:
        response = client.post("/api/embodiment/episodes", json=payload)
    assert response.status_code == 202
    config = response.json()["config"]
    assert config["task_id"] == "construction-v0"
    assert config["scenario_id"] == "multi-action-demo-v0"
    assert config["model"] == "construction-demo-v1"
    assert config["maximum_episode_ticks"] == 1_300
    assert config["evaluation_profile_id"] == "solo-multi-action-showcase-v1"
    assert config["demo_policy_lock"]["scenario_id"] == "multi-action-demo-v0"
    assert config["demo_policy_lock"]["policy_id"] == "multi-action-construction-demo-v1"
    assert config["demo_policy_lock"]["total_decision_budget"] == 1_300


def test_demo_api_rejects_every_scenario_task_model_identity_mismatch() -> None:
    base = {
        "provider": "demo",
        "model": "construction-demo-v1",
        "scenario_id": "multi-action-demo-v0",
        "task_id": "construction-v0",
        "seed": 19,
    }
    invalid = (
        {**base, "task_id": "orientation-v0"},
        {**base, "model": "orientation-demo-v1"},
        {**base, "scenario_id": "construction-v0", "model": "orientation-demo-v1"},
        {**base, "scenario_id": "not-a-scenario-v0"},
        {**base, "scenario_id": 7},
        {**_payload(), "scenario_id": "orientation-v0"},
        {**_scripted_demo_payload(), "scenario_id": "construction-v0"},
    )
    with TestClient(_app()) as client:
        for payload in invalid:
            response = client.post("/api/embodiment/episodes", json=payload)
            assert response.status_code == 422
            assert response.json() == {
                "detail": {"code": "invalid_embodiment_episode_request"}
            }


def test_ordinary_construction_scenario_does_not_inherit_showcase_identity() -> None:
    payload = {
        **_demo_payload("construction-v0"),
        "scenario_id": "construction-v0",
        "maximum_episode_ticks": 18_000,
    }
    with TestClient(_app()) as client:
        response = client.post("/api/embodiment/episodes", json=payload)
    assert response.status_code == 202
    config = response.json()["config"]
    assert config["scenario_id"] == "construction-v0"
    assert config["maximum_episode_ticks"] == 600
    assert config["evaluation_profile_id"] == "solo-construction-v1"
    assert config["demo_policy_lock"]["policy_id"] == "construction-demo-v1"


def test_demo_fixture_lock_changes_with_bound_policy_source() -> None:
    first = demo_fixture_bytes(
        model="orientation-demo-v1",
        task_id="orientation-v0",
        policy_source_sha256="a" * 64,
    )
    second = demo_fixture_bytes(
        model="orientation-demo-v1",
        task_id="orientation-v0",
        policy_source_sha256="b" * 64,
    )
    assert hashlib.sha256(first).hexdigest() != hashlib.sha256(second).hexdigest()


def test_completed_demo_episode_is_eligible_for_native_replay_archive() -> None:
    class RecordingArchive:
        def __init__(self) -> None:
            self.specs = []

        async def save(self, spec, bundles, *, evaluation):
            assert bundles.public.layer == PUBLIC_LAYER
            assert bundles.protected.layer == PROTECTED_LAYER
            assert evaluation.state == "supported"
            self.specs.append(spec)
            return SimpleNamespace(replay_id=spec.episode_id)

    archive = RecordingArchive()
    app = _app()
    app.state.embodiment_episodes = EpisodeService(_executor, replay_archive=archive)
    with TestClient(app) as client:
        created = client.post("/api/embodiment/episodes", json=_demo_payload("orientation-v0"))
        episode_id = created.json()["episode_id"]
        for _ in range(30):
            status = client.get(f"/api/embodiment/episodes/{episode_id}").json()
            if status.get("replay", {}).get("state") == "ready":
                break
            time.sleep(0.01)

    assert status["replay"] == {"state": "ready", "replay_id": episode_id}
    assert len(archive.specs) == 1
    assert archive.specs[0].provider == "demo"
    assert archive.specs[0].run_class == "demo"
    assert archive.specs[0].public_dict()["certification_eligible"] is False


def test_scripted_solo_demo_cannot_select_a_live_or_scored_route() -> None:
    invalid_payloads = (
        {**_scripted_demo_payload(), "task_id": "orientation-v0"},
        {**_scripted_demo_payload(), "model": "balanced-v1"},
        {
            "provider": "scripted",
            "model": "orientation-demo-v1",
            "task_id": "not-a-solo-stage-v0",
            "seed": 7,
        },
    )
    with TestClient(_app()) as client:
        for payload in invalid_payloads:
            response = client.post("/api/embodiment/episodes", json=payload)
            assert response.status_code == 422
            assert response.json() == {"detail": {"code": "invalid_embodiment_episode_request"}}


def test_series_api_never_echoes_either_key() -> None:
    keys = ("sk-series-alpha", "sk-series-bravo")
    payload = {
        "entrants": [
            {"provider": "openai", "model": "model-a", "api_key": keys[0]},
            {"provider": "anthropic", "model": "model-b", "api_key": keys[1]},
        ],
        "seed": 19,
    }
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/series", json=payload)
        assert created.status_code == 202
        assert created.headers["cache-control"] == "no-store"
        series_id = created.json()["series_id"]
        status = client.get(f"/api/embodiment/series/{series_id}")
        cancelled = client.post(f"/api/embodiment/series/{series_id}/cancel")
        assert status.status_code == cancelled.status_code == 200
        assert status.headers["cache-control"] == "no-store"
        assert cancelled.json()["state"] == "cancelled"
        for response in (created, status, cancelled):
            assert all(key not in response.text for key in keys)


def test_series_api_accepts_credential_free_scripted_opponent() -> None:
    secret = "sk-model-versus-scripted"
    payload = {
        "entrants": [
            {"provider": "openai", "model": "model-a", "api_key": secret},
            {"provider": "scripted", "model": "balanced-v1"},
        ],
        "seed": 21,
        "max_live_provider_calls": 360,
    }
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/series", json=payload)
        assert created.status_code == 202
        series_id = created.json()["series_id"]
        assert created.json()["config"]["entrants"][1] == {
            "entrant_id": "entrant_1",
            "provider": "scripted",
            "model": "balanced-v1",
        }
        assert created.json()["config"]["max_live_provider_calls"] == 360
        assert secret not in created.text
        client.post(f"/api/embodiment/series/{series_id}/cancel")


def test_series_api_accepts_exactly_two_keyless_demo_entrants() -> None:
    payload = {
        "entrants": [
            {"provider": "demo", "model": "duelist-alpha-v1"},
            {"provider": "demo", "model": "duelist-bravo-v1"},
        ],
        "seed": 23,
        "max_live_provider_calls": 180,
    }
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/series", json=payload)
        assert created.status_code == 202
        assert created.headers["cache-control"] == "no-store"
        value = created.json()
        assert value["config"]["certification"] == {
            "eligible": False,
            "reason": "demo_provider",
        }
        assert [entrant["provider"] for entrant in value["config"]["entrants"]] == [
            "demo",
            "demo",
        ]
        assert "api_key" not in created.text
        client.post(f"/api/embodiment/series/{value['series_id']}/cancel")


def test_series_api_accepts_resource_relay_demo_pair_without_credentials() -> None:
    payload = {
        "entrants": [
            {"provider": "demo", "model": "resource-relay-alpha-v1"},
            {"provider": "demo", "model": "resource-relay-bravo-v1"},
        ],
        "seed": 29,
        "task_id": "duo-resource-relay-v0",
    }
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/series", json=payload)
        assert created.status_code == 202
        value = created.json()
        assert value["task_id"] == "duo-resource-relay-v0"
        assert value["config"]["task_id"] == "duo-resource-relay-v0"
        assert [entrant["model"] for entrant in value["config"]["entrants"]] == [
            "resource-relay-alpha-v1",
            "resource-relay-bravo-v1",
        ]
        assert "api_key" not in created.text
        client.post(f"/api/embodiment/series/{value['series_id']}/cancel")


def test_series_api_rejects_mixed_or_credentialed_demo_entrants() -> None:
    invalid = (
        [
            {"provider": "demo", "model": "duelist-alpha-v1"},
            {"provider": "openai", "model": "model-a", "api_key": "key"},
        ],
        [
            {"provider": "demo", "model": "duelist-alpha-v1", "api_key": "forbidden"},
            {"provider": "demo", "model": "duelist-bravo-v1"},
        ],
    )
    with TestClient(_app()) as client:
        for entrants in invalid:
            response = client.post(
                "/api/embodiment/series", json={"entrants": entrants, "seed": 1}
            )
            assert response.status_code == 422
            assert response.json() == {
                "detail": {"code": "invalid_embodiment_series_request"}
            }


def test_series_timeline_and_evaluation_routes_are_typed_and_privacy_safe() -> None:
    app = _app()
    service = app.state.embodiment_series

    async def timeline(series_id):
        return {
            "series_id": series_id,
            "legs": [
                {
                    "episode_id": "ep_demo_a",
                    "leg_index": 0,
                    "events": [],
                    "receipts": [],
                }
            ],
        }

    async def evaluation(series_id):
        return {
            "series_id": series_id,
            "certification": {"eligible": False, "reason": "demo_provider"},
            "legs": [
                {
                    "schema_version": "llm-controller/evaluation-projection/1.0.0",
                    "projection_sha256": "a" * 64,
                    "scope": "paired_duel_leg",
                    "state": "supported",
                }
            ],
        }

    service.timeline = timeline
    service.evaluation = evaluation
    with TestClient(app) as client:
        timeline_response = client.get("/api/embodiment/series/series_safe/timeline")
        evaluation_response = client.get("/api/embodiment/series/series_safe/evaluation")
    assert timeline_response.status_code == evaluation_response.status_code == 200
    assert timeline_response.headers["cache-control"] == "no-store"
    assert evaluation_response.headers["cache-control"] == "no-store"
    public = (timeline_response.text + evaluation_response.text).lower()
    for forbidden in (
        "api_key",
        "credential",
        "frame_png",
        "observation_json",
        "system_prompt",
        "raw_output",
        "spectator",
    ):
        assert forbidden not in public


def test_series_archive_route_reports_evidence_and_native_state_honestly() -> None:
    app = _app()

    async def archive_status(series_id):
        return {
            "archive_format": "llm-controller/paired-duel-archive/1.0.0",
            "series_id": series_id,
            "evidence": {"state": "ready", "sha256": "a" * 64},
            "evaluation": {"state": "ready", "sha256": "b" * 64},
            "timeline": {"state": "ready", "sha256": "c" * 64},
            "native_replay": {
                "state": "unavailable",
                "reason": "participant_video_not_recorded",
            },
            "plan_sha256": "d" * 64,
        }

    app.state.embodiment_series.archive_status = archive_status
    with TestClient(app) as client:
        response = client.get("/api/embodiment/series/series_safe/archive")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["native_replay"]["state"] == "unavailable"
    assert "video_url" not in response.text


def test_series_participant_frame_route_returns_only_selected_sanitized_pixels() -> None:
    app = _app()
    store = DuelParticipantFrameStore()
    secret = b"raw-output-and-credential-must-not-cross"
    store.publish(1, "participant_1", 7, _participant_png(secret))

    async def participant_frame(series_id, participant_id):
        assert series_id == "series_safe"
        return "live", store.snapshot(participant_id)

    app.state.embodiment_series.participant_frame = participant_frame
    with TestClient(app) as client:
        response = client.get(
            "/api/embodiment/series/series_safe/participants/participant_1/frame"
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-leg-index"] == "1"
    assert response.headers["x-observation-seq"] == "7"
    assert response.headers["x-participant-id"] == "participant_1"
    assert secret not in response.content


def test_series_native_video_route_serves_only_existing_leg_participant_artifact(tmp_path) -> None:
    app = _app()
    video = tmp_path / "participant.mp4"
    video.write_bytes(b"verified-native-pixels")

    async def native_video_path(series_id, leg_index, participant_id):
        assert (series_id, leg_index, participant_id) == (
            "series_safe", 1, "participant_0"
        )
        return video

    app.state.embodiment_series.native_video_path = native_video_path
    with TestClient(app) as client:
        response = client.get(
            "/api/embodiment/series/series_safe/legs/1/participants/participant_0/video"
        )
        invalid = client.get(
            "/api/embodiment/series/series_safe/legs/2/participants/participant_0/video"
        )
    assert response.status_code == 200
    assert response.content == b"verified-native-pixels"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "no-store"
    assert invalid.status_code == 404


def test_series_timeline_and_evaluation_routes_return_typed_404_and_409() -> None:
    payload = {
        "entrants": [
            {"provider": "demo", "model": "duelist-alpha-v1"},
            {"provider": "demo", "model": "duelist-bravo-v1"},
        ],
        "seed": 23,
    }
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/series", json=payload).json()
        series_id = created["series_id"]
        for suffix, code in (
            ("timeline", "embodiment_series_timeline_not_ready"),
            ("evaluation", "embodiment_series_evaluation_not_ready"),
        ):
            pending = client.get(f"/api/embodiment/series/{series_id}/{suffix}")
            missing = client.get(f"/api/embodiment/series/series_missing/{suffix}")
            assert pending.status_code == 409
            assert pending.json() == {"detail": {"code": code}}
            assert missing.status_code == 404
            assert missing.json() == {
                "detail": {"code": "embodiment_series_not_found"}
            }
        client.post(f"/api/embodiment/series/{series_id}/cancel")


def test_series_api_rejects_any_scripted_credential_and_two_scripted_entrants() -> None:
    invalid_payloads = (
        {
            "entrants": [
                {"provider": "openai", "model": "model-a", "api_key": "key"},
                {"provider": "scripted", "model": "balanced-v1", "api_key": ""},
            ],
            "seed": 1,
        },
        {
            "entrants": [
                {"provider": "scripted", "model": "scout-v1"},
                {"provider": "scripted", "model": "balanced-v1"},
            ],
            "seed": 1,
        },
    )
    with TestClient(_app()) as client:
        for payload in invalid_payloads:
            response = client.post("/api/embodiment/series", json=payload)
            assert response.status_code == 422
            assert response.json() == {"detail": {"code": "invalid_embodiment_series_request"}}


def test_series_public_replay_is_no_store_and_protected_layer_has_no_route() -> None:
    keys = ("sk-series-replay-alpha", "sk-series-replay-bravo")
    payload = {
        "entrants": [
            {"provider": "openai", "model": "model-a", "api_key": keys[0]},
            {"provider": "anthropic", "model": "model-b", "api_key": keys[1]},
        ],
        "seed": 19,
    }
    app = _app()
    with TestClient(app) as client:
        created = client.post("/api/embodiment/series", json=payload)
        series_id = created.json()["series_id"]
        bundle = _public_series_bundle(series_id)
        app.state.embodiment_series._records[series_id].public_evidence = bundle

        replay = client.get(f"/api/embodiment/series/{series_id}/replay")
        protected = client.get(f"/api/embodiment/series/{series_id}/protected-replay")
        assert replay.status_code == 200
        assert replay.headers["cache-control"] == "no-store"
        assert replay.headers["x-content-sha256"] == bundle.content_sha256
        assert replay.content == bundle.bundle_bytes
        assert protected.status_code == 404
        assert all(key not in replay.text for key in keys)


def test_series_replay_is_conflict_until_a_complete_pair_is_sealed() -> None:
    payload = {
        "entrants": [
            {"provider": "openai", "model": "model-a", "api_key": "key-alpha"},
            {"provider": "anthropic", "model": "model-b", "api_key": "key-bravo"},
        ],
        "seed": 19,
    }
    with TestClient(_app()) as client:
        created = client.post("/api/embodiment/series", json=payload)
        response = client.get(f"/api/embodiment/series/{created.json()['series_id']}/replay")
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": {"code": "embodiment_series_evidence_not_ready"}}
