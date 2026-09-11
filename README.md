# DSO_Godot

Asset extraction pipeline for **Drakensang Online**: from the packed client
data to glTF 2.0 + PNG, ready to import into **Godot 4**.

Models, skeletons, animations, textures, maps and particle parameters are all
decoded and converted. Every reader was validated against the complete data
set rather than a sample.

## What it produces

| Stage | Output | Validation |
|---|---|---|
| Unpack | ~162,000 files rebuilt into the `export_win32/` tree | — |
| `.nvx2` meshes | 29,756 | **29,756 / 29,756** parsed |
| `.n3` models | 15,592 | **15,592 / 15,592** parsed |
| `.nax3` + `.nac` animations | 3,767 files, 17,759 clips | **3,767 / 3,767** parsed |
| `.map` maps | 391, 2,218,376 instances | **391 / 391** parsed, 0 missing model |
| `ui/*.bxml` windows | 164, 35,776 widgets | **164 / 164** parsed |
| Export | ~19,900 `.glb`, ~21,400 `.png`, 391 map manifests | Khronos validator: *no errors* |

Counts come from one particular client version; yours will differ slightly.

## Requirements

- Python 3.10+ with `Pillow` and `numpy` (`pip install -r requirements.txt`)
- Godot 4.3 or newer, for the import and the Godot-side scripts
- Roughly 10 GB of free disk space for a full export

## Quick start

```bash
pip install -r requirements.txt

# 1. Unpack the client data
python3 unpack.py /path/to/DSOClient extracted

# 2. Export every model to .glb (plus .fx.json sidecars for particles)
python3 batch_export.py --root extracted/export_win32 --out godot_export \
    --skip uniskel uniskel_dwarf --report export_report.json

# 3. Split the player characters, which are one huge .n3 each
python3 split_character.py extracted/export_win32/models/characters/uniskel.n3 \
    --root extracted/export_win32 --export-root godot_export

# 4. Export the interface (164 windows -> Control scenes)
sh tools/build_crn2dds.sh          # once: the .crn texture transcoder
python3 export_ui.py --root extracted/export_win32 --out godot_export

# 5. Export the maps as placement manifests
python3 export_maps.py --root extracted/export_win32 --out godot_export/maps

# 6. Build a Godot project out of it all
python3 setup_godot.py --project ~/DSOGodot --godot /path/to/godot --stage light
```

### Just want to look at something first?

`make_minimal_project.py` stages one map, one mob, one equipped character and
four effects — and nothing else. It imports in well under a minute, because
only the files actually referenced get staged.

```bash
python3 make_minimal_project.py --project ~/DSOTest --godot /path/to/godot
```

Then open the project and press **F5**. See [docs/IMPORT_GODOT.md](docs/IMPORT_GODOT.md)
for the full import guide, including the traps.

## Layout

```
unpack.py                 step 1: NB3 bundles, __ZN blocks, IB3N indexes, TOCs
export_model.py           one .n3 -> one .glb (also usable as a library)
batch_export.py           parallel export of every model
export_maps.py            .map -> JSON placement manifest + build_map.gd
split_character.py        split a player character into parts + animations
setup_godot.py            build a full Godot project, import included
make_minimal_project.py   build a small test project

dsoexp/                   the format readers
  nvx2.py                 meshes
  n3.py                   models / scene graph
  nax.py                  animations (NAX3 index + NAC key streams)
  dsomap.py               maps
  glb.py                  minimal glTF 2.0 writer
  resources.py            resource paths, texture conversion
  mathutil.py             transform helpers

godot/                    scripts to run inside Godot
  fx_to_scenes.gd         .fx.json -> GPUParticles3D scenes
  build_map.gd            instance a map from its manifest
  build_outfit.gd         assemble a player-character outfit
  apply_variation.gd      apply a body-shape variation (SkeletonModifier3D)
  make_demo.gd            build a demo scene
  make_test_scene.gd      build the minimal test scene
  verify_import.gd        check what Godot made of the .glb files
  verify_scene.gd         check a built scene, orientation and motion included
```

## The interface

`ui/*.bxml` is Nebula3 binary XML: 164 windows, 35,776 widgets. Each widget's
artwork is one group of a shared mesh (`meshes/ui/<window>_s_0.nvx2`) that
carries the geometry, the atlas UVs and the vertex colours; the widget's own
`rect` is a fraction of the screen.

```bash
python3 export_ui.py --root extracted/export_win32 --out godot_export
```

Per window it writes a Godot `Control` tree (`ui/<window>.tscn`) and the
decoded widget tree (`ui/<window>.ui.json`, nothing dropped), plus the atlases
as PNG and the localisation tables (`ui/loca/<lang>.json`).
`godot/ui_browser.gd` flips through the lot; `godot/dso_button.gd` drives the
button states the client shipped. The format and the coordinate system are in
[docs/UI.md](docs/UI.md).

## Formats

Full notes in [docs/FORMATS.md](docs/FORMATS.md). The short version:

Everything is little-endian. FourCCs are stored reversed (`NVX2` shows up as
`2XVN`, `MESH` as `HSEM`, `DSOM` as `MOSD`) because they are packed as a
`uint32` — this is *not* big-endian data, and reading it as such is the
classic trap.

- **`.nvx2`** — header, per-group ranges, interleaved vertices described by a
  component mask, then indices. `UB4N` normals need renormalising or glTF
  rejects them.
- **`.n3`** — a FourCC tag stream with **no length fields**, so every payload
  size is implicit and one unknown tag desynchronises the rest of the file.
  The tag table in `dsoexp/n3.py` has to be exhaustive.
- **`.nax3` / `.nac`** — clip index plus key streams. A curve is one channel
  of one joint; curves come in groups of 3 (or 4 with a velocity channel).
  Rotations are quaternions compressed to `int16`.
- **`.map`** — tagged sections; instances carry position, quaternion and
  optional scale, and reference a template that points into a string table to
  name the model.

Two `.n3` details were established empirically rather than from any
documentation: `SBLB`/`SSPR` carry a single boolean byte, and
`ADPK`/`ADEK`/`ADSK` carry `i32 count` followed by `count * 24` bytes (other
readers treat them as empty, which desynchronises every model that uses them).

Bump maps are **not** RGB normal maps: they are DXT5nm, with X in alpha and Y
in green, R and B unused. Reading RGB directly yields vectors ~1.42 long. The
converter reconstructs a proper tangent-space normal map.

## What is not converted

- **Shader-parameter animators** (`FloatAnimator`) and **UV scrolling**
  (`UvAnimator`) have no animatable glTF equivalent. They are counted in the
  export report but not translated.
- **Particle systems** are exported as parameters (`.fx.json`) and rebuilt on
  top of the `.glb` by `godot/fx_to_scenes.gd`. The emission model is still an
  approximation: Nebula's birth rate becomes a live particle count, its
  four-point envelopes become `Curve`/`Gradient`, and `stretch` /
  `stretch_to_start` have no equivalent. `make_fx_project.py` builds a
  project with nothing but the effects and a viewer, to check them.
- **Cubemap reflections** are kept in each material's `extras` rather than
  wired up, since glTF has no per-material environment reflection.
- **`.crn` (Crunch) textures** are decoded through a transcoder built by
  `tools/build_crn2dds.sh`; without it those 2 559 textures are skipped.

## Credits and licensing

Format understanding started from two repositories by **simo8902**:

- [`drakensang-nb3-bundle-extractor`](https://github.com/simo8902/drakensang-nb3-bundle-extractor)
  (WTFPL) — the container handling in `unpack.py` derives from it.
- [`nebula3-renderer`](https://github.com/simo8902/nebula3-renderer)

**Important:** the second repository carries an "All rights reserved /
unauthorized copying, modification, distribution, or use is strictly
prohibited" header. It was used as **format documentation only**. No line of
its code is reproduced here: the readers in this repository are written in
Python from the Nebula format specifications (NVX2 and NAX3 originate in Radon
Labs' Nebula Device) and from direct observation of the files. Satisfy
yourself about this before redistributing.

The tooling is MIT licensed (see `LICENSE`). **The game assets are not.** They
remain the property of Bigpoint GmbH; this repository contains none of them,
and `.gitignore` is set up to keep it that way. Extract only from a client you
have installed, for personal use.
