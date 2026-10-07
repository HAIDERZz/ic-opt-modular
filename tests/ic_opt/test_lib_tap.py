"""T19.5 (``docs/refactor/T19_5_TAP_TWIN_RECIPE_SPEC.md``): lib_tap -- tapped twins of a table's rows, through a fake EMX.

A small transformer library built through the real em_only chain on demo_6m (the windings on M6 / M5, the ground fixture
on ``auto``, which lands on M4): part ``xfm`` with 8 um openings and part ``xfm_narrow`` with a 1.55 um secondary opening,
where a 3 um same-metal primary tap's stub comes 0.05 um from the secondary's two stubs (M4's min space is 0.1 um). The
fake EMX (``fakes.coupled_snp``) answers with a coupled pair whose L and k follow the geometry and whose tap costs 3 % of
resistance.

T19.6 (``docs/refactor/T19_6_MS_SAME_METAL_TAP_SPEC.md``): lib_tap on an xfm_ms table, a second small library on demo_6m
(the single-turn primary on M6, the multi-turn secondary on M5, its crossunder on M4): part ``ms`` with the ground fixture
on ``auto``, which lands on M3, and part ``ms_m1`` with the default fixture, on the bottom metal."""
from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

import pytest
import yaml

from ic_opt.blocks import library as library_blocks
from ic_opt.blocks.evaluate import evaluate
from ic_opt.em.pcell import footprint as fp
from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.executor import CommandResult
from ic_opt.library import dataset, index, manifest, query
from ic_opt.recipe import BUILTIN_RECIPES, PLAN_MODE, Run, load_recipe
from ic_opt.recipes import lib_tap
from ic_opt.site import HostLimits, Site
from ic_opt.space import Point
from ic_opt.spec import Spec, load_spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, TAP_LOSS, FakeSpectreExecutor, coupled_snp
from tests.ic_opt.library_fixtures import LOCAL, XFM_DIMS, build_library, use_site, xfm_part_spec
from tests.ic_opt.test_library import FIXTURE

pytest.importorskip("klayout.db")

STOP_GHZ = 40
STRATUM = "xfm_demo"
WIDE = [(80, 80, 5, 5, 0), (100, 100, 5, 5, 0), (120, 120, 5, 5, 0), (100, 80, 5, 5, 0), (100, 100, 6, 6, 10), (120, 100, 5, 5, 10)]
NARROW = [(60, 60, 4, 4, 0), (70, 70, 4, 4, 0)]
SAME = {"primary": "same", "secondary": "same", "primary_width_um": 3, "secondary_width_um": 3, "measure": "grounded"}
QUANTITIES = {"Lp_lf": {}, "Ls_lf": {}, "k_lf": {}, "Qp_peak": {"band_ghz": STOP_GHZ}, "Qs_peak": {"band_ghz": STOP_GHZ}, "SRF": {},
              **{curve: {"anchors_ghz": [10]} for curve in ("Lp", "Ls", "Qp", "Qs", "k")}}
STEPS = {"primary_outer_diameter_um": 1, "secondary_outer_diameter_um": 1, "primary_width_um": 0.1, "secondary_width_um": 0.1,
         "center_spacing_um": 0.5}


@pytest.fixture(autouse=True)
def site_file(tmp_path, monkeypatch):
    """The library computes within site.yaml's hosts.local when it is given no limits: this file's, never the developer's."""
    return use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)


def part_spec(project: str, secondary_opening_um: float = 8.0) -> Spec:
    """An untapped xfm_bs part on M6 / M5, its ground fixture on ``auto``, swept 0-40 GHz."""
    d = xfm_part_spec(project, STOP_GHZ).model_dump(mode="json")
    d["devices"][0]["fixed"].update(secondary_opening_um=secondary_opening_um, ground_fixture={**FIXTURE, "metal": "auto"})
    d["budget"] = {"max_simulations": 1000}
    return Spec.model_validate(d)


def build_part(root: Path, name: str, points, **spec) -> None:
    project = root / name
    project.mkdir(parents=True)
    part = part_spec(name, **spec)
    (project / "spec.yaml").write_text(yaml.safe_dump(part.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    executor = FakeSpectreExecutor(store.root / "sims", snp_fn=coupled_snp)
    obs = evaluate(part, [Point({d: f"{v:g}" for d, v in zip(XFM_DIMS, p, strict=True)}, "grid") for p in points], executor, store,
                   limits=FAKE_HOST)
    assert [o.status for o in obs] == ["ok"] * len(points)


def write_manifest(root: Path) -> None:
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {STRATUM: {"generator": "clean_port_xfm_bs", "dims": XFM_DIMS, "parts": [{"store": "xfm"}, {"store": "xfm_narrow"}],
                                "steps": STEPS, "quantities": QUANTITIES}}}
    (root / manifest.MANIFEST).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The library, its rows' emx.log each ending with a peak memory line (row i: 1 + i/4 GB), as a real EMX log does."""
    root = tmp_path_factory.mktemp("taplib")
    build_part(root, "xfm", WIDE)
    build_part(root, "xfm_narrow", NARROW, secondary_opening_um=1.55)
    write_manifest(root)
    for i, log in enumerate(sorted(root.glob("*/.icopt/sims/*/em/xfm/emx.log"))):
        log.write_text(log.read_text() + f"Peak memory usage {1 + i / 4:.2f} GB\nWall-clock time 1.00 sec\n", encoding="utf-8")
    return root


@pytest.fixture
def library(built, tmp_path) -> Path:
    """A copy of the library for one test: the recipe may add part stores and a stratum to it."""
    return Path(shutil.copytree(built, tmp_path / "lib"))


class Host(FakeSpectreExecutor):
    """The fake host; its EMX fails for the twins whose primary outer diameter is ``fail_od``."""

    def __init__(self, scratch_root: Path, fail_od: float | None = None) -> None:
        super().__init__(scratch_root, snp_fn=coupled_snp)
        self.fail_od = fail_od

    def run(self, command, *, cwd=None, timeout_s=None, cshrc=None) -> CommandResult:
        argv = shlex.split(command)
        if argv[0] == "emx" and cwd is not None and self.fail_od is not None:
            cfg = json.loads((Path(cwd) / "geometry_manifest.json").read_text())["geometry"]["config"]
            if float(cfg["primary_outer_diameter_um"]) == self.fail_od:
                self.emx_runs += 1
                return CommandResult(3, "", "emx: license lost", argv, 0.01)
        return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)


def make_run(tmp_path: Path, name: str = "tapping", fail_od: float | None = None) -> tuple[Run, Host]:
    project = tmp_path / name
    project.mkdir()
    (project / "spec.yaml").write_text(yaml.safe_dump(part_spec(name).model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    executor = Host(store.root / "sims", fail_od)
    site = Site({"local": HostLimits(max_threads=8, max_memory_gb=32)})
    return Run(project, load_spec(project / "spec.yaml"), store, executor, None, site, site.host("local")), executor


def plan(main, run: Run, **params) -> None:
    token = PLAN_MODE.set(True)
    try:
        main(run, **params)
    finally:
        PLAN_MODE.reset(token)


def window(frequency_ghz=10, **ranges) -> dict:
    return {"frequency_ghz": frequency_ghz, "ranges": ranges or {"Lp": [1e-12, 1e-6]}}


def report(run: Run) -> dict:
    return json.loads((run.store.root / "reports" / "lib_tap.json").read_text(encoding="utf-8"))


# 1. which rows


def test_a_window_selects_exactly_the_index_rows_inside_its_ranges(built):
    """The rows of the stratum's index at the window's frequency whose values lie inside every range, inclusive -- here
    the bounds are two rows' own values, which stay in."""
    lib = query.Library(built, limits=LOCAL)
    rows = index.build(lib, STRATUM, 10e9).rows
    lp = sorted(r.values["Lp"] for r in rows)
    k = sorted(r.values["k"] for r in rows)
    win = lib_tap.parse_window(window(Lp=[lp[2], lp[-2]], k=[k[1], k[-1]]))
    expected = {(r.part, r.obs_id) for r in rows if lp[2] <= r.values["Lp"] <= lp[-2] and k[1] <= r.values["k"] <= k[-1]}
    picked = lib_tap.select_window(lib, STRATUM, win)
    assert {(s.part, s.obs_id) for s in picked} == expected and 0 < len(expected) < len(rows)
    assert all(s.snp == next(r.snp for r in rows if (r.part, r.obs_id) == (s.part, s.obs_id)) for s in picked)
    assert win.text() == f"window 10 GHz: Lp {lp[2]:g}..{lp[-2]:g}, k {k[1]:g}..{k[-1]:g}"


def test_rows_takes_an_index_table_a_pick_answer_and_a_list(built, tmp_path):
    """A ``lib.index out=`` table gives each cell's best row, a ``lib.pick`` answer its rows, a list its part / obs id pairs
    (a repeat taken once); a row that is not the stratum's, and a file made for another stratum, are refused."""
    lib = query.Library(built, limits=LOCAL)
    grid = {"Lp": [1e-10, 1e-9, 5e-11]}
    library_blocks.index(lib, STRATUM, 10, grid=grid, out=str(tmp_path / "table.json"))
    table = json.loads((tmp_path / "table.json").read_text())
    selected, source = lib_tap.select_rows(lib, STRATUM, str(tmp_path / "table.json"))
    assert [(s.part, s.obs_id) for s in selected] == [(c["best"]["part"], c["best"]["obs_id"]) for c in table["cells"]]
    assert source == f"file {tmp_path / 'table.json'}"
    answer = library_blocks.pick(lib, STRATUM, 10, targets={"Lp": 3e-10}, grid=grid, n=3)
    (tmp_path / "pick.json").write_text(json.dumps(answer))
    picked, _ = lib_tap.select_rows(lib, STRATUM, str(tmp_path / "pick.json"))
    assert [(s.part, s.obs_id) for s in picked] == [(r["part"], r["obs_id"]) for r in answer["rows"]]
    listed = [{"part": "xfm_narrow", "obs_id": "obs_0002"}, {"part": "xfm", "obs_id": "obs_0001"}, {"part": "xfm", "obs_id": "obs_0001"}]
    (tmp_path / "list.json").write_text(json.dumps(listed))
    rows, _ = lib_tap.select_rows(lib, STRATUM, str(tmp_path / "list.json"))
    assert [(s.part, s.obs_id) for s in rows] == [("xfm_narrow", "obs_0002"), ("xfm", "obs_0001")]
    (tmp_path / "foreign.json").write_text(json.dumps([{"part": "xfm", "obs_id": "obs_0099"}, {"part": "ind", "obs_id": "obs_0001"}]))
    with pytest.raises(lib_tap.TapError, match=r"rows: 2 of the 2 rows of file .* are not rows of xfm_demo .*: xfm/obs_0099, ind/obs_0001"):
        lib_tap.select_rows(lib, STRATUM, str(tmp_path / "foreign.json"))
    (tmp_path / "other.json").write_text(json.dumps({**table, "stratum": "ind_demo"}))
    with pytest.raises(lib_tap.TapError, match="lists rows of stratum 'ind_demo', not 'xfm_demo'"):
        lib_tap.select_rows(lib, STRATUM, str(tmp_path / "other.json"))


# 2. the plan


def test_the_plan_runs_the_preflight_only(built, tmp_path, capsys):
    """``--plan``: no EMX and no observation; the plan lists the rows per part, the taps, the fixture each part keeps, the
    rows' own EMX peak memory, the preflight's outcome with the refusals and their reasons, and the envelope. A 3.1 um
    primary tap makes the narrow part's CTP stub touch its P2 stub: refused by the generator (the fixture), with why."""
    run, host = make_run(tmp_path)
    plan(load_recipe("lib_tap"), run, library=str(built), stratum=STRATUM, taps={**SAME, "primary_width_um": 3.1}, window=window())
    out = capsys.readouterr().out
    assert host.emx_runs == 0 and run.store.observations() == [] and not (run.store.root / "reports" / "lib_tap.json").exists()
    assert ("[plan] lib_tap: 8 rows of xfm_demo from window 10 GHz: Lp 1e-12..1e-06 -- xfm 6 (obs_0001, obs_0002, obs_0003, "
            "obs_0004, obs_0005, obs_0006); xfm_narrow 2 (obs_0001, obs_0002)") in out
    assert "taps primary same (M6) width 3.1 um, secondary same (M5) width 3 um; measured grounded" in out
    assert "xfm: fixture kept on M4 (metal_rule free); EMX 1 thread(s) and a 4 GB memory cap per job, up to 2 jobs at once" in out
    assert "the rows' own EMX peak memory (from their emx.log, 6 of 6): median 1.62 GB, max 2.25 GB" in out   # 1.00 .. 2.25 GB
    assert "preflight (generator and DRC gate, no EMX): 6 of 8 clean, 2 refused by the generator, 0 by the DRC gate" in out
    assert ("refused by the generator (2): device xfm: PortError: ground fixture: the stubs of ports P2 and CTP on M4 touch (gap 0 um "
            "between their outlines, chamfers included)") in out and "-- xfm_narrow/obs_0001, xfm_narrow/obs_0002" in out
    assert "[plan] sim.evaluate step='lib_tap:xfm': 6 points" in out and "lib_tap:xfm_narrow" not in out.split("preflight")[1]
    assert "budget: 0 of 1000 simulations used in tapping; this run needs up to 12 more" in out


# 3. the twins


def test_twins_keep_the_rows_fixture_and_take_the_taps(built, tmp_path):
    """Same-metal taps resolve to the windings' own metals; the fixture stays on M4, the metal the rows recorded, under
    ``free``; a via-stack secondary tap on M3 passes through M4 and makes it ``shared``. The tap ports join the ports in
    the generator's order and the topology grounds them; each twin is the row's own parameters with the origin
    ``tap:<part>:<obs id>``; this run's EMX machine facts replace the part's."""
    run, _ = make_run(tmp_path)
    lib = query.Library(built, limits=LOCAL)
    rows = [s for s in lib_tap.select_window(lib, STRATUM, lib_tap.parse_window(window())) if s.part == "xfm"]
    same = lib_tap.twins_of_part(run, lib, "xfm", rows, lib_tap.parse_taps(SAME), threads=2, memory_gb=6, process_file=None)
    device = same.spec.devices[0]
    assert (device.fixed["ct_primary_metal"], device.fixed["ct_secondary_metal"]) == (device.fixed["primary_metal"], device.fixed["secondary_metal"]) == ("6", "5")
    assert device.fixed["ct_primary_width_um"] == device.fixed["ct_secondary_width_um"] == 3
    assert device.fixed["ground_fixture"] == {**FIXTURE, "metal": "M4", "metal_rule": "free"} and (same.fixture_metal, same.metal_rule) == ("M4", "free")
    assert device.ports == ["P1", "N1", "P2", "N2", "CTP", "CTS"] and device.topology.grounded == ["CTP", "CTS"]
    assert device.topology.drives == [("P1", "N1"), ("N2", "P2")] and same.taps == {"primary": "M6", "secondary": "M5"}
    assert (same.spec.em.threads, same.spec.em.memory_gb) == (2, 6) and same.spec.project == "tapping_tap_xfm"
    observations = {o.obs_id: o for o in RunStore(built / "xfm").observations()}
    assert [p.origin for p in same.points] == [f"tap:xfm:{r.obs_id}" for r in rows]
    assert [p.params for p in same.points] == [observations[r.obs_id].params for r in rows]
    for recorded in (built / "xfm" / ".icopt" / "sims").glob("*/em/xfm/xfm.gds"):
        assert fp.recorded_fixture_metal(recorded) == "M4"
    stacked = lib_tap.twins_of_part(run, lib, "xfm", rows, lib_tap.parse_taps({"secondary": "3", "measure": "grounded"}),
                                    threads=None, memory_gb=None, process_file=None)
    device = stacked.spec.devices[0]
    assert device.fixed["ground_fixture"]["metal_rule"] == "shared" and stacked.metal_rule == "shared"
    assert device.ports == ["P1", "N1", "P2", "N2", "CTS"] and device.topology.grounded == ["CTS"] and "ct_primary_metal" not in device.fixed
    assert stacked.spec.em == dataset._spec(built / "xfm").em                    # no override: the part's settings
    above = lib_tap.twins_of_part(run, lib, "xfm", rows, lib_tap.parse_taps({"primary": "M5", "measure": "grounded"}),
                                  threads=None, memory_gb=None, process_file=None)
    assert above.metal_rule == "free"                                                   # a stack M6 -> M5 does not reach M4


# 4. the narrow case


def test_the_narrow_parts_twins_pass_the_preflight_on_the_fixtures_own_spacing(built, tmp_path):
    """The narrow part's twin with 3 um same-metal taps: its CTP stub sits 0.05 um from the P2 and N2 stubs on M4, below M4's
    min space, and the preflight passes it -- M4 holds the fixture alone (``free``), so that spacing is the fixture's own.
    The same twin with the fixture's metal chosen under ``shared`` is refused by the DRC gate on those findings."""
    run, _ = make_run(tmp_path)
    lib = query.Library(built, limits=LOCAL)
    rows = [s for s in lib_tap.select_window(lib, STRATUM, lib_tap.parse_window(window())) if s.part == "xfm_narrow"]
    twins = lib_tap.twins_of_part(run, lib, "xfm_narrow", rows, lib_tap.parse_taps(SAME), threads=None, memory_gb=None, process_file=None)
    assert [o.status for o in lib_tap.preflight([twins], 4)] == ["clean", "clean"]
    from ic_opt.stages.em_chain import build_device

    geometry = build_device(twins.spec, twins.spec.devices[0], twins.points[0], tmp_path / "twin")
    raw = audit_gds(geometry.gds_path, "demo_6m")
    assert [(v.kind, v.count) for v in raw.violations if v.layer == "M4" and v.kind != "max_width"] == [("min_space", 2)]
    shared = twins.spec.model_copy(deep=True)
    shared.devices[0].fixed["ground_fixture"]["metal_rule"] = "shared"
    refused = lib_tap.preflight([lib_tap.Twins(twins.part, shared, twins.rows, twins.points, "M4", "shared", twins.taps, [])], 4)
    assert [(o.status, o.reasons) for o in refused] == [("drc", ("[min_space] M4 x2",))] * 2


# 5. a run


def test_a_run_reports_ratios_and_lists_failed_twins(library, tmp_path, capsys):
    """Every twin whose preflight is clean goes through EMX, one step per part; the EMX of the 100 um primaries fails (three
    twins). The report: the call, the rows, the preflight, per ok twin its values, its row's and the ratios of the six
    quantities and the window's columns at 10 GHz -- L and k as the row's (the tap's midpoint is at 0 V), Q down by the
    tap's resistance -- and the summary; the failed twins are listed and, with ``adopt``, not adopted."""
    run, host = make_run(tmp_path, fail_od=100)
    load_recipe("lib_tap")(run, library=str(library), stratum=STRATUM, taps=SAME, window=window(Lp=[1e-12, 1e-6], k=[0, 1]), adopt="xfm_tap")
    out = capsys.readouterr().out
    doc = report(run)
    assert host.emx_runs == 8 and {o.step for o in run.store.observations()} == {"lib_tap:xfm", "lib_tap:xfm_narrow"}
    assert doc["call"]["taps"] == {**SAME, "primary_width_um": 3.0, "secondary_width_um": 3.0} and doc["call"]["adopt"] == "xfm_tap"
    assert doc["selected"]["parts"] == {"xfm": [f"obs_{i:04d}" for i in range(1, 7)], "xfm_narrow": ["obs_0001", "obs_0002"]}
    assert [p["status"] for p in doc["preflight"]] == ["clean"] * 8
    failed = [p for p in doc["points"] if p["status"] != "ok"]
    assert [(p["part"], p["row"]) for p in failed] == [("xfm", "obs_0002"), ("xfm", "obs_0004"), ("xfm", "obs_0005")]
    assert all(p["status"] == "failed:emx:xfm" and "values" not in p for p in failed)
    ok = [p for p in doc["points"] if p["status"] == "ok"]
    assert len(ok) == 5 and all(p["origin"] == f"tap:{p['part']}:{p['row']}" for p in ok)
    for p in ok:
        assert set(p["ratios"]) == {"Lp_lf", "Ls_lf", "k_lf", "Qp_peak", "Qs_peak", "SRF", "Lp@10", "k@10"}
        for q in ("Lp_lf", "Ls_lf", "k_lf", "Lp@10", "k@10"):
            assert p["ratios"][q] == pytest.approx(1, abs=2e-3), (q, p)
        for q in ("Qp_peak", "Qs_peak"):
            assert 1 / TAP_LOSS - 0.01 < p["ratios"][q] < 1, (q, p)
        assert (p["ratios"]["SRF"] is None) == (p["values"]["row"]["SRF"] is None)
        assert p["ratios"]["SRF"] is None or p["ratios"]["SRF"] == pytest.approx(1, abs=2e-3)
        assert p["ratios"]["Lp_lf"] == p["values"]["twin"]["Lp_lf"] / p["values"]["row"]["Lp_lf"] and p["moved"] == []
    summary = doc["summary"]
    assert (summary["attempted"], summary["ok"], summary["refused"], len(summary["failed"])) == (8, 5, 0, 3)
    qp = summary["ratios"]["Qp_peak"]
    assert qp["n"] == 5 and qp["p10"] <= qp["median"] <= qp["p90"] < 1 and summary["moved"] == []
    assert 0 < summary["ratios"]["SRF"]["n"] < 5                       # the larger devices resonate inside the 40 GHz sweep
    assert f"lib_tap: 5/8 twins ok; median twin / row Lp_lf {summary['ratios']['Lp_lf']['median']:.3f}" in out
    adopted = doc["adopted"]["parts"]
    assert {store: len(ids) for store, ids in adopted.items()} == {"xfm_tap__xfm": 3, "xfm_tap__xfm_narrow": 2}
    origins = {json.loads(line)["origin"] for line in (library / "xfm_tap__xfm" / ".icopt" / "observations.jsonl").read_text().splitlines()}
    assert origins == {"tap:xfm:obs_0001", "tap:xfm:obs_0003", "tap:xfm:obs_0006"}


# 6. adopt


def test_adopt_adds_the_part_stores_and_the_stratum(library, tmp_path, capsys):
    """``adopt``: one fresh part store per source part with the twin spec and the twins (fresh ids, their origins); library.yaml
    backed up, and the new manifest is the old one plus exactly the new stratum -- the source's definition, the new parts,
    a note; the new stratum's dataset builds, its rows the twins, each naming its untapped row; an existing name is
    refused before anything runs."""
    before = (library / manifest.MANIFEST).read_text()
    run, host = make_run(tmp_path)
    load_recipe("lib_tap")(run, library=str(library), stratum=STRATUM, taps=SAME, window=window(), adopt="xfm_tap")
    after = yaml.safe_load((library / manifest.MANIFEST).read_text())
    (backup,) = library.glob("library.yaml.bak_*")
    assert backup.read_text() == before and len(backup.name) == len("library.yaml.bak_20261005T080000")
    old = yaml.safe_load(before)
    entry = after["strata"]["xfm_tap"]
    assert {**after, "strata": {k: v for k, v in after["strata"].items() if k != "xfm_tap"}} == old
    assert list(after["strata"]) == [STRATUM, "xfm_tap"]
    source = old["strata"][STRATUM]
    assert {k: v for k, v in entry.items() if k not in ("parts", "note")} == {k: v for k, v in source.items() if k != "parts"}
    assert entry["parts"] == [{"store": "xfm_tap__xfm"}, {"store": "xfm_tap__xfm_narrow"}]
    assert entry["note"].startswith("tapped twins of xfm_demo (8 rows, window 10 GHz: Lp 1e-12..1e-06): taps primary same (M6) width 3 um, "
                                    "secondary same (M5) width 3 um, measured grounded; lib_tap, 20")
    for part, rows in (("xfm", 6), ("xfm_narrow", 2)):
        store = library / f"xfm_tap__{part}"
        spec = dataset._spec(store)
        assert spec.devices[0].ports == ["P1", "N1", "P2", "N2", "CTP", "CTS"] and (store / ".icopt" / "spec.json").is_file()
        lines = (store / ".icopt" / "observations.jsonl").read_text().splitlines()
        assert [json.loads(line)["obs_id"] for line in lines] == [f"obs_{i:04d}" for i in range(1, rows + 1)]
    lib = query.Library(library, limits=LOCAL)
    ds = lib.dataset("xfm_tap")
    assert len(ds.rows) == 8 and ds.excluded == {} and dataset.check(ds)["ports"] == {6: 8}
    for row in ds.rows:
        twin = next(o for o in RunStore(library / row.part).observations() if o.obs_id == row.obs_id)
        _, part, obs = twin.origin.split(":")
        untapped = lib.dataset(STRATUM).find(row.coords)
        assert row.part == f"xfm_tap__{part}" and (untapped.part, untapped.obs_id) == (part, obs)
    out = capsys.readouterr().out
    assert "lib_tap: adopted 8 twins as xfm_tap (xfm_tap__xfm, xfm_tap__xfm_narrow); library.yaml updated" in out
    assert "its dataset: 8 rows, excluded {}" in out
    runs = host.emx_runs
    for name in ("xfm_tap", STRATUM):
        with pytest.raises(lib_tap.TapError, match=f"adopt={name}: the library has a stratum '{name}' already"):
            load_recipe("lib_tap")(run, library=str(library), stratum=STRATUM, taps=SAME, window=window(), adopt=name)
    (library / "fresh__xfm").mkdir()
    with pytest.raises(lib_tap.TapError, match="adopt=fresh: fresh__xfm exist"):
        load_recipe("lib_tap")(run, library=str(library), stratum=STRATUM, taps=SAME, window=window(), adopt="fresh")
    assert host.emx_runs == runs


def test_adopt_leaves_a_library_yaml_whose_strata_is_not_last_untouched(library, tmp_path, capsys):
    """When ``strata`` is not the last top-level key, appended text would not land in it: library.yaml keeps its bytes, the
    entry is written to ``<new stratum>.stratum.yaml`` and the plan and the run say so; the stores are adopted and the
    stratum's dataset is built in memory from the snippet's entry. A re-run reuses every twin: no EMX."""
    doc = yaml.safe_load((library / manifest.MANIFEST).read_text())
    text = yaml.safe_dump({"strata": doc["strata"], "schema_version": doc["schema_version"], "process_profile": doc["process_profile"]},
                          sort_keys=False)
    (library / manifest.MANIFEST).write_text(text)
    run, host = make_run(tmp_path)
    plan(load_recipe("lib_tap"), run, library=str(library), stratum=STRATUM, taps=SAME, window=window(), adopt="xfm_tap")
    assert "library.yaml cannot take it as appended text: the entry goes to xfm_tap.stratum.yaml" in capsys.readouterr().out
    load_recipe("lib_tap")(run, library=str(library), stratum=STRATUM, taps=SAME, window=window())
    assert host.emx_runs == 8
    load_recipe("lib_tap")(run, library=str(library), stratum=STRATUM, taps=SAME, window=window(), adopt="xfm_tap")
    assert host.emx_runs == 8                                           # every twin reused from the project's store
    assert (library / manifest.MANIFEST).read_text() == text and not list(library.glob("library.yaml.bak_*"))
    snippet = yaml.safe_load((library / "xfm_tap.stratum.yaml").read_text())
    assert list(snippet) == ["xfm_tap"] and snippet["xfm_tap"]["parts"] == [{"store": "xfm_tap__xfm"}, {"store": "xfm_tap__xfm_narrow"}]
    adopted = report(run)["adopted"]
    assert adopted["manifest"] == "snippet" and adopted["dataset"] == {"rows": 8, "excluded": {}}
    assert "library.yaml left untouched: the stratum's entry is in" in capsys.readouterr().out


def test_the_stratum_entry_is_appended_with_the_files_own_indentation(tmp_path):
    """``insert_stratum`` on a file indented by four: the entry lands in strata at four; a flow-style strata is not appended
    to (the snippet file instead)."""
    entry = {"generator": "g", "dims": ["a"], "parts": [{"store": "s"}], "quantities": {"Lp_lf": {}}}
    four = "schema_version: ic-opt-library-v1\nprocess_profile: demo_6m\nstrata:\n    old:\n        generator: g\n        dims: [a]\n" \
           "        parts: [{store: o}]\n        quantities: {Lp_lf: {}}\n"
    (tmp_path / manifest.MANIFEST).write_text(four)
    assert lib_tap.insert_stratum(tmp_path, "new", entry)["manifest"] == "inserted"
    text = (tmp_path / manifest.MANIFEST).read_text()
    assert text.startswith(four) and "\n    new:\n      generator: g\n" in text
    assert yaml.safe_load(text)["strata"]["new"] == entry
    flow = "schema_version: ic-opt-library-v1\nprocess_profile: demo_6m\nstrata: {old: {generator: g, dims: [a], parts: [{store: o}], quantities: {Lp_lf: {}}}}\n"
    (tmp_path / manifest.MANIFEST).write_text(flow)
    assert lib_tap.insert_stratum(tmp_path, "new", entry)["manifest"] == "snippet"
    assert (tmp_path / manifest.MANIFEST).read_text() == flow and yaml.safe_load((tmp_path / "new.stratum.yaml").read_text()) == {"new": entry}


# 7. refusals


def test_calls_that_cannot_be_carried_out_are_refused_with_what_to_change(built, library, tmp_path):
    """Before anything is built: a stratum of another generator; a tap above its winding; a width with a via-stack tap;
    no ``measure``, or ``floating``; neither or both of window and rows; a range over a non-column; a part whose rows
    disagree on the fixture's metal."""
    main = load_recipe("lib_tap")
    run, host = make_run(tmp_path)
    ind = build_library(tmp_path / "ind")

    def call(**params) -> dict:
        return params

    cases = [
        (call(library=str(ind), stratum="ind_demo", taps=SAME, window=window()),
         "stratum ind_demo is clean_port_ind_sym; lib_tap builds tapped twins of clean_port_xfm_bs and clean_port_xfm_ms tables only"),
        (call(library=str(built), stratum=STRATUM, taps={"secondary": "6", "measure": "grounded"}, window=window()),
         r"taps.secondary '6' \(M6\) sits above part xfm's secondary metal M5"),
        (call(library=str(built), stratum=STRATUM, taps={"secondary": "3", "secondary_width_um": 3, "measure": "grounded"}, window=window()),
         r"taps.secondary_width_um applies to a same-metal tap only; taps.secondary '3' \(M3\) sits below"),
        (call(library=str(built), stratum=STRATUM, taps={"primary": "same"}, window=window()), "taps.measure is required"),
        (call(library=str(built), stratum=STRATUM, taps={**SAME, "measure": "floating"}, window=window()),
         r"taps.measure \"floating\" \(taps open\) cannot be measured yet"),
        (call(library=str(built), stratum=STRATUM, taps=SAME), "give exactly one of window=<json>.*; got neither"),
        (call(library=str(built), stratum=STRATUM, taps=SAME, window=window(), rows="x.json"), "give exactly one of window=<json>.*; got both"),
        (call(library=str(built), stratum=STRATUM, taps=SAME, window=window(L=[0, 1])), r"window.ranges: \['L'\] is not a column of xfm_demo's index"),
        (call(library=str(built), stratum=STRATUM, taps={**SAME, "tap": 1}, window=window()), r"taps: unknown key\(s\) \['tap'\]"),
        (call(library=str(built), stratum=STRATUM, taps={"primary_width_um": 3, "measure": "grounded"}, window=window()), "taps: no tap"),
    ]
    for params, message in cases:
        with pytest.raises(lib_tap.TapError, match=message):
            main(run, **params)
    manifest_path = next((library / "xfm" / ".icopt" / "sims").glob("obs_0003/em/xfm/geometry_manifest.json"))
    data = json.loads(manifest_path.read_text())
    data["geometry"]["fixture_metal"] = "M3"
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(lib_tap.TapError, match=r"part xfm: the selected rows disagree on the ground fixture's metal \(M4: 5 .*; M3: 1 \(obs_0003\)\)"):
        main(run, library=str(library), stratum=STRATUM, taps=SAME, window=window())
    assert host.emx_runs == 0 and run.store.observations() == []


# 8. xfm_ms (T19.6)

MS_STRATUM = "ms_demo"
MS_DIMS = [*XFM_DIMS, "secondary_spacing_um", "secondary_turns"]
#: (primary OD, secondary OD, primary W, secondary W, centre spacing, secondary spacing, secondary turns)
MS_POINTS = [(100, 76, 6, 3, 0, 2, 3), (100, 76, 6, 3, 0, 2, 2), (120, 90, 6, 3, 0, 2, 3), (110, 84, 5, 4, 0, 2, 2)]
MS_M1_POINTS = [(100, 76, 6, 3, 0, 2, 3), (120, 90, 6, 3, 0, 2, 2)]
MS_QUANTITIES = {"Lp_lf": {}, "Ls_lf": {}, "k_lf": {"feature_map": "xfm_ms_dimensionless"}, "Qp_peak": {"band_ghz": STOP_GHZ},
                 "Qs_peak": {"band_ghz": STOP_GHZ}, "SRF": {}, "Lp": {"anchors_ghz": [10]},
                 "k": {"anchors_ghz": [10], "feature_map": "xfm_ms_dimensionless"}}
MS_ROWS = len(MS_POINTS) + len(MS_M1_POINTS)


def ms_part_spec(project: str, fixture: dict) -> Spec:
    """An untapped xfm_ms part: the single-turn primary on M6, the multi-turn secondary on M5, swept 0-40 GHz."""
    d = part_spec(project).model_dump(mode="json")
    d["devices"][0].update(id="ms", generator="clean_port_xfm_ms", variables={k: k for k in MS_DIMS},
                           fixed={"primary_opening_um": 8.0, "secondary_opening_um": 6.0, "primary_lead_length_um": 20.0,
                                  "secondary_lead_length_um": 15.0, "primary_metal": "6", "secondary_metal": "5",
                                  "ground_fixture": fixture})
    d["variables"] = [{"name": name, "kind": kind, "lower": lo, "upper": hi, "step": step}
                      for name, kind, lo, hi, step in (("primary_outer_diameter_um", "continuous_step", "60", "200", "0.01"),
                                                       ("secondary_outer_diameter_um", "continuous_step", "40", "200", "0.01"),
                                                       ("primary_width_um", "continuous_step", "3", "10", "0.01"),
                                                       ("secondary_width_um", "continuous_step", "2", "10", "0.01"),
                                                       ("center_spacing_um", "continuous_step", "0", "100", "0.01"),
                                                       ("secondary_spacing_um", "continuous_step", "1", "5", "0.01"),
                                                       ("secondary_turns", "integer", "2", "4", "1"))]
    d["metrics"] = [{"name": "k", "unit": "1", "device": "ms", "quantity": "k_lf"}]
    d["em"]["three_d_metals"] = ["M6", "M5", "M4"]
    return Spec.model_validate(d)


def build_ms_part(root: Path, name: str, points, fixture: dict) -> None:
    project = root / name
    project.mkdir(parents=True)
    part = ms_part_spec(name, fixture)
    (project / "spec.yaml").write_text(yaml.safe_dump(part.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    executor = FakeSpectreExecutor(store.root / "sims", snp_fn=coupled_snp)
    obs = evaluate(part, [Point({d: f"{v:g}" for d, v in zip(MS_DIMS, p, strict=True)}, "grid") for p in points], executor, store,
                   limits=FAKE_HOST)
    assert [o.status for o in obs] == ["ok"] * len(points), [o.issues for o in obs]


@pytest.fixture(scope="module")
def ms_built(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("mstaplib")
    build_ms_part(root, "ms", MS_POINTS, {**FIXTURE, "metal": "auto"})
    build_ms_part(root, "ms_m1", MS_M1_POINTS, dict(FIXTURE))
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m",
           "strata": {MS_STRATUM: {"generator": "clean_port_xfm_ms", "dims": MS_DIMS, "nt_dim": "secondary_turns",
                                   "parts": [{"store": "ms"}, {"store": "ms_m1"}],
                                   "steps": {**STEPS, "secondary_spacing_um": 0.1, "secondary_turns": 1}, "quantities": MS_QUANTITIES}}}
    (root / manifest.MANIFEST).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return root


def ms_rows(lib: query.Library, part: str) -> list:
    return [s for s in lib_tap.select_window(lib, MS_STRATUM, lib_tap.parse_window(window())) if s.part == part]


def ms_twins(run: Run, lib: query.Library, part: str, **taps) -> lib_tap.Twins:
    return lib_tap.twins_of_part(run, lib, part, ms_rows(lib, part), lib_tap.parse_taps({**taps, "measure": "grounded"}),
                                 threads=None, memory_gb=None, process_file=None)


def test_xfm_ms_twins_tap_the_primary_on_its_own_metal(ms_built, tmp_path):
    """``taps.primary: "same"`` on an xfm_ms part: the tap on the single-turn primary's metal (M6), its width, the CTP port
    added and grounded, the fixture kept on M3 -- the metal the rows recorded under ``auto`` -- under ``free``, since a
    same-metal tap draws nothing below the windings; every twin passes the preflight (generator and DRC gate)."""
    run, _ = make_run(tmp_path)
    lib = query.Library(ms_built, limits=LOCAL)
    twins = ms_twins(run, lib, "ms", primary="same", primary_width_um=3)
    device = twins.spec.devices[0]
    assert device.generator == "clean_port_xfm_ms" and len(twins.points) == len(MS_POINTS)
    assert device.fixed["ct_primary_metal"] == device.fixed["primary_metal"] == "6" and device.fixed["ct_primary_width_um"] == 3
    assert "ct_secondary_metal" not in device.fixed and twins.taps == {"primary": "M6"}
    assert device.fixed["ground_fixture"] == {**FIXTURE, "metal": "M3", "metal_rule": "free"}
    assert device.ports == ["P1", "N1", "P2", "N2", "CTP"] and device.topology.grounded == ["CTP"]
    assert [o.status for o in lib_tap.preflight([twins], 4)] == ["clean"] * len(MS_POINTS)


def test_xfm_ms_secondary_taps_are_via_stacks_at_least_two_levels_down(ms_built, tmp_path):
    """The multi-turn secondary keeps ind_sym's tap: two levels down (M3) builds on the part whose fixture is on the bottom
    metal; on the part whose fixture ``auto`` put on M3 that tap would end on the fixture's metal, which holds no port lead:
    the preflight lists the generator's refusal. Three levels down (M2) passes through M3: ``shared``. Beside a same-metal
    primary tap it builds on the three-turn rows; on the two-turn rows the secondary's tap leaves on the primary tap's side
    (ind_sym's tap exits right for an even turn count), on the same centre line, and the two tap stubs overlap: the
    fixture refuses them, as it refuses a via-stack primary tap there (xfm_ms as it was before T19.6)."""
    run, _ = make_run(tmp_path)
    lib = query.Library(ms_built, limits=LOCAL)
    low = ms_twins(run, lib, "ms_m1", secondary="3")
    assert low.spec.devices[0].fixed["ground_fixture"] == {**FIXTURE, "metal": "M1", "metal_rule": "free"}
    assert low.spec.devices[0].ports == ["P1", "N1", "P2", "N2", "CTS"] and low.taps == {"secondary": "M3"}
    assert [o.status for o in lib_tap.preflight([low], 4)] == ["clean"] * len(MS_M1_POINTS)
    onto = ms_twins(run, lib, "ms", secondary="3")
    assert (onto.fixture_metal, onto.metal_rule) == ("M3", "free")
    refused = lib_tap.preflight([onto], 4)
    assert [o.status for o in refused] == ["generator"] * len(MS_POINTS)
    assert all(any("ground fixture: metal 'M3' (M3) carries the lead of port CTS" in r for r in o.reasons) for o in refused), refused
    both = ms_twins(run, lib, "ms", primary="same", secondary="2")
    assert (both.fixture_metal, both.metal_rule) == ("M3", "shared") and both.taps == {"primary": "M6", "secondary": "M2"}
    assert both.spec.devices[0].ports == ["P1", "N1", "P2", "N2", "CTP", "CTS"]
    outcomes = lib_tap.preflight([both], 4)
    assert [o.status for o in outcomes] == ["clean" if p[-1] % 2 else "generator" for p in MS_POINTS]
    assert all("the stubs of ports CTP and CTS on M3 overlap" in o.reasons[0] for o in outcomes if o.status != "clean"), outcomes


def test_xfm_ms_secondary_taps_that_cannot_be_drawn_are_refused_with_why(ms_built, tmp_path):
    """Before anything is built: the secondary's tap on its own metal (``"same"`` or spelled out), one level down (its
    crossunder's metal), or with a width -- each naming why."""
    run, host = make_run(tmp_path)
    main = load_recipe("lib_tap")
    cases = [
        ({"secondary": "same"}, (r"taps.secondary 'same' \(M5, the secondary's own metal\): part ms is an xfm_ms, whose secondary "
                                 r"is its multi-turn winding; .* would cross its turns: give a metal at least two levels below "
                                 r"the secondary \(M3 or lower\), or null")),
        ({"secondary": "M5"}, r"taps.secondary 'M5' \(M5, the secondary's own metal\)"),
        ({"secondary": "4"}, (r"taps.secondary '4' \(M4\) must sit at least two levels below part ms's secondary metal M5: the "
                              r"multi-turn secondary's crossunder occupies M4")),
        ({"secondary": "2", "secondary_width_um": 3}, "taps.secondary_width_um: part ms is an xfm_ms, whose secondary tap is a via-stack tap"),
        ({"primary": "same", "primary_width_um": 3, "secondary": "same"}, r"taps.secondary 'same' \(M5"),
    ]
    for taps, message in cases:
        with pytest.raises(lib_tap.TapError, match=message):
            main(run, library=str(ms_built), stratum=MS_STRATUM, taps={**taps, "measure": "grounded"}, window=window())
    assert host.emx_runs == 0 and run.store.observations() == []


def test_a_run_adopts_xfm_ms_twins_as_an_xfm_ms_table(ms_built, tmp_path, capsys):
    """A run on the xfm_ms table with a 3 um same-metal primary tap simulates every twin, reports L and k as the row's and
    Qp down by the tap's resistance (Qs less so: the secondary is untapped and sees it through the coupling only), and
    adopts them: the new stratum is the source's definition -- the ms generator, its dims with the turns, its quantities
    and feature maps -- and its dataset builds, five ports a row."""
    library = Path(shutil.copytree(ms_built, tmp_path / "lib"))
    run, host = make_run(tmp_path)
    load_recipe("lib_tap")(run, library=str(library), stratum=MS_STRATUM, taps={"primary": "same", "primary_width_um": 3, "measure": "grounded"},
                           window=window(), adopt="ms_tap")
    doc = report(run)
    assert host.emx_runs == MS_ROWS and [p["status"] for p in doc["preflight"]] == ["clean"] * MS_ROWS
    assert doc["twins"]["ms"]["taps"] == {"primary": "M6"} and (doc["twins"]["ms"]["fixture_metal"], doc["twins"]["ms"]["metal_rule"]) == ("M3", "free")
    assert (doc["twins"]["ms_m1"]["fixture_metal"], doc["twins"]["ms_m1"]["metal_rule"]) == ("M1", "free")
    ok = [p for p in doc["points"] if p["status"] == "ok"]
    assert len(ok) == MS_ROWS
    for p in ok:
        for q in ("Lp_lf", "Ls_lf", "k_lf", "Lp@10"):
            assert p["ratios"][q] == pytest.approx(1, abs=2e-3), (q, p)
        assert 1 / TAP_LOSS - 0.01 < p["ratios"]["Qp_peak"] < p["ratios"]["Qs_peak"] < 1, p
    after = yaml.safe_load((library / manifest.MANIFEST).read_text())
    source, entry = after["strata"][MS_STRATUM], after["strata"]["ms_tap"]
    assert {k: v for k, v in entry.items() if k not in ("parts", "note")} == {k: v for k, v in source.items() if k != "parts"}
    assert entry["generator"] == "clean_port_xfm_ms" and entry["dims"] == MS_DIMS and entry["nt_dim"] == "secondary_turns"
    assert entry["quantities"] == MS_QUANTITIES and entry["parts"] == [{"store": "ms_tap__ms"}, {"store": "ms_tap__ms_m1"}]
    assert entry["note"].startswith(f"tapped twins of {MS_STRATUM} ({MS_ROWS} rows, window 10 GHz: Lp 1e-12..1e-06): taps primary same "
                                    "(M6) width 3 um, secondary none, measured grounded; lib_tap, 20")
    ds = query.Library(library, limits=LOCAL).dataset("ms_tap")
    assert len(ds.rows) == MS_ROWS and ds.excluded == {} and dataset.check(ds)["ports"] == {5: MS_ROWS}
    assert f"lib_tap: adopted {MS_ROWS} twins as ms_tap (ms_tap__ms, ms_tap__ms_m1); library.yaml updated" in capsys.readouterr().out


# the pieces


def test_the_summary_flags_the_twins_whose_l_or_k_moved():
    """Per quantity n, median, 10th and 90th percentiles; a twin is flagged when L or k moved by more than 5 %, Q is not."""
    points = [{"part": "a", "row": f"obs_{i}", "twin": f"t{i}", "ratios": {"Lp_lf": lp, "k_lf": 1.0, "Qp_peak": 0.9}} for i, lp in enumerate((1.0, 1.06, 0.94, 1.01))]
    for p in points:
        p["moved"] = [q for q in lib_tap.MOVED_QUANTITIES if q in p["ratios"] and abs(p["ratios"][q] - 1) > lib_tap.MOVED]
    summary = lib_tap.summarize(points)
    assert summary["ratios"]["Lp_lf"]["n"] == 4 and summary["ratios"]["Lp_lf"]["median"] == pytest.approx(1.005)
    assert [m["row"] for m in summary["moved"]] == ["obs_1", "obs_2"] and summary["moved"][0]["ratios"] == {"Lp_lf": 1.06}
    assert lib_tap.ratio(2.0, 0.0) is None and lib_tap.ratio(None, 1.0) is None and lib_tap.ratio(3.0, 2.0) == 1.5


def test_the_peak_memory_is_read_from_emxs_own_line(tmp_path):
    log = tmp_path / "emx.log"
    log.write_text("Reducing, current peak memory usage 2.36 GB...\nPeak memory usage 846.00 MB\nWall-clock time 100.08 sec\n")
    assert lib_tap._peak_gb(log) == pytest.approx(846 / 1024)
    log.write_text("fake emx\n")
    assert lib_tap._peak_gb(log) is None and lib_tap._peak_gb(tmp_path / "missing.log") is None


def test_lib_tap_is_a_built_in_recipe():
    assert "lib_tap" in BUILTIN_RECIPES and load_recipe("lib_tap") is lib_tap.main
