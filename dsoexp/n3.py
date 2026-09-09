"""N3 model reader (Nebula3 scene graph).

The file is a stream of FourCC tags with NO length field: every payload size
is implicit. An unknown tag therefore desynchronises the whole rest of the
file, which is why the tag table below has to be exhaustive.

FourCCs appear packed either way round ('MESH' / 'HSEM'); we normalise on read.
"""
import struct
from dataclasses import dataclass, field

MAGIC = (b"NEB3", b"3BEN")


class N3Error(Exception):
    pass


class Reader:
    def __init__(self, data, path="<memoire>"):
        self.d, self.p, self.path = data, 0, path

    def _take(self, n):
        if self.p + n > len(self.d):
            raise N3Error(f"{self.path}: {n}-byte read out of bounds at {self.p}")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def eof(self):
        return self.p >= len(self.d)

    def fourcc(self):
        return self._take(4).decode("latin-1")

    def u8(self):
        return self._take(1)[0]

    def u16(self):
        return struct.unpack("<H", self._take(2))[0]

    def i32(self):
        return struct.unpack("<i", self._take(4))[0]

    def f32(self):
        return struct.unpack("<f", self._take(4))[0]

    def vec(self, n):
        return struct.unpack(f"<{n}f", self._take(4 * n))

    def string(self):
        n = self.u16()
        return self._take(n).decode("utf-8", "replace") if n else ""


@dataclass
class Joint:
    index: int = 0
    parent: int = -1
    translation: tuple = (0.0, 0.0, 0.0, 1.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    scale: tuple = (1.0, 1.0, 1.0, 1.0)
    name: str = ""


@dataclass
class Node:
    name: str = ""
    type: str = ""
    parent: int = -1
    position: tuple = (0.0, 0.0, 0.0, 1.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    scale: tuple = (1.0, 1.0, 1.0, 1.0)
    rotate_pivot: tuple = (0.0, 0.0, 0.0, 0.0)
    scale_pivot: tuple = (0.0, 0.0, 0.0, 0.0)
    mesh: str = ""
    prim_group: int = -1
    shader: str = ""
    textures: dict = field(default_factory=dict)
    ints: dict = field(default_factory=dict)
    floats: dict = field(default_factory=dict)
    vec4s: dict = field(default_factory=dict)
    joints: list = field(default_factory=list)
    skin_fragments: list = field(default_factory=list)   # (prim_group, [joint indices])
    anim_resource: str = ""
    variation_resource: str = ""
    skin_lists: list = field(default_factory=list)       # (name, [skins], variation)
    box_center: tuple = None
    box_extents: tuple = None
    min_distance: float = None
    max_distance: float = None
    particle: dict = field(default_factory=dict)
    anim_sections: list = field(default_factory=list)


@dataclass
class Model:
    name: str = ""
    type: str = ""
    version: int = 1
    nodes: list = field(default_factory=list)

    def children_of(self, index):
        return [i for i, n in enumerate(self.nodes) if n.parent == index]

    def find(self, node_type):
        return [n for n in self.nodes if n.type == node_type]


# --- Fixed-payload tags, decoded generically ---------------------------------

# Particle envelope curves: 8 f32 + 1 i32.
ENVELOPE = {
    "EFRQ": "emission_frequency", "PLFT": "lifetime",
    "PSMN": "spread_min", "PSMX": "spread_max",
    "PSVL": "start_velocity", "PRVL": "rotation_velocity",
    "PSZE": "particle_size", "PMSS": "particle_mass",
    "PTMN": "time_manipulator", "PVLF": "velocity_factor",
    "PAIR": "air_resistance", "PRED": "color_red",
    "PGRN": "color_green", "PBLU": "color_blue", "PALP": "color_alpha",
}
PART_FLOAT = {
    "PEDU": "emission_duration", "PACD": "activity_distance",
    "PRMN": "start_rotation_min", "PRMX": "start_rotation_max",
    "PGRV": "gravity", "PSTC": "stretch", "PTTX": "texture_tile",
    "PVRM": "velocity_randomize", "PRRM": "rotation_randomize",
    "PSRM": "size_randomize", "PPCT": "precalc_time", "PDEL": "start_delay",
}
PART_INT = {
    "PLPE": "looping", "PROF": "render_oldest_first", "PBBO": "billboard",
    "PSTS": "stretch_to_start", "PRRD": "randomize_rotation",
    "PVAF": "view_angle_fade", "PLSC": "curve_looping", "PSDL": "stretch_detail",
}
# Fixed-payload tags we do not need for the export, so we just skip them.
# SBLB/SSPR are documented nowhere: observed empirically to carry a single
# boolean byte (the next FourCC starts at +1).
SKIP = {"SVSP": 1, "SLKV": 1, "HRCH": 1, "CASH": 1, "SBLB": 1, "SSPR": 1}

# Every known tag, used to work out which way round a FourCC is packed.
KNOWN = (
    {">MDL", "<MDL", ">MND", "<MND", "EOF_",
     "POSI", "ROTN", "SCAL", "RPIV", "SPIV", "SMID", "SMAD", "LBOX", "BCLS",
     "MESH", "PGRI", "SHDR", "STXT", "SINT", "SFLT", "SVEC", "STUS", "SSPI",
     "NSKF", "SFRG", "NJNT", "JONT", "ANIM", "VART", "NSKL", "SKNL",
     "BASE", "SLPT", "ANNO", "SPNM", "SVCN", "ADPK", "ADEK", "ADSK",
     "SANI", "SAGR", "ADDK", "MNTP", "SSTA"}
    | set(ENVELOPE) | set(PART_FLOAT) | set(PART_INT) | set(SKIP)
)


# Class FourCCs (node and model types) use the same reversed packing as
# the tags.
NODE_TYPES = {"TRFN", "SPND", "STND", "CHRN", "CHSN", "PSND", "MANI",
              "MODL", "CHRC", "SKNL"}


def _normalize_class(fourcc):
    if fourcc in NODE_TYPES:
        return fourcc
    rev = fourcc[::-1]
    return rev if rev in NODE_TYPES else fourcc


def _normalize(tag):
    if tag in KNOWN:
        return tag
    rev = tag[::-1]
    return rev if rev in KNOWN else tag


def load(path):
    with open(path, "rb") as f:
        return loads(f.read(), path)


def loads(data, path="<memoire>"):
    r = Reader(data, path)
    magic = r._take(4)
    if magic not in MAGIC:
        raise N3Error(f"{path}: unexpected NEB3 magic {magic!r}")
    (version,) = struct.unpack("<I", r._take(4))
    if version not in (1, 2):
        # Would happen for a big-endian file; never observed in practice.
        raise N3Error(f"{path}: unsupported N3 version {version}")

    model = Model(version=version)
    stack = []
    cur = None

    while not r.eof():
        raw = r.fourcc()
        tag = _normalize(raw)

        if tag == "EOF_":
            break
        elif tag == ">MDL":
            model.type = _normalize_class(r.fourcc())
            model.name = r.string()
        elif tag == "<MDL":
            break
        elif tag == ">MND":
            node = Node(type=_normalize_class(r.fourcc()), name=r.string(),
                        parent=stack[-1] if stack else -1)
            model.nodes.append(node)
            stack.append(len(model.nodes) - 1)
            cur = node
        elif tag == "<MND":
            if not stack:
                raise N3Error(f"{path}: <MND without a matching >MND at {r.p}")
            stack.pop()
            cur = model.nodes[stack[-1]] if stack else None
        elif cur is None:
            raise N3Error(f"{path}: tag '{raw}' outside any node at {r.p}")

        # --- transform ---
        elif tag == "POSI":
            cur.position = r.vec(4)
        elif tag == "ROTN":
            cur.rotation = r.vec(4)
        elif tag == "SCAL":
            cur.scale = r.vec(4)
        elif tag == "RPIV":
            cur.rotate_pivot = r.vec(4)
        elif tag == "SPIV":
            cur.scale_pivot = r.vec(4)
        elif tag == "SMID":
            cur.min_distance = r.f32()
        elif tag == "SMAD":
            cur.max_distance = r.f32()
        elif tag in ("LBOX", "BCLS"):
            cur.box_center, cur.box_extents = r.vec(4), r.vec(4)

        # --- mesh and material ---
        elif tag == "MESH":
            cur.mesh = r.string()
        elif tag == "PGRI":
            cur.prim_group = r.i32()
        elif tag == "SHDR":
            cur.shader = r.string()
        elif tag == "STXT":
            k = r.string(); cur.textures[k] = r.string()
        elif tag == "SINT":
            k = r.string(); cur.ints[k] = r.i32()
        elif tag == "SFLT":
            k = r.string(); cur.floats[k] = r.f32()
        elif tag == "SVEC":
            k = r.string(); cur.vec4s[k] = r.vec(4)
        elif tag in ("STUS", "SSPI"):
            r.i32(); r.vec(4)

        # --- skeleton and skinning ---
        elif tag == "NSKF":
            r.i32()
        elif tag == "SFRG":
            pg = r.i32()
            cur.skin_fragments.append((pg, [r.i32() for _ in range(r.i32())]))
        elif tag == "NJNT":
            r.i32()
        elif tag == "JONT":
            j = Joint(index=r.i32(), parent=r.i32())
            j.translation, j.rotation, j.scale = r.vec(4), r.vec(4), r.vec(4)
            j.name = r.string()
            cur.joints.append(j)
        elif tag == "ANIM":
            cur.anim_resource = r.string()
        elif tag == "VART":
            cur.variation_resource = r.string()
        elif tag == "NSKL":
            r.i32()
        elif tag == "SKNL":
            name = r.string()
            skins = [r.string() for _ in range(r.i32())]
            # Optional variation flag at the end of the block.
            variation = ""
            if not r.eof():
                save = r.p
                flag = r.u8()
                if flag in (0, 1):
                    if flag:
                        variation = r.string()
                else:
                    r.p = save
            cur.skin_lists.append((name, skins, variation))

        # --- animators ---
        elif tag == "BASE":
            cur.anim_sections.append({"node_type": r.i32()})
        elif tag == "SLPT":
            _sect(cur, path)["loop_type"] = r.string()
        elif tag == "ANNO":
            # Path of the target node in the hierarchy
            # ('model/xxx/polySurface69'), NOT a resource: SANI holds the .nax3.
            _sect(cur, path)["target_node"] = r.string()
        elif tag in ("SPNM", "SVCN"):
            _sect(cur, path)["semantic"] = r.string()
        elif tag in ("ADPK", "ADEK", "ADSK"):
            # Animator position / euler / scale keys. Other readers treat
            # these as empty, but they do carry a payload: i32 count, then
            # count * 24 bytes (pad f32, time f32, 4*f32 value).
            # Checked against samples: the gap is exactly 4 + 24 * count.
            count = r.i32()
            if count < 0 or count > 1_000_000:
                raise N3Error(f"{path}: invalid {tag} key count ({count})")
            keys = []
            for _ in range(count):
                r.f32()                      # leading field, always 0.0 in practice
                keys.append((r.f32(), r.vec(4)))
            _sect(cur, path)[tag] = keys
        elif tag == "SANI":
            _sect(cur, path)["anim_resource"] = r.string()
        elif tag == "SAGR":
            _sect(cur, path)["anim_group"] = r.i32()
        elif tag == "ADDK":
            kind, count = r.string(), r.i32()
            keys = []
            for _ in range(count):
                t = r.f32()
                if kind == "Float":
                    keys.append((t, r.f32()))
                elif kind == "Float4":
                    keys.append((t, r.vec(4)))
                elif kind == "Int":
                    keys.append((t, r.i32()))
                else:
                    raise N3Error(f"{path}: unknown ADDK key type '{kind}'")
            _sect(cur, path).setdefault("keys", []).append((kind, keys))

        # --- particles ---
        elif tag in ENVELOPE:
            v = r.vec(8)
            cur.particle[ENVELOPE[tag]] = {"values": v[:4], "keypos": v[4:6],
                                          "freq": v[6], "amp": v[7], "mod": r.i32()}
        elif tag in PART_FLOAT:
            cur.particle[PART_FLOAT[tag]] = r.f32()
        elif tag in PART_INT:
            cur.particle[PART_INT[tag]] = r.i32()
        elif tag == "MNTP":
            cur.particle["type_name"] = r.string()
        elif tag == "SSTA":
            k = r.string()
            cur.particle.setdefault("attrs", {})[k] = r.string()
        elif tag in SKIP:
            r._take(SKIP[tag])
        else:
            raise N3Error(f"{path}: unknown tag '{raw}' at offset {r.p - 4}")

    return model


def _sect(node, path):
    if not node.anim_sections:
        raise N3Error(f"{path}: animator tag before any BASE")
    return node.anim_sections[-1]
