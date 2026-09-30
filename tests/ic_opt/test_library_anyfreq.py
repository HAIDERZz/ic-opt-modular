"""T18.1 section 1: a declared curve answers at any frequency. An extension column ``<curve>@<f>`` is measured again from
each row's sNp under a declared anchor's rule, cached beside the dataset, and used like a declared column by lib.query,
lib.suggest, lib.region, lib.densify, Library.model(s) and the Predict stage; the dataset, its key and its cache files do
not change."""
from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ic_opt import _lock
from ic_opt.blocks import library as library_blocks
from ic_opt.blocks.evaluate import evaluate
from ic_opt.cli import app
from ic_opt.em import measure, touchstone
from ic_opt.library import dataset, densify, region, stage, suggest
from ic_opt.library import query as q
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
from tests.ic_opt.library_fixtures import (
    LOCAL,
    build_library,
    build_xfm_library,
    params,
    rlc_touchstone,
    write_store,
)
from tests.ic_opt.test_library import DIMS

IND_KEY = "0cdd7b6b8152e4128b2e"                     # build_library's dataset key at main 088a182, before T18.1
XFM_KEY = "ebad2777b2387f8641d1"                     # build_xfm_library's, likewise
DECLARED = ["Lp@10", "Lp_lf", "Qp_peak", "SRF_p"]    # build_library's columns
HOLD_S = 1.5                                         # the first sNp read of the lock test takes this much longer


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
    return build_library(tmp_path_factory.mktemp("anyfreq"))


def fresh_copy(root: Path, dest: Path, *, keep_dataset: bool = False) -> Path:
    """The library without its cache files (keeping the dataset's when asked)."""
    copied = shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".cache"))
    if keep_dataset:
        (copied / ".cache").mkdir()
        for p in (root / ".cache").glob("dataset-*.json"):
            shutil.copy2(p, copied / ".cache" / p.name)
    return copied


def with_anchors(root: Path, dest: Path, stratum: str, curve: str, anchors: list[float]) -> Path:
    """A copy of the library whose manifest declares ``curve`` at ``anchors``."""
    copied = fresh_copy(root, dest)
    doc = yaml.safe_load((copied / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"][stratum]["quantities"][curve]["anchors_ghz"] = anchors
    (copied / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return copied


def listing(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def curve_at(od: float, w: float, s: float, nt: int, curve: str, f_hz: float) -> float:
    """The measure kernel's curve at ``f_hz`` for the synthetic inductor itself (what an extension model should predict)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.s2p"
        path.write_text(rlc_touchstone(od, w, s, nt), encoding="utf-8")
        ts = touchstone.read(path)
    return measure.quantities(ts.freqs, ts.s, measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"]), z0=ts.z0).at(curve, f_hz)


def test_an_extension_column_equals_what_a_manifest_declaring_that_anchor_gives(tmp_path):
    """Row by row, None where the declared anchor is None (a resonance within the margin), the same float elsewhere; for
    an inductor stratum and for a coupled pair, whose curves stop below the system SRF."""
    root = build_library(tmp_path / "ind")
    lib = q.Library(root, limits=LOCAL)
    assert lib.columns("ind_demo", ["Lp@33", "Lp@27.5", "Lp@10.0"]) == ["Lp@33", "Lp@27.5", "Lp@10"]    # Lp@10.0 is the declared Lp@10
    ds = lib.dataset("ind_demo")
    assert ds.extensions == {"Lp@33": 33.0, "Lp@27.5": 27.5} and ds.columns == DECLARED
    declared = dataset.build(with_anchors(root, tmp_path / "ind_twin", "ind_demo", "Lp", [10, 27.5, 33]), "ind_demo", cache=False)
    assert [(r.part, r.obs_id) for r in ds.rows] == [(r.part, r.obs_id) for r in declared.rows]
    for column in ("Lp@33", "Lp@27.5"):
        assert [r.values[column] for r in ds.rows] == [r.values[column] for r in declared.rows]
    assert 0 < len(ds.usable("Lp@33")) < len(ds.rows)                  # the margin leaves out the rows resonating below 41.25 GHz

    xroot = build_xfm_library(tmp_path / "xfm")
    xlib = q.Library(xroot, limits=LOCAL)
    assert xlib.column("xfm_demo", "k@25") == "k@25"
    xdeclared = dataset.build(with_anchors(xroot, tmp_path / "xfm_twin", "xfm_demo", "k", [10, 25]), "xfm_demo", cache=False)
    rows = xlib.dataset("xfm_demo").rows
    assert [r.values["k@25"] for r in rows] == [r.values["k@25"] for r in xdeclared.rows] and any(r.values["k@25"] for r in rows)


def test_the_dataset_key_its_cache_files_and_the_declared_columns_stay_what_they_were(tmp_path):
    """The key is main's; a library that never asks for an extension column writes exactly main's files; asking for one adds
    its own files and leaves every earlier one byte for byte, and every declared value as it was."""
    root = build_library(tmp_path / "lib")
    assert dataset.build(root, "ind_demo").key == IND_KEY
    assert dataset.build(build_xfm_library(tmp_path / "xfm"), "xfm_demo").key == XFM_KEY
    lib = q.Library(root, limits=LOCAL)
    lib.model("ind_demo", "Lp_lf")
    q.query(lib, "ind_demo", params(155, 4.5, 2.5, 2), ["Lp_lf"])
    library_blocks.query(lib, "ind_demo", params(150, 5, 3, 2))
    library_blocks.coverage(lib, "ind_demo")
    before = listing(root / ".cache")
    names = sorted(before)
    assert names[:3] == [f"calibration-ind_demo-Lp_lf-{IND_KEY}.json", f"calibration-ind_demo-Lp_lf-{IND_KEY}.json.lock",
                         f"dataset-ind_demo-{IND_KEY}.json"]
    assert len(names) == 4 and names[3].startswith("model-ind_demo-Lp_lf-")
    values = {(r.part, r.obs_id): dict(r.values) for r in lib.dataset("ind_demo").rows}

    q.query(lib, "ind_demo", params(150, 5, 3, 2), ["Lp@15"])
    lib.model("ind_demo", "Lp@15")
    after = listing(root / ".cache")
    assert {name: data for name, data in after.items() if name in before} == before
    assert sorted(set(after) - set(before))[:4] == [f"anchors-ind_demo-{IND_KEY}-15.json", f"anchors-ind_demo-{IND_KEY}-15.json.lock",
                                                     f"calibration-ind_demo-Lp_at_15-{IND_KEY}.json",
                                                     f"calibration-ind_demo-Lp_at_15-{IND_KEY}.json.lock"]
    assert [n.rsplit("-", 1)[0] for n in sorted(set(after) - set(before))[4:]] == ["model-ind_demo-Lp_at_15"]
    ds = lib.dataset("ind_demo")
    assert ds.columns == DECLARED and all({k: v for k, v in r.values.items() if k != "Lp@15"} == values[(r.part, r.obs_id)] for r in ds.rows)
    assert dataset.build(root, "ind_demo", cache=False).key == IND_KEY
    cover = q.coverage(lib, "ind_demo")
    assert list(cover["quantities"]) == DECLARED and list(cover["cached"]) == DECLARED


def test_lib_query_answers_an_extension_column_measured_at_a_row_and_predicted_elsewhere(library):
    lib = q.Library(library, limits=LOCAL)
    lib.column("ind_demo", "Lp@15")
    row = next(r for r in lib.dataset("ind_demo").usable("Lp@15") if r.coords["turns"] == 2)
    geometry = [row.coords[d] for d in DIMS]
    answer = library_blocks.query(lib, "ind_demo", dict(zip(DIMS, geometry)), "Lp@15.0,Lp_lf")
    assert answer["measured"] == {"part": row.part, "obs_id": row.obs_id} and list(answer["quantities"]) == ["Lp@15", "Lp_lf"]
    assert answer["quantities"]["Lp@15"] == {"status": "measured", "value": row.values["Lp@15"], "unit": "H"}
    assert row.values["Lp@15"] == curve_at(*geometry[:3], int(geometry[3]), "Lp", 15e9)          # the row's own sNp, measured again
    between = q.query(lib, "ind_demo", params(115, 4.5, 2.5, 2), ["Lp@15"])["quantities"]["Lp@15"]
    assert between["status"] == "predicted" and between["lo"] < between["value"] < between["hi"] and len(between["nearest"]) == 3
    assert between["value"] == pytest.approx(curve_at(115, 4.5, 2.5, 2, "Lp", 15e9), rel=2e-2)
    assert lib.model("ind_demo", "Lp@15.0") is lib.model("ind_demo", "Lp@15")


def test_lib_suggest_takes_an_extension_target_and_implies_the_resonance_it_needs(library, tmp_path, monkeypatch):
    lib = q.Library(library, limits=LOCAL)
    lib.column("ind_demo", "Lp@15")
    row = next(r for r in lib.dataset("ind_demo").usable("Lp@15") if r.coords["turns"] == 2)
    target = row.values["Lp@15"]
    r = suggest.suggest(lib, "ind_demo", {"Lp@15.0": {"target": target, "tol": 0.05}}, "max:Qp_peak", n=2, pool_size=512, seed=1,
                        verify_build=False)
    assert [(t["quantity"], t["kind"]) for t in r["targets"]] == [("Lp@15", "target"), ("SRF_p", "min")]
    assert r["targets"][1]["value"] == pytest.approx(1.25 * 15e9) and r["notes"] == [
        "added SRF_p >= 18.75 GHz: anchored quantities need the resonance above 1.25 x f0"]
    assert r["satisfying_measured"] >= 1 and r["measured"] and r["candidates"]
    for m in r["measured"]:
        value = m["predicted"]["Lp@15"]["value"]
        assert m["predicted"]["Lp@15"]["lo"] == value == m["predicted"]["Lp@15"]["hi"] and 0.95 * target <= value <= 1.05 * target
    for c in r["candidates"]:
        assert 0.95 * target <= c["predicted"]["Lp@15"]["lo"] <= c["predicted"]["Lp@15"]["hi"] <= 1.05 * target
    from tests.ic_opt.library_fixtures import use_site

    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    out = CliRunner().invoke(app, ["call", "lib.suggest", str(library), "stratum=ind_demo",
                                   f"targets={json.dumps({'Lp@15': {'target': target, 'tol': 0.05}})}", "n=1", "pool_size=256",
                                   "verify_build=false"])
    assert out.exit_code == 0, out.output
    assert [t["quantity"] for t in json.loads(out.stdout)["targets"]] == ["Lp@15", "SRF_p"]


def test_lib_region_and_lib_densify_take_an_extension_column(library):
    lib = q.Library(library, limits=LOCAL)
    lib.column("ind_demo", "Lp@15")
    values = [r.values["Lp@15"] for r in lib.dataset("ind_demo").usable("Lp@15") if r.coords["turns"] == 2]
    low, high = sorted(values)[len(values) // 4], sorted(values)[len(values) // 2]
    r = region.region(lib, "ind_demo", {"Lp@15": {"min": low, "max": high}}, pool_size=512, workers=1, n=1,
                      steps={"outer_diameter_um": 5, "width_um": 0.5, "spacing_um": 0.5}, trend=("Lp@15.0", "outer_diameter_um"))
    assert [t["quantity"] for t in r["targets"]] == ["Lp@15", "SRF_p"] and r["trend"]["quantity"] == "Lp@15"
    assert r["levels"]["mean"]["count"] > 0 and r["measured"]
    assert all(low <= m["values"]["Lp@15"] <= high for m in r["measured"])
    d = densify.densify(lib, "ind_demo", ["Lp@15.0"], n=1, pool_size=256, top=16, workers=1)
    assert d["quantities"] == ["Lp@15"] and len(d["candidates"]) == 1 and set(d["candidates"][0]["predicted"]) == {"Lp@15"}


def test_the_predict_stage_reads_a_device_metric_at_any_frequency(library, tmp_path):
    """A metric {quantity: Lp, frequency_hz: 15e9} on a stratum that declares Lp at 10 GHz only: the measured row's own
    value at a library point, the model's elsewhere; prefit names the extension column."""
    from tests.ic_opt.test_library_stage import design_spec

    lib = q.Library(library, limits=LOCAL)
    d = design_spec("anyfreq").model_dump(mode="json")
    d["metrics"] = [{"name": "L15", "unit": "H", "device": "ind", "quantity": "Lp", "frequency_hz": 15e9}]
    d["constraints"], d["objective"] = [], {"direction": "maximize", "expression": "L15"}
    spec = Spec.model_validate(d)
    pipeline = stage.surrogate_pipeline(spec, lib)
    assert pipeline[-1].prefit(spec) == {"ind_demo": ["Lp@15"]}
    row = next(r for r in lib.dataset("ind_demo").usable("Lp@15") if r.coords["turns"] == 2)
    store = RunStore(tmp_path)
    obs = evaluate(spec, [Point({k: f"{row.coords[k]:g}" for k in DIMS}, "user"),
                          Point({"outer_diameter_um": "115", "width_um": "4.5", "spacing_um": "2.5", "turns": "2"}, "user")],
                   FakeSpectreExecutor(store.root / "sims"), store, pipeline=pipeline, limits=FAKE_HOST)
    measured, predicted = obs
    assert {o.status for o in obs} == {"ok"} and measured.metrics["L15"] == row.values["Lp@15"]
    assert predicted.metrics["L15"] == pytest.approx(curve_at(115, 4.5, 2.5, 2, "Lp", 15e9), rel=2e-2)


def write_big(root: Path) -> Path:
    """Six large two-turn inductors swept to 60 GHz, every one resonating far below 60 GHz; stratum ind with Lp at 10 GHz."""
    write_store(root, "big", [(od, w, 3.0, 2) for od in (180, 190, 200) for w in (4.0, 5.0)])
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {"ind": {"generator": "clean_port_ind_sym", "dims": DIMS, "nt_dim": "turns", "parts": [{"store": "big"}],
                              "quantities": {"Lp_lf": {}, "SRF_p": {}, "Lp": {"anchors_ghz": [10]}}}}}
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def test_refusals_name_the_declared_curves_and_the_parts_sweeps(library, tmp_path):
    lib = q.Library(library, limits=LOCAL)
    with pytest.raises(ValueError, match=r"no quantities \['Ls@33'\].*Ls: not a curve of ind_demo, whose curves are \['Lp'\]"):
        lib.columns("ind_demo", ["Ls@33"])
    with pytest.raises(ValueError, match="no quantities"):
        q.query(lib, "ind_demo", params(150, 5, 3, 2), ["Qp@20"])
    with pytest.raises(ValueError, match=r"no part is swept at 90 GHz.*the parts' sweeps: nt1 0-60 GHz, nt2 0-60 GHz"):
        q.query(lib, "ind_demo", params(150, 5, 3, 2), ["Lp@90"])
    with pytest.raises(ValueError, match="at most six significant digits"):
        lib.column("ind_demo", "Lp@33.1234567")
    for bad in ("Lp@", "Lp@abc", "Lp@-3", "Lp@0", "Lp@inf"):
        with pytest.raises(ValueError, match="a frequency in GHz, a positive number"):
            lib.column("ind_demo", bad)
    big = q.Library(write_big(tmp_path / "big"), limits=LOCAL)
    assert all(r.values["SRF_p"] is not None and r.values["SRF_p"] < 1.25 * 50e9 for r in big.dataset("ind").rows)
    with pytest.raises(ValueError, match=r"no row has Lp@50: .*srf_margin \(1.25\) x f; the parts' sweeps: big 0-60 GHz"):
        big.column("ind", "Lp@50")
    with pytest.raises(ValueError, match="no row has Lp@50"):                  # and again, once measured
        big.models("ind", ["Lp@50"])
    refused = CliRunner().invoke(app, ["call", "lib.query", str(library), "stratum=ind_demo", f"params={json.dumps(params(150, 5, 3, 2))}",
                                       "quantities=Lp@90"])
    assert refused.exit_code == 2 and "no part is swept at 90 GHz" in refused.output


def test_coverage_says_a_curve_answers_at_any_frequency_and_lists_the_declared_columns(library):
    lib = q.Library(library)                                                    # measuring is reading: no site entry needed
    lib.columns("ind_demo", ["Lp@33"])
    cover = q.coverage(lib, "ind_demo")
    assert list(cover["quantities"]) == list(cover["cached"]) == DECLARED
    assert cover["any_frequency"] == ("a declared curve (Lp) answers at any frequency inside the parts' sweeps as <curve>@<GHz>, "
                                      "not only at its anchors: nt1 0-60 GHz, nt2 0-60 GHz")


def test_spawned_fit_workers_read_the_extension_from_its_cache_file(library, tmp_path):
    """The parent measures the extension columns before it starts any worker; the workers load them from the cache file
    (which none of them writes again) and fit, and the models load here."""
    root = fresh_copy(library, tmp_path / "lib", keep_dataset=True)
    lib = q.Library(root, limits=FAKE_HOST)
    assert lib.columns("ind_demo", ["Lp@15", "Lp@12.5"]) == ["Lp@15", "Lp@12.5"]
    files = {p: p.stat().st_mtime_ns for p in (root / ".cache").glob("anchors-*.json")}
    got = lib.models("ind_demo", ["Lp@15", "Lp@12.5"], workers=2, threads=2)
    assert list(got) == ["Lp@15", "Lp@12.5"] and all(m.quantity in ("Lp@15", "Lp@12.5") for m in got.values())
    assert len(files) == 2 and {p: p.stat().st_mtime_ns for p in files} == files
    stems = sorted(p.name.rsplit("-", 1)[0] for p in (root / ".cache").glob("model-*.pkl"))
    assert stems == ["model-ind_demo-Lp_at_12.5", "model-ind_demo-Lp_at_15"]


def measure_marked(root: str, marker: str, start, guarded: bool) -> list:
    """In a spawned process: extend Lp@33 in the library at ``root``, the first sNp read noting this process in ``marker``
    and taking HOLD_S longer; the processes set off together from ``start``. Unguarded: the lock is replaced by nothing."""
    original = dataset._measure
    first = [True]

    def slow(work, device, stratum):
        if first[0]:
            first[0] = False
            with open(marker, "a", encoding="utf-8") as f:
                f.write(f"{os.getpid()}\n")
            time.sleep(HOLD_S)
        return original(work, device, stratum)

    dataset._measure = slow
    if not guarded:
        _lock.waiting_lock = lambda path: contextlib.nullcontext(False)
    lib = q.Library(root, limits=LOCAL)
    lib.dataset("ind_demo")                                                    # read from its cache before the start
    start.wait(timeout=120)
    lib.columns("ind_demo", ["Lp@33"])
    return [r.values["Lp@33"] for r in lib.dataset("ind_demo").rows]


@pytest.mark.parametrize("guarded", [True, False], ids=["locked", "unlocked"])
def test_one_process_measures_an_extension_at_a_time(library, tmp_path, guarded):
    """Two spawned processes ask for the same extension column at the same moment: under the lock next to its cache file
    one measures and the other loads what it wrote; the control without the lock measures in both."""
    root = fresh_copy(library, tmp_path / "lib", keep_dataset=True)
    marker = tmp_path / "measured.txt"
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        start = manager.Barrier(2)
        jobs = [pool.submit(measure_marked, str(root), str(marker), start, guarded) for _ in range(2)]
        first, second = (job.result(timeout=300) for job in jobs)
    measured = marker.read_text(encoding="utf-8").split()
    assert first == second and any(v is not None for v in first)
    assert len(measured) == (1 if guarded else 2)
    if guarded:
        (cached,) = (root / ".cache").glob("anchors-ind_demo-*-33.json")
        assert (root / ".cache" / f"{cached.name}.lock").is_file()


def test_an_extension_column_keeps_its_curves_rule_resonance_and_ceiling_included(tmp_path):
    """A curve declared at 20 GHz with model: resonance, a feature map and a confidence ceiling: its column at 15 GHz is
    composed the same way -- the stratum's own Lp_lf and SRF models, shared, times the ideal rise at 15 GHz and a residual
    on the mapped inputs -- held to the same ceiling, and lib.query says where its value came from."""
    from ic_opt.library import composed
    from tests.ic_opt.library_fixtures import MAPPED, RES_STRATUM, XFM_DIMS, build_resonance_library

    rule = {"model": "resonance", "feature_map": MAPPED, "rel_sigma_max": 0.07}
    lib = q.Library(build_resonance_library(tmp_path / "lib", lp=rule), limits=LOCAL)
    lp15, lf, srf = lib.model(RES_STRATUM, "Lp@15"), lib.model(RES_STRATUM, "Lp_lf"), lib.model(RES_STRATUM, "SRF")
    assert isinstance(lp15.gp, composed.ComposedGP) and lp15.gp.kind == "resonance" and lp15.gp.f0 == 15.0
    assert lp15.gp.lf is lf.gp and lp15.gp.srf is srf.gp and lp15.gp.part.feature_map == MAPPED
    assert lp15.rel_sigma_max == 0.07 and lp15.calibration["model"] == "resonance"
    answer = q.query(lib, RES_STRATUM, dict(zip(XFM_DIMS, [110.0, 104.0, 6.0, 7.0, 0.0])), ["Lp@15"])["quantities"]["Lp@15"]
    assert answer["status"] in ("predicted", "uncertain") and answer["rel_sigma_max"] == 0.07
    c = answer["composition"]
    assert c["model"] == "resonance" and c["Lp_lf"] * c["resonance_factor"] * c["residual"] == pytest.approx(answer["value"], rel=1e-9)
