class_name EmbodimentTrioMazeBroadcastScene
extends Node3D

const YBot := preload("res://scenes/embodiment/y_bot_operator.tscn")
const EntrantPalette := preload("res://scripts/embodiment/presentation/entrant_palette.gd")
const MazeMap := preload("res://scripts/embodiment/trio_games/trio_maze_map.gd")
const PARTICIPANTS := ["participant_0", "participant_1", "participant_2"]
const LANE_TONES := {
	"participant_0": Color("8b6a18"),
	"participant_1": Color("684b8f"),
	"participant_2": Color("23775b"),
}

var _world: Node3D
var _geometry: Node3D
var _camera: Camera3D
var _racers := {}
var _visibility_tiles := {}
var _visible_tile_keys := {}
var _trail_markers := {}
var _replay := {}
var _tick_milli := -1000
var _hud: RichTextLabel
var _beat: Label
var _event_feed: Label
var _title_card: Control
var _winner_card: Control
var _winner_text: RichTextLabel
var _map_rows: Array[String] = []
var _map_start := Vector2i.ZERO
var _map_exit := Vector2i.ZERO
var _map_landmarks: Array[Dictionary] = []
var _map_width := 15
var _map_height := 15
var _map_center := Vector2(7.0, 7.0)
var _tile_scale := 1.0
var _lane_spacing := 18.0
var _lane_offsets := {"participant_0": -18.0, "participant_1": 0.0, "participant_2": 18.0}
var _maximum_ticks := 600


func _ready() -> void:
	_build()


func configure_replay(replay: Dictionary) -> bool:
	_build()
	if replay.get("task_id") != MazeMap.TASK_ID or replay.get("protocol_version") != MazeMap.PROTOCOL_VERSION \
			or not replay.get("racers") is Array or replay.racers.size() != 3 \
			or not replay.get("events", []) is Array or not replay.get("result", {}) is Dictionary:
		return false
	var maximum_ticks: Variant = replay.get("maximum_ticks", 600)
	if typeof(maximum_ticks) != TYPE_INT or maximum_ticks < 1 or maximum_ticks > 100_000:
		return false
	var map_config := _normalise_map(replay.get("map", {}))
	if map_config.is_empty():
		return false
	var seen := {}
	for value: Variant in replay.racers:
		if not value is Dictionary or value.get("participant_id") not in PARTICIPANTS \
				or not value.get("keyframes") is Array or value.keyframes.is_empty():
			return false
		for keyframe: Variant in value.keyframes:
			if not keyframe is Dictionary or not _valid_keyframe(keyframe, map_config, int(maximum_ticks)):
				return false
		seen[value.participant_id] = value
	if seen.size() != 3:
		return false
	_apply_map_config(map_config)
	_maximum_ticks = int(maximum_ticks)
	_replay = replay.duplicate(true)
	_rebuild_geometry()
	for racer_value: Variant in _replay.racers:
		var racer: Dictionary = racer_value
		var participant_id := str(racer.participant_id)
		var actor: Node3D = _racers[participant_id]
		var entrant_id := str(racer.display_name).to_lower()
		if entrant_id in ["sol", "luna", "terra"]:
			EntrantPalette.tint_avatar(actor, entrant_id)
		(actor.get_node("RacerLabel") as Label3D).modulate = Color(str(racer.color))
		var banner_label := _geometry.get_node_or_null("%sBanner/Label" % participant_id) as Label3D
		if banner_label != null:
			banner_label.text = str(racer.display_name).to_upper()
	_build_trails()
	_winner_text.text = _winner_card_copy()
	return apply_race_time(-1000)


func apply_race_time(tick_milli: int) -> bool:
	if _replay.is_empty() or tick_milli < -1000 or tick_milli > _maximum_ticks * 1000:
		return false
	_tick_milli = tick_milli
	var authority_tick := clampi(tick_milli / 1000, 0, _maximum_ticks)
	for value: Variant in _replay.racers:
		var racer: Dictionary = value
		var participant_id := str(racer.participant_id)
		var pose := _pose_at(racer.keyframes, maxi(0, tick_milli))
		if pose.is_empty():
			return false
		var actor: Node3D = _racers[participant_id]
		actor.position = _world_position(participant_id, pose.position)
		actor.rotation.y = -float(pose.heading) * PI / 2.0
		if actor.has_method("play_state"):
			actor.call("play_state", StringName(pose.animation))
		var label := actor.get_node("RacerLabel") as Label3D
		label.text = "%s\n%s" % [str(racer.display_name).to_upper(), str(pose.task).to_upper().replace("_", " ")]
		var bubble := actor.get_node("Speech") as Label3D
		bubble.text = _safe_event_label(participant_id, authority_tick)
		_apply_visibility(participant_id, pose.get("visible_cells", []))
		_apply_trail(participant_id, authority_tick)
	_update_hud(authority_tick)
	_apply_camera_beat(authority_tick)
	_title_card.visible = tick_milli < 0
	_winner_card.visible = authority_tick >= _result_tick()
	return true


func snapshot_copy() -> Dictionary:
	var visible_counts := {}
	var trail_counts := {}
	for participant_id: String in PARTICIPANTS:
		visible_counts[participant_id] = (_visible_tile_keys.get(participant_id, {}) as Dictionary).size()
		var shown := 0
		for marker: Variant in _trail_markers.get(participant_id, []):
			shown += 1 if (marker.node as Node3D).visible else 0
		trail_counts[participant_id] = shown
	return {
		"task_id": MazeMap.TASK_ID,
		"tick_milli": _tick_milli,
		"configured": not _replay.is_empty(),
		"map_dimensions": [_map_width, _map_height],
		"map_start": [_map_start.x, _map_start.y],
		"map_exit": [_map_exit.x, _map_exit.y],
		"landmark_count": _map_landmarks.size(),
		"maximum_ticks": _maximum_ticks,
		"tile_scale": _tile_scale,
		"lane_spacing": _lane_spacing,
		"camera_far": _camera.far,
		"overhead_camera_height": _overhead_camera_height(),
		"visible_cell_counts": visible_counts,
		"trail_cell_counts": trail_counts,
	}


func _normalise_map(value: Variant) -> Dictionary:
	if not value is Dictionary:
		return {}
	var rows_value: Variant = value.get("rows", MazeMap.ROWS)
	if not rows_value is Array or rows_value.size() < 15 or rows_value.size() > 41 \
			or rows_value.size() % 2 == 0:
		return {}
	var rows: Array[String] = []
	var width := -1
	var start_count := 0
	var exit_count := 0
	for row_value: Variant in rows_value:
		if not row_value is String:
			return {}
		var row := str(row_value)
		if width < 0:
			width = row.length()
		if row.length() != width or width < 3 or width > 41 or width % 2 == 0:
			return {}
		for index: int in row.length():
			var symbol := row.substr(index, 1)
			if symbol not in ["#", ".", "S", "E"]:
				return {}
			start_count += 1 if symbol == "S" else 0
			exit_count += 1 if symbol == "E" else 0
		rows.append(row)
	if start_count != 1 or exit_count != 1:
		return {}
	var detected_start := _find_marker(rows, "S")
	var detected_exit := _find_marker(rows, "E")
	var start := _parse_cell(value.get("start", []), detected_start)
	var exit_cell := _parse_cell(value.get("exit", []), detected_exit)
	if not _cell_is_walkable(rows, start) or not _cell_is_walkable(rows, exit_cell) \
			or start == exit_cell:
		return {}
	if detected_start != Vector2i(-1, -1) and start != detected_start:
		return {}
	if detected_exit != Vector2i(-1, -1) and exit_cell != detected_exit:
		return {}
	var landmarks: Array[Dictionary] = []
	var landmark_values: Variant = value.get("landmarks", [])
	if not landmark_values is Array:
		return {}
	for landmark_value: Variant in landmark_values:
		if not landmark_value is Dictionary:
			return {}
		var position := _parse_cell(landmark_value.get("position", []), Vector2i(-1, -1))
		var label: Variant = landmark_value.get("label")
		if not label is String or str(label).is_empty() or str(label).length() > 80 \
				or not _cell_is_walkable(rows, position):
			return {}
		landmarks.append({"position": position, "label": str(label)})
	if landmarks.is_empty() and rows == Array(MazeMap.ROWS):
		for cell: Vector2i in MazeMap.LANDMARKS:
			landmarks.append({"position": cell, "label": str(MazeMap.LANDMARKS[cell])})
	return {
		"rows": rows,
		"start": start,
		"exit": exit_cell,
		"landmarks": landmarks,
	}


func _parse_cell(value: Variant, fallback: Vector2i) -> Vector2i:
	if value is Array and value.size() == 2 and typeof(value[0]) == TYPE_INT \
			and typeof(value[1]) == TYPE_INT:
		return Vector2i(int(value[0]), int(value[1]))
	return fallback


func _find_marker(rows: Array[String], marker: String) -> Vector2i:
	var found := Vector2i(-1, -1)
	for y: int in rows.size():
		for x: int in rows[y].length():
			if rows[y].substr(x, 1) == marker:
				if found != Vector2i(-1, -1):
					return Vector2i(-1, -1)
				found = Vector2i(x, y)
	return found


func _cell_is_walkable(rows: Array[String], cell: Vector2i) -> bool:
	return cell.y >= 0 and cell.y < rows.size() and cell.x >= 0 \
			and cell.x < rows[cell.y].length() and rows[cell.y].substr(cell.x, 1) != "#"


func _valid_keyframe(keyframe: Dictionary, map_config: Dictionary, maximum_ticks: int) -> bool:
	var tick: Variant = keyframe.get("tick")
	var cell_value: Variant = keyframe.get("cell")
	var heading: Variant = keyframe.get("heading")
	if typeof(tick) != TYPE_INT or tick < 0 or tick > maximum_ticks \
			or not cell_value is Array or cell_value.size() != 2 \
			or typeof(cell_value[0]) != TYPE_INT or typeof(cell_value[1]) != TYPE_INT \
			or typeof(heading) != TYPE_INT or heading < 0 or heading >= MazeMap.DIRECTIONS.size():
		return false
	var cell := Vector2i(int(cell_value[0]), int(cell_value[1]))
	if not _cell_is_walkable(map_config.rows, cell):
		return false
	var visible_cells: Variant = keyframe.get("visible_cells", [])
	if not visible_cells is Array:
		return false
	for visible_value: Variant in visible_cells:
		if not visible_value is Array or visible_value.size() != 2 \
				or typeof(visible_value[0]) != TYPE_INT or typeof(visible_value[1]) != TYPE_INT \
				or not _cell_is_walkable(map_config.rows, Vector2i(int(visible_value[0]), int(visible_value[1]))):
			return false
	return true


func _apply_map_config(map_config: Dictionary) -> void:
	_map_rows.clear()
	for row: Variant in map_config.rows:
		_map_rows.append(str(row))
	_map_start = map_config.start
	_map_exit = map_config.exit
	_map_landmarks.clear()
	for landmark: Variant in map_config.landmarks:
		_map_landmarks.append((landmark as Dictionary).duplicate(true))
	_map_height = _map_rows.size()
	_map_width = _map_rows[0].length()
	_map_center = Vector2(float(_map_width - 1) / 2.0, float(_map_height - 1) / 2.0)
	_tile_scale = clampf(15.0 / float(maxi(_map_width, _map_height)), 0.42, 1.0)
	_lane_spacing = float(_map_width) * _tile_scale + 3.0
	_lane_offsets = {
		"participant_0": -_lane_spacing,
		"participant_1": 0.0,
		"participant_2": _lane_spacing,
	}


func _build() -> void:
	if _world != null:
		return
	if _map_rows.is_empty():
		_apply_map_config(_normalise_map({}))
	_world = Node3D.new()
	_world.name = "LabyrinthRunWorld"
	add_child(_world)
	_build_environment()
	_camera = Camera3D.new()
	_camera.name = "LabyrinthBroadcastCamera"
	_camera.current = true
	_camera.fov = 58.0
	_camera.near = 0.1
	_camera.far = 140.0
	_camera.position = Vector3(0.0, 38.0, 32.0)
	_world.add_child(_camera)
	_camera.look_at_from_position(_camera.position, Vector3.ZERO, Vector3.UP)
	_rebuild_geometry()
	_build_hud()


func _rebuild_geometry() -> void:
	if _world == null:
		return
	if _geometry != null:
		_world.remove_child(_geometry)
		_geometry.free()
	_geometry = Node3D.new()
	_geometry.name = "LabyrinthRaceGeometry"
	_world.add_child(_geometry)
	_racers.clear()
	_visibility_tiles.clear()
	_visible_tile_keys.clear()
	_trail_markers.clear()
	for participant_id: String in PARTICIPANTS:
		_build_lane(participant_id)
		_build_racer(participant_id)
	_camera.far = maxf(140.0, _overhead_camera_height() * 4.0)


func _build_environment() -> void:
	var ground := _box("GrassGround", Vector3(58.0, 0.18, 22.0), Color("214b35"))
	ground.position.y = -0.12
	_world.add_child(ground)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-56.0, -28.0, 0.0)
	sun.light_color = Color("ffe1ab")
	sun.light_energy = 1.45
	sun.shadow_enabled = true
	_world.add_child(sun)
	var environment := WorldEnvironment.new()
	var settings := Environment.new()
	settings.background_mode = Environment.BG_COLOR
	settings.background_color = Color("638aa0")
	settings.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	settings.ambient_light_color = Color("b8d7c0")
	settings.ambient_light_energy = 0.7
	settings.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	settings.tonemap_exposure = 0.9
	environment.environment = settings
	_world.add_child(environment)
	for index: int in 28:
		var x := -28.0 + float(index % 14) * 4.3
		var z := -10.2 if index < 14 else 10.2
		var trunk := _box("BoundaryTreeTrunk", Vector3(0.32, 1.6, 0.32), Color("61442f"))
		trunk.position = Vector3(x, 0.8, z)
		_world.add_child(trunk)
		var crown := _sphere("BoundaryTreeCrown", 0.82, Color("2f7043"))
		crown.position = Vector3(x, 2.0, z)
		_world.add_child(crown)


func _build_lane(participant_id: String) -> void:
	var offset: float = _lane_offsets[participant_id]
	_visibility_tiles[participant_id] = {}
	_visible_tile_keys[participant_id] = {}
	_trail_markers[participant_id] = []
	var lane_floor := _box(
		"%sLaneFloor" % participant_id,
		Vector3(float(_map_width) * _tile_scale + 0.4, 0.08, float(_map_height) * _tile_scale + 0.4),
		LANE_TONES[participant_id].darkened(0.42),
	)
	lane_floor.position = Vector3(offset, -0.01, 0.0)
	_geometry.add_child(lane_floor)
	for y: int in _map_rows.size():
		for x: int in _map_rows[y].length():
			if _map_rows[y].substr(x, 1) != "#":
				var sight := _box(
					"%sSight_%d_%d" % [participant_id, x, y],
					Vector3(0.84 * _tile_scale, 0.035, 0.84 * _tile_scale),
					Color("ffd45a"),
				)
				sight.position = _world_position(participant_id, Vector2(x, y)) + Vector3(0.0, 0.035, 0.0)
				sight.material_override = _visibility_material()
				sight.visible = false
				_geometry.add_child(sight)
				(_visibility_tiles[participant_id] as Dictionary)[_cell_key(Vector2i(x, y))] = sight
				continue
			var wall_height := 1.1 * maxf(_tile_scale, 0.55)
			var wall := _box(
				"MazeWall",
				Vector3(0.92 * _tile_scale, wall_height, 0.92 * _tile_scale),
				Color("51614f"),
			)
			wall.position = _world_position(participant_id, Vector2(x, y)) + Vector3(0.0, wall_height / 2.0, 0.0)
			_geometry.add_child(wall)
			if (x + y) % 4 == 0:
				var hedge := _box(
					"MazeHedge",
					Vector3(0.78 * _tile_scale, 0.35 * maxf(_tile_scale, 0.55), 0.78 * _tile_scale),
					LANE_TONES[participant_id].lightened(0.12),
				)
				hedge.position = wall.position + Vector3(0.0, wall_height * 0.61, 0.0)
				_geometry.add_child(hedge)
	var start_banner := _lane_banner(participant_id)
	start_banner.scale = Vector3.ONE * maxf(_tile_scale, 0.55)
	start_banner.position = _world_position(participant_id, Vector2(_map_start)) + Vector3(0.0, 0.0, 1.6 * _tile_scale)
	_geometry.add_child(start_banner)
	var exit_arch := _exit_arch(participant_id)
	exit_arch.scale = Vector3.ONE * maxf(_tile_scale, 0.55)
	exit_arch.position = _world_position(participant_id, Vector2(_map_exit))
	_geometry.add_child(exit_arch)
	_build_landmarks(participant_id)


func _build_landmarks(participant_id: String) -> void:
	for landmark: Dictionary in _map_landmarks:
		var cell: Vector2i = landmark.position
		var marker := _sphere("Landmark", 0.22 * maxf(_tile_scale, 0.65), Color("62d9ff"))
		marker.position = _world_position(participant_id, Vector2(cell)) + Vector3(0.0, 0.35, 0.0)
		var material := marker.material_override as StandardMaterial3D
		material.emission_enabled = true
		material.emission = Color("2d8ca8")
		_geometry.add_child(marker)


func _build_trails() -> void:
	for participant_id: String in PARTICIPANTS:
		for marker: Variant in _trail_markers.get(participant_id, []):
			(marker.node as Node3D).queue_free()
		_trail_markers[participant_id] = []
	for racer_value: Variant in _replay.racers:
		var racer: Dictionary = racer_value
		var participant_id := str(racer.participant_id)
		var keyframes: Array = racer.keyframes
		var previous := Vector2i(int(keyframes[0].cell[0]), int(keyframes[0].cell[1]))
		var step_index := 0
		for index: int in range(1, keyframes.size()):
			var frame: Dictionary = keyframes[index]
			var cell := Vector2i(int(frame.cell[0]), int(frame.cell[1]))
			if cell == previous:
				continue
			var root := Node3D.new()
			root.name = "%sFootsteps_%03d" % [participant_id, step_index]
			root.position = _world_position(participant_id, Vector2(cell.x, cell.y)) + Vector3(0.0, 0.075, 0.0)
			var heading := int(frame.heading)
			var direction: Vector2i = MazeMap.DIRECTIONS[heading]
			var forward := Vector3(float(direction.x), 0.0, float(direction.y))
			var lateral := Vector3(-forward.z, 0.0, forward.x) * 0.13
			var color := Color(str(racer.color)).lightened(0.18)
			for foot_index: int in 2:
				var foot := _sphere("Footprint", 0.11, color)
				foot.scale = Vector3(0.62, 0.16, 1.35)
				foot.position = lateral * (-1.0 if foot_index == 0 else 1.0) + forward * (-0.12 if foot_index == 0 else 0.12)
				var material := foot.material_override as StandardMaterial3D
				material.emission_enabled = true
				material.emission = color.darkened(0.15)
				root.add_child(foot)
			root.visible = false
			_geometry.add_child(root)
			(_trail_markers[participant_id] as Array).append({"node": root, "tick": int(frame.tick)})
			previous = cell
			step_index += 1


func _apply_visibility(participant_id: String, visible_cells: Array) -> void:
	var next_keys := {}
	for cell_value: Variant in visible_cells:
		if not cell_value is Array or cell_value.size() != 2:
			continue
		next_keys[_cell_key(Vector2i(int(cell_value[0]), int(cell_value[1])))] = true
	var previous: Dictionary = _visible_tile_keys[participant_id]
	var tiles: Dictionary = _visibility_tiles[participant_id]
	for key: Variant in previous:
		if not next_keys.has(key) and tiles.has(key):
			(tiles[key] as MeshInstance3D).visible = false
	for key: Variant in next_keys:
		if not previous.has(key) and tiles.has(key):
			(tiles[key] as MeshInstance3D).visible = true
	_visible_tile_keys[participant_id] = next_keys


func _apply_trail(participant_id: String, tick: int) -> void:
	for marker: Variant in _trail_markers[participant_id]:
		(marker.node as Node3D).visible = int(marker.tick) <= tick


func _cell_key(cell: Vector2i) -> String:
	return "%d:%d" % [cell.x, cell.y]


func _build_racer(participant_id: String) -> void:
	var actor := YBot.instantiate() as Node3D
	actor.name = "%sRacer" % participant_id
	var tree := actor.get_node_or_null("AnimationTree") as AnimationTree
	if tree != null and tree.tree_root != null:
		tree.tree_root = tree.tree_root.duplicate(true)
	var entrant_id: String = {"participant_0": "sol", "participant_1": "luna", "participant_2": "terra"}[participant_id]
	EntrantPalette.tint_avatar(actor, entrant_id)
	var actor_scale := 0.72 * maxf(_tile_scale, 0.58)
	actor.scale = Vector3.ONE * actor_scale
	actor.position = _world_position(participant_id, Vector2(_map_start))
	var label := Label3D.new()
	label.name = "RacerLabel"
	label.position.y = 3.2
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.outline_size = 5
	label.font_size = 34
	label.modulate = EntrantPalette.color(entrant_id)
	actor.add_child(label)
	var speech := Label3D.new()
	speech.name = "Speech"
	speech.position.y = 4.15
	speech.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	speech.outline_size = 5
	speech.font_size = 28
	speech.modulate = Color("fff5d8")
	actor.add_child(speech)
	_geometry.add_child(actor)
	_racers[participant_id] = actor


func _build_hud() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var top := ColorRect.new()
	top.position = Vector2(18, 16)
	top.size = Vector2(1404, 150)
	top.color = Color("08121eea")
	layer.add_child(top)
	_hud = RichTextLabel.new()
	_hud.position = Vector2(22, 12)
	_hud.size = Vector2(1360, 132)
	_hud.bbcode_enabled = true
	_hud.scroll_active = false
	_hud.add_theme_font_size_override("normal_font_size", 18)
	top.add_child(_hud)
	_beat = Label.new()
	_beat.position = Vector2(28, 175)
	_beat.size = Vector2(980, 40)
	_beat.add_theme_font_size_override("font_size", 21)
	_beat.add_theme_color_override("font_color", Color("ffe38c"))
	layer.add_child(_beat)
	_event_feed = Label.new()
	_event_feed.position = Vector2(1040, 175)
	_event_feed.size = Vector2(370, 170)
	_event_feed.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_event_feed.add_theme_font_size_override("font_size", 16)
	_event_feed.add_theme_color_override("font_color", Color("e8f1f8"))
	layer.add_child(_event_feed)
	_title_card = _overlay_card(layer, Vector2(260, 285), Vector2(920, 240), Color("07111cf4"))
	var title := RichTextLabel.new()
	title.position = Vector2(42, 28)
	title.size = Vector2(836, 190)
	title.bbcode_enabled = true
	title.fit_content = true
	title.text = "[center][font_size=22][color=#8be9fd]WORLDARENA HIGHLIGHT[/color][/font_size]\n[font_size=50][b]LABYRINTH RUN[/b][/font_size]\n[font_size=22]Three agents · the same maze · no shared vision[/font_size][/center]"
	_title_card.add_child(title)
	_winner_card = _overlay_card(layer, Vector2(210, 245), Vector2(1020, 410), Color("07111cf7"))
	_winner_text = RichTextLabel.new()
	_winner_text.position = Vector2(48, 34)
	_winner_text.size = Vector2(924, 350)
	_winner_text.bbcode_enabled = true
	_winner_text.fit_content = true
	_winner_card.add_child(_winner_text)
	_winner_card.visible = false


func _winner_card_copy() -> String:
	var winner_id: Variant = _replay.result.get("winner_id")
	var winner_name := "No winner"
	for racer: Dictionary in _replay.racers:
		if racer.participant_id == winner_id:
			winner_name = str(racer.display_name)
	var podium := []
	for racer: Dictionary in _replay.racers:
		var finish := "DNF" if racer.get("finish_tick") == null else "%.1fs" % (float(racer.finish_tick) / 10.0)
		podium.append("%s  %s" % [str(racer.display_name), finish])
	return "[center][font_size=20][color=#8be9fd]VERIFIED LIVE RESULT[/color][/font_size]\n[font_size=50][color=#fbbf24][b]%s[/b][/color][/font_size]\n[font_size=24]%s[/font_size]\n\n[font_size=20]%s[/font_size]\n\n[font_size=16][color=#9fb3c8]DETERMINISTIC REPLAY VERIFIED · MAZE-TASK-PLAN-V1[/color][/font_size][/center]" % ["%s WINS" % winner_name if winner_id != null else "NO FINISHER", str(_replay.result.get("reason", "race complete")).replace("_", " "), "     ".join(podium)]


func _update_hud(tick: int) -> void:
	var remaining := maxi(0, _maximum_ticks - tick)
	var remaining_seconds := ceili(float(remaining) / 10.0)
	var lines := "[font_size=22][b]LABYRINTH RUN[/b]  [color=#8be9fd]%02d:%02d[/color]  [color=#ffd45a]VISION %s[/color][/font_size]     " % [remaining_seconds / 60, remaining_seconds % 60, _vision_label()]
	for racer_value: Variant in _replay.racers:
		var entrant: Dictionary = racer_value
		lines += "[color=%s][b]● %s[/b][/color] %s     " % [str(entrant.color), str(entrant.display_name).to_upper(), str(entrant.model)]
	lines += "\n"
	for value: Variant in _replay.racers:
		var racer: Dictionary = value
		var progress := _progress_at(racer, tick)
		var color := str(racer.color)
		lines += "[color=%s][b]%-5s[/b][/color] %-12s  passages %2d  dead ends %d  distance %3d  efficiency %5.2f%%\n" % [color, str(racer.display_name).to_upper(), str(progress.task).replace("_", " "), progress.passages, progress.dead_ends, progress.distance, float(racer.path_efficiency_basis_points) / 100.0]
	_hud.text = lines
	var recent: Array[String] = []
	for value: Variant in _replay.events:
		if int(value.tick) <= tick and int(value.tick) >= tick - 55:
			recent.append("%s · %s" % [_display_name(str(value.participant_id)), str(value.label)])
	_event_feed.text = "EVENT FEED\n" + "\n".join(recent.slice(maxi(0, recent.size() - 4)))


func _progress_at(racer: Dictionary, tick: int) -> Dictionary:
	var keyframes: Array = racer.keyframes
	var distance := 0
	var task := "exploring"
	var previous_cell := Vector2i(int(keyframes[0].cell[0]), int(keyframes[0].cell[1]))
	for value: Variant in keyframes:
		if int(value.tick) > tick:
			break
		var cell := Vector2i(int(value.cell[0]), int(value.cell[1]))
		distance += 1 if cell != previous_cell else 0
		previous_cell = cell
		task = str(value.task)
	var passages := 0
	var dead_ends := 0
	for event: Variant in _replay.events:
		if event.participant_id == racer.participant_id and int(event.tick) <= tick:
			passages += 1 if event.kind in ["junction_choice", "backtrack"] else 0
			dead_ends += 1 if event.kind == "dead_end" else 0
	return {"distance": distance, "task": task, "passages": passages, "dead_ends": dead_ends}


func _pose_at(keyframes: Array, tick_milli: int) -> Dictionary:
	var before: Dictionary = keyframes[0]
	var after: Dictionary = before
	for value: Variant in keyframes:
		if int(value.tick) * 1000 <= tick_milli:
			before = value
			after = value
			continue
		after = value
		break
	var before_position := Vector2(float(before.cell[0]), float(before.cell[1]))
	var after_position := Vector2(float(after.cell[0]), float(after.cell[1]))
	var span := maxi(1, (int(after.tick) - int(before.tick)) * 1000)
	var progress := clampf(float(tick_milli - int(before.tick) * 1000) / float(span), 0.0, 1.0)
	var moving := before_position != after_position and tick_milli < int(after.tick) * 1000
	var state := str(after.state if not moving else "walk")
	return {
		"position": before_position.lerp(after_position, progress),
		"heading": int(after.heading),
		"animation": "hit" if state == "surprised" else "celebrate" if state == "celebrate" else "walk" if moving or state == "walk" else "idle",
		"task": str(after.task if moving else before.task),
		"visible_cells": before.get("visible_cells", []),
	}


func _safe_event_label(participant_id: String, tick: int) -> String:
	var label := "Exploring"
	for value: Variant in _replay.events:
		if value.participant_id == participant_id and int(value.tick) <= tick:
			label = str(value.label)
	return label if label.length() <= 40 else label.left(40)


func _apply_camera_beat(tick: int) -> void:
	var overhead_height := _overhead_camera_height()
	var arena_depth := float(_map_height) * _tile_scale
	var position := Vector3(0.0, overhead_height, arena_depth * 2.15)
	var target := Vector3(0.0, 0.0, 0.0)
	var label := "IDENTICAL LANES · EQUAL MOVEMENT SPEED · PRIVATE VISION"
	var progress := float(tick) / float(maxi(1, _maximum_ticks))
	if progress < 1.0 / 6.0:
		position = Vector3(0.0, overhead_height * 0.58, arena_depth * 1.72)
		target = Vector3(0.0, 0.0, arena_depth * 0.33)
		label = "THE GATES OPEN · THREE INDEPENDENT SEARCHES"
	elif progress < 0.5:
		label = "OVERHEAD COMPARISON · POLICIES DIVERGE"
	elif progress < 0.7:
		position = Vector3(_lane_spacing * 0.5, overhead_height * 0.4, arena_depth * 0.92)
		target = Vector3(_lane_spacing * 0.5, 0.0, 0.0)
		label = "GOLD TILES SHOW EXACT AGENT SIGHTLINES"
	elif progress < 0.8:
		position = Vector3(float(_lane_offsets.participant_0), overhead_height * 0.27, -arena_depth * 0.86)
		target = Vector3(float(_lane_offsets.participant_0), 0.0, -arena_depth * 0.33)
		label = "FOOTSTEPS PRESERVE EVERY EXECUTED CELL"
	elif progress < 11.0 / 12.0:
		position = Vector3(float(_lane_offsets.participant_2), overhead_height * 0.27, -arena_depth * 0.86)
		target = Vector3(float(_lane_offsets.participant_2), 0.0, -arena_depth * 0.33)
		label = "CORRIDOR COMMANDS STOP BEFORE ROUTE CHOICES"
	else:
		position = Vector3(0.0, overhead_height * 0.29, -arena_depth * 0.86)
		target = Vector3(0.0, 0.0, -arena_depth * 0.33)
		label = "PRIVATE MEMORIES · PUBLIC PHYSICAL TRAILS"
	if tick >= _result_tick():
		position = Vector3(0.0, overhead_height * 0.82, arena_depth * 1.92)
		target = Vector3(0.0, 0.0, -arena_depth * 0.13)
		label = "FINAL VERIFIED PODIUM"
	_camera.look_at_from_position(position, target, Vector3.UP)
	_beat.text = label


func _result_tick() -> int:
	return int(_replay.result.get("completion_tick", 0)) \
			if _replay.result.get("reason") == "all_racers_finished" else _maximum_ticks


func _lane_banner(participant_id: String) -> Node3D:
	var root := Node3D.new()
	root.name = "%sBanner" % participant_id
	var post := _box("BannerPost", Vector3(0.18, 2.5, 0.18), Color("5a4431"))
	post.position.y = 1.25
	root.add_child(post)
	var banner := _box("Banner", Vector3(1.8, 0.9, 0.1), LANE_TONES[participant_id].lightened(0.18))
	banner.position = Vector3(0.0, 2.0, 0.0)
	root.add_child(banner)
	var label := Label3D.new()
	label.name = "Label"
	label.text = _display_name(participant_id).to_upper()
	label.position = Vector3(0.0, 2.0, -0.08)
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.outline_size = 4
	label.font_size = 42
	root.add_child(label)
	return root


func _exit_arch(participant_id: String) -> Node3D:
	var root := Node3D.new()
	for x: float in [-0.7, 0.7]:
		var pillar := _box("ExitPillar", Vector3(0.3, 2.6, 0.35), Color("728070"))
		pillar.position = Vector3(x, 1.3, 0.0)
		root.add_child(pillar)
	var lintel := _box("ExitLintel", Vector3(1.7, 0.35, 0.4), Color("8d9a87"))
	lintel.position.y = 2.55
	root.add_child(lintel)
	var glow := _sphere("ExitGlow", 0.42, LANE_TONES[participant_id].lightened(0.42))
	glow.position.y = 2.0
	var material := glow.material_override as StandardMaterial3D
	material.emission_enabled = true
	material.emission = LANE_TONES[participant_id]
	root.add_child(glow)
	return root


func _overlay_card(layer: CanvasLayer, position: Vector2, size: Vector2, color: Color) -> Control:
	var panel := ColorRect.new()
	panel.position = position
	panel.size = size
	panel.color = color
	layer.add_child(panel)
	return panel


func _world_position(participant_id: String, cell: Vector2) -> Vector3:
	return Vector3(
		float(_lane_offsets[participant_id]) + (cell.x - _map_center.x) * _tile_scale,
		0.0,
		(cell.y - _map_center.y) * _tile_scale,
	)


func _overhead_camera_height() -> float:
	var arena_width := 2.0 * _lane_spacing + float(_map_width) * _tile_scale
	var arena_depth := float(_map_height) * _tile_scale
	return maxf(38.0, maxf(arena_width * 0.74, arena_depth * 1.9))


func _display_name(participant_id: String) -> String:
	if not _replay.is_empty():
		for racer_value: Variant in _replay.racers:
			var racer: Dictionary = racer_value
			if racer.participant_id == participant_id:
				return str(racer.display_name)
	return {"participant_0": "Sol", "participant_1": "Luna", "participant_2": "Terra"}.get(participant_id, "Agent")


func _vision_label() -> String:
	if not _replay.get("vision") is Dictionary:
		return "UNKNOWN"
	var value: Variant = _replay.vision.get("range_cells", "unknown")
	return "∞ TO WALL" if typeof(value) == TYPE_STRING and str(value) == "infinite" \
			else "%s CELLS" % str(value)


func _box(name: String, size: Vector3, color: Color) -> MeshInstance3D:
	var node := MeshInstance3D.new()
	node.name = name
	var mesh := BoxMesh.new()
	mesh.size = size
	node.mesh = mesh
	node.material_override = _material(color)
	return node


func _sphere(name: String, radius: float, color: Color) -> MeshInstance3D:
	var node := MeshInstance3D.new()
	node.name = name
	var mesh := SphereMesh.new()
	mesh.radius = radius
	mesh.height = radius * 2.0
	node.mesh = mesh
	node.material_override = _material(color)
	return node


func _material(color: Color) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 0.82
	return material


func _visibility_material() -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_color = Color(1.0, 0.78, 0.20, 0.42)
	material.emission_enabled = true
	material.emission = Color("b67b13")
	material.emission_energy_multiplier = 0.72
	material.roughness = 0.7
	return material
