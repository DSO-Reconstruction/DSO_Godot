# Importing a DSO export into Godot 4

## 1. Stage the tree

Copy the **whole** `godot_export/` folder into your project in one piece:

```bash
mkdir -p ~/MyProject/dso
cp -r godot_export/* ~/MyProject/dso/
```

**Never move a single `.glb` on its own.** Models reference their textures by
relative URI (`../textures/creatures/xxx.png`). Taking a `.glb` out of its
folder breaks its textures. To reorganise, re-export with a different `--out`.

`setup_godot.py` and `make_minimal_project.py` do this staging for you, and
they stage only what is in scope.

## 2. Set the import options *before* opening the project

This matters more than anything else here. A full export is ~19,900 `.glb`
and ~21,400 `.png`: opening the project as-is makes Godot reimport all of it
at once.

The single most useful setting is `detect_3d/compress_to = 0`. Otherwise Godot
imports every texture losslessly as 2D, then reimports it the moment it is
used in 3D — two complete passes over 21,000 files. Both setup scripts write
this into `project.godot` up front:

```
[importer_defaults]

texture={
"compress/mode": 2,
"detect_3d/compress_to": 0,
"mipmaps/generate": true
}
```

`config/features` must also name **your** Godot version. A project declaring
an older version makes Godot offer to convert it, and a headless `--import`
then bails out without importing anything.

### Import in stages

Drop a `.gdignore` in folders you do not need yet and Godot skips them
entirely:

```bash
cd ~/MyProject/dso
for d in ui picking dummies; do touch $d/.gdignore; done
```

Better still, do not stage them at all: Godot walks an ignored folder's tree
even though it skips its contents, whereas a folder that is absent costs
nothing. That is why `setup_godot.py --stage` stages rather than ignores.

### Import from the command line

```bash
godot --headless --path ~/MyProject --import
```

Godot spends several minutes in its scan phase (`_update_scan_actions`) before
writing a single imported file. It looks hung and it is not. Both setup
scripts surface Godot's own percentage so you can tell the difference. The
import is resumable: rerunning it continues where it stopped.

## 3. What Godot makes of a `.glb`

| In the `.glb` | In Godot |
|---|---|
| skeleton (`CHRN`) | `Skeleton3D`, bone names preserved |
| skinned mesh (`CHSN`) | `MeshInstance3D` + `skin` |
| animation clips | `AnimationPlayer` with an `AnimationLibrary` |
| `MANI` animations (props, FX) | position/rotation/scale tracks on the nodes |
| materials | `StandardMaterial3D` (albedo, normal, emission, roughness) |

Animations whose name ends in `-loop` are set to loop on import. Godot
**strips the suffix**, so you will see `foo` with `loop_mode = Linear`, not
`foo-loop`. The importer also drops tracks whose value never changes, so an
animation exported with 18 channels may keep only 10. That is an optimisation,
not a loss.

## 4. Animations do not play in the editor viewport

`autoplay` only fires when the scene **runs**. Press **F5** or **F6**. In the
editor, select the `AnimationPlayer` and start playback from the animation
panel.

If you want to check programmatically whether an animation really moves
anything, `godot/verify_scene.gd` does it — and note two traps it had to work
around:

- Overriding `_process` in a `SceneTree` subclass **replaces** the virtual
  Godot uses to process the tree, so nothing ticks. Use `advance()` to step an
  `AnimationMixer` by hand.
- Reading a bone pose straight after `seek()` returns stale values, because
  applying the pose to the skeleton is deferred within the frame.

## 5. Maps

Maps are JSON manifests, not scenes, so geometry is not duplicated once per
map and you keep them editable.

1. Copy `godot/build_map.gd` into the project.
2. New scene, root `Node3D`, attach the script.
3. In the inspector: `manifest_path` -> a `.map.json`, `model_root` ->
   `res://dso`, `max_instances` -> `500` for a first look, `0` afterwards.
4. Tick `build`.

Start with a small map before a large one; the biggest run to tens of
thousands of objects.

## 6. Particle effects

The `.glb` of an effect on its own looks broken, and that is expected: glTF
can carry neither an additive blend mode nor a particle emitter. What the
importer shows is the emitter's *spawn surface*, drawn as a lit,
alpha-blended blob. Both facts are in the sidecar `.fx.json`, so one script
puts them back:

```bash
cp /path/to/DSO_Godot/godot/fx_to_scenes.gd ~/MyProject/tools/
godot --headless --path ~/MyProject --script res://tools/fx_to_scenes.gd
```

It writes a `<model>.fx.tscn` next to every `<model>.glb` that has effects.
**Use that scene, not the `.glb`.** It holds the imported model with:

- the per-node render state restored from the engine's own `MNTP` tag —
  `Additive` becomes an unshaded, additively blended material with depth
  writes off, `AlphaTest` an alpha scissor, `Decal` an unshaded overlay, and
  so on for the twenty states the client uses. This is the fix if your
  effects came out as dark or milky quads: glTF only has OPAQUE / MASK /
  BLEND, so every additive glow imported as a *lit, alpha-blended* surface;
- the glow artwork put back where an unshaded material can see it. Most
  Nebula glows have no `DiffMap0` at all -- the picture is in `EmsvMap0`, and
  the engine's additive pass adds *that*. In glTF it becomes an
  emissiveTexture over a white base colour, and Godot's unshaded shading
  reads only ALBEDO, so the quads turned into sheets of pure white being
  added to the frame. The emission is folded back into the albedo;
- `Refraction` nodes rebuilt as a real screen-space shader (a DuDv warp of
  the back buffer) instead of the distortion map drawn as colour. The back
  buffer only holds *opaque* geometry, so a refraction effect floating in an
  otherwise empty scene samples black: give the scene a floor;
- each `PSND` emitter turned into a `GPUParticles3D` whose emission points
  and normals are the emitter mesh's own vertices, so particles are born
  where and along the direction the artist authored. The emitter mesh itself
  stops being drawn.

Still an approximation: Nebula's emission *rate* becomes a pool of live
particles, its four-point envelopes become `Curve` / `Gradient` resources,
and `stretch` / `stretch_to_start` have no Godot equivalent.

### A project with nothing but effects

```bash
python3 make_fx_project.py --project ~/DSOFX --godot /path/to/godot
```

Stages the effects with the textures they reference (symlinks, so no second
copy on disk), imports, runs the conversion and drops in `fx_browser.gd`:
left/right walks the effects, drag orbits, `R` restarts the emitters, `S` is
slow motion, `G` hides the floor, `B` cycles the background.

## 7. The interface

```bash
python3 export_ui.py --root extracted/export_win32 --out ~/MyProject/dso \
    --res-prefix res://dso
```

`--res-prefix` must say where the output ends up inside the project, or the
scenes will not find their textures. Then open any `dso/ui/<window>.tscn`, or
put `dso/ui/ui_browser.gd` on a full-rect `Control` and run it: left/right
walks the 164 windows, `L` cycles the language, `V` reveals the alternative
layouts the exporter hid.

Buttons already work: `dso_button.gd` swaps the `normal` / `pressed` /
`mouseover` / `disabled` artwork and emits `dso_pressed(event_name)` with the
event string the game's own UI code listened for.

Details and known gaps: [UI.md](UI.md).

## 8. Player characters

`uniskel` and `uniskel_dwarf` each hold thousands of equipment pieces and
hundreds of named outfits in a single `.n3`. Exported whole, one came to
355 MB. `split_character.py` turns them into:

```
characters/uniskel/
├── __animations.glb    skeleton + every clip, no geometry
├── parts/*.glb         one part per file (skeleton + 1 mesh)
├── outfits.json        outfits: name -> list of parts
└── variations.json     body-shape variations, per joint
```

To assemble an outfit: attach `godot/build_outfit.gd` to a `Node3D`, set
`character_dir` and `outfit_name`, tick `build`. It instances
`__animations.glb` as the base and reparents each part's meshes onto its
`Skeleton3D`; since the parts share bone names, one `AnimationPlayer` drives
everything.

Pick a real outfit, not an `animator_test_*` one — those are test stacks of
40-plus pieces, not characters. A bare base body is e.g. `warrior_male_body_00`
(14 pieces).

### Body-shape variations

Each outfit names a variation (`var_warrior_male_00` and friends). These are
static poses giving a per-joint translation and scale, and they are what makes
a warrior stocky and a mage slight. Without one, every player character has
the neutral proportions of the shared skeleton.

Add `godot/apply_variation.gd` as a **child of the Skeleton3D**, set
`variations_path` and `variation_name`. It is a `SkeletonModifier3D`, so Godot
runs it after animation and the variation adds to the motion instead of being
overwritten by it — which is how the original engine treats it.

This part is a reconstruction: the rotation present in the data is ignored,
since the engine only keeps translation and scale. If a build looks wrong,
start here.

## 9. Known traps

**Orientation.** Nebula3 and glTF are both right-handed Y-up, so nothing is
converted. Verified: characters and mobs import upright. If something arrives
lying down, this is the first thing to suspect; `verify_scene.gd` reports it.

**Normals.** Bump maps are DXT5nm (X in alpha, Y in green) and the exporter
rebuilds them as RGB. If the relief looks inverted or rotated, try swapping X
and Y — the two are statistically indistinguishable, so the standard
convention was assumed — or flip the green channel, since Direct3D counts Y
down and Godot counts it up. See `bump_to_normal` in `dsoexp/resources.py`.

**Roughness.** `SpecMap0` becomes a roughness map (`*_spec_rough.png`,
roughness = 1 − glossiness). An approximation: the original engine was not PBR.

**Environment reflections.** `CubeMap0` has no per-material glTF equivalent.
It is preserved in each material's `extras.nebula_textures`, to be wired up
through a `WorldEnvironment` if you want the original look.

**Not exported.** Shader-parameter animators (`FloatAnimator`, e.g. a pulsing
emission) and UV scrolling (`UvAnimator`) have no animatable glTF equivalent.
They appear in the export report but are not translated; reproducing them
needs an `AnimationPlayer` driving shader parameters on the Godot side.

**Empty models.** Some `.glb` files contain nothing. They are the `ui/`
models: interface layouts with no geometry. Expected.

**Missing textures.** A couple of hundred textures are referenced by models
but absent from the client data. Those materials import without an albedo map.
