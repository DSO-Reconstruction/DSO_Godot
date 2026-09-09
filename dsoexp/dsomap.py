""".map reader: object placement for a DSO map.

A sequence of sections, each tagged by a reversed FourCC ('MOSD' = 'DSOM'):
DSOM/version, MAPI, STRT, SETT, EVET, EMAL, TMPL, INST, GROP, NAVB.

Templates reference the string table by id, and that is what ties an instance
to its .n3 model.
"""
import struct
from dataclasses import dataclass, field

VERSION = 5


class MapError(Exception):
    pass


class _R:
    def __init__(self, data, path):
        self.d, self.p, self.path = data, 0, path

    def take(self, n):
        if self.p + n > len(self.d):
            raise MapError(f"{self.path}: {n}-byte read out of bounds at {self.p}")
        b = self.d[self.p:self.p + n]; self.p += n
        return b

    def tag(self, expected):
        raw = self.take(4).decode("latin-1")
        # The FourCC is stored reversed.
        if raw[::-1] != expected:
            raise MapError(f"{self.path}: expected '{expected}', found '{raw[::-1]}' at {self.p-4}")

    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def i16(self): return struct.unpack("<h", self.take(2))[0]
    def u16(self): return struct.unpack("<H", self.take(2))[0]
    def f32(self): return struct.unpack("<f", self.take(4))[0]
    def vec4(self): return struct.unpack("<4f", self.take(16))
    def boolean(self): return self.take(1)[0] != 0

    def string(self):
        n = struct.unpack("<H", self.take(2))[0]
        return self.take(n).decode("utf-8", "replace") if n else ""


@dataclass
class Template:
    gfx_res_id: int = 0
    gfx_root_node: int = 0
    phx_res_id: int = 0
    coll_res_id: int = 0
    sfx_event_id: int = 0
    center: tuple = (0, 0, 0, 0)
    extents: tuple = (0, 0, 0, 0)
    coll_mesh_group_index: int = 0
    type: int = 0


@dataclass
class Instance:
    pos: tuple = (0, 0, 0, 0)
    rot: tuple = (0, 0, 0, 1)
    scale: tuple = (1, 1, 1, 0)
    use_scaling: bool = False
    use_collide: bool = False
    visible_for_nav: bool = False
    template_index: int = -1
    group_index: int = -1
    name_index: int = 0
    mapping_index: int = 0


@dataclass
class Group:
    name_id: int = 0
    type: int = 0
    parent: int = -1
    instances: list = field(default_factory=list)
    groups: list = field(default_factory=list)


@dataclass
class MapData:
    path: str = ""
    grid_size: tuple = (0, 0, 0, 0)
    center: tuple = (0, 0, 0, 0)
    extents: tuple = (0, 0, 0, 0)
    size: tuple = (0, 0, 0)
    strings: list = field(default_factory=list)
    events: list = field(default_factory=list)
    event_mapping: list = field(default_factory=list)
    templates: list = field(default_factory=list)
    instances: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    nav_blockers: list = field(default_factory=list)

    def string(self, index):
        return self.strings[index] if 0 <= index < len(self.strings) else ""

    def model_of(self, instance):
        """Resource path of the model an instance carries ('' when none)."""
        if not (0 <= instance.template_index < len(self.templates)):
            return ""
        return self.string(self.templates[instance.template_index].gfx_res_id)


def load(path):
    with open(path, "rb") as f:
        data = f.read()
    r = _R(data, path)
    m = MapData(path=path)

    r.tag("DSOM")
    version = r.u32()
    if version != VERSION:
        raise MapError(f"{path}: map version {version}, expected {VERSION}")

    r.tag("MAPI")
    m.grid_size, m.center, m.extents = r.vec4(), r.vec4(), r.vec4()
    m.size = (r.i16(), r.i16(), r.i16())

    r.tag("STRT")
    n_str, table_size = r.u32(), r.u32()
    if n_str:
        buf = r.take(table_size)
        pos = 0
        for _ in range(n_str):
            if pos >= table_size:
                break
            end = buf.find(b"\0", pos)
            if end < 0:
                end = table_size
            m.strings.append(buf[pos:end].decode("utf-8", "replace"))
            pos = end + 1

    r.tag("SETT")
    for _ in range(r.u32()):
        r.u16(); r.u16()

    r.tag("EVET")
    m.events = [r.u32() for _ in range(r.u32())]

    r.tag("EMAL")
    m.event_mapping = [r.string() for _ in range(r.u32())]

    r.tag("TMPL")
    for _ in range(r.u32()):
        t = Template(gfx_res_id=r.u16(), gfx_root_node=r.u16(), phx_res_id=r.u16(),
                     coll_res_id=r.u16(), sfx_event_id=r.u16())
        t.center, t.extents = r.vec4(), r.vec4()
        t.coll_mesh_group_index, t.type = r.u16(), r.u16()
        m.templates.append(t)

    r.tag("INST")
    for _ in range(r.u32()):
        inst = Instance()
        inst.pos, inst.rot = r.vec4(), r.vec4()
        inst.use_scaling = r.boolean()
        inst.scale = r.vec4() if inst.use_scaling else (1.0, 1.0, 1.0, 0.0)
        inst.use_collide = r.boolean()
        inst.visible_for_nav = r.boolean()
        inst.template_index, inst.group_index = r.i16(), r.i16()
        inst.name_index, inst.mapping_index = r.u16(), r.u16()
        m.instances.append(inst)

    r.tag("GROP")
    for _ in range(r.u32()):
        gp = Group(name_id=r.u16(), type=r.u16(), parent=r.i16())
        ni, ng = r.u16(), r.u16()
        gp.instances = [r.u16() for _ in range(ni)]
        gp.groups = [r.u16() for _ in range(ng)]
        m.groups.append(gp)

    r.tag("NAVB")
    for _ in range(r.u32()):
        m.nav_blockers.append([struct.unpack("<2f", r.take(8)) for _ in range(r.u32())])

    return m
