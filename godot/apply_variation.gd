@tool
extends SkeletonModifier3D
## Apply a DSO body-shape variation on top of the animated pose.
##
## Player characters share one skeleton; their build (tall, small, stocky,
## warrior, mage...) comes from a "variation": a static pose giving a
## per-joint translation and scale. The original engine keeps it apart from
## the bind pose (variationTranslation / variationScale) and applies it over
## the animated pose.
##
## This is a reconstruction: the rotation present in the data is ignored,
## since the engine only stores translation and scale. If the build looks
## wrong, this is the first place to check.
##
## Add this node as a CHILD of the Skeleton3D. Godot calls
## _process_modification() after animation, so the variation adds to the
## motion instead of being overwritten by it.

@export_file("*.json") var variations_path: String = "":
	set(value):
		variations_path = value
		_dirty = true
@export var variation_name: String = "":
	set(value):
		variation_name = value
		_dirty = true
## Scale is applied multiplicatively, translation additively.
@export var apply_scale: bool = true
@export var apply_translation: bool = true

var _dirty := true
var _bones: PackedInt32Array = []
var _t: Array[Vector3] = []
var _s: Array[Vector3] = []


func _resolve() -> void:
	_dirty = false
	_bones = PackedInt32Array()
	_t = []
	_s = []
	var skel := get_skeleton()
	if skel == null or variations_path.is_empty() or variation_name.is_empty():
		return
	var f := FileAccess.open(variations_path, FileAccess.READ)
	if f == null:
		push_warning("cannot read variations.json: %s" % variations_path)
		return
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		return
	var table: Dictionary = doc.get("variations", {})
	if not table.has(variation_name):
		push_warning("variation '%s' not found (%d available)" % [
			variation_name, table.size()])
		return
	var missing := 0
	for entry in table[variation_name]:
		var idx := skel.find_bone(String(entry["bone"]))
		if idx < 0:
			missing += 1
			continue
		var tv: Array = entry["t"]
		var sv: Array = entry["s"]
		_bones.append(idx)
		_t.append(Vector3(tv[0], tv[1], tv[2]))
		_s.append(Vector3(sv[0], sv[1], sv[2]))
	if missing > 0:
		push_warning("variation '%s': %d bones not found" % [variation_name, missing])


func _process_modification() -> void:
	if _dirty:
		_resolve()
	var skel := get_skeleton()
	if skel == null or _bones.is_empty():
		return
	for i in _bones.size():
		var b := _bones[i]
		if apply_scale:
			skel.set_bone_pose_scale(b, skel.get_bone_pose_scale(b) * _s[i])
		if apply_translation:
			skel.set_bone_pose_position(b, skel.get_bone_pose_position(b) + _t[i])
