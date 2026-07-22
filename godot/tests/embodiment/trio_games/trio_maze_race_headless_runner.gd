extends SceneTree

const MazeMap := preload("res://scripts/embodiment/trio_games/trio_maze_map.gd")
const Authority := preload("res://scripts/embodiment/trio_games/trio_maze_race_authority.gd")
const Broadcast := preload("res://scripts/embodiment/presentation/maze/trio_maze_broadcast_scene.gd")
const Codec := preload("res://scripts/embodiment/transport/embodiment_frame_codec.gd")


func _init() -> void:
	var invariants := MazeMap.fixture_invariants()
	if invariants != {"walkable_cells": 97, "junctions": 5, "dead_ends": 6, "shortest_path_cells": 60}:
		_fail("legacy fixture invariants: %s" % str(invariants))
		return
	var authority := Authority.new()
	if not authority.configure({
		"task_id": MazeMap.TASK_ID,
		"protocol_version": MazeMap.PROTOCOL_VERSION,
		"episode_id": "ep_maze_headless",
	}).is_empty():
		_fail("authority configure")
		return
	var observation := authority.observe("participant_0")
	if observation.available_passages != ["right"] or observation.has("position") \
			or observation.has("competitors") or observation.has("standings"):
		_fail("authority observation")
		return
	var receipt := authority.submit_decision({
		"protocol_version": MazeMap.PROTOCOL_VERSION,
		"episode_id": "ep_maze_headless",
		"observation_id": observation.observation_id,
		"participant_id": "participant_0",
		"passage_choice": "right",
		"scratchpad_update": "start:right",
	})
	if not receipt.accepted or receipt.path_cells != 8:
		_fail("authority decision")
		return
	for _tick: int in 32:
		authority.step_tick()
	if authority.public_snapshot().racers[0].distance_cells != 8:
		_fail("authority movement")
		return
	var broadcast := Broadcast.new()
	root.add_child(broadcast)
	if broadcast == null or not broadcast.configure_replay(_broadcast_replay()):
		_fail("broadcast configure")
		return
	if not broadcast.apply_race_time(4000):
		_fail("broadcast time")
		return
	var broadcast_snapshot := broadcast.snapshot_copy()
	if broadcast_snapshot.visible_cell_counts != {
		"participant_0": 2, "participant_1": 2, "participant_2": 2,
	} or broadcast_snapshot.trail_cell_counts != {
		"participant_0": 1, "participant_1": 1, "participant_2": 1,
	}:
		_fail("broadcast overlays")
		return
	if broadcast_snapshot.map_dimensions != [15, 15] or broadcast_snapshot.maximum_ticks != 600 \
			or not is_equal_approx(float(broadcast_snapshot.tile_scale), 1.0) \
			or not is_equal_approx(float(broadcast_snapshot.lane_spacing), 18.0):
		_fail("legacy broadcast geometry")
		return
	var cached_parse := Codec.parse_canonical(FileAccess.get_file_as_bytes(
		"res://showcases/labyrinth_run/labyrinth-run-demo.replay.json"
	), 4 * 1024 * 1024)
	var cached_value: Variant = cached_parse.get("value")
	if not bool(cached_parse.get("ok", false)) or not cached_value is Dictionary:
		_fail("cached showcase parse")
		return
	if not broadcast.configure_replay(cached_value):
		_fail("cached showcase replay compatibility")
		return
	var cached_snapshot := broadcast.snapshot_copy()
	if cached_snapshot.map_dimensions != [15, 15] or cached_snapshot.maximum_ticks != 600 \
			or cached_snapshot.landmark_count != 5:
		_fail("cached showcase geometry")
		return
	for map_size: int in [21, 31, 41]:
		var maximum_ticks := 6640 if map_size == 41 else map_size * 100
		if not broadcast.configure_replay(_broadcast_replay(map_size, maximum_ticks)):
			_fail("dynamic broadcast configure %d" % map_size)
			return
		var dynamic_snapshot := broadcast.snapshot_copy()
		if dynamic_snapshot.map_dimensions != [map_size, map_size] \
				or dynamic_snapshot.map_start != [1, map_size - 2] \
				or dynamic_snapshot.map_exit != [map_size - 2, 1] \
				or dynamic_snapshot.landmark_count != 1 \
				or dynamic_snapshot.maximum_ticks != maximum_ticks \
				or float(dynamic_snapshot.tile_scale) > 1.0 \
				or float(dynamic_snapshot.tile_scale) < 0.42 \
				or (map_size == 41 and float(dynamic_snapshot.camera_far) < 160.0):
			_fail("dynamic broadcast geometry %d: %s" % [map_size, str(dynamic_snapshot)])
			return
		if not broadcast.apply_race_time(maximum_ticks * 1000) \
				or broadcast.apply_race_time(maximum_ticks * 1000 + 1):
			_fail("dynamic broadcast time %d" % map_size)
			return
	broadcast.free()
	print("TRIO_MAZE_RACE_HEADLESS_OK")
	quit(0)


func _fail(label: String) -> void:
	push_error("TRIO_MAZE_HEADLESS_FAILED: %s" % label)
	quit(1)


func _broadcast_replay(map_size: int = 0, maximum_ticks: int = 600) -> Dictionary:
	var start := [7, 13] if map_size == 0 else [1, map_size - 2]
	var next := [8, 13] if map_size == 0 else [2, map_size - 2]
	var racers := []
	for index: int in 3:
		var participant_id := "participant_%d" % index
		racers.append({
			"participant_id": participant_id,
			"entrant_id": "entrant_%d" % index,
			"display_name": ["Sol", "Terra", "Luna"][index],
			"model": "test-model-%d" % index,
			"color": ["#ffb454", "#63d6ff", "#c6a8ff"][index],
			"finish_tick": null,
			"distance_cells": 1,
			"shortest_path_cells": 60,
			"path_efficiency_basis_points": 10000,
			"keyframes": [
				{
					"tick": 0, "cell": start,
					"heading": 0,
					"state": "thinking", "task": "exploring",
					"visible_cells": [start],
				},
				{
					"tick": 4, "cell": next, "heading": 1,
					"state": "walk", "task": "exploring",
					"visible_cells": [start, next],
				},
			],
		})
	var replay := {
		"task_id": MazeMap.TASK_ID,
		"protocol_version": MazeMap.PROTOCOL_VERSION,
		"maximum_ticks": maximum_ticks,
		"vision": {"range_cells": 1, "occlusion": "straight_line_walls"},
		"racers": racers,
		"events": [],
		"result": {
			"completion_tick": maximum_ticks,
			"finish_order": [],
			"winner_id": null,
			"reason": "decision_budget_or_tick_limit",
		},
	}
	if map_size > 0:
		replay["map"] = _open_map(map_size)
	return replay


func _open_map(map_size: int) -> Dictionary:
	var rows := []
	var border := "#".repeat(map_size)
	var interior := "#%s#" % ".".repeat(map_size - 2)
	for y: int in map_size:
		rows.append(border if y == 0 or y == map_size - 1 else interior)
	var start := Vector2i(1, map_size - 2)
	var exit_cell := Vector2i(map_size - 2, 1)
	rows[start.y] = str(rows[start.y]).substr(0, start.x) + "S" + str(rows[start.y]).substr(start.x + 1)
	rows[exit_cell.y] = str(rows[exit_cell.y]).substr(0, exit_cell.x) + "E" + str(rows[exit_cell.y]).substr(exit_cell.x + 1)
	return {
		"schema_version": "worldarena/maze-map-spec/1",
		"map_id": "headless-%d" % map_size,
		"generator_version": "headless-v1",
		"seed": map_size,
		"difficulty": "memory_stress" if map_size == 41 else "hard",
		"rows": rows,
		"start": [start.x, start.y],
		"exit": [exit_cell.x, exit_cell.y],
		"landmarks": [{"position": [map_size / 2, map_size / 2], "label": "center-beacon"}],
		"metrics": {},
		"map_sha256": "0".repeat(64),
	}
