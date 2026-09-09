"""Minimal but spec-compliant binary glTF 2.0 (.glb) writer.

Covers what Godot imports: node hierarchy, skinned meshes, PBR materials with
external textures, skeletons and animations.
"""
import json
import struct

BYTE, UBYTE, SHORT, USHORT, UINT, FLOAT = 5120, 5121, 5122, 5123, 5125, 5126
_CSIZE = {BYTE: 1, UBYTE: 1, SHORT: 2, USHORT: 2, UINT: 4, FLOAT: 4}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_FMT = {BYTE: "b", UBYTE: "B", SHORT: "h", USHORT: "H", UINT: "I", FLOAT: "f"}

ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


class GLB:
    def __init__(self, generator="dso-export"):
        self.gltf = {
            "asset": {"version": "2.0", "generator": generator},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [], "meshes": [], "materials": [], "textures": [],
            "images": [], "samplers": [], "accessors": [], "bufferViews": [],
            "skins": [], "animations": [],
        }
        self.bin = bytearray()

    # --- binary buffer ---

    def _align(self, n=4):
        while len(self.bin) % n:
            self.bin.append(0)

    def add_view(self, data, target=None, stride=None):
        self._align()
        offset = len(self.bin)
        self.bin += data
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        if stride:
            view["byteStride"] = stride
        self.gltf["bufferViews"].append(view)
        return len(self.gltf["bufferViews"]) - 1

    def add_accessor(self, values, comp_type, type_str, target=None,
                     normalized=False, minmax=False):
        """`values` is a list of tuples (or of scalars for SCALAR)."""
        n = _NCOMP[type_str]
        flat = []
        for v in values:
            if n == 1:
                flat.append(v)
            else:
                flat.extend(v)
        data = struct.pack(f"<{len(flat)}{_FMT[comp_type]}", *flat)
        view = self.add_view(data, target=target)
        acc = {"bufferView": view, "componentType": comp_type,
               "count": len(values), "type": type_str}
        if normalized:
            acc["normalized"] = True
        if minmax and values:
            if n == 1:
                acc["min"], acc["max"] = [min(flat)], [max(flat)]
            else:
                cols = list(zip(*values))
                acc["min"] = [min(c) for c in cols]
                acc["max"] = [max(c) for c in cols]
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    # --- scene elements ---

    def add_node(self, name=None, translation=None, rotation=None, scale=None,
                 children=None, mesh=None, skin=None):
        node = {}
        if name:
            node["name"] = name
        if translation and tuple(translation) != (0.0, 0.0, 0.0):
            node["translation"] = [float(x) for x in translation]
        if rotation and tuple(rotation) != (0.0, 0.0, 0.0, 1.0):
            node["rotation"] = [float(x) for x in rotation]
        if scale and tuple(scale) != (1.0, 1.0, 1.0):
            node["scale"] = [float(x) for x in scale]
        if children:
            node["children"] = children
        if mesh is not None:
            node["mesh"] = mesh
        if skin is not None:
            node["skin"] = skin
        self.gltf["nodes"].append(node)
        return len(self.gltf["nodes"]) - 1

    def add_image(self, uri):
        self.gltf["images"].append({"uri": uri})
        return len(self.gltf["images"]) - 1

    def add_texture(self, image_index, sampler_index=None):
        tex = {"source": image_index}
        if sampler_index is not None:
            tex["sampler"] = sampler_index
        self.gltf["textures"].append(tex)
        return len(self.gltf["textures"]) - 1

    def add_sampler(self, wrap_s=10497, wrap_t=10497):
        self.gltf["samplers"].append({"wrapS": wrap_s, "wrapT": wrap_t})
        return len(self.gltf["samplers"]) - 1

    def add_material(self, name, base_color_tex=None, normal_tex=None,
                     emissive_tex=None, alpha_mode=None, double_sided=True,
                     roughness_tex=None):
        pbr = {"metallicFactor": 0.0, "roughnessFactor": 0.8}
        if roughness_tex is not None:
            # G = roughness, B = metalness (cancelled by metallicFactor = 0).
            pbr["metallicRoughnessTexture"] = {"index": roughness_tex}
            pbr["roughnessFactor"] = 1.0
        if base_color_tex is not None:
            pbr["baseColorTexture"] = {"index": base_color_tex}
        mat = {"name": name, "pbrMetallicRoughness": pbr,
               "doubleSided": bool(double_sided)}
        if normal_tex is not None:
            mat["normalTexture"] = {"index": normal_tex}
        if emissive_tex is not None:
            mat["emissiveTexture"] = {"index": emissive_tex}
            mat["emissiveFactor"] = [1.0, 1.0, 1.0]
        if alpha_mode:
            mat["alphaMode"] = alpha_mode
        self.gltf["materials"].append(mat)
        return len(self.gltf["materials"]) - 1

    def add_mesh(self, name, primitives):
        self.gltf["meshes"].append({"name": name, "primitives": primitives})
        return len(self.gltf["meshes"]) - 1

    def add_skin(self, name, joint_nodes, ibm_accessor, skeleton=None):
        skin = {"name": name, "joints": joint_nodes,
                "inverseBindMatrices": ibm_accessor}
        if skeleton is not None:
            skin["skeleton"] = skeleton
        self.gltf["skins"].append(skin)
        return len(self.gltf["skins"]) - 1

    def add_animation(self, name, channels, samplers):
        self.gltf["animations"].append(
            {"name": name, "channels": channels, "samplers": samplers})
        return len(self.gltf["animations"]) - 1

    # --- output ---

    def save(self, path):
        self.gltf["buffers"] = [{"byteLength": len(self.bin)}]
        # glTF forbids empty arrays: drop the sections we never filled.
        for key in ("materials", "textures", "images", "samplers", "skins",
                    "animations", "meshes", "accessors", "bufferViews"):
            if not self.gltf[key]:
                del self.gltf[key]

        js = json.dumps(self.gltf, separators=(",", ":")).encode("utf-8")
        js += b" " * ((4 - len(js) % 4) % 4)
        bindata = bytes(self.bin) + b"\0" * ((4 - len(self.bin) % 4) % 4)
        total = 12 + 8 + len(js) + 8 + len(bindata)
        with open(path, "wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, total))
            f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
            f.write(struct.pack("<I4s", len(bindata), b"BIN\0")); f.write(bindata)
        return total
