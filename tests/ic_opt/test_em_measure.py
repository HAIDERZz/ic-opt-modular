"""T9.3: device quantities from S-parameters, the measure stage, em_only / em_circuit with device metrics, V2 parity with the recorded library."""

from __future__ import annotations

import json
import math
import os
import random
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from ic_opt.blocks.evaluate import default_pipeline, evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.em import measure, touchstone
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, synthetic_snp
from tests.ic_opt.test_em_circuit import em_circuit_spec
from tests.ic_opt.test_em_emx import em_only_spec

pytest.importorskip("klayout.db")

L, R, K = 1e-9, 1.0, 0.5           # the fake host's coupled inductor


def test_quantities_of_the_synthetic_coupled_inductor(tmp_path):
    path = tmp_path / "x.s4p"
    path.write_text(synthetic_snp(["emx"], 4, 50.0, freqs=tuple(np.arange(1e9, 21e9, 1e9))))
    ts = touchstone.read(path)
    topo = measure.Topology.from_labels([("P1", "N1"), ("N2", "P2")], [], ["P1", "N1", "P2", "N2"])
    q = measure.quantities(ts.freqs, ts.s, topo, z0=ts.z0)
    # every port shares L and R and couples with k to every other port: a differential drive sees 2L(1-k) and 2R
    assert q.at("Lp", 5e9) == pytest.approx(2 * L * (1 - K), rel=1e-9) and q.at("Ls", 5e9) == pytest.approx(2 * L * (1 - K), rel=1e-9)
    assert q.at("Qp", 5e9) == pytest.approx(2 * math.pi * 5e9 * 2 * L * (1 - K) / (2 * R), rel=1e-9)
    assert q.scalars["Lp_lf"] == pytest.approx(2 * L * (1 - K), rel=1e-9) and q.scalars["SRF_p"] is None and q.scalars["Lp_res"] == q.scalars["Lp_lf"]
    assert q.scalars["Qp_peak"] == pytest.approx(q.at("Qp", 20e9), rel=1e-9)
    assert -1 <= q.scalars["k_lf"] <= 1 and "k" in q.curves
    with pytest.raises(measure.MeasureError, match="outside the swept grid"):
        q.at("Lp", 40e9)
    two = measure.Topology.from_labels([("P1", "N1")], ["CT"], ["P1", "N1", "CT"])
    assert two.drives == [(0, 1)] and two.grounded == [2]
    with pytest.raises(measure.MeasureError, match="topology needs"):
        measure.quantities(ts.freqs, ts.s, two, z0=ts.z0)


def test_measure_reads_the_quantities_the_spec_asks_for(tmp_path):
    d = em_only_spec().model_dump(mode="json")
    d["metrics"] = [{"name": "Lp_5g", "unit": "H", "device": "ind", "quantity": "Lp", "frequency_hz": 5e9},
                    {"name": "Qpk", "unit": "ratio", "device": "ind", "quantity": "Qp_peak"},
                    {"name": "SRF", "unit": "Hz", "device": "ind", "quantity": "SRF_p"}]
    d["constraints"] = [{"metric": "Lp_5g", "op": "gt", "value": "0"}]
    d["objective"] = {"direction": "maximize", "expression": "Qpk"}
    spec = Spec.model_validate(d)
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims")
    pipeline = default_pipeline(spec, None)
    assert [s.name for s in pipeline] == ["pcell", "emx:ind", "measure"]
    obs = evaluate(spec, [Point({"outer_diameter_um": "100", "width_um": "5", "F": "1"}, "user")], ex, store)
    o = obs[0]
    assert o.status == "failed:measure" and o.issues == ["ind/nominal: metric SRF: SRF_p is None"]      # no resonance in a 3-point sweep
    assert o.children["ind/nominal"].metrics["Lp_5g"] == pytest.approx(2 * L * (1 - K), rel=1e-9)

    d["metrics"].pop()                                        # without SRF the point is fine
    spec = Spec.model_validate(d)
    obs = evaluate(spec, [Point({"outer_diameter_um": "100", "width_um": "5", "F": "1"}, "user")], ex, store)
    assert obs[0].status == "ok" and obs[0].fom == pytest.approx(obs[0].metrics["Qpk"]) and obs[0].feasible
    assert json.loads((store.root / "sims" / "obs_0002" / "ind" / "nominal" / "quantities.json").read_text())["Lp_lf"] > 0


def test_em_circuit_with_device_metrics_runs_both_chains(tmp_path):
    spec = em_circuit_spec(tmp_path, corners=("tt", "ss"))
    d = spec.model_dump(mode="json")
    d["metrics"].append({"name": "Qpk", "unit": "ratio", "device": "ind", "quantity": "Qp_peak"})
    d["constraints"].append({"metric": "Qpk", "op": "gt", "value": "1"})
    spec = Spec.model_validate(d)
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0 + (1.0 if c == "ss" else 0.0)})
    deck = import_netlists(spec, ex, store)
    pipeline = default_pipeline(spec, deck)
    assert [s.name for s in pipeline] == ["pcell", "emx:ind", "bind_nport", "spectre", "ocean", "extract", "measure"]
    obs = evaluate(spec, [Point({"ind.od": "100", "ind.w": "5", "F": "20"}, "user")], ex, store, deck=deck)
    o = obs[0]
    assert set(o.children) == {"tb/tt", "tb/ss", "ind/nominal"} and o.status == "ok"
    assert o.metrics["NF"] == 8.0 and o.metrics["Qpk"] > 1 and o.feasible


# -- V2: quantities parity with em-opt's recorded library ------------------------------------

DB = Path(os.environ.get("IC_OPT_EM_DB", "/nonexistent"))      # em-opt experiments/device_db_sweep_n28/outputs/device_db.sqlite
TOPOLOGIES = {"ind_diff": ([("P1", "N1")], []), "xfm_dual_balun": ([("P1", "N1"), ("N2", "P2")], [])}


@pytest.mark.skipif(not DB.exists(), reason="set IC_OPT_EM_DB")
def test_quantities_match_the_recorded_library():
    conn = sqlite3.connect(DB)
    strata = [r[0] for r in conn.execute("select distinct stratum from samples where status='ok'")]
    checked = 0
    for stratum in strata:
        rows = conn.execute("select s.id, s.snp_path, t.name from samples s join strata st on st.name = s.stratum join topologies t on t.name = st.topology_name "
                            "where s.status='ok' and s.stratum=?", (stratum,)).fetchall()
        for sample_id, snp_path, topology in random.Random(20260922).sample(rows, min(6, len(rows))):
            ts = touchstone.read(DB.parent / snp_path)
            columns = ["P1", "N1", "P2", "N2"][: ts.n_ports]
            drives, grounded = TOPOLOGIES[topology]
            q = measure.quantities(ts.freqs, ts.s, measure.Topology.from_labels(drives, grounded, columns), z0=ts.z0)
            for name, value in conn.execute("select name, value from metrics where sample_id=? and freq_hz is null", (sample_id,)):
                if len(drives) == 2 and name.endswith("_peak"):
                    continue          # em-opt took the full-sweep maximum; a coupled pair's Q peak is now searched below the system SRF (T13.7)
                ours = q.scalars[name]
                assert (ours is None) == (value is None), f"{stratum}/{sample_id} {name}: {value} vs {ours}"
                if value is not None:
                    assert ours == pytest.approx(value, rel=1e-9, abs=1e-30), f"{stratum}/{sample_id} {name}"
            for name, freq, value in conn.execute("select name, freq_hz, value from metrics where sample_id=? and freq_hz is not null", (sample_id,)):
                ours = float(q.curves[name][int(np.argmin(np.abs(q.freqs - freq)))])
                if value is None:
                    assert not math.isfinite(ours), f"{stratum}/{sample_id} {name}@{freq:g}"
                else:
                    assert ours == pytest.approx(value, rel=1e-9, abs=1e-30), f"{stratum}/{sample_id} {name}@{freq:g}"
            checked += 1
    assert checked >= len(strata)


def test_a_q_peak_is_searched_below_the_system_resonance():
    """A differential element whose reactance crosses zero at 25 GHz and climbs again past 75 GHz: the full-sweep Q
    maximum would sit at the band edge, the device's Q peak lies below its resonance."""
    freqs = np.arange(0.0, 151e9, 1e9)
    f0, z0 = 25e9, 50.0
    reactance = 2 * np.pi * freqs * 0.3e-9 * np.cos(np.pi * freqs / (2 * f0))         # + below f0, - between f0 and 3 f0, + again above
    z_diff = 1.0 + 1j * reactance
    s = np.empty((len(freqs), 2, 2), dtype=complex)
    for i, zd in enumerate(z_diff):
        z = zd / 2 * np.array([[1, -1], [-1, 1]]) + 1e3 * np.array([[1, 1], [1, 1]])   # differential zd, a stiff common mode
        s[i] = (z - z0 * np.eye(2)) @ np.linalg.inv(z + z0 * np.eye(2))
    q = measure.quantities(freqs, s, measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"]), z0=z0)
    assert q.scalars["SRF_p"] == pytest.approx(f0, abs=1e9) and q.scalars["SRF"] == q.scalars["SRF_p"]
    below = (freqs > 0) & (freqs < q.scalars["SRF_p"])
    assert q.scalars["Qp_peak"] == pytest.approx(np.nanmax(q.curves["Qp"][below]))
    assert np.nanmax(q.curves["Qp"]) > 3 * q.scalars["Qp_peak"]                          # the band-edge climb is ignored

