"""A synthetic inductor library for the lib.* tests: analytic RLC S-parameters written straight into run stores."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from ic_opt.em import measure, touchstone
from ic_opt.observation import Observation
from tests.ic_opt.test_library import DIMS, part_spec

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
