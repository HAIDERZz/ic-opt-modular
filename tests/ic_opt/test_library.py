"""T13.1: the library manifest and the stratum dataset, on a small demo_6m library built through the real em_only chain."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from ic_opt.blocks.evaluate import evaluate
from ic_opt.em import measure, touchstone
from ic_opt.library import dataset, manifest
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, minimal_spec, rlc_snp

pytest.importorskip("klayout.db")

FIXTURE = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
DIMS = ["outer_diameter_um", "width_um", "spacing_um", "turns"]
QUANTITIES = {"Lp_lf": {}, "Lp_res": {}, "Qp_peak": {"band_ghz": 30}, "SRF_p": {}, "Lp": {"anchors_ghz": [5, 20]}, "Qp": {"anchors_ghz": [5, 20]}}


def part_spec(project: str, stop_ghz: float, **em) -> Spec:
    d = minimal_spec()
    d["project"], d["testbenches"] = project, []
    d["devices"] = [{"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"],
                     "fixed": {"opening_um": 8.0, "lead_length_um": 20.0, "metal": "6", "ground_fixture": FIXTURE},
                     "variables": {k: k for k in DIMS}}]
    d["variables"] = [{"name": "outer_diameter_um", "kind": "continuous_step", "lower": "80", "upper": "160", "step": "1"},
                      {"name": "width_um", "kind": "continuous_step", "lower": "3", "upper": "8", "step": "0.1"},
                      {"name": "spacing_um", "kind": "continuous_step", "lower": "2", "upper": "4", "step": "0.1"},
                      {"name": "turns", "kind": "integer", "lower": "1", "upper": "3", "step": "1"}]
    d["em"] = {"process_file": "/site/demo.proc", "mode": "full_wave", "frequencies": {"start_hz": 0, "stop_hz": stop_ghz * 1e9, "step_hz": 1e9},
               "three_d_metals": ["M6", "M5"], "via_separation_um": 0.5, "threads": 1, "memory_gb": 4, "simultaneous_frequencies": 0, **em}
    d["metrics"] = [{"name": "L", "unit": "H", "device": "ind", "quantity": "Lp_lf"}]
    d["objective"] = {"direction": "maximize", "expression": "L"}
    d["constraints"] = []
    return Spec.model_validate(d)


def run_part(root: Path, name: str, stop_ghz: float, points: list[dict], *, fail_emx=None, **em) -> RunStore:
    spec = part_spec(name, stop_ghz, **em)
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "spec.yaml").write_text(yaml.safe_dump(spec.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(root / name)
    ex = FakeSpectreExecutor(store.root / "sims", snp_fn=rlc_snp, fail_emx=fail_emx)
    evaluate(spec, [Point({k: str(v) for k, v in p.items()}, "grid") for p in points], ex, store)
    return store


def write_manifest(root: Path, parts=("ind_nt2", "ind_nt1"), quantities=None, **stratum) -> None:
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {"ind_demo": {"generator": "clean_port_ind_sym", "dims": DIMS, "nt_dim": "turns",
                                   "parts": [{"store": p} if isinstance(p, str) else p for p in parts],
                                   "quantities": quantities or QUANTITIES, **stratum}}}
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


NT2 = [{"outer_diameter_um": od, "width_um": w, "spacing_um": 2, "turns": 2} for od in (100, 120) for w in (4, 5)]
NT1 = [{"outer_diameter_um": od, "width_um": w, "spacing_um": 2, "turns": 1} for od in (100, 120) for w in (4, 5)]


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("lib")
    run_part(root, "ind_nt2", 30, NT2)
    run_part(root, "ind_nt1", 40, NT1)
    write_manifest(root)
    return root


def test_manifest_names_the_measure_quantities_and_fails_closed(tmp_path):
    good = manifest.Stratum.model_validate({"generator": "g", "dims": DIMS, "nt_dim": "turns", "parts": [{"store": "a"}], "quantities": QUANTITIES})
    assert good.columns() == ["Lp_lf", "Lp_res", "Qp_peak", "SRF_p", "Lp@5", "Lp@20", "Qp@5", "Qp@20"]
    bad = [({"Lp": {}}, "needs anchors_ghz"), ({"Lp_lf": {"anchors_ghz": [5]}}, "is a scalar"), ({"SRF_p": {"band_ghz": 10}}, "band_ghz applies"),
           ({"L_lf": {}}, "unknown quantity"), ({"Qp": {"anchors_ghz": [5, 5]}}, "distinct")]
    for quantities, message in bad:
        with pytest.raises(ValueError, match=message):
            manifest.Stratum.model_validate({"generator": "g", "dims": DIMS, "parts": [{"store": "a"}], "quantities": quantities})
    with pytest.raises(ValueError, match="nt_dim"):
        manifest.Stratum.model_validate({"generator": "g", "dims": ["a"], "nt_dim": "turns", "parts": [{"store": "a"}], "quantities": {"Lp_lf": {}}})
    with pytest.raises(ValueError, match="twice"):
        manifest.Stratum.model_validate({"generator": "g", "dims": ["a"], "parts": [{"store": "a"}, {"store": "a"}], "quantities": {"Lp_lf": {}}})
    with pytest.raises(FileNotFoundError, match="library.yaml"):
        manifest.load(tmp_path)
    assert not manifest.is_library(tmp_path)


def test_dataset_measures_every_part_under_one_definition(library):
    ds = dataset.build(library, "ind_demo", cache=False)
    assert len(ds.rows) == 8 and ds.excluded == {} and set(ds.generations) == {"ind_nt2", "ind_nt1"} and ds.cache == "off"
    assert {r.part for r in ds.rows} == {"ind_nt2", "ind_nt1"}
    for r in ds.rows:                                       # every value is the measure kernel on the stored sNp, band and margin applied
        ts = touchstone.read(library / r.snp)
        q = measure.quantities(ts.freqs, ts.s, measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"]), z0=ts.z0)
        assert r.values["Lp_lf"] == q.scalars["Lp_lf"] and r.values["SRF_p"] == q.scalars["SRF_p"]
        band = (q.freqs > 0) & (q.freqs <= 30e9)
        assert r.values["Qp_peak"] == pytest.approx(np.nanmax(q.curves["Qp"][band]), rel=1e-12)
        for f in (5, 20):
            srf = q.scalars["SRF_p"]
            expected = None if srf is not None and srf <= 1.25 * f * 1e9 else q.at("Lp", f * 1e9)
            assert r.values[f"Lp@{f}"] == expected
    srf_rows = [r for r in ds.rows if r.values["SRF_p"] is not None]
    assert srf_rows and any(r.values["Lp@20"] is None for r in srf_rows)          # the synthetic set resonates inside the sweep for some points
    assert ds.find({"outer_diameter_um": 120, "width_um": 5, "spacing_um": 2, "turns": 2}).part == "ind_nt2"
    assert ds.find({"outer_diameter_um": 121, "width_um": 5, "spacing_um": 2, "turns": 2}) is None
    assert ds.matrix().shape == (8, 4) and len(ds.usable("Lp@20")) == sum(r.values["Lp@20"] is not None for r in ds.rows)


def test_check_reports_passivity_reproduction_grids_and_duplicates(library):
    c = dataset.check(dataset.build(library, "ind_demo", cache=False))
    assert c["rows"] == 8 and c["duplicate_coordinates"] == 0 and c["passive_rows"] == 8 and c["stored_reproduced"] == 8
    assert c["stored_mismatch"] == [] and c["ports"] == {2: 8}
    assert {(g["part"], g["n_freq"], g["stop_ghz"], g["rows"]) for g in c["grids"]} == {("ind_nt2", 31, 30.0, 4), ("ind_nt1", 41, 40.0, 4)}
    assert c["values"]["Lp_lf"] == 8


def test_dataset_cache_hits_until_the_observations_change(tmp_path):
    run_part(tmp_path, "ind_nt2", 30, NT2[:2])
    write_manifest(tmp_path, parts=("ind_nt2",))
    first = dataset.build(tmp_path, "ind_demo")
    second = dataset.build(tmp_path, "ind_demo")
    assert (first.cache, second.cache) == ("miss", "hit") and [r.values for r in first.rows] == [r.values for r in second.rows]
    run_part(tmp_path, "ind_nt2", 30, NT2[2:3])                                  # a new observation invalidates the key
    third = dataset.build(tmp_path, "ind_demo")
    assert third.cache == "miss" and len(third.rows) == 3


def test_generations_never_mix_and_failures_are_counted(tmp_path):
    run_part(tmp_path, "ind_nt2", 30, NT2[:3])
    minority = run_part(tmp_path, "ind_nt2", 30, NT2[3:], accuracy={"thickness_um": 0.3})          # other EMX physics: another generation
    run_part(tmp_path, "ind_nt2", 30, [{"outer_diameter_um": 110, "width_um": 4, "spacing_um": 2, "turns": 2}], fail_emx=lambda device: True)
    write_manifest(tmp_path, parts=("ind_nt2",))
    ds = dataset.build(tmp_path, "ind_demo", cache=False)
    assert len(ds.rows) == 3 and ds.excluded == {"other generation": 1, "status:failed:emx:ind": 1}
    pinned = minority.observations()[3].pipeline_fingerprint
    write_manifest(tmp_path, parts=({"store": "ind_nt2", "pipeline_fingerprint": pinned},))
    ds = dataset.build(tmp_path, "ind_demo", cache=False)
    assert [r.obs_id for r in ds.rows] == ["obs_0004"] and ds.generations == {"ind_nt2": pinned}


def test_declaration_errors_fail_loudly(tmp_path):
    run_part(tmp_path, "ind_nt2", 30, NT2[:1])
    write_manifest(tmp_path, parts=("ind_nt2", "missing"))
    with pytest.raises(dataset.DatasetError, match="part missing has no observations"):
        dataset.build(tmp_path, "ind_demo", cache=False)
    write_manifest(tmp_path, parts=("ind_nt2",), quantities={"Qp_peak": {"band_ghz": 50}})
    with pytest.raises(dataset.DatasetError, match="band is 50 GHz but the sweep stops at 30 GHz"):
        dataset.build(tmp_path, "ind_demo", cache=False)
    write_manifest(tmp_path, parts=("ind_nt2",), quantities={"Ls_lf": {}})
    with pytest.raises(dataset.DatasetError, match="needs two drives"):
        dataset.build(tmp_path, "ind_demo", cache=False)
    with pytest.raises(dataset.DatasetError, match="no stratum 'nope'"):
        dataset.build(tmp_path, "nope", cache=False)
