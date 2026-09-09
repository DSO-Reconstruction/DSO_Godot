@tool
extends Node3D
## Assemble a player-character outfit (uniskel / uniskel_dwarf).
##
## The original .n3 holds thousands of parts; split_character.py emitted them
## one per file, plus an animation file with no geometry. This script rebuilds
## an outfit: it instances the animation file as the base, then attaches each
## part's meshes to that base's Skeleton3D.
##
## The parts share the same bone names, so a single AnimationPlayer drives the
## whole thing.

## Folder produced by split_character.py (holds outfits.json and parts/).
@export_dir var character_dir: String = "res://dso/characters/uniskel"
## Outfit name, exactly as it appears in outfits.json.
@export var outfit_name: String = ""
@export var build: bool = false:
	set(value):
		if value:
			_build()


func _build() -> void:
	for c in get_children():
		c.queue_free()

	var f := FileAccess.open(character_dir.path_join("outfits.json"), FileAccess.READ)
	if f == null:
		push_error("outfits.json not found in %s" % character_dir)
		return
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("cannot read outfits.json")
		return

	var wanted: Array = []
	for o in doc["outfits"]:
		if String(o["name"]) == outfit_name:
			wanted = o["parts"]
			break
	if wanted.is_empty():
		push_error("outfit %s not found (%d available)" % [outfit_name, doc["outfits"].size()])
		return

	# Base: skeleton plus every animation, no geometry.
	var anim_path: String = character_dir.path_join(String(doc["animations"]))
	if not ResourceLoader.exists(anim_path):
		push_error("animation file missing: %s" % anim_path)
		return
	var base: Node = (load(anim_path) as PackedScene).instantiate()
	base.name = outfit_name
	add_child(base)
	base.owner = get_tree().edited_scene_root

	var skel := _find_skeleton(base)
	if skel == null:
		push_error("no Skeleton3D inside %s" % anim_path)
		return

	# For each part we take only its MeshInstance3D nodes and reparent them to
	# the base skeleton, so one AnimationPlayer animates them all.
	var added := 0
	var missing: Array[String] = []
	for part in wanted:
		var p: String = character_dir.path_join(String(doc["part_dir"])).path_join(String(part) + ".glb")
		if not ResourceLoader.exists(p):
			missing.append(String(part))
			continue
		var inst: Node = (load(p) as PackedScene).instantiate()
		for m in _meshes(inst):
			m.get_parent().remove_child(m)
			skel.add_child(m)
			m.owner = get_tree().edited_scene_root
			m.skeleton = NodePath("..")
			added += 1
		inst.free()

	print("[outfit] %s: %d meshes from %d parts (%d missing)" % [
		outfit_name, added, wanted.size(), missing.size()])
	if not missing.is_empty():
		push_warning("missing parts: %s" % ", ".join(missing))


func _find_skeleton(n: Node) -> Skeleton3D:
	if n is Skeleton3D:
		return n
	for c in n.get_children():
		var s := _find_skeleton(c)
		if s != null:
			return s
	return null


func _meshes(n: Node) -> Array[MeshInstance3D]:
	var out: Array[MeshInstance3D] = []
	if n is MeshInstance3D:
		out.append(n)
	for c in n.get_children():
		out.append_array(_meshes(c))
	return out
