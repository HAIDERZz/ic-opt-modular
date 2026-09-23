"""Figure: SRF_p of a broadside transformer jumps between its own resonance and the secondary's reflected one.

Usage: xfm_srf_jump_fig.py STORE OBS_A OBS_B OUT_PNG   (STORE = a transformer part, e.g. <library>/xfm_bs_ap)
Top: the two GDS (real renders). Bottom: Im(Z) of the primary and secondary drives (other drive open) over the sweep,
with the kernel's SRF_p / SRF_s marked; the inset zooms on the reflected dip near the secondary's resonance.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ic_opt.em import measure, touchstone
from ic_opt.em.pcell.render import layer_names, render

store, obs_a, obs_b, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
topo = measure.Topology.from_labels([("P1", "N1"), ("N2", "P2")], [], ["P1", "N1", "P2", "N2"])
params = {json.loads(line)["obs_id"]: json.loads(line)["params"] for line in (store / ".icopt" / "observations.jsonl").read_text().splitlines()}
profile = json.loads((store / ".icopt" / "spec.json").read_text())["devices"][0]["profile"]
names = layer_names(profile)

fig = plt.figure(figsize=(12, 9.5), dpi=110)
grid = fig.add_gridspec(2, 2, height_ratios=[1, 1.05])
colours = {obs_a: "#1f6f8b", obs_b: "#c0392b"}
curves = {}
with tempfile.TemporaryDirectory() as tmp:
    for k, obs in enumerate((obs_a, obs_b)):
        work = store / ".icopt" / "sims" / obs / "em" / "xfm"
        png = render(work / "xfm.gds", Path(tmp) / f"{obs}.png", names=names, size_in=5.5, title="")
        ax = fig.add_subplot(grid[0, k])
        ax.imshow(plt.imread(png))
        ax.set_axis_off()
        p = params[obs]
        ax.set_title(f"{obs}: OD {p['primary_outer_diameter_um']}/{p['secondary_outer_diameter_um']} um, "
                     f"W {p['primary_width_um']}/{p['secondary_width_um']} um, offset {p['center_spacing_um']} um",
                     fontsize=9, color=colours[obs])
        ts = touchstone.read(work / "xfm.s4p")
        q = measure.quantities(ts.freqs, ts.s, topo, z0=ts.z0)
        zm = measure._mixed_mode_z(measure.s_to_z(ts.s, z0=ts.z0), topo)
        curves[obs] = (ts.freqs / 1e9, np.imag(zm[:, 0, 0]), np.imag(zm[:, 1, 1]), q.scalars["SRF_p"], q.scalars["SRF_s"])

ax = fig.add_subplot(grid[1, :])
inset = ax.inset_axes([0.07, 0.52, 0.34, 0.44])
for obs, (f, zp, zs, srf_p, srf_s) in curves.items():
    c = colours[obs]
    for target in (ax, inset):
        target.plot(f, zp, color=c, linewidth=1.6, label=f"{obs} Im(Z) primary" if target is ax else None)
        target.plot(f, zs, color=c, linewidth=1.0, linestyle="--", label=f"{obs} Im(Z) secondary" if target is ax else None)
        if srf_p:
            target.plot(srf_p / 1e9, 0, "o", color=c, markersize=8, markerfacecolor="white", markeredgewidth=2)
        if srf_s:
            target.plot(srf_s / 1e9, 0, "x", color=c, markersize=8, markeredgewidth=2)
    ax.annotate(f"SRF_p {srf_p / 1e9:.1f} GHz, SRF_s {srf_s / 1e9:.1f} GHz", (srf_p / 1e9, 0), xytext=(8, 22 if obs == obs_a else -34),
                textcoords="offset points", color=c, fontsize=9, arrowprops={"arrowstyle": "->", "color": c})
for target in (ax, inset):
    target.axhline(0, color="black", linewidth=0.8)
ax.set_xlim(0, curves[obs_a][0].max())
ax.set_ylim(-600, 1500)
ax.set_xlabel("frequency (GHz)")
ax.set_ylabel("Im(Z) of the drive, other drive open (ohm)")
ax.legend(loc="upper right", fontsize=8)
ax.set_title("SRF_p = first zero of the primary's Im(Z): the secondary's resonance reflects a dip that crosses zero in one case and not the other",
             fontsize=10)
inset.set_xlim(94, 110)
inset.set_ylim(-150, 500)
inset.set_title("zoom 94-110 GHz", fontsize=8)
inset.tick_params(labelsize=7)
fig.tight_layout()
out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out)
print(out)
