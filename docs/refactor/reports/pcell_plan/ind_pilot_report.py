"""Read the inductor pilot's arms (ind_pilot.py) and report convergence, full-wave vs quasistatic, and cost.

Usage: ind_pilot_report.py PILOT_DIR  -> PILOT_DIR/pilot.json + PILOT_DIR/fig_*.png
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ic_opt.em import measure, touchstone

pilot = Path(sys.argv[1])
FREQS = (1e9, 10e9, 30e9, 60e9, 100e9)
TOPO = measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"])


def log_stats(log: Path) -> dict:
    text = log.read_text(errors="replace")
    grab = lambda pattern: (float(m.group(1)) if (m := re.search(pattern, text)) else None)
    return {"wall_s": grab(r"Wall-clock time ([0-9.]+) sec"), "peak_gb": grab(r"Peak memory usage ([0-9.]+) GB"),
            "basis": grab(r"(\d+) basis functions")}


arms = {}
for arm_dir in sorted(p for p in pilot.iterdir() if (p / ".icopt" / "pilot_arm.json").exists()):
    meta = json.loads((arm_dir / ".icopt" / "pilot_arm.json").read_text())
    runs = {}
    for i, geom in enumerate(meta["geometries"], 1):
        work = arm_dir / ".icopt" / "sims" / f"obs_{i:04d}" / "em" / "ind"
        snp = work / "ind.s2p"
        if not snp.exists():
            continue
        ts = touchstone.read(snp)
        q = measure.quantities(ts.freqs, ts.s, TOPO, z0=ts.z0)
        runs[geom] = {"L_nH": {f"{f / 1e9:g}": q.at("Lp", f) * 1e9 for f in FREQS}, "Q": {f"{f / 1e9:g}": q.at("Qp", f) for f in FREQS},
                      "L_lf_nH": q.scalars["Lp_lf"] * 1e9, "Q_peak": q.scalars["Qp_peak"],
                      "f_Qpeak_GHz": float(q.freqs[int(np.nanargmax(np.where(q.freqs > 0, q.curves["Qp"], np.nan)))] / 1e9),
                      "SRF_GHz": q.scalars["SRF_p"] / 1e9 if q.scalars["SRF_p"] else None,
                      "curve_f": (q.freqs / 1e9).tolist(), "curve_L": (q.curves["Lp"] * 1e9).tolist(), "curve_Q": q.curves["Qp"].tolist(),
                      **log_stats(work / "emx.log")}
    arms[meta["arm"]] = {**meta, "runs": runs}

# convergence: every arm against the finest full-wave run of the same geometry (xfine where it exists, else fine)
ref = {**arms.get("fw_fine", {}).get("runs", {}), **arms.get("fw_xfine", {}).get("runs", {})}


def rel(a, b):
    return None if a is None or b is None or b == 0 else 100.0 * (a - b) / abs(b)


table = []
for arm, a in arms.items():
    for geom, r in a["runs"].items():
        base = ref.get(geom)
        row = {"arm": arm, "geom": geom, "wall_s": r["wall_s"], "peak_gb": r["peak_gb"], "basis": r["basis"], "L_lf_nH": r["L_lf_nH"],
               "Q_peak": r["Q_peak"], "f_Qpeak_GHz": r["f_Qpeak_GHz"], "SRF_GHz": r["SRF_GHz"]}
        if base:
            row["dL_lf_%"] = rel(r["L_lf_nH"], base["L_lf_nH"])
            row["dL_max_%"] = max(abs(rel(r["L_nH"][k], base["L_nH"][k])) for k in r["L_nH"] if base["SRF_GHz"] is None or float(k) < 0.8 * base["SRF_GHz"])
            row["dQ_peak_%"] = rel(r["Q_peak"], base["Q_peak"])
            row["dQ_max_%"] = max(abs(rel(r["Q"][k], base["Q"][k])) for k in r["Q"] if base["SRF_GHz"] is None or float(k) < 0.8 * base["SRF_GHz"])
            row["dSRF_%"] = rel(r["SRF_GHz"], base["SRF_GHz"])
        table.append(row)
(pilot / "pilot.json").write_text(json.dumps({"arms": {k: {kk: vv for kk, vv in v.items() if kk != "runs"} for k, v in arms.items()},
                                              "table": table, "runs": {k: v["runs"] for k, v in arms.items()}}, indent=1))

for row in table:
    print(f"{row['arm']:<16}{row['geom']:<6} wall {row['wall_s'] or 0:7.1f}s peak {row['peak_gb'] or 0:5.2f}GB basis {int(row['basis'] or 0):7d} | "
          f"L_lf {row['L_lf_nH']:.4f} Qpk {row['Q_peak']:6.2f}@{row['f_Qpeak_GHz']:.0f}G SRF {row['SRF_GHz'] or float('nan'):6.1f} | "
          + " ".join(f"{k} {row[k]:+6.2f}" for k in ("dL_lf_%", "dL_max_%", "dQ_peak_%", "dQ_max_%", "dSRF_%") if row.get(k) is not None))

# figures: L(f) and Q(f) per geometry, every arm overlaid
styles = {"qs_coarse": ("#9aa7b4", ":"), "qs_medium": ("#6f8193", ":"), "qs_fine": ("#44586b", ":"),
          "fw_coarse": ("#e3a15f", "-"), "fw_medium": ("#c9661f", "-"), "fw_fine": ("#8a3a0d", "-"), "fw_xfine": ("#3a1604", "-"), "fw_medium_m1_3d": ("#3c6e47", "--")}
for geom in ("small", "mid", "large"):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.8), dpi=110)
    for arm, a in arms.items():
        r = a["runs"].get(geom)
        if not r:
            continue
        c, ls = styles.get(arm, ("black", "-"))
        f = np.array(r["curve_f"])
        ax1.plot(f, r["curve_L"], color=c, ls=ls, lw=1.4, label=arm)
        ax2.plot(f, r["curve_Q"], color=c, ls=ls, lw=1.4, label=arm)
    base = ref.get(geom)
    top = min(150, 1.3 * base["SRF_GHz"]) if base and base["SRF_GHz"] else 150
    ax1.set_xlim(0, top)
    ax2.set_xlim(0, top)
    if base:
        ax1.set_ylim(0, 2.2 * base["L_lf_nH"])
        ax2.set_ylim(0, 1.25 * base["Q_peak"])
    ax1.set_xlabel("GHz")
    ax1.set_ylabel("Lp (nH)")
    ax2.set_xlabel("GHz")
    ax2.set_ylabel("Qp")
    ax1.grid(alpha=0.3)
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=7, loc="upper right")
    fig.suptitle(f"{geom}: dotted = quasistatic, solid = full-wave (darker = finer mesh)", fontsize=10)
    fig.tight_layout()
    fig.savefig(pilot / f"fig_{geom}.png")
    plt.close(fig)
print("wrote", pilot / "pilot.json")
