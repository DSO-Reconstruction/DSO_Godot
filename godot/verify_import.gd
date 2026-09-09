extends SceneTree
## Import check: load .glb files from the project and inspect what Godot made.
##
##   godot --headless --path <projet> --script res://tools/verify_import.gd
##
## Checks ORIENTATION in particular: a character should be taller (Y) than it
## is wide (X/Z) and sit around Y=0. A model lying down would point to a
## missing axis conversion between Nebula3 and glTF.

const SCAN_ROOT := "res://dso"
const MAX_FILES := 40


func _init() -> void:
	var files: Array[String] = []
	_collect(SCAN_ROOT, files)
	print("[verify] %d .glb found, checking %d" % [files.size(), mini(files.size(), MAX_FILES)])
	var stats := {"ok": 0, "vide": 0, "sans_texture": 0, "couche": 0}
	for i in mini(files.size(), MAX_FILES):
		_check(files[i], stats)
	print("\n[verify] summary: %s" % stats)
	quit()


func _collect(dir_path: String, out: Array[String]) -> void:
	var d := DirAccess.open(dir_path)
	if d == null:
		return
	d.list_dir_begin()
	var name := d.get_next()
	while name != "":
		var full := dir_path.path_join(name)
		if d.current_is_dir():
			if not name.begins_with("."):
				_collect(full, out)
		elif name.ends_with(".glb"):
			out.append(full)
		name = d.get_next()
	d.list_dir_end()


func _check(path: String, stats: Dictionary) -> void:
	var packed = load(path)
	if packed == null:
		print("  LOAD FAILED: %s" % path)
		return
	var root: Node = packed.instantiate()

	var meshes: Array[MeshInstance3D] = []
	var skels: Array[Skeleton3D] = []
	var players: Array[AnimationPlayer] = []
	_walk(root, meshes, skels, players)

	# Boite englobante globale, en espace du modele.
	var aabb := AABB()
	var first := true
	var textured := 0
	for m in meshes:
		if m.mesh == null:
			continue
		var box: AABB = m.global_transform * m.mesh.get_aabb()
		aabb = box if first else aabb.merge(box)
		first = false
		for si in m.mesh.get_surface_count():
			var mat = m.mesh.surface_get_material(si)
			if mat is BaseMaterial3D and mat.albedo_texture != null:
				textured += 1
				break

	var anims: PackedStringArray = []
	var looped := 0
	var dur := 0.0
	for p in players:
		for a in p.get_animation_list():
			anims.append(a)
			var an := p.get_animation(a)
			dur = maxf(dur, an.length)
			if an.loop_mode != Animation.LOOP_NONE:
				looped += 1

	var label := path.get_file()
	if meshes.is_empty():
		stats["vide"] += 1
		print("  %-38s EMPTY (no mesh)" % label)
		root.free()
		return

	var size := aabb.size
	var upright := size.y >= maxf(size.x, size.z) * 0.55
	var bones := skels[0].get_bone_count() if not skels.is_empty() else 0
	if not upright and bones > 0:
		stats["couche"] += 1
	if textured == 0:
		stats["sans_texture"] += 1
	stats["ok"] += 1

	print("  %-38s meshes=%-3d bones=%-4d anims=%-4d (%d looping, max %.1fs)" % [
		label, meshes.size(), bones, anims.size(), looped, dur])
	print("        aabb=(%.2f x %.2f x %.2f) base_y=%.2f  %s  textures=%d/%d" % [
		size.x, size.y, size.z, aabb.position.y,
		("upright" if upright else "LYING DOWN?"), textured, meshes.size()])


func _walk(n: Node, meshes: Array[MeshInstance3D], skels: Array[Skeleton3D],
		players: Array[AnimationPlayer]) -> void:
	if n is MeshInstance3D:
		meshes.append(n)
	elif n is Skeleton3D:
		skels.append(n)
	elif n is AnimationPlayer:
		players.append(n)
	for c in n.get_children():
		_walk(c, meshes, skels, players)
