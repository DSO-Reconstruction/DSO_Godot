class_name DSOModifierVM
extends RefCounted
## Executes the parsed modifier language against actors.
##
## There is NO naming rule connecting a modifier verb to the attribute it
## writes: only 2 of the exported verbs match an attribute name, and only 14
## stats carry the `Factor{Absolute,Relative}` pair. The verb -> stat mapping
## below is therefore hand-authored, and deliberately explicit.
##
## Every verb falls in one of a few families, so the interpreter is a dispatch
## over families rather than one function per verb:
##
##   STAT     write a contribution to a stat, honouring absolute/relative
##   DAMAGE   apply damage or healing to current health
##   RESOURCE change the skill resource
##   EFFECT   apply, stop or immunise a status effect
##   FEATURE  enable/disable an actor capability (this is how CC works)
##   VAR      write a free-form named variable
##   VISUAL   presentation only; the server records and ignores it
##   TODO     recognised but not simulated yet -- counted, never silent
##
## Unknown verbs are counted in `unhandled` instead of being dropped, so
## coverage is measurable rather than guessed at.

enum Family { STAT, DAMAGE, HEAL, RESOURCE, EFFECT, FEATURE, VAR, VISUAL, TODO }

## verb -> [family, stat or action, takes a qualifier from the args]
const VERBS := {
	# --- stats -----------------------------------------------------------
	"MaxHealthPoints":            [Family.STAT, "HealthPoints", false],
	"Damage":                     [Family.STAT, "Damage", true],
	"Resistance":                 [Family.STAT, "Resistance", true],
	"TargetReduceResistance":     [Family.STAT, "TargetResistanceDamage", true],
	"BlockValue":                 [Family.STAT, "Block", false],
	"CriticalValue":              [Family.STAT, "Critical", false],
	"SkillResourceRegeneration":  [Family.STAT, "ResourceRegeneration", false],
	"ResourceCost":               [Family.STAT, "ResourceCost", false],
	"LifeLeech":                  [Family.STAT, "LifeLeech", false],
	"DamageReflection":           [Family.STAT, "DamageReflection", false],
	"AmmoVulnerability":          [Family.STAT, "AmmoVulnerability", true],
	"TargetAddDamage":            [Family.STAT, "TargetAddDamage", true],
	"SkillPunchThrough":          [Family.STAT, "SkillPunchThrough", false],
	# Speed splits by category: Skills means attack speed, otherwise movement.
	"Speed":                      [Family.STAT, "@speed", true],
	"AnimRunSpeed":               [Family.STAT, "MovementSpeed", false],

	# --- health and resource --------------------------------------------
	"CurrHealthPointsDmg":        [Family.DAMAGE, "", true],
	"SkillDamage":                [Family.DAMAGE, "", true],
	"SkillHitDamage":             [Family.DAMAGE, "", true],
	"TriggerSkillDamage":         [Family.DAMAGE, "", true],
	"CurrHealthPoints":           [Family.HEAL, "", false],
	"CurrSkillResource":          [Family.RESOURCE, "", false],

	# --- status effects --------------------------------------------------
	"ActorStatusEffect":          [Family.EFFECT, "actor", false],
	"SkillStatusEffect":          [Family.EFFECT, "skill", false],
	"StopStatusEffect":           [Family.EFFECT, "stop", false],
	"StopStatusEffectGroup":      [Family.EFFECT, "stop_group", false],
	"StatusEffectImmunityGroup":  [Family.EFFECT, "immune_group", false],
	"CurrStatusEffectDuration":   [Family.EFFECT, "duration", false],

	# --- capabilities: stun, root, silence ------------------------------
	"ActorFeature":               [Family.FEATURE, "", false],

	# --- free-form variables --------------------------------------------
	"ActorVariable":              [Family.VAR, "", false],

	# --- presentation, client-side only ---------------------------------
	"Localize":                   [Family.VISUAL, "text", false],
	"OverheadText":               [Family.VISUAL, "text", false],
	"SkinModifier":               [Family.VISUAL, "skin", false],
	"VariationModifier":          [Family.VISUAL, "variation", false],
	"AnimSet":                    [Family.VISUAL, "anim", false],

	# --- recognised, not simulated in this slice ------------------------
	"Minion":                     [Family.TODO, "spawn", false],
	"Pet":                        [Family.TODO, "spawn", false],
	"DropAmount":                 [Family.TODO, "loot", false],
	"SupportScore":               [Family.TODO, "meta", false],
	"ActiveCoolDown":             [Family.TODO, "cooldown", false],
	"ActorCoolDownReset":         [Family.TODO, "cooldown", false],
	"UnlockHiddenSkillId":        [Family.TODO, "unlock", false],
	"UnlockHiddenSkills":         [Family.TODO, "unlock", false],
	"SkillTiming":                [Family.TODO, "timing", false],
	"BulletImpactCount":          [Family.TODO, "projectile", false],
}

## Damage types the client uses; a qualifier that is not one of these is a
## category (Skills, Movement) rather than an element.
const DAMAGE_TYPES := ["Physical", "Fire", "Ice", "Poison", "Lightning",
	"DarkMagic", "Holy", "Bleeding", "Frost"]

var data: DSOGameData
var effects: RefCounted          ## DSOStatusEffects, set by the engine
var unhandled: Dictionary = {}   ## verb -> count
var log_visual := false


func _init(game_data: DSOGameData) -> void:
	data = game_data


## Run one modifier list against a target.
##   source: the id that owns these contributions, so they can be withdrawn
##   params: the `$n` values passed in by the skill or effect
func run(mods: Array, target, caster, source: String, params: Dictionary) -> void:
	for m in mods:
		if typeof(m) == TYPE_DICTIONARY:
			run_one(m, target, caster, source, params)


func run_one(mod: Dictionary, target, caster, source: String,
		params: Dictionary) -> void:
	var verb := String(mod.get("verb", ""))
	if not VERBS.has(verb):
		unhandled[verb] = int(unhandled.get(verb, 0)) + 1
		return
	var spec: Array = VERBS[verb]
	var family: int = spec[0]
	var which: String = spec[1]
	var args: Array = mod.get("args", [])

	match family:
		Family.STAT:
			_stat(mod, args, target, source, params, which, spec[2])
		Family.DAMAGE:
			_damage(mod, args, target, caster, params)
		Family.HEAL:
			_heal(args, target, params)
		Family.RESOURCE:
			_resource(args, target, params)
		Family.EFFECT:
			_effect(mod, args, target, caster, which, params)
		Family.FEATURE:
			_feature(args, target, source, params)
		Family.VAR:
			_var(args, target, params)
		Family.VISUAL:
			if log_visual:
				print("[vm] visual %s -> %s" % [verb, mod.get("raw", "")])
		Family.TODO:
			unhandled[verb] = int(unhandled.get(verb, 0)) + 1


func _qualifier(args: Array) -> String:
	## First identifier argument that is not a mode keyword.
	for a in args:
		var id := DSOGameData.ident(a)
		if id != "" and id != "absolute" and id != "relative":
			return id
	return ""


func _stat(mod: Dictionary, args: Array, target, source: String,
		params: Dictionary, stat: String, qualified: bool) -> void:
	if args.is_empty():
		return
	var amount := DSOGameData.number(args[0], params)
	var mode := DSOGameData.mode_of(mod)
	var sub := _qualifier(args) if qualified else ""

	if stat == "@speed":
		# 'Skills' means attack speed; anything else is movement.
		stat = "AttacksPerSecond" if sub == "Skills" else "MovementSpeed"
		if sub == "Skills" or sub == "Movement":
			sub = ""
	# Min/Max on a Damage modifier bound the roll rather than qualifying it.
	if sub == "Min" or sub == "Max":
		sub = ""
	target.attributes.add(DSOAttributes.key(stat, sub), amount, mode, source)


func _damage(mod: Dictionary, args: Array, target, caster,
		params: Dictionary) -> void:
	if args.is_empty():
		return
	var amount := DSOGameData.number(args[0], params)
	var mode := DSOGameData.mode_of(mod)
	var element := ""
	for a in args:
		var id := DSOGameData.ident(a)
		if id in DAMAGE_TYPES:
			element = id
			break
	# A relative amount is a fraction of the target's maximum health, which is
	# how the shipped damage-over-time effects are authored.
	var hp := amount
	if mode == "relative":
		hp = amount * target.max_health()
	target.take_damage(hp, element, caster)


func _heal(args: Array, target, params: Dictionary) -> void:
	if args.is_empty():
		return
	var amount := DSOGameData.number(args[0], params)
	var mode := DSOGameData.mode_of({"args": args})
	target.heal(amount * target.max_health() if mode == "relative" else amount)


func _resource(args: Array, target, params: Dictionary) -> void:
	if args.is_empty():
		return
	var amount := DSOGameData.number(args[0], params)
	var mode := DSOGameData.mode_of({"args": args})
	target.add_resource(amount * target.max_resource() if mode == "relative"
		else amount)


func _effect(mod: Dictionary, args: Array, target, caster, which: String,
		params: Dictionary) -> void:
	if effects == null:
		return
	match which:
		"actor", "skill":
			# 'action, [skill,] effect_id, C:.., D:..' -- the effect id is the
			# last identifier that names a known effect.
			var eid := ""
			for a in args:
				var id := DSOGameData.ident(a)
				if id != "" and data.effects.has(id):
					eid = id
			if eid.is_empty():
				return
			var named: Dictionary = mod.get("named", {})
			var dur := DSOGameData.number(named.get("D", {}), params)
			var chance := 1.0
			if named.has("C"):
				chance = DSOGameData.number(named["C"], params)
			var sub_params := {}
			for k in named.keys():
				if String(k).begins_with("$"):
					sub_params[String(k).substr(1)] = named[k]
			effects.apply(target, caster, eid, sub_params, dur, chance)
		"stop":
			for a in args:
				var id := DSOGameData.ident(a)
				if id != "":
					effects.stop(target, id)
		"stop_group":
			for a in args:
				var id := DSOGameData.ident(a)
				if id != "":
					effects.stop_group(target, id)
		"immune_group":
			for a in args:
				var id := DSOGameData.ident(a)
				if id != "":
					effects.add_immunity(target, id)
		"duration":
			pass


func _feature(args: Array, target, source: String, params: Dictionary) -> void:
	## 'ActorFeature:$0,Movement,Skills' -- 0 disables the listed capabilities.
	if args.is_empty():
		return
	var enabled := DSOGameData.number(args[0], params) != 0.0
	for a in args:
		var id := DSOGameData.ident(a)
		if id != "" and id != "absolute" and id != "relative":
			target.set_feature(id, enabled, source)


func _var(args: Array, target, params: Dictionary) -> void:
	if args.size() < 2:
		return
	var amount := DSOGameData.number(args[0], params)
	var name := _qualifier(args)
	if name.is_empty():
		return
	var mode := DSOGameData.mode_of({"args": args})
	if mode == "relative":
		target.attributes.set_var(name,
			target.attributes.get_var(name) * (1.0 + amount))
	else:
		target.attributes.set_var(name,
			target.attributes.get_var(name) + amount)


## Verbs the interpreter knows, for coverage reporting.
func coverage() -> Dictionary:
	var by_family := {}
	for verb in VERBS.keys():
		var f: int = VERBS[verb][0]
		by_family[f] = int(by_family.get(f, 0)) + 1
	return {"known": VERBS.size(), "by_family": by_family,
			"unhandled": unhandled.duplicate()}
