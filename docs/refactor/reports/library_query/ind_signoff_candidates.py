"""T13.6: pick the inductor sign-off candidates with lib.suggest (predicted, built and audited designs; nothing is simulated).

Usage: ind_signoff_candidates.py LIBRARY_ROOT OUT_DIR  ->  OUT_DIR/candidates_<stratum>.json (the lib_signoff candidates= input)
The queries spread over turns levels and both metal bodies: a small single/two-turn coil, a mid one with an SRF floor,
an anchored 28 GHz target on AP; 1 / 2 / 4 nH coils on M10.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ic_opt.library import query
from ic_opt.library import suggest as sg

QUERIES = {
    "ind_sym_ap": [("Lp_lf 0.20 nH +-3 %, max Qp_peak", {"Lp_lf": {"target": 0.20e-9, "tol": 0.03}}, "max:Qp_peak", 2),
                   ("Lp_lf 0.5 nH +-3 %, SRF_p >= 60 GHz, max Qp_peak", {"Lp_lf": {"target": 0.5e-9, "tol": 0.03}, "SRF_p": {"min": 60e9}}, "max:Qp_peak", 2),
                   ("Lp@28 0.30 nH +-3 %, max Qp@28", {"Lp@28": {"target": 0.30e-9, "tol": 0.03}}, "max:Qp@28", 1)],
    "ind_sym_m10": [("Lp_lf 1.0 nH +-3 %, max Qp@10", {"Lp_lf": {"target": 1.0e-9, "tol": 0.03}}, "max:Qp@10", 2),
                    ("Lp_lf 2.0 nH +-3 %, SRF_p >= 20 GHz, max Qp_peak", {"Lp_lf": {"target": 2.0e-9, "tol": 0.03}, "SRF_p": {"min": 20e9}}, "max:Qp_peak", 2),
                    ("Lp_lf 4.0 nH +-3 %, SRF_p >= 10 GHz, max Qp_peak", {"Lp_lf": {"target": 4.0e-9, "tol": 0.03}, "SRF_p": {"min": 10e9}}, "max:Qp_peak", 1)],
}


def main() -> None:
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    lib = query.Library(root)
    for stratum, queries in QUERIES.items():
        picked, answers = [], []
        for name, targets, objective, n in queries:
            a = sg.suggest(lib, stratum, targets, objective, n=n)
            answers.append({"name": name, "targets": targets, "objective": objective, **{k: a[k] for k in ("satisfying", "satisfying_measured", "notes")},
                            "candidates": a["candidates"], "measured": a["measured"][:2]})
            picked += [{"params": c["params"], "query": name, "predicted": c["predicted"], "build": c["build"]} for c in a["candidates"]]
            print(f"{stratum}: {name}: {a['satisfying']} satisfy ({a['satisfying_measured']} measured), {len(a['candidates'])} built {a['notes']}")
            for c in a["candidates"]:
                print("   ", {k: round(v, 2) for k, v in c["params"].items()},
                      {q: round(v["value"] * (1e9 if q.startswith("L") else 1e-9 if q.startswith("SRF") else 1), 4) for q, v in c["predicted"].items()})
        (out / f"candidates_{stratum}.json").write_text(json.dumps({"stratum": stratum, "candidates": picked, "queries": answers}, indent=1, default=float))


if __name__ == "__main__":
    main()
