#!/usr/bin/env python3
"""Build a small Godot test project: 1 map, 1 mob, 1 equipped character, 4 FX.

Only the files actually needed are staged (the map's models, the textures they
really reference), which turns the import from hours into a couple of minutes.

    python3 make_minimal_project.py --project ~/DSOTest --godot <binary>
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GD_DIR = os.path.join(HERE, "godot")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
PROGRESS = re.compile(r"\[\s*(\d+)%\s*\]\s*(\S+)")

PROJECT_GODOT = '''config_version=5

[application]

config/name="DSO Test"
config/features=PackedStringArray("{gdver}", "Forward Plus")

[importer_defaults]

texture={{
"compress/mode": 2,
"detect_3d/compress_to": 0,
"mipmaps/generate": true
}}

[rendering]

textures/vram_compression/import_s3tc_bptc=true
'''


def log(m):
    print(f"[test] {m}", flush=True)


def glb_images(path):
    """URIs of a .glb's images, read from its JSON header alone."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return []
            f.seek(12)
            n = struct.unpack("<I", f.read(4))[0]
            f.seek(20)
            return [i["uri"] for i in json.loads(f.read(n)).get("images", [])
                    if "uri" in i]
    except Exception:
        return []


def run_godot(godot, project, *args, log_path=None, echo=(), watch=False):
    """Run Godot, streaming its output and showing ITS progress.

    Godot spends several minutes scanning before importing anything: without
    surfacing its percentage the script looks like it has hung.
    """
    cmd = [godot, "--headless", "--path", project, *args]
    t0 = time.time()
    kept, stop = [], threading.Event()
    state = {"pct": None, "phase": ""}

    def ticker():
        while not stop.wait(15.0):
            pct = f"{state['pct']:3}%" if state["pct"] is not None else "  ? "
            n = len(os.listdir(os.path.join(project, ".godot", "imported"))) \
                if os.path.isdir(os.path.join(project, ".godot", "imported")) else 0
            print(f"    ... {(time.time()-t0)/60:4.1f} min | {pct} "
                  f"{state['phase'][:32]:32} | {n} importes", flush=True)

    if watch:
        threading.Thread(target=ticker, daemon=True).start()

    lf = open(log_path, "a") if log_path else None
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            if lf:
                lf.write(line)
            st = ANSI.sub("", line).rstrip()
            m = PROGRESS.match(st.lstrip())
            if m:
                state["pct"], state["phase"] = int(m.group(1)), m.group(2)
                continue
            if any(st.startswith(k) for k in echo):
                kept.append(st)
                print("    " + st, flush=True)
            elif "ERROR" in st and "Blocking" not in st and len(kept) < 200:
                kept.append(st)
        p.wait()
        code = p.returncode
    finally:
        stop.set()
        if lf:
            lf.close()
    return code, kept, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--export", default="godot_export")
    ap.add_argument("--godot", required=True)
    ap.add_argument("--map", default="a0002_start_hub")
    ap.add_argument("--mob", default="characters/two_head_witch")
    ap.add_argument("--character", default="uniskel")
    # Warrior base body: the 14 pieces of the bare character, no armour.
    # (the 'animator_test_*' outfits are test stacks, not real characters)
    ap.add_argument("--outfit", default="warrior_male_body_00")
    ap.add_argument("--fx", nargs="*",
                    default=["effects/000_long_blue", "effects/000_long_red",
                             "effects/000_neck_2", "effects/000_head"])
    ap.add_argument("--map-instances", type=int, default=850)
    ap.add_argument("--copy", action="store_true",
                    help="copy instead of symlinking (default: symlinks)")
    a = ap.parse_args()

    export = os.path.abspath(a.export)
    project = os.path.abspath(a.project)
    godot = shutil.which(a.godot) or (a.godot if os.path.isfile(a.godot) else None)
    if godot is None:
        sys.exit(f"Godot not found: {a.godot}")
    ver = subprocess.run([godot, "--version"], capture_output=True,
                         text=True).stdout.strip()
    gdver = ".".join(ver.split(".")[:2])
    log(f"Godot {ver} -> config/features {gdver}")

    if os.path.exists(project):
        shutil.rmtree(project)
    os.makedirs(os.path.join(project, "tools"))
    os.makedirs(os.path.join(project, "dso"))

    # --- 1. Pick the models -------------------------------------------------
    wanted = set()          # chemins relatifs a l'export

    mob = a.mob + ".glb"
    if not os.path.isfile(os.path.join(export, mob)):
        sys.exit(f"mob not found: {mob}")
    wanted.add(mob)

    cdir = f"characters/{a.character}"
    anims = f"{cdir}/__animations.glb"
    if not os.path.isfile(os.path.join(export, anims)):
        sys.exit(f"character animations not found: {anims}")
    wanted.add(anims)
    with open(os.path.join(export, cdir, "outfits.json")) as f:
        odoc = json.load(f)
    outfit = next((o for o in odoc["outfits"] if o["name"] == a.outfit), None)
    if outfit is None:
        sys.exit(f"unknown outfit: {a.outfit}")
    parts = []
    for p in outfit["parts"]:
        rel = f"{cdir}/parts/{p}.glb"
        if os.path.isfile(os.path.join(export, rel)):
            wanted.add(rel)
            parts.append(p)
    log(f"outfit '{a.outfit}': {len(parts)}/{len(outfit['parts'])} parts")

    fx_models = []
    for fx in a.fx:
        rel = fx + ".glb"
        if os.path.isfile(os.path.join(export, rel)):
            wanted.add(rel)
            fx_models.append(fx)
    log(f"FX: {len(fx_models)}")

    map_rel = f"maps/{a.map}.map.json"
    with open(os.path.join(export, map_rel)) as f:
        mdoc = json.load(f)
    map_models = []
    for ref in mdoc["models"]:
        rel = ref.split(":")[-1] + ".glb"
        if os.path.isfile(os.path.join(export, rel)):
            wanted.add(rel)
            map_models.append(rel)
    log(f"map '{a.map}': {len(map_models)}/{len(mdoc['models'])} models, "
        f"{len(mdoc['instances'])} instances")

    # --- 2. Textures actually referenced ------------------------------------
    files = set(wanted)
    for rel in wanted:
        src = os.path.join(export, rel)
        for uri in glb_images(src):
            tex = os.path.normpath(os.path.join(os.path.dirname(src), uri))
            trel = os.path.relpath(tex, export)
            if not trel.startswith("..") and os.path.isfile(tex):
                files.add(trel)
    n_tex = sum(1 for f in files if f.endswith(".png"))
    log(f"{len(files)-n_tex} models + {n_tex} textures to stage")

    # --- 3. Stage them, preserving the relative tree ------------------------
    for rel in sorted(files):
        dst = os.path.join(project, "dso", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if a.copy:
            shutil.copy(os.path.join(export, rel), dst)
        else:
            os.symlink(os.path.join(export, rel), dst)
    # The .fx.json files describe the emitters; the manifest carries the map.
    for fx in fx_models:
        s = os.path.join(export, fx + ".fx.json")
        if os.path.isfile(s):
            shutil.copy(s, os.path.join(project, "dso", fx + ".fx.json"))
    os.makedirs(os.path.join(project, "dso", "maps"), exist_ok=True)
    shutil.copy(os.path.join(export, map_rel),
                os.path.join(project, "dso", map_rel))
    shutil.copy(os.path.join(export, cdir, "outfits.json"),
                os.path.join(project, "dso", cdir, "outfits.json"))
    vsrc = os.path.join(export, cdir, "variations.json")
    if os.path.isfile(vsrc):
        shutil.copy(vsrc, os.path.join(project, "dso", cdir, "variations.json"))

    for fn in os.listdir(GD_DIR):
        if fn.endswith(".gd"):
            shutil.copy(os.path.join(GD_DIR, fn),
                        os.path.join(project, "tools", fn))
    with open(os.path.join(project, "project.godot"), "w") as f:
        f.write(PROJECT_GODOT.format(gdver=gdver))

    # Parameters read by the scene script.
    with open(os.path.join(project, "test_config.json"), "w") as f:
        json.dump({"map": f"res://dso/{map_rel}", "map_instances": a.map_instances,
                   "mob": f"res://dso/{mob}", "character_dir": f"res://dso/{cdir}",
                   "outfit": a.outfit,
                   "variations": f"res://dso/{cdir}/variations.json",
                   "fx": [f"res://dso/{x}.fx.tscn" for x in fx_models]}, f, indent=1)
    log(f"project staged: {project}")

    # --- 4. Import ----------------------------------------------------------
    log("headless import (Godot scans first -- be patient, do not kill it)")
    code, errs, dt = run_godot(godot, project, "--import",
                               log_path=os.path.join(project, "import.log"),
                               watch=True)
    imported = len(os.listdir(os.path.join(project, ".godot", "imported"))) \
        if os.path.isdir(os.path.join(project, ".godot", "imported")) else 0
    log(f"import: {dt/60:.1f} min, {imported} files, exit {code}, "
        f"{len(errs)} errors")
    for l in errs[:6]:
        print("    " + l.strip(), flush=True)

    # --- 5. FX --------------------------------------------------------------
    log("converting FX")
    run_godot(godot, project, "--script", "res://tools/fx_to_scenes.gd",
              echo=("[fx]",))

    # --- 6. Test scene ------------------------------------------------------
    log("building the scene")
    run_godot(godot, project, "--script", "res://tools/make_test_scene.gd",
              echo=("[scene]",))
    if os.path.isfile(os.path.join(project, "test.tscn")):
        with open(os.path.join(project, "project.godot")) as f:
            pg = f.read()
        with open(os.path.join(project, "project.godot"), "w") as f:
            f.write(pg.replace('config/name="',
                               'run/main_scene="res://test.tscn"\nconfig/name="', 1))
        run_godot(godot, project, "--import")

    # --- 7. Verification ----------------------------------------------------
    log("verifying")
    run_godot(godot, project, "--script", "res://tools/verify_scene.gd",
              echo=("[verif]",))
    log("")
    log(f"READY: open {project} in Godot; test.tscn is the main scene.")


if __name__ == "__main__":
    main()
