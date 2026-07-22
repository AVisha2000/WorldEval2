extends SceneTree

const Scene := preload("res://scripts/embodiment/presentation/maze/trio_maze_broadcast_scene.gd")
const Codec := preload("res://scripts/embodiment/transport/embodiment_frame_codec.gd")
const CAPTURE_FPS := 30
const FRAMES_PER_TICK := 3
const INTRO_FRAMES := 5 * CAPTURE_FPS
const OUTRO_FRAMES := 7 * CAPTURE_FPS

var _failed := false


func _init() -> void:
	_run.call_deferred()


func _run() -> void:
	if DisplayServer.get_name() == "headless":
		_fail("labyrinth_movie_maker_environment_invalid")
		return
	var replay_path := _replay_path()
	if replay_path.is_empty():
		_fail("labyrinth_movie_maker_replay_missing")
		return
	var parsed := Codec.parse_canonical(FileAccess.get_file_as_bytes(replay_path), 4 * 1024 * 1024)
	if not bool(parsed.get("ok", false)) or not parsed.value is Dictionary:
		_fail("labyrinth_movie_maker_replay_invalid")
		return
	var replay: Dictionary = parsed.value
	var scene := Scene.new()
	root.add_child(scene)
	if not scene.configure_replay(replay):
		_fail("labyrinth_movie_maker_projection_rejected")
		return
	var maximum_ticks: Variant = replay.get("maximum_ticks", 600)
	if typeof(maximum_ticks) != TYPE_INT or maximum_ticks < 1 or maximum_ticks > 100_000:
		_fail("labyrinth_movie_maker_projection_rejected")
		return
	for frame: int in INTRO_FRAMES:
		if not scene.apply_race_time(-1000):
			_fail("labyrinth_movie_maker_projection_rejected")
			return
		await process_frame
	for tick: int in int(maximum_ticks):
		for frame: int in FRAMES_PER_TICK:
			if not scene.apply_race_time(tick * 1000 + (frame + 1) * 1000 / FRAMES_PER_TICK):
				_fail("labyrinth_movie_maker_projection_rejected")
				return
			await process_frame
	for frame: int in OUTRO_FRAMES:
		if not scene.apply_race_time(int(maximum_ticks) * 1000):
			_fail("labyrinth_movie_maker_projection_rejected")
			return
		await process_frame
	print("LABYRINTH_RUN_MOVIE_MAKER_OK")
	quit(0)


func _replay_path() -> String:
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--labyrinth-replay="):
			return argument.trim_prefix("--labyrinth-replay=")
	return ""


func _fail(code: String) -> void:
	if _failed:
		return
	_failed = true
	push_error(code)
	quit(2)
