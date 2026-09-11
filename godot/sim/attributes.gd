class_name DSOAttributes
extends RefCounted
## Stat container for one actor.
##
## The client's attribute registry exposes 14 stats in `<Stat>FactorAbsolute`
## and `<Stat>FactorRelative` pairs. That pairing is the aggregation formula:
##
##     final = (base + sum of absolute) * (1 + sum of relative)
##
## Absolute contributions are flat additions, relative ones are fractions.
## The order matters for balance and is fixed here on purpose.
##
## Contributions are tagged with the id of whatever added them, so a status
## effect can withdraw exactly what it granted when it ends -- without
## recomputing anything else.

## The 14 stats that carry a Factor pair in the registry.
const CANONICAL := [
	"Armor", "AttacksPerSecond", "BasePower", "Block", "Critical", "Damage",
	"HealthPoints", "HealthPointsPerSecond", "MaxResource", "MovementSpeed",
	"Resistance", "ResourceGain", "ResourceRegeneration",
	"TargetResistanceDamage",
]

var _base: Dictionary = {}          ## stat key -> float
var _absolute: Dictionary = {}      ## stat key -> {source_id: float}
var _relative: Dictionary = {}      ## stat key -> {source_id: float}
var _cache: Dictionary = {}
var _vars: Dictionary = {}          ## free-form named variables (ActorVariable)


## A stat may be qualified, e.g. Resistance/Fire or MovementSpeed/Skills.
static func key(stat: String, sub: String = "") -> String:
	return stat if sub.is_empty() else "%s/%s" % [stat, sub]


func set_base(stat_key: String, value: float) -> void:
	_base[stat_key] = value
	_cache.erase(stat_key)


func get_base(stat_key: String) -> float:
	return float(_base.get(stat_key, 0.0))


func add(stat_key: String, value: float, mode: String, source: String) -> void:
	var table: Dictionary = _relative if mode == "relative" else _absolute
	if not table.has(stat_key):
		table[stat_key] = {}
	var bucket: Dictionary = table[stat_key]
	bucket[source] = float(bucket.get(source, 0.0)) + value
	_cache.erase(stat_key)


## Drop every contribution a given source made, across all stats.
func remove_source(source: String) -> void:
	for table in [_absolute, _relative]:
		for stat_key in table.keys():
			var bucket: Dictionary = table[stat_key]
			if bucket.erase(source):
				_cache.erase(stat_key)


func value(stat_key: String) -> float:
	if _cache.has(stat_key):
		return _cache[stat_key]
	var flat := get_base(stat_key)
	for v in _absolute.get(stat_key, {}).values():
		flat += float(v)
	var factor := 1.0
	for v in _relative.get(stat_key, {}).values():
		factor += float(v)
	# A relative total below -100% would flip the sign, which never makes
	# sense for a stat; clamp instead.
	var out: float = flat * maxf(factor, 0.0)
	_cache[stat_key] = out
	return out


## Sum a qualified stat with its unqualified form, the usual lookup for
## damage types: Resistance/Fire on top of Resistance.
func value_with_sub(stat: String, sub: String) -> float:
	var total := value(stat)
	if not sub.is_empty():
		total += value(key(stat, sub))
	return total


func set_var(name: String, value_: float) -> void:
	_vars[name] = value_


func get_var(name: String, default: float = 0.0) -> float:
	return float(_vars.get(name, default))


func snapshot() -> Dictionary:
	var out := {}
	for stat_key in _base.keys():
		out[stat_key] = value(stat_key)
	for stat_key in _absolute.keys():
		out[stat_key] = value(stat_key)
	for stat_key in _relative.keys():
		out[stat_key] = value(stat_key)
	return out
