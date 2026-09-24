"""Is D4 (crossover far pad poking into the neighbouring ring's chamfer) open for ind_sym NT>=3 on the current code?
Generate a small-OD x NT grid on the public demo_6m profile and run the packaged DRC audit on every accepted GDS."""
import collections
import itertools
import json
import sys
import tempfile
from pathlib import Path

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.drc_audit import audit_gds

FIX = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
g = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
tally = collections.Counter()
viol = []
reasons = collections.Counter()
for od, nt, w, s in itertools.product([60, 70, 80, 90, 100, 120], [3, 4, 5], [3, 4, 5, 6], [2, 3]):
    cfg = {"outer_diameter_um": od, "width_um": w, "spacing_um": s, "opening_um": 8.0, "lead_length_um": 20.0, "turns": nt,
           "top_metal": "6", "bottom_metal": "5", "ground_fixture": FIX, "process_profile": "demo_6m", "port_order": ["P1", "N1"]}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = g.generate(g.config_model.model_validate(cfg), outdir=Path(tmp), gds_name="x.gds")
        except Exception as exc:  # noqa: BLE001 -- a refusal is counted with its reason, the grid goes on
            tally["refused"] += 1; reasons[type(exc).__name__ + ": " + str(exc)[:80]] += 1
            continue
        rep = audit_gds(r.gds_path, "demo_6m")
        real = [v for v in rep.violations if (v.kind, v.layer) != ("max_width", "M1")]
        n = len(real)
        tally["clean" if n == 0 else "violations"] += 1
        if n:
            viol.append({"od": od, "nt": nt, "w": w, "s": s, "n": n, "kinds": sorted({(v.kind, v.layer) for v in real}), "first": str(real[0])[:200]})
print(dict(tally)); print(reasons.most_common(6))
for v in viol[:12]:
    print(v)
Path(sys.argv[1]).write_text(json.dumps({"tally": dict(tally), "violations": viol}, indent=1))
