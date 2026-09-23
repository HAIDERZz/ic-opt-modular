"""A synthetic inductor library for the lib.* tests: analytic RLC S-parameters written straight into run stores."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from ic_opt.em import measure, touchstone
from ic_opt.observation import Observation
from ic_opt.spec import Spec
from tests.ic_opt.fakes import minimal_spec
from tests.ic_opt.test_library import DIMS, FIXTURE, part_spec

STOP_GHZ = 60


def rlc_touchstone(od: float, w: float, s: float, nt: int, stop_ghz: float = STOP_GHZ) -> str:
    """R + jwL between the ports, C/2 to ground at each: L grows with turns and diameter, C with area (resonances in and above the sweep)."""
    ind = 0.4e-9 * nt**2 * (od / 100) ** 1.3 * (5 / w) ** 0.15 * (1 - 0.04 * (s - 2))
    res = 0.3 + 0.02 * nt * od / w
    cap = 12e-15 * nt * (od / 100) ** 2 * (w / 5) ** 0.5
    lines = ["! Touchstone from a synthetic RLC library", "# Hz S RI R 50"]
    eye = np.eye(2)
    for f in np.arange(0.0, stop_ghz * 1e9 + 0.5e9, 1e9):
        om = 2 * np.pi * f
        ys, yc = 1 / (res + 1j * om * ind), 1j * om * cap / 2
        y = np.array([[ys + yc, -ys], [-ys, ys + yc]])
        sp = (eye - 50 * y) @ np.linalg.inv(eye + 50 * y)
        lines.append(f"{f:.0f} " + " ".join(f"{v.real:.12e} {v.imag:.12e}" for v in sp.T.reshape(-1)))
    return "\n".join(lines) + "\n"


def write_store(root: Path, name: str, points: list[tuple[float, float, float, int]]) -> None:
    project = root / name
    (project / ".icopt").mkdir(parents=True)
    (project / "spec.yaml").write_text(yaml.safe_dump(part_spec(name, STOP_GHZ).model_dump(mode="json")), encoding="utf-8")
    lines = []
    for i, (od, w, s, nt) in enumerate(points, 1):
        obs = f"obs_{i:04d}"
        em = project / ".icopt" / "sims" / obs / "em" / "ind"
        em.mkdir(parents=True)
        (em / "ind.s2p").write_text(rlc_touchstone(od, w, s, nt), encoding="utf-8")
        params = {"outer_diameter_um": f"{od:g}", "width_um": f"{w:g}", "spacing_um": f"{s:g}", "turns": str(nt)}
        o = Observation(obs_id=obs, params=params, origin="grid", status="ok", spec_fingerprint="spec", pipeline_fingerprint="gen1",
                        started_at="", finished_at="")
        lines.append(o.model_dump_json())
    (project / ".icopt" / "observations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_library(root: Path) -> Path:
    """nt1: 39 single-turn rows (spacing fixed at 2), nt2: 66 two-turn rows; stratum ind_demo with Lp_lf, Qp_peak, SRF_p, Lp@10."""
    write_store(root, "nt1", [(od, w, 2.0, 1) for od in range(80, 201, 10) for w in (4.0, 5.0, 6.0)])
    write_store(root, "nt2", [(od, w, s, 2) for od in range(100, 201, 10) for w in (4.0, 5.0, 6.0) for s in (2.0, 3.0)])
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {"ind_demo": {"generator": "clean_port_ind_sym", "dims": DIMS, "nt_dim": "turns",
                                   "parts": [{"store": "nt1"}, {"store": "nt2"}],
                                   "quantities": {"Lp_lf": {}, "Qp_peak": {"band_ghz": STOP_GHZ}, "SRF_p": {}, "Lp": {"anchors_ghz": [10]}}}}}
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def truth(od, w, s, nt) -> dict:
    """The measure kernel's scalars for the synthetic geometry itself (what the model should predict)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.s2p"
        path.write_text(rlc_touchstone(od, w, s, nt))
        ts = touchstone.read(path)
    return measure.quantities(ts.freqs, ts.s, measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"]), z0=ts.z0).scalars


def params(od, w, s, nt) -> dict:
    return dict(zip(DIMS, (od, w, s, nt)))


# -- a synthetic broadside-transformer library (xfm_bs dims, 4-port coupled inductors) ---------------------------

XFM_DIMS = ["primary_outer_diameter_um", "secondary_outer_diameter_um", "primary_width_um", "secondary_width_um", "center_spacing_um"]
XFM_STOP_GHZ = 60


def xfm_physics(op: float, os_: float, wp: float, ws: float, cs: float) -> dict:
    """Lp / Ls grow with diameter; k depends only on dimensionless ratios (as broadside coupling nearly does)."""
    offset = 4 * cs / (op + os_)
    k = 0.75 * np.exp(-1.5 * np.log(op / os_) ** 2) * (1 - 1.2 * offset**2) * (1 - 0.3 * (wp / op + ws / os_))
    return {"Lp": 0.35e-9 * (op / 100) ** 1.25 * (5 / wp) ** 0.12, "Ls": 0.35e-9 * (os_ / 100) ** 1.25 * (5 / ws) ** 0.12, "k": k,
            "Rp": 0.4 + 0.02 * op / wp, "Rs": 0.4 + 0.02 * os_ / ws, "Cp": 8e-15 * (op / 100) ** 2, "Cs": 8e-15 * (os_ / 100) ** 2}


def xfm_touchstone(op: float, os_: float, wp: float, ws: float, cs: float, stop_ghz: float = XFM_STOP_GHZ) -> str:
    """Ports P1 N1 P2 N2: the primary branch P1 -> N1, the secondary N2 -> P2 (drives (P1, N1), (N2, P2) couple positively), C/2 per port."""
    ph = xfm_physics(op, os_, wp, ws, cs)
    m = ph["k"] * np.sqrt(ph["Lp"] * ph["Ls"])
    inc = np.array([[1, 0], [-1, 0], [0, -1], [0, 1]], dtype=float)
    lines = ["! Touchstone from a synthetic coupled-inductor library", "# Hz S RI R 50"]
    eye = np.eye(4)
    for f in np.arange(0.0, stop_ghz * 1e9 + 0.5e9, 1e9):
        om = 2 * np.pi * f
        zb = np.array([[ph["Rp"] + 1j * om * ph["Lp"], 1j * om * m], [1j * om * m, ph["Rs"] + 1j * om * ph["Ls"]]])
        y = inc @ np.linalg.inv(zb) @ inc.T + np.diag(1j * om * np.array([ph["Cp"], ph["Cp"], ph["Cs"], ph["Cs"]]) / 2)
        sp = (eye - 50 * y) @ np.linalg.inv(eye + 50 * y)
        lines.append(f"{f:.0f} " + " ".join(f"{v.real:.12e} {v.imag:.12e}" for v in sp.reshape(-1)))
    return "\n".join(lines) + "\n"


def xfm_part_spec(project: str, stop_ghz: float = XFM_STOP_GHZ) -> Spec:
    d = minimal_spec()
    d["project"], d["testbenches"] = project, []
    d["devices"] = [{"id": "xfm", "generator": "clean_port_xfm_bs", "profile": "demo_6m", "ports": ["P1", "N1", "P2", "N2"],
                     "fixed": {"primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
                               "secondary_lead_length_um": 20.0, "primary_metal": "6", "secondary_metal": "5", "ground_fixture": FIXTURE},
                     "variables": {k: k for k in XFM_DIMS}, "topology": {"drives": [["P1", "N1"], ["N2", "P2"]], "grounded": []}}]
    d["variables"] = [{"name": name, "kind": "continuous_step", "lower": lo, "upper": hi, "step": "0.01"}
                      for name, lo, hi in (("primary_outer_diameter_um", "40", "260"), ("secondary_outer_diameter_um", "40", "260"),
                                           ("primary_width_um", "4", "10"), ("secondary_width_um", "4", "10"), ("center_spacing_um", "0", "130"))]
    d["em"] = {"process_file": "/site/demo.proc", "mode": "full_wave", "frequencies": {"start_hz": 0, "stop_hz": stop_ghz * 1e9, "step_hz": 1e9},
               "three_d_metals": ["M6", "M5"], "via_separation_um": 0.5, "threads": 1, "memory_gb": 4, "simultaneous_frequencies": 0}
    d["metrics"] = [{"name": "k", "unit": "1", "device": "xfm", "quantity": "k_lf"}]
    d["objective"] = {"direction": "maximize", "expression": "k"}
    d["constraints"] = []
    return Spec.model_validate(d)


def xfm_points() -> list[tuple[float, float, float, float, float]]:
    """Diameters 80-200 with secondary/primary 0.6-1.25x, two widths per winding, concentric and offset by a quarter of the mean diameter."""
    out = []
    for op in range(80, 201, 20):
        for ratio in (0.6, 0.8, 1.0, 1.25):
            os_ = round(op * ratio)
            for wp in (4.0, 7.0):
                for ws in (4.0, 7.0):
                    for offset in (0.0, 0.25):
                        out.append((float(op), float(os_), wp, ws, round(offset * (op + os_) / 4, 2)))
    return out


def build_xfm_library(root: Path, *, feature_map: str | None = "xfm_bs_dimensionless") -> Path:
    """Stratum xfm_demo (one part, 224 rows): Lp_lf, Ls_lf, k_lf, Qp_peak, Qs_peak, SRF_p, SRF_s and k@10 (k with ``feature_map``)."""
    project = root / "xfm"
    (project / ".icopt").mkdir(parents=True)
    (project / "spec.yaml").write_text(yaml.safe_dump(xfm_part_spec("xfm").model_dump(mode="json")), encoding="utf-8")
    lines = []
    for i, geometry in enumerate(xfm_points(), 1):
        obs = f"obs_{i:04d}"
        em = project / ".icopt" / "sims" / obs / "em" / "xfm"
        em.mkdir(parents=True)
        (em / "xfm.s4p").write_text(xfm_touchstone(*geometry), encoding="utf-8")
        o = Observation(obs_id=obs, params={d: f"{v:g}" for d, v in zip(XFM_DIMS, geometry)}, origin="grid", status="ok",
                        spec_fingerprint="spec", pipeline_fingerprint="gen1", started_at="", finished_at="")
        lines.append(o.model_dump_json())
    (project / ".icopt" / "observations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    k_rule = {"feature_map": feature_map} if feature_map else {}
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {"xfm_demo": {"generator": "clean_port_xfm_bs", "dims": XFM_DIMS, "parts": [{"store": "xfm"}],
                                   "steps": {"primary_outer_diameter_um": 1, "secondary_outer_diameter_um": 1, "primary_width_um": 0.1,
                                             "secondary_width_um": 0.1, "center_spacing_um": 0.5},
                                   "quantities": {"Lp_lf": {}, "Ls_lf": {}, "k_lf": k_rule, "Qp_peak": {"band_ghz": XFM_STOP_GHZ},
                                                  "Qs_peak": {"band_ghz": XFM_STOP_GHZ}, "SRF_p": {}, "SRF_s": {},
                                                  "k": {"anchors_ghz": [10], **k_rule}}}}}
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def xfm_truth(geometry) -> dict:
    """The measure kernel's scalars for one synthetic transformer."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.s4p"
        path.write_text(xfm_touchstone(*geometry))
        ts = touchstone.read(path)
    topo = measure.Topology.from_labels([("P1", "N1"), ("N2", "P2")], [], ["P1", "N1", "P2", "N2"])
    return measure.quantities(ts.freqs, ts.s, topo, z0=ts.z0).scalars
