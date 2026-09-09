#!/usr/bin/env python3
"""Batch-export every N3 model to .glb, in parallel."""
import argparse, os, sys, traceback, json
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_model import Exporter

ROOT = OUT = None


def job(rel):
    ex = Exporter(ROOT, OUT)
    src = os.path.join(ROOT, "models", rel)
    dst = os.path.join(OUT, os.path.splitext(rel)[0] + ".glb")
    try:
        r = ex.export(src, out_path=dst)
        # Cap the warnings: huge lists returned by thousands of tasks used to
        # choke the parent process while it aggregated the results.
        out = {k: v for k, v in r.items() if k not in ("out", "warnings")}
        out["warnings"] = r.get("warnings", [])[:5]
        out["n_warnings"] = len(r.get("warnings", []))
        return {"rel": rel, "ok": True, **out}
    except Exception as e:
        return {"rel": rel, "ok": False, "error": f"{type(e).__name__}: {e}"}


def init(root, out):
    global ROOT, OUT
    ROOT, OUT = root, out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="extracted/export_win32")
    ap.add_argument("--out", default="godot_export")
    ap.add_argument("--subdir", default="", help="restrict to one models/ subfolder")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--report", default=None)
    ap.add_argument("--skip", nargs="*", default=[],
                    help="model names to skip (without .n3), e.g. uniskel")
    a = ap.parse_args()

    mroot = os.path.join(a.root, "models")
    rels = []
    for dp, _, fns in os.walk(os.path.join(mroot, a.subdir)):
        for fn in fns:
            if fn.endswith(".n3"):
                rels.append(os.path.relpath(os.path.join(dp, fn), mroot))
    if a.skip:
        skip = set(a.skip)
        before = len(rels)
        rels = [r for r in rels
                if os.path.splitext(os.path.basename(r))[0] not in skip]
        print(f"[export] {before - len(rels)} models skipped: {sorted(skip)}", flush=True)
    rels.sort()
    print(f"[export] {len(rels)} models -> {a.out}", flush=True)

    init(a.root, a.out)
    results = []
    with ProcessPoolExecutor(max_workers=a.workers,
                             initializer=init, initargs=(a.root, a.out)) as pool:
        for i, r in enumerate(pool.map(job, rels, chunksize=1), 1):
            results.append(r)
            if i % 250 == 0 or i == len(rels):
                ok = sum(1 for x in results if x["ok"])
                print(f"[export] {i}/{len(rels)}  ok={ok}  failed={i-ok}", flush=True)

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    print(f"\n[export] done: {len(ok)} ok, {len(bad)} failed")
    print(f"[export] maillages={sum(r.get('meshes',0) for r in ok)} "
          f"anims={sum(r.get('animations',0) for r in ok)} "
          f"emetteurs_fx={sum(r.get('fx',0) for r in ok)}")
    warn = {}
    for r in ok:
        for w in r.get("warnings", []):
            k = w.split(":")[0]
            warn[k] = warn.get(k, 0) + 1
    print("[export] warnings by kind:")
    for k, v in sorted(warn.items(), key=lambda x: -x[1])[:10]:
        print(f"    {v:7} {k}")
    if bad:
        print("[export] first failures:")
        for r in bad[:10]:
            print(f"    {r['rel']}: {r['error']}")
    if a.report:
        with open(a.report, "w") as f:
            json.dump(results, f, indent=1)
        print(f"[export] report -> {a.report}")


if __name__ == "__main__":
    main()
