from __future__ import annotations

from dataclasses import replace

import pytest
from genesis_arena.embodiment.lab.games import (
    GAME_CATALOG,
    GAME_CATALOG_SCHEMA_VERSION,
    GAME_CATEGORIES,
    GAME_SPEC_SCHEMA_VERSION,
    GameCapabilities,
    GameCatalog,
    GameCatalogError,
    GameCategory,
    GameParticipantRange,
    game_spec,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, canonical_sha256


def test_catalog_covers_current_lab_game_guide_seeds_with_factual_readiness() -> None:
    assert tuple(game.id for game in GAME_CATALOG.games) == (
        "central-relay",
        "checkpoint-race",
        "crossroads-conquest",
        "duo-spar",
        "interaction",
        "labyrinth-run",
        "mini-rts",
        "movement-maze",
        "neutral-encounter",
        "operator-action-course",
        "orientation",
        "relay-control",
        "resource-relay",
        "solo-construction",
        "trio-free-for-all",
        "trio-relay",
    )
    assert game_spec("central-relay").readiness == "live_ready"
    assert game_spec("interaction").readiness == "live_ready"
    assert game_spec("labyrinth-run").readiness == "live_ready"
    assert game_spec("mini-rts").readiness == "live_ready"
    assert game_spec("movement-maze").readiness == "live_ready"
    assert game_spec("neutral-encounter").readiness == "live_ready"
    assert game_spec("operator-action-course").readiness == "live_ready"
    assert game_spec("orientation").readiness == "live_ready"
    assert game_spec("solo-construction").readiness == "live_ready"
    assert game_spec("crossroads-conquest").readiness == "demo_replay_ready"
    assert game_spec("duo-spar").readiness == "demo_replay_ready"
    assert game_spec("relay-control").readiness == "demo_replay_ready"
    assert game_spec("checkpoint-race").readiness == "demo_replay_ready"
    assert game_spec("trio-free-for-all").readiness == "demo_replay_ready"
    assert game_spec("trio-relay").readiness == "demo_replay_ready"


def test_catalog_and_game_public_projections_are_canonical_and_hash_bound() -> None:
    labyrinth = game_spec("labyrinth-run")
    game_payload = labyrinth.public_dict()
    game_body = {key: value for key, value in game_payload.items() if key != "spec_sha256"}

    assert game_payload["schema_version"] == GAME_SPEC_SCHEMA_VERSION
    assert game_payload["spec_sha256"] == canonical_sha256(game_body)
    assert labyrinth.canonical_public_bytes() == canonical_json_bytes(game_payload)
    assert labyrinth.public_dict() == game_payload

    catalog_payload = GAME_CATALOG.public_dict()
    catalog_body = {key: value for key, value in catalog_payload.items() if key != "catalog_sha256"}
    assert catalog_payload["schema_version"] == GAME_CATALOG_SCHEMA_VERSION
    assert catalog_payload["catalog_sha256"] == canonical_sha256(catalog_body)
    assert catalog_payload["catalog_sha256"] == (
        "277314d111863c4c21954d7c4543e5c0c1b3b927574053594efc35f4013206f6"
    )
    assert len(catalog_payload["games"]) == 16
    assert GAME_CATALOG.canonical_public_bytes() == canonical_json_bytes(catalog_payload)
    assert GAME_CATALOG.public_dict() == catalog_payload
    assert GameCatalog(games=GAME_CATALOG.games).public_dict() == catalog_payload


def test_game_passports_have_stable_taxonomy_participants_and_explicit_capabilities() -> None:
    expected = {
        "central-relay": ("two-agent-games", 2, 2, "competitive", True),
        "checkpoint-race": ("two-agent-games", 2, 2, "competitive", False),
        "crossroads-conquest": ("strategy-worlds", 3, 3, "competitive", False),
        "duo-spar": ("two-agent-games", 2, 2, "competitive", False),
        "interaction": ("solo-agent-tasks", 1, 1, "solo", True),
        "labyrinth-run": ("multi-agent-games", 3, 3, "competitive", True),
        "mini-rts": ("strategy-worlds", 2, 2, "competitive", True),
        "movement-maze": ("solo-agent-tasks", 1, 1, "solo", True),
        "neutral-encounter": ("solo-agent-tasks", 1, 1, "solo", True),
        "operator-action-course": ("sandbox-primitives", 1, 1, "solo", True),
        "orientation": ("solo-agent-tasks", 1, 1, "solo", True),
        "relay-control": ("two-agent-games", 2, 2, "competitive", False),
        "resource-relay": ("two-agent-games", 2, 2, "competitive", False),
        "solo-construction": ("solo-agent-tasks", 1, 1, "solo", True),
        "trio-free-for-all": ("multi-agent-games", 3, 3, "competitive", False),
        "trio-relay": ("multi-agent-games", 3, 3, "competitive", False),
    }
    capability_fields = {
        "live_launch",
        "demo",
        "replay",
        "spectator",
        "benchmark",
        "checkpoint",
    }

    for game in GAME_CATALOG.games:
        category_id, minimum, maximum, interaction, live_launch = expected[game.id]
        payload = game.public_dict()
        assert payload["primary_category"]["id"] == category_id
        assert payload["participants"] == {"minimum": minimum, "maximum": maximum}
        assert payload["interaction_kind"] == interaction
        assert payload["secondary_tags"] == sorted(set(payload["secondary_tags"]))
        assert set(payload["capabilities"]) == capability_fields
        assert all(isinstance(value, bool) for value in payload["capabilities"].values())
        assert payload["capabilities"]["live_launch"] is live_launch
        if game.readiness == "live_ready":
            assert payload["capabilities"]["live_launch"] is True
        elif game.readiness == "demo_replay_ready":
            assert payload["capabilities"]["live_launch"] is False
            assert payload["capabilities"]["demo"] or payload["capabilities"]["replay"]


def test_catalog_projects_complete_ordered_category_groups_for_a_grouped_picker() -> None:
    payload = GAME_CATALOG.public_dict()
    categories = payload["categories"]

    assert [category["id"] for category in categories] == [
        "sandbox-primitives",
        "solo-agent-tasks",
        "two-agent-games",
        "multi-agent-games",
        "strategy-worlds",
    ]
    assert [category["label"] for category in categories] == [
        "Sandbox Primitives",
        "Solo Agent Tasks",
        "Two-Agent Games",
        "Multi-Agent Games",
        "Strategy Worlds",
    ]
    assert [category["order"] for category in categories] == [10, 20, 30, 40, 50]
    assert categories[0]["game_ids"] == ["operator-action-course"]
    assert categories[1]["game_ids"] == [
        "interaction",
        "movement-maze",
        "neutral-encounter",
        "orientation",
        "solo-construction",
    ]
    assert categories[2]["game_ids"] == [
        "central-relay",
        "checkpoint-race",
        "duo-spar",
        "relay-control",
        "resource-relay",
    ]
    assert categories[3]["game_ids"] == [
        "labyrinth-run",
        "trio-free-for-all",
        "trio-relay",
    ]
    assert categories[4]["game_ids"] == ["crossroads-conquest", "mini-rts"]

    projected_ids = [game_id for category in categories for game_id in category["game_ids"]]
    assert len(projected_ids) == len(set(projected_ids)) == len(GAME_CATALOG.games)
    assert set(projected_ids) == {game.id for game in GAME_CATALOG.games}
    assert all(category["game_ids"] for category in categories)
    assert tuple(category.public_dict() for category in GAME_CATEGORIES) == tuple(
        {key: value for key, value in category.items() if key != "game_ids"}
        for category in categories
    )


def test_v2_passports_preserve_every_v1_guide_field_and_add_no_runtime_paths() -> None:
    v1_fields = {
        "schema_version",
        "id",
        "title",
        "readiness",
        "readiness_note",
        "task_ids",
        "capability_statements",
        "agent_interface",
        "scoring",
        "configuration_controls",
        "failure_modes",
        "safety",
        "supported_modes",
        "spec_sha256",
    }

    for game in GAME_CATALOG.games:
        payload = game.public_dict()
        assert v1_fields <= set(payload)
        public_text = repr(payload)
        assert "/Users/" not in public_text
        assert "/workspace/" not in public_text
        assert "\\\\" not in public_text


def test_game_passport_taxonomy_and_capability_invariants_fail_closed() -> None:
    labyrinth = game_spec("labyrinth-run")

    with pytest.raises(GameCatalogError, match="unsupported"):
        GameCategory(
            id="unknown-category",  # type: ignore[arg-type]
            label="Unknown",
            order=999,
            description="Not a registered WorldEval taxonomy category.",
        )
    with pytest.raises(GameCatalogError, match="participant range"):
        GameParticipantRange(minimum=3, maximum=2)
    with pytest.raises(GameCatalogError, match="replay evidence"):
        GameCapabilities(
            live_launch=False,
            demo=False,
            replay=False,
            spectator=False,
            benchmark=True,
            checkpoint=False,
        )
    with pytest.raises(GameCatalogError, match="live-ready"):
        replace(
            labyrinth,
            capabilities=replace(labyrinth.capabilities, live_launch=False),
        )
    with pytest.raises(GameCatalogError, match="exactly one participant"):
        replace(labyrinth, interaction_kind="solo")
    with pytest.raises(GameCatalogError, match="unique, and sorted"):
        replace(labyrinth, secondary_tags=("race", "navigation"))


def test_labyrinth_guide_describes_only_current_live_controls_and_private_boundaries() -> None:
    labyrinth = game_spec("labyrinth-run").public_dict()
    controls = {control["id"]: control for control in labyrinth["configuration_controls"]}
    modes = {mode["id"] for mode in labyrinth["supported_modes"]}
    text = repr(labyrinth).casefold()

    assert tuple(labyrinth["task_ids"]) == ("trio-maze-race-v0", "trio-maze-race-v1")
    assert controls["provider"]["options"] == ["openai", "anthropic", "gemini"]
    assert controls["vision_range_cells"]["options"] == ["1", "2", "4", "8", "infinite"]
    assert "live_provider_race" in modes
    assert "map_seed" not in controls
    for protected in ("raw provider responses", "navigation memory", "credentials"):
        assert protected in text


def test_catalog_covers_existing_missing_authorities_without_overclaiming_live_modes() -> None:
    task_ids = {task_id for game in GAME_CATALOG.games for task_id in game.task_ids}
    assert {
        "orientation-v0",
        "interaction-v0",
        "neutral-encounter-v0",
        "operator-action-course-v0",
        "central-relay-v0",
        "duo-spar-v0",
        "trio-relay-v0",
        "trio-free-for-all-v0",
    } <= task_ids

    assert game_spec("central-relay").capabilities.live_launch is True
    assert game_spec("operator-action-course").capabilities.live_launch is True
    for game_id in ("duo-spar", "trio-free-for-all", "trio-relay"):
        game = game_spec(game_id)
        assert game.capabilities.live_launch is False
        assert game.capabilities.demo is True
        assert game.capabilities.replay is True


def test_operator_action_course_is_the_godot_owned_composite_primitive_passport() -> None:
    game = game_spec("operator-action-course").public_dict()
    text = repr(game).casefold()

    assert game["primary_category"]["id"] == "sandbox-primitives"
    assert tuple(game["task_ids"]) == ("operator-action-course-v0",)
    assert {"live_solo_course", "deterministic_demo", "primitive_diagnostics"} == {
        mode["id"] for mode in game["supported_modes"]
    }
    for station in (
        "walk",
        "turn",
        "gather",
        "carry",
        "deposit",
        "build",
        "dash",
        "guard",
        "primary",
        "cancel",
        "hazard",
        "celebrate",
    ):
        assert station in text
    assert "godot authority" in text


def test_catalog_rejects_out_of_order_and_unknown_game_lookups() -> None:
    with pytest.raises(GameCatalogError, match="id-sorted"):
        GameCatalog(games=tuple(reversed(GAME_CATALOG.games)))
    with pytest.raises(GameCatalogError, match="not registered"):
        game_spec("not-a-game")
