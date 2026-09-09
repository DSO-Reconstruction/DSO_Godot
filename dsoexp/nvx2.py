"""NVX2 mesh reader (Nebula2/Nebula3).

Header: 'NVX2' + 6 u32 (numGroups, numVertices, vertexWidth in *floats*,
numTriangles, numEdges, componentMask), then numGroups * 6 u32 groups, then
the interleaved vertex block, then the indices (u16 or u32).
"""
import struct
from dataclasses import dataclass, field

# Vertex component mask bits, with their size in bytes.
COORD, NORMAL, NORMAL_UB4N = 1 << 0, 1 << 1, 1 << 2
UV0, UV0_S2, UV1, UV1_S2 = 1 << 3, 1 << 4, 1 << 5, 1 << 6
UV2, UV2_S2, UV3, UV3_S2 = 1 << 7, 1 << 8, 1 << 9, 1 << 10
COLOR, COLOR_UB4N = 1 << 11, 1 << 12
TANGENT, TANGENT_UB4N = 1 << 13, 1 << 14
BINORMAL, BINORMAL_UB4N = 1 << 15, 1 << 16
WEIGHTS, WEIGHTS_UB4N = 1 << 17, 1 << 18
JINDICES, JINDICES_UB4 = 1 << 19, 1 << 20

# Declaration order is the order the components appear in a vertex.
LAYOUT = [
    (COORD, 12), (NORMAL, 12), (NORMAL_UB4N, 4),
    (UV0, 8), (UV0_S2, 4), (UV1, 8), (UV1_S2, 4),
    (UV2, 8), (UV2_S2, 4), (UV3, 8), (UV3_S2, 4),
    (COLOR, 16), (COLOR_UB4N, 4),
    (TANGENT, 12), (TANGENT_UB4N, 4),
    (BINORMAL, 12), (BINORMAL_UB4N, 4),
    (WEIGHTS, 16), (WEIGHTS_UB4N, 4),
    (JINDICES, 16), (JINDICES_UB4, 4),
]


@dataclass
class Group:
    first_vertex: int
    num_vertices: int
    first_triangle: int
    num_triangles: int
    first_edge: int
    num_edges: int


@dataclass
class Nvx2:
    groups: list = field(default_factory=list)
    num_vertices: int = 0
    stride: int = 0
    comp_mask: int = 0
    offsets: dict = field(default_factory=dict)
    vbuf: bytes = b""
    indices: list = field(default_factory=list)

    def has(self, comp):
        return comp in self.offsets

    def component(self, comp, index):
        """Raw bytes of component `comp` for vertex `index`."""
        base = index * self.stride + self.offsets[comp]
        return self.vbuf[base:base + dict(LAYOUT)[comp]]


def _s2(v):
    """Fixed-point int16 UV: divide by 8192 (Nebula's fps2float)."""
    return v / 8192.0


def _unit(v):
    """Renormalise a vector.

    The UB4N encoding stores one byte per axis, which leaves normals about 1%
    off unit length -- and glTF rejects non-unit normals.
    """
    n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    return (0.0, 0.0, 1.0) if n < 1e-6 else (v[0] / n, v[1] / n, v[2] / n)


def load(path):
    with open(path, "rb") as f:
        data = f.read()
    return loads(data, path)


def loads(data, path="<memoire>"):
    if len(data) < 28:
        raise ValueError(f"{path}: too short to be an NVX2")
    magic = data[:4]
    # 'NVX2' shows up either as-is or as '2XVN': same FourCC, packed the other
    # way round. Either way the integers stay little-endian -- reading them as
    # big-endian because of the reversed magic is the classic trap here.
    if magic not in (b"NVX2", b"2XVN"):
        raise ValueError(f"{path}: unexpected NVX2 magic {magic!r}")
    e = "<"
    n_groups, n_verts, width, n_tris, _n_edges, mask = struct.unpack_from(e + "6I", data, 4)

    off = 28
    groups = []
    for _ in range(n_groups):
        groups.append(Group(*struct.unpack_from(e + "6I", data, off)))
        off += 24

    stride = width * 4
    offsets, cur = {}, 0
    for comp, size in LAYOUT:
        if mask & comp:
            offsets[comp] = cur
            cur += size
    if cur != stride:
        # Mask and vertexWidth must agree or the whole vertex block misdecodes.
        raise ValueError(
            f"{path}: mask {mask:#x} implies {cur} bytes but stride is {stride}")

    vsize = n_verts * stride
    vbuf = data[off:off + vsize]
    if len(vbuf) != vsize:
        raise ValueError(f"{path}: truncated vertex data")
    off += vsize

    # Index count: upper bound of the triangles referenced by the groups.
    max_tri = max((g.first_triangle + g.num_triangles for g in groups), default=0)
    n_idx = max_tri * 3 or n_tris
    remain = len(data) - off
    if n_idx:
        need16, need32 = n_idx * 2, n_idx * 4
        if remain == need32 or (remain >= need32 and n_verts >= 65536):
            indices = list(struct.unpack_from(e + f"{n_idx}I", data, off))
        elif remain >= need16:
            indices = list(struct.unpack_from(e + f"{n_idx}H", data, off))
        else:
            raise ValueError(f"{path}: truncated indices ({remain} bytes for {n_idx})")
    else:
        indices = []

    return Nvx2(groups=groups, num_vertices=n_verts, stride=stride, comp_mask=mask,
                offsets=offsets, vbuf=vbuf, indices=indices)


def decode(m):
    """Decode an NVX2 into per-vertex arrays, ready for glTF.

    Returns a dict of lists: positions, normals, tangents, binormals, uv0..uv3,
    colors, joints, weights. Keys the mesh does not carry are simply absent.
    """
    n, out = m.num_vertices, {}
    vb, st, of = m.vbuf, m.stride, m.offsets

    if COORD in of:
        o = of[COORD]
        out["positions"] = [struct.unpack_from("<3f", vb, i * st + o) for i in range(n)]
    if NORMAL in of:
        o = of[NORMAL]
        out["normals"] = [_unit(struct.unpack_from("<3f", vb, i * st + o)) for i in range(n)]
    elif NORMAL_UB4N in of:
        o = of[NORMAL_UB4N]
        out["normals"] = [_unit(tuple(b / 127.5 - 1.0 for b in vb[i * st + o:i * st + o + 3]))
                        for i in range(n)]
    if TANGENT in of:
        o = of[TANGENT]
        out["tangents"] = [_unit(struct.unpack_from("<3f", vb, i * st + o)) for i in range(n)]
    elif TANGENT_UB4N in of:
        o = of[TANGENT_UB4N]
        out["tangents"] = [_unit(tuple(b / 127.5 - 1.0 for b in vb[i * st + o:i * st + o + 3]))
                        for i in range(n)]
    if BINORMAL in of:
        o = of[BINORMAL]
        out["binormals"] = [_unit(struct.unpack_from("<3f", vb, i * st + o)) for i in range(n)]
    elif BINORMAL_UB4N in of:
        o = of[BINORMAL_UB4N]
        out["binormals"] = [_unit(tuple(b / 127.5 - 1.0 for b in vb[i * st + o:i * st + o + 3]))
                        for i in range(n)]

    for key, full, packed in (("uv0", UV0, UV0_S2), ("uv1", UV1, UV1_S2),
                              ("uv2", UV2, UV2_S2), ("uv3", UV3, UV3_S2)):
        if full in of:
            o = of[full]
            out[key] = [struct.unpack_from("<2f", vb, i * st + o) for i in range(n)]
        elif packed in of:
            o = of[packed]
            out[key] = [tuple(_s2(v) for v in struct.unpack_from("<2h", vb, i * st + o))
                        for i in range(n)]

    if COLOR in of:
        o = of[COLOR]
        out["colors"] = [struct.unpack_from("<4f", vb, i * st + o) for i in range(n)]
    elif COLOR_UB4N in of:
        o = of[COLOR_UB4N]
        out["colors"] = [tuple(b / 255.0 for b in vb[i * st + o:i * st + o + 4])
                         for i in range(n)]

    if WEIGHTS in of:
        o = of[WEIGHTS]
        out["weights"] = [struct.unpack_from("<4f", vb, i * st + o) for i in range(n)]
    elif WEIGHTS_UB4N in of:
        o = of[WEIGHTS_UB4N]
        raw = [tuple(b / 255.0 for b in vb[i * st + o:i * st + o + 4]) for i in range(n)]
        out["weights"] = [tuple(w / s for w in v) if (s := sum(v)) > 0 else (1.0, 0, 0, 0)
                          for v in raw]
    if JINDICES in of:
        o = of[JINDICES]
        out["joints"] = [tuple(int(v) for v in struct.unpack_from("<4f", vb, i * st + o))
                         for i in range(n)]
    elif JINDICES_UB4 in of:
        o = of[JINDICES_UB4]
        out["joints"] = [tuple(vb[i * st + o:i * st + o + 4]) for i in range(n)]

    return out
