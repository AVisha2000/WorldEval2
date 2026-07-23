"""Public, versioned metadata used by the WorldEval Lab product surface.

The Lab registry deliberately describes existing environment capabilities without creating a new
authority or provider boundary.  Product/API layers can consume its canonical public projection
when they are ready to expose a catalogue or individual game guide pages.
"""

from .contracts import (
    RACE_CARTRIDGE_SCHEMA_VERSION,
    REPLAY_PROJECTION_SCHEMA_VERSION,
    RUN_CONTRACT_SCHEMA_VERSION,
    RUN_STATE_SCHEMA_VERSION,
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
from .games import (
    GAME_CATALOG,
    GAME_CATALOG_SCHEMA_VERSION,
    GAME_SPEC_SCHEMA_VERSION,
    GameAgentInterface,
    GameCatalog,
    GameCatalogError,
    GameConfigurationControl,
    GameMetric,
    GameMode,
    GameSafety,
    GameScoring,
    GameSpec,
    game_spec,
)

__all__ = [
    "GAME_CATALOG",
    "GAME_CATALOG_SCHEMA_VERSION",
    "GAME_SPEC_SCHEMA_VERSION",
    "GameAgentInterface",
    "GameCatalog",
    "GameCatalogError",
    "GameConfigurationControl",
    "GameMetric",
    "GameMode",
    "GameSafety",
    "GameScoring",
    "GameSpec",
    "LabContractError",
    "RACE_CARTRIDGE_SCHEMA_VERSION",
    "REPLAY_PROJECTION_SCHEMA_VERSION",
    "RUN_CONTRACT_SCHEMA_VERSION",
    "RUN_STATE_SCHEMA_VERSION",
    "RaceCartridge",
    "ReplayProjection",
    "RunContract",
    "RunEntrant",
    "RunLifecycle",
    "RunMode",
    "RunState",
    "assert_public_projection_safe",
    "game_spec",
]
