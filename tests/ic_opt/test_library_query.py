"""T13.3: lib.query / lib.coverage / lib.load on a synthetic library, and `ic-opt call` on a library root.
T14.1: fitted models cached on disk, and Library.models fitting the uncached ones in parallel processes.
T15.4: those processes are spawned (Windows / macOS / Linux alike): fresh interpreters that inherit nothing."""
from __future__ import annotations

import json
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from threadpoolctl import threadpool_limits
from typer.testing import CliRunner

from ic_opt import migrate_store
from ic_opt.cli import app
from ic_opt.library import dataset, gp
from ic_opt.library import query as q
from ic_opt.spec import load_spec
from tests.ic_opt.fakes import FakeSpectreExecutor, age_store
from tests.ic_opt.library_fixtures import STOP_GHZ, build_library, build_xfm_library, params, truth


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
    return build_library(tmp_path_factory.mktemp("qlib"))


def count_fits(monkeypatch) -> list[str]:
    """From now on, the nt_mode of every model fitted in this process (a per_nt model's sub-GPs fit inside its fit, uncounted)."""
    fits: list[str] = []
    depth = [0]
    original = gp.StratumGP.fit

    def fit(self, x, y):
        if depth[0] == 0:
            fits.append(self.nt_mode)
        depth[0] += 1
        try:
            return original(self, x, y)
        finally:
            depth[0] -= 1

    monkeypatch.setattr(gp.StratumGP, "fit", fit)
    return fits


def fresh_copy(library: Path, dest: Path) -> Path:
    """The synthetic library without its .cache: nothing calibrated or fitted yet."""
    return shutil.copytree(library, dest, ignore=shutil.ignore_patterns(".cache"))


def test_measured_at_library_points_and_calibrated_predictions_between(library):
    lib = q.Library(library)
    hit = q.query(lib, "ind_demo", params(150, 5, 3, 2))
    assert hit["measured"]["part"] == "nt2" and {v["status"] for v in hit["quantities"].values()} == {"measured"}
    assert hit["quantities"]["Lp_lf"]["value"] == lib.dataset("ind_demo").find(params(150, 5, 3, 2)).values["Lp_lf"]
    between = q.query(lib, "ind_demo", params(155, 4.5, 2.5, 2), ["Lp_lf", "Qp_peak"])
    lp = between["quantities"]["Lp_lf"]
    assert between["measured"] is None and lp["status"] == "predicted" and lp["unit"] == "H" and lp["k_scale"] >= 1.0
    assert lp["lo"] < lp["value"] < lp["hi"] and len(lp["nearest"]) == 3 and lp["nearest"][0]["scaled_distance"] > 0
    assert lp["value"] == pytest.approx(truth(155, 4.5, 2.5, 2)["Lp_lf"], rel=2e-2)
    single = q.query(lib, "ind_demo", params(135, 5.5, 2, 1), ["Lp_lf"])["quantities"]["Lp_lf"]
    assert single["status"] == "predicted" and single["value"] == pytest.approx(truth(135, 5.5, 2, 1)["Lp_lf"], rel=2e-2)


def test_domain_answers_carry_the_reason_and_evidence(library):
    lib = q.Library(library)
    answers = {
        "range": q.query(lib, "ind_demo", params(250, 5, 2, 2), ["Lp_lf"]),
        "fixed spacing": q.query(lib, "ind_demo", params(135, 5, 3, 1), ["Lp_lf"]),
        "half turn": q.query(lib, "ind_demo", params(135, 5, 2, 1.5), ["Lp_lf"]),
    }
    got = {k: (a["quantities"]["Lp_lf"]["status"], a["quantities"]["Lp_lf"]["criterion"]) for k, a in answers.items()}
    assert got == {"range": ("out_of_domain", 1), "fixed spacing": ("out_of_domain", 1), "half turn": ("out_of_domain", 2)}
    assert "spacing_um is 2 for every row at turns=1" in answers["fixed spacing"]["quantities"]["Lp_lf"]["reason"]
    assert answers["range"]["quantities"]["Lp_lf"]["fill_points"][1]["outer_diameter_um"] == 200.0
    small = q.query(lib, "ind_demo", params(85, 5.5, 2, 1), ["SRF_p"])["quantities"]["SRF_p"]
    assert truth(85, 5.5, 2, 1)["SRF_p"] is None
    assert small["status"] == "above_sweep" and small["lower_bound"] == STOP_GHZ * 1e9 and small["value"] is None
    big = q.query(lib, "ind_demo", params(185, 5.5, 2.5, 2), ["SRF_p"])["quantities"]["SRF_p"]
    assert big["status"] == "predicted" and big["value"] == pytest.approx(truth(185, 5.5, 2.5, 2)["SRF_p"], rel=3e-2)
    shaky = q.query(lib, "ind_demo", params(155, 4.5, 2.5, 2), ["Lp_lf"], rel_sigma_max=1e-12)["quantities"]["Lp_lf"]
    assert shaky["status"] == "uncertain" and shaky["value"] > 0 and shaky["rel_sigma"] > 1e-12
    with pytest.raises(ValueError, match="missing"):
        q.query(lib, "ind_demo", {"outer_diameter_um": 100}, ["Lp_lf"])
    with pytest.raises(ValueError, match="no quantities"):
        q.query(lib, "ind_demo", params(150, 5, 3, 2), ["Ls_lf"])


def test_calibration_is_cached_and_never_narrows(library, monkeypatch):
    first = q.Library(library).model("ind_demo", "Qp_peak")
    files = list((library / ".cache").glob("calibration-ind_demo-Qp_peak-*.json"))
    assert len(files) == 1 and first.calibration["k_scale"] >= 1.0 and first.calibration["source"] == "holdout 5x20%"
    monkeypatch.setattr(gp, "holdout", lambda *a, **k: pytest.fail("calibration must come from the cache"))
    again = q.Library(library).model("ind_demo", "Qp_peak")
    assert again.calibration == first.calibration and again.gp.k_scale == first.calibration["k_scale"]
    assert q.Library(library, calibrate=False).model("ind_demo", "Qp_peak").calibration == {"k_scale": 1.0, "source": "off"}


def test_a_new_library_loads_the_fitted_model_from_disk(library, monkeypatch):
    """What a new process sees: no fit, rows and domain guard rebuilt from the dataset, the fitted model's answers exactly."""
    for stale in (library / ".cache").glob("model-ind_demo-Lp_at_10-*.pkl"):
        stale.unlink()                                                             # so the first instance below fits
    fitted = q.Library(library).model("ind_demo", "Lp@10")
    fits = count_fits(monkeypatch)
    second = q.Library(library)
    loaded = second.model("ind_demo", "Lp@10")
    assert fits == [] and loaded.gp is not fitted.gp and loaded.calibration == fitted.calibration
    assert [(r.part, r.obs_id) for r in loaded.rows] == [(r.part, r.obs_id) for r in fitted.rows]
    x = second.dataset("ind_demo").matrix(loaded.rows)[::10]
    assert (loaded.guard.inside(x) == fitted.guard.inside(x)).all()
    for got, want in zip(loaded.gp.predict(x), fitted.gp.predict(x)):                  # (mu, sigma)
        np.testing.assert_allclose(got, want, rtol=1e-12)


def test_a_corrupt_model_cache_is_refitted_and_overwritten(library, monkeypatch):
    lib = q.Library(library)
    lib.model("ind_demo", "Lp_lf")
    path = lib._model_file("ind_demo", "Lp_lf")
    path.write_bytes(b"\x80\x05 not a pickle")
    fits = count_fits(monkeypatch)
    q.Library(library).model("ind_demo", "Lp_lf")
    assert fits == ["per_nt"] and lib._model_file("ind_demo", "Lp_lf") == path
    q.Library(library).model("ind_demo", "Lp_lf")
    assert fits == ["per_nt"]                                                       # the third instance loads the rewritten file


def test_a_new_cache_version_never_reads_old_pickles(library, monkeypatch):
    lib = q.Library(library)
    lib.model("ind_demo", "Qp_peak")
    old = lib._model_file("ind_demo", "Qp_peak")
    monkeypatch.setattr(q, "MODEL_CACHE_VERSION", q.MODEL_CACHE_VERSION + 1)
    assert old is not None and lib._model_file("ind_demo", "Qp_peak") is None
    fits = count_fits(monkeypatch)
    q.Library(library).model("ind_demo", "Qp_peak")
    new = lib._model_file("ind_demo", "Qp_peak")
    assert fits == ["per_nt"] and new is not None and new != old and old.is_file()


def fit_here(self, x, y):
    """StratumGP.fit in this process while Library.models runs: only its workers may fit, and they are spawned."""
    raise AssertionError("a fit ran in the calling process, or in a worker that inherited its state (fork, not spawn)")


def test_models_fits_the_uncached_quantities_in_spawned_processes(library, tmp_path, monkeypatch):
    """Nothing cached: two worker processes calibrate, fit and write the caches, this process only loads them, and the models
    answer as sequential model() calls do. The workers are spawned: fresh interpreters, so this process's patched fit, which
    forked workers would inherit, never reaches them. Both sides fit on one BLAS thread: OpenBLAS rounds differently with
    one thread than with several, which moves this library's fits by up to 2e-6."""
    names = ["Qp_peak", "SRF_p"]
    sequential = fresh_copy(library, tmp_path / "sequential")
    with threadpool_limits(limits=1):
        reference = q.Library(sequential)
        expected = {name: reference.model("ind_demo", name) for name in names}
    parallel = fresh_copy(library, tmp_path / "parallel")
    monkeypatch.setattr(gp.StratumGP, "fit", fit_here)
    got = q.Library(parallel).models("ind_demo", names, workers=2, threads=2)
    assert list(got) == names
    stems = sorted(p.name.rsplit("-", 1)[0] for p in (parallel / ".cache").glob("*-ind_demo-*"))
    assert stems == ["calibration-ind_demo-Qp_peak", "calibration-ind_demo-SRF_p", "dataset-ind_demo", "model-ind_demo-Qp_peak",
                     "model-ind_demo-SRF_p"]
    x = reference.dataset("ind_demo").matrix()[::15]
    for name in names:
        for mine, theirs in zip(got[name].gp.predict(x), expected[name].gp.predict(x)):
            np.testing.assert_allclose(mine, theirs, rtol=1e-12)


def fit_in_a_failing_worker(*args):
    """A Library.models worker whose fits all fail: StratumGP.fit is broken inside the spawned process only."""
    def fit(self, x, y):
        raise np.linalg.LinAlgError("not positive definite")

    gp.StratumGP.fit = fit
    return q._fit_in_worker(*args)


def test_models_raises_a_failed_fit_from_its_worker(library, tmp_path, monkeypatch):
    """The fit fails only in the worker processes: had their error been dropped, this process -- whose fit works -- would
    have fitted without one. The error arrives with the worker's own traceback."""
    monkeypatch.setattr(q, "_fit_in_worker", fit_in_a_failing_worker)             # what models() hands its workers
    with pytest.raises(np.linalg.LinAlgError, match="not positive definite") as failed:
        q.Library(fresh_copy(library, tmp_path / "lib")).models("ind_demo", ["Lp_lf", "SRF_p"], workers=2)
    assert ", in _fit_in_worker\n" in str(failed.value.__cause__)                  # concurrent.futures' remote traceback


def fit_recording_blas(root: Path, quantity: str, threads: int) -> list[tuple[str, int]]:
    """In a spawned process: _fit_in_worker with every fit noting the size of each BLAS / OpenMP pool as it starts."""
    from threadpoolctl import threadpool_info

    seen: list[tuple[str, int]] = []
    original = gp.StratumGP.fit

    def fit(self, x, y):
        seen.extend((pool["internal_api"], pool["num_threads"]) for pool in threadpool_info())
        return original(self, x, y)

    gp.StratumGP.fit = fit
    q._fit_in_worker(root, True, "ind_demo", quantity, threads)
    return seen


def test_a_spawned_worker_fits_with_every_blas_pool_capped(library, tmp_path, monkeypatch):
    """_fit_in_worker in a fresh interpreter, as Library.models starts it: its arguments are all it needs, and every fit (the
    five hold-out fits of the calibration, then the model) runs with each BLAS / OpenMP pool at ``threads``."""
    monkeypatch.setenv("OMP_NUM_THREADS", "3")                  # the worker's pools start at 3, so a missing cap would show
    for name in ("OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.delenv(name, raising=False)
    root = fresh_copy(library, tmp_path / "lib")
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        seen = pool.submit(fit_recording_blas, root, "Lp_lf", 1).result()
    assert len(seen) >= 6 and {api for api, _ in seen} >= {"openblas", "openmp"} and {n for _, n in seen} == {1}
    assert len(list((root / ".cache").glob("model-ind_demo-Lp_lf-*.pkl"))) == 1


def test_coverage_and_load(library):
    lib = q.Library(library)
    c = q.coverage(lib, "ind_demo")
    assert c["rows"] == 39 + 66 and c["parts"] == {"nt1": 39, "nt2": 66} and c["levels"] == {1: 39, 2: 66}
    assert c["dims"]["outer_diameter_um"] == {"min": 80.0, "max": 200.0, "distinct": 13} and c["quantities"]["Lp_lf"]["rows"] == 105
    assert c["quantities"]["SRF_p"]["unit"] == "Hz" and 0 < c["quantities"]["SRF_p"]["rows"] < 105
    loaded = q.load(lib)
    assert loaded["ind_demo"]["rows"] == 105 and loaded["ind_demo"]["passive_rows"] == 105


def test_call_on_a_library_root_prints_json(library):
    runner = CliRunner()
    out = runner.invoke(app, ["call", "lib.query", str(library), "stratum=ind_demo", 'params={"outer_diameter_um":150,"width_um":5,"spacing_um":3,"turns":2}',
                              "quantities=Lp_lf,SRF_p"])
    assert out.exit_code == 0, out.output
    body = json.loads(out.output)
    assert body["measured"]["obs_id"] and set(body["quantities"]) == {"Lp_lf", "SRF_p"}
    cov = runner.invoke(app, ["call", "lib.coverage", str(library), "stratum=ind_demo"])
    assert cov.exit_code == 0 and json.loads(cov.output)["rows"] == 105
    bad = runner.invoke(app, ["call", "lib.coverage", str(library), "stratum=nope"])
    assert bad.exit_code == 2 and "no stratum 'nope'" in bad.output


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("build", [build_library, build_xfm_library], ids=["ind", "xfm"])
def test_migrate_store_keeps_each_library_part_one_generation(tmp_path, build):
    """T15.2 / C2 on the synthetic libraries stamped the pre-T15.2 way: after migrating every part, lib.load reports the same
    generations (renamed one to one) and the datasets the same rows; --dry-run writes nothing, a second run changes nothing."""
    root = build(tmp_path / "lib")
    host = FakeSpectreExecutor(tmp_path / "host")                                   # hashes every process file to one content
    parts = sorted(p.parent.parent for p in root.glob("*/.icopt/observations.jsonl"))
    for part in parts:
        age_store(part, load_spec(part / "spec.yaml"), host)
    before = q.load(q.Library(root))
    rows = {name: [asdict(r) for r in dataset.build(root, name, cache=False).rows] for name in before}

    files = tree(root)
    dry = [migrate_store.migrate(part, host, dry_run=True) for part in parts]
    assert tree(root) == files and all(d.restamped == d.rows == d.spec_rows == d.pipeline_rows > 0 for d in dry)

    renamed = {old: new for part in parts for old, new in migrate_store.migrate(part, host).pipelines.values()}
    after = q.load(q.Library(root))
    for name, b in before.items():
        assert len(set(b["generations"].values())) == 1 and after[name]["generations"] == {p: renamed[g] for p, g in b["generations"].items()}
        assert {k: v for k, v in after[name].items() if k not in ("generations", "cache")} == {k: v for k, v in b.items() if k not in ("generations", "cache")}
        assert [asdict(r) for r in dataset.build(root, name, cache=False).rows] == rows[name]
    files = tree(root)
    assert not any(migrate_store.migrate(part, host).changed for part in parts) and tree(root) == files


def test_migrate_store_keeps_the_library_caches(tmp_path, monkeypatch):
    """The dataset key is what the rows are, not how they are stamped: after migrating every part each .cache file keeps
    its name, the dataset is a cache hit that reports the new generations, and the model loads instead of refitting."""
    root = build_library(tmp_path / "lib")
    host = FakeSpectreExecutor(tmp_path / "host")
    parts = sorted(p.parent.parent for p in root.glob("*/.icopt/observations.jsonl"))
    for part in parts:
        age_store(part, load_spec(part / "spec.yaml"), host)
    q.Library(root).model("ind_demo", "Lp_lf")                            # dataset, calibration and model cached, old stamps
    caches = sorted(p.name for p in (root / ".cache").iterdir())
    assert any(c.startswith("dataset-ind_demo-") for c in caches) and any(c.startswith("model-ind_demo-Lp_lf-") for c in caches)

    renamed = {old: new for part in parts for old, new in migrate_store.migrate(part, host).pipelines.values()}
    fits = count_fits(monkeypatch)
    lib = q.Library(root)
    lib.model("ind_demo", "Lp_lf")
    ds = lib.dataset("ind_demo")
    assert fits == [] and sorted(p.name for p in (root / ".cache").iterdir()) == caches
    assert ds.cache == "hit" and set(ds.generations.values()) == set(renamed.values())
