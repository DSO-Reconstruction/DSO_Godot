class_name DSOStatusEffects
extends RefCounted
## The status-effect engine: buffs, debuffs, damage over time and auras.
##
## In the shipped data these are all one table, with a lifecycle of modifier
## lists: start, tick (at a given rate), stop (removed early) and done (ran
## out). Stacking is governed by max_stack, an exclusive group, and an
## opposing effect that cancels this one.
##
## Contributions a running effect makes are tagged with a unique instance id,
## so ending it withdraws exactly what it granted.

class Instance extends RefCounted:
	var uid: String
	var effect_id: String
	var target: DSOActor
	var caster: DSOActor
	var params: Dictionary
	var remaining: float          ## seconds; INF for a permanent effect
	var tick_rate: float
	var tick_accum: float = 0.0
	var stacks: int = 1
	var groups: Array = []

var data: DSOGameData
var vm: DSOModifierVM
## actor id -> Array[Instance]
var active: Dictionary = {}
var _uid := 0


func _init(game_data: DSOGameData, modifier_vm: DSOModifierVM) -> void:
	data = game_data
	vm = modifier_vm
	vm.effects = self


func _groups_of(effect: Dictionary) -> Array:
	var raw := String(effect.get("groups", ""))
	if raw.is_empty():
		return []
	var out: Array = []
	for g in raw.split(","):
		var t := g.strip_edges()
		if not t.is_empty():
			out.append(t)
	return out


func list_for(actor: DSOActor) -> Array:
	return active.get(actor.id, [])


func find(actor: DSOActor, effect_id: String) -> Instance:
	for inst in list_for(actor):
		if inst.effect_id == effect_id:
			return inst
	return null


## Apply an effect. `duration` of 0 falls back to the effect's own duration;
## a duration of 0 there too means permanent until stopped.
func apply(target: DSOActor, caster: DSOActor, effect_id: String,
		params: Dictionary, duration: float, chance: float) -> Instance:
	if not target.alive:
		return null
	var effect := data.effect(effect_id)
	if effect.is_empty():
		return null
	# A chance of 0 in the data means "always" -- the field is only used to
	# express a real probability when it is above zero.
	if chance > 0.0 and chance < 1.0 and randf() > chance:
		return null

	var groups := _groups_of(effect)
	for g in groups:
		if target.is_immune_to(g):
			return null

	# An opposing effect cancels this one instead of coexisting.
	var opposing := String(effect.get("opposing_effect", ""))
	if not opposing.is_empty():
		stop(target, opposing)

	var exclusive := String(effect.get("exclusive_group", ""))
	if not exclusive.is_empty():
		for inst in list_for(target).duplicate():
			var other := data.effect(inst.effect_id)
			if String(other.get("exclusive_group", "")) == exclusive \
					and inst.effect_id != effect_id:
				_end(inst, "stop")

	var max_stack := int(effect.get("max_stack", 1))
	var existing := find(target, effect_id)
	if existing != null:
		if max_stack > 1 and existing.stacks < max_stack:
			existing.stacks += 1
		# Reapplying refreshes the timer, which is what players expect.
		existing.remaining = _duration(effect, duration)
		return existing

	_uid += 1
	var inst := Instance.new()
	inst.uid = "%s#%d" % [effect_id, _uid]
	inst.effect_id = effect_id
	inst.target = target
	inst.caster = caster
	inst.params = params
	inst.remaining = _duration(effect, duration)
	inst.tick_rate = float(effect.get("tick_rate", 0.0))
	inst.groups = groups

	if not active.has(target.id):
		active[target.id] = []
	active[target.id].append(inst)

	var phases: Dictionary = effect.get("phases", {})
	if phases.has("start"):
		vm.run(phases["start"], target, caster, inst.uid, params)
	return inst


func _duration(effect: Dictionary, override: float) -> float:
	if override > 0.0:
		return override
	var d := float(effect.get("duration", 0.0))
	return d if d > 0.0 else INF


func stop(target: DSOActor, effect_id: String) -> void:
	for inst in list_for(target).duplicate():
		if inst.effect_id == effect_id:
			_end(inst, "stop")


func stop_group(target: DSOActor, group: String) -> void:
	for inst in list_for(target).duplicate():
		if group in inst.groups:
			_end(inst, "stop")


func add_immunity(target: DSOActor, group: String) -> void:
	target.add_immunity(group)
	stop_group(target, group)


func _end(inst: Instance, phase: String) -> void:
	var effect := data.effect(inst.effect_id)
	var phases: Dictionary = effect.get("phases", {})
	if phases.has(phase):
		vm.run(phases[phase], inst.target, inst.caster, inst.uid, inst.params)
	# Withdraw everything this instance granted.
	inst.target.attributes.remove_source(inst.uid)
	inst.target.clear_feature_source(inst.uid)
	for g in inst.groups:
		pass
	var bucket: Array = active.get(inst.target.id, [])
	bucket.erase(inst)


## Advance every active effect. Called from the server tick.
func step(delta: float) -> void:
	for actor_id in active.keys():
		for inst in (active[actor_id] as Array).duplicate():
			if not inst.target.alive:
				_end(inst, "stop")
				continue
			var effect := data.effect(inst.effect_id)
			var phases: Dictionary = effect.get("phases", {})
			if inst.tick_rate > 0.0 and phases.has("tick"):
				inst.tick_accum += delta
				while inst.tick_accum >= inst.tick_rate:
					inst.tick_accum -= inst.tick_rate
					vm.run(phases["tick"], inst.target, inst.caster,
						inst.uid, inst.params)
					if not inst.target.alive:
						break
			if inst.remaining != INF:
				inst.remaining -= delta
				if inst.remaining <= 0.0:
					_end(inst, "done")


func count(actor: DSOActor) -> int:
	return list_for(actor).size()
