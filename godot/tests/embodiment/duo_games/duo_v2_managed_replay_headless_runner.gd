extends SceneTree

const Canonical := preload("res://scripts/embodiment/transport/embodiment_frame_codec.gd")
const Codec := preload("res://scripts/embodiment/v2/transport/embodiment_frame_codec_v2.gd")
const Dispatcher := preload("res://scripts/embodiment/v2/transport/control_game_dispatcher_v2.gd")
const Host := preload("res://scripts/embodiment/v2/transport/embodiment_managed_session_host_v2.gd")
const Identity := preload("res://scripts/embodiment/v2/protocol/embodiment_protocol_package_identity_v2.gd")
const ReplayDispatcher := preload("res://scripts/embodiment/v2/replay/embodiment_replay_dispatcher.gd")
const TICKET := "0123456789abcdef0123456789abcdef0123456789a"
const TASKS := [
	"duo-checkpoint-race-v0", "duo-relay-control-v0", "duo-spar-v0",
	"duo-resource-relay-v0",
	"rts-skirmish-v0",
]

var failures := PackedStringArray()


func _init() -> void:
	_test_dispatch_and_participant_sources()
	_test_managed_two_seat_text_and_fixed_fallback()
	_test_versioned_replay_for_all_duo_games()
	_finish()


func _config(task_id: String, episode_id: String, profile: String = "text-visible-v1") -> Dictionary:
	return {"protocol_version": Identity.PROTOCOL_VERSION, "episode_id": episode_id,
		"mode": "model-duel-v0", "task_id": task_id, "seed": 41,
		"observation_profile": profile, "timing_track": "step-locked-v1",
		"maximum_episode_ticks": 1200,
		"participant_ids": ["participant_0", "participant_1"]}


func _secret() -> PackedByteArray:
	var secret := PackedByteArray()
	secret.resize(32)
	for index: int in 32:
		secret[index] = index + 1
	return secret


func _launch(config: Dictionary) -> Dictionary:
	return {"attachment_ticket": TICKET, "config": config,
		"config_sha256": Canonical.sha256_bytes(Canonical.canonical_bytes(config)),
		"connection_id": "duo_v2_managed", "protocol_package_sha256": Identity.SHA256}


func _test_dispatch_and_participant_sources() -> void:
	for task_id: String in TASKS:
		var dispatcher := Dispatcher.new()
		var config := _config(task_id, "ep_dispatch_%s" % task_id.replace("-", "_"))
		_check(dispatcher.configure(config).is_empty(), "%s dispatcher configure failed" % task_id)
		_check(dispatcher.observe_all().size() == 2, "%s did not project two observations" % task_id)
		_check(dispatcher.capability_status().implemented_tasks == [
			"movement-maze-v0", "operator-action-course-v0", "duo-checkpoint-race-v0",
			"duo-relay-control-v0", "duo-spar-v0",
			"duo-resource-relay-v0",
			"rts-skirmish-v0",
		], "v2 runtime capability list drifted")
		for participant_id: String in ["participant_0", "participant_1"]:
			var rival_id := "participant_1" if participant_id == "participant_0" else "participant_0"
			var source: Dictionary = dispatcher.participant_presentation_source(participant_id)
			var observation: Dictionary = dispatcher.observe_all()[participant_id]
			var source_text := JSON.stringify(source)
			_check(source.participant_id == participant_id, "%s source identity drifted" % participant_id)
			_check(rival_id not in source_text and "spectator" not in source_text,
				"%s source leaked rival identity or spectator state" % participant_id)
			var semantic_ids: Array = observation.visible_entities.map(
				func(entity: Dictionary) -> String: return str(entity.id))
			for entity: Dictionary in source.visible_entities:
				_check(str(entity.id) in semantic_ids,
					"presentation source included a semantically hidden entity")
			_check("position_mt" not in JSON.stringify(observation),
				"participant observation leaked exact coordinates")
	var invalid := Dispatcher.new()
	var invalid_config := _config("duo-spar-v0", "ep_invalid_duo_mode")
	invalid_config.mode = "solo-curriculum-v0"
	_check(not invalid.configure(invalid_config).is_empty(), "duo task launched in solo mode")


func _test_managed_two_seat_text_and_fixed_fallback() -> void:
	var config := _config("duo-checkpoint-race-v0", "ep_duo_managed_v2")
	var host := Host.new()
	_check(host.configure(_launch(config), _secret()).is_empty(), "duo host configure failed")
	var peer := Codec.new()
	_check(peer.configure(config.episode_id, _secret(), "python").is_empty(), "duo peer configure failed")
	var hello: Dictionary = peer.decode(host.begin_handshake().payload)
	_check(bool(hello.get("ok", false)), "duo hello failed")
	var auth := peer.encode("auth", Codec.ZERO_HASH, {"attachment_ticket": TICKET})
	var ready_encoded: Dictionary = host.receive(auth.payload)
	var ready: Dictionary = peer.decode(ready_encoded.payload)
	_check(bool(ready.get("ok", false)), "duo ready failed")
	if not bool(ready.get("ok", false)):
		return
	_check(ready.frame.body.observations.size() == 2, "ready frame omitted a participant")
	_check(ready.frame.body.capability_status.implemented_modes == [
		"solo-curriculum-v0", "scripted-duel-v0", "model-duel-v0",
	], "managed capability modes drifted")
	var first_window := _window(config, 0, 0, 10,
		_decision(_action(config, "participant_0", 0, {"move_y": 1000}, "forward")),
		{"disposition": "no_input", "action": null, "fallback": "neutral",
			"no_input_reason": "timeout"})
	var first_request := peer.encode("decision_window", ready.frame.body.state_hash,
		{"window": first_window})
	var first_result := peer.decode(host.receive(first_request.payload).payload)
	_check(bool(first_result.get("ok", false)), "managed duo step failed")
	if not bool(first_result.get("ok", false)):
		return
	var result: Dictionary = first_result.frame.body.result
	_check(result.observations.participant_0.tick == 10 \
		and result.observations.participant_1.tick == 10, "participant ticks diverged")
	_check(result.receipts.participant_0.accepted, "valid seat was neutralized")
	_check(result.receipts.participant_1.no_input_reason == "timeout" \
		and result.receipts.participant_1.applied_ticks == 10,
		"timeout did not create an independent ten-tick neutral receipt")
	var malformed := _window(config, 1, 10, 3,
		_decision(_action(config, "participant_0", 1, {}, "bad_horizon", 3)),
		_decision(_action(config, "participant_1", 1, {}, "bad_horizon", 3)))
	var malformed_request := peer.encode("decision_window", first_result.frame.boundary_hash,
		{"window": malformed})
	var malformed_result := peer.decode(host.receive(malformed_request.payload).payload)
	_check(bool(malformed_result.get("ok", false)), "invalid horizon failed managed session")
	if bool(malformed_result.get("ok", false)):
		var malformed_step: Dictionary = malformed_result.frame.body.result
		_check(malformed_step.observations.participant_0.tick == 20,
			"invalid duel horizon did not advance exactly ten neutral ticks")
		_check(not malformed_step.receipts.participant_0.accepted \
			and not malformed_step.receipts.participant_1.accepted,
			"invalid joint horizon was accepted")


func _test_versioned_replay_for_all_duo_games() -> void:
	for task_id: String in TASKS:
		var config := _config(task_id, "ep_replay_%s" % task_id.replace("-", "_"))
		var dispatcher := Dispatcher.new()
		_check(dispatcher.configure(config).is_empty(), "%s replay dispatcher configure failed" % task_id)
		var body := {"schema_version": "llm-controller/episode-replay/1.0.0",
			"protocol_version": Identity.PROTOCOL_VERSION,
			"protocol_package_sha256": Identity.SHA256, "config": config,
			"config_sha256": Canonical.sha256_bytes(Canonical.canonical_bytes(config)),
			"initial_observations": dispatcher.observe_all(),
			"initial_state_hash": dispatcher.checkpoint_hash(), "steps": [],
			"final_terminal": {}, "final_state_hash": ""}
		for index: int in 120:
			var decision_0 := {"disposition": "no_input", "action": null,
				"fallback": "neutral", "no_input_reason": "missing"}
			if task_id == "duo-relay-control-v0":
				decision_0 = _decision(_action(config, "participant_0", index,
					{"move_y": 1000} if index < 3 else {}, "relay_%d" % index))
			var window := _window(config, index, index * 10, 10, decision_0,
				{"disposition": "no_input", "action": null, "fallback": "neutral",
					"no_input_reason": "missing"})
			_check(dispatcher.decision_window_schema_valid(window),
				"%s replay window %d was invalid" % [task_id, index])
			var result: Dictionary = dispatcher.step_window(window)
			body.steps.append({"decision_window": window, "result": result})
			if dispatcher.terminal().ended:
				break
		body.final_terminal = dispatcher.terminal()
		body.final_state_hash = dispatcher.checkpoint_hash()
		var replay := body.duplicate(true)
		replay["ledger_sha256"] = Canonical.sha256_bytes(Canonical.canonical_bytes(body))
		var payload := Canonical.canonical_bytes(replay)
		if task_id == "duo-relay-control-v0" and not _replay_output_path().is_empty():
			var replay_file := FileAccess.open(_replay_output_path(), FileAccess.WRITE)
			if replay_file == null:
				_check(false, "could not write requested relay replay fixture")
			else:
				replay_file.store_buffer(payload)
				replay_file.close()
		if not _replay_output_directory().is_empty():
			var task_path := _replay_output_directory().path_join("%s.json" % task_id)
			var task_file := FileAccess.open(task_path, FileAccess.WRITE)
			if task_file == null:
				_check(false, "could not write requested %s replay fixture" % task_id)
			else:
				task_file.store_buffer(payload)
				task_file.close()
		var first: Dictionary = ReplayDispatcher.new().verify(payload)
		var second: Dictionary = ReplayDispatcher.new().verify(payload)
		_check(bool(first.get("ok", false)), "%s replay failed: %s bytes=%d parse=%s" % [
			task_id, first.get("code"), payload.size(),
			str(Canonical.parse_canonical(payload, 16 * 1024 * 1024).get("code")) \
				+ " noncanonical=" + _noncanonical_path(replay),
		])
		_check(first == second, "%s replay verification was nondeterministic" % task_id)
		_check(first.get("final_state_hash") == dispatcher.checkpoint_hash(),
			"%s verified hash drifted" % task_id)


func _window(
	config: Dictionary, sequence: int, start_tick: int, duration: int,
	decision_0: Dictionary, decision_1: Dictionary,
) -> Dictionary:
	return {"episode_id": config.episode_id, "observation_seq": sequence,
		"mode": config.mode, "start_tick": start_tick, "duration_ticks": duration,
		"decisions": {"participant_0": decision_0, "participant_1": decision_1}}


func _decision(action: Dictionary) -> Dictionary:
	return {"disposition": "accepted", "action": action,
		"fallback": "none", "no_input_reason": null}


func _action(
	config: Dictionary, participant_id: String, sequence: int, values: Dictionary,
	label: String, duration: int = 10,
) -> Dictionary:
	var buttons := {"interact": false, "primary": false, "guard": false, "dash": false,
		"ability_1": false, "ability_2": false, "cycle_item": false, "cancel": false}
	for key: String in buttons:
		buttons[key] = bool(values.get(key, false))
	return {"protocol_version": Identity.PROTOCOL_VERSION, "episode_id": config.episode_id,
		"observation_seq": sequence,
		"action_id": "%s_%s_%d" % [label, participant_id, sequence],
		"control": {"move_x": int(values.get("move_x", 0)),
			"move_y": int(values.get("move_y", 0)), "look_x": int(values.get("look_x", 0)),
			"look_y": 0, "duration_ticks": duration, "buttons": buttons},
		"intent_label": label, "memory_update": ""}


func _check(condition: bool, message: String) -> void:
	if not condition:
		failures.append(message)


func _noncanonical_path(value: Variant, path: String = "$") -> String:
	if typeof(value) in [TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING]:
		return ""
	if value is Array:
		for index: int in value.size():
			var found := _noncanonical_path(value[index], "%s[%d]" % [path, index])
			if not found.is_empty():
				return found
		return ""
	if value is Dictionary:
		for key: Variant in value:
			if not key is String:
				return "%s.<key:%s>" % [path, type_string(typeof(key))]
			var found := _noncanonical_path(value[key], "%s.%s" % [path, key])
			if not found.is_empty():
				return found
		return ""
	return "%s<%s>" % [path, type_string(typeof(value))]


func _replay_output_path() -> String:
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--write-duo-replay="):
			return argument.trim_prefix("--write-duo-replay=")
	return ""


func _replay_output_directory() -> String:
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--write-duo-replay-dir="):
			return argument.trim_prefix("--write-duo-replay-dir=")
	return ""


func _finish() -> void:
	if failures.is_empty():
		print("DUO_V2_MANAGED_REPLAY_OK")
		quit(0)
		return
	for failure: String in failures:
		push_error("DUO_V2_MANAGED_REPLAY_FAILURE: %s" % failure)
	print("DUO_V2_MANAGED_REPLAY_FAILED count=%d" % failures.size())
	quit(1)
