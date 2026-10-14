"""N-126 (``docs/refactor/N126_FREQUENCY_GRID_SPEC.md``): a frequency grid declared in library.yaml, each curve's knots, and
``lib.prefit`` to fit them ahead.

``frequency_grid`` is a library-level entry (``manifest.FrequencyGrid``): below ``until_ghz`` the distinct round(from_ghz x
ratio^n), then every ``step_ghz`` from ``until_ghz`` on, inside a part's sweep. It is in no stratum's dump, so no dataset key
and no cache file name changes. ``Library.knots`` is a curve's anchors and the grid's frequencies, less the columns that can
have no model; the grid's columns are measured in one pass over the rows (``dataset.anchors_at``). ``lib.prefit`` fits every
knot column not cached, stratum after stratum, and is resumable; ``Library.cached`` adds ``grid n/m`` per curve.

The fake strata (``grid_library``), two-turn and single-turn inductors swept 0-60 GHz, each one joint model per column (no
turns dim): ``big`` -- six large two-turn inductors resonating at 16-20 GHz, so at 16 GHz one row has a value (too few for a
joint model, which needs three) and from 20 GHz on none -- and ``tiny`` -- six single-turn ones resonating above the sweep,
with a value at every frequency. Both declare SRF_p and the curves Lp and Qp anchored at 10 GHz; the grid ``GRID`` gives 2,
4, 8, 16, 38 and 60 GHz."""
from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ic_opt import blocks
from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.library import dataset, manifest, prefit
from ic_opt.library import query as q
from tests.ic_opt.fakes import FAKE_HOST
from tests.ic_opt.library_fixtures import (
    LOCAL,
    RES_STRATUM,
    build_library,
    build_resonance_library,
    build_xfm_library,
    use_site,
    write_store,
)
from tests.ic_opt.test_library import DIMS
from tests.ic_opt.test_library_anyfreq import IND_KEY, XFM_KEY

RULE = {"from_ghz": 1, "ratio": 1.1, "until_ghz": 50, "step_ghz": 5}         # the specification's entry
RULE_0_250 = [*range(1, 15), 16, 17, 19, 21, 23, 26, 28, 31, 34, 37, 41, 45, *range(50, 251, 5)]
GRID = {"from_ghz": 2, "ratio": 2, "until_ghz": 16, "step_ghz": 22}         # 2, 4, 8, then 16, 38, 60 over 0-60 GHz
CANDIDATES = [2.0, 4.0, 8.0, 10.0, 16.0, 38.0, 60.0]                          # with the anchor at 10 GHz
BIG_KNOTS = [2.0, 4.0, 8.0, 10.0]
TOO_FEW = "{} usable rows: too few for a model of this column"
BIG_SKIPPED = {"Lp@16": TOO_FEW.format(1), "Lp@38": TOO_FEW.format(0), "Lp@60": TOO_FEW.format(0),
               "Qp@16": TOO_FEW.format(1), "Qp@38": TOO_FEW.format(0), "Qp@60": TOO_FEW.format(0)}
QUANTITIES = {"SRF_p": {}, "Lp": {"anchors_ghz": [10]}, "Qp": {"anchors_ghz": [10]}}


def set_grid(root: Path, grid: dict | None) -> Path:
    """Rewrite ``root``'s library.yaml with ``grid`` as its frequency_grid (None: without one)."""
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc.pop("frequency_grid", None)
    if grid is not None:
        doc["frequency_grid"] = grid
    (root / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return root


def grid_library(root: Path, grid: dict | None = GRID) -> Path:
    write_store(root, "big", [(od, w, 3.0, 2) for od in (180.0, 190.0, 200.0) for w in (4.0, 5.0)])
    write_store(root, "tiny", [(od, w, 2.0, 1) for od in (80.0, 90.0, 100.0) for w in (5.0, 6.0)])
    strata = {name: {"generator": "clean_port_ind_sym", "dims": DIMS, "parts": [{"store": name}], "quantities": QUANTITIES}
              for name in ("big", "tiny")}
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m", "strata": strata}
    (root / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return set_grid(root, grid)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The grid library with its datasets cached (copied per test without the rest of its cache)."""
    root = grid_library(tmp_path_factory.mktemp("grid"))
    for name in ("big", "tiny"):
        dataset.build(root, name)
    return root


def fresh(built: Path, dest: Path) -> Path:
    """A copy of the grid library holding only its datasets' cache files."""
    copied = shutil.copytree(built, dest, ignore=shutil.ignore_patterns(".cache"))
    (copied / ".cache").mkdir()
    for p in (built / ".cache").glob("dataset-*.json"):
        shutil.copy2(p, copied / ".cache" / p.name)
    return copied


def fitted(monkeypatch) -> list[tuple[str, str]]:
    """From now on, every (stratum, column) ``Library._fit`` fits in this process."""
    out: list[tuple[str, str]] = []
    original = q.Library._fit

    def fit(self, stratum, quantity):
        out.append((stratum, quantity))
        return original(self, stratum, quantity)

    monkeypatch.setattr(q.Library, "_fit", fit)
    return out


# -- the rule -------------------------------------------------------------------------------------------------------------

def test_the_rule_gives_the_67_frequencies_of_a_0_250_ghz_sweep_and_keeps_to_the_sweeps():
    grid = manifest.FrequencyGrid(**RULE)
    assert grid.frequencies([(0.0, 250e9)]) == [float(f) for f in RULE_0_250] and len(RULE_0_250) == 67
    assert grid.frequencies([(0.0, 60e9)]) == [float(f) for f in RULE_0_250 if f <= 60]           # clipped to the sweep
    assert grid.frequencies([(10e9, 60e9), (100e9, 120e9)]) == [10.0, 11.0, 12.0, 13.0, 14.0, 16.0, 17.0, 19.0, 21.0, 23.0, 26.0,
                                                                   28.0, 31.0, 34.0, 37.0, 41.0, 45.0, 50.0, 55.0, 60.0, 100.0,
                                                                   105.0, 110.0, 115.0, 120.0]   # inside one of the sweeps
    assert grid.frequencies([(0.0, 250e9), (0.0, 120e9)]) == grid.frequencies([(0.0, 250e9)])
    assert grid.frequencies([]) == []                                                           # no part states its sweep
    assert manifest.FrequencyGrid(**GRID).frequencies([(0.0, 60e9)]) == [2.0, 4.0, 8.0, 16.0, 38.0, 60.0]
    small = manifest.FrequencyGrid(from_ghz=0.3, ratio=1.5, until_ghz=10, step_ghz=2.5)          # round() of 0.3, 0.45: no frequency
    assert small.frequencies([(0.0, 20e9)]) == [1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 12.5, 15.0, 17.5, 20.0]
    fine = manifest.FrequencyGrid(from_ghz=1, ratio=2, until_ghz=50, step_ghz=0.1).frequencies([(0.0, 50.35e9)])
    assert fine[-4:] == [50.0, 50.1, 50.2, 50.3] and all(float(f"{f:g}") == f for f in fine)     # each names its column


@pytest.mark.parametrize("entry, message", [
    ({**RULE, "ratio": 1.0}, "ratio\n  Input should be greater than 1"),
    ({**RULE, "step_ghz": 0}, "step_ghz\n  Input should be greater than 0"),
    ({**RULE, "from_ghz": 0}, "from_ghz\n  Input should be greater than 0"),
    ({**RULE, "from_ghz": 50}, r"from_ghz \(50\) must lie below until_ghz \(50\)"),
    ({**RULE, "until_ghz": float("inf")}, "until_ghz\n  Input should be a finite number"),
    ({**RULE, "stop_ghz": 250}, "stop_ghz\n  Extra inputs are not permitted"),
])
def test_the_entry_is_checked(entry, message):
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m", "frequency_grid": entry,
           "strata": {"s": {"generator": "clean_port_ind_sym", "dims": DIMS, "parts": [{"store": "p"}], "quantities": QUANTITIES}}}
    with pytest.raises(ValueError, match=message):
        manifest.Library.model_validate(doc)
    assert manifest.Library.model_validate({**doc, "frequency_grid": RULE}).frequency_grid == manifest.FrequencyGrid(**RULE)
    assert manifest.Library.model_validate({k: v for k, v in doc.items() if k != "frequency_grid"}).frequency_grid is None


def test_the_entry_changes_no_dataset_key_and_no_cache_file_name(tmp_path):
    """With and without the entry: main's dataset keys (an inductor, a transformer and a composed-curve table), a composed
    curve's calibration name, and the same cache files, byte for byte where they are JSON, for a declared column, an
    extension column and their resonance's models."""
    root = build_library(tmp_path / "ind")
    listings = {}
    for grid in (None, RULE):
        set_grid(root, grid)
        cache = tmp_path / f"cache_{'grid' if grid else 'none'}"
        lib = q.Library(root, limits=LOCAL, cache_dir=cache)
        assert lib.dataset("ind_demo").key == IND_KEY and (lib.manifest.frequency_grid is None) == (grid is None)
        lib.model("ind_demo", "Lp@10")
        lib.model("ind_demo", "Lp@15")
        listings[grid is not None] = {p.name: p.read_bytes() if p.suffix == ".json" else None for p in sorted(cache.iterdir())}
    assert listings[True] == listings[False]
    names = sorted(listings[True])
    assert [n.rsplit("-", 1)[0] for n in names if n.startswith("model-")] == ["model-ind_demo-Lp_at_10", "model-ind_demo-Lp_at_15",
                                                                               "model-ind_demo-SRF_p"]
    assert f"anchors-ind_demo-{IND_KEY}-15.json" in names and f"dataset-ind_demo-{IND_KEY}.json" in names
    xroot = build_xfm_library(tmp_path / "xfm")
    rroot = build_resonance_library(tmp_path / "res", lp={"model": "resonance", "feature_map": "xfm_bs_dimensionless"})
    composed = {}
    for grid in (None, RULE):
        assert dataset.build(set_grid(xroot, grid), "xfm_demo", cache=False).key == XFM_KEY
        lib = q.Library(set_grid(rroot, grid), limits=LOCAL, cache_dir=tmp_path / "res_cache")
        composed[grid is not None] = (lib.dataset(RES_STRATUM).key, lib._composed_calibration_name(RES_STRATUM, "Lp@20"))
    assert composed[True] == composed[False]


# -- one pass for several frequencies -------------------------------------------------------------------------------------

def counted(monkeypatch) -> list[str]:
    """From now on, every row sNp ``dataset._measure`` measures (its directory)."""
    out: list[str] = []
    original = dataset._measure

    def measure(work, device, stratum):
        out.append(str(work))
        return original(work, device, stratum)

    monkeypatch.setattr(dataset, "_measure", measure)
    return out


def test_several_frequencies_are_measured_in_one_pass_into_the_files_anchors_writes(built, tmp_path, monkeypatch):
    root = fresh(built, tmp_path / "lib")
    rule = manifest.load(root).strata["big"]
    ds = dataset.build(root, "big")
    one = q.Library(root, limits=LOCAL, cache_dir=tmp_path / "one").cache
    many = q.Library(root, limits=LOCAL, cache_dir=tmp_path / "many").cache
    expected = {f: dataset.anchors(root, ds, rule, f, cache=one) for f in (15.0, 25.0, 33.0)}
    measured = counted(monkeypatch)
    got = dataset.anchors_at(root, ds, rule, [15.0, 25.0, 33.0], cache=many)
    assert got == expected and len(measured) == len(ds.rows)                         # one pass, not three
    for f in (15.0, 25.0, 33.0):
        name = dataset.anchors_file(ds, f)
        assert (tmp_path / "many" / name).read_bytes() == (tmp_path / "one" / name).read_bytes()
        assert (tmp_path / "many" / f"{name}.lock").is_file()
    assert dataset.anchors_at(root, ds, rule, [33.0, 15.0], cache=many) == {33.0: expected[33.0], 15.0: expected[15.0]}
    assert len(measured) == len(ds.rows)                                             # read from the cache
    assert dataset.anchors_at(root, ds, rule, [25.0, 40.0], cache=many)[25.0] == expected[25.0]
    assert len(measured) == 2 * len(ds.rows) and (tmp_path / "many" / dataset.anchors_file(ds, 40.0)).is_file()
    assert dataset.anchors_at(root, ds, rule, [40.0, 41.0], cache=None)[40.0] == dataset.anchors(root, ds, rule, 40.0, cache=many)
    assert not (tmp_path / "many" / dataset.anchors_file(ds, 41.0)).exists()          # cache None: nothing written


def knots_marked(root: str, marker: str, start) -> dict:
    """In a spawned process: every curve's knots of ``big``, the first sNp read noting this process in ``marker`` and taking
    a second longer; the processes set off together from ``start``."""
    original = dataset._measure
    first = [True]

    def slow(work, device, stratum):
        if first[0]:
            first[0] = False
            with open(marker, "a", encoding="utf-8") as f:
                f.write(f"{os.getpid()}\n")
            time.sleep(1.0)
        return original(work, device, stratum)

    dataset._measure = slow
    lib = q.Library(root, limits=LOCAL)
    lib.dataset("big")
    start.wait(timeout=120)
    return {c: lib.knots("big", c) for c in ("Lp", "Qp")}


def test_one_process_measures_the_grid_at_a_time(built, tmp_path):
    """Two processes ask for the knots at once: one measures the grid's six frequencies, holding their lock files, and the
    other reads what it wrote."""
    root = fresh(built, tmp_path / "lib")
    marker = tmp_path / "measured.txt"
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        start = manager.Barrier(2)
        jobs = [pool.submit(knots_marked, str(root), str(marker), start) for _ in range(2)]
        first, second = (job.result(timeout=300) for job in jobs)
    assert first == second == {"Lp": BIG_KNOTS, "Qp": BIG_KNOTS}
    assert len(marker.read_text(encoding="utf-8").split()) == 1
    assert len(list((root / ".cache").glob("anchors-big-*.json"))) == 6                 # the grid's six frequencies, measured once


# -- knots ----------------------------------------------------------------------------------------------------------------

def test_knots_are_the_anchors_and_the_grid_less_the_columns_that_can_have_no_model(built, tmp_path, monkeypatch):
    root = fresh(built, tmp_path / "lib")
    lib = q.Library(root)                                                    # measuring is reading: no site entry needed
    big = lib.dataset("big")
    assert sorted(round(r.values["SRF_p"] / 1e9, 2) for r in big.rows) == [16.29, 17.62, 18.11, 18.81, 19.13, 20.33]
    assert all(r.values["SRF_p"] is None for r in lib.dataset("tiny").rows)           # resonating above the sweep
    assert lib.grid("big") == [2.0, 4.0, 8.0, 16.0, 38.0, 60.0]
    measured = counted(monkeypatch)
    assert lib.knots("big", "Lp") == BIG_KNOTS                                          # 16 GHz: one row; 38, 60: none
    assert len(measured) == len(big.rows)                                              # the six frequencies in one pass
    assert lib.knots("big", "Qp") == BIG_KNOTS and len(measured) == len(big.rows)
    assert {c: lib.no_model("big", c)["reason"] for c in BIG_SKIPPED} == BIG_SKIPPED
    assert lib.knots("tiny", "Lp") == lib.knots("tiny", "Qp") == CANDIDATES
    assert big.columns == ["SRF_p", "Lp@10", "Qp@10"] and set(big.extensions) == {
        f"{c}@{f:g}" for c in ("Lp", "Qp") for f in (2, 4, 8, 16, 38, 60)}            # the dataset's own columns as they were
    assert not list((root / ".cache").glob("model-*")) and not list((root / ".cache").glob("calibration-*"))   # nothing fitted
    with pytest.raises(ValueError, match=r"big declares no curve 'Ls'; its curves: \['Lp', 'Qp'\]"):
        lib.knots("big", "Ls")
    with pytest.raises(ValueError, match="no stratum 'nope'"):
        lib.knots("nope", "Lp")
    plain = q.Library(set_grid(fresh(built, tmp_path / "plain"), None))
    assert plain.grid("big") == [] and plain.knots("big", "Lp") == [10.0] and plain.knots("tiny", "Qp") == [10.0]


def test_cached_counts_each_curves_knots_and_coverage_answers_with_it(built, tmp_path, monkeypatch):
    root = fresh(built, tmp_path / "lib")
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    lib = q.Library(root)
    assert lib.cached("big") == {"SRF_p": "none", "Lp@10": "none", "Qp@10": "none", "Lp": "grid 0/4", "Qp": "grid 0/4"}
    lib.models("big", ["Lp@4", "Lp@10"])
    assert lib.cached("big") == {"SRF_p": "model", "Lp@10": "model", "Qp@10": "none", "Lp": "grid 2/4", "Qp": "grid 0/4"}
    cover = library_blocks.coverage(root, "tiny")
    assert cover["cached"] == {"SRF_p": "too few rows (0)", "Lp@10": "none", "Qp@10": "none", "Lp": "grid 0/7", "Qp": "grid 0/7"}
    assert q.load(lib, "big")["big"]["cached"]["Lp"] == "grid 2/4"
    plain = q.Library(set_grid(fresh(built, tmp_path / "plain"), None))
    assert plain.cached("big") == {"SRF_p": "none", "Lp@10": "none", "Qp@10": "none"}       # without a grid, as before


# -- lib.prefit -----------------------------------------------------------------------------------------------------------

def test_prefit_fits_the_missing_knot_columns_and_a_second_run_fits_nothing(built, tmp_path, monkeypatch, capsys):
    root = fresh(built, tmp_path / "lib")
    fits = fitted(monkeypatch)
    out = prefit.prefit(q.Library(root, limits=LOCAL), strata=["big"], curves=["Lp"])
    big = out["strata"]["big"]
    assert out["frequency_grid"] == {"from_ghz": 2.0, "ratio": 2.0, "until_ghz": 16.0, "step_ghz": 22.0} and list(out["strata"]) == ["big"]
    assert big["knots"] == {"Lp": BIG_KNOTS} and big["fitted"] == ["Lp@2", "Lp@4", "Lp@8", "Lp@10"] and big["cached"] == []
    assert big["skipped"] == {c: why for c, why in BIG_SKIPPED.items() if c.startswith("Lp")} and big["seconds"] >= 0
    assert fits == [("big", "SRF_p"), ("big", "Lp@2"), ("big", "Lp@4"), ("big", "Lp@8"), ("big", "Lp@10")]   # with its resonance
    err = capsys.readouterr().err
    assert "[lib.prefit] big: 4 knot columns (Lp 4): 0 cached, 4 to fit; 3 skipped\n" in err
    assert f"[lib.prefit] big: skipped Lp@38, Lp@60 -- {TOO_FEW.format(0)}\n" in err
    assert f"[lib.prefit] big: skipped Lp@16 -- {TOO_FEW.format(1)}\n" in err
    assert "[lib.prefit] big: fitted 4 (Lp 4), cached 0, skipped 3; " in err

    fits.clear()
    out = prefit.prefit(q.Library(root, limits=LOCAL))                               # every stratum, every curve
    assert [s for s, _c in fits].count("big") == 4 and {c for s, c in fits if s == "big"} == {"Qp@2", "Qp@4", "Qp@8", "Qp@10"}
    assert sorted(c for s, c in fits if s == "tiny") == sorted(f"{c}@{f:g}" for c in ("Lp", "Qp") for f in CANDIDATES)
    assert out["strata"]["big"]["cached"] == ["Lp@2", "Lp@4", "Lp@8", "Lp@10"] and out["strata"]["big"]["skipped"] == BIG_SKIPPED
    assert out["strata"]["tiny"]["knots"] == {"Lp": CANDIDATES, "Qp": CANDIDATES} and out["strata"]["tiny"]["skipped"] == {}

    fits.clear()
    again = prefit.prefit(q.Library(root, limits=LOCAL))
    assert fits == [] and all(s["fitted"] == [] for s in again["strata"].values())
    assert again["strata"]["tiny"]["cached"] == [f"{c}@{f:g}" for c in ("Lp", "Qp") for f in CANDIDATES]
    assert q.Library(root).cached("tiny")["Qp"] == "grid 7/7"


class Interrupted(RuntimeError):
    """Stands for Ctrl-C, or the machine going down, in the middle of a fit."""


def test_an_interrupted_prefit_continues_where_it_stopped(built, tmp_path, monkeypatch):
    root = fresh(built, tmp_path / "lib")
    original = q.Library._fit
    calls: list[str] = []

    def interrupted(self, stratum, quantity):
        calls.append(quantity)
        if len(calls) == 4:
            raise Interrupted
        return original(self, stratum, quantity)

    monkeypatch.setattr(q.Library, "_fit", interrupted)
    with pytest.raises(Interrupted):
        prefit.prefit(q.Library(root, limits=LOCAL), strata=["tiny"])
    assert calls == ["Lp@2", "Lp@4", "Lp@8", "Lp@10"]                                 # three fitted and cached, the fourth cut short
    monkeypatch.setattr(q.Library, "_fit", original)
    fits = fitted(monkeypatch)
    out = prefit.prefit(q.Library(root, limits=LOCAL), strata=["tiny"])
    assert out["strata"]["tiny"]["cached"] == ["Lp@2", "Lp@4", "Lp@8"] and len(fits) == 14 - 3
    assert out["strata"]["tiny"]["fitted"] == [c for c in (f"{c}@{f:g}" for c in ("Lp", "Qp") for f in CANDIDATES)
                                               if c not in ("Lp@2", "Lp@4", "Lp@8")]


def test_prefit_refuses_unknown_strata_and_curves_and_says_when_there_is_no_grid(built, tmp_path, monkeypatch):
    root = fresh(built, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    with pytest.raises(ValueError, match=r"no strata \['nope'\] in .*library.yaml; have \['big', 'tiny'\]"):
        prefit.prefit(lib, strata=["nope"])
    with pytest.raises(ValueError, match=r"curves \['Ls', 'kk'\]: no stratum asked for declares them; the curves they declare: \['Lp', 'Qp'\]"):
        prefit.prefit(lib, curves=["Lp", "Ls", "kk"])
    fits = fitted(monkeypatch)
    plain = prefit.prefit(q.Library(set_grid(fresh(built, tmp_path / "plain"), None), limits=LOCAL), strata=["tiny"], curves=["Qp"])
    assert plain["frequency_grid"] is None and plain["notes"] == ["library.yaml declares no frequency_grid: each curve's knots are its anchors"]
    assert plain["strata"]["tiny"]["knots"] == {"Qp": [10.0]} and fits == [("tiny", "Qp@10")]


def test_prefit_fits_in_spawned_workers_a_few_columns_per_call(built, tmp_path, monkeypatch):
    """Several workers: each call of Library.models gets ROUNDS columns per worker, the workers read the grid's columns from
    the files the parent measured, and the parent keeps none of the knot models in memory afterwards."""
    root = fresh(built, tmp_path / "lib")
    calls: list[list[str]] = []
    original = q.Library.models

    def models(self, stratum, quantities, **caps):
        calls.append(list(quantities))
        return original(self, stratum, quantities, **caps)

    monkeypatch.setattr(q.Library, "models", models)
    lib = q.Library(root, limits=FAKE_HOST)
    out = prefit.prefit(lib, strata=["tiny"], curves=["Lp"], workers=2, threads=2)
    assert [len(c) for c in calls] == [4, 3] and out["strata"]["tiny"]["fitted"] == [f"Lp@{f:g}" for f in CANDIDATES]
    assert not any(stratum == "tiny" and column.startswith("Lp@") for stratum, column in lib._models)
    stems = sorted(p.name.rsplit("-", 1)[0] for p in (root / ".cache").glob("model-*.pkl"))
    assert stems == sorted(f"model-tiny-Lp_at_{f:g}" for f in CANDIDATES)
    assert q.Library(root).cached("tiny")["Lp"] == "grid 7/7"


def test_the_prefit_block_on_the_command_line(built, tmp_path, monkeypatch):
    root = fresh(built, tmp_path / "lib")
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    assert "lib.prefit" in blocks.REGISTRY
    done = CliRunner().invoke(app, ["call", "lib.prefit", str(root), "strata=big", "curves=Lp,Qp", "workers=1", "threads=2"])
    assert done.exit_code == 0, done.output
    answer = json.loads(done.stdout)
    assert answer["strata"]["big"]["fitted"] == [f"{c}@{f:g}" for c in ("Lp", "Qp") for f in BIG_KNOTS]
    assert answer["strata"]["big"]["skipped"] == BIG_SKIPPED and answer["notes"] == []
    assert "[lib.prefit] big: fitted 8 (Lp 4, Qp 4), cached 0, skipped 6; " in done.stderr
    refused = CliRunner().invoke(app, ["call", "lib.prefit", str(root), "workers=5"])
    assert refused.exit_code == 2 and "workers=5 exceeds 1: max_threads 2" in refused.output
    cover = CliRunner().invoke(app, ["call", "lib.coverage", str(root), "stratum=big"])
    assert cover.exit_code == 0 and json.loads(cover.stdout)["cached"]["Qp"] == "grid 4/4"
