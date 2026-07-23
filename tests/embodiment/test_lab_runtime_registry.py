from __future__ import annotations

from dataclasses import replace

import pytest
from genesis_arena.embodiment.episode_service import EpisodeRunSpec
from genesis_arena.embodiment.lab.games import GAME_CATALOG
from genesis_arena.embodiment.lab.runtime_registry import (
    GAME_RUNTIME_BINDINGS,
    GAME_RUNTIME_PROFILES,
    GAME_RUNTIME_REGISTRY,
    RUNTIME_REGISTRY_SCHEMA_VERSION,
    GameRuntimeBindingNotFoundError,
    GameRuntimeModeUnavailableError,
    GameRuntimeProfile,
    GameRuntimeRegistry,
    GameRuntimeRegistryError,
    resolve_runtime_launch,
    runtime_binding,
    runtime_profile,
    safe_runtime_manifest,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, canonical_sha256


def test_registry_covers_every_catalog_game_with_truthful_launch_modes() -> None:
    assert tuple(binding.game_id for binding in GAME_RUNTIME_BINDINGS) == tuple(
        game.id for game in GAME_CATALOG.games
    )
    expected = {
        "central-relay": (True, True, True, True),
        "checkpoint-race": (False, True, True, True),
        "crossroads-conquest": (False, False, True, True),
        "duo-spar": (False, True, True, True),
        "interaction": (True, True, True, True),
        "labyrinth-run": (True, False, True, True),
        "mini-rts": (True, True, True, True),
        "movement-maze": (True, True, True, True),
        "neutral-encounter": (True, True, True, True),
        "operator-action-course": (True, True, True, True),
        "orientation": (True, True, True, True),
        "relay-control": (False, True, True, True),
        "resource-relay": (False, True, True, True),
        "solo-construction": (True, True, True, True),
        "trio-free-for-all": (False, True, True, True),
        "trio-relay": (False, True, True, True),
    }

    for game_id, capabilities in expected.items():
        binding = runtime_binding(game_id)
        assert (
            binding.live_launch,
            binding.demo_launch,
            binding.replay,
            binding.spectator,
        ) == capabilities


def test_launch_resolution_is_explicit_and_never_treats_replay_as_authority() -> None:
    assert resolve_runtime_launch("labyrinth-run", "live").profile_id == "labyrinth.live"
    assert resolve_runtime_launch("mini-rts", "demo").canonical_task_id == "rts-skirmish-v0"
    assert resolve_runtime_launch("mini-rts", "live").canonical_task_id == "rts-skirmish-v1"
    movement = resolve_runtime_launch("movement-maze", "live")
    assert movement.protocol_version == "llm-controller/0.2.0"
    assert movement.authority_owner == "godot"

    for game_id, mode in (
        ("checkpoint-race", "live"),
        ("crossroads-conquest", "live"),
        ("crossroads-conquest", "demo"),
        ("labyrinth-run", "demo"),
        ("relay-control", "live"),
        ("labyrinth-run", "replay"),
        ("labyrinth-run", "anything"),
    ):
        with pytest.raises(GameRuntimeModeUnavailableError):
            resolve_runtime_launch(game_id, mode)

    with pytest.raises(GameRuntimeBindingNotFoundError):
        resolve_runtime_launch("not-a-game", "live")


def test_live_control_profiles_match_task_selected_managed_protocols() -> None:
    for profile_id, task_id, catalog_game_id in (
        ("solo.movement-maze.live", "movement-maze-v0", "movement-maze"),
        ("solo.operator-action-course.live", "operator-action-course-v0", None),
    ):
        profile = runtime_profile(profile_id)
        run = EpisodeRunSpec(
            episode_id=f"ep_registry_{task_id}",
            provider="openai",
            model="test-model",
            task_id=task_id,
            seed=1,
        )
        assert profile.authority_kind == "solo_episode"
        assert run.protocol_version == profile.protocol_version == "llm-controller/0.2.0"
        if catalog_game_id is not None:
            assert resolve_runtime_launch(catalog_game_id, "live") == profile


def test_profiles_cover_all_existing_embodiment_tasks_and_non_catalog_authorities() -> None:
    canonical_tasks = {
        profile.canonical_task_id
        for profile in GAME_RUNTIME_PROFILES
        if profile.canonical_task_id is not None
    }
    assert {
        "orientation-v0",
        "interaction-v0",
        "construction-v0",
        "neutral-encounter-v0",
        "central-relay-v0",
        "movement-maze-v0",
        "operator-action-course-v0",
        "duo-checkpoint-race-v0",
        "duo-relay-control-v0",
        "duo-spar-v0",
        "duo-resource-relay-v0",
        "rts-skirmish-v0",
        "rts-skirmish-v1",
        "trio-relay-v0",
        "trio-free-for-all-v0",
        "trio-maze-race-v0",
        "trio-maze-race-v1",
        "crossroads-conquest-v0",
    } <= canonical_tasks

    assert runtime_profile("worldarena-duel.live").canonical_scenario_id == ("crossroads-duel-v1")
    assert runtime_profile("worldarena-duel.live").authority_kind == "managed_duel_match"
    assert runtime_profile("legacy.survival.live").availability == "blocked"
    assert runtime_profile("legacy.survival.live").blocked_reason == (
        "canonical_lab_lifecycle_unavailable"
    )


def test_new_catalog_games_use_exact_existing_authority_profiles() -> None:
    catalog_expected = {
        "duel.central-relay.demo",
        "duel.central-relay.live",
        "duel.spar.demo",
        "solo.interaction.demo",
        "solo.interaction.live",
        "solo.neutral-encounter.demo",
        "solo.neutral-encounter.live",
        "solo.operator-action-course.demo",
        "solo.operator-action-course.live",
        "solo.orientation.demo",
        "solo.orientation.live",
        "trio.free-for-all.demo",
        "trio.relay.demo",
    }
    profile_by_id = {profile.profile_id: profile for profile in GAME_RUNTIME_PROFILES}
    assert catalog_expected <= set(profile_by_id)
    assert all(profile_by_id[profile_id].exposure == "catalog" for profile_id in catalog_expected)
    catalog_refs = {
        profile_id for binding in GAME_RUNTIME_BINDINGS for profile_id in binding.profile_ids
    }
    assert catalog_expected <= catalog_refs
    assert GAME_RUNTIME_REGISTRY.resolve_profile_launch("trio.relay.demo").launchable is True

    internal_expected = {
        "legacy.survival.live",
        "worldarena-duel.baseline",
        "worldarena-duel.live",
    }
    assert all(profile_by_id[profile_id].exposure == "internal" for profile_id in internal_expected)
    assert internal_expected.isdisjoint(catalog_refs)


def test_authority_ownership_does_not_disguise_backend_or_cached_replay_boundaries() -> None:
    labyrinth = runtime_profile("labyrinth.live")
    assert labyrinth.authority_kind == "labyrinth_race"
    assert labyrinth.authority_owner == "backend"

    for profile in GAME_RUNTIME_PROFILES:
        if profile.mode == "replay":
            assert profile.authority_kind == "cached_showcase"
            assert profile.authority_owner == "sealed_replay"
            assert profile.availability == "replay_only"
            assert profile.providers == ()
            assert profile.launchable is False
        if profile.authority_kind in {
            "arena_websocket",
            "legacy_world_socket",
            "managed_duel_match",
            "paired_series",
            "solo_episode",
            "trio_series",
        }:
            assert profile.authority_owner == "godot"


def test_safe_manifest_is_canonical_hash_bound_and_contains_no_runtime_paths_or_secrets() -> None:
    manifest = safe_runtime_manifest()
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}

    assert manifest["schema_version"] == RUNTIME_REGISTRY_SCHEMA_VERSION
    assert manifest["manifest_sha256"] == canonical_sha256(body)
    assert GAME_RUNTIME_REGISTRY.canonical_safe_bytes() == canonical_json_bytes(manifest)
    assert safe_runtime_manifest() == manifest

    rendered = canonical_json_bytes(manifest).decode("utf-8")
    for forbidden in (
        "/Users/",
        "/workspace/",
        "C:\\",
        ".gd",
        ".py",
        "api_key",
        "credential",
        "secret",
        "scratchpad",
        "raw_response",
    ):
        assert forbidden not in rendered
    assert "managed_lab_adapter_unavailable" in rendered
    assert "crossroads-duel-v1" in rendered


def test_profile_and_registry_invariants_fail_closed() -> None:
    live = runtime_profile("solo.orientation.live")
    replay = runtime_profile("labyrinth.cached-replay")

    with pytest.raises(GameRuntimeRegistryError, match="sealed replay"):
        replace(replay, providers=("demo",))
    with pytest.raises(GameRuntimeRegistryError, match="stable reason"):
        replace(live, availability="blocked")
    with pytest.raises(GameRuntimeRegistryError, match="providers"):
        replace(live, providers=())
    with pytest.raises(GameRuntimeRegistryError, match="canonical task or scenario"):
        replace(live, canonical_task_id=None, canonical_scenario_id=None)
    with pytest.raises(GameRuntimeRegistryError, match="protocol_version"):
        replace(live, protocol_version="/Users/example/protocol.json")

    movement_binding = runtime_binding("movement-maze")
    inconsistent = replace(movement_binding, live_launch=False)
    bindings = tuple(
        inconsistent if binding.game_id == "movement-maze" else binding
        for binding in GAME_RUNTIME_BINDINGS
    )
    with pytest.raises(GameRuntimeRegistryError, match="differs from admitted"):
        GameRuntimeRegistry(profiles=GAME_RUNTIME_PROFILES, bindings=bindings)

    internal = runtime_profile("worldarena-duel.live")
    catalog_internal = replace(internal, profile_id="worldarena-duel.catalog-copy")
    profiles = tuple(
        sorted((*GAME_RUNTIME_PROFILES, catalog_internal), key=lambda profile: profile.profile_id)
    )
    first_binding = GAME_RUNTIME_BINDINGS[0]
    bad_binding = replace(
        first_binding,
        profile_ids=tuple(sorted((*first_binding.profile_ids, catalog_internal.profile_id))),
        live_launch=True,
    )
    bindings = (bad_binding, *GAME_RUNTIME_BINDINGS[1:])
    with pytest.raises(GameRuntimeRegistryError, match="internal"):
        GameRuntimeRegistry(profiles=profiles, bindings=bindings)


def test_profile_constructor_rejects_ambiguous_replay_or_path_material() -> None:
    with pytest.raises(GameRuntimeRegistryError, match="provider-free sealed replay"):
        GameRuntimeProfile(
            profile_id="bad.replay",
            authority_kind="cached_showcase",
            authority_owner="sealed_replay",
            mode="replay",
            availability="available",
            protocol_version="example/1",
            canonical_task_id="example-v0",
            canonical_scenario_id="example-v0",
            participant_count=1,
            providers=("demo",),
            exposure="internal",
        )
