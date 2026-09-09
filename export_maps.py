#!/usr/bin/env python3
"""Export .map files as JSON placement manifests.

Each map becomes a JSON file listing its instances (model + position +
quaternion + scale). The Godot script 'build_map.gd' rebuilds the scene by
instancing the already-exported .glb files, so geometry is not duplicated
once per map and the maps stay editable inside Godot.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsoexp import dsomap


def convert(path, out_dir, model_dir_check=None):
    m = dsomap.load(path)
    name = os.path.splitext(os.path.basename(path))[0]

    models, model_index = [], {}
    missing = set()
    instances = []
    for inst in m.instances:
        ref = m.model_of(inst)
        if not ref:
            continue                                  # instance with no graphics
        if ref not in model_index:
            if model_dir_check and not os.path.isfile(
                    os.path.join(model_dir_check, ref.split(":", 1)[-1] + ".n3")):
                missing.add(ref)
            model_index[ref] = len(models)
            models.append(ref)
        instances.append({
            "m": model_index[ref],
            "p": [round(v, 5) for v in inst.pos[:3]],
            "r": [round(v, 6) for v in inst.rot[:4]],
            "s": [round(v, 5) for v in inst.scale[:3]],
            "n": m.string(inst.name_index),
            "g": inst.group_index,
            "col": inst.use_collide,
        })

    doc = {
        "map": name,
        "size": list(m.size),
        "center": [round(v, 4) for v in m.center[:3]],
        "extents": [round(v, 4) for v in m.extents[:3]],
        "models": models,
        "groups": [{"name": m.string(g.name_id), "type": g.type, "parent": g.parent}
                   for g in m.groups],
        "instances": instances,
        "nav_blockers": [[[round(x, 3), round(y, 3)] for x, y in poly]
                         for poly in m.nav_blockers],
    }
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name + ".map.json")
    with open(out, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    return {"map": name, "instances": len(instances), "models": len(models),
            "missing": sorted(missing), "out": out,
            "bytes": os.path.getsize(out)}


GODOT_SCRIPT = '''@tool
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
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="extracted/export_win32")
    ap.add_argument("--out", default="godot_export/maps")
    a = ap.parse_args()

    maps_dir = os.path.join(a.root, "maps")
    models_dir = os.path.join(a.root, "models")
    files = sorted(f for f in os.listdir(maps_dir) if f.endswith(".map"))
    print(f"[maps] {len(files)} maps -> {a.out}", flush=True)

    total_inst = total_bytes = 0
    all_missing = set()
    failures = []
    for i, fn in enumerate(files, 1):
        try:
            r = convert(os.path.join(maps_dir, fn), a.out, models_dir)
        except Exception as e:
            failures.append((fn, f"{type(e).__name__}: {e}"))
            continue
        total_inst += r["instances"]
        total_bytes += r["bytes"]
        all_missing |= set(r["missing"])
        if i % 100 == 0 or i == len(files):
            print(f"[maps] {i}/{len(files)}", flush=True)

    script = os.path.join(a.out, "build_map.gd")
    with open(script, "w") as f:
        f.write(GODOT_SCRIPT)

    print(f"\n[maps] {len(files)-len(failures)} maps converted, "
          f"{total_inst} instances, {total_bytes/1048576:.1f} MB of manifests")
    print(f"[maps] Godot script -> {script}")
    if all_missing:
        print(f"[maps] {len(all_missing)} models referenced but missing, e.g.:")
        for r in sorted(all_missing)[:8]:
            print(f"    {r}")
    for fn, err in failures[:8]:
        print(f"[maps] FAILED {fn}: {err}")


if __name__ == "__main__":
    main()
