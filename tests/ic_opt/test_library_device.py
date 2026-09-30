"""T18.2B sections 3 and 4: ``touchstone.write``, the ``pick`` stage (a point's library row, its sNp in the bindings' port
order), and a circuit spec with a library device evaluated end to end on the fake host -- no EMX anywhere."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from ic_opt.blocks.evaluate import default_pipeline, evaluate, plan_shape
from ic_opt.blocks.netlist import import_netlists
from ic_opt.em import touchstone
from ic_opt.eval import engine
from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.library import link
from ic_opt.library.stage import Pick
from ic_opt.observation import Observation
from ic_opt.space import Point
from ic_opt.stages.em_chain import Measure
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
from tests.ic_opt.library_device_fixtures import F0, NETLIST, library_spec, xfm_library
from tests.ic_opt.library_fixtures import rlc_touchstone, xfm_touchstone
from tests.ic_opt.test_blocks import maestro_export


@pytest.fixture(autouse=True)
def fresh():
    link.clear()
    yield
    link.clear()


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return xfm_library(tmp_path_factory.mktemp("device") / "lib")


# -- touchstone.write -------------------------------------------------------------------------------------------------------

def test_write_reads_back_what_it_was_given(tmp_path):
    source = tmp_path / "x.s4p"
    source.write_text(xfm_touchstone(100.0, 80.0, 4.0, 7.0, 0.0), encoding="utf-8")
    ts = touchstone.read(source)
    back = touchstone.read(touchstone.write(tmp_path / "y.s4p", ts, comments=["a comment"]))
    assert (back.unit, back.fmt, back.z0) == ("Hz", "RI", 50.0) and np.array_equal(back.freqs, ts.freqs)
    assert np.array_equal(back.s, ts.s)                                           # RI: exactly
    text = (tmp_path / "y.s4p").read_text(encoding="utf-8").splitlines()
    assert text[:2] == ["! a comment", "# Hz S RI R 50"] and text[2].split()[0] == "0" and len(text[2].split()) == 9
    assert float(text[2].split()[1]) == ts.s[0, 0, 0].real and float(text[2].split()[2]) == ts.s[0, 0, 0].imag
    assert text[6].split()[0] == "1000000000" and len(text) == 2 + 4 * len(ts.freqs)   # a row of the matrix a line
    two = tmp_path / "r.s2p"
    two.write_text(rlc_touchstone(120.0, 5.0, 2.0, 2), encoding="utf-8")
    ts2 = touchstone.read(two)
    assert np.array_equal(touchstone.read(touchstone.write(tmp_path / "w.s2p", ts2)).s, ts2.s)   # S11 S21 S12 S22 kept
    for unit, fmt in (("GHz", "MA"), ("MHz", "DB"), ("kHz", "RI")):
        other = touchstone.Touchstone(ts.freqs.copy(), ts.s.copy(), 25.0, unit, fmt)
        again = touchstone.read(touchstone.write(tmp_path / f"{fmt}.s4p", other))
        assert (again.unit, again.fmt, again.z0) == (unit, fmt, 25.0) and np.allclose(again.freqs, ts.freqs, rtol=1e-15)
        assert np.allclose(again.s, ts.s, rtol=1e-12, atol=1e-14), (unit, fmt)
    with pytest.raises(touchstone.TouchstoneError, match="another port count"):
        touchstone.write(tmp_path / "z.s2p", ts)


def test_a_permuted_file_reads_back_as_the_permuted_matrices_and_wraps_a_long_row(tmp_path):
    source = tmp_path / "x.s4p"
    source.write_text(xfm_touchstone(160.0, 128.0, 7.0, 4.0, 18.0), encoding="utf-8")
    ts = touchstone.read(source)
    order = [2, 3, 0, 1]
    back = touchstone.read(touchstone.write(tmp_path / "p.s4p", touchstone.permuted(ts, order)))
    assert np.array_equal(back.s, ts.s[:, order][:, :, order]) and back.s[3, 0, 1] == ts.s[3, 2, 3]
    with pytest.raises(touchstone.TouchstoneError, match="lists each port once"):
        touchstone.permuted(ts, [0, 0, 1, 2])
    eight = touchstone.Touchstone(ts.freqs[:3].copy(), np.tile(ts.s[:3], (1, 2, 2)), 50.0)
    written = touchstone.write(tmp_path / "e.s8p", eight)
    assert len(written.read_text(encoding="utf-8").splitlines()) == 1 + 3 * 8 * 2       # 8 pairs a row: two lines of four
    assert np.array_equal(touchstone.read(written).s, eight.s)


# -- pick -----------------------------------------------------------------------------------------------------------------

def context(spec, tmp_path: Path, obs: str = "obs_0001") -> StageContext:
    store = RunStore(tmp_path / "proj")
    workdir = store.root / "sims" / obs
    workdir.mkdir(parents=True, exist_ok=True)
    return StageContext(spec=spec, executor=None, store=store, obs_id=obs, workdir=workdir, remote_dir="r")


def a_point(spec, *, several: bool = False, nth: int = 0) -> tuple[Point, object]:
    """A valid point (F=20) on a combination of the table -- the one of the most rows when ``several``, else its ``nth`` --
    and that combination's best row."""
    linked = link.resolve(spec)["xfmr"]
    cell = max(linked.table.cells, key=lambda c: (len(linked.table.cells[c]), c)) if several else linked.table.levels()[nth]
    values = linked.table.values(cell)
    texts = {"xfmr.Lp": f"{round(values['Lp'] * 1e12):d}p", "xfmr.Ls": f"{round(values['Ls'] * 1e12):d}p",
             "xfmr.k": f"{values['k']:g}"}
    return Point({"F": "20", **texts}, "user"), linked.table.cells[cell][0]


def measured(spec, geometry, tmp_path) -> dict:
    ctx = context(spec, tmp_path, "obs_measure")
    ctx.unit = "xfmr"
    return Measure(simulates=False).run(geometry, ctx).metrics


def test_pick_copies_the_row_s_snp_in_its_own_order_and_says_which_row(root, tmp_path):
    spec = library_spec(root)
    point, row = a_point(spec, several=True)
    ctx = context(spec, tmp_path)
    stage = Pick(spec)
    geometry = stage.run(point, ctx)
    target = ctx.workdir / "em" / "xfmr" / "xfmr.s4p"
    assert (stage.name, stage.level, stage.runs, stage.fingerprint(point, ctx)) == ("pick", "point", 0, None)
    assert stage.identity == link.identity(spec) and engine.point_runs([stage]) == 0
    assert target.read_bytes() == (root / row.snp).read_bytes()                       # the bindings' order is the file's
    sp = geometry.sparams["xfmr"]
    assert (sp.path, sp.port_labels, sp.z0) == (target, ["P1", "N1", "P2", "N2"], 50.0)
    assert sp.topology.drives == [("P1", "N1"), ("N2", "P2")]                           # the part's: the library's measure
    picks = json.loads((ctx.workdir / "em" / "pick.json").read_text(encoding="utf-8"))
    assert picks == geometry.picks == {"xfmr": {
        "stratum": "xfm_demo", "part": row.part, "obs_id": row.obs_id, "geometry": row.params, "values": row.values,
        "footprint": None, "combination": {k: point.params[k] for k in ("xfmr.Lp", "xfmr.Ls", "xfmr.k")}}}
    assert "/" not in json.dumps(picks)                                                # no path: the row by part and obs id
    metrics = measured(spec, geometry, tmp_path)
    assert metrics == {"Lp": row.values["Lp"], "k": row.values["k"], "Qp": row.values["Qp"]}    # the same file, the same numbers


def test_pick_permutes_the_ports_to_the_bindings_order_and_the_device_measures_the_same(root, tmp_path):
    spec = library_spec(root, terminals=("P2", "N2", "P1", "N1"))
    point, row = a_point(spec)
    ctx = context(spec, tmp_path)
    geometry = Pick(spec).run(point, ctx)
    target = ctx.workdir / "em" / "xfmr" / "xfmr.s4p"
    source = touchstone.read(root / row.snp)
    written = touchstone.read(target)
    assert target.read_bytes() != (root / row.snp).read_bytes() and geometry.sparams["xfmr"].port_labels == ["P2", "N2", "P1", "N1"]
    assert np.array_equal(written.s, source.s[:, [2, 3, 0, 1]][:, :, [2, 3, 0, 1]]) and np.array_equal(written.freqs, source.freqs)
    assert target.read_text(encoding="utf-8").startswith(f"! ic-opt: library row xfm_demo {row.part}/{row.obs_id}, ports "
                                                         "reordered from P1 N1 P2 N2 to P2 N2 P1 N1\n# Hz S RI R 50\n")
    metrics = measured(spec, geometry, tmp_path)
    for name in ("Lp", "k", "Qp"):                                                      # the bound instance sees the right ports
        assert metrics[name] == pytest.approx(row.values[name], rel=1e-9), name


def test_a_point_off_the_table_fails_as_failed_pick_naming_the_nearest_combination(root, tmp_path):
    spec = library_spec(root)
    linked = link.resolve(spec)["xfmr"]
    empty = next((a, b, c) for a in range(16) for b in range(16) for c in range(6) if (a, b, c) not in linked.table.cells)
    values = linked.table.values(empty)
    point = Point({"F": "20", "xfmr.Lp": f"{round(values['Lp'] * 1e12):d}p", "xfmr.Ls": f"{round(values['Ls'] * 1e12):d}p",
                   "xfmr.k": f"{values['k']:g}"}, "user")
    with pytest.raises(StageFailure, match=r"device xfmr: xfmr.Lp=.* is not a combination of its library table .* the nearest "
                                           r"one is xfmr.Lp="):
        Pick(spec).run(point, context(spec, tmp_path))
    spec = circuit(root, tmp_path)
    store = RunStore(tmp_path / "engine")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0})
    obs = evaluate(spec, [point], ex, store, deck=import_netlists(spec, ex, store), limits=FAKE_HOST)
    assert obs[0].status == "failed:pick" and {c.status for c in obs[0].children.values()} == {"failed:pick"}
    assert "is not a combination of its library table" in obs[0].issues[0]
    assert obs[0].simulations == 1                  # its testbench child, counted as the children of a failed:pcell point are


# -- end to end ------------------------------------------------------------------------------------------------------------

def circuit(root, tmp_path, **kwargs):
    export = maestro_export(tmp_path / "maestro", "tb", params="temperature=27 F=20")
    (export / "netlist" / "input.scs").write_text(NETLIST, encoding="utf-8")
    return library_spec(root, export=export, **kwargs)


def test_a_circuit_with_a_library_device_is_evaluated_without_emx(root, tmp_path):
    spec = circuit(root, tmp_path, corners=[{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss"}])
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0 + int(p["F"]) / 100 + (1.0 if c == "ss" else 0.0)})
    deck = import_netlists(spec, ex, store)
    pipeline = default_pipeline(spec, deck)
    assert [s.name for s in pipeline] == ["pick", "bind_nport", "spectre", "ocean", "extract", "measure"]
    assert engine.point_runs(pipeline) == 0
    first, row = a_point(spec, several=True)
    second, other = a_point(spec, nth=0 if first.params != a_point(spec)[0].params else 1)
    obs = evaluate(spec, [first, second], ex, store, deck=deck, parallel_jobs=1, limits=FAKE_HOST)
    assert [o.status for o in obs] == ["ok", "ok"] and set(obs[0].children) == {"tb/tt", "tb/ss", "xfmr/nominal"}
    assert obs[0].metrics["NF"] == pytest.approx(8.2)                                    # the testbench's, worst case: ss
    assert {k: obs[0].metrics[k] for k in ("Lp", "k", "Qp")} == {k: row.values[k] for k in ("Lp", "k", "Qp")}   # the row's sNp
    child = obs[0].children["xfmr/nominal"]
    assert child.library_row == {"stratum": "xfm_demo", "part": row.part, "obs_id": row.obs_id, "geometry": row.params,
                                 "values": row.values, "footprint": None}
    assert obs[1].children["xfmr/nominal"].library_row["obs_id"] == other.obs_id
    assert "library_row" not in obs[0].children["tb/tt"].model_dump()               # every other child as before
    assert Observation.model_validate_json(obs[0].model_dump_json()).children["xfmr/nominal"].library_row == child.library_row
    assert obs[0].corners() == {"tt", "ss"} and obs[0].simulations == 2              # the device child is no corner, no simulation
    netlist = (store.root / "sims" / "obs_0001" / "tb" / "tt" / "netlist" / "input.scs").read_text(encoding="utf-8")
    assert 'NPORT0 ( p 0 n 0 s1 0 s2 0 ) nport file="models/xfmr.s4p" interp=bbspice' in netlist
    bound = store.root / "sims" / "obs_0001" / "tb" / "tt" / "netlist" / "models" / "xfmr.s4p"
    assert bound.read_bytes() == (root / row.snp).read_bytes()
    assert ex.emx_runs == 0 and not list(store.root.rglob("emx.cmd")) and not any("emx" in c for c in ex.commands)
    steps = [json.loads(line) for line in (store.root / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
    assert steps[-1]["simulations"] == 4                                                  # 2 points x 2 testbench simulations
    assert plan_shape(spec, pipeline, "all", ex, 1, FAKE_HOST) == (
        "(2 testbench sims + 1 device measurements of library rows, no simulation) = 2 simulations per point on local, "
        "1 workers × (2 + 1) threads (spectre), no EMX runs (the devices are library rows)")


def test_the_budget_counts_the_testbench_simulations_only(root, tmp_path):
    spec = circuit(root, tmp_path, budget={"max_simulations": 2})
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0})
    deck = import_netlists(spec, ex, store)
    linked = link.resolve(spec)["xfmr"]
    points = []
    for cell in linked.table.levels()[:3]:
        values = linked.table.values(cell)
        points.append(Point({"F": "20", "xfmr.Lp": f"{round(values['Lp'] * 1e12):d}p",
                             "xfmr.Ls": f"{round(values['Ls'] * 1e12):d}p", "xfmr.k": f"{values['k']:g}"}, "user"))
    assert [o.simulations for o in evaluate(spec, points[:2], ex, store, deck=deck, limits=FAKE_HOST)] == [1, 1]
    with pytest.raises(engine.BudgetExceeded, match="2 simulations used, 1 needed per point"):
        evaluate(spec, points[2:], ex, store, deck=deck, limits=FAKE_HOST)


def test_a_spec_of_library_devices_alone_measures_the_rows(root, tmp_path):
    spec = library_spec(root, testbenches=[], bindings=[], metrics=[
        {"name": "Lp", "unit": "H", "device": "xfmr", "quantity": "Lp", "frequency_hz": F0},
        {"name": "k", "unit": "1", "device": "xfmr", "quantity": "k", "frequency_hz": F0}],
        constraints=[], objective={"direction": "maximize", "expression": "k"})
    pipeline = default_pipeline(spec, None)
    assert [s.name for s in pipeline] == ["pick", "measure"]
    store = RunStore(tmp_path / "proj")
    point, row = a_point(spec)
    (o,) = evaluate(spec, [point], FakeSpectreExecutor(store.root / "sims"), store, limits=FAKE_HOST)
    assert o.status == "ok" and o.metrics == {"Lp": row.values["Lp"], "k": row.values["k"]} and o.simulations == 0
    assert o.children["xfmr/nominal"].library_row["obs_id"] == row.obs_id and o.corners() == {"nominal"}


def test_a_device_that_states_its_topology_is_measured_with_it(root, tmp_path):
    """The device's own topology wins over the part's: the secondary driven the other way measures k < 0 (and says so)."""
    from tests.ic_opt.library_device_fixtures import library_device

    device = library_device(root) | {"topology": {"drives": [["P1", "N1"], ["P2", "N2"]]}}
    spec = library_spec(root, device=device)
    point, row = a_point(spec)
    geometry = Pick(spec).run(point, context(spec, tmp_path))
    assert geometry.sparams["xfmr"].topology is None
    ctx = context(spec, tmp_path, "obs_measure")
    ctx.unit = "xfmr"
    result = Measure(simulates=False).run(geometry, ctx)
    assert result.metrics["k"] == pytest.approx(-row.values["k"], rel=1e-9)
    assert result.library_row["obs_id"] == row.obs_id

