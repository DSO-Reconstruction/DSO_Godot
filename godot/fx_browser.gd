extends Node3D
## Flip through the rebuilt effect scenes.
##
## Put this on the root Node3D of a scene and run it.
##
##   left / right or space   previous / next effect
##   drag left mouse         orbit           wheel  zoom
##   R                       restart the emitters
##   G                       ground grid and 1.8 m scale reference
##   B                       cycle the background (dark / grey / white)
##   S                       toggle slow motion, useful for one-shots
##
##   fx_dir : where the <model>.fx.tscn live, e.g. res://dso/effects

@export_dir var fx_dir: String = "res://dso/effects"
@export var start_index: int = 0

const BACKGROUNDS: Array[Color] = [
	Color(0.01, 0.01, 0.015), Color(0.12, 0.12, 0.14), Color(0.8, 0.8, 0.82)]

var _files: PackedStringArray = []
var _index := 0
var _current: Node3D = null

var _yaw := 0.6
var _pitch := 0.35
var _dist := 6.0
var _target := Vector3(0, 1.0, 0)
var _dragging := false
var _bg := 0
var _slow := false

var _camera: Camera3D
var _env: Environment
var _helpers: Node3D
var _picker: OptionButton
var _title: Label


func _ready() -> void:
	_scan()
	_build_stage()
	_build_ui()
	_index = clampi(start_index, 0, maxi(_files.size() - 1, 0))
	_picker.select(_index)
	_show()


func _scan() -> void:
	var dirs: Array[String] = [fx_dir]
	while not dirs.is_empty():
		var d0: String = dirs.pop_back()
		var d := DirAccess.open(d0)
		if d == null:
			continue
		for sub in d.get_directories():
			dirs.append(d0.path_join(sub))
		for f in d.get_files():
			if f.ends_with(".fx.tscn"):
				_files.append(d0.path_join(f))
	_files.sort()
	if _files.is_empty():
		push_error("fx_browser: no .fx.tscn under %s -- run fx_to_scenes.gd first" % fx_dir)


func _build_stage() -> void:
	_camera = Camera3D.new()
	_camera.current = true
	_camera.far = 500.0
	add_child(_camera)

	var we := WorldEnvironment.new()
	_env = Environment.new()
	_env.background_mode = Environment.BG_COLOR
	_env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	_env.ambient_light_color = Color(0.55, 0.55, 0.6)
	_env.ambient_light_energy = 1.0
	# Effects are mostly additive: a little bloom is what sells them.
	_env.glow_enabled = true
	_env.glow_intensity = 0.6
	_env.glow_bloom = 0.1
	we.environment = _env
	add_child(we)
	_apply_background()

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, -35, 0)
	sun.light_energy = 0.8
	add_child(sun)

	_helpers = Node3D.new()
	add_child(_helpers)
	_helpers.add_child(_ground())
	_helpers.add_child(_grid())
	_helpers.add_child(_scale_ref())


## An opaque floor. Refraction and decal passes read the *back buffer*, which
## only holds opaque geometry: with nothing solid in the scene they sample
## black and paint a black hole where the distortion should be. A real map has
## a floor; the viewer needs one too.
func _ground() -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(24, 24)
	mi.mesh = plane
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(0.09, 0.09, 0.11)
	m.roughness = 1.0
	mi.material_override = m
	mi.position.y = -0.01
	return mi


## A 10 x 10 m grid on the floor, one line per metre.
func _grid() -> MeshInstance3D:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_LINES)
	for i in range(-5, 6):
		st.add_vertex(Vector3(i, 0, -5))
		st.add_vertex(Vector3(i, 0, 5))
		st.add_vertex(Vector3(-5, 0, i))
		st.add_vertex(Vector3(5, 0, i))
	var mi := MeshInstance3D.new()
	mi.mesh = st.commit()
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.albedo_color = Color(0.28, 0.28, 0.34)
	mi.material_override = m
	return mi


## A 1.8 m capsule: effects are authored around a character.
func _scale_ref() -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var caps := CapsuleMesh.new()
	caps.radius = 0.3
	caps.height = 1.8
	mi.mesh = caps
	mi.position = Vector3(0, 0.9, 0)
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(0.22, 0.22, 0.26)
	m.roughness = 0.9
	mi.material_override = m
	return mi


func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var bar := HBoxContainer.new()
	bar.set_anchors_preset(Control.PRESET_TOP_WIDE)
	layer.add_child(bar)
	_picker = OptionButton.new()
	for f in _files:
		_picker.add_item(f.get_file().trim_suffix(".fx.tscn"))
	_picker.item_selected.connect(func(i: int) -> void: _index = i; _show())
	bar.add_child(_picker)
	_title = Label.new()
	bar.add_child(_title)


# ------------------------------------------------------------------ input

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		match mb.button_index:
			MOUSE_BUTTON_LEFT:
				_dragging = mb.pressed
			MOUSE_BUTTON_WHEEL_UP:
				_dist = maxf(_dist * 0.9, 0.3)
			MOUSE_BUTTON_WHEEL_DOWN:
				_dist = minf(_dist * 1.1, 200.0)
		_place_camera()
		return
	if event is InputEventMouseMotion and _dragging:
		var mm := event as InputEventMouseMotion
		_yaw -= mm.relative.x * 0.008
		_pitch = clampf(_pitch + mm.relative.y * 0.008, -1.4, 1.4)
		_place_camera()
		return
	if not (event is InputEventKey) or not event.is_pressed():
		return
	match (event as InputEventKey).keycode:
		KEY_RIGHT, KEY_SPACE:
			_index = (_index + 1) % maxi(_files.size(), 1)
			_picker.select(_index)
			_show()
		KEY_LEFT:
			_index = (_index - 1 + _files.size()) % maxi(_files.size(), 1)
			_picker.select(_index)
			_show()
		KEY_R:
			_restart(_current)
		KEY_G:
			_helpers.visible = not _helpers.visible
		KEY_B:
			_bg = (_bg + 1) % BACKGROUNDS.size()
			_apply_background()
		KEY_S:
			_slow = not _slow
			Engine.time_scale = 0.2 if _slow else 1.0
			_status()


func _apply_background() -> void:
	_env.background_color = BACKGROUNDS[_bg]


func _place_camera() -> void:
	var dir := Vector3(
		cos(_pitch) * sin(_yaw), sin(_pitch), cos(_pitch) * cos(_yaw))
	_camera.position = _target + dir * _dist
	_camera.look_at(_target, Vector3.UP)


# ------------------------------------------------------------------ scenes

func _show() -> void:
	if _current != null:
		_current.queue_free()
		_current = null
	if _files.is_empty():
		return
	var ps := load(_files[_index]) as PackedScene
	if ps == null:
		return
	_current = ps.instantiate()
	add_child(_current)
	_frame(_current)
	_play(_current)
	_status()


func _status() -> void:
	var n := 0
	if _current != null:
		n = _count_emitters(_current)
	_title.text = "  %s   (%d/%d)   %d emitters%s" % [
		_files[_index].get_file().trim_suffix(".fx.tscn"),
		_index + 1, _files.size(), n, "   [slow]" if _slow else ""]


func _count_emitters(n: Node) -> int:
	var c := 1 if n is GPUParticles3D else 0
	for ch in n.get_children():
		c += _count_emitters(ch)
	return c


## Point the camera at whatever the effect actually occupies.
##
## A GPUParticles3D reports its *visibility* AABB, which is deliberately
## generous, so framing on that pushes the camera kilometres back. Emitter
## origins plus a metre of padding is what actually reads.
func _frame(root: Node3D) -> void:
	var box := AABB(Vector3(0, 0.9, 0), Vector3.ZERO)      # the scale capsule
	for n in _all(root):
		if not (n is Node3D):
			continue
		var p := (n as Node3D).global_position
		var b := AABB(p, Vector3.ZERO)
		if n is MeshInstance3D and (n as MeshInstance3D).mesh != null:
			b = (n as MeshInstance3D).get_aabb()
			b.position += p
		elif not (n is GPUParticles3D):
			continue
		box = box.merge(b)
	box = box.grow(1.0)
	_target = box.position + box.size * 0.5
	_dist = clampf(box.size.length() * 1.1, 2.5, 40.0)
	_place_camera()


func _all(n: Node, out: Array = []) -> Array:
	out.append(n)
	for c in n.get_children():
		_all(c, out)
	return out


## Emitters and animations both need a kick: an imported AnimationPlayer does
## not autoplay, and one-shot particle systems have already finished.
func _play(root: Node) -> void:
	for n in _all(root):
		if n is AnimationPlayer:
			var ap := n as AnimationPlayer
			var names := ap.get_animation_list()
			if names.size() > 0:
				ap.play(names[0])
		elif n is GPUParticles3D:
			var p := n as GPUParticles3D
			p.emitting = true
			p.restart()


func _restart(root: Node) -> void:
	if root == null:
		return
	_play(root)
