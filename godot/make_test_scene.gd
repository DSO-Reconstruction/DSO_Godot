extends SceneTree
## Build res://test.tscn from test_config.json: the map, an animated mob, an
## equipped player character, and the FX.

const CONFIG := "res://test_config.json"


func _init() -> void:
	var cfg := _json(CONFIG)
	if cfg.is_empty():
		print("[scene] cannot read test_config.json")
		quit(1)
		return

	var root := Node3D.new()
	root.name = "Test"
	_lighting(root)

	_build_map(root, cfg)
	_build_mob(root, cfg)
	_build_character(root, cfg)
	_build_fx(root, cfg)

	var cam := Camera3D.new()
	cam.name = "Camera3D"
	cam.position = Vector3(6.0, 5.0, 14.0)
	cam.rotation_degrees = Vector3(-14.0, 12.0, 0.0)
	cam.current = true
	cam.far = 500.0
	root.add_child(cam)
	cam.owner = root

	var packed := PackedScene.new()
	if packed.pack(root) != OK:
		print("[scene] packing failed")
		quit(1)
		return
	var err := ResourceSaver.save(packed, "res://test.tscn")
	print("[scene] test.tscn: %s" % ("written" if err == OK else "FAILED %d" % err))
	quit()


func _json(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return {}
	var d = JSON.parse_string(f.get_as_text())
	return d if typeof(d) == TYPE_DICTIONARY else {}


func _lighting(root: Node3D) -> void:
	var sun := DirectionalLight3D.new()
	sun.name = "Sun"
	sun.rotation_degrees = Vector3(-50.0, 35.0, 0.0)
	sun.shadow_enabled = true
	root.add_child(sun)
	sun.owner = root
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	sky.sky_material = ProceduralSkyMaterial.new()
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = 0.8
	var we := WorldEnvironment.new()
	we.name = "WorldEnvironment"
	we.environment = env
	root.add_child(we)
	we.owner = root


func _build_map(root: Node3D, cfg: Dictionary) -> void:
	var doc := _json(String(cfg.get("map", "")))
	if doc.is_empty():
		print("[scene] map: manifest missing")
		return
	var holder := Node3D.new()
	holder.name = "Map_" + String(doc.get("map", "map"))
	root.add_child(holder)
	holder.owner = root

	# Load each distinct model once, then one instance per placed object.
	var scenes: Array = []
	var missing := 0
	for ref in doc["models"]:
		var p := "res://dso/%s.glb" % String(ref).split(":")[-1]
		if ResourceLoader.exists(p):
			scenes.append(load(p))
		else:
			scenes.append(null)
			missing += 1

	var limit := int(cfg.get("map_instances", 0))
	var built := 0
	for inst in doc["instances"]:
		if limit > 0 and built >= limit:
			break
		var ps: PackedScene = scenes[int(inst["m"])]
		if ps == null:
			continue
		var node: Node3D = ps.instantiate()
		var p: Array = inst["p"]
		var r: Array = inst["r"]
		var s: Array = inst["s"]
		# Godot composes Quaternion -> Basis itself, so we make no assumption
		# about how matrices are serialised.
		var basis := Basis(Quaternion(r[0], r[1], r[2], r[3]))
		basis = basis.scaled(Vector3(s[0], s[1], s[2]))
		node.transform = Transform3D(basis, Vector3(p[0], p[1], p[2]))
		var nm := String(inst["n"])
		node.name = nm if not nm.is_empty() else "inst_%d" % built
		holder.add_child(node)
		_own(node, root)
		built += 1
	print("[scene] map: %d objects placed, %d models (%d missing)" % [
		built, scenes.size(), missing])


func _build_mob(root: Node3D, cfg: Dictionary) -> void:
	var p := String(cfg.get("mob", ""))
	if not ResourceLoader.exists(p):
		print("[scene] mob: %s missing" % p)
		return
	var holder := Node3D.new()
	holder.name = "Mob"
	holder.position = Vector3(-4.0, 0.0, 6.0)
	root.add_child(holder)
	holder.owner = root
	var inst: Node = (load(p) as PackedScene).instantiate()
	holder.add_child(inst)
	_own(inst, root)
	var ap := _find(inst, "AnimationPlayer") as AnimationPlayer
	var picked := ""
	if ap != null:
		var names := ap.get_animation_list()
		picked = _prefer(names)
		if not picked.is_empty():
			ap.autoplay = picked
	var skel := _find(inst, "Skeleton3D") as Skeleton3D
	print("[scene] mob: %d bones, %d anims, autoplay=%s" % [
		skel.get_bone_count() if skel != null else 0,
		ap.get_animation_list().size() if ap != null else 0, picked])


func _build_character(root: Node3D, cfg: Dictionary) -> void:
	var cdir := String(cfg.get("character_dir", ""))
	var doc := _json(cdir.path_join("outfits.json"))
	if doc.is_empty():
		print("[scene] character: outfits.json missing")
		return
	var want := String(cfg.get("outfit", ""))
	var parts: Array = []
	for o in doc["outfits"]:
		if String(o["name"]) == want:
			parts = o["parts"]
			break

	var anim_path: String = cdir.path_join(String(doc["animations"]))
	if not ResourceLoader.exists(anim_path):
		print("[scene] character: %s missing" % anim_path)
		return
	var holder := Node3D.new()
	holder.name = "Character_" + want
	holder.position = Vector3(4.0, 0.0, 6.0)
	root.add_child(holder)
	holder.owner = root

	# Base = skeleton plus every clip, no geometry.
	var base: Node = (load(anim_path) as PackedScene).instantiate()
	# We graft the part meshes INTO this subtree, so it has to be saved as
	# plain nodes rather than as an instance: otherwise the additions do not
	# survive saving the scene.
	base.scene_file_path = ""
	holder.add_child(base)
	_own_deep(base, root)
	var skel := _find(base, "Skeleton3D") as Skeleton3D
	if skel == null:
		print("[scene] character: no Skeleton3D")
		return

	# For each part we keep only its meshes, reparented to this skeleton, so
	# a single AnimationPlayer drives the whole character.
	var added := 0
	var absent := 0
	for part in parts:
		var pp: String = cdir.path_join("parts").path_join(String(part) + ".glb")
		if not ResourceLoader.exists(pp):
			absent += 1
			continue
		var pi: Node = (load(pp) as PackedScene).instantiate()
		for m in _meshes(pi):
			m.get_parent().remove_child(m)
			skel.add_child(m)
			m.owner = root
			m.skeleton = NodePath("..")
			added += 1
		pi.free()

	# Body shape: the outfit names a variation, applied on top of the
	# animation (all player characters share one skeleton).
	var variation := ""
	for o in doc["outfits"]:
		if String(o["name"]) == want:
			variation = String(o.get("variation", ""))
			break
	var vpath := String(cfg.get("variations", ""))
	if not variation.is_empty() and not vpath.is_empty() and FileAccess.file_exists(vpath):
		var mod := SkeletonModifier3D.new()
		mod.set_script(load("res://tools/apply_variation.gd"))
		mod.name = "Variation"
		skel.add_child(mod)
		mod.owner = root
		mod.set("variations_path", vpath)
		mod.set("variation_name", variation)
		print("[scene] character: variation '%s' applied" % variation)

	var ap := _find(base, "AnimationPlayer") as AnimationPlayer
	var picked := ""
	if ap != null:
		picked = _prefer(ap.get_animation_list())
		if not picked.is_empty():
			ap.autoplay = picked
	print("[scene] character: %d bones, %d meshes from %d parts (%d missing), %d anims, autoplay=%s" % [
		skel.get_bone_count(), added, parts.size(), absent,
		ap.get_animation_list().size() if ap != null else 0, picked])


func _build_fx(root: Node3D, cfg: Dictionary) -> void:
	var holder := Node3D.new()
	holder.name = "FX"
	root.add_child(holder)
	holder.owner = root
	var i := 0
	var placed := 0
	var emitters := 0
	for p in cfg.get("fx", []):
		var path := String(p)
		if not ResourceLoader.exists(path):
			print("[scene] FX missing: %s" % path)
			i += 1
			continue
		var inst: Node = (load(path) as PackedScene).instantiate()
		if inst is Node3D:
			(inst as Node3D).position = Vector3(float(i) * 2.5 - 4.0, 1.5, 9.0)
		holder.add_child(inst)
		_own(inst, root)
		emitters += _count(inst, "GPUParticles3D")
		placed += 1
		i += 1
	print("[scene] FX: %d scenes, %d emitters" % [placed, emitters])


## Prefer an idle animation, so the scene reads well when first opened.
func _prefer(names: PackedStringArray) -> String:
	if names.is_empty():
		return ""
	# Favour the plainest idle names: out of 743 clips, "bomb_idle" is not
	# what you want to see on opening.
	for pref in ["idle_01", "idle_1", "stand_idle", "combat_idle_01"]:
		for n in names:
			if n.to_lower().ends_with(pref):
				return n
	for n in names:
		var low := n.to_lower()
		if low.begins_with("idle") or low.contains("_idle"):
			return n
	return names[0]


func _find(n: Node, cls: String) -> Node:
	if n.is_class(cls):
		return n
	for c in n.get_children():
		var r := _find(c, cls)
		if r != null:
			return r
	return null


func _count(n: Node, cls: String) -> int:
	var t := 1 if n.is_class(cls) else 0
	for c in n.get_children():
		t += _count(c, cls)
	return t


func _meshes(n: Node) -> Array[MeshInstance3D]:
	var out: Array[MeshInstance3D] = []
	if n is MeshInstance3D:
		out.append(n)
	for c in n.get_children():
		out.append_array(_meshes(c))
	return out


## For an INSTANCED scene, owner must be set on its root only: Godot then
## saves it as an instance and its children follow. Setting owner on every
## descendant saves them on top of the instance, which duplicates nodes and
## breaks the binding between animation tracks and bones.
func _own(n: Node, owner_node: Node) -> void:
	n.owner = owner_node


## For a subtree whose instance link is cut (empty scene_file_path), every
## node must be owned explicitly to be saved.
func _own_deep(n: Node, owner_node: Node) -> void:
	n.owner = owner_node
	for c in n.get_children():
		_own_deep(c, owner_node)
