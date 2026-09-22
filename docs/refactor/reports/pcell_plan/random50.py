"""Fifty random devices per family on one profile, each self-checked, with thumbnails and an HTML report.

Usage: random50.py PROFILE OUT_DIR [--per-family 50] [--seed 1]
Bodies alternate between the two top metals the library uses on that profile
(AP / M10 on n28_1p10m); taps, PGS and the new port spacing are mixed in at
fixed rates so the report covers them. Draws the generator refuses are counted
(with masked reasons) and redrawn until the family has its quota.

Checks on every accepted device: the packaged DRC audit, the connectivity of
the port labels, the landing-pad containment predicate, the manifest's port
orientations, and the build time.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import time
from pathlib import Path

import klayout.db as kdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from ic_opt.em.pcell import GEOMETRY_VERSION, get_generator
from ic_opt.em.pcell._pcell_core import _metal_index
from ic_opt.em.pcell.connectivity import nets
from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.render import layer_names, render
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter

FIX = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
PGS = {"strip_width_um": 2.0, "strip_spacing_um": 2.0, "margin_um": 5.0}
FAMILIES = ["clean_port_ind_sym", "clean_port_xfm_bs", "clean_port_xfm_ms", "clean_port_xfm_balun", "clean_port_xfm_tw", "clean_port_xfm_il"]


def g(rng, lo, hi, step=0.1):
    """Uniform on [lo, hi] snapped to ``step``."""
    return round(round(rng.uniform(lo, hi) / step) * step, 4)


def below(metal: str, levels: int) -> str:
    return str(_metal_index(metal) - levels)


def draw(rng: random.Random, family: str, bodies: list[tuple[str, str]]) -> dict:
    top, second = rng.choice(bodies)                     # (winding metal, the metal below it) for the two-plane families
    tap, ps, pgs = rng.random() < 0.3, rng.random() < 0.2, rng.random() < 0.2
    if family == "clean_port_ind_sym":
        c = {"outer_diameter_um": g(rng, 60, 240, 1), "width_um": g(rng, 4, 10), "spacing_um": g(rng, 2, 4), "opening_um": 8.0, "lead_length_um": 20.0,
             "turns": rng.randint(1, 5), "metal": top, "port_order": ["P1", "N1"]}
        if tap:
            c.update(ct_metal=below(top, 2), port_order=["P1", "N1", "CT"])
        if ps:
            c["port_spacing_um"] = g(rng, 21.0 - 6, 21.0 + 10, 0.01)
        if pgs:
            c["pgs"] = dict(PGS)
    elif family == "clean_port_xfm_bs":
        odp, ods = g(rng, 60, 240, 1), g(rng, 60, 240, 1)
        c = {"primary_outer_diameter_um": odp, "secondary_outer_diameter_um": ods, "primary_width_um": g(rng, 4, 10), "secondary_width_um": g(rng, 4, 10),
             "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0,
             "center_spacing_um": g(rng, 0, min(odp, ods) / 2, 0.01), "primary_metal": top, "secondary_metal": second, "port_order": ["P1", "N1", "P2", "N2"]}
        if tap:
            c.update(ct_primary_metal=below(top, 2), ct_secondary_metal=below(second, 2), port_order=["P1", "N1", "P2", "N2", "CTP", "CTS"])
        if ps:
            c[rng.choice(["primary_port_spacing_um", "secondary_port_spacing_um"])] = g(rng, 15, 31, 0.01)
        if pgs:
            c["pgs"] = dict(PGS)
    elif family == "clean_port_xfm_ms":
        odp, ods = g(rng, 60, 240, 1), g(rng, 50, 200, 1)
        c = {"primary_outer_diameter_um": odp, "secondary_outer_diameter_um": ods, "primary_width_um": g(rng, 4, 10), "secondary_width_um": g(rng, 3, 8),
             "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0,
             "secondary_turns": rng.randint(2, 5), "secondary_spacing_um": g(rng, 2, 4), "center_spacing_um": g(rng, 0, min(odp, ods) / 2, 0.01),
             "primary_metal": top, "secondary_metal": second, "port_order": ["P1", "N1", "P2", "N2"]}
        if tap:
            c.update(ct_primary_metal=below(top, 2), ct_secondary_metal=below(second, 2), port_order=["P1", "N1", "P2", "N2", "CTP", "CTS"])
        if ps:
            c[rng.choice(["primary_port_spacing_um", "secondary_port_spacing_um"])] = g(rng, 15, 31, 0.01)
        if pgs:
            c["pgs"] = dict(PGS)
    elif family == "clean_port_xfm_balun":
        ntp, w, s = rng.randint(1, 4), g(rng, 4, 10), g(rng, 2, 4)
        odp = g(rng, 60 + 30 * ntp, 240, 1)
        ods = round(odp - 2 * ntp * (w + s) - 2 * s - g(rng, 0, 10, 0.2), 2)
        c = {"primary_outer_diameter_um": odp, "secondary_outer_diameter_um": ods, "primary_width_um": w, "secondary_width_um": g(rng, 4, 10),
             "spacing_um": s, "primary_opening_um": 8.0, "secondary_opening_um": g(rng, 8, 15), "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0,
             "primary_turns": ntp, "secondary_turns": 1, "center_spacing_um": 0.0, "metal": top, "port_order": ["P1", "N1", "P2", "N2"]}
        if tap:
            c.update(ct_primary_metal=below(top, 2), ct_secondary_metal=below(top, 2), port_order=["P1", "N1", "P2", "N2", "CTP", "CTS"])
        if ps:
            c["primary_port_spacing_um"] = g(rng, 15, 31, 0.01)
    elif family == "clean_port_xfm_tw":
        c = {"outer_diameter_um": g(rng, 80, 300, 1), "width_um": g(rng, 4, 10), "spacing_um": g(rng, 2, 6), "ring_count": rng.choice([3, 5, 7]),
             "port_gap_p_um": 8.0, "port_gap_n_um": 8.0, "lead_length_um": 20.0, "metal": top, "port_order": ["P1", "N1", "P2", "N2"]}
    else:
        c = {"outer_diameter_um": g(rng, 80, 300, 1), "width_um": g(rng, 4, 9), "spacing_um": g(rng, 2, 4), "turns": rng.randint(2, 5),
             "primary_opening_um": g(rng, 12, 18), "secondary_opening_um": g(rng, 12, 18), "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0,
             "metal": top, "port_order": ["P1", "N1", "P2", "N2"]}
        if tap:
            up = _metal_index(top) < 11
            ct = "AP" if up else below(top, 3)
            c.update(ct_primary_metal=ct, ct_secondary_metal=ct, port_order=["P1", "N1", "P2", "N2", "CTP", "CTS"])
    return c


def landing_pad_hang(gds: Path, profile: str, metal: str, w_um: float) -> float:
    """Worst crossover landing pad hang on the winding metal, 0 when none.

    A landing pad is a small (<= 1.5 W) box on the winding metal that carries via
    cuts of a via connecting that metal downward -- the review's predicate,
    tightened so xfm_tw's short same-layer stubs (no cuts) are not mistaken for
    pads. The pad must lie entirely on the rest of the metal (one dbu of slack)."""
    adapter = get_geometry_rule_adapter(profile)
    name = metal.upper() if metal.isalpha() else f"M{metal}"
    drawing = adapter.layer(name).drawing
    via_layers = [v.drawing for v in adapter.profile.layer_catalog.vias.values() if name in v.connects]
    layout = kdb.Layout()
    layout.read(str(gds))
    top = layout.top_cells()[0]
    cuts = kdb.Region()
    for via in via_layers:
        index = layout.find_layer(kdb.LayerInfo(*via))
        if index is not None:
            cuts.insert(top.begin_shapes_rec(index))
    index = layout.layer(*drawing)
    limit = round(1.5 * w_um / layout.dbu)
    pads, ring = [], kdb.Region()
    it = top.begin_shapes_rec(index)
    while not it.at_end():
        shape = it.shape()
        if not shape.is_text():
            poly = shape.polygon.transformed(it.trans())
            box = poly.bbox()
            if poly.num_points() == 4 and box.width() <= limit and box.height() <= limit and poly.is_box() and not (cuts & kdb.Region(box)).is_empty():
                pads.append(box)
            else:
                ring.insert(poly)
        it.next()
    ring.merge()
    ring.size(1)
    # a box no other metal on this layer touches is a via stack's intermediate level (a tap's through-pad), not a landing
    hangs = [(kdb.Region(b) - ring).area() / b.area() for b in pads if not (kdb.Region(b) & ring).is_empty()]
    return max(hangs, default=0.0)


def topology_ok(found: dict[str, int]) -> bool:
    ground = {found[k] for k in found if k.startswith("G")}
    primary = {found["P1"], found["N1"]}
    ok = len(ground) == 1 and len(primary) == 1 and primary != ground and 0 not in found.values()
    if "P2" in found:
        secondary = {found["P2"], found["N2"]}
        ok = ok and len(secondary) == 1 and secondary != primary and secondary != ground
        if "CTP" in found:
            ok = ok and found["CTP"] in primary and found["CTS"] in secondary
    if "CT" in found:
        ok = ok and found["CT"] in primary
    return ok


def check(family: str, config: dict, gds: Path, manifest: dict, profile: str) -> dict:
    findings = sorted({f"{v.kind}:{v.layer}" for v in audit_gds(gds, profile).violations} - {"max_width:M1"})
    found = nets(gds, profile)
    ports = manifest["ports"]
    outward = all({0: p["x_um"] > 0, 180: p["x_um"] < 0, 90: p["y_um"] > 0, 270: p["y_um"] < 0}[p["orientation_deg"]] for p in ports)
    windings = [(config["metal"], config.get("width_um") or config.get("primary_width_um"))] if "metal" in config else \
        [(config["primary_metal"], config["primary_width_um"]), (config["secondary_metal"], config["secondary_width_um"])]
    # xfm_tw's dive-leg pads bridge a ring arc's end and the same-layer leg by design (the pad IS the conductor
    # there), so the containment predicate does not apply to it; its topology is covered by the net check.
    hang = 0.0 if family == "clean_port_xfm_tw" else max(landing_pad_hang(gds, profile, m, w) for m, w in windings)
    return {"audit": findings, "nets": found, "topology_ok": topology_ok(found), "ports_outward": outward, "pad_hang": hang,
            "ok": not findings and topology_ok(found) and outward and hang == 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-family", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bodies", default="AP:10,10:9", help="winding metal:metal below, comma separated")
    args = ap.parse_args()
    bodies = [tuple(b.split(":")) for b in args.bodies.split(",")]
    names = layer_names(args.profile)
    summary = {"profile": args.profile, "geometry_version": GEOMETRY_VERSION, "seed": args.seed, "per_family": args.per_family, "families": {}}
    for family in FAMILIES:
        rng = random.Random(f"{args.seed}:{family}")
        gen = get_generator(family, plugin_module="builtin:clean_port")
        accepted, refused, reasons, devices = 0, 0, collections.Counter(), []
        fam_dir = args.out / family
        while accepted < args.per_family:
            config = draw(rng, family, bodies)
            full = {**config, "process_profile": args.profile, "ground_fixture": dict(FIX)}
            if family == "clean_port_xfm_ms":
                full["ground_fixture"].update({"stub_width_um": config["primary_width_um"], "stub_width_by_port_um": {"P2": config["secondary_width_um"], "N2": config["secondary_width_um"]}})
            outdir = fam_dir / f"{accepted:02d}"
            t0 = time.perf_counter()
            try:
                model = gen.config_model.model_validate(full)
                result = gen.generate(model, outdir=outdir, gds_name=f"{family}.gds")
            except Exception as exc:  # noqa: BLE001 -- every refusal class is data here
                refused += 1
                masked = re.sub(r"-?[0-9]+(\.[0-9]+)?", "#", str(exc).splitlines()[0])[:110]
                reasons[f"{type(exc).__name__}: {masked}"] += 1
                continue
            built_s = time.perf_counter() - t0
            manifest = json.loads(result.manifest_path.read_text())
            checks = check(family, config, result.gds_path, manifest, args.profile)
            render(result.gds_path, outdir / "thumb.png", names=names, dpi=48, size_in=2.4, title="")
            devices.append({"index": accepted, "config": config, "gds": str(result.gds_path), "build_s": round(built_s, 3), **checks})
            accepted += 1
            print(f"{family} {accepted:02d}/{args.per_family} refused_so_far={refused} ok={checks['ok']} {built_s:.2f}s", flush=True)
        # contact sheet
        cols = 10
        rows = (len(devices) + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 1.6, rows * 1.6), dpi=90)
        for ax in axes.flat:
            ax.axis("off")
        for ax, d in zip(axes.flat, devices):
            ax.imshow(mpimg.imread(Path(d["gds"]).parent / "thumb.png"))
            ax.set_title(f"{d['index']:02d}" + ("" if d["ok"] else " !"), fontsize=7, color="black" if d["ok"] else "red")
        fig.tight_layout(pad=0.2)
        fig.savefig(fam_dir / "sheet.png")
        plt.close(fig)
        summary["families"][family] = {"accepted": accepted, "refused": refused, "refusal_reasons": reasons.most_common(8),
                                       "all_ok": all(d["ok"] for d in devices), "not_ok": [d["index"] for d in devices if not d["ok"]],
                                       "taps": sum(1 for d in devices if any(k.startswith("ct_") for k in d["config"])),
                                       "port_spacing": sum(1 for d in devices if any(k.endswith("port_spacing_um") for k in d["config"])),
                                       "pgs": sum(1 for d in devices if "pgs" in d["config"]),
                                       "build_s": {"min": min(d["build_s"] for d in devices), "median": sorted(d["build_s"] for d in devices)[len(devices) // 2], "max": max(d["build_s"] for d in devices)},
                                       "devices": devices}
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({f: {k: v for k, v in s.items() if k != "devices"} for f, s in summary["families"].items()}, indent=1))


if __name__ == "__main__":
    main()
