from __future__ import annotations

import pytest
from genesis_arena.embodiment.lab.games import (
    GAME_CATALOG,
    GAME_CATALOG_SCHEMA_VERSION,
    GAME_SPEC_SCHEMA_VERSION,
    GameCatalog,
    GameCatalogError,
    game_spec,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, canonical_sha256


def test_catalog_covers_current_lab_game_guide_seeds_with_factual_readiness() -> None:
    assert tuple(game.id for game in GAME_CATALOG.games) == (
        "checkpoint-race",
        "crossroads-conquest",
        "labyrinth-run",
        "mini-rts",
        "movement-maze",
        "relay-control",
        "resource-relay",
        "solo-construction",
    )
    assert game_spec("labyrinth-run").readiness == "live_ready"
    assert game_spec("mini-rts").readiness == "live_ready"
    assert game_spec("movement-maze").readiness == "live_ready"
    assert game_spec("solo-construction").readiness == "live_ready"
    assert game_spec("crossroads-conquest").readiness == "demo_replay_ready"
    assert game_spec("relay-control").readiness == "demo_replay_ready"
    assert game_spec("checkpoint-race").readiness == "demo_replay_ready"


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
    assert GAME_CATALOG.canonical_public_bytes() == canonical_json_bytes(catalog_payload)
    assert GAME_CATALOG.public_dict() == catalog_payload


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


def test_catalog_rejects_out_of_order_and_unknown_game_lookups() -> None:
    with pytest.raises(GameCatalogError, match="id-sorted"):
        GameCatalog(games=tuple(reversed(GAME_CATALOG.games)))
    with pytest.raises(GameCatalogError, match="not registered"):
        game_spec("not-a-game")
