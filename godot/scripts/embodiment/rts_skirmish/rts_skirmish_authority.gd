class_name EmbodimentRtsSkirmishAuthority
extends "res://scripts/embodiment/duo_games/duo_game_authority.gd"

## Deterministic, first-class-unit authority for the cached RTS story.  `operators` remains a
## compatibility mirror for the v2 controller transport; all gameplay truth lives in `units`.

const TaskPlan := preload("res://scripts/embodiment/rts_skirmish/rts_task_plan_contract.gd")
const TASK_ID := "rts-skirmish-v0"
const UNIT_SPEED_MT := 80
const UNIT_HEALTH := 1000
const TOWN_HALL_HEALTH := 2000
const TOWER_HEALTH := 1200
const GATHER_TICKS := 60
const DEPOSIT_TICKS := 20
const ARM_TICKS := 40
const INTERACT_RADIUS_MT := 160
const ATTACK_RANGE_MT := 1200
const SIEGE_RANGE_MT := 520
const FORMATION_LANES_MT := [-500, 0, 500]
const TOWN_HALLS := {"participant_0": Vector2i(-5000, 5000), "participant_1": Vector2i(5000, -5000)}
const BARRACKS := {"participant_0": Vector2i(-3600, 5000), "participant_1": Vector2i(3600, -5000)}
const TOWERS := {"participant_0": Vector2i(-5000, 3500), "participant_1": Vector2i(5000, -3500)}
const BRIDGE_RALLY := {"participant_0": Vector2i(-700, 700), "participant_1": Vector2i(700, -700)}
const BRIDGE_FIGHT := {"participant_0": Vector2i(-380, 380), "participant_1": Vector2i(380, -380)}
## Shared with the broadcast map.  These nodes are reset-time authority state, never
## visibility-triggered spawns; Red is the exact rotational mirror of Blue.
const TREE_POSITIONS := {
	"participant_0": [Vector2i(-6200, 5000), Vector2i(-7000, 6100), Vector2i(-5700, 6900), Vector2i(-7600, 7100)],
	"participant_1": [Vector2i(6200, -5000), Vector2i(7000, -6100), Vector2i(5700, -6900), Vector2i(7600, -7100)],
}
const ORE_POSITIONS := {
	"participant_0": [Vector2i(-5200, 6100), Vector2i(-6400, 7400), Vector2i(-7700, 6500)],
	"participant_1": [Vector2i(5200, -6100), Vector2i(6400, -7400), Vector2i(7700, -6500)],
}

var units: Dictionary = {}
var resources: Dictionary = {}
var structures: Dictionary = {}
var economy: Dictionary = {}
var combat_stats: Dictionary = {}
var last_task_plans: Dictionary = {}
var story_phase := "opening_economy"
var victory_latched := false


func expected_task_id() -> String:
	return TASK_ID


func configure(config: Dictionary) -> PackedStringArray:
	var errors := PackedStringArray()
	if config.get("protocol_version") != PROTOCOL_VERSION: errors.append("protocol_version_invalid")
	if config.get("task_id") != TASK_ID: errors.append("task_id_invalid")
	if not config.get("episode_id") is String or not str(config.episode_id).begins_with("ep_"): errors.append("episode_id_invalid")
	if config.get("participant_ids") != PARTICIPANTS: errors.append("participant_ids_invalid")
	if config.get("maximum_episode_ticks") != MAXIMUM_TICKS: errors.append("maximum_episode_ticks_invalid")
	if not errors.is_empty(): return errors
	task_id = TASK_ID
	episode_id = str(config.episode_id)
	reset()
	return errors


func reset() -> Dictionary:
	tick = 0
	observation_seq = 0
	event_seq = 0
	previous_receipts = {}
	recent_events = []
	replay_windows = []
	terminal = {"ended": false, "outcome": "running", "reason": "in_progress"}
	winner_id = null
	invalid_windows = {"participant_0": 0, "participant_1": 0}
	units = {}
	resources = {}
	structures = {}
	economy = {}
	combat_stats = {}
	last_task_plans = {}
	story_phase = "opening_economy"
	victory_latched = false
	for participant_id: String in PARTICIPANTS:
		structures[participant_id] = {"town_hall": {"health": TOWN_HALL_HEALTH, "state": "intact", "built": true}, "barracks": {"state": "unbuilt", "progress_ticks": 0, "built": false}, "tower": {"health": TOWER_HEALTH, "state": "unbuilt", "progress_ticks": 0, "built": false}}
		economy[participant_id] = {"wood": 0, "ore": 0}
		combat_stats[participant_id] = {"hits_landed": 0, "hits_received": 0, "knockouts": 0, "town_hall_damage_dealt": 0, "town_hall_damage_received": 0}
		for index: int in 4:
			var id := _resource_id(participant_id, "tree", index)
			resources[id] = {"id": id, "team": participant_id, "kind": "tree", "position_mt": TREE_POSITIONS[participant_id][index], "stock": 1, "state": "standing", "reserved_by": ""}
		for index: int in 3:
			var ore_id := _resource_id(participant_id, "ore", index)
			resources[ore_id] = {"id": ore_id, "team": participant_id, "kind": "ore", "position_mt": ORE_POSITIONS[participant_id][index], "stock": 1, "state": "available", "reserved_by": ""}
		_spawn_unit(participant_id, 0)
	operators = {"participant_0": _new_operator(TOWN_HALLS.participant_0, 2), "participant_1": _new_operator(TOWN_HALLS.participant_1, 6)}
	for participant_id: String in PARTICIPANTS: _sync_operator(participant_id)
	return observe_all()


func _new_unit(participant_id: String, index: int) -> Dictionary:
	var id := _unit_id(participant_id, index)
	return {"id": id, "team": participant_id, "role": "worker", "position_mt": TOWN_HALLS[participant_id], "heading": 2 if participant_id == "participant_0" else 6, "health": UNIT_HEALTH, "alive": true, "task": "idle", "milestone": "idle", "target_id": "", "progress_ticks": 0, "arming_ticks": 0, "attack_cooldown_ticks": 0, "carrying": "", "animation_state": "idle", "spawned_tick": tick, "deposits": 0, "materials_gathered": 0, "distance_travelled_mt": 0}


func _spawn_unit(participant_id: String, index: int, events: Array[Dictionary] = []) -> void:
	var id := _unit_id(participant_id, index)
	if units.has(id): return
	units[id] = _new_unit(participant_id, index)
	if index > 0: events.append(_event("rts_unit_spawned", [participant_id], {"unit_id": id, "unit_count": _living_count(participant_id)}))


func _new_operator(position: Vector2i, heading: int) -> Dictionary:
	var value: Dictionary = super._new_operator(position, heading)
	value.carrying = ""
	value.wood = 0
	value.ore = 0
	value.materials_gathered = 0
	value.deposits = 0
	value.barracks_built = 0
	value.towers_built = 0
	value.units_trained = 0
	value.central_hold_ticks = 0
	value.town_hall_damage_dealt = 0
	value.town_hall_damage_received = 0
	value.presentation_intent = "Deploying workers"
	return value


func _apply_joint_tick(_controls: Dictionary, _first_tick: bool, _receipt_codes: Dictionary, events: Array[Dictionary]) -> void:
	# The canonical demo is a scripted policy. Validity still controls receipts and replay input,
	# while the autonomous authority makes every intermediate walk/hold state reproducible.
	_story_tick(events)
	for participant_id: String in PARTICIPANTS: _sync_operator(participant_id)


func _story_tick(events: Array[Dictionary]) -> void:
	if tick < 340:
		story_phase = "opening_economy"
		_update_opening_economy(events)
	elif tick < 520:
		story_phase = "final_economy"
		_update_final_economy(events)
	elif tick < 650:
		story_phase = "construction_and_arming"
		_update_construction(events)
	elif tick < 800:
		story_phase = "rally"
		_update_rally()
	elif tick < 920:
		story_phase = "bridge_battle"
		_update_battle(events)
	elif tick < 1030:
		story_phase = "pursuit"
		_update_pursuit(events)
	elif tick < 1120:
		story_phase = "tower_siege"
		_update_structure_attack("tower", events)
	elif tick < 1190:
		story_phase = "town_hall_siege"
		_update_structure_attack("town_hall", events)
	else:
		story_phase = "victory"
		for unit_id: String in _team_unit_ids("participant_0"):
			if bool(units[unit_id].alive): units[unit_id].task = "celebrating"; units[unit_id].animation_state = "celebrate"


func _update_opening_economy(events: Array[Dictionary]) -> void:
	for participant_id: String in PARTICIPANTS:
		var first: Dictionary = units[_unit_id(participant_id, 0)]
		if int(first.deposits) == 0:
			_run_gather_cycle(first, _resource_id(participant_id, "tree", 0), events)
			continue
		_spawn_unit(participant_id, 1, events)
		var second: Dictionary = units[_unit_id(participant_id, 1)]
		if int(first.deposits) < 2: _run_gather_cycle(first, _resource_id(participant_id, "ore", 0), events)
		if int(second.deposits) < 1: _run_gather_cycle(second, _resource_id(participant_id, "tree", 1), events)
		if int(first.deposits) + int(second.deposits) >= 3:
			_spawn_unit(participant_id, 2, events)
			# Unit 2's unlock is the milestone boundary into the three-worker fan-out.  Starting
			# immediately preserves the full 60-tick harvest and 20-tick deposit holds while
			# still completing the economy beat before construction begins at tick 520.
			_update_final_economy_for_team(participant_id, events)


func _update_final_economy(events: Array[Dictionary]) -> void:
	for participant_id: String in PARTICIPANTS:
		_spawn_unit(participant_id, 1, events)
		_spawn_unit(participant_id, 2, events)
		_update_final_economy_for_team(participant_id, events)


func _update_final_economy_for_team(participant_id: String, events: Array[Dictionary]) -> void:
	var targets := [_resource_id(participant_id, "tree", 2), _resource_id(participant_id, "tree", 3), _resource_id(participant_id, "ore", 1)]
	var required_deposits := [3, 2, 1]
	for index: int in 3:
		var unit: Dictionary = units[_unit_id(participant_id, index)]
		if int(unit.deposits) < required_deposits[index]:
			_run_gather_cycle(unit, targets[index], events)


func _update_construction(events: Array[Dictionary]) -> void:
	for participant_id: String in PARTICIPANTS:
		var builder: Dictionary = units[_unit_id(participant_id, 0)]
		var barracks: Dictionary = structures[participant_id].barracks
		var tower: Dictionary = structures[participant_id].tower
		if tick == 520:
			if int(economy[participant_id].wood) < 2 or int(economy[participant_id].ore) < 1:
				events.append(_event("rts_construction_failed", [participant_id], {"structure": "barracks", "reason": "resources_insufficient"}))
			else:
				economy[participant_id].wood -= 2
				economy[participant_id].ore -= 1
				barracks.state = "building"
				events.append(_event("rts_construction_started", [participant_id], {"structure": "barracks"}))
		if tick < 550:
			if _walk_to(builder, BARRACKS[participant_id], "building", "barracks"):
				builder.animation_state = "build"
				barracks.progress_ticks += 1
		elif tick == 550 and str(barracks.state) == "building":
			barracks.state = "active"
			barracks.built = true
			events.append(_event("rts_structure_built", [participant_id], {"structure": "barracks"}))

		if tick >= 550:
			_update_unit_arming(participant_id, events)
		if tick == 590:
			if int(economy[participant_id].wood) < 1 or int(economy[participant_id].ore) < 1:
				events.append(_event("rts_construction_failed", [participant_id], {"structure": "tower", "reason": "resources_insufficient"}))
			else:
				economy[participant_id].wood -= 1
				economy[participant_id].ore -= 1
				tower.state = "building"
				events.append(_event("rts_construction_started", [participant_id], {"structure": "tower"}))
		if tick >= 590 and tick < 640:
			if _walk_to(builder, TOWERS[participant_id], "building", "tower"):
				builder.animation_state = "build"
				tower.progress_ticks += 1
		elif tick == 640 and str(tower.state) == "building":
			tower.state = "active"
			tower.built = true
			events.append(_event("rts_structure_built", [participant_id], {"structure": "tower"}))


func _update_unit_arming(participant_id: String, events: Array[Dictionary]) -> void:
	for unit_id: String in _team_unit_ids(participant_id):
		var unit: Dictionary = units[unit_id]
		if not bool(unit.alive) or str(unit.role) == "militia":
			continue
		# Every worker arms at a deterministic slot around the barracks.  This keeps the
		# first-class actors physically distinct before they become the bridge formation.
		if _walk_to(unit, _arming_slot(participant_id, unit_id), "arming", "barracks"):
			unit.animation_state = "build"
			unit.arming_ticks += 1
			if int(unit.arming_ticks) >= ARM_TICKS:
				unit.role = "militia"
				unit.task = "idle"
				unit.animation_state = "idle"
				events.append(_event("rts_unit_armed", [participant_id], {"unit_id": unit_id}))


func _update_rally() -> void:
	for participant_id: String in PARTICIPANTS:
		for index: int in 3:
			var unit: Dictionary = units.get(_unit_id(participant_id, index), {})
			if not unit.is_empty() and bool(unit.alive):
				_walk_to(unit, _bridge_lane(participant_id, index), "rallying", "bridge")


func _update_battle(events: Array[Dictionary]) -> void:
	for participant_id: String in PARTICIPANTS:
		for index: int in 3:
			var unit_id: String = _unit_id(participant_id, index)
			if not units.has(unit_id):
				continue
			var unit: Dictionary = units[unit_id]
			if not bool(unit.alive):
				continue
			var rival_id: String = _unit_id(_rival(participant_id), index)
			var rival: Dictionary = units.get(rival_id, {})
			var lane: Vector2i = _bridge_lane(participant_id, index)
			if not _walk_to(unit, lane, "attacking", rival_id):
				continue
			if not rival.is_empty() and bool(rival.alive) and _face_target(unit, rival.position_mt):
				unit.animation_state = "guard" if participant_id == "participant_1" and tick < 850 else "attack"
	if tick in [810, 830, 845]: _damage_unit("blue_0", "red_0", 250, events)
	if tick in [820, 835, 849]: _damage_unit("red_0", "blue_1", 250, events)
	if tick in [860, 875, 895]: _damage_unit("red_1", "blue_2", 250, events)
	if tick == 850: _kill_unit("blue_0", "red_0", events)
	if tick == 880: _kill_unit("red_0", "blue_1", events)
	if tick == 900: _kill_unit("red_1", "blue_2", events)


func _update_pursuit(events: Array[Dictionary]) -> void:
	var red: Dictionary = units["red_2"]
	if bool(red.alive):
		_walk_to(red, TOWN_HALLS.participant_1, "retreating", "town_hall")
	for unit_id: String in ["blue_1", "blue_2"]:
		var blue: Dictionary = units[unit_id]
		if not bool(blue.alive) or not bool(red.alive):
			continue
		if blue.position_mt.distance_squared_to(red.position_mt) > ATTACK_RANGE_MT * ATTACK_RANGE_MT:
			_walk_to(blue, red.position_mt, "attacking", "red_2")
		elif _face_target(blue, red.position_mt):
			blue.task = "attacking"
			blue.target_id = "red_2"
			blue.animation_state = "attack"
	if tick in [940, 970, 1000]: _damage_unit("red_2", "blue_1", 250, events)
	if tick == 1020: _kill_unit("red_2", "blue_1", events)


func _update_structure_attack(structure: String, events: Array[Dictionary]) -> void:
	var target: Vector2i = TOWERS.participant_1 if structure == "tower" else TOWN_HALLS.participant_1
	var state: Dictionary = structures.participant_1[structure]
	for index: int in 2:
		var unit_id: String = ["blue_1", "blue_2"][index]
		var unit: Dictionary = units[unit_id]
		if not bool(unit.alive):
			continue
		# Keep both surviving militia readable around the target instead of occupying one
		# presentation silhouette.  The 500 mt radius remains inside SIEGE_RANGE_MT.
		var attack_position: Vector2i = target + Vector2i(-300, -400 if index == 0 else 400)
		if _walk_to(unit, attack_position, "attacking", "enemy_" + structure) and _face_target(unit, target):
			unit.animation_state = "attack"
	if tick % 2 == 0 and str(state.state) != "destroyed" and _living_attacker_in_range(target):
		var damage: int = 40 if structure == "tower" else 90
		state.health = maxi(0, int(state.health) - damage)
		if structure == "town_hall":
			combat_stats.participant_0.town_hall_damage_dealt += damage
			combat_stats.participant_1.town_hall_damage_received += damage
		events.append(_event("rts_structure_damaged", ["participant_0", "participant_1"], {"structure": structure, "health": int(state.health)}))
		if int(state.health) == 0:
			state.state = "destroyed"
			events.append(_event("rts_structure_destroyed", ["participant_0", "participant_1"], {"structure": structure}))
			# The final stronghold loss also collapses the remaining Red production site.
			# This is an authority state transition, rather than a presentation-only hide,
			# so the replay and public broadcast agree that Red has no surviving structures.
			if structure == "town_hall":
				var red_barracks: Dictionary = structures.participant_1.barracks
				if str(red_barracks.state) != "destroyed":
					red_barracks.state = "destroyed"
					events.append(_event("rts_structure_destroyed", ["participant_0", "participant_1"], {"structure": "barracks"}))


func _run_gather_cycle(unit: Dictionary, resource_id: String, events: Array[Dictionary]) -> void:
	if not bool(unit.alive): return
	var resource: Dictionary = resources[resource_id]
	if unit.carrying.is_empty() and unit.milestone != "returning":
		_walk_to(unit, resource.position_mt, "walking", resource_id)
		if _at(unit, resource.position_mt):
			unit.task = "harvesting"; unit.animation_state = "gather"; unit.target_id = resource_id; unit.progress_ticks += 1
			resource.reserved_by = unit.id
			resource.state = "harvesting"
			if int(unit.progress_ticks) >= GATHER_TICKS:
				unit.carrying = "wood" if resource.kind == "tree" else "ore"
				unit.milestone = "returning"; unit.progress_ticks = 0; resource.stock = maxi(0, int(resource.stock) - 1)
				resource.state = "stump" if resource.kind == "tree" and int(resource.stock) == 0 else ("cracked" if resource.kind == "ore" and int(resource.stock) == 0 else resource.state)
				resource.reserved_by = ""
				events.append(_event("rts_material_gathered", [str(unit.team)], {"unit_id": unit.id, "kind": unit.carrying, "resource_id": resource_id}))
	else:
		_walk_to(unit, TOWN_HALLS[str(unit.team)], "returning", "town_hall")
		if _at(unit, TOWN_HALLS[str(unit.team)]):
			unit.task = "depositing"; unit.animation_state = "gather"; unit.progress_ticks += 1
			if int(unit.progress_ticks) >= DEPOSIT_TICKS:
				var kind: String = str(unit.carrying)
				unit.deposits += 1; unit.materials_gathered += 1; economy[str(unit.team)][kind] += 1; unit.carrying = ""; unit.milestone = "idle"; unit.progress_ticks = 0
				events.append(_event("rts_material_deposited", [str(unit.team)], {"unit_id": unit.id, "kind": kind, "team_total": int(economy[str(unit.team)][kind])}))


func _walk_to(unit: Dictionary, destination: Vector2i, task: String, target_id: String = "") -> bool:
	if not bool(unit.alive): return false
	if not target_id.is_empty(): unit.target_id = target_id
	var offset: Vector2i = destination - unit.position_mt
	if offset == Vector2i.ZERO:
		unit.task = task
		return true
	var target_heading := _heading_to(unit.position_mt, destination)
	if int(unit.heading) != target_heading:
		unit.heading = _next_heading(int(unit.heading), target_heading)
		unit.task = "turning"; unit.animation_state = "turn"
		return false
	var step: Vector2i
	if offset.length_squared() <= UNIT_SPEED_MT * UNIT_SPEED_MT:
		step = offset
	else:
		var forward: Vector2i = FORWARD_BASIS[int(unit.heading)]
		step = Vector2i(_divide_toward_zero(forward.x * UNIT_SPEED_MT, 1000), _divide_toward_zero(forward.y * UNIT_SPEED_MT, 1000))
	unit.position_mt += step
	unit.distance_travelled_mt += int(round(sqrt(float(step.length_squared()))))
	unit.task = task; unit.milestone = task; unit.animation_state = "walk"
	return unit.position_mt == destination


func _face_target(unit: Dictionary, target: Vector2i) -> bool:
	var desired := _heading_to(unit.position_mt, target)
	if int(unit.heading) == desired:
		return true
	unit.heading = _next_heading(int(unit.heading), desired)
	unit.task = "turning"
	unit.animation_state = "turn"
	return false


func _kill_unit(victim_id: String, attacker_id: String, events: Array[Dictionary]) -> void:
	var victim: Dictionary = units[victim_id]
	if not bool(victim.alive): return
	# A scripted casualty is still a normal, integer combat transition.  Emit the final
	# public hit (250 -> 0 in the locked cinematic) before the defeat event so health bars
	# never jump from a non-zero value straight to a removed unit.
	_damage_unit(victim_id, attacker_id, int(victim.health), events, true)
	victim.health = 0; victim.alive = false; victim.task = "defeated"; victim.animation_state = "defeat"; victim.target_id = attacker_id
	combat_stats[str(units[attacker_id].team)].knockouts += 1
	events.append(_event("rts_unit_lost", [str(victim.team), str(units[attacker_id].team)], {"unit_id": victim_id, "remaining_units": _living_count(str(victim.team))}))


func _damage_unit(victim_id: String, attacker_id: String, damage: int, events: Array[Dictionary], allow_defeat: bool = false) -> void:
	var victim: Dictionary = units[victim_id]
	if not bool(victim.alive): return
	victim.health = maxi(0 if allow_defeat else 1, int(victim.health) - damage)
	victim.task = "hit"; victim.animation_state = "hit"; victim.target_id = attacker_id
	combat_stats[str(units[attacker_id].team)].hits_landed += 1
	combat_stats[str(victim.team)].hits_received += 1
	units[attacker_id].task = "attacking"
	units[attacker_id].animation_state = "attack"
	units[attacker_id].target_id = victim_id
	events.append(_event("rts_unit_hit", [str(victim.team), str(units[attacker_id].team)], {"unit_id": victim_id, "damage": damage, "health": int(victim.health)}))


func _living_attacker_in_range(target: Vector2i) -> bool:
	for unit_id: String in ["blue_1", "blue_2"]:
		var unit: Dictionary = units[unit_id]
		if bool(unit.alive) and unit.position_mt.distance_squared_to(target) <= SIEGE_RANGE_MT * SIEGE_RANGE_MT:
			return true
	return false


func _resolve_terminal(events: Array[Dictionary]) -> void:
	if int(structures.participant_1.town_hall.health) <= 0:
		victory_latched = true
	# Preserve a full 1200-tick authority record after the Town Hall falls; the final eleven
	# seconds are a deterministic celebration hold used by the native 150-second capture.
	if tick >= MAXIMUM_TICKS and not bool(terminal.ended):
		if victory_latched:
			_finish_win("participant_0", "town_hall_destroyed", events)
		else:
			terminal = {"ended": true, "outcome": "draw", "reason": "time_limit_draw"}
			winner_id = null
			_emit_terminal(events)


func _finish_win(participant_id: String, reason: String, events: Array[Dictionary]) -> void:
	if bool(terminal.ended): return
	winner_id = participant_id
	terminal = {"ended": true, "outcome": "win", "reason": reason}
	events.append(_event("episode_won", PARTICIPANTS, {"winner": participant_id, "reason": reason}))
	_emit_terminal(events)


func _emit_terminal(events: Array[Dictionary]) -> void:
	events.append(_event("rts_skirmish_completed", PARTICIPANTS, {"task_id": TASK_ID, "completion_tick": tick, "terminal_outcome": terminal.outcome, "terminal_reason": terminal.reason, "winner_id": winner_id}))
	for participant_id: String in PARTICIPANTS:
		var outcome := "win" if participant_id == winner_id else "loss"
		events.append(_event("rts_skirmish_participant_summary", [participant_id], _summary(participant_id, outcome)))


func _summary(participant_id: String, outcome: String) -> Dictionary:
	var team_units := _team_unit_ids(participant_id)
	var deposits := 0
	for id: String in team_units: deposits += int(units[id].deposits)
	var stats: Dictionary = combat_stats[participant_id]
	return {"task_id": TASK_ID, "completion_tick": tick, "terminal_outcome": terminal.outcome, "terminal_reason": terminal.reason, "participant_id": participant_id, "outcome": outcome, "decision_windows": int(operators[participant_id].decision_windows), "fallback_windows": int(operators[participant_id].fallback_windows), "materials_gathered": deposits, "deposits": deposits, "barracks_built": 1 if bool(structures[participant_id].barracks.built) else 0, "towers_built": 1 if bool(structures[participant_id].tower.built) else 0, "units_trained": team_units.size(), "central_hold_ticks": 0, "town_hall_damage_dealt": int(stats.town_hall_damage_dealt), "town_hall_damage_received": int(stats.town_hall_damage_received), "hits_landed": int(stats.hits_landed), "hits_received": int(stats.hits_received), "knockouts": int(stats.knockouts)}


func observe(participant_id: String) -> Dictionary:
	var entities: Array[Dictionary] = []
	for resource_id: String in _sorted_keys(resources):
		var resource: Dictionary = resources[resource_id]
		if str(resource.team) == participant_id: entities.append({"id": _visible_id(resource_id), "kind": "resource_" + str(resource.kind), "bearing": "front", "distance": "medium", "affordances": ["gather"], "state": str(resource.state)})
	for unit_id: String in _team_unit_ids(participant_id):
		var unit: Dictionary = units[unit_id]
		entities.append({"id": _visible_id(unit_id), "kind": "unit", "bearing": "front", "distance": "near", "affordances": ["task"], "state": "alive" if bool(unit.alive) else "defeated"})
	if story_phase in ["bridge_battle", "pursuit", "tower_siege", "town_hall_siege", "victory"]:
		for unit_id: String in _team_unit_ids(_rival(participant_id)):
			var rival_unit: Dictionary = units[unit_id]
			if bool(rival_unit.alive):
				entities.append({"id": _visible_id(unit_id), "kind": "hostile_unit", "bearing": "front", "distance": "near", "affordances": ["attack_unit"], "state": "alive"})
	entities.append({"id": _visible_id("town_hall"), "kind": "town_hall", "bearing": "front", "distance": "near", "affordances": ["return_material"], "state": str(structures[participant_id].town_hall.state)})
	entities.append({"id": _visible_id("barracks"), "kind": "barracks", "bearing": "front", "distance": "near", "affordances": ["build", "arm"], "state": str(structures[participant_id].barracks.state)})
	entities.append({"id": _visible_id("tower"), "kind": "tower", "bearing": "front", "distance": "near", "affordances": ["build"], "state": str(structures[participant_id].tower.state)})
	if story_phase in ["tower_siege", "town_hall_siege", "victory"]:
		entities.append({"id": _visible_id("enemy_tower"), "kind": "hostile_tower", "bearing": "front", "distance": "medium", "affordances": ["attack_structure"], "state": str(structures[_rival(participant_id)].tower.state)})
		entities.append({"id": _visible_id("enemy_town_hall"), "kind": "hostile_town_hall", "bearing": "front", "distance": "medium", "affordances": ["attack_structure"], "state": str(structures[_rival(participant_id)].town_hall.state)})
	return {"protocol_version": PROTOCOL_VERSION, "episode_id": episode_id, "observation_seq": observation_seq, "tick": tick, "profile": "text-visible-v1", "goal": "Grow workers into militia and destroy the opposing Town Hall.", "remaining_ticks": maxi(0, MAXIMUM_TICKS - tick), "self": {"health_percent": 100, "energy_percent": 100, "facing": "east", "contact": "clear", "inventory": [], "status": [story_phase]}, "visible_entities": entities, "recent_events": _events_for(participant_id), "previous_receipt": previous_receipts.get(participant_id), "memory": str(operators[participant_id].memory), "terminal": terminal.duplicate(true)}


func task_plan_schema_valid(participant_id: String, plan: Variant) -> bool:
	if not TaskPlan.validate(plan, episode_id, observation_seq, _team_unit_ids(participant_id), _visible_target_ids(participant_id), _alive_unit_ids(participant_id)).is_empty():
		return false
	for assignment: Dictionary in plan.assignments:
		if not _task_target_compatible(participant_id, str(assignment.task), str(assignment.target_id)):
			return false
	return true


func step_task_plan_window(window: Dictionary) -> Dictionary:
	# Adapter for the dispatcher: validate plans, turn each into a neutral legacy controller action,
	# and retain the accepted plan in canonical replay evidence.
	var translated: Dictionary = window.duplicate(true)
	var plans: Dictionary = translated.get("plans", {})
	translated.erase("plans")
	translated["decisions"] = {}
	for participant_id: String in PARTICIPANTS:
		var plan: Variant = plans.get(participant_id)
		var valid: bool = task_plan_schema_valid(participant_id, plan)
		if valid: last_task_plans[participant_id] = plan.duplicate(true)
		translated.decisions[participant_id] = _neutral_decision(participant_id, valid, "invalid_task_plan" if not valid else "")
	var result: Dictionary = step_window(translated)
	if not replay_windows.is_empty(): replay_windows[replay_windows.size() - 1]["task_plans"] = last_task_plans.duplicate(true)
	return result


func _neutral_decision(participant_id: String, accepted: bool, reason: String) -> Dictionary:
	if not accepted: return {"disposition": "no_input", "action": null, "fallback": "neutral", "no_input_reason": "invalid"}
	return {"disposition": "accepted", "action": {"protocol_version": PROTOCOL_VERSION, "episode_id": episode_id, "observation_seq": observation_seq, "action_id": "task_plan_%s_%d" % [participant_id, observation_seq], "control": {"move_x": 0, "move_y": 0, "look_x": 0, "look_y": 0, "duration_ticks": DECISION_TICKS, "buttons": {"interact": false, "primary": false, "guard": false, "dash": false, "ability_1": false, "ability_2": false, "cycle_item": false, "cancel": false}}, "intent_label": "RTS task plan", "memory_update": ""}, "fallback": "none", "no_input_reason": null}


func checkpoint() -> Dictionary:
	var projected_units := {}
	for unit_id: String in _sorted_keys(units):
		var unit: Dictionary = units[unit_id]
		projected_units[unit_id] = {"team": unit.team, "role": unit.role, "position_mt": [unit.position_mt.x, unit.position_mt.y], "heading": unit.heading, "health": unit.health, "alive": unit.alive, "task": unit.task, "milestone": unit.milestone, "target_id": unit.target_id, "progress_ticks": unit.progress_ticks, "arming_ticks": unit.arming_ticks, "attack_cooldown_ticks": unit.attack_cooldown_ticks, "carrying": unit.carrying, "animation_state": unit.animation_state, "deposits": unit.deposits, "distance_travelled_mt": unit.distance_travelled_mt}
	var projected_resources := {}
	for resource_id: String in _sorted_keys(resources):
		var resource: Dictionary = resources[resource_id]
		projected_resources[resource_id] = {"team": resource.team, "kind": resource.kind, "position_mt": [resource.position_mt.x, resource.position_mt.y], "stock": resource.stock, "state": resource.state, "reserved_by": resource.reserved_by}
	return {"protocol_version": PROTOCOL_VERSION, "task_id": TASK_ID, "episode_id": episode_id, "tick": tick, "observation_seq": observation_seq, "event_seq": event_seq, "story_phase": story_phase, "victory_latched": victory_latched, "units": projected_units, "resources": projected_resources, "structures": structures.duplicate(true), "economy": economy.duplicate(true), "combat_stats": combat_stats.duplicate(true), "last_task_plans": last_task_plans.duplicate(true), "invalid_windows": invalid_windows.duplicate(true), "terminal": terminal.duplicate(true), "winner_id": winner_id}


func authority_aggregates() -> Dictionary:
	var participants := {}
	for participant_id: String in PARTICIPANTS:
		var outcome := "draw" if winner_id == null else ("win" if winner_id == participant_id else "loss")
		participants[participant_id] = _summary(participant_id, outcome)
		for key: String in ["task_id", "completion_tick", "terminal_outcome", "terminal_reason", "participant_id", "outcome"]: participants[participant_id].erase(key)
	return {"completion_tick": tick if bool(terminal.ended) else null, "terminal_outcome": terminal.outcome, "terminal_reason": terminal.reason, "participants": participants}


func participant_presentation_source(participant_id: String) -> Dictionary:
	var projected_units: Array[Dictionary] = []
	for unit_id: String in _team_unit_ids(participant_id):
		var unit: Dictionary = units[unit_id]
		projected_units.append({"unit_id": unit_id, "id": unit_id, "team": unit.team, "role": unit.role, "position_mt": {"x": unit.position_mt.x, "y": unit.position_mt.y}, "heading": unit.heading, "intent": _unit_intent(unit), "task": unit.task, "animation_state": unit.animation_state, "health_percent": _divide_toward_zero(int(unit.health) * 100, UNIT_HEALTH), "alive": unit.alive, "carrying": unit.carrying})
	var public_resources: Array[Dictionary] = []
	for resource_id: String in _sorted_keys(resources):
		var resource: Dictionary = resources[resource_id]
		if str(resource.team) == participant_id:
			public_resources.append({"id": _visible_id(resource_id), "kind": "resource_wood" if resource.kind == "tree" else "resource_ore", "position_mt": {"x": resource.position_mt.x, "y": resource.position_mt.y}, "stock": resource.stock, "state": resource.state})
	var safe_events: Array[Dictionary] = []
	for event: Dictionary in _events_for(participant_id):
		safe_events.append({"type": str(event.kind), "public_summary": str(event.summary)})
	return {"participant_id": participant_id, "operator": projected_units[0] if not projected_units.is_empty() else {}, "units": projected_units, "resources": public_resources, "public_phase": story_phase.replace("_", " ").capitalize(), "public_objective": _public_objective(), "recent_events": safe_events, "own": {"town_hall": {"position_mt": {"x": TOWN_HALLS[participant_id].x, "y": TOWN_HALLS[participant_id].y}, "health_percent": _divide_toward_zero(int(structures[participant_id].town_hall.health) * 100, TOWN_HALL_HEALTH), "state": structures[participant_id].town_hall.state}, "barracks": {"position_mt": {"x": BARRACKS[participant_id].x, "y": BARRACKS[participant_id].y}, "health_percent": 100 if bool(structures[participant_id].barracks.built) and str(structures[participant_id].barracks.state) != "destroyed" else 0, "state": structures[participant_id].barracks.state}, "tower": {"position_mt": {"x": TOWERS[participant_id].x, "y": TOWERS[participant_id].y}, "health_percent": _divide_toward_zero(int(structures[participant_id].tower.health) * 100, TOWER_HEALTH) if bool(structures[participant_id].tower.built) else 0, "state": structures[participant_id].tower.state}, "resources": economy[participant_id].duplicate(true), "units": projected_units}, "visible_entities": public_resources}


func _sync_operator(participant_id: String) -> void:
	var op: Dictionary = operators[participant_id]
	var leader: Dictionary = units[_unit_id(participant_id, 0)]
	op.position_mt = leader.position_mt; op.heading = leader.heading; op.health = leader.health; op.carrying = leader.carrying; op.animation_state = leader.animation_state
	op.units_trained = _living_count(participant_id); op.deposits = 0; op.materials_gathered = 0
	for unit_id: String in _team_unit_ids(participant_id): op.deposits += int(units[unit_id].deposits); op.materials_gathered += int(units[unit_id].materials_gathered)
	op.barracks_built = 1 if structures[participant_id].barracks.state == "active" else 0; op.towers_built = 1 if structures[participant_id].tower.state == "active" else 0; op.presentation_intent = story_phase


func _resource_id(participant_id: String, kind: String, index: int) -> String:
	return ("blue" if participant_id == "participant_0" else "red") + "_" + kind + "_" + str(index)


func _visible_id(authority_id: String) -> String:
	# The frozen v2 observation contract reserves semantic IDs beginning with `v_`.  Gameplay and
	# sealed task plans keep their stable authority IDs; only the participant-visible projection
	# crosses this explicit namespace boundary.
	return "v_" + authority_id


func _unit_id(participant_id: String, index: int) -> String:
	return ("blue" if participant_id == "participant_0" else "red") + "_" + str(index)

func _team_unit_ids(participant_id: String) -> Array[String]:
	var values: Array[String] = []
	for index: int in 3:
		var id := _unit_id(participant_id, index)
		if units.has(id): values.append(id)
	return values

func _alive_unit_ids(participant_id: String) -> Array[String]:
	var values: Array[String] = []
	for id: String in _team_unit_ids(participant_id):
		if bool(units[id].alive): values.append(id)
	return values

func _visible_target_ids(participant_id: String) -> Array[String]:
	var values: Array[String] = ["town_hall", "barracks", "tower", "bridge"]
	for resource_id: String in _sorted_keys(resources):
		if str(resources[resource_id].team) == participant_id: values.append(resource_id)
	for unit_id: String in _team_unit_ids(participant_id): values.append(unit_id)
	if story_phase in ["bridge_battle", "pursuit", "tower_siege", "town_hall_siege", "victory"]:
		for unit_id: String in _team_unit_ids(_rival(participant_id)):
			if bool(units[unit_id].alive): values.append(unit_id)
	if story_phase in ["tower_siege", "town_hall_siege", "victory"]:
		values.append("enemy_tower")
		values.append("enemy_town_hall")
	return values

func _task_target_compatible(participant_id: String, task: String, target_id: String) -> bool:
	if task == "gather": return target_id in _owned_resource_ids(participant_id)
	if task == "return_material" or task == "retreat": return target_id == "town_hall"
	if task == "build": return target_id in ["barracks", "tower"]
	if task == "arm": return target_id == "barracks"
	if task == "rally" or task == "hold": return target_id == "bridge"
	if task == "attack_structure": return target_id in ["enemy_town_hall", "enemy_tower"]
	if task == "attack_unit": return target_id in _team_unit_ids(_rival(participant_id))
	return false

func _owned_resource_ids(participant_id: String) -> Array[String]:
	var values: Array[String] = []
	for resource_id: String in _sorted_keys(resources):
		if str(resources[resource_id].team) == participant_id: values.append(resource_id)
	return values


func _unit_intent(unit: Dictionary) -> String:
	var task := str(unit.task)
	var target := str(unit.target_id)
	if task == "walking" and "tree" in target: return "Harvest %s" % target.replace("blue_", "").replace("red_", "").replace("_", " ").capitalize()
	if task == "walking" and "ore" in target: return "Mine %s" % target.replace("blue_", "").replace("red_", "").replace("_", " ").capitalize()
	if task == "harvesting": return "Chopping tree" if "tree" in target else "Mining ore"
	if task == "returning": return "Returning %s" % str(unit.carrying)
	if task == "depositing": return "Depositing materials"
	if task == "building": return "Building %s" % target.replace("_", " ")
	if task == "arming": return "Arming as militia"
	if task == "rallying": return "Rally at the bridge"
	if task == "retreating": return "Retreat to base"
	if task == "attacking": return "Attack %s" % target.replace("_", " ")
	if task == "hit": return "Under attack"
	if task == "defeated": return "Unit defeated"
	if task == "celebrating": return "Blue victory"
	if task == "turning": return "Face the objective"
	return "Hold position"


func _public_objective() -> String:
	if story_phase in ["opening_economy", "final_economy"]: return "Harvest persistent resources and return each load."
	if story_phase == "construction_and_arming": return "Complete the barracks and tower, then arm every worker."
	if story_phase == "rally": return "March in formation toward the bridge."
	if story_phase == "bridge_battle": return "Break the opposing line at the bridge."
	if story_phase == "pursuit": return "Pursue the final Red fighter."
	if story_phase == "tower_siege": return "Destroy Red's defensive tower."
	if story_phase == "town_hall_siege": return "Destroy the Red Town Hall."
	return "Celebrate the deterministic Blue victory."

func _living_count(participant_id: String) -> int:
	var count := 0
	for id: String in _team_unit_ids(participant_id):
		if bool(units[id].alive): count += 1
	return count

func _at(unit: Dictionary, position: Vector2i) -> bool:
	return unit.position_mt.distance_squared_to(position) <= INTERACT_RADIUS_MT * INTERACT_RADIUS_MT


func _bridge_lane(participant_id: String, index: int) -> Vector2i:
	return Vector2i(-450 if participant_id == "participant_0" else 450, int(FORMATION_LANES_MT[index]))


func _arming_slot(participant_id: String, unit_id: String) -> Vector2i:
	var index := int(unit_id.get_slice("_", 1))
	return BARRACKS[participant_id] + Vector2i(0, int(FORMATION_LANES_MT[index]))


func _heading_to(origin: Vector2i, target: Vector2i) -> int:
	var offset := target - origin
	if offset == Vector2i.ZERO: return 0
	if abs(offset.x) * 2 < abs(offset.y): return 4 if offset.y > 0 else 0
	if abs(offset.y) * 2 < abs(offset.x): return 2 if offset.x > 0 else 6
	if offset.x > 0: return 3 if offset.y > 0 else 1
	return 5 if offset.y > 0 else 7

func _next_heading(current: int, target: int) -> int:
	var clockwise := posmod(target - current, 8)
	return posmod(current + (1 if clockwise <= 4 else -1), 8)

func _sorted_keys(value: Dictionary) -> Array[String]:
	var keys: Array[String] = []
	for key: Variant in value.keys(): keys.append(str(key))
	keys.sort()
	return keys
