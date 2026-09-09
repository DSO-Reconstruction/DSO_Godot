#!/usr/bin/env python3
"""Split a player character (uniskel*) into reusable parts.

A single .n3 holds thousands of equipment pieces and hundreds of named
outfits, so exporting it in one go yields a .glb of several hundred MB.
Instead this produces:

  <name>/__animations.glb   skeleton + every clip, no geometry
  <name>/parts/<part>.glb   skeleton + a single part, no animation
  <name>/outfits.json       the outfits: name -> list of parts
  <name>/variations.json    body-shape variations, per joint

No geometry is duplicated, and the parts share the same bone names as the
animation file, so Godot can drive them all with one AnimationLibrary.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsoexp import n3 as n3mod
from export_model import Exporter, CHARACTER_NODE

ROOT = OUT = SRC = None


def _init(root, out, src):
    global ROOT, OUT, SRC
    ROOT, OUT, SRC = root, out, src


def _part(name):
    ex = Exporter(ROOT, OUT)
    dst = os.path.join(OUT, "parts", name + ".glb")
    try:
        r = ex.export(SRC, out_path=dst, only_subtrees=[name],
                      with_animations=False)
        return name, r["bytes"], r["meshes"], None
    except Exception as e:
        return name, 0, 0, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("n3", help="e.g. .../models/characters/uniskel.n3")
    ap.add_argument("--root", default="extracted/export_win32")
    ap.add_argument("--out", default=None, help="default: <export>/characters/<name>")
    ap.add_argument("--export-root", default="godot_export")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()

    name = os.path.splitext(os.path.basename(a.n3))[0]
    out = a.out or os.path.join(a.export_root, "characters", name)
    os.makedirs(out, exist_ok=True)

    model = n3mod.load(a.n3)
    chars = model.find(CHARACTER_NODE)
    if not chars:
        print(f"{name}: no CharacterNode, nothing to split")
        return
    char = chars[0]

    # Parts are the subtrees sitting directly under the CharacterNode.
    parts = [nd.name for i, nd in enumerate(model.nodes)
             if nd.parent >= 0 and model.nodes[nd.parent].type == CHARACTER_NODE
             and nd.name]
    parts = sorted(set(parts))
    print(f"[{name}] {len(parts)} parts, {len(char.skin_lists)} outfits", flush=True)

    # 1) The animation file: skeleton only, plus every clip.
    ex = Exporter(a.root, out)
    anim_path = os.path.join(out, "__animations.glb")
    r = ex.export(a.n3, out_path=anim_path, only_subtrees=["__aucune__"],
                  with_meshes=False)
    print(f"[{name}] animations: {r['bytes']/1048576:.1f} MB, "
          f"{r['animations']} clips, {r['joints']} bones", flush=True)

    # 2) One file per part.
    _init(a.root, out, a.n3)
    done = failed = total = 0
    with ProcessPoolExecutor(max_workers=a.workers,
                             initializer=_init,
                             initargs=(a.root, out, a.n3)) as pool:
        for pname, nbytes, nmesh, err in pool.map(_part, parts, chunksize=4):
            if err:
                failed += 1
            else:
                done += 1
                total += nbytes
            if (done + failed) % 250 == 0:
                print(f"[{name}] parts {done+failed}/{len(parts)}", flush=True)
    print(f"[{name}] parts: {done} ok, {failed} failed, "
          f"{total/1048576:.1f} MB total", flush=True)

    # 3) Body-shape variations (per-joint translation + scale).
    #    These are static poses, not animations: the engine keeps them apart
    #    (variationTranslation / variationScale) and applies them on top of
    #    the animated pose. We emit JSON for the Godot modifier to consume.
    variations = {}
    vref = char.variation_resource
    if vref:
        vpath = os.path.join(a.root, "anims", vref.split(":", 1)[-1])
        if os.path.isfile(vpath):
            from dsoexp import nax as naxmod
            bone_names = [j.name for j in sorted(char.joints, key=lambda j: j.index)]
            for clip in naxmod.load(vpath):
                joints = []
                for k in range(min(clip.num_joints, len(bone_names))):
                    tr = clip.joint_track(k)
                    t = tr.get("translation", [(0.0, 0.0, 0.0, 0.0)])[0]
                    sc = tr.get("scale", [(1.0, 1.0, 1.0, 0.0)])[0]
                    joints.append({"bone": bone_names[k],
                                   "t": [round(v, 6) for v in t[:3]],
                                   "s": [round(v, 6) for v in sc[:3]]})
                if joints:
                    variations[clip.name] = joints
            with open(os.path.join(out, "variations.json"), "w") as f:
                json.dump({"character": name, "variations": variations}, f)
            print(f"[{name}] {len(variations)} variations -> variations.json",
                  flush=True)
        else:
            print(f"[{name}] variations not found: {vref}", flush=True)

    # 4) The outfits.
    outfits = [{"name": n, "parts": skins, "variation": var}
               for n, skins, var in char.skin_lists]
    with open(os.path.join(out, "outfits.json"), "w") as f:
        json.dump({"character": name,
                   "animations": "__animations.glb",
                   "part_dir": "parts",
                   "joints": r["joints"],
                   "variations": "variations.json" if variations else "",
                   "outfits": outfits}, f, indent=1)
    print(f"[{name}] {len(outfits)} outfits -> outfits.json", flush=True)


if __name__ == "__main__":
    main()
