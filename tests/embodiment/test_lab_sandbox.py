from __future__ import annotations

from dataclasses import replace

import pytest
from genesis_arena.embodiment.lab.sandbox import (
    OPERATOR_ACTION_COURSE_SANDBOX_RECIPE,
    SANDBOX_MANIFEST_SCHEMA_VERSION,
    SANDBOX_PRIMITIVE_SCHEMA_VERSION,
    SANDBOX_PRIMITIVES,
    SANDBOX_RECIPE_SCHEMA_VERSION,
    SandboxAuthorityBinding,
    SandboxManifestError,
    SandboxPrimitiveSpec,
    SandboxRecipeSpec,
    draft_sandbox_recipe,
    resolve_sandbox_primitive_order,
    sandbox_manifest,
)
from genesis_arena.embodiment.protocol import canonical_json_bytes, canonical_sha256


def _primitive(
    primitive_id: str,
    rank: int,
    *,
    dependencies: tuple[str, ...] = (),
) -> SandboxPrimitiveSpec:
    return SandboxPrimitiveSpec(
        id=primitive_id,
        title=primitive_id.replace("-", " ").title(),
        summary=f"Godot owns the {primitive_id} test capability.",
        composition_rank=rank,
        dependencies=dependencies,
        capabilities=(f"{primitive_id}-capability",),
    )


def test_primitive_registry_covers_the_existing_godot_owned_building_blocks() -> None:
    expected_ids = {
        "movement",
        "orientation",
        "visibility",
        "interaction",
        "construction",
        "resources-economy",
        "combat",
        "event-ledger",
        "scoring-termination",
        "checkpoint-serialization",
    }

    assert {primitive.id for primitive in SANDBOX_PRIMITIVES} == expected_ids
    assert all(primitive.authority_owner == "godot" for primitive in SANDBOX_PRIMITIVES)
    assert all(
        primitive.schema_version == SANDBOX_PRIMITIVE_SCHEMA_VERSION
        for primitive in SANDBOX_PRIMITIVES
    )
    assert resolve_sandbox_primitive_order(tuple(sorted(expected_ids))) == (
        "event-ledger",
        "orientation",
        "movement",
        "visibility",
        "interaction",
        "resources-economy",
        "construction",
        "combat",
        "scoring-termination",
        "checkpoint-serialization",
    )


def test_primitive_and_recipe_hashes_are_canonical_deterministic_and_round_trip() -> None:
    primitive = SANDBOX_PRIMITIVES[0]
    primitive_payload = primitive.public_dict()
    primitive_body = {
        key: value for key, value in primitive_payload.items() if key != "spec_sha256"
    }
    assert primitive_payload["spec_sha256"] == canonical_sha256(primitive_body)
    assert primitive.canonical_public_bytes() == canonical_json_bytes(primitive_payload)
    assert SandboxPrimitiveSpec.from_dict(primitive_payload) == primitive

    recipe = OPERATOR_ACTION_COURSE_SANDBOX_RECIPE
    recipe_payload = recipe.public_dict()
    recipe_body = {key: value for key, value in recipe_payload.items() if key != "recipe_sha256"}
    assert recipe_payload["recipe_sha256"] == canonical_sha256(recipe_body)
    assert recipe.canonical_public_bytes() == canonical_json_bytes(recipe_payload)
    assert SandboxRecipeSpec.from_dict(recipe_payload) == recipe

    # Returned projections never hand mutable caller-owned data back into a frozen spec.
    primitive_payload["dependencies"].append("tamper")
    recipe_payload["primitive_ids"].append("tamper")
    assert primitive.public_dict()["dependencies"] == list(primitive.dependencies)
    assert recipe.public_dict()["primitive_ids"] == list(recipe.primitive_ids)


def test_exact_projection_fields_and_bound_hashes_fail_closed() -> None:
    primitive_payload = SANDBOX_PRIMITIVES[0].public_dict()
    primitive_payload["extra"] = True
    with pytest.raises(SandboxManifestError, match="fields differ"):
        SandboxPrimitiveSpec.from_dict(primitive_payload)

    primitive_payload = SANDBOX_PRIMITIVES[0].public_dict()
    primitive_payload["title"] = "Changed"
    with pytest.raises(SandboxManifestError, match="does not match"):
        SandboxPrimitiveSpec.from_dict(primitive_payload)

    recipe_payload = OPERATOR_ACTION_COURSE_SANDBOX_RECIPE.public_dict()
    del recipe_payload["composition_order"]
    with pytest.raises(SandboxManifestError, match="fields differ"):
        SandboxRecipeSpec.from_dict(recipe_payload)

    recipe_payload = OPERATOR_ACTION_COURSE_SANDBOX_RECIPE.public_dict()
    recipe_payload["recipe_sha256"] = "0" * 64
    with pytest.raises(SandboxManifestError, match="does not match"):
        SandboxRecipeSpec.from_dict(recipe_payload)


def test_specs_reject_mutable_sequence_fields() -> None:
    primitive = SANDBOX_PRIMITIVES[0]
    with pytest.raises(SandboxManifestError, match="immutable tuple"):
        replace(primitive, dependencies=[])  # type: ignore[arg-type]
    with pytest.raises(SandboxManifestError, match="immutable tuple"):
        replace(primitive, capabilities=["events"])  # type: ignore[arg-type]

    recipe = OPERATOR_ACTION_COURSE_SANDBOX_RECIPE
    with pytest.raises(SandboxManifestError, match="immutable tuple"):
        replace(recipe, primitive_ids=list(recipe.primitive_ids))  # type: ignore[arg-type]
    with pytest.raises(SandboxManifestError, match="immutable tuple"):
        replace(
            recipe,
            composition_order=list(recipe.composition_order),  # type: ignore[arg-type]
        )


def test_dependency_validation_rejects_unknown_missing_and_cyclic_primitives() -> None:
    unknown_registry = (_primitive("alpha", 10, dependencies=("missing",)),)
    with pytest.raises(SandboxManifestError, match="unknown dependencies"):
        resolve_sandbox_primitive_order(("alpha",), primitives=unknown_registry)

    closed_registry = (
        _primitive("alpha", 10),
        _primitive("beta", 20, dependencies=("alpha",)),
    )
    with pytest.raises(SandboxManifestError, match="omits required dependencies"):
        resolve_sandbox_primitive_order(("beta",), primitives=closed_registry)

    cyclic_registry = (
        _primitive("alpha", 10, dependencies=("beta",)),
        _primitive("beta", 20, dependencies=("alpha",)),
    )
    with pytest.raises(SandboxManifestError, match="cycle detected"):
        resolve_sandbox_primitive_order(
            ("alpha", "beta"),
            primitives=cyclic_registry,
        )


def test_composition_order_is_dependency_driven_and_not_caller_order() -> None:
    registry = (
        _primitive("foundation", 30),
        _primitive("adapter", 10, dependencies=("foundation",)),
        _primitive("surface", 20, dependencies=("adapter",)),
    )

    assert resolve_sandbox_primitive_order(
        ("surface", "foundation", "adapter"),
        primitives=registry,
    ) == ("foundation", "adapter", "surface")


def test_only_the_frozen_operator_action_course_recipe_is_executable() -> None:
    recipe = OPERATOR_ACTION_COURSE_SANDBOX_RECIPE

    assert recipe.schema_version == SANDBOX_RECIPE_SCHEMA_VERSION
    assert recipe.id == "operator-action-course-sandbox-v1"
    assert recipe.lifecycle == "canonical"
    assert recipe.executable is True
    assert recipe.authority_binding == SandboxAuthorityBinding(
        task_id="operator-action-course-v0",
        protocol_version="llm-controller/0.2.0",
    )
    assert set(recipe.primitive_ids) == {primitive.id for primitive in SANDBOX_PRIMITIVES}

    with pytest.raises(SandboxManifestError, match="only the canonical"):
        replace(recipe, id="custom-executable")
    with pytest.raises(SandboxManifestError, match="only the canonical"):
        replace(
            recipe,
            authority_binding=SandboxAuthorityBinding(
                task_id="movement-maze-v0",
                protocol_version="llm-controller/0.2.0",
            ),
        )
    with pytest.raises(SandboxManifestError, match="unbound drafts"):
        replace(recipe, executable=False, authority_binding=None)


def test_custom_compositions_are_dependency_checked_unbound_draft_metadata() -> None:
    recipe = draft_sandbox_recipe(
        recipe_id="movement-inspection-draft",
        title="Movement inspection",
        summary="A proposed composition for reviewing movement and participant visibility.",
        primitive_ids=("visibility", "orientation", "movement"),
    )

    assert recipe.lifecycle == "draft"
    assert recipe.executable is False
    assert recipe.authority_binding is None
    assert recipe.primitive_ids == ("movement", "orientation", "visibility")
    assert recipe.composition_order == ("orientation", "movement", "visibility")

    with pytest.raises(SandboxManifestError, match="omits required dependencies"):
        draft_sandbox_recipe(
            recipe_id="visibility-without-orientation",
            title="Incomplete visibility",
            summary="This draft deliberately omits a required primitive.",
            primitive_ids=("visibility",),
        )


def test_manifest_is_safe_canonical_hash_bound_and_contains_only_admitted_recipe() -> None:
    payload = sandbox_manifest()
    body = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    serialized = canonical_json_bytes(payload)
    lower = serialized.lower()

    assert payload["schema_version"] == SANDBOX_MANIFEST_SCHEMA_VERSION
    assert payload["authority_owner"] == "godot"
    assert payload["manifest_sha256"] == canonical_sha256(body)
    assert [recipe["id"] for recipe in payload["recipes"]] == ["operator-action-course-sandbox-v1"]
    assert len(payload["primitives"]) == 10
    for forbidden in (
        b"/users/",
        b"/home/",
        b"/workspace/",
        b"file://",
        b"\\\\",
        b"api_key",
        b"credential",
        b"prompt",
        b"scratchpad",
        b"raw_response",
        b"hidden_state",
    ):
        assert forbidden not in lower

    assert sandbox_manifest() == payload
    assert canonical_json_bytes(sandbox_manifest()) == serialized


def test_public_specs_reject_paths_credentials_and_non_godot_authority() -> None:
    with pytest.raises(SandboxManifestError, match="safe public text"):
        replace(SANDBOX_PRIMITIVES[0], summary="/Users/example/project/module.gd")
    with pytest.raises(SandboxManifestError, match="safe public text"):
        replace(
            SANDBOX_PRIMITIVES[0],
            summary="Use sk-proj-abcdefghijklmnopqrstuvwxyz123456 for this module.",
        )
    with pytest.raises(SandboxManifestError, match="must be Godot"):
        replace(SANDBOX_PRIMITIVES[0], authority_owner="backend")
