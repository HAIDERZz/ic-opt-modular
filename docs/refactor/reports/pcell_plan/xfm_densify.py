"""Densify the ms transformer library where its anchored high-frequency columns are thin (T13.7 follow-up).

Usable rows at an anchor f0 need a system SRF above 1.25 x f0 (35 GHz for 28 GHz, 75 GHz for 60 GHz); a multi-turn
secondary resonates low, so the production grid left ~440 / ~80 such rows per metal pair. This planner proposes the
extra points: in the small-secondary region it completes the (W_P, W_S, S_S) orthogonal array to the full 3 x 3 x 3
factorial and adds intermediate outer diameters, keeps only candidates the library's own SRF model predicts above
35 GHz, builds each one (generator refusals drop out), and writes them in the production grid format so
``xfm_library.py <grid> <root> --plan`` prices the batch and ``xfm_library.py`` runs it into the same stores (same
EMX settings per class: one generation, the dataset simply grows).

Usage: xfm_densify.py LIBRARY_ROOT OUT_GRID_JSON [--min-srf-ghz 35] [--jobs 8]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from xfm_grid import PAIRS, config, inner
from xfm_memory import CLASSES, winding_perimeter_um

OD_P = (60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 135.0, 150.0, 165.0, 180.0)
OD_S = (60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0)
WIDTHS, SPACINGS, TURNS = (4.0, 7.0, 10.0), (2.0, 3.0, 4.0), (2, 3)
MIN_INNER_UM = 30.0
PROFILE = "n28_1p10m"


def candidates(tag: str, pm: str, sm: str, three_d: list[str]) -> list[dict]:
    out = []
    for odp in OD_P:
        for ods in OD_S:
            if not 0.5 * odp <= ods <= 2.0 * odp:
                continue
            for nt in TURNS:
                for wp in WIDTHS:
                    for ws in WIDTHS:
                        for s in SPACINGS:
                            p = {"pair": tag, "primary_metal": pm, "secondary_metal": sm, "three_d": three_d, "od_p": odp, "od_s": ods,
                                 "w_p": wp, "w_s": ws, "nt_s": nt, "s_s": s, "offset": 0.0,
                                 "inner_p": round(inner(odp, wp, 0, 1), 3), "inner_s": round(inner(ods, ws, s, nt), 3)}
                            if min(p["inner_p"], p["inner_s"]) >= MIN_INNER_UM:
                                out.append(p)
    return out


def coords(p: dict) -> tuple:
    return (p["od_p"], p["od_s"], p["w_p"], p["w_s"], round(p["offset"] * (p["od_p"] + p["od_s"]) / 4, 2), p["s_s"], float(p["nt_s"]))


def _build(p: dict) -> dict:
    """Generator verdict and, when it builds, the winding perimeter the memory model runs on."""
    from ic_opt.em.pcell import get_generator

    generator = get_generator("clean_port_xfm_ms", plugin_module="builtin:clean_port")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            generator.generate(generator.config_model.model_validate(config("ms", p, PROFILE)), outdir=Path(tmp), gds_name="x.gds")
        except Exception as exc:  # noqa: BLE001 -- every refusal class is data here
            return {"status": "refused", "why": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"}
        return {"status": "ok", "perimeter_um": round(winding_perimeter_um(Path(tmp) / "x.gds", PROFILE), 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--min-srf-ghz", type=float, default=35.0)
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()
    from ic_opt.library import query

    lib = query.Library(args.root, calibrate=False)
    grid = json.loads((args.root / "xfm_grid.json").read_text())
    a, b = grid["memory_model"]["a"], grid["memory_model"]["b"]
    result = {"profile": PROFILE, "purpose": "ms high-frequency densification", "min_srf_ghz": args.min_srf_ghz,
              "memory_model": grid["memory_model"], "families": {"ms": {"grid": {"od_p": OD_P, "od_s": OD_S, "widths": WIDTHS, "ms_spacing": SPACINGS,
                                                                                "ms_turns": TURNS, "offsets": [0.0], "min_inner_um": MIN_INNER_UM,
                                                                                "opening_um": 8.0, "lead_length_um": 20.0}, "points": []}}}
    tally: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    for tag, pm, sm, three_d in PAIRS["ms"]:
        stratum = f"xfm_ms_{tag}"
        ds = lib.dataset(stratum)
        have = {tuple(round(r.coords[d], 6) for d in ds.dims) for r in ds.rows}
        model = lib.model(stratum, "SRF")
        pool = [p for p in candidates(tag, pm, sm, three_d) if tuple(round(v, 6) for v in coords(p)) not in have]
        x = np.array([coords(p) for p in pool], dtype=float)
        mu, sigma = model.gp.predict(x)
        inside = model.guard.inside(x)
        for p, m, s_, ok in zip(pool, mu, sigma, inside):
            p["srf_pred_ghz"] = None if not np.isfinite(m) else round(float(m), 1)
            p["srf_pred_rel_sigma"] = None if not np.isfinite(m) else round(float(s_ / m), 3)
            p["in_domain"] = bool(ok)
        kept = [p for p in pool if p["srf_pred_ghz"] is not None and p["srf_pred_ghz"] >= args.min_srf_ghz]
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            verdicts = list(ex.map(_build, kept, chunksize=4))
        for p, v in zip(kept, verdicts):
            p.update(v)
            if v["status"] == "ok":
                p["mem_pred_gb"] = round(a * p["perimeter_um"] ** b, 2)
                p["mem_class"] = next(c for c, (upper, _, _) in CLASSES.items() if p["mem_pred_gb"] <= upper)
            else:
                reasons[re.sub(r"-?[0-9]+(\.[0-9]+)?", "#", p["why"])[:120]] += 1
            tally[(tag, v["status"])] += 1
            result["families"]["ms"]["points"].append(p)
        ok = [p for p in kept if p["status"] == "ok"]
        s60 = sum(p["srf_pred_ghz"] >= 75 for p in ok)
        by_nt = collections.Counter(p["nt_s"] for p in ok)
        by_class = collections.Counter(p["mem_class"] for p in ok)
        print(f"{stratum}: {len(pool)} new candidates, {len(kept)} predicted SRF >= {args.min_srf_ghz:g} GHz, {len(ok)} build "
              f"({s60} predicted >= 75 GHz; NT {dict(sorted(by_nt.items()))}; memory classes {dict(sorted(by_class.items()))}; "
              f"library today: {len(ds.usable('Lp@28'))} rows at 28 GHz, {len(ds.usable('Lp@60'))} at 60 GHz)")
    for reason, n in reasons.most_common(5):
        print(f"  {n:5d}  {reason}")
    result["families"]["ms"]["tally"] = {f"{t}:{s}": n for (t, s), n in tally.items()}
    result["families"]["ms"]["refusal_reasons"] = reasons.most_common()
    args.out.write_text(json.dumps(result, indent=1, default=float))
    n_ok = sum(1 for p in result["families"]["ms"]["points"] if p["status"] == "ok")
    print(f"wrote {args.out}: {n_ok} points; price it with: xfm_library.py {args.out} {args.root} --plan")


if __name__ == "__main__":
    main()
