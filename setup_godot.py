#!/usr/bin/env python3
"""Set up a complete Godot 4 project from a DSO export.

Does the whole thing in one go: project tree, import settings, staging of the
assets (copy or symlink), tool scripts, headless import, FX conversion to
GPUParticles3D, a demo scene, and a final verification pass.

    python3 setup_godot.py --project ~/DSOGodot --godot /path/to/godot

By default only the useful folders are staged; --stage all takes everything
(expect hours for ~15,600 models and ~21,000 textures).
"""
import argparse
import json
import os
import shutil
import re
import subprocess
import sys
import threading

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Godot prints its own progress: "[  61% ] _update_scan_actions | file"
PROGRESS = re.compile(r"\[\s*(\d+)%\s*\]\s*(\S+)")
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GD_DIR = os.path.join(HERE, "godot")

# Import settings applied project-wide.
#  compress/mode 2         = VRAM compression, right for 3D
#  detect_3d/compress_to 0 = disable re-detection. Otherwise Godot imports
#    every texture losslessly as 2D, then reimports it the moment it is used
#    in 3D -- two full passes over 21,000 files.
PROJECT_GODOT = '''config_version=5

[application]

config/name="{name}"
config/features=PackedStringArray("{gdver}", "Forward Plus")

[importer_defaults]

texture={{
"compress/mode": 2,
"detect_3d/compress_to": 0,
"mipmaps/generate": true
}}

[rendering]

textures/vram_compression/import_s3tc_bptc=true
lights_and_shadows/directional_shadow/soft_shadow_filter_quality=2
'''

# Heavy folders that are rarely useful to start with.
HEAVY = ["ui", "picking", "dummies", "badname"]


def log(msg):
    print(f"[setup] {msg}", flush=True)


def run_godot(godot, project, *args, log_path=None, echo=(), progress_dir=None):
    """Run Godot, streaming its output instead of buffering it in memory.

    Importing thousands of models produces an enormous log. Buffering it in
    RAM and only printing at the end left the script silent for hours, which
    looks exactly like a hang. We write everything to a file and echo the
    lines that matter as they arrive, plus a progress counter.
    """
    cmd = [godot, "--headless", "--path", project, *args]
    t0 = time.time()
    kept = []
    stop = threading.Event()
    state = {"pct": None, "phase": ""}

    def ticker():
        while not stop.wait(20.0):
            el = time.time() - t0
            n = _imported_count(progress_dir) if progress_dir else 0
            # The scan phase produces no files for several minutes: without
            # Godot's own percentage the script looks stuck.
            pct = f"{state['pct']:3}%" if state["pct"] is not None else "  ? "
            print(f"   ... {el/60:5.1f} min | {pct} {state['phase'][:34]:34} "
                  f"| {n} importes", flush=True)

    th = threading.Thread(target=ticker, daemon=True)
    if progress_dir is not None:
        th.start()

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
                print("   " + st, flush=True)
            elif "ERROR" in st and "Blocking" not in st and len(kept) < 4000:
                kept.append(st)
        p.wait()
        code = p.returncode
    finally:
        stop.set()
        if lf:
            lf.close()
    return code, kept, time.time() - t0


def _imported_count(project):
    d = os.path.join(project, ".godot", "imported")
    try:
        return len(os.listdir(d))
    except OSError:
        return 0


def stage_dirs(export, stage):
    """Model folders to stage into the project, per the requested stage."""
    tops = sorted(d for d in os.listdir(export)
                  if os.path.isdir(os.path.join(export, d))
                  and d != "textures")
    if stage == "all":
        return tops
    if stage == "characters":
        return [d for d in tops if d in ("characters", "maps")]
    return [d for d in tops if d not in HEAVY]        # 'light'


def glb_images(path):
    """URIs of the images a .glb references (reads the JSON header only)."""
    try:
        with open(path, "rb") as f:
            head = f.read(28)
            if head[:4] != b"glTF":
                return []
            n = struct.unpack_from("<I", head, 12)[0]
            f.seek(20)
            doc = json.loads(f.read(n))
        return [i["uri"] for i in doc.get("images", []) if "uri" in i]
    except Exception:
        return []


def needed_texture_dirs(export, model_dirs):
    """Texture subfolders these models actually reference.

    Characters only use 28% of the textures, and 85 of the 125 subfolders are
    of no use to them at all. Not staging those saves Godot from scanning and
    importing them.
    """
    texroot = os.path.join(export, "textures")
    needed = set()
    for d in model_dirs:
        for r, _, fs in os.walk(os.path.join(export, d)):
            for fn in fs:
                if not fn.endswith(".glb"):
                    continue
                for uri in glb_images(os.path.join(r, fn)):
                    p = os.path.normpath(os.path.join(r, uri))
                    rel = os.path.relpath(p, texroot)
                    if not rel.startswith(".."):
                        needed.add(rel.split(os.sep)[0])
    return needed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="Godot project folder to create")
    ap.add_argument("--export", default="godot_export")
    ap.add_argument("--godot", default="godot", help="Godot 4.x binary")
    ap.add_argument("--name", default="DSO Assets")
    ap.add_argument("--stage", choices=["light", "characters", "all"], default="light",
                    help="what to stage (default: light)")
    ap.add_argument("--link", action="store_true",
                    help="symlink instead of copying (saves several GB)")
    ap.add_argument("--no-import", action="store_true", help="skip the import step")
    ap.add_argument("--no-fx", action="store_true", help="skip the FX conversion")
    ap.add_argument("--no-demo", action="store_true", help="skip creating demo.tscn")
    ap.add_argument("--force", action="store_true", help="overwrite an existing project")
    ap.add_argument("--resume", action="store_true",
                    help="project already staged: resume without re-copying assets")
    a = ap.parse_args()

    export = os.path.abspath(a.export)
    project = os.path.abspath(a.project)
    if not os.path.isdir(export):
        sys.exit(f"export not found: {export}")
    if os.path.exists(project) and not a.resume:
        if not a.force:
            sys.exit(f"{project} already exists "
                     "(--resume to continue, --force to start over)")
        shutil.rmtree(project)

    # Verifier Godot avant de copier des Go pour rien.
    godot = shutil.which(a.godot) or (a.godot if os.path.isfile(a.godot) else None)
    if godot is None:
        sys.exit(f"Godot not found: {a.godot}\n"
                 "  pass --godot /path/to/Godot_v4.x_linux.x86_64")
    ver = subprocess.run([godot, "--version"], capture_output=True, text=True).stdout.strip()
    log(f"Godot : {ver}")
    if not ver.startswith("4."):
        sys.exit("this tooling requires Godot 4.x")
    # The version must appear in config/features, otherwise Godot offers to
    # convert the project and the headless import bails out.
    gdver = ".".join(ver.split(".")[:2])
    log(f"config/features: {gdver}")

    # 1. Project skeleton
    os.makedirs(os.path.join(project, "tools"), exist_ok=True)
    with open(os.path.join(project, "project.godot"), "w") as f:
        f.write(PROJECT_GODOT.format(name=a.name, gdver=gdver))
    log(f"project created: {project}")

    # 2. Assets: stage only the requested scope.
    dst = os.path.join(project, "dso")
    os.makedirs(dst, exist_ok=True)
    model_dirs = stage_dirs(export, a.stage)
    texdirs = needed_texture_dirs(export, model_dirs)
    log(f"stage '{a.stage}': {len(model_dirs)} model folders, "
        f"{len(texdirs)} texture subfolders")

    def place(src, target):
        if a.link:
            os.symlink(src, target)
        elif os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copy(src, target)

    for d in model_dirs:
        place(os.path.join(export, d), os.path.join(dst, d))
    os.makedirs(os.path.join(dst, "textures"), exist_ok=True)
    for d in sorted(texdirs):
        src = os.path.join(export, "textures", d)
        if os.path.exists(src):
            place(src, os.path.join(dst, "textures", d))
    n_tex = sum(1 for r, _, fs in os.walk(os.path.join(dst, "textures"))
                for f in fs if f.endswith(".png"))
    log(f"assets {'linked' if a.link else 'copied'}: "
        f"{sum(1 for r, _, fs in os.walk(dst) for f in fs if f.endswith('.glb'))} glb, "
        f"{n_tex} textures")
    log("   out-of-scope folders are not staged at all, so Godot never")
    log("   scans them. Re-run with --stage all to get everything.")

    # 3. Tool scripts
    for fn in sorted(os.listdir(GD_DIR)):
        if fn.endswith(".gd"):
            shutil.copy(os.path.join(GD_DIR, fn), os.path.join(project, "tools", fn))
    log("tool scripts installed in tools/")

    # 5. Import
    if not a.no_import:
        logf = os.path.join(project, "import.log")
        log(f"headless import running -- full log: {logf}")
        log("  (re-run with --resume to continue where it stopped)")
        code, errs, dt = run_godot(godot, project, "--import",
                                   log_path=logf, progress_dir=project)
        n = _imported_count(project)
        log(f"import termine en {dt/60:.1f} min : {n} fichiers, "
            f"code {code}, {len(errs)} erreurs")
        for l in errs[:8]:
            print("   " + l.strip(), flush=True)
        if n == 0:
            log("NO file imported -- see the log above")

    # 6. FX -> GPUParticles3D
    if not a.no_fx:
        log("converting FX to GPUParticles3D...")
        code, kept, dt = run_godot(godot, project,
                                   "--script", "res://tools/fx_to_scenes.gd",
                                   log_path=os.path.join(project, "fx.log"),
                                   echo=("[fx]",))

    # 7. Scene de demonstration
    if not a.no_demo:
        log("building demo.tscn...")
        code, kept, dt = run_godot(godot, project,
                                   "--script", "res://tools/make_demo.gd",
                                   echo=("[demo]",))
        # main_scene is only set once the scene really exists, otherwise
        # Godot complains about a missing main scene.
        if os.path.isfile(os.path.join(project, "demo.tscn")):
            with open(os.path.join(project, "project.godot"), "r") as f:
                pg = f.read()
            pg = pg.replace('config/name="', 'run/main_scene="res://demo.tscn"\nconfig/name="', 1)
            with open(os.path.join(project, "project.godot"), "w") as f:
                f.write(pg)
            run_godot(godot, project, "--import")

    # 8. Verification
    log("verifying the import...")
    code, kept, dt = run_godot(godot, project,
                               "--script", "res://tools/verify_import.gd",
                               echo=("[verif]", "  "))

    log("")
    log(f"READY: open {project} in Godot; demo.tscn is the main scene.")
    log("  maps     : Node3D + tools/build_map.gd (see docs/IMPORT_GODOT.md)")
    log("  outfits  : Node3D + tools/build_outfit.gd")
    log("  FX       : the <model>.fx.tscn files next to the .glb")


if __name__ == "__main__":
    main()
