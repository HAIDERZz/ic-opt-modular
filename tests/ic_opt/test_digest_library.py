"""T18.2B section 5: what a reader sees of a library device -- the digest's ``library`` entry (version 4: the table, the
frequency, the rule, the combinations on the grid and how many the run visited; the rows the best points took) and its
section in ``digest.md``, and the report's "Best observed" naming the row, its geometry and its footprint."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ic_opt import digest as dg
from ic_opt import recipe
from ic_opt.blocks import analyze
from ic_opt.library import link
from ic_opt.recipes import optimize
from ic_opt.site import HostLimits, Site
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, make_spec
from tests.ic_opt.library_device_fixtures import NETLIST, library_spec_dict, xfm_library
from tests.ic_opt.test_blocks import maestro_export

SITE = Site({"local": HostLimits(max_threads=16, max_memory_gb=64)})


@pytest.fixture(autouse=True)
def fresh():
    link.clear()
    yield
    link.clear()


def library_run(root: Path, where: Path, **overrides) -> recipe.Run:
    export = maestro_export(where / "maestro", "tb", params="temperature=27 F=20")
    (export / "netlist" / "input.scs").write_text(NETLIST, encoding="utf-8")
    d = library_spec_dict(root, export=export, **overrides)
    d["simulator"] = {**d["simulator"], "license_check": False}
    spec = Spec.model_validate(d)
    store = RunStore(where / "proj")
    executor = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 9.5 - int(p["F"]) / 10})
    return recipe.Run(store.root, spec, store, executor, None, SITE, SITE.host("local"))


def test_the_digest_names_each_table_what_the_run_visited_and_the_rows_the_best_points_took(tmp_path):
    root = xfm_library(tmp_path / "lib")
    run = library_run(root, tmp_path)
    optimize.main(run, budget=12, batch=4, seed=1)
    obs = run.store.observations()
    spec = run.spec
    path = analyze.digest(spec, obs, run.store)
    d = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    facts = link.summary(spec)["xfmr"]
    names = ("xfmr.Lp", "xfmr.Ls", "xfmr.k")
    visited = {tuple(o.params[n] for n in names) for o in obs}
    assert d["digest_version"] == 5 and d["library"]["notes"] == {}
    assert d["library"]["devices"] == [{"device": "xfmr", "table": "xfm_demo", "frequency_hz": 2e10, "srf_margin": 1.25,
                                        "prefer": "max:Qmin", "variables": {"Lp": "xfmr.Lp", "Ls": "xfmr.Ls", "k": "xfmr.k"},
                                        "combinations": facts["combinations"], "rows": facts["rows"], "of": 1536,
                                        "visited": len(visited)}]
    feasible = sorted((o for o in obs if o.feasible), key=lambda o: o.objective)
    top = d["library"]["top"]
    assert [p["id"] for p in top] == [o.obs_id for o in feasible[:5]] and top[0]["id"] == d["progress"]["best"]["id"]
    for point, o in zip(top, feasible, strict=False):
        row = o.children["xfmr/nominal"].library_row
        assert point["rows"] == {"xfmr": {k: row[k] for k in ("part", "obs_id", "geometry", "values", "footprint")}}
        assert point["objective"] == pytest.approx(o.fom)
    md = path.read_text(encoding="utf-8")
    section = md.split("## Library devices\n\n")[1].split("\n## ")[0]
    assert (f"| xfmr | xfm_demo | 20 GHz | max:Qmin | {facts['combinations']} of 1536 ({facts['rows']} rows) | "
            f"{len(visited)} |") in section
    best = feasible[0].children["xfmr/nominal"].library_row
    line = next(line for line in section.splitlines() if line.startswith(f"| `{feasible[0].obs_id}` |"))
    assert f"| xfmr | {best['part']}/{best['obs_id']} | primary_outer_diameter_um={best['geometry']['primary_outer_diameter_um']:g}" in line
    assert f"Lp={dg.quantity(best['values']['Lp'], 'H')}" in line and "Qmin=" in line and line.endswith("| — |")
    report = analyze.report(spec, obs, run.store).read_text(encoding="utf-8")
    observed = report.split("## Best observed\n\n")[1].split("\n## ")[0]
    assert (f"- device xfmr: library row {best['part']}/{best['obs_id']} of xfm_demo, geometry "
            f"primary_outer_diameter_um={best['geometry']['primary_outer_diameter_um']:g}") in observed
    assert ", footprint —" in observed                                                   # the fixture's rows keep no GDS
    plain = dg.digest(make_spec(), [])
    assert plain["library"] is None and "## Library devices" not in dg.markdown(plain)


def test_a_library_that_cannot_be_read_here_leaves_the_table_s_size_out_and_no_path(tmp_path):
    root = xfm_library(tmp_path / "lib")
    run = library_run(root, tmp_path)
    optimize.main(run, budget=8, batch=4, seed=2)
    obs = run.store.observations()
    moved = tmp_path / "elsewhere"
    shutil.move(str(root), moved)                                  # the digest is read on a machine without the library
    link.clear()
    d = dg.digest(run.spec, obs)
    (device,) = d["library"]["devices"]
    assert (device["combinations"], device["rows"], device["of"], device["prefer"]) == (None, None, None, None)
    assert d["library"]["notes"]["combinations"] == dg.LIBRARY_UNREAD and len(d["library"]["top"]) >= 1
    assert d["strategy"]["metric_gp"] is None and "no search region" in d["strategy"]["notes"]["metric_gp"]
    text = json.dumps(d) + dg.markdown(d)
    assert str(root) not in text and str(tmp_path) not in text and ".s4p" not in text


def test_the_per_point_table_names_the_row_each_point_took(tmp_path):
    """N-97 (F7): ``reports/points.md`` gives per library device the row the point took, by part and obs id -- the fake
    library of ``test_library_device_run`` -- and no path."""
    root = xfm_library(tmp_path / "lib")
    run = library_run(root, tmp_path)
    optimize.main(run, budget=8, batch=4, seed=2)
    obs = run.store.observations()
    analyze.digest(run.spec, obs, run.store, points=True)
    table = (run.store.reports_dir() / "points.md").read_text(encoding="utf-8")
    assert "| obs | step | origin | status | objective | NF | Lp | k | Qp | xfmr row |" in table
    assert "Per library device, the row the point took (part/obs id)." in table
    rows = [line for line in table.splitlines() if line.startswith("| `obs_")]
    assert len(rows) == len(obs) == 8
    for line, o in zip(rows, obs, strict=True):
        row = o.children["xfmr/nominal"].library_row
        assert line.startswith(f"| `{o.obs_id}` |") and line.endswith(f"| {row['part']}/{row['obs_id']} |")
    assert str(root) not in table and str(tmp_path) not in table and ".s4p" not in table
    assert "(8 rows, written with this digest)" in (run.store.reports_dir() / "digest.md").read_text(encoding="utf-8")
