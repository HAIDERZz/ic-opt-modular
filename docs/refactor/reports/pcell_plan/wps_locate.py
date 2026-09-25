"""Locate the wide-parallel-spacing edge pairs the packaged audit flags on an ind_sym the generator accepted (demo_6m, W=5, S=2)."""
import json
import sys
from pathlib import Path

import klayout.db as kdb

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.drc_audit import _edge_key, _region_for, _um_to_dbu
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter

out = Path(sys.argv[1])
od, nt, w, s = (int(x) for x in sys.argv[2:6])
FIX = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
g = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
cfg = {"outer_diameter_um": od, "width_um": w, "spacing_um": s, "opening_um": 8.0, "lead_length_um": 20.0, "turns": nt,
       "metal": "6", "ground_fixture": FIX, "process_profile": "demo_6m", "port_order": ["P1", "N1"]}
r = g.generate(g.config_model.model_validate(cfg), outdir=out, gds_name=f"ind_sym_od{od}_nt{nt}_w{w}_s{s}.gds")
adapter = get_geometry_rule_adapter("demo_6m")
layout = kdb.Layout(); layout.read(str(r.gds_path)); top = layout.top_cells()[0]; top.flatten(True)
region = _region_for(layout, top, adapter.layer("M6").drawing)
dbu = layout.dbu
rule = adapter.profile.layout_rules.passive_region.wide_parallel_spacing[0]
narrow = {_edge_key(e) for pair in region.width_check(_um_to_dbu(rule.when_width_gt_um, dbu) + 1, True, kdb.Metrics.Euclidian).each() for e in (pair.first, pair.second)}
pairs = []
for pair in region.space_check(_um_to_dbu(rule.min_space_um, dbu), True, kdb.Metrics.Euclidian, 1, _um_to_dbu(rule.when_parallel_length_gt_um, dbu) + 1).each():
    if _edge_key(pair.first) not in narrow and _edge_key(pair.second) not in narrow:
        e1, e2 = pair.first, pair.second
        bb = kdb.Box(e1.p1, e1.p2) + kdb.Box(e2.p1, e2.p2)
        pairs.append({"e1": str(e1), "e2": str(e2), "distance_um": round(pair.distance() * dbu, 4), "bbox_um": [bb.left * dbu, bb.bottom * dbu, bb.right * dbu, bb.top * dbu]})
print(r.gds_path); print(json.dumps(pairs, indent=1))
(out / "pairs.json").write_text(json.dumps({"gds": str(r.gds_path), "pairs": pairs}, indent=1))
