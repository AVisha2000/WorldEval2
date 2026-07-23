class_name EmbodimentControlGameDispatcherV2
extends RefCounted

const MazeAuthority := preload("res://scripts/embodiment/control_games/movement_maze_authority.gd")
const CourseAuthority := preload("res://scripts/embodiment/control_games/operator_action_course_authority.gd")
const RaceAuthority := preload("res://scripts/embodiment/duo_games/checkpoint_race_authority.gd")
const RelayAuthority := preload("res://scripts/embodiment/duo_games/relay_control_authority.gd")
const SparAuthority := preload("res://scripts/embodiment/duo_games/spar_authority.gd")
const ResourceRelayAuthority := preload(
	"res://scripts/embodiment/duo_games/resource_relay_authority.gd"
)
const RtsSkirmishAuthority := preload(
	"res://scripts/embodiment/rts_skirmish/rts_skirmish_authority.gd"
)
const RtsSkirmishV1Authority := preload(
	"res://scripts/embodiment/rts_skirmish/rts_skirmish_v1_authority.gd"
)
const Codec := preload("res://scripts/embodiment/transport/embodiment_frame_codec.gd")
const ProtocolIdentity := preload("res://scripts/embodiment/v2/protocol/embodiment_protocol_package_identity_v2.gd")

const CONFIG_FIELDS := [
	"protocol_version", "episode_id", "mode", "task_id", "seed", "observation_profile",
	"timing_track", "maximum_episode_ticks", "participant_ids",
]
const WINDOW_FIELDS := ["episode_id", "observation_seq", "mode", "start_tick", "duration_ticks", "decisions"]
## The additive RTS planner deliberately has its own envelope.  It is only accepted for the
## RTS authority; every older task continues to use the frozen ordinary decision window.
const RTS_TASK_WINDOW_FIELDS := ["episode_id", "observation_seq", "mode", "start_tick", "duration_ticks", "plans"]
const DECISION_FIELDS := ["disposition", "action", "fallback", "no_input_reason"]
const RTS_TASK_MEMORY_PREFIX := "rts-task-plan-"
const SOLO_TASKS := ["movement-maze-v0", "operator-action-course-v0"]
const DUO_TASKS := [
	"duo-checkpoint-race-v0", "duo-relay-control-v0", "duo-spar-v0",
	"duo-resource-relay-v0",
	"rts-skirmish-v0", "rts-skirmish-v1",
]
## Public capability status is part of the immutable llm-controller/0.2.0 package.  An additive
## internal dispatcher target must not silently expand that locked protocol vocabulary.
const PROTOCOL_TASKS := [
	"movement-maze-v0", "operator-action-course-v0",
	"duo-checkpoint-race-v0", "duo-relay-control-v0", "duo-spar-v0",
	"duo-resource-relay-v0",
	"rts-skirmish-v0",
]
const TASKS := [
	"movement-maze-v0", "operator-action-course-v0",
	"duo-checkpoint-race-v0", "duo-relay-control-v0", "duo-spar-v0",
	"duo-resource-relay-v0",
	"rts-skirmish-v0", "rts-skirmish-v1",
]

var authority = null
var config := {}
## When a live RTS task window is stepped, this is the standards-compatible ordinary window
## that carries the sealed task-plan evidence.  Capture code writes this into the v2 replay,
## so frozen replay envelopes do not need a new step shape.
var last_replay_decision_window: Dictionary = {}
## Private, sealed replay evidence.  It is never exposed through observations or presentation.
var last_rts_task_plan_window: Dictionary = {}


func configure(value: Dictionary) -> PackedStringArray:
	var errors := PackedStringArray()
	var task_id := str(value.get("task_id", ""))
	var solo := task_id in SOLO_TASKS
	var expected_participants := ["participant_0"] if solo \
		else ["participant_0", "participant_1"]
	if not Codec._has_exact_fields(value, CONFIG_FIELDS) \
		or value.get("protocol_version") != ProtocolIdentity.PROTOCOL_VERSION \
		or not Codec._valid_episode_id(str(value.get("episode_id", ""))) \
		or task_id not in TASKS \
		or (solo and value.get("mode") != "solo-curriculum-v0") \
		or (not solo and value.get("mode") not in ["scripted-duel-v0", "model-duel-v0"]) \
		or value.get("timing_track") != "step-locked-v1" \
		or value.get("participant_ids") != expected_participants \
		or value.get("observation_profile") not in ["text-visible-v1", "hybrid-visible-v1"] \
		or typeof(value.get("seed")) != TYPE_INT or value.seed < 0 \
		or typeof(value.get("maximum_episode_ticks")) != TYPE_INT \
		or value.maximum_episode_ticks < 1 or value.maximum_episode_ticks > 18000 \
		or (not solo and value.maximum_episode_ticks != 1200):
		errors.append("control_game_config_invalid")
		return errors
	config = value.duplicate(true)
	last_replay_decision_window = {}
	last_rts_task_plan_window = {}
	match task_id:
		"movement-maze-v0": authority = MazeAuthority.new()
		"operator-action-course-v0": authority = CourseAuthority.new()
		"duo-checkpoint-race-v0": authority = RaceAuthority.new()
		"duo-relay-control-v0": authority = RelayAuthority.new()
		"duo-spar-v0": authority = SparAuthority.new()
		"duo-resource-relay-v0": authority = ResourceRelayAuthority.new()
		"rts-skirmish-v0": authority = RtsSkirmishAuthority.new()
		"rts-skirmish-v1": authority = RtsSkirmishV1Authority.new()
	errors.append_array(authority.configure(value))
	if not errors.is_empty():
		authority = null
	return errors


func step_window(window: Variant) -> Dictionary:
	assert(authority != null, "configure before stepping")
	if config.task_id in ["rts-skirmish-v0", "rts-skirmish-v1"]:
		if _is_rts_task_window(window):
			last_replay_decision_window = _rts_compatibility_window(window)
			last_rts_task_plan_window = window.duplicate(true)
			return authority.step_task_plan_window(window)
		var embedded_task_window := _rts_task_window_from_compatibility(window)
		if not embedded_task_window.is_empty():
			last_replay_decision_window = window.duplicate(true)
			last_rts_task_plan_window = embedded_task_window.duplicate(true)
			return authority.step_task_plan_window(embedded_task_window)
		last_replay_decision_window = window.duplicate(true) if window is Dictionary else {}
		last_rts_task_plan_window = {}
	if config.task_id in DUO_TASKS:
		return authority.step_window(window)
	var translated := _translate_window(window)
	return authority.step_window(translated.action, translated.duration_ticks, translated.no_input_reason)


func observe_all() -> Dictionary:
	if config.task_id in DUO_TASKS:
		return authority.observe_all()
	return {"participant_0": authority.observe()}


func checkpoint_hash() -> String:
	return authority.checkpoint_hash()


func terminal() -> Dictionary:
	return authority.terminal.duplicate(true)


func capability_status() -> Dictionary:
	return {
		"implemented_modes": ["solo-curriculum-v0", "scripted-duel-v0", "model-duel-v0"],
		"implemented_observation_profiles": ["text-visible-v1", "hybrid-visible-v1"],
		"implemented_tasks": PROTOCOL_TASKS.duplicate(),
		"certified_modes": [],
		"certified_observation_profiles": [],
		"scored_observation_profiles": [],
	}


func participant_presentation_source(participant_id: String) -> Dictionary:
	return authority.participant_presentation_source(participant_id)


func decision_window_schema_valid(window: Variant) -> bool:
	if config.task_id in ["rts-skirmish-v0", "rts-skirmish-v1"] and _is_rts_task_window(window):
		return _rts_task_window_schema_valid(window)
	var participants: Array = config.participant_ids
	var duo: bool = config.task_id in DUO_TASKS
	if not window is Dictionary or not Codec._has_exact_fields(window, WINDOW_FIELDS) \
		or window.get("episode_id") != config.episode_id \
		or typeof(window.get("observation_seq")) != TYPE_INT \
		or window.observation_seq != authority.observation_seq \
		or window.get("mode") != config.mode \
		or typeof(window.get("start_tick")) != TYPE_INT or window.start_tick != authority.tick \
		or typeof(window.get("duration_ticks")) != TYPE_INT \
		or (duo and window.duration_ticks != 10) \
		or (not duo and (window.duration_ticks < 1 or window.duration_ticks > 20)) \
		or not window.get("decisions") is Dictionary \
		or window.decisions.size() != participants.size():
		return false
	for participant_id: String in participants:
		if not window.decisions.has(participant_id):
			return false
		var decision: Variant = window.decisions[participant_id]
		if not decision is Dictionary or not Codec._has_exact_fields(decision, DECISION_FIELDS):
			return false
		if decision.disposition == "no_input":
			if decision.action != null or decision.fallback != "neutral" \
				or decision.no_input_reason not in ["missing", "invalid", "timeout", "stale_observation"]:
				return false
		elif decision.disposition == "accepted":
			var valid_action: bool
			if duo:
				valid_action = authority._valid_action(decision.action)
			else:
				valid_action = decision.action is Dictionary \
					and authority._validate_action(decision.action).is_empty()
			if not valid_action or decision.fallback != "none" or decision.no_input_reason != null \
				or decision.action.control.duration_ticks != window.duration_ticks:
				return false
		else:
			return false
	return true


func _is_rts_task_window(window: Variant) -> bool:
	return window is Dictionary and window.has("plans") and not window.has("decisions")


func _rts_task_window_schema_valid(window: Variant) -> bool:
	if not window is Dictionary or not Codec._has_exact_fields(window, RTS_TASK_WINDOW_FIELDS) \
		or window.get("episode_id") != config.episode_id \
		or typeof(window.get("observation_seq")) != TYPE_INT \
		or window.observation_seq != authority.observation_seq \
		or window.get("mode") != config.mode \
		or typeof(window.get("start_tick")) != TYPE_INT or window.start_tick != authority.tick \
		or typeof(window.get("duration_ticks")) != TYPE_INT or window.duration_ticks != 10 \
		or not window.get("plans") is Dictionary or window.plans.size() != 2:
		return false
	for participant_id: String in config.participant_ids:
		if not window.plans.has(participant_id) \
			or not authority.task_plan_schema_valid(participant_id, window.plans[participant_id]):
			return false
	return true


func _rts_compatibility_window(window: Dictionary) -> Dictionary:
	var decisions := {}
	var plans: Dictionary = window.get("plans", {})
	for participant_id: String in config.participant_ids:
		var plan: Variant = plans.get(participant_id)
		if authority.task_plan_schema_valid(participant_id, plan):
			decisions[participant_id] = _rts_compatibility_decision(participant_id, plan)
			continue
		decisions[participant_id] = {
			"disposition": "no_input", "action": null,
			"fallback": "neutral", "no_input_reason": "invalid",
		}
	return {
		"episode_id": window.get("episode_id"),
		"observation_seq": window.get("observation_seq"),
		"mode": window.get("mode"),
		"start_tick": window.get("start_tick"),
		"duration_ticks": window.get("duration_ticks"),
		"decisions": decisions,
	}


func _rts_compatibility_decision(participant_id: String, plan: Dictionary) -> Dictionary:
	var buttons := {}
	for button: String in ["interact", "primary", "guard", "dash", "ability_1", "ability_2", "cycle_item", "cancel"]:
		buttons[button] = false
	return {
		"disposition": "accepted",
		"action": {
			"protocol_version": ProtocolIdentity.PROTOCOL_VERSION,
			"episode_id": config.episode_id,
			"observation_seq": authority.observation_seq,
			"action_id": "rts_task_%s_%d" % [participant_id, authority.observation_seq],
			"control": {"move_x": 0, "move_y": 0, "look_x": 0, "look_y": 0, "duration_ticks": 10, "buttons": buttons},
			"intent_label": str(plan.intent_label),
			"memory_update": "",
		},
		"fallback": "none", "no_input_reason": null,
	}


func _rts_task_window_from_compatibility(window: Variant) -> Dictionary:
	if not window is Dictionary or not Codec._has_exact_fields(window, WINDOW_FIELDS) \
		or not window.get("decisions") is Dictionary:
		return {}
	var plans := {}
	var found_embedded_plan := false
	for participant_id: String in config.participant_ids:
		var decision: Variant = window.decisions.get(participant_id)
		if decision is Dictionary and decision.get("disposition") == "no_input":
			plans[participant_id] = null
			continue
		if not decision is Dictionary or decision.get("disposition") != "accepted" \
			or not decision.get("action") is Dictionary:
			return {}
		var memory_update: Variant = decision.action.get("memory_update")
		if not memory_update is String or not str(memory_update).begins_with(RTS_TASK_MEMORY_PREFIX):
			return {}
		found_embedded_plan = true
		var separator := str(memory_update).find(":")
		if separator < RTS_TASK_MEMORY_PREFIX.length():
			return {}
		var payload := str(memory_update).substr(separator + 1).to_utf8_buffer()
		var parsed := Codec.parse_canonical(payload, 2048)
		if not bool(parsed.get("ok", false)) or not parsed.get("value") is Dictionary:
			return {}
		plans[participant_id] = parsed.value
	if not found_embedded_plan:
		return {}
	return {
		"episode_id": window.get("episode_id"), "observation_seq": window.get("observation_seq"),
		"mode": window.get("mode"), "start_tick": window.get("start_tick"),
		"duration_ticks": window.get("duration_ticks"), "plans": plans,
	}


func _translate_window(window: Variant) -> Dictionary:
	var neutral := {"action": null, "duration_ticks": 1, "no_input_reason": "invalid"}
	if not window is Dictionary:
		return neutral
	var duration: Variant = window.get("duration_ticks")
	if typeof(duration) == TYPE_INT and duration >= 1 and duration <= 20:
		neutral.duration_ticks = duration
	if not Codec._has_exact_fields(window, WINDOW_FIELDS) \
		or window.get("episode_id") != config.episode_id \
		or typeof(window.get("observation_seq")) != TYPE_INT \
		or window.observation_seq != authority.observation_seq \
		or window.get("mode") != config.mode \
		or typeof(window.get("start_tick")) != TYPE_INT or window.start_tick != authority.tick \
		or typeof(window.get("decisions")) != TYPE_DICTIONARY \
		or window.decisions.size() != 1 or not window.decisions.has("participant_0"):
		return neutral
	var decision: Variant = window.decisions.participant_0
	if not decision is Dictionary or not Codec._has_exact_fields(decision, DECISION_FIELDS):
		return neutral
	if decision.disposition == "no_input" and decision.action == null \
		and decision.fallback == "neutral" \
		and decision.no_input_reason in ["missing", "invalid", "timeout", "stale_observation"]:
		neutral.no_input_reason = decision.no_input_reason
		return neutral
	if decision.disposition == "accepted" and decision.action is Dictionary \
		and decision.fallback == "none" and decision.no_input_reason == null \
		and decision.action.get("control") is Dictionary \
		and decision.action.control.get("duration_ticks") == neutral.duration_ticks:
		return {"action": decision.action, "duration_ticks": neutral.duration_ticks, "no_input_reason": "invalid"}
	return neutral
