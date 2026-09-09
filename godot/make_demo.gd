extends SceneTree
## Builds res://demo.tscn: a few animated characters, an FX, lighting and a
## camera. A quick visual sanity check right after the import.

const CANDIDATES := [
	"res://dso/characters/boss_01.glb",
	"res://dso/characters/two_head_witch.glb",
	"res://dso/characters/beast_hound.glb",
	"res://dso/characters/atlantide.glb",
	"res://dso/characters/wendigo.glb",
]
const SPACING := 3.0


func _init() -> void:
	var root := Node3D.new()
	root.name = "Demo"

	_add_lighting(root)

	var placed := 0
	for path in CANDIDATES:
		if not ResourceLoader.exists(path):
			continue
		var ps := load(path) as PackedScene
		if ps == null:
			continue
		var inst: Node = ps.instantiate()
		var holder := Node3D.new()
		holder.name = path.get_file().get_basename()
		holder.position = Vector3((float(placed) - 2.0) * SPACING, 0.0, 0.0)
		root.add_child(holder)
		holder.owner = root
		holder.add_child(inst)
		_own_all(inst, root)
		# Start the first available animation.
		var ap := _find_player(inst)
		if ap != null:
			var names := ap.get_animation_list()
			if names.size() > 0:
				ap.autoplay = _prefer_idle(names)
		placed += 1
		if placed >= 5:
			break
	print("[demo] %d characters placed" % placed)

	# A particle effect, if it has already been converted.
	var fx_count := 0
	for fx in _find_fx("res://dso/effects", 3):
		var ps := load(fx) as PackedScene
		if ps == null:
			continue
		var inst: Node = ps.instantiate()
		if inst is Node3D:
			(inst as Node3D).position = Vector3(float(fx_count) * 2.0 - 2.0, 1.5, -4.0)
		root.add_child(inst)
		_own_all(inst, root)
		fx_count += 1
	print("[demo] %d effects placed" % fx_count)

	# Camera cadrant l'ensemble.
	var cam := Camera3D.new()
	cam.name = "Camera3D"
	cam.position = Vector3(0.0, 2.5, 9.0)
	cam.rotation_degrees = Vector3(-10.0, 0.0, 0.0)
	cam.current = true
	root.add_child(cam)
	cam.owner = root

	var packed := PackedScene.new()
	if packed.pack(root) != OK:
		print("[demo] packing failed")
		quit(1)
		return
	var err := ResourceSaver.save(packed, "res://demo.tscn")
	print("[demo] demo.tscn %s" % ("written" if err == OK else "FAILED (%d)" % err))
	quit()


func _add_lighting(root: Node3D) -> void:
	var sun := DirectionalLight3D.new()
	sun.name = "Sun"
	sun.rotation_degrees = Vector3(-45.0, 40.0, 0.0)
	sun.shadow_enabled = true
	root.add_child(sun)
	sun.owner = root

	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	sky.sky_material = ProceduralSkyMaterial.new()
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = 0.6
	var we := WorldEnvironment.new()
	we.name = "WorldEnvironment"
	we.environment = env
	root.add_child(we)
	we.owner = root


## Prefer an idle animation, otherwise just the first one.
func _prefer_idle(names: PackedStringArray) -> String:
	for n in names:
		var low := n.to_lower()
		if low.contains("idle") and not low.contains("combat"):
			return n
	return names[0]


func _find_player(n: Node) -> AnimationPlayer:
	if n is AnimationPlayer:
		return n
	for c in n.get_children():
		var r := _find_player(c)
		if r != null:
			return r
	return null


func _find_fx(dir_path: String, limit: int) -> Array[String]:
	var out: Array[String] = []
	var d := DirAccess.open(dir_path)
	if d == null:
		return out
	d.list_dir_begin()
	var name := d.get_next()
	while name != "" and out.size() < limit:
		if not d.current_is_dir() and name.ends_with(".fx.tscn"):
			out.append(dir_path.path_join(name))
		name = d.get_next()
	d.list_dir_end()
	return out


## For an INSTANCED scene, owner goes on its root only. Setting it on every
## descendant makes them saved twice over and breaks the binding between the
## animation tracks and the skeleton bones.
func _own_all(n: Node, owner_node: Node) -> void:
	n.owner = owner_node
