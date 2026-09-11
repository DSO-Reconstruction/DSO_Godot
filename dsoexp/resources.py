"""Nebula resource path resolution ('msh:', 'tex:', 'ani:') and texture
conversion to PNG, since glTF cannot reference DDS."""
import os
import shutil
import subprocess
import tempfile

PREFIX_DIR = {"msh": "meshes", "tex": "textures", "ani": "anims",
              "shd": "shaders", "mdl": "models", "phys": "physics"}
TEX_EXT = (".dds", ".crn", ".ktx2", ".png", ".tga")


class Resolver:
    def __init__(self, root):
        self.root = os.path.abspath(root)          # .../export_win32
        self._miss = set()

    def path(self, ref, default_ext=None):
        """'tex:npc/foo' -> '<root>/textures/npc/foo.dds' (first extension found)."""
        if not ref:
            return None
        ref = ref.replace("\\", "/")
        if ":" in ref:
            prefix, rest = ref.split(":", 1)
            sub = PREFIX_DIR.get(prefix)
            if sub is None:
                return None
        else:
            sub, rest = "", ref
        base = os.path.join(self.root, sub, rest) if sub else os.path.join(self.root, rest)

        if os.path.isfile(base):
            return base
        exts = TEX_EXT if sub == "textures" else ([default_ext] if default_ext else [])
        for e in exts:
            if e and os.path.isfile(base + e):
                return base + e
        self._miss.add(ref)
        return None

    @property
    def missing(self):
        return sorted(self._miss)


# --- Crunch (.crn) ----------------------------------------------------------
# A fifth of the client's textures are Crunch-compressed, which Pillow cannot
# read. `tools/build_crn2dds.sh` builds a transcoder to plain DXTn DDS; when
# it is absent those textures are simply skipped.
_CRN_CACHE = os.path.join(tempfile.gettempdir(), "dso_crn_cache")
_CRN_TOOL_MISSING = []


def crn_tool():
    for cand in (os.environ.get("DSO_CRN2DDS"),
                 os.path.join(os.path.dirname(os.path.dirname(
                     os.path.abspath(__file__))), "build", "crn2dds"),
                 shutil.which("crn2dds")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def crn_to_dds(src):
    """Transcode a .crn to a cached .dds. Returns the path, or None."""
    tool = crn_tool()
    if tool is None:
        if not _CRN_TOOL_MISSING:
            _CRN_TOOL_MISSING.append(True)
        return None
    key = os.path.abspath(src).lstrip(os.sep).replace(os.sep, "__")
    dst = os.path.join(_CRN_CACHE, key + ".dds")
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return dst
    os.makedirs(_CRN_CACHE, exist_ok=True)
    try:
        r = subprocess.run([tool], input="%s\t%s\n" % (src, dst),
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return dst if r.returncode == 0 and os.path.isfile(dst) else None


def _decodable(src):
    """Path Pillow can open: .crn goes through the transcoder first."""
    if src.lower().endswith(".crn"):
        return crn_to_dds(src)
    return src


def dds_to_png(src, dst):
    """Decompress a DDS or CRN to PNG. True if the PNG is in place."""
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return True
    real = _decodable(src)
    if real is None:
        return False
    src = real
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        from PIL import Image
        with Image.open(src) as im:
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.save(dst, "PNG", optimize=False)
        return True
    except Exception:
        return False


def spec_to_roughness(src, dst):
    """Specular map -> glTF metallic-roughness map.

    glTF expects roughness in the green channel and metalness in blue.
    Roughness is the inverse of glossiness: shiny areas are smooth.
    """
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return True
    src = _decodable(src) or src
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        from PIL import Image, ImageChops
        with Image.open(src) as im:
            im.load()
            gray = im.convert("L")
        rough = ImageChops.invert(gray)
        zero = Image.new("L", rough.size, 0)
        Image.merge("RGB", (zero, rough, zero)).save(dst, "PNG")
        return True
    except Exception:
        return False


def bump_to_normal(src, dst):
    """Nebula bump map (DXT5nm) -> RGB tangent-space normal map for glTF.

    These DDS files do NOT hold the normal in RGB: R is a constant 255 and B a
    constant 0 (both unused); the data lives in green and alpha. That is the
    era's DXT5nm packing, which exploits the better precision of the DXT5
    alpha block: X in alpha, Y in green, Z reconstructed.

    Measured over the shipped textures: x^2+y^2 averages 0.056 and exceeds 1
    on 0.01% of pixels, which confirms the scheme. Reading RGB as-is instead
    yields vectors about 1.42 long, which glTF rejects.
    """
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return True
    src = _decodable(src) or src
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        import numpy as np
        from PIL import Image
        with Image.open(src) as im:
            im.load()
            a = np.asarray(im.convert("RGBA")).astype(np.float32)
        x = a[..., 3] / 127.5 - 1.0
        y = a[..., 1] / 127.5 - 1.0
        z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
        rgb = np.stack([x, y, z], axis=-1)
        out = np.clip((rgb + 1.0) * 127.5, 0, 255).astype(np.uint8)
        Image.fromarray(out, "RGB").save(dst, "PNG")
        return True
    except Exception:
        return False


def is_bump(ref):
    """Bump maps are identified by their filename suffix in the game data."""
    return bool(ref) and ref.lower().endswith("_bump")


_ALPHA_CACHE = {}


def has_alpha(src, threshold=0.995):
    """Does the texture carry a meaningful alpha channel?

    DXT1 decodes to a fully opaque alpha; DXT3/DXT5 may carry a real cutout.
    We look at the share of opaque pixels: below the threshold the material
    needs alpha blending rather than being treated as opaque.
    """
    if src in _ALPHA_CACHE:
        return _ALPHA_CACHE[src]
    out = False
    try:
        import numpy as np
        from PIL import Image
        with Image.open(_decodable(src) or src) as im:
            im.load()
            if im.mode == "RGBA":
                a = np.asarray(im)[..., 3]
                out = bool((a >= 250).mean() < threshold)
    except Exception:
        out = False
    _ALPHA_CACHE[src] = out
    return out
