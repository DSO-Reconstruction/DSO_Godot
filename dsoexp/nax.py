"""Nebula3 animation reader: NAX3 index + NAC key streams.

A '<base>_animations.nax3' file declares the clips and their curves; the keys
themselves are either embedded at the end of the NAX3 or held in one
'<base>_<clipname>.nac' file per clip.

One curve is one channel (translation / scale / rotation) of one joint. Curves
come in groups of 3, or 4 when a velocity channel is present.
"""
import os
import struct
from dataclasses import dataclass, field

TICKS_PER_SEC = 1000.0

NAX_MAGIC = {b"NAX3", b"3XAN", b"HAN0", b"0HAN", b"NA01", b"10AN"}
NAC_MAGIC = {b"NAC0", b"0CAN", b"CAN0"}

# Curve type -> animated channel.
TRANSLATION, SCALE, ROTATION, VELOCITY = 0, 1, 2, 4
CURVE_CHANNEL = {TRANSLATION: "translation", SCALE: "scale",
                 ROTATION: "rotation", VELOCITY: "velocity"}


class NaxError(Exception):
    pass


@dataclass
class Curve:
    first_key: int = 0
    is_active: bool = False
    is_static: bool = False
    curve_type: int = 0
    static_key: tuple = (0.0, 0.0, 0.0, 1.0)


@dataclass
class Event:
    name: str = ""
    category: str = ""
    key_index: int = 0


@dataclass
class Clip:
    name: str = ""
    start_key: int = 0
    num_keys: int = 0
    key_stride: int = 0
    key_duration: int = 1
    pre_infinity: int = 0
    post_infinity: int = 0
    curves: list = field(default_factory=list)
    events: list = field(default_factory=list)
    keys: list = field(default_factory=list)     # [frame][active_curve_index] -> vec4

    @property
    def fps(self):
        return TICKS_PER_SEC / self.key_duration if self.key_duration else 30.0

    @property
    def duration(self):
        return self.num_keys * self.key_duration / TICKS_PER_SEC

    @property
    def curves_per_joint(self):
        # Some skeletons carry a 4th channel (velocity).
        return 4 if len(self.curves) > 3 and self.curves[3].curve_type == VELOCITY else 3

    @property
    def num_joints(self):
        return len(self.curves) // self.curves_per_joint

    def active_map(self):
        """Index of each curve in the key stream (-1 when static).

        Memoised: called once per frame and per curve while sampling.
        """
        out, k = [], 0
        for c in self.curves:
            if c.is_static:
                out.append(-1)
            else:
                out.append(k)
                k += 1
        return out

    def sample(self, curve_index, frame):
        """Value of one curve at a given frame."""
        c = self.curves[curve_index]
        if c.is_static:
            return c.static_key
        ai = self.active_map()[curve_index]
        if ai < 0 or frame >= len(self.keys) or ai >= len(self.keys[frame]):
            return c.static_key
        return self.keys[frame][ai]

    def joint_track(self, joint_index):
        """Return {'translation': [...], 'rotation': [...], 'scale': [...]}
        with one value per frame for the requested joint."""
        per = self.curves_per_joint
        base = joint_index * per
        track = {}
        for k in range(per):
            ci = base + k
            if ci >= len(self.curves):
                break
            chan = CURVE_CHANNEL.get(self.curves[ci].curve_type)
            if chan in (None, "velocity"):
                continue
            track[chan] = [self.sample(ci, f) for f in range(max(1, self.num_keys))]
        return track


def _cstr(b):
    z = b.find(b"\0")
    return b[:z if z >= 0 else len(b)].decode("utf-8", "replace").strip()


def load(path, load_keys=True):
    """Load a NAX3 and, if asked, its keys (embedded or via the .nac files)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] not in NAX_MAGIC:
        raise NaxError(f"{path}: unexpected NAX3 magic {data[:4]!r}")

    num_clips, num_keys = struct.unpack_from("<2i", data, 4)
    if num_clips < 0 or num_keys < 0:
        raise NaxError(f"{path}: invalid counts clips={num_clips} keys={num_keys}")

    off = 12
    clips = []
    for ci in range(num_clips):
        (n_curves, start_key, clip_keys, stride,
         duration, pre, post, n_events) = struct.unpack_from("<5H2BH", data, off)
        off += 14
        name = _cstr(data[off:off + 50]); off += 50
        if duration == 0:
            raise NaxError(f"{path}: clip {ci} has a zero key duration")
        clip = Clip(name=name, start_key=start_key, num_keys=clip_keys,
                    key_stride=stride, key_duration=duration,
                    pre_infinity=pre, post_infinity=post)
        for _ in range(n_events):
            ev = Event(name=_cstr(data[off:off + 47]),
                       category=_cstr(data[off + 47:off + 62]),
                       key_index=struct.unpack_from("<H", data, off + 62)[0])
            off += 64
            clip.events.append(ev)
        for _ in range(n_curves):
            fk, active, static, ctype, _pad = struct.unpack_from("<I4B", data, off)
            off += 8
            clip.curves.append(Curve(first_key=fk, is_active=bool(active),
                                     is_static=bool(static), curve_type=ctype,
                                     static_key=struct.unpack_from("<4f", data, off)))
            off += 16
        clips.append(clip)

    # Keys embedded at the end of the file, when present and complete.
    embedded = []
    need = num_keys * 16
    if num_keys and len(data) - off >= need:
        embedded = [struct.unpack_from("<4f", data, off + 16 * i) for i in range(num_keys)]

    if load_keys:
        for i, clip in enumerate(clips):
            _fill_keys(path, clip, i, embedded)
    return clips


def _fill_keys(nax_path, clip, clip_index, embedded):
    active = sum(0 if c.is_static else 1 for c in clip.curves)
    frames = max(1, clip.num_keys)
    if active == 0:
        return                                   # fully static clip

    if embedded:
        # The embedded stream is indexed globally from startKeyIndex.
        stride = clip.key_stride or active
        for f in range(frames):
            row = []
            for a in range(active):
                k = clip.start_key + f * stride + a
                row.append(embedded[k] if k < len(embedded) else (0.0, 0.0, 0.0, 1.0))
            clip.keys.append(row)
        return

    nac = _find_nac(nax_path, clip, clip_index)
    if nac:
        clip.keys = _read_nac(nac, clip, frames, active)


def _find_nac(nax_path, clip, clip_index):
    d = os.path.dirname(nax_path)
    base = os.path.basename(nax_path)
    for suffix in ("_animations.nax3", "_variations.nax3", ".nax3"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    cands = []
    if clip.name:
        cands += [f"{base}_{clip.name}.nac", f"{clip.name}.nac"]
    cands.append(f"{base}_clip{clip_index}.nac")
    for c in cands:
        p = os.path.join(d, c)
        if os.path.exists(p):
            return p
    return None


def _read_nac(path, clip, frames, active):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] not in NAC_MAGIC:
        raise NaxError(f"{path}: unexpected NAC magic {data[:4]!r}")
    off = 4
    rows = []
    for _ in range(frames):
        row = [(0.0, 0.0, 0.0, 1.0)] * active
        for ci, c in enumerate(clip.curves):
            if c.is_static:
                continue
            ai = clip.active_map()[ci]
            if c.curve_type == ROTATION:
                if off + 8 > len(data):
                    return rows
                # Quaternion compressed to signed int16.
                q = struct.unpack_from("<4h", data, off); off += 8
                val = tuple(v / 32768.0 for v in q)
            else:
                if off + 16 > len(data):
                    return rows
                val = struct.unpack_from("<4f", data, off); off += 16
            if 0 <= ai < active:
                row[ai] = val
        rows.append(row)
    return rows
