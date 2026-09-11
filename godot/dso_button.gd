extends Control
## Runtime behaviour for a DSO button widget.
##
## The exported scenes keep every visual state the client shipped as a child
## Control named `normal`, `pressed`, `mouseover`, `mouseoverpressed` or
## `disabled`. This swaps between them and re-emits the widget's own event
## name, which is the string the game's UI code listened for.

signal dso_pressed(event_name: String)

@export var dso_event: String = ""
@export var disabled: bool = false:
	set(v):
		disabled = v
		_refresh()

var _states: Dictionary = {}
var _hover := false
var _down := false


func _ready() -> void:
	for c in get_children():
		if c is Control and c.name in ["normal", "pressed", "mouseover",
				"mouseoverpressed", "disabled"]:
			_states[String(c.name)] = c
	if dso_event.is_empty() and has_meta("dso_event"):
		dso_event = String(get_meta("dso_event"))
	mouse_entered.connect(_on_enter)
	mouse_exited.connect(_on_exit)
	_refresh()


func _on_enter() -> void:
	_hover = true
	_refresh()


func _on_exit() -> void:
	_hover = false
	_down = false
	_refresh()


func _gui_input(event: InputEvent) -> void:
	if disabled or not (event is InputEventMouseButton):
		return
	var mb := event as InputEventMouseButton
	if mb.button_index != MOUSE_BUTTON_LEFT:
		return
	if mb.pressed:
		_down = true
		_refresh()
	elif _down:
		_down = false
		_refresh()
		dso_pressed.emit(dso_event)


## First state present, in order of preference.
func _pick() -> String:
	var want: Array[String] = []
	if disabled:
		want = ["disabled", "normal"]
	elif _down and _hover:
		want = ["mouseoverpressed", "pressed", "mouseover", "normal"]
	elif _down:
		want = ["pressed", "normal"]
	elif _hover:
		want = ["mouseover", "normal"]
	else:
		want = ["normal"]
	for w in want:
		if _states.has(w):
			return w
	return ""


func _refresh() -> void:
	var on := _pick()
	for name in _states:
		(_states[name] as Control).visible = name == on
