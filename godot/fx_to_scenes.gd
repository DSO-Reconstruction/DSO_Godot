extends SceneTree
## Convert exported .fx.json files into Godot GPUParticles3D scenes.
##
## Run headless:
##   godot --headless --path <project> --script res://tools/fx_to_scenes.gd
##
## Nebula emitters are described by "envelope curves" with 4 control points
## (at times 0, keypos0, keypos1, 1). We map those onto Godot Curve/Gradient
## resources, and the scalars onto ParticleProcessMaterial properties.
## This is an approximation: the original emission model is not Godot's.

const FX_ROOT := "res://dso"
const OUT_SUFFIX := ".fx.tscn"


func _init() -> void:
	var files: Array[String] = []
	_collect(FX_ROOT, files)
	print("[fx] %d .fx.json files found" % files.size())
	var made := 0
	var emitters := 0
	for f in files:
		var n := _convert(f)
		if n > 0:
			made += 1
			emitters += n
	print("[fx] %d scenes written, %d emitters" % [made, emitters]),
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
		elif name.ends_with(".fx.json"):
			out.append(full)
		name = d.get_next()
	d.list_dir_end()


## Representative value of an envelope: its largest control value.
func _env_peak(env) -> float:
	if typeof(env) != TYPE_DICTIONARY:
		return float(env) if typeof(env) in [TYPE_FLOAT, TYPE_INT] else 0.0
	var best := 0.0
	for v in env.get("values", []):
		best = max(best, abs(float(v)))
	return best


func _env_first(env) -> float:
	if typeof(env) != TYPE_DICTIONARY:
		return float(env) if typeof(env) in [TYPE_FLOAT, TYPE_INT] else 0.0
	var vals: Array = env.get("values", [])
	return float(vals[0]) if vals.size() > 0 else 0.0


## Envelope -> Godot Curve, honouring the control-point positions.
func _env_curve(env) -> Curve:
	if typeof(env) != TYPE_DICTIONARY:
		return null
	var vals: Array = env.get("values", [])
	if vals.size() < 4:
		return null
	var kp: Array = env.get("keypos", [0.33, 0.66])
	var t1: float = clampf(float(kp[0]) if kp.size() > 0 else 0.33, 0.0, 1.0)
	var t2: float = clampf(float(kp[1]) if kp.size() > 1 else 0.66, 0.0, 1.0)
	var peak := _env_peak(env)
	if peak <= 0.0:
		return null
	var curve := Curve.new()
	curve.min_value = 0.0
	curve.max_value = 1.0
	# Normalised: Godot multiplies the curve by the base scale.
	for pair in [[0.0, vals[0]], [t1, vals[1]], [t2, vals[2]], [1.0, vals[3]]]:
		curve.add_point(Vector2(float(pair[0]), clampf(float(pair[1]) / peak, 0.0, 1.0)))
	return curve


## RGBA envelopes -> Gradient (colour ramp over the lifetime).
func _color_ramp(em: Dictionary) -> Gradient:
	var r = em.get("color_red", null)
	var g = em.get("color_green", null)
	var b = em.get("color_blue", null)
	var a = em.get("color_alpha", null)
	if r == null and g == null and b == null and a == null:
		return null
	var kp: Array = [0.33, 0.66]
	if typeof(a) == TYPE_DICTIONARY:
		kp = a.get("keypos", kp)
	var t1: float = clampf(float(kp[0]), 0.0, 1.0)
	var t2: float = clampf(float(kp[1]), 0.0, 1.0)
	# Offsets must increase strictly, or Godot keeps a dead point.
	var times := [0.0, maxf(t1, 0.001), maxf(t2, t1 + 0.001), 1.0]
	var offs := PackedFloat32Array()
	var cols := PackedColorArray()
	for i in 4:
		offs.append(float(times[i]))
		cols.append(Color(_env_at(r, i, 1.0), _env_at(g, i, 1.0),
			_env_at(b, i, 1.0), _env_at(a, i, 1.0)))
	var grad := Gradient.new()
	# Direct assignment: a Gradient starts with 2 points and remove_point
	# refuses to go below 1, which left an opaque black point at t=0.
	grad.offsets = offs
	grad.colors = cols
	return grad


func _env_at(env, i: int, fallback: float) -> float:
	if typeof(env) != TYPE_DICTIONARY:
		return fallback
	var vals: Array = env.get("values", [])
	return clampf(float(vals[i]), 0.0, 1.0) if i < vals.size() else fallback


func _convert(json_path: String) -> int:
	var f := FileAccess.open(json_path, FileAccess.READ)
	if f == null:
		return 0
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		return 0
	var list: Array = doc.get("emitters", [])
	if list.is_empty():
		return 0

	var root := Node3D.new()
	root.name = String(doc.get("model", "fx")).get_file()
	var count := 0

	for e in list:
		var em: Dictionary = e.get("emitter", {})
		if em.is_empty():
			continue
		var p := GPUParticles3D.new()
		p.name = String(e.get("node", "emitter_%d" % count))

		var lifetime := maxf(_env_peak(em.get("lifetime", 1.0)), 0.05)
		var freq := maxf(_env_peak(em.get("emission_frequency", 10.0)), 0.1)
		p.lifetime = lifetime
		# Godot counts live particles; Nebula specifies an emission rate.
		p.amount = int(clampf(ceil(freq * lifetime), 1.0, 8192.0))
		p.preprocess = float(em.get("precalc_time", 0.0))
		var looping := int(em.get("looping", 1)) != 0
		p.one_shot = not looping
		p.visibility_aabb = AABB(Vector3(-8, -8, -8), Vector3(16, 16, 16))

		var mat := ParticleProcessMaterial.new()
		mat.gravity = Vector3(0.0, -float(em.get("gravity", 0.0)), 0.0)
		var vel := _env_peak(em.get("start_velocity", 0.0))
		var vrand := clampf(float(em.get("velocity_randomize", 0.0)), 0.0, 1.0)
		mat.initial_velocity_min = vel * (1.0 - vrand)
		mat.initial_velocity_max = vel
		var spread := _env_peak(em.get("spread_max", 0.0))
		mat.spread = clampf(rad_to_deg(spread), 0.0, 180.0)
		mat.damping_min = 0.0
		mat.damping_max = _env_peak(em.get("air_resistance", 0.0))
		var av := rad_to_deg(_env_peak(em.get("rotation_velocity", 0.0)))
		mat.angular_velocity_min = -av if int(em.get("randomize_rotation", 0)) != 0 else av
		mat.angular_velocity_max = av
		var rmin := rad_to_deg(float(em.get("start_rotation_min", 0.0)))
		var rmax := rad_to_deg(float(em.get("start_rotation_max", 0.0)))
		mat.angle_min = minf(rmin, rmax)
		mat.angle_max = maxf(rmin, rmax)
		mat.particle_flag_disable_z = false
		# Size: a base value plus a curve over the lifetime.
		var size_peak := maxf(_env_peak(em.get("particle_size", 1.0)), 0.001)
		var srand := clampf(float(em.get("size_randomize", 0.0)), 0.0, 1.0)
		mat.scale_min = size_peak * (1.0 - srand)
		mat.scale_max = size_peak
		var scurve := _env_curve(em.get("particle_size", null))
		if scurve != null:
			var ct := CurveTexture.new()
			ct.curve = scurve
			mat.scale_curve = ct
		var grad := _color_ramp(em)
		if grad != null:
			var gt := GradientTexture1D.new()
			gt.gradient = grad
			mat.color_ramp = gt
		p.process_material = mat

		# Rendering: camera-facing quad, emitter texture, shader blend mode.
		var draw := QuadMesh.new()
		draw.size = Vector2.ONE
		var sm := StandardMaterial3D.new()
		sm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		sm.billboard_mode = (BaseMaterial3D.BILLBOARD_PARTICLES
			if int(em.get("billboard", 1)) != 0 else BaseMaterial3D.BILLBOARD_DISABLED)
		sm.billboard_keep_scale = true
		sm.vertex_color_use_as_albedo = true
		sm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		sm.cull_mode = BaseMaterial3D.CULL_DISABLED
		sm.disable_receive_shadows = true
		var kind := String(em.get("type_name", "")).to_lower()
		if kind.contains("additive"):
			sm.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
		elif kind.contains("multiply"):
			sm.blend_mode = BaseMaterial3D.BLEND_MODE_MUL
		var tex_ref := String(e.get("textures", {}).get("DiffMap0", ""))
		var tex := _load_texture(json_path, tex_ref)
		if tex != null:
			sm.albedo_texture = tex
		var tile := int(float(em.get("texture_tile", 1.0)))
		if tile > 1:
			# The texture is a sprite atlas: Godot handles that through
			# particles_anim_*, not uv1_scale (which distorted the display).
			sm.particles_anim_h_frames = tile
			sm.particles_anim_v_frames = tile
			sm.particles_anim_loop = false
			mat.anim_offset_min = 0.0
			mat.anim_offset_max = 1.0
		draw.material = sm
		p.draw_pass_1 = draw

		root.add_child(p)
		p.owner = root
		count += 1

	if count == 0:
		root.free()
		return 0

	var packed := PackedScene.new()
	if packed.pack(root) != OK:
		root.free()
		return 0
	var out_path := json_path.trim_suffix(".fx.json") + OUT_SUFFIX
	var err := ResourceSaver.save(packed, out_path)
	root.free()
	return count if err == OK else 0


## 'tex:folder/name' -> res://.../textures/folder/name.png, relative to the .fx.json.
func _load_texture(json_path: String, ref: String) -> Texture2D:
	if ref.is_empty():
		return null
	var rel := ref.split(":")[-1]
	var dir := json_path.get_base_dir()
	# Walk up the tree until a textures/ folder turns up.
	for _i in 6:
		var candidate := dir.path_join("textures").path_join(rel + ".png")
		if ResourceLoader.exists(candidate):
			return load(candidate)
		if dir == "res://":
			break
		dir = dir.get_base_dir()
	return null
