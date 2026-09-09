@tool
extends Node
## Rebuild a DSO map inside Godot from a .map.json manifest.
##
## Usage: attach this script to a Node3D, fill in the two paths, then tick
## "build" in the inspector.
##
## Transforms are composed by Godot itself (Quaternion -> Basis), which avoids
## making any assumption about how matrices are serialised in .tscn.

@export_file("*.json") var manifest_path: String = ""
## Folder holding the exported .glb files (same tree as models/).
@export_dir var model_root: String = "res://models"
## Stop after N instances (0 = no limit). Handy for a first look.
@export var max_instances: int = 0
@export var build: bool = false:
	set(value):
		if value:
			_build()

func _build() -> void:
	for child in get_children():
		child.queue_free()

	var file := FileAccess.open(manifest_path, FileAccess.READ)
	if file == null:
		push_error("Cannot read manifest: %s" % manifest_path)
		return
	var doc: Dictionary = JSON.parse_string(file.get_as_text())
	if doc == null:
		push_error("Invalid JSON: %s" % manifest_path)
		return

	# Load each distinct model once, then instance it.
	var scenes: Array = []
	var failed := 0
	for ref in doc["models"]:
		var rel: String = String(ref).split(":")[-1]
		var path := "%s/%s.glb" % [model_root, rel]
		if ResourceLoader.exists(path):
			scenes.append(load(path))
		else:
			scenes.append(null)
			failed += 1
			push_warning("Missing model: %s" % path)

	var built := 0
	for inst in doc["instances"]:
		if max_instances > 0 and built >= max_instances:
			break
		var scene: PackedScene = scenes[int(inst["m"])]
		if scene == null:
			continue
		var node := scene.instantiate()
		var p: Array = inst["p"]
		var r: Array = inst["r"]
		var s: Array = inst["s"]
		var basis := Basis(Quaternion(r[0], r[1], r[2], r[3]))
		basis = basis.scaled(Vector3(s[0], s[1], s[2]))
		node.transform = Transform3D(basis, Vector3(p[0], p[1], p[2]))
		var label: String = String(inst["n"])
		node.name = label if not label.is_empty() else "inst_%d" % built
		add_child(node)
		node.owner = get_tree().edited_scene_root
		built += 1

	print("[DSO] %s: %d instances, %d models (%d missing)" % [
		doc["map"], built, scenes.size(), failed])
