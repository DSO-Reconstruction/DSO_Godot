#!/usr/bin/env python3
"""Build a Godot project for looking at the effects, and nothing else.

Stages a selection of `<model>.glb` + `<model>.fx.json` with the textures they
actually reference, imports them, runs `fx_to_scenes.gd` to rebuild the
emitters and the blend modes, and drops in a browser scene.

    python3 make_fx_project.py --project ~/DSOFX --godot <binary>
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_minimal_project import glb_images, run_godot          # noqa: E402

GD_DIR = os.path.join(HERE, "godot")
# Small, dense, and the ones anyone actually wants to see first.
DEFAULT_DIRS = ["effects_playerskills", "effects_weapons", "effects_dwarf",
                "effects"]

PROJECT_GODOT = '''config_version=5

[application]

config/name="DSO FX"
config/features=PackedStringArray("{gdver}", "Forward Plus")
run/main_scene="res://fx_browser.tscn"

[display]

window/size/viewport_width=1280
window/size/viewport_height=800

[importer_defaults]

texture={{
"compress/mode": 2,
"detect_3d/compress_to": 0,
"mipmaps/generate": true
}}

[rendering]

textures/vram_compression/import_s3tc_bptc=true
'''

BROWSER_TSCN = '''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://tools/fx_browser.gd" id="1_b"]

[node name="FX" type="Node3D"]
script = ExtResource("1_b")
fx_dir = "res://dso"
'''


def log(m):
    print("[fx-proj] %s" % m, flush=True)


def emitter_count(fx_json):
    """How many real particle emitters a model has (not just blend states)."""
    try:
        with open(fx_json) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return 0
    return sum(1 for e in doc.get("emitters", [])
               if "emission_frequency" in e.get("emitter", {}))


def collect(export, dirs, count):
    """Pick the models to stage: the small folders whole, `effects` sampled."""
    picked, pool = [], []
    for d in dirs:
        full = os.path.join(export, d)
        if not os.path.isdir(full):
            continue
        here = []
        for name in sorted(os.listdir(full)):
            if not name.endswith(".fx.json"):
                continue
            base = name[:-len(".fx.json")]
            glb = os.path.join(full, base + ".glb")
            if not os.path.isfile(glb):
                continue
            if emitter_count(os.path.join(full, name)) == 0:
                continue
            here.append((d, base))
        # The last folder is the big one: sample it, keep the others whole.
        if d == dirs[-1] and len(dirs) > 1:
            pool = here
        else:
            picked.extend(here)
    if not count:
        picked.extend(pool)
        return picked
    room = max(count - len(picked), 0)
    if pool and room:
        step = max(1, len(pool) // room)
        picked.extend(pool[::step][:room])
    return picked[:count]


def stage(export, project, models, copy):
    """Link (or copy) the .glb, its sidecar and every texture it references."""
    done, textures = 0, 0
    seen = set()
    for sub, base in models:
        src_dir = os.path.join(export, sub)
        dst_dir = os.path.join(project, "dso", sub)
        os.makedirs(dst_dir, exist_ok=True)
        for ext in (".glb", ".fx.json"):
            src, dst = os.path.join(src_dir, base + ext), os.path.join(dst_dir, base + ext)
            if not os.path.isfile(src) or os.path.exists(dst):
                continue
            (shutil.copy2 if copy else os.symlink)(src, dst)
        done += 1
        # Texture URIs in the .glb are relative to the .glb itself.
        for uri in glb_images(os.path.join(src_dir, base + ".glb")):
            src = os.path.normpath(os.path.join(src_dir, uri))
            if src in seen or not os.path.isfile(src):
                continue
            seen.add(src)
            rel = os.path.relpath(src, export)
            dst = os.path.join(project, "dso", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst):
                (shutil.copy2 if copy else os.symlink)(src, dst)
                textures += 1
    return done, textures


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--export", default="godot_export")
    ap.add_argument("--godot", required=True)
    ap.add_argument("--dirs", nargs="*", default=DEFAULT_DIRS,
                    help="folders to take effects from; the last one is sampled")
    ap.add_argument("--count", type=int, default=600,
                    help="how many models to stage (0 = every one)")
    ap.add_argument("--copy", action="store_true",
                    help="copy instead of symlinking (default: symlinks)")
    a = ap.parse_args()

    export = os.path.abspath(a.export)
    project = os.path.abspath(a.project)
    godot = shutil.which(a.godot) or (a.godot if os.path.isfile(a.godot) else None)
    if godot is None:
        sys.exit("Godot not found: %s" % a.godot)
    gdver = ".".join(subprocess.run([godot, "--version"], capture_output=True,
                                    text=True).stdout.strip().split(".")[:2])

    models = collect(export, a.dirs, a.count)
    if not models:
        sys.exit("no effects with emitters found under %s" % export)
    log("%d effect models selected" % len(models))

    os.makedirs(os.path.join(project, "tools"), exist_ok=True)
    n, t = stage(export, project, models, a.copy)
    log("%d models, %d textures staged" % (n, t))

    for name in ("fx_to_scenes.gd", "fx_browser.gd"):
        shutil.copy2(os.path.join(GD_DIR, name), os.path.join(project, "tools", name))
    with open(os.path.join(project, "project.godot"), "w") as f:
        f.write(PROJECT_GODOT.format(gdver=gdver))
    with open(os.path.join(project, "fx_browser.tscn"), "w") as f:
        f.write(BROWSER_TSCN)

    log("importing (this is the slow part)")
    code, msgs, secs = run_godot(godot, project, "--import",
                                 log_path=os.path.join(project, "import.log"),
                                 watch=True)
    log("import finished in %.1f min (exit %d)" % (secs / 60.0, code))

    log("rebuilding the emitters")
    code, msgs, secs = run_godot(godot, project, "--script",
                                 "res://tools/fx_to_scenes.gd", echo=("[fx]",))
    log("done in %.1f min" % (secs / 60.0))
    log("open %s and press F5" % project)


if __name__ == "__main__":
    main()
