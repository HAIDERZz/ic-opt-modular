"""T14 follow-up: sign-off candidates BETWEEN the transformer library's lattice levels (user approval 2026-09-24).

The bs strata's anchored models (Lp@40 ...) carry 5-9 % sigma between the sampled outer-diameter levels while their
held-out error on the lattice is 0.2-0.5 %. Real EMX at off-lattice designs tells whether that sigma is honest or
pessimistic. Candidates come from the 40 GHz region answers' mean set (points the models call feasible), ranked by
their scaled distance to the nearest measured row (the most off-lattice first), kept apart, built with the real
generator, and written in lib_signoff's candidates= format with the library's predictions for the record.

Usage: xfm_signoff_candidates.py LIBRARY_ROOT REGION_JSON [REGION_JSON ...] OUT_DIR [--per-stratum 5]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ic_opt.library import query, suggest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("answers", type=Path, nargs="+")
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-stratum", type=int, default=5)
    ap.add_argument("--min-spacing", type=float, default=0.12)
    args = ap.parse_args()
    lib = query.Library(args.root)
    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.answers:
        a = json.loads(path.read_text(encoding="utf-8"))
        st = a["stratum"]
        ds = lib.dataset(st)
        guard = lib.model(st, a["targets"][0]["quantity"]).guard
        pts = a["points_sample"]
        names = sorted({t["quantity"] for t in a["targets"]})          # the answer's quantities only: every other column would need a fit
        x = np.array([[p["params"][d] for d in ds.dims] for p in pts])
        far = np.array([guard.nearest(p["params"], k=1)[0][1] for p in pts])      # scaled distance to the nearest measured row
        order = np.argsort(-far, kind="stable")
        chosen = suggest.diversify(x, [int(i) for i in order], lib.ranges(st), ds.dims, keep=4 * args.per_stratum, min_spacing=args.min_spacing)
        picked = []
        for i in chosen:
            params = pts[i]["params"]
            build = suggest.build_check(lib, st, params)
            if not build["built"]:
                continue
            answer = query.query(lib, st, params, names)
            picked.append({"params": params, "distance_to_nearest_row": float(far[i]), "level": pts[i]["level"],
                           "predicted": {q: e for q, e in answer["quantities"].items()}, "build": build})
            if len(picked) == args.per_stratum:
                break
        (args.out / f"candidates_{st}.json").write_text(json.dumps({"stratum": st, "source": str(path), "candidates": picked}, indent=1, default=float))
        print(f"{st}: {len(picked)} candidates (nearest-row distance {min(p['distance_to_nearest_row'] for p in picked):.3f}-"
              f"{max(p['distance_to_nearest_row'] for p in picked):.3f} of the scaled box)")
        for p in picked:
            q = p["predicted"]
            print("   " + ", ".join(f"{d.replace('_um', '').replace('primary_', 'P_').replace('secondary_', 'S_').replace('outer_diameter', 'OD').replace('center_spacing', 'CS').replace('width', 'W')}={v:g}" for d, v in p["params"].items())
                  + " | " + ", ".join(f"{k}={e['value'] * (1e12 if k.startswith('L') else 1e-9 if k.startswith('SRF') else 1):.3g}"
                                       f"±{e.get('rel_sigma', 0) * 100:.1f}%" for k, e in q.items() if e.get("status") == "predicted" and ("@40" in k or k == "SRF")))


if __name__ == "__main__":
    main()
