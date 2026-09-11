"""Minimal writer for Godot 4 text scenes (.tscn).

Only what the exporters need: external resources, inline sub-resources and a
node tree. Properties are written verbatim, so callers pass already-formatted
GDScript literals (see the `v_*` helpers).
"""
import os


def v_vec2(x, y):
    return "Vector2(%s, %s)" % (f(x), f(y))


def v_rect2(x, y, w, h):
    return "Rect2(%s, %s, %s, %s)" % (f(x), f(y), f(w), f(h))


def v_color(r, g, b, a=1.0):
    return "Color(%s, %s, %s, %s)" % (f(r), f(g), f(b), f(a))


def v_str(s):
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def v_bool(b):
    return "true" if b else "false"


def v_pool_vec2(points):
    return "PackedVector2Array(%s)" % ", ".join(
        "%s, %s" % (f(x), f(y)) for x, y in points)


def v_pool_color(cols):
    return "PackedColorArray(%s)" % ", ".join(
        ", ".join(f(c) for c in col) for col in cols)


def v_faces(faces):
    return "[%s]" % ", ".join(
        "PackedInt32Array(%s)" % ", ".join(str(i) for i in fa) for fa in faces)


def f(x):
    """Compact float literal: Godot re-saves these anyway, keep them short."""
    if isinstance(x, int):
        return str(x)
    x = round(float(x), 4) + 0.0
    return str(int(x)) if x == int(x) else repr(x)


class Scene:
    def __init__(self):
        self._ext = {}          # (type, path) -> id
        self._sub = []          # (type, id, props)
        self._nodes = []        # (name, type, parent, props, groups)
        self._n = 0

    # -- resources ---------------------------------------------------------
    def ext(self, rtype, path):
        key = (rtype, path)
        if key not in self._ext:
            self._n += 1
            self._ext[key] = "%d_%s" % (self._n, os.path.basename(path)
                                        .replace(".", "_").replace("-", "_")[:24])
        return 'ExtResource("%s")' % self._ext[key]

    def sub(self, rtype, props):
        self._n += 1
        rid = "%s_%d" % (rtype, self._n)
        self._sub.append((rtype, rid, props))
        return 'SubResource("%s")' % rid

    # -- nodes -------------------------------------------------------------
    def node(self, name, ntype, parent, props=None, script=None):
        props = dict(props or {})
        if script:
            props["script"] = script
        self._nodes.append((name, ntype, parent, props))
        return name

    def dumps(self):
        steps = len(self._ext) + len(self._sub) + 1
        out = ["[gd_scene load_steps=%d format=3]\n" % steps]
        for (rtype, path), rid in self._ext.items():
            out.append('[ext_resource type="%s" path="%s" id="%s"]\n'
                       % (rtype, path, rid))
        out.append("")
        for rtype, rid, props in self._sub:
            out.append('\n[sub_resource type="%s" id="%s"]' % (rtype, rid))
            for k, v in props.items():
                out.append("%s = %s" % (k, v))
            out.append("")
        for name, ntype, parent, props in self._nodes:
            head = '\n[node name="%s" type="%s"' % (name, ntype)
            if parent is not None:
                head += ' parent="%s"' % parent
            out.append(head + "]")
            for k, v in props.items():
                out.append("%s = %s" % (k, v))
            out.append("")
        return "\n".join(out)

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.dumps())
