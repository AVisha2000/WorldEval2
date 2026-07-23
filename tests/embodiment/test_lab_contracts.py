from __future__ import annotations

import copy

import pytest
from genesis_arena.embodiment.lab.contracts import (
    LabContractError,
    RaceCartridge,
    ReplayProjection,
    RunContract,
    RunEntrant,
    RunLifecycle,
    RunMode,
    RunState,
    assert_public_projection_safe,
)


def _contract() -> RunContract:
    return RunContract.create(
        run_id="run_labyrinth_001",
        game_id="labyrinth-run",
        game_version="labyrinth-run-v2",
        mode=RunMode.EXPLORATORY,
        entrants=(
            RunEntrant("entrant_terra", "gpt-5.6-terra"),
            RunEntrant("entrant_sol", "gpt-5.6-sol", display_name="Sol"),
        ),
        seed_policy={"kind": "fixed", "seed": 42},
        budget={"decision_calls": 450, "max_ticks": 1_800},
        configuration={"skill_mode": "none", "vision_depth": 4},
        runtime_version="worldeval-2026.07",
        scenario_id="labyrinth-main",
        map_id="main-medium-01",
        map_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )


def test_run_contract_is_canonical_immutable_and_round_trippable():
    contract = _contract()
    reordered = RunContract.create(
        run_id="run_labyrinth_001",
        game_id="labyrinth-run",
        game_version="labyrinth-run-v2",
        mode="exploratory",
        entrants=(
            RunEntrant("entrant_sol", "gpt-5.6-sol", display_name="Sol"),
            RunEntrant("entrant_terra", "gpt-5.6-terra"),
        ),
        seed_policy={"seed": 42, "kind": "fixed"},
        budget={"max_ticks": 1_800, "decision_calls": 450},
        configuration={"vision_depth": 4, "skill_mode": "none"},
        runtime_version="worldeval-2026.07",
        scenario_id="labyrinth-main",
        map_id="main-medium-01",
        map_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )

    assert contract.contract_sha256 == reordered.contract_sha256
    assert RunContract.from_dict(contract.as_dict()) == contract
    assert contract.canonical_bytes == reordered.canonical_bytes

    rendered = dict(contract.as_dict())
    rendered["configuration"]["vision_depth"] = 99
    assert contract.configuration["vision_depth"] == 4


def test_contract_clone_records_parent_and_only_changed_safe_fields():
    root = _contract()
    child = root.clone(
        run_id="run_labyrinth_002",
        changes={"configuration": {"skill_mode": "maze-navigation-v1", "vision_depth": 8}},
    )

    assert root.parent_contract_sha256 is None
    assert child.parent_contract_sha256 == root.contract_sha256
    assert child.run_id == "run_labyrinth_002"
    assert child.configuration["vision_depth"] == 8
    assert root.configuration["vision_depth"] == 4
    assert child.lineage_diff == {
        "configuration": {
            "from": {"skill_mode": "none", "vision_depth": 4},
            "to": {"skill_mode": "maze-navigation-v1", "vision_depth": 8},
        }
    }
    assert child.contract_sha256 != root.contract_sha256

    with pytest.raises(LabContractError, match="immutable"):
        root.clone(run_id="run_labyrinth_003", changes={"contract_sha256": "f" * 64})
    with pytest.raises(LabContractError, match="new run_id"):
        root.clone(run_id=root.run_id, changes={})


@pytest.mark.parametrize(
    "key",
    (
        "apiKey",
        "prompt_text",
        "rawResponse",
        "scratchpad_update",
        "navigation-memory-v2",
        "agentObservations",
    ),
)
def test_protected_material_is_rejected_from_contracts_and_public_projections(key):
    with pytest.raises(LabContractError, match="protected"):
        assert_public_projection_safe({key: "not for a browser"})

    with pytest.raises(LabContractError, match="protected"):
        ReplayProjection.create(
            run_id="run_labyrinth_001",
            contract_sha256="c" * 64,
            status="running",
            sequence=0,
            snapshot={key: "not for a browser"},
        )

    root = _contract()
    with pytest.raises(LabContractError, match="protected"):
        root.clone(run_id="run_labyrinth_unsafe", changes={"configuration": {key: "unsafe"}})


def test_hash_only_bindings_are_safe_but_credential_like_values_are_not():
    assert_public_projection_safe({"prompt_sha256": "d" * 64, "peak_memory_bytes": 2_048})
    with pytest.raises(LabContractError, match="digest"):
        assert_public_projection_safe({"prompt_sha256": "not-a-digest"})
    with pytest.raises(LabContractError, match="credential-like"):
        assert_public_projection_safe({"label": "sk-proj-abcdefghijklmnopqrstuvwx"})
    with pytest.raises(LabContractError, match="credential-like"):
        assert_public_projection_safe({"label": "AIza" + "a" * 35})
    with pytest.raises(LabContractError, match="credential-like"):
        RunEntrant("entrant_0", "sk-proj-" + "a" * 24)


def test_run_lifecycle_accepts_only_legal_transitions_and_terminal_evidence():
    root = _contract()
    draft = RunState.create(run_id=root.run_id, contract_sha256=root.contract_sha256)
    queued = draft.transition("queued")
    running = queued.transition("running")
    checkpointed = running.transition("checkpointed", checkpoint_sequence=1)
    resumed = checkpointed.transition("running")
    completed = resumed.transition("completed", result_sha256="e" * 64)
    sealed = completed.transition("sealed")
    verified = sealed.transition("verified")

    assert verified.status is RunLifecycle.VERIFIED
    assert verified.checkpoint_sequence == 1
    assert RunState.from_dict(verified.as_dict()) == verified
    with pytest.raises(LabContractError, match="illegal"):
        verified.transition("running")
    with pytest.raises(LabContractError, match="advance"):
        running.transition("checkpointed", checkpoint_sequence=0)
    with pytest.raises(LabContractError, match="result fingerprint"):
        resumed.transition("completed")
    with pytest.raises(LabContractError, match="failure_code"):
        resumed.transition("failed")


def test_race_cartridge_binds_private_checkpoint_reference_to_a_safe_projection():
    contract = _contract()
    state = (
        RunState.create(run_id=contract.run_id, contract_sha256=contract.contract_sha256)
        .transition("queued")
        .transition("running")
        .transition("checkpointed", checkpoint_sequence=1)
    )
    projection = ReplayProjection.create(
        run_id=contract.run_id,
        contract_sha256=contract.contract_sha256,
        status="checkpointed",
        sequence=1,
        snapshot={"racers": [{"participant_id": "entrant_sol", "position": [1, 2]}]},
        events=[{"kind": "move", "tick": 12}],
    )
    cartridge = RaceCartridge.create(
        cartridge_id="cartridge_labyrinth_001",
        contract=contract,
        state=state,
        public_projection=projection,
        authority_checkpoint_sha256="f" * 64,
    )

    assert RaceCartridge.from_dict(cartridge.as_dict()) == cartridge
    assert cartridge.state == state
    assert cartridge.public_projection == projection
    assert "authority_checkpoint" not in cartridge.as_dict()["public_projection"]

    without_private_digest = copy.deepcopy(dict(cartridge.as_dict()))
    without_private_digest["authority_checkpoint_sha256"] = None
    without_private_digest.pop("cartridge_sha256")
    with pytest.raises(LabContractError, match="private checkpoint digest"):
        RaceCartridge._from_body(without_private_digest)

    unsafe = copy.deepcopy(dict(cartridge.as_dict()))
    unsafe["public_projection"]["snapshot"] = {"navigation_memory": {"x": 2}}
    unsafe.pop("cartridge_sha256")
    with pytest.raises(LabContractError, match="protected"):
        RaceCartridge._from_body(unsafe)
