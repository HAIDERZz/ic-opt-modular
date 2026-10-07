"""N-91 (``docs/refactor/N91_LIB_REFINE_SPEC.md``): lib_refine -- the library grows around a run's best point, through a fake
EMX.

A transformer library built through the real em_only chain on demo_6m (part ``xfm``: 162 geometries, the windings on M6 /
M5, the ground fixture on ``auto``, swept to 60 GHz); its fake EMX (``fakes.coupled_snp``) answers with a coupled pair whose
L, k, Q and resonance follow the geometry, so a refined candidate measures as the table's own rows do. A circuit spec takes
its transformer from the table at 10 GHz (T18.2B), its variables Lp, Ls and k on grids fine enough that each row has a
combination of its own. The run's points are evaluated with the fake Spectre (NF falls with F); the best one takes the
centre row, (100, 100, 5, 5, 0)."""
from __future__ import annotations

import json
import math
import shlex
import shutil
from pathlib import Path

import pytest
import yaml

from ic_opt import space
from ic_opt.blocks.evaluate import evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.executor import CommandResult
from ic_opt.library import dataset, index, link, manifest, query
from ic_opt.recipe import BUILTIN_RECIPES, PLAN_MODE, Run, load_recipe
from ic_opt.recipes import lib_refine
from ic_opt.site import HostLimits, Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, coupled_snp
from tests.ic_opt.library_device_fixtures import NETLIST, library_device, library_spec_dict
from tests.ic_opt.library_fixtures import LOCAL, XFM_DIMS, use_site, xfm_part_spec
from tests.ic_opt.test_blocks import maestro_export
from tests.ic_opt.test_library import FIXTURE

pytest.importorskip("klayout.db")

STRATUM = "xfm_demo"
STOP_GHZ = 60
F0 = 10e9
CENTRE = dict(zip(XFM_DIMS, (100.0, 100.0, 5.0, 5.0, 0.0), strict=True))
STEPS = {"primary_outer_diameter_um": 1, "secondary_outer_diameter_um": 1, "primary_width_um": 0.5, "secondary_width_um": 0.1,
         "center_spacing_um": 0.5}
QUANTITIES = {"Lp_lf": {}, "Ls_lf": {}, "k_lf": {}, "Qp_peak": {"band_ghz": STOP_GHZ}, "Qs_peak": {"band_ghz": STOP_GHZ}, "SRF": {},
              **{curve: {"anchors_ghz": [10]} for curve in ("Lp", "Ls", "Qp", "Qs", "k")}}
GRID = {"xfmr.Lp": ("300p", "430p", "1p"), "xfmr.Ls": ("300p", "430p", "1p"), "xfmr.k": ("0.68", "0.75", "0.001")}
MODELS = ["Lp@10", "Ls@10", "k@10", "Qp@10", "Qs@10", "SRF"]
SITE = Site({"local": HostLimits(max_threads=16, max_memory_gb=64)})


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """A library without limits reads site.yaml's hosts.local: this file's, never the developer's; and every test resolves
    its library devices afresh."""
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    link.clear()
    yield
    link.clear()


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The library: 3 x 3 primary / secondary diameters, 3 x 3 widths, two centre offsets; each row's emx.log ends with a
    peak memory line (row i: 1 + i/100 GB), as a real EMX log does. Its models of the device's columns, the index at 10 GHz
    and the footprints are computed once here, into the library's .cache, which every copy takes along."""
    root = tmp_path_factory.mktemp("refinelib")
    d = xfm_part_spec("xfm", STOP_GHZ).model_dump(mode="json")
    d["devices"][0]["fixed"].update(ground_fixture={**FIXTURE, "metal": "auto"})
    d["budget"] = {"max_simulations": 1000}
    part = Spec.model_validate(d)
    project = root / "xfm"
    project.mkdir()
    (project / "spec.yaml").write_text(yaml.safe_dump(part.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    points = [Point({k: f"{v:g}" for k, v in zip(XFM_DIMS, (op, os_, wp, ws, cs), strict=True)}, "grid")
              for op in (90, 100, 110) for os_ in (90, 100, 110) for wp in (4.5, 5, 5.5) for ws in (4.5, 5, 5.5) for cs in (0, 5)]
    obs = evaluate(part, points, FakeSpectreExecutor(store.root / "sims", snp_fn=coupled_snp), store, limits=FAKE_HOST, parallel_jobs=8)
    assert {o.status for o in obs} == {"ok"}
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {STRATUM: {"generator": "clean_port_xfm_bs", "dims": XFM_DIMS, "parts": [{"store": "xfm"}], "steps": STEPS,
                                "quantities": QUANTITIES}}}
    (root / manifest.MANIFEST).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    for i, log in enumerate(sorted(root.glob("xfm/.icopt/sims/*/em/xfm/emx.log"))):
        log.write_text(log.read_text() + f"Peak memory usage {1 + i / 100:.2f} GB\nWall-clock time 1.00 sec\n", encoding="utf-8")
    lib = query.Library(root, limits=LOCAL)
    lib.models(STRATUM, MODELS)
    index.build(lib, STRATUM, F0)
    return root


@pytest.fixture
def library(built, tmp_path) -> Path:
    """A copy of the library for one test: the recipe adopts rows into it."""
    return Path(shutil.copytree(built, tmp_path / "lib"))


def nf(params, tb, corner):
    """The fake testbench: NF falls with F."""
    return {"NF": 9.5 - int(params["F"]) / 10}


class Host(FakeSpectreExecutor):
    """The fake host: Spectre answers ``metric``, EMX the coupled pair; EMX fails for a geometry ``fail(config)`` names."""

    def __init__(self, scratch_root: Path, metric=nf, fail=None) -> None:
        super().__init__(scratch_root, metric, snp_fn=coupled_snp)
        self.fail = fail

    def run(self, command, *, cwd=None, timeout_s=None, cshrc=None) -> CommandResult:
        argv = shlex.split(command)
        if argv[0] == "emx" and cwd is not None and self.fail is not None:
            cfg = json.loads((Path(cwd) / "geometry_manifest.json").read_text())["geometry"]["config"]
            if self.fail(cfg):
                self.emx_runs += 1
                return CommandResult(3, "", "emx: license lost", argv, 0.01)
        return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)


def make_run(root: Path, where: Path, *, metric=nf, fail=None, grid=None, budget: int = 200, **library) -> tuple[Run, Host]:
    """The circuit project at ``where``: the library spec (its transformer from ``root`` at 10 GHz, ``library`` its source's
    other fields) with a Maestro export holding the transformer's nport."""
    export = maestro_export(where / "maestro", "tb", params="temperature=27 F=20")
    (export / "netlist" / "input.scs").write_text(NETLIST, encoding="utf-8")
    d = library_spec_dict(root, export=export, grid=grid or GRID, device=library_device(root, frequency_hz=F0, **library),
                          budget={"max_simulations": budget})
    d["simulator"] = {**d["simulator"], "license_check": False}
    spec = Spec.model_validate(d)
    project = where / "proj"
    project.mkdir(parents=True)
    (project / "spec.yaml").write_text(yaml.safe_dump(spec.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    host = Host(store.root / "sims", metric, fail)
    return Run(project, spec, store, host, None, SITE, SITE.host("local")), host


def cell_of(linked: link.Linked, geometry: dict) -> tuple[int, ...]:
    """The combination whose best row has this geometry."""
    (cell,) = [c for c, rows in linked.table.cells.items() if rows[0].params == geometry]
    return cell


def point_at(spec: Spec, linked: link.Linked, cell, f: int) -> Point:
    variables = {v.name: v for v in spec.variables}
    texts = {}
    for name, k in zip(linked.names, cell, strict=True):
        lower, unit = space.parse_scalar(variables[name].lower)
        texts[name] = space.format_value(lower + k * space.parse_scalar(variables[name].step)[0], unit)
    return Point({"F": str(f), **texts}, "user")


OTHER = (dict(zip(XFM_DIMS, (90.0, 90.0, 5.0, 5.0, 0.0), strict=True)), dict(zip(XFM_DIMS, (90.0, 100.0, 4.5, 5.0, 5.0), strict=True)),
         dict(zip(XFM_DIMS, (100.0, 90.0, 5.5, 5.5, 0.0), strict=True)))


def history(run: Run, *, rechecked: bool = False, others: bool = True) -> list:
    """Three points of the run: F=30 on the centre row's combination (the best), F=24 and F=20 on two others (none with
    ``others=False``); with ``rechecked``, a fourth (F=26, a third row) evaluated in the signoff recipe's re-check step."""
    linked = link.resolve(run.spec)["xfmr"]
    deck = import_netlists(run.spec, run.executor, run.store)
    wanted = ((CENTRE, 30), (OTHER[0], 24), (OTHER[1], 20)) if others else ((CENTRE, 30),)
    points = [point_at(run.spec, linked, cell_of(linked, g), f) for g, f in wanted]
    obs = list(evaluate(run.spec, points, run.executor, run.store, deck=deck, step="optimize", limits=run.limits))
    if rechecked:
        obs += evaluate(run.spec, [point_at(run.spec, linked, cell_of(linked, OTHER[2]), 26)], run.executor, run.store, deck=deck,
                        step="signoff", limits=run.limits)
    return obs


def plan(run: Run, **params) -> None:
    token = PLAN_MODE.set(True)
    try:
        load_recipe("lib_refine")(run, **params)
    finally:
        PLAN_MODE.reset(token)


def a_pass(run: Run, root: Path, **params) -> lib_refine.Pass:
    """``plan_device`` for the transformer, as the recipe calls it."""
    best, _, rows = lib_refine.best_point(run.spec, run.store.observations())
    options = {"steps": 1, "n": 4, "prefer": None, "threads": None, "memory_gb": None, "process_file": None, **params}
    return lib_refine.plan_device(run, run.spec.devices[0], query.Library(root, limits=LOCAL), best, rows, **options)


# 1. the best point and its row


def test_the_best_point_is_found_a_rechecked_one_first_and_refused_without_one(library, tmp_path):
    """The best feasible point over this problem's steps and its row as its device child recorded it; a point the signoff
    recipe re-checked at every corner comes first, though another has the better objective; no feasible point: refused,
    with how many points there are, before anything is drawn or simulated."""
    run, _ = make_run(library, tmp_path / "a")
    obs = history(run)
    best, checked, rows = lib_refine.best_point(run.spec, run.store.observations())
    assert (best.obs_id, checked, len(rows)) == (obs[0].obs_id, False, 3) and best.params["F"] == "30"
    row = lib_refine.row_of(best, "xfmr")
    assert (row["stratum"], row["part"]) == (STRATUM, "xfm") and row["geometry"] == CENTRE and row["values"]["Lp"] > 0
    run, _ = make_run(library, tmp_path / "b")
    obs = history(run, rechecked=True)
    best, checked, rows = lib_refine.best_point(run.spec, run.store.observations())
    assert (best.obs_id, best.step, checked, len(rows)) == (obs[3].obs_id, "signoff", True, 4)     # F=26 re-checked, F=30 not
    assert lib_refine.row_of(best, "xfmr")["geometry"] == OTHER[2]
    run, host = make_run(library, tmp_path / "c", metric=lambda params, tb, corner: {"NF": 9.5})      # NF < 9 dB: never met
    history(run)
    with pytest.raises(lib_refine.RefineError, match="no feasible point among the 3 points of this problem"):
        load_recipe("lib_refine")(run)
    assert host.emx_runs == 0


# 2. the local grid


def test_the_local_grid_leaves_out_the_row_the_table_and_other_turns_levels():
    """Over (od, w) with steps 1 and 0.1, turns held whatever its step: steps=1 gives 3^2 - 1 geometries around the row, the
    one the table holds left out; steps=2 gives 5^2 - 1; another turns level never appears."""
    rule = manifest.Stratum(generator="clean_port_ind_sym", dims=["od", "w", "turns"], nt_dim="turns", parts=[{"store": "p"}],
                            quantities={"Lp_lf": {}}, steps={"od": 1, "w": 0.1, "turns": 1})

    def row(od, w, turns):
        return dataset.Row("p", f"o{od}{w}{turns}", {"od": od, "w": w, "turns": turns}, {}, 6e10, 61, 2, 1.0, None, "x")

    ds = dataset.Dataset("ind", ["od", "w", "turns"], "turns", ["Lp_lf"], [row(100, 5.0, 2), row(101, 5.1, 2), row(100, 5.0, 3)], {})
    one = lib_refine.local_grid(ds, rule, {"od": 100.0, "w": 5.0, "turns": 2.0}, 1)
    assert (one["size"], one["in_table"], len(one["neighbours"])) == (8, 1, 7) and one["held"] == ["turns"]
    geometries = {(nb.params["od"], nb.params["w"]) for nb in one["neighbours"]}
    assert geometries == {(99, 4.9), (99, 5.0), (99, 5.1), (100, 4.9), (100, 5.1), (101, 4.9), (101, 5.0)}
    assert {nb.params["turns"] for nb in one["neighbours"]} == {2.0}
    two = lib_refine.local_grid(ds, rule, {"od": 100.0, "w": 5.0, "turns": 2.0}, 2)
    assert (two["size"], two["in_table"], len(two["neighbours"])) == (24, 1, 23)
    assert {nb.params["w"] for nb in two["neighbours"]} == {4.8, 4.9, 5.0, 5.1, 5.2}          # exact decimals, not 4.8999...
    no_w = lib_refine.local_grid(ds, rule.model_copy(update={"steps": {"od": 1}}), {"od": 100.0, "w": 5.0, "turns": 2.0}, 1)
    assert no_w["held"] == ["w", "turns"] and sorted(nb.params["od"] for nb in no_w["neighbours"]) == [99, 101]   # 101: no row there
    with pytest.raises(lib_refine.RefineError, match="declares no steps"):
        lib_refine.local_grid(ds, rule.model_copy(update={"steps": {"turns": 1}}), {"od": 100.0, "w": 5.0, "turns": 2.0}, 1)


def test_the_local_grid_of_the_table(built):
    lib = query.Library(built, limits=LOCAL)
    ds, rule = lib.dataset(STRATUM), lib.manifest.strata[STRATUM]
    one = lib_refine.local_grid(ds, rule, CENTRE, 1)
    assert (one["size"], one["in_table"], len(one["neighbours"])) == (242, 2, 240)     # primary width +-0.5: two rows of the table
    assert all(ds.find(nb.params) is None for nb in one["neighbours"])
    assert lib_refine.local_grid(ds, rule, CENTRE, 2)["size"] == 5**5 - 1


# 3. the window


def test_the_window_rules_in_their_order():
    axes = {"Lp": index.Axis.of("Lp", 3e-10, 4e-10, 1e-12), "k": index.Axis.of("k", 0.6, 0.8, 0.01)}
    ok = {"Lp": {"status": "predicted", "value": 3.5e-10}, "k": {"status": "predicted", "value": 0.7}}
    srf = {"status": "predicted", "value": 5e10}
    assert lib_refine.verdict(ok, srf, axes, 4e10) == (None, "")
    assert lib_refine.verdict(ok, {"status": "above_sweep", "value": None}, axes, 4e10) == (None, "")
    assert lib_refine.verdict(ok, None, axes, 4e10) == (None, "")                       # no SRF declared: no margin rule
    assert lib_refine.verdict(ok, srf, axes, 5e10)[0] == "srf_margin"                    # at the margin is not above it
    assert lib_refine.verdict({**ok, "k": {"status": "predicted", "value": 0.806}}, srf, axes, 4e10)[0] == "outside_range"
    assert lib_refine.verdict({**ok, "k": {"status": "predicted", "value": 0.804}}, srf, axes, 4e10)[0] is None   # the end level's half
    assert lib_refine.verdict({**ok, "Lp": {"status": "uncertain", "value": 3.5e-10}}, srf, axes, 4e10)[0] == "uncertain"
    assert lib_refine.verdict(ok, {"status": "uncertain", "value": 5e10}, axes, 4e10)[0] == "uncertain"
    out = {"status": "out_of_domain", "reason": "center_spacing_um=-0.5 outside the measured range [0, 5]"}
    assert lib_refine.verdict({**ok, "Lp": {"status": "uncertain", "value": 1.0}, "k": out}, srf, axes, 4e10) == (
        "out_of_domain", "k: center_spacing_um=-0.5 outside the measured range [0, 5]")
    assert lib_refine.verdict({**ok, "k": {"status": "predicted", "value": 0.9}}, {"status": "out_of_domain"}, axes, 9e10)[0] == "out_of_domain"


def test_the_window_drops_and_counts_and_the_kept_ones_rank_by_prefer(library, tmp_path, capsys):
    """Out of domain: the 81 neighbours with a centre offset of -0.5 (below the table's 0). Outside a range: the primary's
    inductance is held at the centre row's level, so a larger primary goes beyond it. Below the SRF margin: the margin sits
    0.2 % under the centre row's SRF, so the neighbours that resonate lower go. Each dropped neighbour's prediction shows
    its rule; the kept ones rank by Qmin, the largest first, and the candidates are the first clean ones."""
    probe, _ = make_run(library, tmp_path / "probe")
    row = next(r for r in link.resolve(probe.spec)["xfmr"].index.rows if r.params == CENTRE)
    link.clear()
    lp = f"{round(row.values['Lp'] * 1e12)}p"
    margin = 0.998 * row.values["SRF"] / F0
    run, _ = make_run(library, tmp_path / "run", grid={**GRID, "xfmr.Lp": ("300p", lp, "1p")}, srf_margin=margin)
    history(run)
    p = a_pass(run, library)
    dropped = {r: [nb for nb in p.neighbours if nb.verdict == r] for r in lib_refine.RULES}
    assert len(dropped["out_of_domain"]) == 81 and all(nb.params["center_spacing_um"] == -0.5 for nb in dropped["out_of_domain"])
    assert dropped["outside_range"] and dropped["srf_margin"] and not dropped["uncertain"]
    axis = p.linked.table.axes[0]
    assert all(axis.level(nb.predicted["Lp"]["value"]) is None for nb in dropped["outside_range"])
    assert all(nb.srf["value"] <= p.margin_hz for nb in dropped["srf_margin"])
    assert all(nb.srf["value"] > p.margin_hz and axis.level(nb.predicted["Lp"]["value"]) is not None for nb in p.ranked)
    assert p.margin_hz == pytest.approx(margin * F0) and len(p.ranked) == 240 - sum(len(v) for v in dropped.values())
    qmin = [nb.predicted["Qmin"]["value"] for nb in p.ranked]
    assert qmin == sorted(qmin, reverse=True) and p.prefer == ("max", "Qmin")
    assert [nb.label for nb in p.chosen] == ["c1", "c2", "c3", "c4"] and all(nb.preflight == "clean" for nb in p.chosen)
    out = capsys.readouterr().out
    assert (f"of 240 neighbours, out_of_domain 81, uncertain 0, outside_range {len(dropped['outside_range'])} "
            f"(xfmr.Lp {len(dropped['outside_range'])}), srf_margin {len(dropped['srf_margin'])}; {len(p.ranked)} kept") in out
    by_qp = a_pass(run, library, prefer="max:Qp")
    qp = [nb.predicted["Qp"]["value"] for nb in by_qp.ranked]
    assert qp == sorted(qp, reverse=True) and by_qp.prefer == ("max", "Qp")
    with pytest.raises(ValueError, match="prefer 'max:L': expected max:<column> or min:<column> over the index's columns"):
        a_pass(run, library, prefer="max:L")


def test_min_area_draws_the_kept_neighbours_and_ranks_them(library, tmp_path):
    """``prefer=min:area``: the footprint of each neighbour the window keeps is drawn (no EMX), and the smallest ranks first;
    a dropped neighbour is not drawn. The primary's inductance held at the centre row's level keeps the drawing short."""
    probe, _ = make_run(library, tmp_path / "probe")
    lp = round(next(r for r in link.resolve(probe.spec)["xfmr"].index.rows if r.params == CENTRE).values["Lp"] * 1e12)
    link.clear()
    run, _ = make_run(library, tmp_path / "run", grid={**GRID, "xfmr.Lp": (f"{lp}p", f"{lp}p", "1p")})
    history(run, others=False)
    p = a_pass(run, library, prefer="min:area", n=2)
    assert p.ranked and all(nb.predicted["area"]["status"] == "drawn" for nb in p.ranked)
    assert all("area" not in nb.predicted for nb in p.neighbours if nb.verdict is not None)
    areas = [nb.predicted["area"]["value"] for nb in p.ranked]
    assert areas == sorted(areas) and p.prefer == ("min", "area") and len(p.chosen) == 2


def test_a_refused_candidate_is_replaced_by_the_next_in_rank(library, tmp_path, monkeypatch, capsys):
    """The preflight refuses c1 and c3 (a DRC finding, injected): n=3 takes c2, then c4 and c5; c6 is never drawn."""
    from ic_opt.recipes.lib_tap import Outcome

    real = lib_refine.preflight

    def refusing(twins, workers):
        return [Outcome(o.part, o.row, "drc", ("[min_space] M4 x2",)) if o.row in ("c1", "c3") else o for o in real(twins, workers)]

    monkeypatch.setattr(lib_refine, "preflight", refusing)
    run, _ = make_run(library, tmp_path)
    history(run)
    p = a_pass(run, library, n=3)
    assert [nb.label for nb in p.chosen] == ["c2", "c4", "c5"]
    assert [nb.preflight for nb in p.ranked[:6]] == ["drc", "clean", "drc", "clean", "clean", None]
    out = capsys.readouterr().out
    assert "preflight (generator and DRC gate, no EMX) of 5: 3 clean, 0 refused by the generator, 2 by the DRC gate" in out
    assert "xfmr: c1 refused by the DRC gate: [min_space] M4 x2" in out and "xfmr: c3 refused by the DRC gate" in out


def test_a_second_pass_on_the_same_table_leaves_out_the_first_one_s_candidates(library, tmp_path, capsys):
    """Two devices on one table: the second pass skips the geometries the first one chose, so none is simulated or adopted
    twice; it takes the next ones in its rank instead."""
    run, _ = make_run(library, tmp_path)
    history(run)
    first = a_pass(run, library, n=2)
    second = a_pass(run, library, n=2, taken={(first.part, nb.point.key) for nb in first.chosen})
    assert [nb.params for nb in second.chosen] == [nb.params for nb in first.ranked[2:4]]
    assert "2 kept neighbour(s) are candidates of another device's pass already (the same table): left out here" in capsys.readouterr().out


def test_a_column_too_uncertain_drops_its_neighbours(library, tmp_path):
    """k's confidence ceiling at 1e-9 (library.yaml): every neighbour inside the domain is dropped as uncertain."""
    doc = yaml.safe_load((library / manifest.MANIFEST).read_text())
    doc["strata"][STRATUM]["quantities"]["k"]["rel_sigma_max"] = 1e-9
    (library / manifest.MANIFEST).write_text(yaml.safe_dump(doc, sort_keys=False))
    run, _ = make_run(library, tmp_path)
    history(run)
    p = a_pass(run, library)
    counts = {r: sum(nb.verdict == r for nb in p.neighbours) for r in lib_refine.RULES}
    assert counts == {"out_of_domain": 81, "uncertain": 159, "outside_range": 0, "srf_margin": 0} and p.chosen == []
    assert {nb.detail for nb in p.neighbours if nb.verdict == "uncertain"} == {"k: uncertain"}


# 4. the plan and the run


def test_the_plan_runs_no_emx_and_the_run_measures_adopts_and_says_what_next(library, tmp_path, capsys):
    """``--plan``: the best point, the row, the grid, the rules' counts, the candidates with their predictions and
    combinations, the envelope, the part's rows' own peak memory and the budget; no EMX, no report. The run: EMX for the
    candidates (the one with a 101 um primary fails), predicted against measured, the ok ones adopted into part xfm with
    the refine origin, the dataset grown by them, the last line the command that continues the run; library.yaml as it
    was."""
    before = (library / manifest.MANIFEST).read_bytes()
    run, host = make_run(library, tmp_path, fail=lambda cfg: float(cfg["primary_outer_diameter_um"]) == 101)
    obs = history(run)
    plan(run, n=4)
    out = capsys.readouterr().out
    assert host.emx_runs == 0 and len(run.store.observations()) == 3 and not (run.store.root / "reports" / "lib_refine.json").exists()
    assert f"[plan] lib_refine: the best point {obs[0].obs_id} of the 3 points of this problem (step optimize, objective 6.5)" in out
    assert "[plan] lib_refine: xfmr: its row xfm/" in out and "of xfm_demo: primary_outer_diameter_um=100 " in out
    assert ("xfmr: the local grid: 242 geometries within 1 step(s) of the row over primary_outer_diameter_um (1), "
            "secondary_outer_diameter_um (1), primary_width_um (0.5), secondary_width_um (0.1), center_spacing_um (0.5) "
            "(library.yaml steps); 2 of them rows of the table already, 240 neighbours") in out
    assert "xfmr: predicted at 10 GHz (Lp -> xfmr.Lp, Ls -> xfmr.Ls, k -> xfmr.k; SRF above 1.25 x 10 GHz): of 240 neighbours" in out
    assert "preflight (generator and DRC gate, no EMX) of 4: 4 clean, 0 refused by the generator, 0 by the DRC gate" in out
    assert all(f"xfmr: c{i} " in out for i in range(1, 5)) and "on a combination the table does not hold: xfmr.Lp=" in out
    assert ("EMX with part xfm's spec: 1 thread(s) and a 4 GB memory cap per job, up to 2 jobs at once; the part's rows' own "
            "EMX peak memory (from their emx.log, 162 of 162): median 1.81 GB, max 2.61 GB") in out
    assert "budget: 3 of 200 simulations used in proj; this run needs up to 8 more" in out
    assert "[plan] sim.evaluate step='lib_refine:xfm': 4 points" in out
    rows_before = len(query.Library(library, limits=LOCAL).dataset(STRATUM).rows)
    load_recipe("lib_refine")(run, n=4)
    out = capsys.readouterr().out
    report = json.loads((run.store.root / "reports" / "lib_refine.json").read_text())
    device = report["devices"]["xfmr"]
    assert report["best"]["obs_id"] == obs[0].obs_id and report["best"]["rechecked"] is False
    assert device["grid"] == {"varied": STEPS, "held": [], "size": 242, "in_table": 2, "neighbours": 240,
                              "dropped": device["grid"]["dropped"], "kept": device["grid"]["kept"]}
    assert device["grid"]["dropped"]["out_of_domain"] == 81 and len(device["candidates"]) == 4
    points = device["points"]
    failed = [e for e in points if e["status"] != "ok"]
    ok = [e for e in points if e["status"] == "ok"]
    assert host.emx_runs == 4 and len(points) == 4
    assert all(e["params"]["primary_outer_diameter_um"] == 101 and "adopted" not in e and e["status"] == "failed:emx:xfm" for e in failed)
    assert failed and len(ok) == 4 - len(failed)
    for e in ok:
        lp = e["quantities"]["Lp@10"]
        assert lp["status"] == "predicted" and lp["measured"] == pytest.approx(lp["predicted"], rel=2e-2)
        assert lp["inside"] == (lp["lo"] <= lp["measured"] <= lp["hi"]) and (lp["z"] > 0) == (lp["measured"] > lp["predicted"])
        assert set(e["quantities"]) == set(MODELS) and e["ratios"]["Lp"] == e["values"]["Lp"] / device["row"]["values"]["Lp"]
        assert e["in_index"] and e["adopted"].startswith("obs_")
    adopted = device["adopted"]
    assert adopted["part"] == "xfm" and adopted["ids"] == [e["adopted"] for e in ok]
    assert adopted["origin"] == f"refine:proj:{obs[0].obs_id}"
    rows = {o.obs_id: o for o in RunStore(library / "xfm").observations()}
    assert {rows[i].origin for i in adopted["ids"]} == {adopted["origin"]} and all(rows[i].status == "ok" for i in adopted["ids"])
    assert (library / "xfm" / ".icopt" / "adopted.yaml").is_file()
    assert device["dataset"] == {"rows_before": rows_before, "rows": rows_before + len(ok), "excluded": {}}
    assert (library / manifest.MANIFEST).read_bytes() == before
    assert (f"lib_refine: xfmr: 4 candidates, {len(ok)} ok, {len(ok)} adopted; max:Qmin the row "
            f"{device['row']['values']['Qmin']:.4g}, the best adopted ") in out
    assert f"xfm_demo's dataset {rows_before} -> {rows_before + len(ok)} rows" in out
    last = out.strip().splitlines()[-1]
    assert last.startswith("[run] lib_refine: next: the grown table is a new generation for the next process")
    assert f"ic-opt run optimize {run.store.project_dir} budget={3 + len(ok)} with the parameters it ran with" in last


# 5. the next generation


def test_after_adoption_a_new_library_and_the_device_s_index_hold_the_rows(library, tmp_path):
    run, _ = make_run(library, tmp_path)
    history(run)
    identity = link.identity(run.spec)
    load_recipe("lib_refine")(run, n=2)
    adopted = json.loads((run.store.root / "reports" / "lib_refine.json").read_text())["devices"]["xfmr"]["adopted"]["ids"]
    assert len(adopted) == 2
    ds = query.Library(library, limits=LOCAL).dataset(STRATUM)
    assert {("xfm", i) for i in adopted} <= {(r.part, r.obs_id) for r in ds.rows}
    link.clear()                                                  # the next process
    linked = link.resolve(run.spec)["xfmr"]
    assert {("xfm", i) for i in adopted} <= {(r.part, r.obs_id) for r in linked.index.rows}
    assert link.identity(run.spec) != identity
    held = {(r.part, r.obs_id) for rows in linked.table.cells.values() for r in rows}
    assert {("xfm", i) for i in adopted} <= held                   # on the grid: candidates of the next batch


# refusals and the pieces


def test_calls_that_cannot_be_carried_out_are_refused(library, tmp_path):
    run, host = make_run(library, tmp_path, budget=4)
    history(run)
    main = load_recipe("lib_refine")
    for params, message in (({"device": "ind"}, r"device=ind: no library device of that id; the spec's library devices: \['xfmr'\]"),
                            ({"steps": 0}, "steps must be a whole number of at least 1, got 0"),
                            ({"n": "8"}, "n must be a whole number of at least 1"),
                            ({"n": 2}, r"budget: 3 of 4 simulations used in .*, and this run needs up to 4 more")):
        with pytest.raises(lib_refine.RefineError, match=message):
            main(run, **params)
    assert host.emx_runs == 0 and not run.store.observations().by_step("lib_refine:xfm")
    d = run.spec.model_dump(mode="json")
    d["devices"] = []
    d["bindings"] = []
    d["variables"] = d["variables"][:1]
    d["metrics"] = d["metrics"][:1]
    bare = Run(run.project, Spec.model_validate(d), run.store, run.executor, None, SITE, SITE.host("local"))
    with pytest.raises(lib_refine.RefineError, match="its spec has no library device"):
        main(bare)


def test_the_continuation_follows_the_recipe_that_ran(library, tmp_path):
    from ic_opt.observation import Observation

    run, _ = make_run(library, tmp_path)

    def o(step: str) -> Observation:
        return Observation(obs_id="obs_0001", params={}, origin="x", status="ok", spec_fingerprint="s", pipeline_fingerprint="p",
                           step=step, started_at="", finished_at="")

    rows = [o("search@ss"), o("search@ss"), o("signoff"), o("coarse"), o("fine"), o("fine"), o("optimize")]
    project = run.store.project_dir
    assert lib_refine._continuation(run, rows, o("signoff"), 3) == f"ic-opt run signoff {project} corner=ss budget=5"
    assert lib_refine._continuation(run, rows, o("search@ss#2"), 3) == f"ic-opt run signoff {project} corner=ss budget=5"
    assert lib_refine._continuation(run, rows, o("fine"), 2) == f"ic-opt run coarse_to_fine {project} coarse_budget=1 fine_budget=4"
    assert lib_refine._continuation(run, rows, o("optimize"), 0) == f"ic-opt run optimize {project} budget=2"


def test_lib_refine_is_a_built_in_recipe():
    assert "lib_refine" in BUILTIN_RECIPES and load_recipe("lib_refine") is lib_refine.main
    assert math.isclose(lib_refine.K, 2.0)
