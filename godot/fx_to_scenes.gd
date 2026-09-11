extends SceneTree
## Rebuild the Nebula3 effects on top of the exported .glb models.
##
##   godot --headless --path <project> --script res://tools/fx_to_scenes.gd
##
## For every <model>.fx.json it writes <model>.fx.tscn: the .glb instanced as
## it was imported, with two things put back that glTF cannot carry.
##
## 1. Render state. Nebula picks a blend mode per node (the `MNTP` tag:
##    Solid, Alpha, Additive, AlphaTest, Refraction, Decal...). glTF has only
##    OPAQUE / MASK / BLEND, so every additive glow imported as a *lit,
##    alpha-blended* surface -- which is why the effects came out as dark
##    quads. The mode is in the sidecar JSON; here it becomes the matching
##    BaseMaterial3D (additive, unshaded, no depth write, no culling).
##
## 2. The emitters themselves. A `PSND` node's mesh is not artwork: it is the
##    surface particles are born on. Left alone the .glb draws it as a static
##    blob. Here the mesh is dropped and its vertices and normals become the
##    emission points of a GPUParticles3D, so particles spawn where and along
##    the direction the artist authored.
##
## The emitter model is still an approximation: Nebula's rate becomes a live
## particle count, its four-point envelopes become Curve/Gradient resources,
## and `stretch` / `stretch_to_start` have no Godot equivalent.

const FX_ROOT := "res://dso"
const OUT_SUFFIX := ".fx.tscn"
const MAX_EMISSION_POINTS := 2048

## Nebula's refraction pass warps what is behind the surface by a DuDv map.
## glTF has nowhere to put that, and drawing the DuDv map as colour looks
## worse than nothing, so it is rebuilt as a small screen-space shader.
const REFRACTION_SHADER := """shader_type spatial;
render_mode unshaded, blend_mix, depth_draw_never, cull_disabled,
	shadows_disabled, specular_disabled;

uniform sampler2D dudv_map : source_color, filter_linear_mipmap, repeat_enable;
uniform sampler2D screen_tex : hint_screen_texture, filter_linear_mipmap;
uniform float strength : hint_range(0.0, 0.3) = 0.05;

void fragment() {
	vec4 d = texture(dudv_map, UV);
	vec2 offset = (d.rg * 2.0 - 1.0) * strength;
	ALBEDO = textureLod(screen_tex, SCREEN_UV + offset, 0.0).rgb;
	ALPHA = d.a;
}
"""

var _refraction: Shader = null

var _made := 0
var _emitters := 0
var _fixed := 0
var _skipped := 0


func _init() -> void:
	var files: Array[String] = []
	_collect(FX_ROOT, files)
	print("[fx] %d .fx.json found" % files.size())
	for f in files:
		_convert(f)
	print("[fx] %d scenes written, %d emitters rebuilt, %d materials corrected, %d skipped"
		% [_made, _emitters, _fixed, _skipped])
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


# --------------------------------------------------------------- envelopes

## Representative value of a Nebula envelope: its largest control point.
func _env_peak(env) -> float:
	if typeof(env) != TYPE_DICTIONARY:
		return float(env) if typeof(env) in [TYPE_FLOAT, TYPE_INT] else 0.0
	var best := 0.0
	for v in env.get("values", []):
		best = max(best, abs(float(v)))
	return best


## Envelope -> Curve, normalised to its peak, honouring the key positions.
func _env_curve(env) -> Curve:
	if typeof(env) != TYPE_DICTIONARY:
		return null
	var vals: Array = env.get("values", [])
	if vals.size() < 4:
		return null
	var peak := _env_peak(env)
	if peak <= 0.0:
		return null
	var kp: Array = env.get("keypos", [0.33, 0.66])
	var t1: float = clampf(float(kp[0]) if kp.size() > 0 else 0.33, 0.0, 1.0)
	var t2: float = clampf(float(kp[1]) if kp.size() > 1 else 0.66, t1 + 0.001, 1.0)
	var curve := Curve.new()
	curve.min_value = 0.0
	curve.max_value = 1.0
	for pair in [[0.0, vals[0]], [t1, vals[1]], [t2, vals[2]], [1.0, vals[3]]]:
		curve.add_point(Vector2(float(pair[0]), clampf(float(pair[1]) / peak, 0.0, 1.0)))
	return curve


func _env_at(env, i: int, fallback: float) -> float:
	if typeof(env) != TYPE_DICTIONARY:
		return fallback
	var vals: Array = env.get("values", [])
	return clampf(float(vals[i]), 0.0, 1.0) if i < vals.size() else fallback


## The four RGBA envelopes -> one colour ramp over the particle lifetime.
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
	var t2: float = clampf(float(kp[1]), t1 + 0.001, 1.0)
	var offs := PackedFloat32Array([0.0, maxf(t1, 0.001), t2, 1.0])
	var cols := PackedColorArray()
	for i in 4:
		cols.append(Color(_env_at(r, i, 1.0), _env_at(g, i, 1.0),
			_env_at(b, i, 1.0), _env_at(a, i, 1.0)))
	var grad := Gradient.new()
	# Assigned wholesale: a fresh Gradient starts with two points and
	# remove_point refuses to drop the last one, which left a black stop at 0.
	grad.offsets = offs
	grad.colors = cols
	return grad


# ----------------------------------------------------------- render states

## Apply one Nebula render state to a material.
##
## The names are the engine's own (`MNTP`). Anything unknown is left as the
## importer made it rather than guessed at.
func _apply_state(mat: BaseMaterial3D, state: String) -> bool:
	match state:
		"Solid", "DecalReceiveSolid":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED
			return true
		"AlphaTest", "DecalReceiveAlphaTest":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
			mat.alpha_scissor_threshold = 0.5
			return true
		"Alpha", "AlphaLit", "AlphaBgLit", "DecalReceiveAlphaLit":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			return true
		"AlphaLitOnlyNormalNoZWrite":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			return true
		"AlphaUnlit", "PostAlphaUnlit", "PreAlphaUnlit", "Background":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			return true
		"Additive", "PreAdditive":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
			mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			mat.cull_mode = BaseMaterial3D.CULL_DISABLED
			return true
		"Decal", "DecalEffect", "StretchedDecal":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			return true
		"Refraction":
			# Handled by a screen-space ShaderMaterial in _fix_materials; on a
			# particle draw pass, where that shader has no meaning, fall back
			# to an unshaded veil.
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			return true
	return false


## Unshaded materials in Godot only read ALBEDO -- emission is ignored.
##
## Most Nebula glows carry no `DiffMap0` at all: the artwork is in `EmsvMap0`,
## and the engine's additive pass adds *that*. Imported as glTF it becomes an
## emissiveTexture over a white base colour, so the moment the material goes
## unshaded the quad turns into a sheet of pure white being added to the
## frame. Folding the emission back into the albedo is what makes an additive
## effect look like the effect again.
func _fold_emission(mat: BaseMaterial3D) -> void:
	if mat.albedo_texture != null or not mat.emission_enabled:
		return
	if mat.emission_texture != null:
		mat.albedo_texture = mat.emission_texture
		mat.albedo_color = Color(mat.emission, 1.0) if mat.emission != Color.BLACK \
			else Color.WHITE
	else:
		mat.albedo_color = Color(mat.emission, mat.albedo_color.a)
	mat.emission_enabled = false


func _refraction_material(src: BaseMaterial3D) -> ShaderMaterial:
	if _refraction == null:
		_refraction = Shader.new()
		_refraction.code = REFRACTION_SHADER
	var sm := ShaderMaterial.new()
	sm.shader = _refraction
	var tex := src.albedo_texture
	if tex == null:
		tex = src.emission_texture
	if tex != null:
		sm.set_shader_parameter("dudv_map", tex)
	sm.set_shader_parameter("strength", 0.05)
	return sm


func _fix_materials(mi: MeshInstance3D, state: String) -> void:
	var mesh := mi.mesh
	if mesh == null:
		return
	for i in mesh.get_surface_count():
		var src := mi.get_active_material(i)
		if not (src is BaseMaterial3D):
			continue
		if state == "Refraction":
			mi.set_surface_override_material(i, _refraction_material(src as BaseMaterial3D))
			_fixed += 1
			continue
		var mat := (src as BaseMaterial3D).duplicate() as BaseMaterial3D
		if _apply_state(mat, state):
			if mat.shading_mode == BaseMaterial3D.SHADING_MODE_UNSHADED:
				_fold_emission(mat)
			mat.disable_receive_shadows = true
			mi.set_surface_override_material(i, mat)
			_fixed += 1


# -------------------------------------------------------------- emitters

## Emitter mesh vertices/normals -> emission point textures.
func _emission_from_mesh(mat: ParticleProcessMaterial, mesh: Mesh) -> void:
	if mesh == null or mesh.get_surface_count() == 0:
		return
	var pts := PackedVector3Array()
	var nrm := PackedVector3Array()
	for s in mesh.get_surface_count():
		var arr := mesh.surface_get_arrays(s)
		var v: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
		var n: PackedVector3Array = (arr[Mesh.ARRAY_NORMAL]
			if arr[Mesh.ARRAY_NORMAL] != null else PackedVector3Array())
		for i in v.size():
			pts.append(v[i])
			nrm.append(n[i] if i < n.size() else Vector3.UP)
	if pts.is_empty():
		return
	# One pixel per point; sample down rather than ship a huge texture.
	var step := maxi(1, int(ceil(float(pts.size()) / MAX_EMISSION_POINTS)))
	var kept := PackedVector3Array()
	var kept_n := PackedVector3Array()
	for i in range(0, pts.size(), step):
		kept.append(pts[i])
		kept_n.append(nrm[i])
	var img := Image.create(kept.size(), 1, false, Image.FORMAT_RGBF)
	var imn := Image.create(kept.size(), 1, false, Image.FORMAT_RGBF)
	for i in kept.size():
		img.set_pixel(i, 0, Color(kept[i].x, kept[i].y, kept[i].z))
		imn.set_pixel(i, 0, Color(kept_n[i].x, kept_n[i].y, kept_n[i].z))
	mat.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_DIRECTED_POINTS
	mat.emission_point_count = kept.size()
	mat.emission_point_texture = ImageTexture.create_from_image(img)
	mat.emission_normal_texture = ImageTexture.create_from_image(imn)


func _build_emitter(name: String, em: Dictionary, mi: MeshInstance3D) -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.name = name + "_particles"

	var lifetime := maxf(_env_peak(em.get("lifetime", 1.0)), 0.05)
	var freq := maxf(_env_peak(em.get("emission_frequency", 10.0)), 0.1)
	p.lifetime = lifetime
	# Nebula gives a birth rate; Godot wants a pool of live particles.
	p.amount = int(clampf(ceil(freq * lifetime), 1.0, 8192.0))
	p.preprocess = float(em.get("precalc_time", 0.0))
	p.one_shot = int(em.get("looping", 1)) == 0
	var dur := float(em.get("emission_duration", 0.0))
	if p.one_shot and dur > 0.0:
		p.lifetime = maxf(lifetime, dur)
	p.local_coords = false
	# Particles leave the emitter: a tight AABB culls them mid-flight.
	var reach := maxf(_env_peak(em.get("start_velocity", 0.0)) * lifetime, 1.0) + 4.0
	p.visibility_aabb = AABB(Vector3.ONE * -reach, Vector3.ONE * (reach * 2.0))

	var mat := ParticleProcessMaterial.new()
	_emission_from_mesh(mat, mi.mesh if mi != null else null)
	mat.gravity = Vector3(0.0, -float(em.get("gravity", 0.0)), 0.0)
	var vel := _env_peak(em.get("start_velocity", 0.0))
	var vrand := clampf(float(em.get("velocity_randomize", 0.0)), 0.0, 1.0)
	mat.initial_velocity_min = vel * (1.0 - vrand)
	mat.initial_velocity_max = vel
	mat.spread = clampf(rad_to_deg(_env_peak(em.get("spread_max", 0.0))), 0.0, 180.0)
	mat.damping_min = 0.0
	mat.damping_max = _env_peak(em.get("air_resistance", 0.0))
	var av := rad_to_deg(_env_peak(em.get("rotation_velocity", 0.0)))
	mat.angular_velocity_min = -av if int(em.get("randomize_rotation", 0)) != 0 else av
	mat.angular_velocity_max = av
	var rmin := rad_to_deg(float(em.get("start_rotation_min", 0.0)))
	var rmax := rad_to_deg(float(em.get("start_rotation_max", 0.0)))
	mat.angle_min = minf(rmin, rmax)
	mat.angle_max = maxf(rmin, rmax)
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
	return p


## The billboard quad the particles are drawn with, in the emitter's own
## render state -- this is where the additive glows come back.
func _draw_pass(em: Dictionary, mi: MeshInstance3D) -> QuadMesh:
	var draw := QuadMesh.new()
	draw.size = Vector2.ONE
	var sm: StandardMaterial3D = null
	if mi != null:
		var src := mi.get_active_material(0)
		if src is StandardMaterial3D:
			sm = (src as StandardMaterial3D).duplicate() as StandardMaterial3D
	if sm == null:
		sm = StandardMaterial3D.new()
	sm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	sm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	sm.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	sm.cull_mode = BaseMaterial3D.CULL_DISABLED
	sm.disable_receive_shadows = true
	sm.vertex_color_use_as_albedo = true
	sm.billboard_mode = (BaseMaterial3D.BILLBOARD_PARTICLES
		if int(em.get("billboard", 1)) != 0 else BaseMaterial3D.BILLBOARD_DISABLED)
	sm.billboard_keep_scale = true
	_apply_state(sm, String(em.get("type_name", "Alpha")))
	_fold_emission(sm)
	var tile := int(float(em.get("texture_tile", 1.0)))
	if tile > 1:
		# The texture is a flipbook, which Godot drives through
		# particles_anim_*, not uv1_scale (that just distorted it).
		sm.particles_anim_h_frames = tile
		sm.particles_anim_v_frames = tile
		sm.particles_anim_loop = false
	draw.material = sm
	return draw


# ---------------------------------------------------------------- one model

func _index(node: Node, out: Dictionary) -> void:
	out[String(node.name)] = node
	for c in node.get_children():
		_index(c, out)


func _convert(json_path: String) -> void:
	var f := FileAccess.open(json_path, FileAccess.READ)
	if f == null:
		return
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		return
	var list: Array = doc.get("emitters", [])
	if list.is_empty():
		return
	var glb := json_path.trim_suffix(".fx.json") + ".glb"
	if not ResourceLoader.exists(glb):
		_skipped += 1
		return
	var packed_src := load(glb) as PackedScene
	if packed_src == null:
		_skipped += 1
		return
	var root := packed_src.instantiate()
	var by_name := {}
	_index(root, by_name)

	var touched := 0
	for e in list:
		var em: Dictionary = e.get("emitter", {})
		if em.is_empty():
			continue
		var node = by_name.get(String(e.get("node", "")))
		var mi := node as MeshInstance3D
		if em.has("emission_frequency"):
			if mi == null:
				continue
			var p := _build_emitter(String(e.get("node", "fx")), em, mi)
			p.draw_pass_1 = _draw_pass(em, mi)
			# The emitter mesh is the spawn surface, not artwork: stop drawing
			# it, but keep the node so animation tracks still find it.
			mi.mesh = null
			mi.set_meta("dso_emitter", true)
			mi.add_child(p)
			_emitters += 1
			touched += 1
		elif mi != null:
			_fix_materials(mi, String(em.get("type_name", "")))
			touched += 1

	if touched == 0:
		root.free()
		_skipped += 1
		return

	_own(root, root)
	var packed := PackedScene.new()
	if packed.pack(root) != OK:
		root.free()
		_skipped += 1
		return
	var err := ResourceSaver.save(packed, json_path.trim_suffix(".fx.json") + OUT_SUFFIX)
	root.free()
	if err == OK:
		_made += 1
	else:
		_skipped += 1


## Everything must be owned by the root or PackedScene.pack drops it.
func _own(node: Node, root: Node) -> void:
	if node != root:
		node.owner = root
	for c in node.get_children():
		_own(c, root)
