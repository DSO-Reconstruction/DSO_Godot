#!/usr/bin/env python3
"""Step 1: unpack the DSO client data (NB3 bundles, loose files and TOCs).

Container handling is derived from drakensang-nb3-bundle-extractor (WTFPL).
Output: the export_win32/... tree, rebuilt from the TOC files.
"""
import os, sys, re, struct, zlib, hashlib
from concurrent.futures import ProcessPoolExecutor

IN_ROOT  = sys.argv[1] if len(sys.argv) > 1 else "DSOClient"
OUT_ROOT = sys.argv[2] if len(sys.argv) > 2 else "extracted"

HASH_SUFFIX = re.compile(r"\._([0-9a-fA-F]{32})$")


def sanitize(rel):
    rel = rel.replace("\\", "/").lstrip("/")
    parts = []
    for p in rel.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return "/".join(parts)


def decomp(comp, xsize):
    for wb in (15, -15, 31):
        try:
            return zlib.decompress(comp, wb, xsize or 0)
        except zlib.error:
            pass
    d = zlib.decompressobj()
    return d.decompress(comp, xsize or 0) + d.flush()


def write_out(rel, data):
    path = os.path.join(OUT_ROOT, sanitize(rel))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def do_single(path, rel, data):
    """__ZN: a single zlib block (a loose file or a TOC)."""
    xsize = struct.unpack_from("<I", data, 4)[0]
    raw = decomp(data[8:], xsize)
    write_out(os.path.basename(rel), raw)
    return 1


def do_bundle(path, rel, data):
    """_B3NHB3N: multi-file archive holding real internal paths."""
    n_files, _skip, _info_off, base_off = struct.unpack_from("<4I", data, 8)
    off = 24
    names = []
    for _ in range(n_files):
        (nlen,) = struct.unpack_from("<H", data, off); off += 2
        names.append(data[off:off + nlen].decode("utf-8", "replace")); off += nlen
    count = 0
    for name in names:
        off += 4 + 32                              # tag + hash
        size, rel_off = struct.unpack_from("<2I", data, off); off += 8
        start = rel_off + base_off
        blob = data[start:start + size]
        if blob[:4] == b"__ZN":
            xsize = struct.unpack_from("<I", blob, 4)[0]
            try:
                out = decomp(blob[8:], xsize)
            except Exception:
                out = blob[8:]
            write_out(name[:-3] if name.endswith(".nz") else name, out)
        else:
            write_out(name, blob)
        count += 1
    return count


def do_ib3n(path, rel, data):
    """IB3N: .nbi index -> text."""
    off = 4
    (cnt,) = struct.unpack_from("<H", data, off); off += 2
    lines = []
    for i in range(cnt):
        (nlen,) = struct.unpack_from("<H", data, off); off += 2
        name = data[off:off + nlen].decode("utf-8", "replace"); off += nlen
        (size,) = struct.unpack_from("<I", data, off); off += 4
        (hlen,) = struct.unpack_from("<H", data, off); off += 2
        h = data[off:off + hlen].decode("ascii", "ignore"); off += hlen
        lines.append(f"{i}\t{name}\t{size}\t{h}")
    tag = hashlib.sha1(sanitize(rel).encode()).hexdigest()[:8]
    base = os.path.splitext(sanitize(rel))[0].replace("/", "_")
    write_out(f"_nbi/{base}_{tag}.toc.txt", "\n".join(lines).encode())
    return 1


def handle(args):
    path, rel = args
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] == b"__ZN":
            return ("single", do_single(path, rel, data), rel)
        if data[:8] == b"_B3NHB3N":
            return ("bundle", do_bundle(path, rel, data), rel)
        if data[:4] == b"IB3N":
            return ("ib3n", do_ib3n(path, rel, data), rel)
        write_out(rel, data)
        return ("raw", 1, rel)
    except Exception as e:
        return ("error", 0, f"{rel}: {type(e).__name__} {e}")


def relocate():
    """Move every file to its real path using the TOCs (path|f|md5)."""
    h2p, tocs = {}, set()
    for root, _, files in os.walk(OUT_ROOT):
        for fn in files:
            if "__toc" not in fn:
                continue
            fp = os.path.join(root, fn)
            tocs.add(os.path.normpath(fp))
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        p = line.strip().split("|")
                        if len(p) == 3 and p[1] == "f":
                            h2p[p[2]] = p[0]
            except Exception as e:
                print(f"[toc] {fp}: {e}")
    print(f"[toc] {len(h2p)} entries across {len(tocs)} TOC files", flush=True)
    moved = stripped = 0
    for root, _, files in os.walk(OUT_ROOT):
        for fn in list(files):
            fp = os.path.normpath(os.path.join(root, fn))
            if fp in tocs:
                continue
            m = HASH_SUFFIX.search(fn)
            if not m:
                continue
            target = h2p.get(m.group(1))
            if target:
                dst = os.path.join(OUT_ROOT, sanitize(target))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(fp, dst)
                moved += 1
            else:
                os.replace(fp, os.path.join(root, HASH_SUFFIX.sub("", fn)))
                stripped += 1
    print(f"[toc] moved={moved} hash_stripped={stripped}", flush=True)


def main():
    jobs = []
    for dp, _, fns in os.walk(IN_ROOT):
        if "_sauvegarde" in dp:
            continue
        for fn in fns:
            p = os.path.join(dp, fn)
            jobs.append((p, os.path.relpath(p, IN_ROOT)))
    print(f"[in] {len(jobs)} files to process", flush=True)
    stats, errors = {}, []
    with ProcessPoolExecutor() as ex:
        for i, (kind, n, info) in enumerate(ex.map(handle, jobs, chunksize=4), 1):
            stats[kind] = stats.get(kind, 0) + n
            if kind == "error":
                errors.append(info)
            if i % 100 == 0:
                print(f"[in] {i}/{len(jobs)} {stats}", flush=True)
    print(f"[in] done {stats}", flush=True)
    for e in errors[:40]:
        print(f"[err] {e}", flush=True)
    relocate()


if __name__ == "__main__":
    main()
