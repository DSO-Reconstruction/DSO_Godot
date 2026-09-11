class_name DSOActor
extends RefCounted
## One combat participant, on the server.
##
## Holds stats, current health/resource, active status effects and capability
## flags. Deliberately engine-agnostic: no Node, no scene tree, so the same
## code runs in a headless zone server and in tests.

signal died(actor)
signal damaged(actor, amount, element, source)
signal healed(actor, amount)

var id: String
var char_class: String
var level: int = 1
var attributes := DSOAttributes.new()
var health: float = 0.0
var resource: float = 0.0
var alive := true
var position := Vector3.ZERO
var facing: float = 0.0

## capability -> {source_id: bool}. A capability is disabled while any source
## disables it, which is how overlapping stuns behave.
var _features: Dictionary = {}
## effect group -> count of immunity sources
var _immunities: Dictionary = {}


func _init(actor_id: String, cls: String = "") -> void:
	id = actor_id
	char_class = cls


func max_health() -> float:
	return maxf(attributes.value("HealthPoints"), 1.0)


func max_resource() -> float:
	return maxf(attributes.value("MaxResource"), 0.0)


func fill() -> void:
	health = max_health()
	resource = max_resource()
	alive = true


## Damage after resistance. Resistance is read as a fraction of reduction,
## qualified by element and falling back to the unqualified stat.
func take_damage(amount: float, element: String, source) -> void:
	if not alive or amount <= 0.0:
		return
	var resist := attributes.value_with_sub("Resistance", element)
	var reduction: float = clampf(resist, 0.0, 0.9)
	var applied: float = amount * (1.0 - reduction)
	health = maxf(health - applied, 0.0)
	damaged.emit(self, applied, element, source)
	if health <= 0.0:
		alive = false
		died.emit(self)


func heal(amount: float) -> void:
	if not alive or amount <= 0.0:
		return
	health = minf(health + amount, max_health())
	healed.emit(self, amount)


func add_resource(amount: float) -> void:
	resource = clampf(resource + amount, 0.0, max_resource())


func set_feature(name: String, enabled: bool, source: String) -> void:
	if not _features.has(name):
		_features[name] = {}
	if enabled:
		_features[name].erase(source)
	else:
		_features[name][source] = true


func has_feature(name: String) -> bool:
	return _features.get(name, {}).is_empty()


func clear_feature_source(source: String) -> void:
	for name in _features.keys():
		_features[name].erase(source)


func add_immunity(group: String) -> void:
	_immunities[group] = int(_immunities.get(group, 0)) + 1


func remove_immunity(group: String) -> void:
	var n := int(_immunities.get(group, 0)) - 1
	if n <= 0:
		_immunities.erase(group)
	else:
		_immunities[group] = n


func is_immune_to(group: String) -> bool:
	return _immunities.has(group)


func distance_to(other: DSOActor) -> float:
	return position.distance_to(other.position)
