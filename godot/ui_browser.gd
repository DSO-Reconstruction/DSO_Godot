extends Control
## Flip through every exported DSO window.
##
## Put this on the root Control of a scene and run it: left/right (or the
## dropdown) switches window, `L` cycles the language, `V` reveals the
## alternative layouts the exporter left hidden.
##
##   ui_dir : where <window>.tscn live, e.g. res://dso/ui

@export_dir var ui_dir: String = "res://dso/ui"

var _windows: PackedStringArray = []
var _index := 0
var _current: Node = null
var _loca: Dictionary = {}
var _langs: PackedStringArray = []
var _lang := 0
var _reveal := false

@onready var _picker := OptionButton.new()
@onready var _title := Label.new()


func _ready() -> void:
	_scan()
	_scan_loca()
	var bar := HBoxContainer.new()
	bar.set_anchors_preset(Control.PRESET_TOP_WIDE)
	bar.add_child(_picker)
	bar.add_child(_title)
	add_child(bar)
	for w in _windows:
		_picker.add_item(w)
	_picker.item_selected.connect(func(i: int) -> void: _index = i; _show())
	_show()


func _scan() -> void:
	var d := DirAccess.open(ui_dir)
	if d == null:
		push_error("ui_browser: cannot open %s" % ui_dir)
		return
	for f in d.get_files():
		if f.ends_with(".tscn"):
			_windows.append(f.get_basename())
	_windows.sort()


func _scan_loca() -> void:
	var d := DirAccess.open(ui_dir.path_join("loca"))
	if d == null:
		return
	for f in d.get_files():
		if f.ends_with(".json"):
			_langs.append(f.get_basename())
	_langs.sort()


func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventKey) or not event.is_pressed():
		return
	match (event as InputEventKey).keycode:
		KEY_RIGHT, KEY_SPACE:
			_index = (_index + 1) % _windows.size()
		KEY_LEFT:
			_index = (_index - 1 + _windows.size()) % _windows.size()
		KEY_V:
			_reveal = not _reveal
		KEY_L:
			if not _langs.is_empty():
				_lang = (_lang + 1) % _langs.size()
				_loca = {}
		_:
			return
	_picker.select(_index)
	_show()


func _show() -> void:
	if _current != null:
		_current.queue_free()
		_current = null
	if _windows.is_empty():
		return
	var name := _windows[_index]
	var ps := load(ui_dir.path_join(name + ".tscn")) as PackedScene
	if ps == null:
		return
	_current = ps.instantiate()
	add_child(_current)
	move_child(_current, 0)
	if _reveal:
		_reveal_all(_current)
	if not _langs.is_empty():
		_translate(_current)
	_title.text = "  %s  (%d/%d)%s" % [name, _index + 1, _windows.size(),
		("  [%s]" % _langs[_lang]) if not _langs.is_empty() else ""]


func _reveal_all(n: Node) -> void:
	if n is CanvasItem:
		(n as CanvasItem).visible = true
	for c in n.get_children():
		_reveal_all(c)


func _translate(n: Node) -> void:
	if _loca.is_empty():
		var f := FileAccess.open(ui_dir.path_join("loca/%s.json" % _langs[_lang]),
			FileAccess.READ)
		if f == null:
			return
		var parsed = JSON.parse_string(f.get_as_text())
		if typeof(parsed) != TYPE_DICTIONARY:
			return
		_loca = parsed
	_apply(n)


func _apply(n: Node) -> void:
	if n is Label and n.has_meta("dso_loca"):
		var key := String(n.get_meta("dso_loca"))
		if _loca.has(key):
			(n as Label).text = String(_loca[key])
	for c in n.get_children():
		_apply(c)
