# The interface

164 windows, 35,776 widgets, all of it in `export_win32/ui/`.

## `.bxml` — Nebula3 binary XML

Little-endian; the FourCC is packed backwards, as everywhere in these files
(`BXML` reads `LMXB`).

```
'BXML'                       u32
numAttrs, numNodes, numStrings   3 x u32
numAttrs   x (u32 nameIdx, u32 valueIdx)
numNodes   x (u32 nameIdx, firstChild, nextSibling, parent, firstAttr, numAttrs)
numStrings x NUL-terminated UTF-8
```

Every index points into the string table; `0x7fffffff` means *none*. Node
order is arbitrary — the root is the one whose `parent` is `0x7fffffff`, and
children are walked through `firstChild` then the `nextSibling` chain.
`dsoexp/bxml.py` reads it; run it directly to dump a window as plain XML:

```bash
python3 -m dsoexp.bxml extracted/export_win32/ui/confirmdialog.bxml
```

The same container holds `sequences/*.pbxml` (50 files).

## Widgets

17 tags, in descending order of use: `Label` (24,869), `TextLabel`, `Button`,
`Icon`, `Slot`, `List`, `Window`, `Canvas`, `TextButton`, `UVProgressBar`,
`FrameLabel`, `ProgressBarElement`, `SlotButton`, `ProgressBar`, `TextEntry`,
`Slider`. Every one of them is a rectangle with optional artwork and optional
text, so they all export to a `Control`; the original tag is kept in
`metadata/dso_type`.

## Coordinates

Two systems meet in every widget, and getting the relation between them wrong
is the whole difficulty.

- `rect="left,top,right,bottom"` is a **fraction of the screen**, absolute,
  not relative to the parent.
- `mesh` + `meshGrpIdx` point at one group of the window's shared `.nvx2`.
  That geometry is **centred on the origin**, in a box four units wide and
  three tall — a 4:3 screen. A screen of 1024x768 therefore puts both axes at
  **256 px per mesh unit**, which is the resolution the UI was drawn for.
- `scale="sx,sy"` stretches the mesh. Measured over 12,038 widgets,
  `rect_width / (mesh_width * scale_x)` is exactly `0.25` and the vertical
  equivalent exactly `1/3` at the 99th percentile: the mesh, scaled, *is* the
  rect. Where they differ the mesh is the artwork and the rect the hit area.

The exporter therefore lays a widget out from its `rect` and draws it from its
mesh. `HorizontalParentAlignment` / `VerticalParentAlignment`
(`Left|HCenter|Right`, `Top|VCenter|Bottom`) become the Godot anchors, so the
window keeps its pixel size and re-anchors when the viewport changes, which is
what the engine did. Verified against Godot's own layout pass: every widget
lands on its authored rect to within 0.01 px.

## Artwork

A mesh group carries positions, UVs into the atlas named by `texture`, and
vertex colours (`COLOR_UB4N`) that are genuinely used — tints, gradients,
alpha-0 spacers.

- One axis-aligned quad with a uniform colour → `TextureRect` over an
  `AtlasTexture` region (16,070 widgets), or a `ColorRect` when the texture is
  the engine's `system/white` placeholder.
- Anything else → `Polygon2D` with explicit triangles, UVs in texture pixels
  and per-vertex colours (11,860 widgets). This covers the 3-slice and 9-slice
  frames and the odd composites exactly, without having to recognise them.

A widget whose atlas is missing from the client data (216 of them, mostly
retired event art) draws nothing: painting the bare geometry would put an
opaque white block over the window.

## Mutually exclusive layouts

Widgets that share one rect with their siblings are alternative states of the
same slot: a button's `normal`/`pressed`/`mouseover`, the three colours of a
latency light, the four class backgrounds of the character creator, the frames
of a flipbook. The engine shows one. The exporter hides the others when — and
only when — every id in the group is a button state, or three or more widgets
share the rect. A rect shared by exactly two widgets is just as often a frame
stacked on its fill; hiding one of those wipes out half a window.

Nothing is deleted: the nodes are in the scene with `visible = false`.
`--keep-variants` leaves them all on, and `ui_browser.gd` toggles them with
`V`.

## Text

`Text` is a localisation key, `<category>_<item>`, resolved against
`language/<lang>/*.xml` (81,618 strings per language, 22 languages). The
resolved string is baked into the `Label`, and the key stays in
`metadata/dso_loca` so a viewer can re-translate at runtime — that is what
`ui_browser.gd` does with `L`.

`fontSize` is not in pixels and the engine's conversion factor is not in the
data, so `--font-scale` (default 1.5) is a calibration knob. `TextEffect`
1 and 2 become a Godot shadow and outline respectively.

## What is not converted

- **Bitmap fonts.** `ui/*_info.bytes` + `textures/ui/tahoma.dds` are the
  client's own glyph atlases. The scenes use a `SystemFont` asking for Tahoma
  instead, so the metrics are close but not identical.
- **UI animations.** 820 widgets carry `AnimRes` / `AnimGroup` /
  `AnimDuration` pointing into `anims/ui/`. They are kept in the `.ui.json`
  and ignored by the scene writer.
- **Behaviour.** `List`, `Slider`, `ProgressBar`, `TextEntry` and `Slot`
  export as plain `Control`s with their artwork; only buttons get a runtime
  script. Item icons, list rows and bar fills were filled in by the game's
  own code, which is not in the data.
