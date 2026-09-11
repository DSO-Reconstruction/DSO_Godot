class_name DSOSkills
extends RefCounted
## Server-side skill execution.
##
## Combat timing in the shipped data is expressed in ANIMATION FRAMES, not
## seconds: HitFrame, SkillUnblockFrame, MotionUnblockFrame,
## InvincibilityFrames. The extracted .nax3 clips run at 25 fps, so frames are
## converted with ANIM_FPS. That is the seam between the asset pipeline and
## the simulation: the same frame numbers drive the client animation and the
## server's hit resolution.
##
## A cast is therefore not instantaneous. It is queued, and the hit lands when
## the cast timer reaches HitFrame -- which is what lets a client play the
## swing while the server stays authoritative over the outcome.

const ANIM_FPS := 25.0

## How a skill picks its victims.
const TARGET_SELF := ["User"]
const TARGET_SINGLE := ["Actor"]
const TARGET_AREA := ["Radius", "RadiusRandomTarget", "Angle", "Cone",
	"AngleMinHpTarget", "RandomPosition"]

class Cast extends RefCounted:
	var caster: DSOActor
	var skill_id: String
	var skill: Dictionary
	var target: DSOActor
	var point: Vector3
	var elapsed: float = 0.0
	var hit_at: float = 0.0
	var ends_at: float = 0.0
	var hit_done := false

var data: DSOGameData
var vm: DSOModifierVM
var effects: DSOStatusEffects
## actor id -> {cooldown_key: seconds remaining}
var cooldowns: Dictionary = {}
var casting: Array = []
## Populated by the zone so the runtime can find victims.
var actors_provider: Callable = Callable()

signal cast_started(caster, skill_id)
signal cast_hit(caster, skill_id, victims)
signal cast_failed(caster, skill_id, reason)


func _init(game_data: DSOGameData, modifier_vm: DSOModifierVM,
		effect_engine: DSOStatusEffects) -> void:
	data = game_data
	vm = modifier_vm
	effects = effect_engine


func _cooldown_key(skill: Dictionary, skill_id: String) -> String:
	var cat := String(skill.get("cooldown_category", ""))
	return cat if not cat.is_empty() else skill_id


func remaining_cooldown(actor: DSOActor, skill_id: String) -> float:
	var skill := data.skill(skill_id)
	return float(cooldowns.get(actor.id, {}).get(
		_cooldown_key(skill, skill_id), 0.0))


## Validate and begin a cast. Returns "" on success, a reason otherwise.
func cast(caster: DSOActor, skill_id: String, target: DSOActor,
		point: Vector3 = Vector3.ZERO) -> String:
	var skill := data.skill(skill_id)
	if skill.is_empty():
		return "unknown_skill"
	if not caster.alive:
		return "dead"
	if not caster.has_feature("Skills"):
		return "skills_disabled"
	if remaining_cooldown(caster, skill_id) > 0.0:
		return "cooldown"

	var cost := float(skill.get("resource_cost", 0.0))
	if cost > 0.0 and caster.resource < cost:
		return "no_resource"

	var targeting := String(skill.get("targeting", "Actor"))
	if targeting in TARGET_SINGLE:
		if target == null or not target.alive:
			return "no_target"
		var reach := float(skill.get("range", 0.0))
		if reach > 0.0 and caster.distance_to(target) > reach:
			return "out_of_range"

	if cost > 0.0:
		caster.add_resource(-cost)
	var gain := float(skill.get("resource_gain", 0.0))
	if gain > 0.0:
		caster.add_resource(gain)

	var cd := float(skill.get("cooldown", 0.0))
	if cd > 0.0:
		if not cooldowns.has(caster.id):
			cooldowns[caster.id] = {}
		cooldowns[caster.id][_cooldown_key(skill, skill_id)] = cd

	var c := Cast.new()
	c.caster = caster
	c.skill_id = skill_id
	c.skill = skill
	c.target = target
	c.point = point
	c.hit_at = float(skill.get("hit_frame", 0)) / ANIM_FPS
	var unblock := float(skill.get("unblock_frame", skill.get("hit_frame", 0)))
	c.ends_at = maxf(unblock / ANIM_FPS, c.hit_at)
	casting.append(c)
	cast_started.emit(caster, skill_id)
	return ""


func _victims(c: Cast) -> Array:
	var targeting := String(c.skill.get("targeting", "Actor"))
	if targeting in TARGET_SELF:
		return [c.caster]
	if targeting in TARGET_SINGLE:
		return [c.target] if c.target != null and c.target.alive else []
	if not (targeting in TARGET_AREA) or not actors_provider.is_valid():
		return [c.target] if c.target != null and c.target.alive else []

	# Area shapes: a radius around the caster, optionally limited to an arc.
	var reach := float(c.skill.get("range", 0.0))
	if reach <= 0.0:
		reach = float(c.skill.get("hit_range", 0.0))
	var half_angle := deg_to_rad(float(c.skill.get("angle", 360.0))) * 0.5
	var out: Array = []
	for a in actors_provider.call() as Array:
		if a == c.caster or not a.alive:
			continue
		if reach > 0.0 and c.caster.distance_to(a) > reach:
			continue
		if targeting == "Angle" or targeting == "Cone":
			var to := a.position - c.caster.position
			if to.length() > 0.001:
				var ang: float = absf(angle_difference(
					c.caster.facing, atan2(to.x, to.z)))
				if ang > half_angle:
					continue
		out.append(a)
	return out


## Base hit damage, before the target's resistance.
func _damage_of(c: Cast) -> float:
	var base := c.caster.attributes.value("Damage")
	var modifier := float(c.skill.get("damage_modifier", 1.0))
	if modifier <= 0.0:
		modifier = 1.0
	var amount := base * modifier
	var crit := c.caster.attributes.value("Critical")
	if crit > 0.0 and randf() < clampf(crit, 0.0, 1.0):
		amount *= 1.0 + maxf(c.caster.attributes.value("CriticalDamageBonus"),
			1.0)
	return amount


func _resolve(c: Cast) -> void:
	c.hit_done = true
	var element := String(c.skill.get("damage_type", ""))
	var victims := _victims(c)

	# The caster's own effects fire regardless of whether anything was hit.
	for r in c.skill.get("user_effects", []):
		_apply_ref(r, c.caster, c.caster)

	var damage := _damage_of(c)
	for v in victims:
		if damage > 0.0:
			v.take_damage(damage, element, c.caster)
		for r in c.skill.get("victim_effects", []):
			_apply_ref(r, v, c.caster)
	cast_hit.emit(c.caster, c.skill_id, victims)


func _apply_ref(r: Dictionary, target: DSOActor, caster: DSOActor) -> void:
	if typeof(r) != TYPE_DICTIONARY:
		return
	# A trigger means the effect only fires on a condition (a critical hit,
	# a skill start) that this slice does not evaluate yet.
	if r.has("trigger"):
		return
	effects.apply(target, caster, String(r.get("id", "")),
		r.get("params", {}), float(r.get("duration", 0.0)),
		float(r.get("chance", 1.0)))


## Advance cooldowns and in-flight casts. Called from the server tick.
func step(delta: float) -> void:
	for actor_id in cooldowns.keys():
		var table: Dictionary = cooldowns[actor_id]
		for k in table.keys():
			var left := float(table[k]) - delta
			if left <= 0.0:
				table.erase(k)
			else:
				table[k] = left

	for c in casting.duplicate():
		c.elapsed += delta
		if not c.hit_done and c.elapsed >= c.hit_at:
			_resolve(c)
		if c.elapsed >= c.ends_at and c.hit_done:
			casting.erase(c)
