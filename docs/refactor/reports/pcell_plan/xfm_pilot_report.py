"""Read the transformer pilot's arms (xfm_pilot.py): fine vs extra-fine convergence of L / k / Q / SRF, and cost.

Usage: xfm_pilot_report.py PILOT_DIR  -> PILOT_DIR/pilot.json + PILOT_DIR/fig_<geometry>.png
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
FREQS = (1e9, 10e9, 30e9, 60e9, 100e9, 150e9, 200e9)
TOPO = measure.Topology.from_labels([("P1", "N1"), ("N2", "P2")], [], ["P1", "N1", "P2", "N2"])
SCALARS = ("Lp_lf", "Ls_lf", "k_lf", "Qp_peak", "Qs_peak", "SRF_p", "SRF_s")


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
        work = arm_dir / ".icopt" / "sims" / f"obs_{i:04d}" / "em" / "xfm"
        snps = sorted(work.glob("xfm.s*p"))
        if not snps:
            continue
        ts = touchstone.read(snps[0])
        q = measure.quantities(ts.freqs, ts.s, TOPO, z0=ts.z0)
        lim = min(s for s in (q.scalars["SRF_p"], q.scalars["SRF_s"], ts.freqs[-1]) if s) * 0.8
        runs[geom] = {"scalars": {k: q.scalars.get(k) for k in SCALARS},
                      "at": {name: {f"{f / 1e9:g}": q.at(name, f) for f in FREQS if f <= ts.freqs[-1] and f < lim} for name in ("Lp", "Ls", "k", "Qp", "Qs")},
                      "curves": {"f": (q.freqs / 1e9).tolist(), **{n: q.curves[n].tolist() for n in ("Lp", "Ls", "k", "Qp", "Qs")}},
                      **log_stats(work / "emx.log")}
    arms[meta["arm"]] = {**meta, "runs": runs}


def rel(a, b):
    return None if a is None or b is None or b == 0 else 100.0 * (a - b) / abs(b)


table = []
for fam in ("bs", "ms"):
    fine, xfine = arms.get(f"{fam}_fine", {}).get("runs", {}), arms.get(f"{fam}_xfine", {}).get("runs", {})
    for geom in fine:
        f, x = fine[geom], xfine.get(geom)
        row = {"geom": geom, "fine": {k: f[k] for k in ("wall_s", "peak_gb", "basis")}, "xfine": {k: x[k] for k in ("wall_s", "peak_gb", "basis")} if x else None,
               "scalars": f["scalars"], "d_scalar_%": {}, "d_curve_max_%": {}}
        if x:
            row["d_scalar_%"] = {k: rel(f["scalars"][k], x["scalars"][k]) for k in SCALARS}
            for name in ("Lp", "Ls", "k", "Qp", "Qs"):
                common = [k for k in f["at"][name] if k in x["at"][name]]
                row["d_curve_max_%"][name] = max((abs(rel(f["at"][name][k], x["at"][name][k])) for k in common), default=None)
        table.append(row)
(pilot / "pilot.json").write_text(json.dumps({"arms": {k: {kk: vv for kk, vv in v.items() if kk != "runs"} for k, v in arms.items()},
                                              "table": table, "runs": {k: v["runs"] for k, v in arms.items()}}, indent=1))

for row in table:
    s = row["scalars"]
    print(f"{row['geom']:<9} fine wall {row['fine']['wall_s'] or 0:6.1f}s peak {row['fine']['peak_gb'] or 0:5.2f}GB"
          + (f" | xfine wall {row['xfine']['wall_s'] or 0:7.1f}s peak {row['xfine']['peak_gb'] or 0:6.2f}GB" if row["xfine"] else "")
          + f" | Lp {s['Lp_lf'] * 1e9:.4f} Ls {s['Ls_lf'] * 1e9:.4f} k {s['k_lf']:.4f} Qp {s['Qp_peak']:.2f} Qs {s['Qs_peak']:.2f}"
          + f" SRFp {(s['SRF_p'] or 0) / 1e9:.1f} SRFs {(s['SRF_s'] or 0) / 1e9:.1f}")
    if row["d_scalar_%"]:
        print("          fine vs xfine scalars %: " + " ".join(f"{k} {v:+.2f}" for k, v in row["d_scalar_%"].items() if v is not None))
        print("          fine vs xfine curve max %: " + " ".join(f"{k} {v:.2f}" for k, v in row["d_curve_max_%"].items() if v is not None))

for geom in {g for a in arms.values() for g in a["runs"]}:
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
    for arm, a in sorted(arms.items()):
        r = a["runs"].get(geom)
        if not r:
            continue
        c = r["curves"]
        f = np.array(c["f"])
        style = "-" if a["mesh"] == "fine" else "--"
        axes[0].plot(f, np.array(c["Lp"]) * 1e9, style, label=f"Lp {a['mesh']}")
        axes[0].plot(f, np.array(c["Ls"]) * 1e9, style, label=f"Ls {a['mesh']}")
        axes[1].plot(f, c["Qp"], style, label=f"Qp {a['mesh']}")
        axes[1].plot(f, c["Qs"], style, label=f"Qs {a['mesh']}")
        axes[2].plot(f, c["k"], style, label=f"k {a['mesh']}")
    scal = next(a["runs"][geom]["scalars"] for a in arms.values() if geom in a["runs"])
    top = min(s for s in (scal["SRF_p"], scal["SRF_s"]) if s) / 1e9 * 0.9 if (scal["SRF_p"] or scal["SRF_s"]) else None
    for ax, ylabel, ylim in ((axes[0], "L (nH)", None), (axes[1], "Q", (0, None)), (axes[2], "k", (0, 1))):
        ax.set_xlabel("GHz")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
        if top:
            ax.set_xlim(0, top)
        if ylim:
            ax.set_ylim(*ylim)
    if top:
        lo, hi = axes[0].get_ylim()
        axes[0].set_ylim(max(lo, 0), min(hi, 3 * max(scal["Lp_lf"], scal["Ls_lf"]) * 1e9))
    fig.suptitle(f"{geom}: fine (solid) vs extra-fine (dashed), up to 0.9 x SRF", fontsize=10)
    fig.tight_layout()
    fig.savefig(pilot / f"fig_{geom}.png", dpi=100)
    plt.close(fig)
print("figures in", pilot)
