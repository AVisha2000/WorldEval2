class_name EmbodimentTrioMazeMap
extends RefCounted

const TASK_ID := "trio-maze-race-v0"
const PROTOCOL_VERSION := "maze-task-plan-v1"
const TILE_MT := 1000
const ROWS := [
	"###############",
	"#......E......#",
	"#.###.#.#######",
	"#.#...#.#.....#",
	"###.###.#.###.#",
	"#...#...#...#.#",
	"#.#########.#.#",
	"#.#...#...#.#.#",
	"#.#.#.#.#.#.#.#",
	"#...#...#...#.#",
	"#.###########.#",
	"#.........#...#",
	"#####.#####.#.#",
	"#.....#S....#.#",
	"###############",
]
const DIRECTIONS := [Vector2i.UP, Vector2i.RIGHT, Vector2i.DOWN, Vector2i.LEFT]
const LANDMARKS := {
	Vector2i(13, 11): "twin-torches",
	Vector2i(1, 9): "old-well",
	Vector2i(5, 11): "broken-statue",
	Vector2i(5, 1): "blue-crystal",
	Vector2i(7, 1): "mossy-pillar",
}


static func start_cell() -> Vector2i:
	return Vector2i(7, 13)


static func exit_cell() -> Vector2i:
	return Vector2i(7, 1)


static func is_walkable(cell: Vector2i) -> bool:
	return cell.y >= 0 and cell.y < ROWS.size() and cell.x >= 0 \
		and cell.x < ROWS[cell.y].length() and ROWS[cell.y][cell.x] != "#"


static func neighbours(cell: Vector2i) -> Array[Vector2i]:
	var values: Array[Vector2i] = []
	for delta: Vector2i in DIRECTIONS:
		if is_walkable(cell + delta):
			values.append(cell + delta)
	return values


static func location_type(cell: Vector2i) -> String:
	if cell == exit_cell():
		return "exit"
	var degree := neighbours(cell).size()
	if degree == 1:
		return "entrance" if cell == start_cell() else "dead_end"
	if degree >= 3:
		return "junction"
	return "corridor"


static func landmark(cell: Vector2i) -> String:
	return str(LANDMARKS.get(cell, "none"))


static func available_passages(cell: Vector2i, heading: int) -> Array[String]:
	var values: Array[String] = []
	for relative: int in 4:
		if is_walkable(cell + DIRECTIONS[posmod(heading + relative, 4)]):
			values.append(["forward", "right", "back", "left"][relative])
	return values


static func corridor_path(cell: Vector2i, heading: int, choice: String) -> Array[Vector2i]:
	var relative: int = ["forward", "right", "back", "left"].find(choice)
	if relative < 0:
		return []
	var first: Vector2i = cell + DIRECTIONS[posmod(heading + relative, 4)]
	if not is_walkable(first):
		return []
	var path: Array[Vector2i] = [cell, first]
	var previous: Vector2i = cell
	var current: Vector2i = first
	while current != exit_cell() and neighbours(current).size() == 2:
		var following: Array[Vector2i] = neighbours(current)
		following.erase(previous)
		previous = current
		current = following[0]
		path.append(current)
	return path


static func shortest_path_cells() -> int:
	var frontier: Array[Vector2i] = [start_cell()]
	var distance := {start_cell(): 0}
	var cursor := 0
	while cursor < frontier.size():
		var current := frontier[cursor]
		cursor += 1
		if current == exit_cell():
			return int(distance[current])
		for candidate: Vector2i in neighbours(current):
			if not distance.has(candidate):
				distance[candidate] = int(distance[current]) + 1
				frontier.append(candidate)
	return -1


static func fixture_invariants() -> Dictionary:
	var walkable := 0
	var junctions := 0
	var dead_ends := 0
	for y: int in ROWS.size():
		for x: int in ROWS[y].length():
			var cell := Vector2i(x, y)
			if not is_walkable(cell):
				continue
			walkable += 1
			var degree := neighbours(cell).size()
			# Topology metrics count the exit when it is also a junction, while observations
			# still present that cell as the semantically stronger `exit` location type.
			junctions += 1 if degree >= 3 else 0
			dead_ends += 1 if degree == 1 and cell != start_cell() else 0
	return {
		"walkable_cells": walkable,
		"junctions": junctions,
		"dead_ends": dead_ends,
		"shortest_path_cells": shortest_path_cells(),
	}
