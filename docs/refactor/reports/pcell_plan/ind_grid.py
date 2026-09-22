"""The proposed inductor library grid: build every point with the current generator (no EMX) and report what it covers.

Feasibility comes from the generator itself (fail-closed refusals, with reasons);
the inductance each point will land on is estimated with Mohan's current-sheet
expression calibrated against the recorded N28 library, so the grid can be judged
before a single EMX second is spent.

Usage: ind_grid.py PROFILE OUT.json [--db <device_db.sqlite>]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sqlite3
import tempfile
from pathlib import Path

from ic_opt.em.pcell import get_generator

MU0 = 4e-7 * math.pi
# octagon current-sheet coefficients (Mohan 1999)
C1, C2, C3, C4 = 1.07, 2.29, 0.0, 0.19


def mohan_nh(od_um: float, w_um: float, s_um: float, nt: int) -> float:
    """Single-ended current-sheet inductance of an octagonal spiral, in nH."""
    d_in = od_um - 2 * nt * w_um - 2 * (nt - 1) * s_um
    d_avg = (od_um + d_in) / 2
    rho = (od_um - d_in) / (od_um + d_in)
    l_h = MU0 * nt * nt * (d_avg * 1e-6) * C1 / 2 * (math.log(C2 / rho) + C3 * rho + C4 * rho * rho)
    return l_h * 1e9


def recorded(db: Path) -> list[dict]:
    q = """select s.params_json, max(case when m.name='Lp_lf' then m.value end), max(case when m.name='SRF_p' then m.value end)
           from samples s join metrics m on m.sample_id=s.id
           where s.family='ind_sym' and s.status='ok' and m.freq_hz is null group by s.id"""
    out = []
    for params, l, srf in sqlite3.connect(db).execute(q):
        d = json.loads(params)
        if l:
            out.append({**d, "L_nH": l * 1e9, "srf_GHz": srf / 1e9 if srf else None})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("out", type=Path)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--od", default="60,80,100,120,150,180,210,240")
    ap.add_argument("--width", default="4,5,6,7,8,9,10")
    ap.add_argument("--spacing", default="2,3,4")
    ap.add_argument("--turns", default="1,2,3,4,5")
    ap.add_argument("--bodies", default="AP,10")
    ap.add_argument("--min-inner-um", type=float, default=30.0)
    args = ap.parse_args()

    ods = [float(x) for x in args.od.split(",")]
    widths = [float(x) for x in args.width.split(",")]
    spacings = [float(x) for x in args.spacing.split(",")]
    turns = [int(x) for x in args.turns.split(",")]
    bodies = args.bodies.split(",")

    calibration = None
    if args.db:
        ref = recorded(args.db)
        ratios = [r["L_nH"] / mohan_nh(r["outer_diameter_um"], r["width_um"], r["spacing_um"], r["turns"]) for r in ref]
        ratios.sort()
        calibration = ratios[len(ratios) // 2]
        spread = [abs(r / calibration - 1) for r in ratios]
        spread.sort()
        print(f"calibration against {len(ref)} recorded rows: median measured/Mohan = {calibration:.3f}, "
              f"|error| median {100 * spread[len(spread) // 2]:.1f}% p90 {100 * spread[int(0.9 * len(spread))]:.1f}%")

    generator = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
    fixture = {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
    points, tally, reasons = [], collections.Counter(), collections.Counter()
    for metal in bodies:
        for nt in turns:
            for od in ods:
                for w in widths:
                    for s in (spacings if nt > 1 else spacings[:1]):     # NT=1 geometry does not depend on spacing
                        inner = od - 2 * nt * w - 2 * (nt - 1) * s
                        row = {"metal": metal, "turns": nt, "outer_diameter_um": od, "width_um": w, "spacing_um": s,
                               "inner_um": round(inner, 3), "L_est_nH": None, "status": None, "why": ""}
                        if inner < args.min_inner_um:
                            row["status"] = "skipped:inner"
                            tally[(metal, "skipped:inner")] += 1
                            points.append(row)
                            continue
                        config = {"process_profile": args.profile, "port_order": ["P1", "N1"], "outer_diameter_um": od,
                                  "width_um": w, "spacing_um": s, "opening_um": 8.0, "lead_length_um": 20.0, "turns": nt,
                                  "metal": metal, "ground_fixture": {**fixture, "stub_width_um": w}}
                        with tempfile.TemporaryDirectory() as tmp:
                            try:
                                generator.generate(generator.config_model.model_validate(config), outdir=Path(tmp), gds_name="x.gds")
                            except Exception as exc:  # noqa: BLE001 -- every refusal class is data here
                                row["status"] = "refused"
                                row["why"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                                reasons[re.sub(r"-?[0-9]+(\.[0-9]+)?", "#", row["why"])[:110]] += 1
                                tally[(metal, "refused")] += 1
                                points.append(row)
                                continue
                        row["status"] = "ok"
                        if calibration:
                            row["L_est_nH"] = round(calibration * mohan_nh(od, w, s, nt), 4)
                        tally[(metal, "ok")] += 1
                        points.append(row)

    ok = [p for p in points if p["status"] == "ok"]
    print({f"{m}:{k}": n for (m, k), n in sorted(tally.items())}, "| total ok", len(ok))
    if calibration:
        ls = sorted(p["L_est_nH"] for p in ok)
        print(f"estimated L nH: {ls[0]:.3f} .. {ls[-1]:.2f}  (decades: " +
              ", ".join(f"{lo}-{hi}nH: {sum(1 for x in ls if lo <= x < hi)}" for lo, hi in ((0, 0.3), (0.3, 1), (1, 3), (3, 10))) + ")")
    for reason, n in reasons.most_common(8):
        print(f"{n:4d}  {reason}")
    args.out.write_text(json.dumps({"profile": args.profile, "grid": {"od": ods, "width": widths, "spacing": spacings, "turns": turns, "bodies": bodies,
                                                                      "min_inner_um": args.min_inner_um, "opening_um": 8.0, "lead_length_um": 20.0},
                                    "calibration": calibration, "tally": {f"{m}:{k}": n for (m, k), n in tally.items()},
                                    "refusal_reasons": reasons.most_common(), "points": points}, indent=1))


if __name__ == "__main__":
    main()
