"""Assemble the N28 inductor query dataset from the library's run stores, and check the data on the way in.

Every ok observation's sNp is re-measured with ic_opt's measure kernel under one uniform definition (the NT=1
projects swept to 250 GHz, the rest to 150 GHz, so band-dependent quantities are recomputed on 0-150 GHz):
  L_lf, L_res, SRF (full sweep, None if no resonance), Q_peak150 and its frequency, and anchored L / Q at 10 / 28 / 60 GHz.
Checks: stored quantities.json reproduces (L_lf, SRF bit-identical), no duplicate coordinates, sNp passivity
(largest singular value of S <= 1 + 1e-6 at every frequency), frequency grid and port count as specified.

Usage: ind_dataset.py LIBRARY_ROOT OUT.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from ic_opt.em import measure, touchstone

PROJECTS = {"ind_sym_ap": "AP", "ind_sym_ap_nt1": "AP", "ind_sym_m10": "M10", "ind_sym_m10_nt1": "M10"}
ANCHORS_GHZ = (10, 28, 60)
TOPO = measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"])


def at(q: measure.Quantities, name: str, f_hz: float) -> float | None:
    try:
        return q.at(name, f_hz)
    except measure.MeasureError:
        return None


def row(project: str, obs: dict, sims: Path) -> dict:
    work = sims / obs["obs_id"]
    snp = work / "em" / "ind" / "ind.s2p"
    ts = touchstone.read(snp)
    q = measure.quantities(ts.freqs, ts.s, TOPO, z0=ts.z0)
    stored = json.loads((work / "ind" / "nominal" / "quantities.json").read_text())
    band = ts.freqs <= 150e9
    qb = np.where(band & (ts.freqs > 0), q.curves["Qp"], np.nan)
    i_pk = int(np.nanargmax(qb))
    sv = np.linalg.svd(ts.s, compute_uv=False).max(axis=1)
    p = obs["params"]
    return {
        "body": PROJECTS[project], "project": project, "obs": obs["obs_id"],
        "od": float(p["outer_diameter_um"]), "w": float(p["width_um"]), "s": float(p["spacing_um"]), "nt": int(p["turns"]),
        "L_lf": q.scalars["Lp_lf"], "L_res": q.scalars["Lp_res"], "SRF": q.scalars["SRF_p"],
        "Q_peak150": float(qb[i_pk]), "f_Qpeak150": float(ts.freqs[i_pk]), "Qpeak_at_band_edge": bool(ts.freqs[i_pk] >= 150e9),
        **{f"L@{g}": at(q, "Lp", g * 1e9) for g in ANCHORS_GHZ}, **{f"Q@{g}": at(q, "Qp", g * 1e9) for g in ANCHORS_GHZ},
        "stop_ghz": float(ts.freqs[-1] / 1e9), "n_freq": len(ts.freqs), "n_ports": ts.n_ports,
        "stored_matches": stored["Lp_lf"] == q.scalars["Lp_lf"] and stored.get("SRF_p") == q.scalars["SRF_p"],
        "max_singular": float(sv.max()),
        "snp": str(snp),
    }


def main() -> None:
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows, notes = [], []
    for project in PROJECTS:
        store = root / project / ".icopt"
        obs = [json.loads(line) for line in (store / "observations.jsonl").read_text().splitlines()]
        bad = [o["obs_id"] for o in obs if o["status"] != "ok"]
        if bad:
            notes.append(f"{project}: {len(bad)} not-ok observations skipped: {bad[:5]}")
        rows += [row(project, o, store / "sims") for o in obs if o["status"] == "ok"]
    keys = [(r["body"], r["od"], r["w"], r["s"], r["nt"]) for r in rows]
    dup = len(keys) - len(set(keys))
    checks = {
        "rows": len(rows),
        "duplicate_coordinates": dup,
        "stored_quantities_reproduced": sum(r["stored_matches"] for r in rows),
        "passive_rows": sum(r["max_singular"] <= 1 + 1e-6 for r in rows),
        "max_singular_value": max(r["max_singular"] for r in rows),
        "grid_150": sum(r["n_freq"] == 151 and r["stop_ghz"] == 150 for r in rows),
        "grid_250": sum(r["n_freq"] == 251 and r["stop_ghz"] == 250 for r in rows),
        "two_port": sum(r["n_ports"] == 2 for r in rows),
        "srf_in_band": sum(r["SRF"] is not None for r in rows),
        "qpeak150_at_band_edge": sum(r["Qpeak_at_band_edge"] for r in rows),
        "notes": notes,
    }
    out.write_text(json.dumps({"checks": checks, "rows": rows}, indent=1))
    print(json.dumps(checks, indent=1))


if __name__ == "__main__":
    main()
