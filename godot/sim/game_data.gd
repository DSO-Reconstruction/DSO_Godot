class_name DSOGameData
extends RefCounted
## Loads the JSON bundle produced by `export_gamedata.py`.
##
## The bundle already has the modifier language parsed into structured form,
## so nothing here parses text: the runtime only walks dictionaries.

var attributes: Dictionary = {}
var skills: Dictionary = {}
var effects: Dictionary = {}
var loaded := false


func load_bundle(path: String) -> bool:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("cannot open game data: %s" % path)
		return false
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("game data is not a JSON object: %s" % path)
		return false
	attributes = doc.get("attributes", {})
	skills = doc.get("skills", {})
	effects = doc.get("effects", {})
	loaded = true
	print("[gamedata] %d attributes, %d skills, %d effects" % [
		attributes.size(), skills.size(), effects.size()])
	return true


func skill(id: String) -> Dictionary:
	return skills.get(id, {})


func effect(id: String) -> Dictionary:
	return effects.get(id, {})


func skills_of(char_class: String) -> Array[String]:
	var out: Array[String] = []
	for id in skills.keys():
		if String(skills[id].get("char_class", "")) == char_class:
			out.append(id)
	out.sort()
	return out


## Resolve one serialised argument to a number, substituting `$n` parameters.
##   {"n": 1.5} -> 1.5      {"p": "0"} -> params["0"]      {"i": "Fire"} -> 0.0
static func number(arg: Variant, params: Dictionary = {}) -> float:
	if typeof(arg) != TYPE_DICTIONARY:
		return 0.0
	if arg.has("n"):
		return float(arg["n"])
	if arg.has("p"):
		var v = params.get(String(arg["p"]))
		if typeof(v) == TYPE_DICTIONARY:
			return number(v, {})
		return float(v) if v != null else 0.0
	return 0.0


static func ident(arg: Variant) -> String:
	if typeof(arg) != TYPE_DICTIONARY:
		return ""
	return String(arg.get("i", ""))


## Mode carried by the first argument that has one; absolute by default.
static func mode_of(mod: Dictionary) -> String:
	for a in mod.get("args", []):
		if typeof(a) == TYPE_DICTIONARY and a.has("mode"):
			return String(a["mode"])
	return "absolute"
