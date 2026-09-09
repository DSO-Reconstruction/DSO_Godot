# Nebula3 / DSO file formats

Everything below is little-endian.

## FourCC packing

FourCCs are stored **reversed**: `NVX2` appears as `2XVN`, `MESH` as `HSEM`,
`DSOM` as `MOSD`. They are packed as a `uint32`, so the byte order of the code
flips while the integers that follow stay little-endian.

This is the single easiest thing to get wrong. Seeing `2XVN` and switching the
reader to big-endian produces plausible-looking garbage.

## `.nvx2` — meshes

```
'NVX2'            4 bytes (or '2XVN')
numGroups         u32
numVertices       u32
vertexWidth       u32   in *floats*, so stride = vertexWidth * 4
numTriangles      u32
numEdges          u32
componentMask     u32
groups            numGroups * 6 u32:
                    firstVertex, numVertices, firstTriangle,
                    numTriangles, firstEdge, numEdges
vertices          numVertices * stride bytes, interleaved
indices           u16 or u32 (decide from the remaining byte count)
```

`componentMask` describes the vertex layout. Components appear in bit order:

| Bit | Component | Bytes |
|---|---|---|
| 0 | Coord | 12 |
| 1 / 2 | Normal / NormalUB4N | 12 / 4 |
| 3-10 | Uv0..Uv3, each full or `S2` | 8 / 4 |
| 11 / 12 | Color / ColorUB4N | 16 / 4 |
| 13 / 14 | Tangent / TangentUB4N | 12 / 4 |
| 15 / 16 | Binormal / BinormalUB4N | 12 / 4 |
| 17 / 18 | Weights / WeightsUB4N | 16 / 4 |
| 19 / 20 | JIndices / JIndicesUB4 | 16 / 4 |

The sum of the selected component sizes must equal the stride; if it does not,
the mask was misread.

- `S2` UVs are fixed point: `int16 / 8192`.
- `UB4N` values are `byte / 127.5 - 1.0`. One byte per axis leaves normals
  about 1% off unit length, and glTF rejects non-unit normals, so
  **renormalise them**.

## `.n3` — models and scene graph

```
'NEB3'            4 bytes (or '3BEN')
version           u32 (1 or 2)
tag stream        FourCC tags until '<MDL' or 'EOF_'
```

The tag stream has **no length fields**. Every payload size is implicit, so an
unknown tag desynchronises everything after it. The tag table in
`dsoexp/n3.py` must therefore be exhaustive.

Structural tags: `>MDL` (class FourCC + string name), `<MDL`, `>MND` (push a
node: class FourCC + string name), `<MND` (pop), `EOF_` (terminator).

Strings are `u16` length followed by raw UTF-8 bytes.

Node classes that matter:

| Class | Role |
|---|---|
| `TRFN` | transform node |
| `SPND` | static mesh |
| `CHSN` | skinned mesh (carries joint palettes) |
| `CHRN` | character: skeleton, animation resource, skin lists |
| `PSND` | particle emitter |
| `MANI` | animator |

Key payloads: `POSI`/`ROTN`/`SCAL`/`RPIV`/`SPIV` are 4 floats each; `LBOX`
and `BCLS` are 8 floats (centre then extents); `MESH` and `SHDR` are strings;
`STXT` is two strings (semantic, resource); `JONT` is two i32 then 12 floats
then a string; `SFRG` is an i32 prim-group index, an i32 joint count, then
that many i32 palette entries.

Resource prefixes: `msh:` -> `meshes/`, `tex:` -> `textures/`,
`ani:` -> `anims/`, `shd:` -> `shaders/`.

### Two tags documented nowhere

- **`SBLB`, `SSPR`** — a single boolean byte. Established by observing that
  the next FourCC begins exactly one byte later.
- **`ADPK` / `ADEK` / `ADSK`** — animator position / euler / scale keys.
  Layout is `i32 count`, then `count * 24` bytes (a leading f32 that is always
  0.0, an f32 time, then a 4-float value). Other readers treat these as empty,
  which desynchronises every model that uses them. Confirmed by the gap to the
  next known tag being exactly `4 + 24 * count` across all samples.

### Animator sections

A `MANI` node holds sections opened by `BASE` (an i32 `AnimNodeType`):

| Value | Kind |
|---|---|
| 0-2 | Int / Float / Float4 animator (shader parameters) |
| 3 | TransformAnimator |
| 4 | TransformCurveAnimator |
| 5 | UvAnimator |

For `TransformCurveAnimator`, the important pairing is:

- `ANNO` = the **target node path**, e.g. `model/goinginactive/polySurface69`,
  resolved by walking node names.
- `SANI` = the **animation resource**, e.g. `ani:foo/bar.nax3`.
- `SAGR` = `anim_group`, which is the **clip index** inside that `.nax3`.

Those two are easy to swap; `ANNO` is a node path, not a resource.

## `.nax3` / `.nac` — animations

```
'NAX3'            4 bytes (also seen as 'HAN0', 'NA01' and reversals)
numClips          i32
numKeys           i32
per clip:
  numCurves       u16
  startKeyIndex   u16
  numKeys         u16
  keyStride       u16
  keyDuration     u16   in milliseconds
  preInfinity     u8
  postInfinity    u8
  numEvents       u16
  name            char[50]
  events          numEvents * (char[47] name, char[15] category, u16 keyIndex)
  curves          numCurves * (u32 firstKey, u8 isActive, u8 isStatic,
                               u8 curveType, u8 pad, 4 * f32 staticKey)
keys              numKeys * 4 * f32, when embedded
```

`fps = 1000 / keyDuration`. `postInfinity != 0` means the clip cycles.

A curve is one channel of one joint. Curve types: `0` translation, `1` scale,
`2` rotation, `4` velocity. Curves come in groups of 3 per joint, or 4 when a
velocity channel is present (check whether `curves[3].curveType == 4`).

Two shapes exist in practice:

- **Character clips** (referenced by `CHRN`/`ANIM`): one clip per animation,
  with many joints.
- **Animator clips** (referenced by `MANI`/`SANI`): one clip per animated
  node, selected by `anim_group`.

When the keys are not embedded, they live in `<base>_<clipname>.nac`: magic
`NAC0`, then per frame and per non-static curve, either 4 × `int16 / 32768`
for a rotation (a compressed quaternion, renormalise it) or 4 × `f32`.

About 80% of the shipped curves are flagged static. Writing them repeated on
every frame inflates output roughly fourfold for nothing — but a fully
constant clip still needs two keys, at the clip bounds, or its duration
collapses to zero.

## `.map` — map object placement

Sections, each opened by a reversed FourCC:

```
DSOM  version (5)
MAPI  grid_size vec4, center vec4, extents vec4, 3 * i16 size
STRT  numStrings u32, tableSize u32, then a NUL-separated blob
SETT  count u32, then count * 2 u16
EVET  count u32, then count * u32
EMAL  count u32, then count * string
TMPL  count u32, then per template:
        5 * u16 (gfxResId, gfxRootNode, phxResId, collResId, sfxEventId)
        center vec4, extents vec4, u16 collMeshGroupIndex, u16 type
INST  count u32, then per instance:
        pos vec4, rot vec4 (quaternion),
        bool useScaling -> if set, scale vec4
        bool useCollide, bool visibleForNavMeshGen,
        i16 templateIndex, i16 groupIndex, u16 nameIndex, u16 mappingIndex
GROP  count u32, then per group:
        u16 nameId, u16 type, i16 parent, u16 nInst, u16 nGroups,
        then the index arrays
NAVB  count u32, then per polygon: count u32, then that many f32 pairs
```

`templates[instance.templateIndex].gfxResId` indexes the string table and
gives the `.n3` model path. That link is what turns a map into a scene.

## Textures

All DDS, DXT1/DXT3/DXT5.

**Bump maps are DXT5nm, not RGB normal maps.** R is a constant 255 and B a
constant 0; the data is in green and alpha: X in alpha, Y in green, Z
reconstructed as `sqrt(1 - x² - y²)`.

Measured over the shipped textures, `x² + y²` averages 0.056 and exceeds 1 on
0.01% of pixels, which confirms the scheme. Reading RGB as-is gives vectors
about 1.42 long.

Whether X and Y are swapped cannot be told apart statistically; the standard
DXT5nm convention is assumed. If the relief looks rotated, swapping them is
the first thing to try, and flipping green the second (Direct3D counts Y down,
Godot counts it up).

## Shaders

24 shader names appear across the models. These render transparently and need
`alphaMode = BLEND`, which is easy to miss: `decal`, `particle`, `refraction`,
`volumefog`, `glow`, `water`, `ocean`, `coast`, `sequenceadditive`,
`unlitalphavertexcolors`, `simplelayer`.

Leaving `decal` out shows up immediately as opaque squares lying on the
ground. The exporter also inspects the base-colour texture's alpha channel, so
materials that need blending for other reasons are caught too.
