"""Canonical, public metadata for composing existing Godot sandbox primitives.

This module is a manifest boundary, never a gameplay implementation.  Primitive records name
capabilities already owned by Godot.  Recipes can describe how those capabilities fit together,
but only the frozen operator action course recipe is executable.  Every other composition is
draft metadata until a separately reviewed Godot authority and protocol binding exists.

The public projections deliberately contain stable logical identifiers rather than source-tree
paths.  Moving a Godot script therefore cannot silently change a recipe hash or teach the Lab to
invent authority outside the simulation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from ..protocol import canonical_json_bytes, canonical_sha256, strict_json_loads
from .contracts import assert_public_projection_safe

SANDBOX_PRIMITIVE_SCHEMA_VERSION = "worldeval/sandbox-primitive/1"
SANDBOX_RECIPE_SCHEMA_VERSION = "worldeval/sandbox-recipe/1"
SANDBOX_MANIFEST_SCHEMA_VERSION = "worldeval/sandbox-manifest/1"

SandboxRecipeLifecycle = Literal["canonical", "draft"]

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
_PROTOCOL_VERSION = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILESYSTEM_MARKERS = (
    "/Users/",
    "/home/",
    "/workspace/",
    "file://",
    "\\",
)

_EXECUTABLE_RECIPE_ID = "operator-action-course-sandbox-v1"
_EXECUTABLE_TASK_ID = "operator-action-course-v0"
_EXECUTABLE_PROTOCOL_VERSION = "llm-controller/0.2.0"


class SandboxManifestError(ValueError):
    """A primitive, recipe, or public sandbox projection is invalid."""


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SandboxManifestError(f"{label} fields differ")
    return value


def _identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise SandboxManifestError(f"{field_name} is invalid")
    return value


def _protocol_version(value: object) -> str:
    if (
        not isinstance(value, str)
        or _PROTOCOL_VERSION.fullmatch(value) is None
        or value.startswith(("/", "."))
        or ".." in value
        or "\\" in value
    ):
        raise SandboxManifestError("protocol_version is invalid")
    return value


def _public_text(value: object, *, field_name: str, maximum_bytes: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value.encode("utf-8")) > maximum_bytes
        or value.startswith(("/", "~/"))
        or ".." in value
        or any(marker in value for marker in _FILESYSTEM_MARKERS)
    ):
        raise SandboxManifestError(f"{field_name} is not safe public text")
    try:
        assert_public_projection_safe(value, path=field_name)
    except ValueError as error:
        raise SandboxManifestError(f"{field_name} is not safe public text") from error
    return value


def _identifier_tuple(
    value: object,
    *,
    field_name: str,
    allow_empty: bool,
    require_sorted: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise SandboxManifestError(f"{field_name} must be a sequence")
    identifiers = tuple(_identifier(item, field_name=f"{field_name} item") for item in value)
    if not allow_empty and not identifiers:
        raise SandboxManifestError(f"{field_name} must not be empty")
    if len(set(identifiers)) != len(identifiers):
        raise SandboxManifestError(f"{field_name} must be unique")
    if require_sorted and identifiers != tuple(sorted(identifiers)):
        raise SandboxManifestError(f"{field_name} must be sorted")
    return identifiers


def _canonical_copy(value: object) -> object:
    """Return an independent canonical-JSON copy of already-safe public data."""

    try:
        assert_public_projection_safe(value, path="sandbox")
        return strict_json_loads(canonical_json_bytes(value))
    except (TypeError, ValueError) as error:
        raise SandboxManifestError("sandbox projection is not canonical public JSON") from error


@dataclass(frozen=True)
class SandboxPrimitiveSpec:
    """One stable logical capability already implemented by the Godot authority."""

    id: str
    title: str
    summary: str
    composition_rank: int
    dependencies: tuple[str, ...]
    capabilities: tuple[str, ...]
    authority_owner: Literal["godot"] = "godot"
    schema_version: str = SANDBOX_PRIMITIVE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SANDBOX_PRIMITIVE_SCHEMA_VERSION:
            raise SandboxManifestError("sandbox primitive schema is unsupported")
        _identifier(self.id, field_name="primitive id")
        _public_text(self.title, field_name="primitive title", maximum_bytes=96)
        _public_text(self.summary, field_name="primitive summary", maximum_bytes=480)
        if (
            isinstance(self.composition_rank, bool)
            or not isinstance(self.composition_rank, int)
            or not 0 <= self.composition_rank <= 10_000
        ):
            raise SandboxManifestError("primitive composition_rank is invalid")
        if not isinstance(self.dependencies, tuple):
            raise SandboxManifestError("primitive dependencies must be an immutable tuple")
        dependencies = _identifier_tuple(
            self.dependencies,
            field_name="primitive dependencies",
            allow_empty=True,
        )
        if self.id in dependencies:
            raise SandboxManifestError("primitive cannot depend on itself")
        if not isinstance(self.capabilities, tuple):
            raise SandboxManifestError("primitive capabilities must be an immutable tuple")
        _identifier_tuple(
            self.capabilities,
            field_name="primitive capabilities",
            allow_empty=False,
        )
        if self.authority_owner != "godot":
            raise SandboxManifestError("sandbox primitive authority must be Godot")

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "composition_rank": self.composition_rank,
            "dependencies": list(self.dependencies),
            "capabilities": list(self.capabilities),
            "authority_owner": self.authority_owner,
        }

    @property
    def spec_sha256(self) -> str:
        return canonical_sha256(self._hash_body())

    def public_dict(self) -> dict[str, object]:
        payload = {**self._hash_body(), "spec_sha256": self.spec_sha256}
        copied = _canonical_copy(payload)
        if not isinstance(copied, dict):  # pragma: no cover - canonical JSON preserves objects.
            raise SandboxManifestError("primitive projection is not an object")
        return copied

    def canonical_public_bytes(self) -> bytes:
        return canonical_json_bytes(self.public_dict())

    @classmethod
    def from_dict(cls, value: object) -> SandboxPrimitiveSpec:
        parsed = _exact_mapping(
            value,
            fields=frozenset(
                {
                    "schema_version",
                    "id",
                    "title",
                    "summary",
                    "composition_rank",
                    "dependencies",
                    "capabilities",
                    "authority_owner",
                    "spec_sha256",
                }
            ),
            label="sandbox primitive",
        )
        expected_hash = parsed["spec_sha256"]
        if not isinstance(expected_hash, str) or _SHA256.fullmatch(expected_hash) is None:
            raise SandboxManifestError("primitive spec_sha256 is invalid")
        primitive = cls(
            schema_version=parsed["schema_version"],  # type: ignore[arg-type]
            id=parsed["id"],  # type: ignore[arg-type]
            title=parsed["title"],  # type: ignore[arg-type]
            summary=parsed["summary"],  # type: ignore[arg-type]
            composition_rank=parsed["composition_rank"],  # type: ignore[arg-type]
            dependencies=_identifier_tuple(
                parsed["dependencies"],
                field_name="primitive dependencies",
                allow_empty=True,
            ),
            capabilities=_identifier_tuple(
                parsed["capabilities"],
                field_name="primitive capabilities",
                allow_empty=False,
            ),
            authority_owner=parsed["authority_owner"],  # type: ignore[arg-type]
        )
        if primitive.spec_sha256 != expected_hash:
            raise SandboxManifestError("primitive spec_sha256 does not match")
        return primitive


def _primitive_index(
    primitives: Sequence[SandboxPrimitiveSpec],
) -> dict[str, SandboxPrimitiveSpec]:
    if not isinstance(primitives, (tuple, list)) or not primitives:
        raise SandboxManifestError("primitive registry must not be empty")
    index: dict[str, SandboxPrimitiveSpec] = {}
    ranks: set[int] = set()
    for primitive in primitives:
        if not isinstance(primitive, SandboxPrimitiveSpec):
            raise SandboxManifestError("primitive registry contains an invalid record")
        if primitive.id in index:
            raise SandboxManifestError("primitive registry contains duplicate ids")
        if primitive.composition_rank in ranks:
            raise SandboxManifestError("primitive registry contains duplicate ranks")
        index[primitive.id] = primitive
        ranks.add(primitive.composition_rank)
    for primitive in primitives:
        unknown = sorted(set(primitive.dependencies) - set(index))
        if unknown:
            raise SandboxManifestError(
                f"primitive {primitive.id} has unknown dependencies: {', '.join(unknown)}"
            )
    return index


def resolve_sandbox_primitive_order(
    primitive_ids: Sequence[str],
    *,
    primitives: Sequence[SandboxPrimitiveSpec] | None = None,
) -> tuple[str, ...]:
    """Validate a dependency-closed selection and return a stable topological order."""

    registry = SANDBOX_PRIMITIVES if primitives is None else primitives
    index = _primitive_index(registry)
    selected = _identifier_tuple(
        primitive_ids,
        field_name="recipe primitive_ids",
        allow_empty=False,
        require_sorted=False,
    )
    selected_set = set(selected)
    unknown = sorted(selected_set - set(index))
    if unknown:
        raise SandboxManifestError(f"recipe references unknown primitives: {', '.join(unknown)}")
    missing_dependencies = sorted(
        {
            dependency
            for primitive_id in selected_set
            for dependency in index[primitive_id].dependencies
            if dependency not in selected_set
        }
    )
    if missing_dependencies:
        raise SandboxManifestError(
            "recipe omits required dependencies: " + ", ".join(missing_dependencies)
        )

    remaining_dependencies = {
        primitive_id: set(index[primitive_id].dependencies) for primitive_id in selected_set
    }
    resolved: list[str] = []
    while remaining_dependencies:
        ready = sorted(
            (
                primitive_id
                for primitive_id, dependencies in remaining_dependencies.items()
                if not dependencies
            ),
            key=lambda primitive_id: (
                index[primitive_id].composition_rank,
                primitive_id,
            ),
        )
        if not ready:
            cycle_ids = ", ".join(sorted(remaining_dependencies))
            raise SandboxManifestError(f"primitive dependency cycle detected: {cycle_ids}")
        primitive_id = ready[0]
        resolved.append(primitive_id)
        del remaining_dependencies[primitive_id]
        for dependencies in remaining_dependencies.values():
            dependencies.discard(primitive_id)
    return tuple(resolved)


@dataclass(frozen=True)
class SandboxAuthorityBinding:
    """The only fields needed to identify an existing executable Godot authority."""

    task_id: str
    protocol_version: str
    authority_owner: Literal["godot"] = "godot"

    def __post_init__(self) -> None:
        _identifier(self.task_id, field_name="authority task_id")
        _protocol_version(self.protocol_version)
        if self.authority_owner != "godot":
            raise SandboxManifestError("sandbox recipe authority must be Godot")

    def public_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "protocol_version": self.protocol_version,
            "authority_owner": self.authority_owner,
        }

    @classmethod
    def from_dict(cls, value: object) -> SandboxAuthorityBinding:
        parsed = _exact_mapping(
            value,
            fields=frozenset({"task_id", "protocol_version", "authority_owner"}),
            label="sandbox authority binding",
        )
        return cls(
            task_id=parsed["task_id"],  # type: ignore[arg-type]
            protocol_version=parsed["protocol_version"],  # type: ignore[arg-type]
            authority_owner=parsed["authority_owner"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class SandboxRecipeSpec:
    """An immutable primitive composition, executable only when hash-bound to Godot."""

    id: str
    title: str
    summary: str
    lifecycle: SandboxRecipeLifecycle
    executable: bool
    primitive_ids: tuple[str, ...]
    composition_order: tuple[str, ...]
    authority_binding: SandboxAuthorityBinding | None
    schema_version: str = SANDBOX_RECIPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SANDBOX_RECIPE_SCHEMA_VERSION:
            raise SandboxManifestError("sandbox recipe schema is unsupported")
        _identifier(self.id, field_name="recipe id")
        _public_text(self.title, field_name="recipe title", maximum_bytes=120)
        _public_text(self.summary, field_name="recipe summary", maximum_bytes=600)
        if self.lifecycle not in ("canonical", "draft"):
            raise SandboxManifestError("sandbox recipe lifecycle is unsupported")
        if not isinstance(self.executable, bool):
            raise SandboxManifestError("sandbox recipe executable must be boolean")
        if not isinstance(self.primitive_ids, tuple):
            raise SandboxManifestError("recipe primitive_ids must be an immutable tuple")
        primitive_ids = _identifier_tuple(
            self.primitive_ids,
            field_name="recipe primitive_ids",
            allow_empty=False,
        )
        if not isinstance(self.composition_order, tuple):
            raise SandboxManifestError("recipe composition_order must be an immutable tuple")
        _identifier_tuple(
            self.composition_order,
            field_name="recipe composition_order",
            allow_empty=False,
            require_sorted=False,
        )
        expected_order = resolve_sandbox_primitive_order(primitive_ids)
        if tuple(self.composition_order) != expected_order:
            raise SandboxManifestError(
                "recipe composition_order does not match deterministic dependency order"
            )

        if self.executable:
            if (
                self.lifecycle != "canonical"
                or self.id != _EXECUTABLE_RECIPE_ID
                or not isinstance(self.authority_binding, SandboxAuthorityBinding)
                or self.authority_binding.task_id != _EXECUTABLE_TASK_ID
                or self.authority_binding.protocol_version != _EXECUTABLE_PROTOCOL_VERSION
                or primitive_ids != tuple(sorted(primitive.id for primitive in SANDBOX_PRIMITIVES))
            ):
                raise SandboxManifestError(
                    "only the canonical operator action course recipe is executable"
                )
        elif (
            self.lifecycle != "draft"
            or self.authority_binding is not None
            or self.id == _EXECUTABLE_RECIPE_ID
        ):
            raise SandboxManifestError("non-executable custom recipes must be unbound drafts")

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "lifecycle": self.lifecycle,
            "executable": self.executable,
            "primitive_ids": list(self.primitive_ids),
            "composition_order": list(self.composition_order),
            "authority_binding": (
                None if self.authority_binding is None else self.authority_binding.public_dict()
            ),
        }

    @property
    def recipe_sha256(self) -> str:
        return canonical_sha256(self._hash_body())

    def public_dict(self) -> dict[str, object]:
        payload = {**self._hash_body(), "recipe_sha256": self.recipe_sha256}
        copied = _canonical_copy(payload)
        if not isinstance(copied, dict):  # pragma: no cover - canonical JSON preserves objects.
            raise SandboxManifestError("recipe projection is not an object")
        return copied

    def canonical_public_bytes(self) -> bytes:
        return canonical_json_bytes(self.public_dict())

    @classmethod
    def from_dict(cls, value: object) -> SandboxRecipeSpec:
        parsed = _exact_mapping(
            value,
            fields=frozenset(
                {
                    "schema_version",
                    "id",
                    "title",
                    "summary",
                    "lifecycle",
                    "executable",
                    "primitive_ids",
                    "composition_order",
                    "authority_binding",
                    "recipe_sha256",
                }
            ),
            label="sandbox recipe",
        )
        expected_hash = parsed["recipe_sha256"]
        if not isinstance(expected_hash, str) or _SHA256.fullmatch(expected_hash) is None:
            raise SandboxManifestError("recipe recipe_sha256 is invalid")
        binding = (
            None
            if parsed["authority_binding"] is None
            else SandboxAuthorityBinding.from_dict(parsed["authority_binding"])
        )
        recipe = cls(
            schema_version=parsed["schema_version"],  # type: ignore[arg-type]
            id=parsed["id"],  # type: ignore[arg-type]
            title=parsed["title"],  # type: ignore[arg-type]
            summary=parsed["summary"],  # type: ignore[arg-type]
            lifecycle=parsed["lifecycle"],  # type: ignore[arg-type]
            executable=parsed["executable"],  # type: ignore[arg-type]
            primitive_ids=_identifier_tuple(
                parsed["primitive_ids"],
                field_name="recipe primitive_ids",
                allow_empty=False,
            ),
            composition_order=_identifier_tuple(
                parsed["composition_order"],
                field_name="recipe composition_order",
                allow_empty=False,
                require_sorted=False,
            ),
            authority_binding=binding,
        )
        if recipe.recipe_sha256 != expected_hash:
            raise SandboxManifestError("recipe recipe_sha256 does not match")
        return recipe


SANDBOX_PRIMITIVES = (
    SandboxPrimitiveSpec(
        id="event-ledger",
        title="Event ledger",
        summary=(
            "Godot records deterministic public world events with stable ordering for replay "
            "and evaluation."
        ),
        composition_rank=10,
        dependencies=(),
        capabilities=("deterministic-events", "replay-events"),
    ),
    SandboxPrimitiveSpec(
        id="orientation",
        title="Orientation",
        summary=("Godot owns participant facing and applies validated turn controls to the world."),
        composition_rank=20,
        dependencies=(),
        capabilities=("facing", "turn-control"),
    ),
    SandboxPrimitiveSpec(
        id="movement",
        title="Movement",
        summary=(
            "Godot applies bounded movement controls, collision rules, and authoritative travel."
        ),
        composition_rank=30,
        dependencies=("orientation",),
        capabilities=("collision", "locomotion"),
    ),
    SandboxPrimitiveSpec(
        id="visibility",
        title="Visibility",
        summary=(
            "Godot derives participant-visible entities and affordances from authoritative world "
            "geometry."
        ),
        composition_rank=40,
        dependencies=("orientation",),
        capabilities=("affordances", "participant-view"),
    ),
    SandboxPrimitiveSpec(
        id="interaction",
        title="Interaction",
        summary=(
            "Godot validates contextual use, cancellation, carrying, and depositing against "
            "visible affordances."
        ),
        composition_rank=50,
        dependencies=("visibility",),
        capabilities=("cancel-control", "context-actions", "item-transfer"),
    ),
    SandboxPrimitiveSpec(
        id="resources-economy",
        title="Resources and economy",
        summary=(
            "Godot owns resource quantities, gathering, inventory transfer, and spending rules."
        ),
        composition_rank=60,
        dependencies=("interaction",),
        capabilities=("gathering", "inventory", "resource-costs"),
    ),
    SandboxPrimitiveSpec(
        id="construction",
        title="Construction",
        summary=("Godot validates build affordances, resource costs, placement, and completion."),
        composition_rank=70,
        dependencies=("resources-economy",),
        capabilities=("build-control", "placement-validation"),
    ),
    SandboxPrimitiveSpec(
        id="combat",
        title="Combat",
        summary=("Godot resolves attacks, guards, abilities, hazards, health changes, and defeat."),
        composition_rank=80,
        dependencies=("orientation", "visibility"),
        capabilities=("abilities", "damage-resolution", "defence"),
    ),
    SandboxPrimitiveSpec(
        id="scoring-termination",
        title="Scoring and termination",
        summary=(
            "Godot determines progress, terminal outcomes, and authority-derived scoring facts."
        ),
        composition_rank=90,
        dependencies=("event-ledger",),
        capabilities=("outcomes", "progress", "score-facts"),
    ),
    SandboxPrimitiveSpec(
        id="checkpoint-serialization",
        title="Checkpoint serialization",
        summary=(
            "Godot seals deterministic authority checkpoints that support recovery and offline "
            "replay verification."
        ),
        composition_rank=100,
        dependencies=("event-ledger", "scoring-termination"),
        capabilities=("authority-checkpoints", "replay-verification"),
    ),
)

# Validate the complete checked-in registry at import time.  This catches duplicate ranks,
# unknown dependencies, and cycles before any API can project the manifest.
_ALL_PRIMITIVE_IDS = tuple(sorted(primitive.id for primitive in SANDBOX_PRIMITIVES))
resolve_sandbox_primitive_order(_ALL_PRIMITIVE_IDS)

OPERATOR_ACTION_COURSE_SANDBOX_RECIPE = SandboxRecipeSpec(
    id=_EXECUTABLE_RECIPE_ID,
    title="Operator action course",
    summary=(
        "A frozen executable composition bound to the existing Godot operator action course "
        "authority and controller protocol."
    ),
    lifecycle="canonical",
    executable=True,
    primitive_ids=_ALL_PRIMITIVE_IDS,
    composition_order=resolve_sandbox_primitive_order(_ALL_PRIMITIVE_IDS),
    authority_binding=SandboxAuthorityBinding(
        task_id=_EXECUTABLE_TASK_ID,
        protocol_version=_EXECUTABLE_PROTOCOL_VERSION,
    ),
)


def draft_sandbox_recipe(
    *,
    recipe_id: str,
    title: str,
    summary: str,
    primitive_ids: Sequence[str],
) -> SandboxRecipeSpec:
    """Create non-executable composition metadata from a dependency-closed selection."""

    selected = tuple(sorted(primitive_ids))
    return SandboxRecipeSpec(
        id=recipe_id,
        title=title,
        summary=summary,
        lifecycle="draft",
        executable=False,
        primitive_ids=selected,
        composition_order=resolve_sandbox_primitive_order(selected),
        authority_binding=None,
    )


def sandbox_manifest() -> dict[str, object]:
    """Return a canonical, browser-safe catalogue of primitives and admitted recipes."""

    body: dict[str, object] = {
        "schema_version": SANDBOX_MANIFEST_SCHEMA_VERSION,
        "authority_owner": "godot",
        "primitives": [primitive.public_dict() for primitive in SANDBOX_PRIMITIVES],
        "recipes": [OPERATOR_ACTION_COURSE_SANDBOX_RECIPE.public_dict()],
    }
    payload = {**body, "manifest_sha256": canonical_sha256(body)}
    copied = _canonical_copy(payload)
    if not isinstance(copied, dict):  # pragma: no cover - canonical JSON preserves objects.
        raise SandboxManifestError("sandbox manifest projection is not an object")
    return copied
