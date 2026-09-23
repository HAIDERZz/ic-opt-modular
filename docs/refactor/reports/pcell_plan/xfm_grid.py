"""The proposed xfm_bs / xfm_ms library grid: build every point with the current generator (no EMX) and report what it covers.

Feasibility comes from the generator itself (fail-closed refusals, with reasons). Each winding's
inductance is estimated with the Mohan current-sheet expression calibrated on the N28 inductor library
just built (same metals, same fixture); the coupling each point will land on is estimated from the old
N28 transformer library (geometry generations 2-5) by nearest neighbours in (OD_S/OD_P, offset, NT, W_P, W_S),
so the k coverage of the grid can be judged before a single EMX second is spent.

Usage: xfm_grid.py PROFILE OUT.json --ind-rows <library_rows.json> --old-db <device_db.sqlite> [--family bs|ms ...]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from ic_opt.em.pcell import get_generator

sys.path.insert(0, str(Path(__file__).parent))
from ind_grid import mohan_nh

OD_LEVELS = (60.0, 80.0, 100.0, 120.0, 150.0, 180.0, 210.0, 240.0)
NEAR_DELTAS = (-12.0, -8.0, -4.0, 4.0, 8.0, 12.0)          # bs only: OD_S = OD_P + delta resolves the k peak at OD_S ~ OD_P
FIXTURE = {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
# (tag, primary metal, secondary metal, EMX --3d metals); the primary sits one metal above the secondary
PAIRS = {"bs": (("ap", "AP", "10", ["AP", "M10"]), ("m10", "10", "9", ["M10", "M9"])),
         "ms": (("ap", "AP", "10", ["AP", "M10", "M9"]), ("m10", "10", "9", ["M10", "M9", "M8"]))}


def inner(od: float, w: float, s: float, nt: int) -> float:
    return od - 2 * nt * w - 2 * (nt - 1) * s


def calibration(ind_rows: Path) -> dict[tuple[str, int], float]:
    """Median EMX/Mohan per (body metal, turns) from the N28 inductor library (fine mesh, full-wave)."""
    by = collections.defaultdict(list)
    for r in json.loads(ind_rows.read_text()):
        if r["status"] == "ok" and r["L_nH"]:
            by[(r["metal"], r["turns"])].append(r["L_nH"] / mohan_nh(r["od"], r["w"], r["s"], r["turns"]))
    return {k: sorted(v)[len(v) // 2] for k, v in by.items()}


def old_rows(db: Path, family: str) -> list[dict]:
    q = f"""select s.stratum, s.params_json, max(case when m.name='k_lf' then m.value end)
            from samples s join metrics m on m.sample_id=s.id
            where s.family='xfm_{family}' and s.status='ok' and m.freq_hz is null group by s.id"""
    out = []
    for stratum, params, k in sqlite3.connect(db).execute(q):
        p = json.loads(params)
        if k is None:
            continue
        if family == "bs":
            odp, ods, wp, ws, nt = p["primary_outer_diameter_um"], p["secondary_outer_diameter_um"], p["primary_width_um"], p["secondary_width_um"], 1
        else:
            odp, ods, wp, ws, nt = p["single_outer_diameter_um"], p["multi_outer_diameter_um"], p["single_width_um"], p["multi_width_um"], p["multi_turns"]
        out.append({"tag": "ap" if "_ap" in stratum else "m10", "odp": odp, "ratio": ods / odp, "offset": p["center_spacing_ratio"],
                    "nt": nt, "wp": wp, "ws": ws, "k": k})
    return out


def k_estimate(old: list[dict], tag: str, odp: float, ratio: float, offset: float, nt: int, wp: float, ws: float, n: int = 5) -> float | None:
    """Mean k of the n nearest old points (same metal pair and turns); log-ratio dominates the distance."""
    cands = [r for r in old if r["tag"] == tag and r["nt"] == nt]
    if not cands:
        return None
    def dist(r: dict) -> float:
        return ((math.log(r["ratio"] / ratio) / 0.05) ** 2 + ((r["offset"] - offset) / 0.1) ** 2 + ((r["odp"] - odp) / 60) ** 2
                + ((r["wp"] - wp) / 3) ** 2 + ((r["ws"] - ws) / 3) ** 2)
    near = sorted(cands, key=dist)[:n]
    return sum(r["k"] for r in near) / len(near)


def l9(widths: list[float], spacings: list[float], cell: int) -> list[tuple[float, float, float]]:
    """Orthogonal-array L9 over (W_P, W_S, S_S) at three levels each: every pair of the three is fully crossed in the
    nine rows; the third factor's assignment rotates with ``cell`` so the library as a whole still meets all 27 triples."""
    assert len(widths) == 3 and len(spacings) == 3, "L9 needs three levels per factor"
    return [(widths[a], widths[b], spacings[(a + b + cell) % 3]) for a in range(3) for b in range(3)]


def candidates(family: str, widths: list[float], spacings: list[float], turns: list[int], offsets: list[float], min_inner: float):
    for tag, pm, sm, three_d in PAIRS[family]:
        cell = 0
        for odp in OD_LEVELS:
            ods_set = {o for o in OD_LEVELS if 0.5 * odp <= o <= 2.0 * odp}
            if family == "bs":
                ods_set |= {odp + d for d in NEAR_DELTAS}
            for ods in sorted(ods_set):
                for nt in (turns if family == "ms" else [1]):
                    cell += 1
                    triples = l9(widths, spacings, cell) if family == "ms" else [(wp, ws, 0.0) for wp in widths for ws in widths]
                    for wp, ws, s in triples:
                        for off in offsets:
                            if off and (ods != odp or wp != ws):   # offsets only on the matched diagonal (k knob, decision X3)
                                continue
                            yield {"pair": tag, "primary_metal": pm, "secondary_metal": sm, "three_d": three_d,
                                   "od_p": odp, "od_s": ods, "w_p": wp, "w_s": ws, "nt_s": nt, "s_s": s, "offset": off,
                                   "inner_p": round(inner(odp, wp, 0, 1), 3), "inner_s": round(inner(ods, ws, s, nt), 3)}


def config(family: str, p: dict, profile: str) -> dict:
    spacing = round(p["offset"] * (p["od_p"] + p["od_s"]) / 4, 2)
    c = {"process_profile": profile, "port_order": ["P1", "N1", "P2", "N2"],
         "primary_outer_diameter_um": p["od_p"], "secondary_outer_diameter_um": p["od_s"],
         "primary_width_um": p["w_p"], "secondary_width_um": p["w_s"],
         "primary_opening_um": 8.0, "secondary_opening_um": 8.0,
         "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0,
         "center_spacing_um": spacing, "primary_metal": p["primary_metal"], "secondary_metal": p["secondary_metal"],
         "ground_fixture": dict(FIXTURE)}
    if family == "ms":
        c |= {"secondary_turns": p["nt_s"], "secondary_spacing_um": p["s_s"]}
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("out", type=Path)
    ap.add_argument("--ind-rows", type=Path, required=True)
    ap.add_argument("--old-db", type=Path, required=True)
    ap.add_argument("--family", nargs="*", default=["bs", "ms"])
    ap.add_argument("--width", default="4,6,8,10")
    ap.add_argument("--ms-width", default="4,7,10")
    ap.add_argument("--ms-spacing", default="2,3,4")
    ap.add_argument("--ms-turns", default="2,3,4,5")
    ap.add_argument("--offsets", default="0,0.25,0.5,0.75")
    ap.add_argument("--min-inner-um", type=float, default=30.0)
    args = ap.parse_args()
    cal = calibration(args.ind_rows)
    offsets = [float(x) for x in args.offsets.split(",")]
    result = {"profile": args.profile, "calibration": {f"{m}:{t}": v for (m, t), v in cal.items()}, "families": {}}
    for family in args.family:
        widths = [float(x) for x in (args.width if family == "bs" else args.ms_width).split(",")]
        old = old_rows(args.old_db, family)
        generator = get_generator(f"clean_port_xfm_{family}", plugin_module="builtin:clean_port")
        points, tally, reasons = [], collections.Counter(), collections.Counter()
        for p in candidates(family, widths, [float(x) for x in args.ms_spacing.split(",")], [int(x) for x in args.ms_turns.split(",")],
                            offsets, args.min_inner_um):
            if min(p["inner_p"], p["inner_s"]) < args.min_inner_um:
                p["status"] = "skipped:inner"
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    try:
                        generator.generate(generator.config_model.model_validate(config(family, p, args.profile)), outdir=Path(tmp), gds_name="x.gds")
                        p["status"] = "ok"
                    except Exception as exc:  # noqa: BLE001 -- every refusal class is data here
                        p["status"] = "refused"
                        p["why"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                        reasons[re.sub(r"-?[0-9]+(\.[0-9]+)?", "#", p["why"])[:120]] += 1
            tally[(p["pair"], p["status"])] += 1
            if p["status"] == "ok":
                body_p = "AP" if p["primary_metal"] == "AP" else "10"
                body_s = "10"     # secondaries sit on M10 or M9; M9 has no inductor rows, so the M10 calibration stands in
                p["Lp_est_nH"] = round(cal.get((body_p, 1), 1.0) * mohan_nh(p["od_p"], p["w_p"], 2.0, 1), 4)
                p["Ls_est_nH"] = round(cal.get((body_s, p["nt_s"]), 1.0) * mohan_nh(p["od_s"], p["w_s"], p["s_s"] or 2.0, p["nt_s"]), 4)
                k = k_estimate(old, p["pair"], p["od_p"], p["od_s"] / p["od_p"], p["offset"], p["nt_s"], p["w_p"], p["w_s"])
                p["k_est"] = round(k, 3) if k is not None else None
            points.append(p)
        ok = [p for p in points if p["status"] == "ok"]
        print(f"xfm_{family}: {len(points)} candidates, {len(ok)} ok |", {f"{t}:{s}": n for (t, s), n in sorted(tally.items())})
        for key in ("Lp_est_nH", "Ls_est_nH", "k_est"):
            v = sorted(p[key] for p in ok if p.get(key) is not None)
            if v:
                print(f"  {key}: {v[0]:.3f} .. {v[-1]:.3f}  median {v[len(v) // 2]:.3f}")
        for reason, n in reasons.most_common(6):
            print(f"  {n:5d}  {reason}")
        result["families"][family] = {"grid": {"od_levels": OD_LEVELS, "near_deltas": NEAR_DELTAS if family == "bs" else [], "widths": widths,
                                               "ms_spacing": args.ms_spacing, "ms_turns": args.ms_turns, "offsets": offsets,
                                               "min_inner_um": args.min_inner_um, "opening_um": 8.0, "lead_length_um": 20.0},
                                      "tally": {f"{t}:{s}": n for (t, s), n in tally.items()}, "refusal_reasons": reasons.most_common(),
                                      "points": points}
    args.out.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
