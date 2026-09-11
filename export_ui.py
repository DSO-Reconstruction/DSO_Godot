#!/usr/bin/env python3
"""Export the Drakensang Online interface (ui/*.bxml) to Godot 4 scenes.

Each window is a binary XML tree of widgets; the artwork of every widget is a
group of a shared mesh (meshes/ui/<window>_s_0.nvx2) that carries the geometry,
the atlas UVs and the vertex colours. This produces, per window:

  <out>/<window>.tscn        Control tree, ready to instantiate
  <out>/<window>.ui.json     the decoded widget tree, nothing dropped

plus the shared atlases as PNG and the localisation tables as JSON.

  python3 export_ui.py --root extracted/export_win32 --out godot_export
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsoexp import bxml, nvx2, tscn
from dsoexp.resources import Resolver, dds_to_png

# The widget rects are fractions of the screen; the meshes live in a 4 x 3
# ortho box centred on the widget. A 4:3 screen of 1024x768 makes both scales
# come out at 256 px per mesh unit, which is the reference the UI was drawn at.
BASE_W, BASE_H = 1024.0, 768.0
PX_PER_UNIT = 256.0

H_ANCHOR = {"Left": 0.0, "HCenter": 0.5, "Right": 1.0}
V_ANCHOR = {"Top": 0.0, "VCenter": 0.5, "Bottom": 1.0}
H_ALIGN = {"Left": 0, "HCenter": 1, "Right": 2}
V_ALIGN = {"Top": 0, "VCenter": 1, "Bottom": 2}

# Widgets that behave like a button: their children are the visual states.
BUTTON_TAGS = {"Button", "TextButton", "SlotButton"}
STATE_IDS = ("normal", "pressed", "mouseover", "mouseoverpressed", "disabled")
# Widgets the player actually interacts with; the rest must not eat clicks.
INTERACTIVE = BUTTON_TAGS | {"Slot", "TextEntry", "Slider", "List"}

# 'system/white' is the engine's untextured 1x1: draw the vertex colours only.
BLANK_TEX = ("system/white", "system/black", "system/nobump", "system/zeroalpha")


def fvals(s, n, default=0.0):
    try:
        out = [float(x) for x in s.split(",")]
    except (AttributeError, ValueError):
        return [default] * n
    return (out + [default] * n)[:n]


# --------------------------------------------------------------- localisation

def load_loca(root, langs):
    """language/<lang>/*.xml -> {'<category>_<item>': text}."""
    out = {}
    for lang in langs:
        d = os.path.join(root, "language", lang)
        if not os.path.isdir(d):
            continue
        table = {}
        for name in sorted(os.listdir(d)):
            if not name.endswith(".xml"):
                continue
            try:
                tree = ET.parse(os.path.join(d, name))
            except ET.ParseError:
                continue
            for cat in tree.getroot().iter("category"):
                prefix = cat.get("name", "")
                for item in cat.iter("item"):
                    table["%s_%s" % (prefix, item.get("name", ""))] = item.text or ""
        out[lang] = table
    return out


# ------------------------------------------------------------------ textures

class Textures:
    """Converts the referenced atlases to PNG once and remembers their size."""

    def __init__(self, res, texdir, res_prefix):
        self.res, self.texdir, self.prefix = res, texdir, res_prefix
        self.cache = {}
        self.missing, self.undecodable = set(), set()

    def get(self, ref):
        """'ui/struktur' -> ('res://.../textures/ui/struktur.png', (w, h))."""
        if not ref or ref in BLANK_TEX:
            return None
        if ref in self.cache:
            return self.cache[ref]
        src = self.res.path("tex:" + ref)
        out = None
        if src is None:
            self.missing.add(ref)
        else:
            png = os.path.join(self.texdir, ref + ".png")
            if dds_to_png(src, png):
                try:
                    from PIL import Image
                    with Image.open(png) as im:
                        size = im.size
                    out = ("%s/textures/%s.png" % (self.prefix, ref), size)
                except Exception:
                    out = None
            if out is None:
                self.undecodable.add(os.path.splitext(src)[1].lstrip("."))
        self.cache[ref] = out
        return out


# ---------------------------------------------------------------- mesh pieces

class UiMesh:
    """One meshes/ui/*.nvx2, decoded once per window."""

    def __init__(self, path):
        self.m = nvx2.load(path)
        self.d = nvx2.decode(self.m)

    def group(self, idx, sx, sy):
        """Group `idx` as (verts_px, uvs, colors, faces), centred on (0, 0).

        Screen pixels, y down; `scale` is the widget's own stretch factor.
        """
        if idx < 0 or idx >= len(self.m.groups):
            return None
        g = self.m.groups[idx]
        tri = self.m.indices[g.first_triangle * 3:
                             (g.first_triangle + g.num_triangles) * 3]
        if not tri:
            return None
        order, remap = [], {}
        for i in tri:
            if i not in remap:
                remap[i] = len(order)
                order.append(i)
        pos, uv0 = self.d["positions"], self.d.get("uv0")
        col = self.d.get("colors")
        verts = [(pos[i][0] * sx * PX_PER_UNIT, -pos[i][1] * sy * PX_PER_UNIT)
                 for i in order]
        uvs = [uv0[i] for i in order] if uv0 else [(0.0, 0.0)] * len(order)
        cols = [col[i] for i in order] if col else [(1.0, 1.0, 1.0, 1.0)] * len(order)
        faces = [[remap[tri[k]], remap[tri[k + 1]], remap[tri[k + 2]]]
                 for k in range(0, len(tri), 3)]
        return verts, uvs, cols, faces


def as_quad(verts, uvs):
    """Axis-aligned single quad? -> (rect, uv_rect, flip_h, flip_v) or None."""
    if len(verts) != 4:
        return None
    xs = sorted({round(v[0], 3) for v in verts})
    ys = sorted({round(v[1], 3) for v in verts})
    us = sorted({round(u[0], 5) for u in uvs})
    vs = sorted({round(u[1], 5) for u in uvs})
    if len(xs) != 2 or len(ys) != 2 or len(us) != 2 or len(vs) != 2:
        return None
    # Each corner must pair the same way in both spaces, or it is not a plain
    # blit (rotated / mirrored diagonally).
    flip_h = flip_v = None
    for (x, y), (u, v) in zip(verts, uvs):
        h = (round(x, 3) == xs[0]) == (round(u, 5) == us[0])
        w = (round(y, 3) == ys[0]) == (round(v, 5) == vs[0])
        if flip_h is None:
            flip_h, flip_v = h, w
        elif (h, w) != (flip_h, flip_v):
            return None
    return ((xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0]),
            (us[0], vs[0], us[1] - us[0], vs[1] - vs[0]),
            not flip_h, not flip_v)


# -------------------------------------------------------------------- window

class WindowExporter:
    def __init__(self, tex, loca, font_scale, res_prefix, button_script=None):
        self.tex, self.loca = tex, loca
        self.font_scale, self.prefix = font_scale, res_prefix
        self.button_script = button_script
        self._atlas = {}
        self.stats = {"nodes": 0, "quads": 0, "polys": 0, "labels": 0,
                      "buttons": 0, "no_mesh": 0, "no_texture": 0}

    # -- helpers -----------------------------------------------------------
    def text_of(self, raw):
        if not raw:
            return raw
        return self.loca.get(raw, raw)

    def _font(self, sc, bold, italic):
        key = (bool(bold), bool(italic))
        cache = getattr(self, "_fonts", None)
        if cache is None:
            cache = self._fonts = {}
        if key not in cache:
            props = {"font_names": 'PackedStringArray("Tahoma", "DejaVu Sans", "Arial")'}
            if bold:
                props["font_weight"] = "700"
            if italic:
                props["font_italic"] = "true"
            cache[key] = sc.sub("SystemFont", props)
        return cache[key]

    # -- one widget --------------------------------------------------------
    def emit(self, sc, node, parent_path, parent_rect, names, mesh, hidden=False):
        a = node.attrs
        rect = fvals(a.get("rect", "0,0,1,1"), 4)
        l, t, r, b = (rect[0] * BASE_W, rect[1] * BASE_H,
                      rect[2] * BASE_W, rect[3] * BASE_H)
        w, h = max(r - l, 0.0), max(b - t, 0.0)
        pl, pt, pr, pb = parent_rect
        ax = H_ANCHOR.get(a.get("HorizontalParentAlignment"), 0.5)
        ay = V_ANCHOR.get(a.get("VerticalParentAlignment"), 0.5)
        ox = pl + ax * (pr - pl)
        oy = pt + ay * (pb - pt)

        name = names.unique(a.get("id") or node.tag)
        props = {
            "layout_mode": "1",
            "anchors_preset": "-1",
            "anchor_left": tscn.f(ax), "anchor_top": tscn.f(ay),
            "anchor_right": tscn.f(ax), "anchor_bottom": tscn.f(ay),
            "offset_left": tscn.f(l - ox), "offset_top": tscn.f(t - oy),
            "offset_right": tscn.f(r - ox), "offset_bottom": tscn.f(b - oy),
        }
        if node.tag in INTERACTIVE and a.get("ClickThrough") != "true":
            props["mouse_filter"] = "0"          # MOUSE_FILTER_STOP
        else:
            props["mouse_filter"] = "2"          # MOUSE_FILTER_IGNORE
        ntype = "Control"
        if node.tag in BUTTON_TAGS:
            self.stats["buttons"] += 1
        if hidden:
            props["visible"] = "false"
        props["metadata/dso_type"] = tscn.v_str(node.tag)
        if a.get("event"):
            props["metadata/dso_event"] = tscn.v_str(a["event"])
        script = None
        if node.tag in BUTTON_TAGS and self.button_script:
            script = sc.ext("Script", self.button_script)
            props["dso_event"] = tscn.v_str(a.get("event", ""))
        sc.node(name, ntype, parent_path, props, script=script)
        self.stats["nodes"] += 1
        path = name if parent_path == "." else "%s/%s" % (parent_path, name)

        self._art(sc, node, path, w, h, mesh)
        self._label(sc, node, path)
        return path, (l, t, r, b)

    # -- the widget artwork ------------------------------------------------
    def _art(self, sc, node, path, w, h, mesh):
        a = node.attrs
        if "meshGrpIdx" not in a or a.get("HideBackground") == "true":
            self.stats["no_mesh"] += 1
            return
        if mesh is None:
            return
        sx, sy = fvals(a.get("scale", "1,1"), 2, 1.0)
        grp = mesh.group(int(a["meshGrpIdx"]), sx or 1.0, sy or 1.0)
        if grp is None:
            return
        verts, uvs, cols, faces = grp
        ref = a.get("texture", "")
        tex = self.tex.get(ref)
        if tex is None and ref and ref not in BLANK_TEX:
            # The atlas is absent from the client data (or unreadable). Drawing
            # the bare geometry would paint an opaque white block over the
            # window, which is worse than drawing nothing.
            self.stats["no_texture"] += 1
            return
        cx, cy = w / 2.0, h / 2.0

        quad = as_quad(verts, uvs) if len(faces) == 2 else None
        uniform = all(c == cols[0] for c in cols)
        inside = all(-0.001 <= u <= 1.001 and -0.001 <= v <= 1.001 for u, v in uvs)

        if quad and uniform and (tex is None or inside):
            (qx, qy, qw, qh), (u0, v0, uw, vh), fh, fv = quad
            props = {
                "layout_mode": "0",
                "offset_left": tscn.f(cx + qx), "offset_top": tscn.f(cy + qy),
                "offset_right": tscn.f(cx + qx + qw),
                "offset_bottom": tscn.f(cy + qy + qh),
                "mouse_filter": "2",
            }
            if tex is None:
                if cols[0] != (1.0, 1.0, 1.0, 1.0):
                    props["color"] = tscn.v_color(*cols[0])
                sc.node("art", "ColorRect", path, props)
            else:
                if cols[0] != (1.0, 1.0, 1.0, 1.0):
                    props["modulate"] = tscn.v_color(*cols[0])
                res, (tw, th) = tex
                region = tscn.v_rect2(u0 * tw, v0 * th, uw * tw, vh * th)
                key = (res, region)
                if key not in self._atlas:
                    self._atlas[key] = sc.sub("AtlasTexture", {
                        "atlas": sc.ext("Texture2D", res), "region": region})
                props["texture"] = self._atlas[key]
                props["expand_mode"] = "1"
                if fh:
                    props["flip_h"] = "true"
                if fv:
                    props["flip_v"] = "true"
                sc.node("art", "TextureRect", path, props)
            self.stats["quads"] += 1
            return

        props = {
            "position": tscn.v_vec2(cx, cy),
            "polygon": tscn.v_pool_vec2(verts),
            "polygons": tscn.v_faces(faces),
            "antialiased": "false",
        }
        if tex is not None:
            res, (tw, th) = tex
            props["texture"] = sc.ext("Texture2D", res)
            props["uv"] = tscn.v_pool_vec2([(u * tw, v * th) for u, v in uvs])
            if not inside:
                props["texture_repeat"] = "2"
        if not uniform:
            props["vertex_colors"] = tscn.v_pool_color(cols)
        elif cols[0] != (1.0, 1.0, 1.0, 1.0):
            props["color"] = tscn.v_color(*cols[0])
        sc.node("art", "Polygon2D", path, props)
        self.stats["polys"] += 1

    # -- the widget text ---------------------------------------------------
    def _label(self, sc, node, path):
        a = node.attrs
        if "font" not in a:
            return
        text = self.text_of(a.get("Text", ""))
        size = max(1, int(round(float(a.get("fontSize", "10") or 10)
                                * self.font_scale)))
        props = {
            "layout_mode": "1", "anchors_preset": "15",
            "anchor_right": "1.0", "anchor_bottom": "1.0",
            "mouse_filter": "2",
            "text": tscn.v_str(text),
            "horizontal_alignment": str(H_ALIGN.get(a.get("HorizontalAlignment"), 0)),
            "vertical_alignment": str(V_ALIGN.get(a.get("VerticalAlignment"), 0)),
            "theme_override_font_sizes/font_size": str(size),
            "theme_override_fonts/font": self._font(sc, a.get("bold") == "true",
                                                    a.get("italic") == "true"),
        }
        col = fvals(a.get("fontColor", "1,1,1,1"), 4, 1.0)
        props["theme_override_colors/font_color"] = tscn.v_color(*col)
        if a.get("WordBreak") == "true":
            props["autowrap_mode"] = "3"          # AUTOWRAP_WORD_SMART
        effect = a.get("TextEffect", "0")
        ecol = fvals(a.get("TextEffectColor", "0,0,0,1"), 4)
        esize = float(a.get("TextEffectSize", "0") or 0)
        if effect == "1" and esize > 0:
            gx, gy = fvals(a.get("GlowOffset", "1,1"), 2, 1.0)
            props["theme_override_colors/font_shadow_color"] = tscn.v_color(*ecol)
            props["theme_override_constants/shadow_offset_x"] = str(int(round(gx or 1)))
            props["theme_override_constants/shadow_offset_y"] = str(int(round(gy or 1)))
        elif effect == "2" and esize > 0:
            props["theme_override_colors/font_outline_color"] = tscn.v_color(*ecol)
            props["theme_override_constants/outline_size"] = str(
                max(1, int(round(esize * 2))))
        raw = a.get("Text", "")
        # Keep the key next to the resolved string so a viewer can re-translate.
        if raw and " " not in raw and raw.count(".") >= 2:
            props["metadata/dso_loca"] = tscn.v_str(raw)
        sc.node("Text", "Label", path, props)
        self.stats["labels"] += 1


class Names:
    def __init__(self):
        self.seen = {}

    def unique(self, name):
        name = "".join(c if c.isalnum() or c in "_-" else "_"
                       for c in (name or "node")) or "node"
        n = self.seen.get(name, 0)
        self.seen[name] = n + 1
        return name if n == 0 else "%s%d" % (name, n + 1)



def exclusive(node):
    """Indices of sibling widgets to start hidden.

    Widgets that share one rect with their siblings are alternative states of
    the same slot: a button's normal/pressed/mouseover, the three colours of a
    latency indicator, the four class backgrounds of the character creator,
    the frames of a flipbook. The engine shows exactly one; drawn all at once
    they pile up.

    Two signals, both needed -- a rect shared by exactly two widgets is just
    as often a frame stacked on its fill, and hiding one of those wipes out
    half a window:
      * every id in the group is a button state -> keep `normal`;
      * three or more widgets share the rect    -> keep the first.
    Nothing is dropped either way: the nodes are in the scene, just hidden.
    """
    by_rect = {}
    for i, c in enumerate(node.children):
        by_rect.setdefault(c.attrs.get("rect"), []).append(i)
    out = set()
    for rect, group in by_rect.items():
        if rect is None or len(group) < 2:
            continue
        states = all(node.children[i].attrs.get("id") in STATE_IDS for i in group)
        if not states and len(group) < 3:
            continue
        keep = group[0]
        for i in group:
            if node.children[i].attrs.get("id") == "normal":
                keep = i
                break
        out.update(i for i in group if i != keep)
    return out


def to_json(node):
    return {"type": node.tag, "attrs": node.attrs,
            "children": [to_json(c) for c in node.children]}


def export_window(path, root, tex, loca, out, font_scale, res_prefix,
                  exclusive_variants=True, button_script=None):
    doc = bxml.parse(open(path, "rb").read())
    name = os.path.splitext(os.path.basename(path))[0]

    mesh = None
    mpath = os.path.join(root, "meshes", "ui", name + "_s_0.nvx2")
    if os.path.isfile(mpath):
        mesh = UiMesh(mpath)

    sc = tscn.Scene()
    sc.node(name, "Control", None, {
        "layout_mode": "3", "anchors_preset": "15",
        "anchor_right": "1.0", "anchor_bottom": "1.0",
        "mouse_filter": "2"})
    exp = WindowExporter(tex, loca, font_scale, res_prefix, button_script)
    names = Names()
    screen = (0.0, 0.0, BASE_W, BASE_H)

    def walk(node, parent_path, parent_rect):
        hidden = exclusive(node) if exclusive_variants else set()
        for i, child in enumerate(node.children):
            if child.tag in ("Nebula3", "Window"):
                walk(child, parent_path, parent_rect)
                continue
            p, rect = exp.emit(sc, child, parent_path, parent_rect, names, mesh,
                               hidden=i in hidden)
            walk(child, p, rect)

    walk_root = doc
    walk(walk_root, ".", screen)

    sc.save(os.path.join(out, name + ".tscn"))
    with open(os.path.join(out, name + ".ui.json"), "w", encoding="utf-8") as fh:
        json.dump(to_json(doc), fh, indent=1)
    return exp.stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="extracted/export_win32")
    ap.add_argument("--out", default="godot_export")
    ap.add_argument("--res-prefix", default="res://dso",
                    help="where <out> will live inside the Godot project")
    ap.add_argument("--lang", default="en,fr", help="localisations to export")
    ap.add_argument("--ui-lang", default="en", help="language baked into the scenes")
    ap.add_argument("--font-scale", type=float, default=1.5,
                    help="fontSize -> Godot font size (the engine's own factor "
                         "is not in the data; calibrate here)")
    ap.add_argument("--only", nargs="*", help="export only these windows")
    ap.add_argument("--no-scripts", action="store_true",
                    help="do not attach the button runtime script")
    ap.add_argument("--keep-variants", action="store_true",
                    help="leave every alternative layout visible instead of "
                         "showing only the first of each stack")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    out = os.path.join(os.path.abspath(args.out), "ui")
    texdir = os.path.join(os.path.abspath(args.out), "textures")
    os.makedirs(out, exist_ok=True)

    langs = [l.strip() for l in args.lang.split(",") if l.strip()]
    loca = load_loca(root, langs)
    locadir = os.path.join(out, "loca")
    os.makedirs(locadir, exist_ok=True)
    for lang, table in loca.items():
        with open(os.path.join(locadir, lang + ".json"), "w", encoding="utf-8") as fh:
            json.dump(table, fh, ensure_ascii=False, indent=0)

    prefix = args.res_prefix.rstrip("/")
    button_script = None
    here = os.path.dirname(os.path.abspath(__file__))
    if not args.no_scripts:
        import shutil
        for name in ("dso_button.gd", "ui_browser.gd"):
            src = os.path.join(here, "godot", name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(out, name))
        button_script = "%s/ui/dso_button.gd" % prefix

    tex = Textures(Resolver(root), texdir, prefix)
    files = sorted(f for f in os.listdir(os.path.join(root, "ui"))
                   if f.endswith(".bxml"))
    if args.only:
        keep = {o if o.endswith(".bxml") else o + ".bxml" for o in args.only}
        files = [f for f in files if f in keep]

    total, windows = {}, []
    for f in files:
        try:
            st = export_window(os.path.join(root, "ui", f), root, tex,
                               loca.get(args.ui_lang, {}), out,
                               args.font_scale, args.res_prefix.rstrip("/"),
                               not args.keep_variants, button_script)
        except Exception as exc:                     # keep going, report at the end
            print("  !! %s: %s: %s" % (f, type(exc).__name__, exc))
            continue
        windows.append({"name": os.path.splitext(f)[0], **st})
        for k, v in st.items():
            total[k] = total.get(k, 0) + v

    manifest = {"base_resolution": [BASE_W, BASE_H], "px_per_mesh_unit": PX_PER_UNIT,
                "font_scale": args.font_scale, "language": args.ui_lang,
                "languages": sorted(loca), "windows": windows, "totals": total,
                "missing_textures": sorted(tex.missing),
                "undecodable_texture_formats": sorted(tex.undecodable)}
    with open(os.path.join(out, "ui_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)

    print("%d windows -> %s" % (len(windows), out))
    print("  " + ", ".join("%s=%d" % kv for kv in sorted(total.items())))
    if tex.missing:
        print("  %d textures referenced but absent from the client data"
              % len(tex.missing))
    if tex.undecodable:
        print("  formats Pillow cannot read: %s"
              % ", ".join(sorted(tex.undecodable)))


if __name__ == "__main__":
    main()
