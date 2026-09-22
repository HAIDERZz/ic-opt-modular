"""Build all six clean-port families on the packaged public demo_6m profile (safe to publish), then render them."""
import json
import sys
from pathlib import Path

from ic_opt.em.pcell import get_generator

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
FIX = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_width_um": 5.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
P4 = ["P1", "N1", "P2", "N2"]
CONFIGS = {
    "clean_port_ind_sym": {"port_order": ["P1", "N1", "CT"], "outer_diameter_um": 100.0, "width_um": 5.0, "spacing_um": 2.0, "opening_um": 8.0,
                           "lead_length_um": 20.0, "turns": 2, "top_metal": "6", "bottom_metal": "5", "ct_metal": "4"},
    "clean_port_xfm_bs": {"port_order": P4, "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 100.0, "primary_width_um": 5.0,
                          "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
                          "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0, "primary_metal": "6", "secondary_metal": "5"},
    "clean_port_xfm_ms": {"port_order": P4, "single_outer_diameter_um": 100.0, "multi_outer_diameter_um": 76.0, "single_width_um": 6.0,
                          "multi_width_um": 3.0, "single_opening_um": 8.0, "multi_opening_um": 6.0, "single_lead_length_um": 20.0,
                          "multi_lead_length_um": 15.0, "multi_turns": 3, "multi_spacing_um": 2.0, "center_spacing_um": 0.0,
                          "single_metal": "6", "multi_metal": "5"},
    "clean_port_xfm_balun": {"port_order": P4, "primary_outer_diameter_um": 200.0, "secondary_outer_diameter_um": 184.0, "primary_width_um": 5.0,
                             "secondary_width_um": 5.0, "spacing_um": 3.0, "primary_opening_um": 8.0, "secondary_opening_um": 12.0,
                             "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0, "primary_turns": 1, "secondary_turns": 1,
                             "center_spacing_um": 0.0, "balun_metal": "6"},
    "clean_port_xfm_tw": {"port_order": P4, "outer_diameter_um": 260.0, "width_um": 6.0, "spacing_um": 6.0, "ring_count": 3,
                          "opening_p_um": 10.0, "opening_n_um": 10.0, "lead_length_um": 20.0, "top_metal": "6"},
    "clean_port_xfm_il": {"port_order": P4, "outer_diameter_um": 200.0, "width_um": 5.0, "spacing_um": 2.5, "turns": 3,
                          "opening_p_um": 18.0, "opening_s_um": 18.0, "lead_p_um": 20.0, "lead_s_um": 20.0, "top_metal": "6"},
}
jobs = []
for gid, cfg in CONFIGS.items():
    g = get_generator(gid, plugin_module="builtin:clean_port")
    full = {**cfg, "process_profile": "demo_6m", "ground_fixture": dict(FIX)}
    if gid == "clean_port_xfm_ms":
        full["ground_fixture"].update({"stub_width_um": 6.0, "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0}})
    try:
        r = g.generate(g.config_model.model_validate(full), outdir=out / gid, gds_name=f"{gid}.gds")
    except Exception as exc:
        print(gid, "FAILED", type(exc).__name__, str(exc)[:300]); continue
    print(gid, "ok", r.gds_path)
    jobs.append({"gds": str(r.gds_path), "out": str(out / f"{gid}.png"), "title": f"{gid} (demo_6m, current code)"})
json.dump(jobs, open(out / "jobs.json", "w"), indent=1)
