#!/usr/bin/env python3
"""Export one N3 model (character, prop, FX) to a Godot-importable .glb.

Assembles the node hierarchy, the NVX2 meshes, the skeleton and skinning from
the CharacterNode, materials with textures converted to PNG, and every
animation clip from the referenced .nax3 files.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsoexp import n3 as n3mod, nvx2 as nvx2mod, nax as naxmod
from dsoexp.glb import (GLB, FLOAT, UBYTE, USHORT, UINT,
                        ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER)
from dsoexp.mathutil import compose, normalize_quat
from dsoexp.resources import (Resolver, dds_to_png, spec_to_roughness,
                              bump_to_normal, is_bump, has_alpha)

# Nebula texture semantics -> glTF role.
TEX_ROLES = {"DiffMap0": "base", "BumpMap0": "normal", "EmsvMap0": "emissive"}
# Engine-side placeholder textures: no point exporting them.
DUMMY_TEX = ("system/", "dummies/")

# Shaders whose rendering is transparent by nature. Without this list decals
# came out OPAQUE, which showed up as white squares lying on the ground.
TRANSPARENT_SHADERS = {
    "decal", "particle", "refraction", "volumefog", "glow", "water", "ocean",
    "coast", "sequenceadditive", "unlitalphavertexcolors", "simplelayer",
}
# glTF has no additive blend mode: we blend and record the original shader in
# the material extras.
ADDITIVE_SHADERS = {"glow", "sequenceadditive"}

CHARACTER_NODE, SKIN_NODE, SHAPE_NODE = "CHRN", "CHSN", "SPND"
PARTICLE_NODE, ANIMATOR_NODE = "PSND", "MANI"
MESH_NODES = (SKIN_NODE, SHAPE_NODE, PARTICLE_NODE)

# Animator kinds (the engine's AnimNodeType enum).
TRANSFORM_CURVE_ANIMATOR = 4      # node TRS driven by a .nax3
FLOAT_ANIMATOR = 1                # shader parameter: no glTF equivalent
UV_ANIMATOR = 5                   # UV scrolling: no glTF equivalent

# Godot loops an imported animation whose name ends with '-loop'.
LOOP_SUFFIX = "-loop"


class Exporter:
    def __init__(self, root, outdir, texdir=None, verbose=False):
        self.res = Resolver(root)
        self.outdir = os.path.abspath(outdir)
        self.texdir = os.path.abspath(texdir or os.path.join(outdir, "textures"))
        self.verbose = verbose
        self.warnings = []
        self._out_dir = self.outdir

    def log(self, msg):
        if self.verbose:
            print(f"   {msg}", flush=True)

    def warn(self, msg):
        self.warnings.append(msg)

    # ------------------------------------------------------------------ textures

    def _texture(self, g, ref, cache):
        """Convert the texture to PNG and return its glTF index (or None)."""
        if not ref or any(d in ref for d in DUMMY_TEX):
            return None
        if ref in cache:
            return cache[ref]
        src = self.res.path(ref)
        if not src:
            self.warn(f"texture not found: {ref}")
            cache[ref] = None
            return None
        rel = os.path.splitext(os.path.relpath(src, os.path.join(self.res.root, "textures")))[0]
        # Bump maps are DXT5nm: the RGB normal has to be reconstructed.
        bump = is_bump(ref)
        png = os.path.join(self.texdir, rel + ("_nrm.png" if bump else ".png"))
        convert = bump_to_normal if bump else dds_to_png
        if not convert(src, png):
            self.warn(f"texture conversion failed: {src}")
            cache[ref] = None
            return None
        # Godot -- and the glTF spec -- resolve URIs relative to the glTF file
        # itself, not to any project root.
        uri = os.path.relpath(png, self._out_dir).replace(os.sep, "/")
        if "_sampler" not in cache:
            cache["_sampler"] = g.add_sampler()      # un seul echantillonneur partage
        idx = g.add_texture(g.add_image(uri), cache["_sampler"])
        cache[ref] = idx
        return idx

    def _roughness(self, g, spec_ref, cache):
        """Build a roughness map from a specular/glossiness map."""
        if not spec_ref or any(d in spec_ref for d in DUMMY_TEX):
            return None
        key = ("rough", spec_ref)
        if key in cache:
            return cache[key]
        src = self.res.path(spec_ref)
        if not src:
            cache[key] = None
            return None
        rel = os.path.splitext(os.path.relpath(
            src, os.path.join(self.res.root, "textures")))[0]
        png = os.path.join(self.texdir, rel + "_rough.png")
        if not spec_to_roughness(src, png):
            cache[key] = None
            return None
        if "_sampler" not in cache:
            cache["_sampler"] = g.add_sampler()
        uri = os.path.relpath(png, self._out_dir).replace(os.sep, "/")
        idx = g.add_texture(g.add_image(uri), cache["_sampler"])
        cache[key] = idx
        return idx

    def _material(self, g, node, cache, matcache):
        key = (node.shader, tuple(sorted(node.textures.items())))
        if key in matcache:
            return matcache[key]
        roles = {}
        for sem, role in TEX_ROLES.items():
            if sem in node.textures:
                roles[role] = self._texture(g, node.textures[sem], cache)
        # SpecMap0 has no direct metallic-roughness equivalent, so we turn it
        # into a roughness map (green channel, roughness = 1 - glossiness),
        # which Godot reads natively through metallicRoughnessTexture.
        rough = self._roughness(g, node.textures.get("SpecMap0"), cache)
        # Transparency comes either from the shader or from a base-colour
        # texture that carries a real alpha channel.
        shader = (node.shader or "").split(":")[-1]
        alpha = None
        if shader in TRANSPARENT_SHADERS or "alpha" in shader:
            alpha = "BLEND"
        else:
            diff = node.textures.get("DiffMap0")
            if diff and not any(d in diff for d in DUMMY_TEX):
                src = self.res.path(diff)
                if src and has_alpha(src):
                    alpha = "BLEND"
        idx = g.add_material(f"{node.name}_{(node.shader or 'mat').split(':')[-1]}",
                             base_color_tex=roles.get("base"),
                             normal_tex=roles.get("normal"),
                             emissive_tex=roles.get("emissive"),
                             alpha_mode=alpha,
                             roughness_tex=rough)
        # Environment reflection is a scene-level notion in Godot
        # (WorldEnvironment), not a material one. Keep it as metadata.
        extras = {k: v for k, v in node.textures.items()
                  if k in ("CubeMap0", "SpecMap0", "DiffMap1")}
        if extras or node.shader:
            g.gltf["materials"][idx]["extras"] = {
                "nebula_shader": node.shader, "nebula_textures": extras}
        matcache[key] = idx
        return idx

    # ------------------------------------------------------------------- skeleton

    def _skeleton(self, g, char_node):
        """Create the joint nodes and the inverse-bind-matrix accessor.

        Returns (skin index, joint node list) or (None, []).
        """
        joints = char_node.joints
        if not joints:
            return None, []

        order = sorted(range(len(joints)), key=lambda i: joints[i].index)
        by_index = {joints[i].index: i for i in order}

        # Global bind-pose matrices, parents before children.
        local, glob = {}, {}
        for i in order:
            j = joints[i]
            local[i] = compose(j.translation, normalize_quat(j.rotation), j.scale)
        for i in order:
            j = joints[i]
            p = by_index.get(j.parent)
            glob[i] = local[i] if p is None else glob[p] @ local[i]

        # One glTF node per joint, then wire parents to children.
        node_of = {}
        for i in order:
            j = joints[i]
            node_of[i] = g.add_node(
                name=j.name or f"joint_{j.index}",
                translation=j.translation[:3],
                rotation=normalize_quat(j.rotation),
                scale=j.scale[:3])
        roots = []
        for i in order:
            p = by_index.get(joints[i].parent)
            if p is None:
                roots.append(node_of[i])
            else:
                g.gltf["nodes"][node_of[p]].setdefault("children", []).append(node_of[i])

        joint_nodes = [node_of[i] for i in order]
        ibms = []
        for i in order:
            try:
                ibms.append(tuple(np.linalg.inv(glob[i]).T.flatten()))
            except np.linalg.LinAlgError:
                # Zero-scaled joint: the matrix cannot be inverted.
                self.warn(f"singular bind matrix for '{joints[i].name}'")
                ibms.append(tuple(np.eye(4).flatten()))
        ibm_acc = g.add_accessor(ibms, FLOAT, "MAT4")
        skin = g.add_skin(char_node.name or "skeleton", joint_nodes, ibm_acc,
                          skeleton=roots[0] if roots else None)
        return skin, roots

    # --------------------------------------------------------------------- meshes

    def _mesh(self, g, node, meshcache, texcache, matcache):
        """Build (or reuse) a glTF mesh for one N3 node."""
        path = self.res.path(node.mesh)
        if not path:
            self.warn(f"mesh not found: {node.mesh}")
            return None, False
        material = self._material(g, node, texcache, matcache)
        group = max(0, node.prim_group)
        palette = dict(node.skin_fragments).get(group)
        key = (path, group, material, tuple(palette) if palette else None)
        if key in meshcache:
            return meshcache[key]
        skinned = False

        try:
            m = nvx2mod.load(path)
            d = nvx2mod.decode(m)
        except Exception as e:
            self.warn(f"NVX2 read failed {os.path.basename(path)}: {e}")
            return None, False
        if "positions" not in d or not m.indices:
            self.warn(f"mesh has no geometry: {os.path.basename(path)}")
            return None, False

        if group >= len(m.groups):
            self.warn(f"group {group} missing from {os.path.basename(path)}")
            return None, False
        gr = m.groups[group]
        start, count = gr.first_triangle * 3, gr.num_triangles * 3
        indices = m.indices[start:start + count]
        if not indices:
            return None, False

        # Compact: keep only the vertices this group actually uses.
        used = sorted(set(indices))
        remap = {v: i for i, v in enumerate(used)}
        local_idx = [remap[i] for i in indices]

        attrs = {"POSITION": g.add_accessor([d["positions"][v] for v in used],
                                            FLOAT, "VEC3", ARRAY_BUFFER, minmax=True)}
        if "normals" in d:
            attrs["NORMAL"] = g.add_accessor([d["normals"][v] for v in used],
                                             FLOAT, "VEC3", ARRAY_BUFFER)
        if "uv0" in d:
            attrs["TEXCOORD_0"] = g.add_accessor([d["uv0"][v] for v in used],
                                                 FLOAT, "VEC2", ARRAY_BUFFER)
        if "uv1" in d:
            attrs["TEXCOORD_1"] = g.add_accessor([d["uv1"][v] for v in used],
                                                 FLOAT, "VEC2", ARRAY_BUFFER)
        if "colors" in d:
            attrs["COLOR_0"] = g.add_accessor([d["colors"][v] for v in used],
                                              FLOAT, "VEC4", ARRAY_BUFFER)
        # glTF wants TANGENT as VEC4 with w = handedness. Without it the
        # importer regenerates tangents and the normal map can come out flipped.
        if "tangents" in d and "normals" in d:
            tans = []
            for v in used:
                t, nrm = d["tangents"][v], d["normals"][v]
                b = d.get("binormals", {})
                w = 1.0
                if "binormals" in d:
                    bn = d["binormals"][v]
                    cross = (nrm[1] * t[2] - nrm[2] * t[1],
                             nrm[2] * t[0] - nrm[0] * t[2],
                             nrm[0] * t[1] - nrm[1] * t[0])
                    w = -1.0 if sum(c * k for c, k in zip(cross, bn)) < 0.0 else 1.0
                tans.append((t[0], t[1], t[2], w))
            attrs["TANGENT"] = g.add_accessor(tans, FLOAT, "VEC4", ARRAY_BUFFER)

        # Skinning: the vertex indices point into the fragment's joint palette,
        # which in turn points into the CharacterNode's joint array.
        if palette is not None and "joints" in d and "weights" in d:
            jj, ww = [], []
            for v in used:
                raw, w = d["joints"][v], d["weights"][v]
                # glTF requires a zero index wherever the weight is zero.
                jj.append(tuple(palette[k] if (wt > 0.0 and k < len(palette)) else 0
                                for k, wt in zip(raw, w)))
                ww.append(tuple(float(x) for x in w))
            attrs["JOINTS_0"] = g.add_accessor(jj, USHORT, "VEC4", ARRAY_BUFFER)
            attrs["WEIGHTS_0"] = g.add_accessor(ww, FLOAT, "VEC4", ARRAY_BUFFER)
            skinned = True

        ctype = USHORT if len(used) < 65536 else UINT
        idx_acc = g.add_accessor(local_idx, ctype, "SCALAR", ELEMENT_ARRAY_BUFFER)
        prim = {"attributes": attrs, "indices": idx_acc, "material": material}
        mesh = g.add_mesh(f"{os.path.basename(path)[:-5]}_g{group}", [prim])
        meshcache[key] = (mesh, skinned)
        return mesh, skinned

    # ------------------------------------------------------------------ animations

    def _animations(self, g, char_node, joint_nodes, max_clips=None):
        """Skeletal animations: the ANIM resource, then the VART variations."""
        written = 0
        for ref, prefix in ((char_node.anim_resource, ""),
                            (char_node.variation_resource, "var_")):
            if ref:
                written += self._anim_file(g, ref, joint_nodes, max_clips, prefix)
        return written

    def _anim_file(self, g, ref, joint_nodes, max_clips, prefix):
        path = self.res.path(ref)
        if not path:
            self.warn(f"animations not found: {ref}")
            return 0

        try:
            clips = naxmod.load(path)
        except Exception as e:
            self.warn(f"NAX3 read failed {os.path.basename(path)}: {e}")
            return 0

        written = 0
        for clip in clips[:max_clips] if max_clips else clips:
            channels, samplers, times_cache = [], [], {}
            for jslot in range(min(clip.num_joints, len(joint_nodes))):
                ch, sm = self._trs_channels(g, clip, joint_nodes[jslot],
                                            group=jslot, times_cache=times_cache)
                base = len(samplers)
                for c in ch:
                    c["sampler"] += base
                channels += ch
                samplers += sm
            if channels:
                # post_infinity != 0 means the curve continues as a cycle.
                loop = LOOP_SUFFIX if clip.post_infinity else ""
                g.add_animation(prefix + (clip.name or f"clip_{written}") + loop,
                                channels, samplers)
                written += 1
        return written

    @staticmethod
    def _subtree_filter(model, only_subtrees):
        """Predicate: is this node kept, judging by its subtree root?"""
        if not only_subtrees:
            return lambda i: True
        wanted = set(only_subtrees)
        # A subtree root is an ancestor whose parent is the CharacterNode
        # (or the file root).
        top = {}
        for i, nd in enumerate(model.nodes):
            chain, cur = [], i
            while cur >= 0:
                chain.append(cur)
                parent = model.nodes[cur].parent
                if parent < 0 or model.nodes[parent].type == CHARACTER_NODE:
                    break
                cur = parent
            top[i] = chain[-1] if chain else i
        return lambda i: (model.nodes[i].type == CHARACTER_NODE
                          or model.nodes[top[i]].name in wanted)

    # ------------------------------------------------------ MANI animations

    @staticmethod
    def _node_paths(model):
        """Map 'name/name/name' -> N3 node index, as used by the ANNO tags."""
        children = {}
        for i, nd in enumerate(model.nodes):
            children.setdefault(nd.parent, []).append(i)
        paths = {}
        stack = [(i, "") for i in children.get(-1, [])]
        while stack:
            idx, prefix = stack.pop()
            name = model.nodes[idx].name
            path = f"{prefix}/{name}" if prefix else name
            paths.setdefault(path, idx)
            for c in children.get(idx, []):
                stack.append((c, path))
        return paths

    def _trs_channels(self, g, clip, gltf_node, group=0, times_cache=None):
        """Emit the translation/rotation/scale channels of one curve group.

        80% of the game's curves are flagged static. Writing them repeated on
        every frame inflated the files about fourfold for nothing: a constant
        curve only needs its value at the clip bounds.
        """
        frames = max(1, clip.num_keys)
        if frames < 2:
            return [], []
        track = clip.joint_track(group)
        if not track:
            return [], []

        # Time accessors shared across the whole clip.
        if times_cache is None:
            times_cache = {}
        if "full" not in times_cache:
            times = [f * clip.key_duration / naxmod.TICKS_PER_SEC
                     for f in range(frames)]
            times_cache["full"] = g.add_accessor(times, FLOAT, "SCALAR", minmax=True)
            # A constant curve keeps TWO keys, at the clip bounds. With only
            # one, a clip whose every channel is constant would collapse to a
            # zero duration -- observed on the pose-variation clips.
            times_cache["const"] = g.add_accessor([times[0], times[-1]],
                                                  FLOAT, "SCALAR", minmax=True)

        channels, samplers = [], []
        for chan, values in track.items():
            first = values[0]
            constant = all(v == first for v in values)
            vals = [first, first] if constant else values
            t_acc = times_cache["const" if constant else "full"]
            if chan == "rotation":
                acc = g.add_accessor([normalize_quat(v) for v in vals], FLOAT, "VEC4")
            else:
                acc = g.add_accessor([(v[0], v[1], v[2]) for v in vals], FLOAT, "VEC3")
            samplers.append({"input": t_acc, "output": acc, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1,
                             "target": {"node": gltf_node, "path": chan}})
        return channels, samplers

    def _animator_animations(self, g, model, node_of):
        """Transform animations carried by the MANI animator nodes.

        A TransformCurveAnimator section names a target node (ANNO tag) and a
        clip inside the .nax3 (SANI tag), picked by 'anim_group'. Every section
        pointing at the same .nax3 becomes a single glTF animation, since they
        make up one overall movement.
        """
        paths = self._node_paths(model)
        by_resource = {}
        skipped = {"float": 0, "uv": 0, "other": 0}
        for nd in model.nodes:
            if nd.type != ANIMATOR_NODE:
                continue
            for sect in nd.anim_sections:
                kind = sect.get("node_type")
                if kind != TRANSFORM_CURVE_ANIMATOR:
                    key = ("float" if kind == FLOAT_ANIMATOR
                           else "uv" if kind == UV_ANIMATOR else "other")
                    skipped[key] += 1
                    continue
                ref = sect.get("anim_resource")
                target = sect.get("target_node")
                if not ref or not target:
                    continue
                by_resource.setdefault(ref, []).append(sect)

        written = 0
        for ref, sects in by_resource.items():
            path = self.res.path(ref)
            if not path:
                self.warn(f"animator nax3 not found: {ref}")
                continue
            try:
                clips = naxmod.load(path)
            except Exception as e:
                self.warn(f"NAX3 read failed {os.path.basename(path)}: {e}")
                continue

            channels, samplers, loop = [], [], False
            for sect in sects:
                n3_index = paths.get(sect["target_node"])
                if n3_index is None:
                    # Fallback: last path segment, since names are unique in
                    # most models.
                    n3_index = paths.get(sect["target_node"].rsplit("/", 1)[-1])
                if n3_index is None or n3_index not in node_of:
                    self.warn(f"unresolved animator target: {sect['target_node']}")
                    continue
                ci = sect.get("anim_group", 0) or 0
                if not (0 <= ci < len(clips)):
                    self.warn(f"clip {ci} missing from {os.path.basename(path)}")
                    continue
                ch, sm = self._trs_channels(g, clips[ci], node_of[n3_index])
                base = len(samplers)
                for c in ch:
                    c["sampler"] += base
                channels += ch
                samplers += sm
                if str(sect.get("loop_type", "")).lower() == "loop":
                    loop = True

            if channels:
                name = os.path.splitext(os.path.basename(path))[0]
                g.add_animation(name + (LOOP_SUFFIX if loop else ""), channels, samplers)
                written += 1
        return written, skipped

    # ------------------------------------------------------------------ pipeline

    def export(self, n3_path, out_path=None, max_clips=None,
               only_subtrees=None, with_animations=True, with_meshes=True):
        """Export a .n3 to a .glb.

        only_subtrees: keep only the subtrees whose root (under the
        CharacterNode) carries one of these names. Used to split a player
        character, whose single .n3 holds thousands of parts.
        """
        model = n3mod.load(n3_path)
        name = os.path.splitext(os.path.basename(n3_path))[0]
        out_path = out_path or os.path.join(self.outdir, name + ".glb")
        self._out_dir = os.path.dirname(os.path.abspath(out_path))
        g = GLB(generator="dso-export (Nebula3 -> glTF)")
        texcache, matcache, meshcache = {}, {}, {}

        chars = model.find(CHARACTER_NODE)
        skin, skel_roots = (None, [])
        joint_nodes = []
        if chars:
            skin, skel_roots = self._skeleton(g, chars[0])
            if skin is not None:
                joint_nodes = g.gltf["skins"][skin]["joints"]

        # Mirror the N3 hierarchy, emitting only the nodes we need.
        keep = self._subtree_filter(model, only_subtrees)

        node_of, scene_roots = {}, list(skel_roots)
        for i, nd in enumerate(model.nodes):
            if not keep(i):
                continue
            mesh, has_joints = None, False
            if with_meshes and nd.type in MESH_NODES and nd.mesh:
                mesh, has_joints = self._mesh(g, nd, meshcache, texcache, matcache)
            # Attach the skin only when the mesh really carries JOINTS_0,
            # otherwise glTF reports NODE_SKIN_WITH_NON_SKINNED_MESH (an error).
            skinned = (mesh is not None and has_joints and skin is not None)
            if skinned:
                # Root node with no local transform: the pose comes from the skin.
                gi = g.add_node(name=nd.name or f"node_{i}", mesh=mesh, skin=skin)
                node_of[i] = gi
                scene_roots.append(gi)
                continue
            gi = g.add_node(name=nd.name or f"node_{i}",
                            translation=nd.position[:3],
                            rotation=normalize_quat(nd.rotation),
                            scale=nd.scale[:3],
                            mesh=mesh)
            node_of[i] = gi
            if nd.parent in node_of:
                g.gltf["nodes"][node_of[nd.parent]].setdefault("children", []).append(gi)
            else:
                scene_roots.append(gi)

        # Particle emitters have no glTF equivalent, so their parameters go to
        # a sidecar JSON file to be rebuilt inside Godot.
        fx = []
        for i, nd in enumerate(model.nodes):
            if not with_meshes:
                break          # skeleton-only export: no FX to describe
            if nd.type != PARTICLE_NODE and not nd.particle:
                continue
            # Honour the split: without this filter every part of a player
            # character carried the whole model's emitters.
            if i not in node_of:
                continue
            fx.append({"node": nd.name, "gltf_node": node_of.get(i),
                       "parent": model.nodes[nd.parent].name if nd.parent >= 0 else None,
                       "mesh": nd.mesh, "shader": nd.shader,
                       "textures": nd.textures, "emitter": nd.particle})

        n_anims, n_mani, skipped = 0, 0, {}
        if with_animations:
            if chars and joint_nodes:
                n_anims = self._animations(g, chars[0], joint_nodes, max_clips)
            # Animator animations also exist on models with no skeleton.
            n_mani, skipped = self._animator_animations(g, model, node_of)
            n_anims += n_mani

        g.gltf["scenes"][0]["nodes"] = scene_roots
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        size = g.save(out_path)
        if fx:
            import json
            with open(os.path.splitext(out_path)[0] + ".fx.json", "w") as f:
                json.dump({"model": model.name, "emitters": fx}, f, indent=1)
        return {"out": out_path, "bytes": size, "fx": len(fx),
                "mani": n_mani, "skipped_animators": skipped,
                "nodes": len(g.gltf["nodes"]),
                "meshes": len(g.gltf.get("meshes", [])),
                "materials": len(g.gltf.get("materials", [])),
                "joints": len(joint_nodes), "animations": n_anims,
                "warnings": list(self.warnings)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("n3", nargs="+", help=".n3 file(s) to export")
    ap.add_argument("--root", default="extracted/export_win32")
    ap.add_argument("--out", default="godot_export")
    ap.add_argument("--max-clips", type=int, default=None,
                    help="cap the number of clips (for testing)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    ex = Exporter(a.root, a.out, verbose=a.verbose)
    for p in a.n3:
        ex.warnings.clear()
        try:
            r = ex.export(p, max_clips=a.max_clips)
        except Exception as e:
            print(f"FAILED {os.path.basename(p)}: {type(e).__name__}: {e}")
            continue
        print(f"OK {os.path.basename(r['out']):45} "
              f"{r['bytes']/1024:8.0f} KB  nodes={r['nodes']:4} meshes={r['meshes']:3} "
              f"mat={r['materials']:3} bones={r['joints']:3} anims={r['animations']:3}")
        for w in r["warnings"][:6]:
            print(f"    ! {w}")
        if len(r["warnings"]) > 6:
            print(f"    ! ... and {len(r['warnings'])-6} more warnings")


if __name__ == "__main__":
    main()
