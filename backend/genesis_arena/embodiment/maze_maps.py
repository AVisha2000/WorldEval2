"""Versioned, deterministic maze maps for live Labyrinth Run episodes.

The cached showcase keeps its historical module-level fixture.  Live and benchmark episodes use
``MazeMapSpec`` instead, so every graph operation is bound to immutable, hash-checked geometry.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Literal, Mapping, Sequence

from .protocol import canonical_json_bytes

MAZE_MAP_SCHEMA = "worldarena/maze-map-spec/1"
MAZE_GENERATOR_VERSION = "recursive-backtracker-v1"
MazeDifficulty = Literal["easy", "medium", "hard", "memory_stress", "showcase"]

_HEADINGS = ((0, -1), (1, 0), (0, 1), (-1, 0))
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIFFICULTY_CONFIG: Mapping[str, tuple[int, int, int]] = {
    # dimension, deterministic extra connections, landmark count
    "easy": (15, 0, 5),
    "medium": (21, 0, 4),
    "hard": (31, 12, 2),
    "memory_stress": (41, 32, 0),
}
_LANDMARK_NAMES = (
    "amber-beacon",
    "blue-crystal",
    "broken-statue",
    "bronze-gong",
    "carved-obelisk",
    "green-brazier",
    "mossy-pillar",
    "old-well",
    "silver-arch",
    "twin-torches",
)


class MazeMapError(ValueError):
    """A maze specification or deterministic generation request is invalid."""


@dataclass(frozen=True, order=True)
class MazeLandmark:
    position: tuple[int, int]
    label: str

    def __post_init__(self) -> None:
        _validate_position(self.position, "landmark")
        if not isinstance(self.label, str) or not _SAFE_ID.fullmatch(self.label):
            raise MazeMapError("maze landmark label is invalid")

    def as_dict(self) -> dict[str, object]:
        return {"position": list(self.position), "label": self.label}

    @classmethod
    def from_dict(cls, value: object) -> MazeLandmark:
        if not isinstance(value, Mapping) or set(value) != {"position", "label"}:
            raise MazeMapError("maze landmark fields are invalid")
        return cls(_position_from_json(value["position"], "landmark"), value["label"])


@dataclass(frozen=True)
class MazeTopologyMetrics:
    width: int
    height: int
    walkable_cells: int
    edge_count: int
    shortest_path_cells: int
    dead_ends: tuple[tuple[int, int], ...]
    junctions: tuple[tuple[int, int], ...]
    cycle_rank: int
    dfs_upper_bound_cells: int
    reachable: bool

    def __post_init__(self) -> None:
        for name in (
            "width",
            "height",
            "walkable_cells",
            "edge_count",
            "shortest_path_cells",
            "cycle_rank",
            "dfs_upper_bound_cells",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MazeMapError(f"maze metric {name} is invalid")
        if self.width < 5 or self.height < 5 or not isinstance(self.reachable, bool):
            raise MazeMapError("maze dimensions or reachability are invalid")
        for name in ("dead_ends", "junctions"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or tuple(sorted(values)) != values:
                raise MazeMapError(f"maze metric {name} is not canonical")
            for value in values:
                _validate_position(value, name)
        if self.dfs_upper_bound_cells != 2 * self.edge_count:
            raise MazeMapError("maze DFS bound must equal twice the undirected edge count")

    @property
    def dead_end_count(self) -> int:
        return len(self.dead_ends)

    @property
    def junction_count(self) -> int:
        return len(self.junctions)

    def as_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "walkable_cells": self.walkable_cells,
            "edge_count": self.edge_count,
            "shortest_path_cells": self.shortest_path_cells,
            "dead_end_count": self.dead_end_count,
            "dead_ends": [list(value) for value in self.dead_ends],
            "junction_count": self.junction_count,
            "junctions": [list(value) for value in self.junctions],
            "cycle_rank": self.cycle_rank,
            "dfs_upper_bound_cells": self.dfs_upper_bound_cells,
            "reachable": self.reachable,
        }

    @classmethod
    def from_dict(cls, value: object) -> MazeTopologyMetrics:
        fields = {
            "width",
            "height",
            "walkable_cells",
            "edge_count",
            "shortest_path_cells",
            "dead_end_count",
            "dead_ends",
            "junction_count",
            "junctions",
            "cycle_rank",
            "dfs_upper_bound_cells",
            "reachable",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise MazeMapError("maze metric fields are invalid")
        dead_ends = _position_list_from_json(value["dead_ends"], "dead ends")
        junctions = _position_list_from_json(value["junctions"], "junctions")
        if value["dead_end_count"] != len(dead_ends) or value["junction_count"] != len(
            junctions
        ):
            raise MazeMapError("maze metric counts differ")
        return cls(
            width=value["width"],
            height=value["height"],
            walkable_cells=value["walkable_cells"],
            edge_count=value["edge_count"],
            shortest_path_cells=value["shortest_path_cells"],
            dead_ends=dead_ends,
            junctions=junctions,
            cycle_rank=value["cycle_rank"],
            dfs_upper_bound_cells=value["dfs_upper_bound_cells"],
            reachable=value["reachable"],
        )


@dataclass(frozen=True)
class MazeMapSpec:
    map_id: str
    generator_version: str
    seed: int
    difficulty: MazeDifficulty
    rows: tuple[str, ...]
    start: tuple[int, int]
    exit: tuple[int, int]
    landmarks: tuple[MazeLandmark, ...]
    metrics: MazeTopologyMetrics
    map_sha256: str = ""
    schema_version: str = MAZE_MAP_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != MAZE_MAP_SCHEMA:
            raise MazeMapError("maze map schema is invalid")
        if not isinstance(self.map_id, str) or not _SAFE_ID.fullmatch(self.map_id):
            raise MazeMapError("maze map id is invalid")
        if not isinstance(self.generator_version, str) or not _SAFE_ID.fullmatch(
            self.generator_version
        ):
            raise MazeMapError("maze generator version is invalid")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise MazeMapError("maze seed is invalid")
        if self.difficulty not in (*_DIFFICULTY_CONFIG, "showcase"):
            raise MazeMapError("maze difficulty is invalid")
        _validate_rows(self.rows)
        _validate_position(self.start, "start")
        _validate_position(self.exit, "exit")
        if self.start == self.exit:
            raise MazeMapError("maze start and exit must differ")
        if not isinstance(self.landmarks, tuple) or tuple(sorted(self.landmarks)) != self.landmarks:
            raise MazeMapError("maze landmarks are not canonical")
        graph = graph_from_rows(self.rows)
        if self.start not in graph or self.exit not in graph:
            raise MazeMapError("maze start or exit is blocked")
        if self.rows[self.start[1]][self.start[0]] != "S" or self.rows[self.exit[1]][
            self.exit[0]
        ] != "E":
            raise MazeMapError("maze start or exit marker differs")
        if sum(row.count("S") for row in self.rows) != 1 or sum(
            row.count("E") for row in self.rows
        ) != 1:
            raise MazeMapError("maze must contain one start and one exit")
        landmark_positions = [value.position for value in self.landmarks]
        if len(set(landmark_positions)) != len(landmark_positions) or any(
            position not in graph for position in landmark_positions
        ):
            raise MazeMapError("maze landmark placement is invalid")
        expected_metrics = analyze_maze_rows(self.rows, self.start, self.exit)
        if self.metrics != expected_metrics or not expected_metrics.reachable:
            raise MazeMapError("maze topology metrics differ from its rows")
        if self.difficulty in _DIFFICULTY_CONFIG:
            dimension, cycle_rank, landmark_count = _DIFFICULTY_CONFIG[self.difficulty]
            if (
                expected_metrics.width != dimension
                or expected_metrics.height != dimension
                or expected_metrics.cycle_rank != cycle_rank
                or len(self.landmarks) != landmark_count
            ):
                raise MazeMapError("maze difficulty definition differs")
        expected_hash = hashlib.sha256(canonical_json_bytes(self._hash_body())).hexdigest()
        if self.map_sha256:
            if not _SHA256.fullmatch(self.map_sha256) or self.map_sha256 != expected_hash:
                raise MazeMapError("maze map hash differs")
        else:
            object.__setattr__(self, "map_sha256", expected_hash)

    @property
    def landmark_map(self) -> Mapping[tuple[int, int], str]:
        return {value.position: value.label for value in self.landmarks}

    @property
    def participant_call_budget(self) -> int:
        return self.metrics.dfs_upper_bound_cells

    def graph(self) -> Mapping[tuple[int, int], tuple[tuple[int, int], ...]]:
        return graph_from_rows(self.rows)

    def _hash_body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "map_id": self.map_id,
            "generator_version": self.generator_version,
            "seed": self.seed,
            "difficulty": self.difficulty,
            "rows": list(self.rows),
            "start": list(self.start),
            "exit": list(self.exit),
            "landmarks": [value.as_dict() for value in self.landmarks],
            "metrics": self.metrics.as_dict(),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self._hash_body(), "map_sha256": self.map_sha256}

    @classmethod
    def from_dict(cls, value: object) -> MazeMapSpec:
        fields = {
            "schema_version",
            "map_id",
            "generator_version",
            "seed",
            "difficulty",
            "rows",
            "start",
            "exit",
            "landmarks",
            "metrics",
            "map_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise MazeMapError("maze map fields are invalid")
        rows = value["rows"]
        landmarks = value["landmarks"]
        if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            raise MazeMapError("maze map rows are invalid")
        if not isinstance(landmarks, list):
            raise MazeMapError("maze landmarks are invalid")
        return cls(
            map_id=value["map_id"],
            generator_version=value["generator_version"],
            seed=value["seed"],
            difficulty=value["difficulty"],
            rows=tuple(rows),
            start=_position_from_json(value["start"], "start"),
            exit=_position_from_json(value["exit"], "exit"),
            landmarks=tuple(MazeLandmark.from_dict(item) for item in landmarks),
            metrics=MazeTopologyMetrics.from_dict(value["metrics"]),
            map_sha256=value["map_sha256"],
            schema_version=value["schema_version"],
        )


def graph_from_rows(
    rows: Sequence[str],
) -> Mapping[tuple[int, int], tuple[tuple[int, int], ...]]:
    return _cached_graph_from_rows(tuple(rows))


@lru_cache(maxsize=256)
def _cached_graph_from_rows(
    rows: tuple[str, ...],
) -> Mapping[tuple[int, int], tuple[tuple[int, int], ...]]:
    walkable = {
        (x, y) for y, row in enumerate(rows) for x, symbol in enumerate(row) if symbol != "#"
    }
    return MappingProxyType(
        {
            cell: tuple(
                (cell[0] + dx, cell[1] + dy)
                for dx, dy in _HEADINGS
                if (cell[0] + dx, cell[1] + dy) in walkable
            )
            for cell in sorted(walkable)
        }
    )


def analyze_maze_rows(
    rows: Sequence[str], start: tuple[int, int], exit_cell: tuple[int, int]
) -> MazeTopologyMetrics:
    graph = graph_from_rows(rows)
    if start not in graph or exit_cell not in graph:
        raise MazeMapError("maze analysis endpoints are invalid")
    distances = _distances(graph, start)
    edge_count = sum(map(len, graph.values())) // 2
    dead_ends = tuple(sorted(cell for cell, neighbours in graph.items() if len(neighbours) == 1))
    junctions = tuple(sorted(cell for cell, neighbours in graph.items() if len(neighbours) >= 3))
    return MazeTopologyMetrics(
        width=len(rows[0]),
        height=len(rows),
        walkable_cells=len(graph),
        edge_count=edge_count,
        shortest_path_cells=distances.get(exit_cell, 0),
        dead_ends=dead_ends,
        junctions=junctions,
        cycle_rank=max(0, edge_count - len(graph) + 1),
        dfs_upper_bound_cells=2 * edge_count,
        reachable=len(distances) == len(graph),
    )


def generate_maze_map(*, seed: int, difficulty: str, map_id: str) -> MazeMapSpec:
    """Generate one stable benchmark maze from a recursive-backtracker base."""

    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise MazeMapError("maze seed is invalid")
    if difficulty not in _DIFFICULTY_CONFIG:
        raise MazeMapError("maze generator difficulty is invalid")
    if not isinstance(map_id, str) or not _SAFE_ID.fullmatch(map_id):
        raise MazeMapError("maze map id is invalid")
    dimension, extra_connections, landmark_count = _DIFFICULTY_CONFIG[difficulty]
    rows = [["#" for _ in range(dimension)] for _ in range(dimension)]
    logical = tuple(
        (x, y) for y in range(1, dimension, 2) for x in range(1, dimension, 2)
    )
    for x, y in logical:
        rows[y][x] = "."
    rng = random.Random(seed)
    start_cell = logical[rng.randrange(len(logical))]
    visited = {start_cell}
    stack = [start_cell]
    while stack:
        current = stack[-1]
        candidates = []
        for dx, dy in _HEADINGS:
            candidate = (current[0] + 2 * dx, current[1] + 2 * dy)
            if candidate in logical and candidate not in visited:
                candidates.append(candidate)
        if not candidates:
            stack.pop()
            continue
        candidate = candidates[rng.randrange(len(candidates))]
        rows[(current[1] + candidate[1]) // 2][(current[0] + candidate[0]) // 2] = "."
        visited.add(candidate)
        stack.append(candidate)

    loop_walls = []
    for y in range(1, dimension - 1):
        for x in range(1, dimension - 1):
            if rows[y][x] != "#":
                continue
            horizontal = x % 2 == 0 and y % 2 == 1
            vertical = x % 2 == 1 and y % 2 == 0
            if horizontal and rows[y][x - 1] != "#" and rows[y][x + 1] != "#":
                loop_walls.append((x, y))
            elif vertical and rows[y - 1][x] != "#" and rows[y + 1][x] != "#":
                loop_walls.append((x, y))
    rng.shuffle(loop_walls)
    if len(loop_walls) < extra_connections:
        raise MazeMapError("maze generator cannot add requested connections")
    for x, y in loop_walls[:extra_connections]:
        rows[y][x] = "."

    unmarked = tuple("".join(row) for row in rows)
    graph = graph_from_rows(unmarked)
    start, exit_cell = _diameter_endpoints(graph)
    rows[start[1]][start[0]] = "S"
    rows[exit_cell[1]][exit_cell[0]] = "E"
    final_rows = tuple("".join(row) for row in rows)
    metrics = analyze_maze_rows(final_rows, start, exit_cell)
    if not metrics.reachable or metrics.cycle_rank != extra_connections:
        raise MazeMapError("generated maze topology differs")
    landmark_positions = _distributed_landmarks(
        graph, start=start, exit_cell=exit_cell, count=landmark_count
    )
    landmarks = tuple(
        sorted(
            MazeLandmark(position, f"{_LANDMARK_NAMES[index % len(_LANDMARK_NAMES)]}-{index + 1}")
            for index, position in enumerate(landmark_positions)
        )
    )
    return MazeMapSpec(
        map_id=map_id,
        generator_version=MAZE_GENERATOR_VERSION,
        seed=seed,
        difficulty=difficulty,
        rows=final_rows,
        start=start,
        exit=exit_cell,
        landmarks=landmarks,
        metrics=metrics,
    )


def default_maze_map_spec() -> MazeMapSpec:
    """Return the immutable live equivalent of the historical showcase maze."""

    from .labyrinth_run import LANDMARKS, MAZE_ROWS, _start_exit

    start, exit_cell = _start_exit()
    return MazeMapSpec(
        map_id="trio-maze-race-v0",
        generator_version="legacy-fixed-v1",
        seed=0,
        difficulty="showcase",
        rows=tuple(MAZE_ROWS),
        start=start,
        exit=exit_cell,
        landmarks=tuple(
            sorted(MazeLandmark(position, label) for position, label in LANDMARKS.items())
        ),
        metrics=analyze_maze_rows(MAZE_ROWS, start, exit_cell),
    )


def _diameter_endpoints(
    graph: Mapping[tuple[int, int], Sequence[tuple[int, int]]]
) -> tuple[tuple[int, int], tuple[int, int]]:
    best_distance = -1
    best_pair: tuple[tuple[int, int], tuple[int, int]] | None = None
    for source in sorted(graph):
        for target, distance in _distances(graph, source).items():
            if target <= source:
                continue
            pair = (source, target)
            if distance > best_distance or (distance == best_distance and pair < best_pair):
                best_distance = distance
                best_pair = pair
    if best_pair is None:
        raise MazeMapError("maze has no diameter")
    return best_pair


def _distributed_landmarks(
    graph: Mapping[tuple[int, int], Sequence[tuple[int, int]]],
    *,
    start: tuple[int, int],
    exit_cell: tuple[int, int],
    count: int,
) -> tuple[tuple[int, int], ...]:
    selected: list[tuple[int, int]] = []
    anchors = [start, exit_cell]
    distance_maps = {anchor: _distances(graph, anchor) for anchor in anchors}
    for _ in range(count):
        choices = [cell for cell in graph if cell not in {start, exit_cell, *selected}]
        candidate = min(
            choices,
            key=lambda cell: (
                -min(distance_maps[anchor][cell] for anchor in anchors),
                cell,
            ),
        )
        selected.append(candidate)
        anchors.append(candidate)
        distance_maps[candidate] = _distances(graph, candidate)
    return tuple(selected)


def _distances(
    graph: Mapping[tuple[int, int], Sequence[tuple[int, int]]], start: tuple[int, int]
) -> Mapping[tuple[int, int], int]:
    queue = deque([start])
    output = {start: 0}
    while queue:
        current = queue.popleft()
        for candidate in graph[current]:
            if candidate not in output:
                output[candidate] = output[current] + 1
                queue.append(candidate)
    return output


def _validate_rows(rows: object) -> None:
    if (
        not isinstance(rows, tuple)
        or len(rows) < 5
        or len(rows) % 2 == 0
        or not all(isinstance(row, str) for row in rows)
        or len({len(row) for row in rows}) != 1
        or len(rows[0]) < 5
        or len(rows[0]) % 2 == 0
        or any(set(row) - {"#", ".", "S", "E"} for row in rows)
        or set(rows[0]) != {"#"}
        or set(rows[-1]) != {"#"}
        or any(row[0] != "#" or row[-1] != "#" for row in rows)
    ):
        raise MazeMapError("maze rows are invalid")


def _validate_position(value: object, label: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        raise MazeMapError(f"maze {label} position is invalid")


def _position_from_json(value: object, label: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise MazeMapError(f"maze {label} position is invalid")
    position = tuple(value)
    _validate_position(position, label)
    return position


def _position_list_from_json(value: object, label: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        raise MazeMapError(f"maze {label} positions are invalid")
    output = tuple(_position_from_json(item, label) for item in value)
    if tuple(sorted(output)) != output:
        raise MazeMapError(f"maze {label} positions are not canonical")
    return output


__all__ = [
    "MAZE_GENERATOR_VERSION",
    "MAZE_MAP_SCHEMA",
    "MazeDifficulty",
    "MazeLandmark",
    "MazeMapError",
    "MazeMapSpec",
    "MazeTopologyMetrics",
    "analyze_maze_rows",
    "default_maze_map_spec",
    "generate_maze_map",
    "graph_from_rows",
]
