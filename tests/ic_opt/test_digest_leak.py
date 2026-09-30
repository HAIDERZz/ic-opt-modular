"""The run digest carries fixed fields and the texts the run produced, never the project's or the host's own text
(T17.10 specification, 1.6; SABLE: free text from the design must not leak through what an agent reads).

Canaries ride wherever such text lives: the metrics' OCEAN expressions (``LEAK_EXPR``); the testbench's export root and
cell names, the corner's model file, every child's ``sim_dir`` (``LEAK_PATH``); the spec's descriptions and a refused
advice's text as given (``LEAK_SPEC_TEXT``); and the children's issues (``LEAK_ISSUE_FREE_TEXT``). An issue text is a run
output: it stays, cut to 200 characters, in the entry of the texts given most often and nowhere else. The instance names
of an operating-point table are the table the digest prints (T17.1.5 specification, section 5.2); the canary of the
tables rides in a quantity outside section 4's list and in the tables of the points the digest does not print.
"""

from __future__ import annotations

import copy
import json

from ic_opt import advice as advice_rules
from ic_opt import digest as dg
from ic_opt.blocks import analyze
from ic_opt.observation import ChildResult, Observation
from ic_opt.sim.corner import aggregate
from ic_opt.store import RunStore
from tests.ic_opt.fakes import make_spec
from tests.ic_opt.test_digest import abc_metrics, abc_rows

HIDDEN = ("LEAK_EXPR", "LEAK_PATH", "LEAK_SPEC_TEXT")        # nowhere in the digest
ISSUE = "LEAK_ISSUE_FREE_TEXT"                                # in the entry of the issue texts given most often only
SIMULATOR = ("threads_per_run", "parallel_jobs", "timeout_s", "license_check", "keep_failed_runs", "spectre_x", "psfxl")
SAID = f"{ISSUE}: the simulator stopped, " + "and went on " * 20 + "until it named /LEAK_PATH/scratch/spectre.out"


def leaky_spec():
    """The digest tests' problem (A decides m1 >= 5, B above 0.7 gives no value, m2 grows with C), at one corner, with a
    canary wherever the project's own text lives."""
    return make_spec(
        description="LEAK_SPEC_TEXT: what the designer wrote about the circuit",
        testbenches=[{"id": "tb", "maestro_point_root": "/LEAK_PATH/exports/tb", "virtuoso_library": "LEAK_PATH_lib",
                      "cell": "LEAK_PATH_cell", "test_name": "LEAK_PATH_test"}],
        corners=[{"id": "tt", "model_file": "/LEAK_PATH/models/tt.scs", "model_section": "tt",
                  "description": "LEAK_SPEC_TEXT typical"}],
        variables=[{"name": "A", "kind": "integer", "lower": "0", "upper": "9", "step": "1"},
                   {"name": "B", "kind": "continuous_step", "lower": "0", "upper": "1", "step": "0.1"},
                   {"name": "C", "kind": "integer", "lower": "1", "upper": "100", "step": "1"}],
        metrics=[{"name": "m1", "unit": "V", "expression": 'value(getData("/LEAK_EXPR_out") 1)'},
                 {"name": "m2", "unit": "Hz", "expression": 'bandwidth(VF("/LEAK_EXPR_net") 3 "low")'},
                 {"name": "spare", "unit": "1", "expression": "LEAK_EXPR_spare()"}],
        constraints=[{"metric": "m1", "op": "ge", "value": "5"}],
        objective={"direction": "minimize", "expression": "m2"})


def leaky_rows(spec):
    """60 points of the digest tests' run at corner tt. Every child names its directory under the canary; a failed one
    says ``SAID``; every point that ran holds an operating-point table with a quantity outside section 4's list, and every
    one but the start point and the best point an instance the digest never prints."""
    rows = []
    for i, (params, origin) in enumerate(abc_rows()[:60]):
        metrics = abc_metrics(params)
        where = f"LEAK_PATH/sims/obs_{i:04d}/tb/tt"
        child = (ChildResult(unit="tb", corner="tt", status="failed:spectre", issues=[SAID], sim_dir=where)
                 if metrics is None else
                 ChildResult(unit="tb", corner="tt", status="ok", metrics=metrics, sim_dir=where,
                             operating_points={"/M1": {"gm": 1e-3, "ids": 1e-4, "LEAK_EXPR_quantity": 1.0}}))
        agg = aggregate(spec, {"tb/tt": child})
        rows.append(Observation(obs_id=f"obs_{i:04d}", params=params, origin=origin, children={"tb/tt": child},
                                metrics=agg.metrics, fom=agg.fom, objective=agg.objective, feasible=agg.feasible,
                                constraint_penalty=agg.constraint_penalty, status=agg.status, issues=agg.issues,
                                spec_fingerprint=spec.fingerprint(), pipeline_fingerprint="p", step="optimize",
                                simulations=1, started_at="t", finished_at="t"))
    best = min((o for o in rows if o.feasible), key=lambda o: o.objective).obs_id
    shown = {best, rows[0].obs_id}                               # rows[0]: the start point, the design as exported
    for i, o in enumerate(rows):
        table = o.children["tb/tt"].operating_points
        if table is not None and o.obs_id not in shown:
            child = o.children["tb/tt"].model_copy(update={"operating_points": {**table, "/LEAK_PATH_x/M9": {"gm": 2e-3}}})
            rows[i] = o.model_copy(update={"children": {"tb/tt": child}})
    return rows


def advice_of(spec):
    """An adopted advice, and a refused one whose text as given carries the canary."""
    adopted = {"id": "a1", "event": "adopt", "at": "t", "since": 30, "author": "agent: made-up", "reason": "look near A=5",
               "start": [], "ranges": {"A": ["4", "7"]}, "fixed": {}, "vary": [], "spec_fingerprint": spec.fingerprint()}
    refused = advice_rules.refusal(spec, {"author": "agent: made-up", "reason": "LEAK_SPEC_TEXT copied from the netlist",
                                          "ranges": {"A": ["0", "20"]}},
                                   "ranges: A [0, 20] reaches outside the spec's range [0, 9]", [adopted], 40)
    return [adopted, refused]


def test_the_digest_carries_no_text_of_the_project_or_the_host():
    spec = leaky_spec()
    d = dg.digest(spec, leaky_rows(spec), advice=advice_of(spec))
    as_json, md = json.dumps(d, ensure_ascii=False, allow_nan=False), dg.markdown(d)
    for text in (as_json, md):
        assert [c for c in HIDDEN if c in text] == []
        assert [key for key in SIMULATOR if key in text] == []
    # the issue text: kept, cut to 200 characters (what followed the cut named a file of the host), and only there
    (said,) = d["failures"]["messages"]
    assert said["text"].startswith(f"tb/tt: {ISSUE}") and len(said["text"]) == dg.TEXT_LIMIT and said["text"].endswith("…")
    without = copy.deepcopy(d)
    without["failures"]["messages"] = []
    assert ISSUE not in json.dumps(without, ensure_ascii=False)
    assert [line for line in md.splitlines() if ISSUE in line] == [f"  - {said['count']} × `{said['text']}`"]
    # what the digest does carry of the tables and the advice: section 4's quantities, the refusal's message and id
    assert d["operating_points"]["best"]["children"]["tb/tt"] == {"/M1": {"ids": 1e-4, "gm": 1e-3}}
    assert d["advice_refused"] == [{"id": "r1", "since": 40,
                                    "reason": "ranges: A [0, 20] reaches outside the spec's range [0, 9]"}]


def test_the_files_the_block_writes_carry_none_either(tmp_path):
    spec = leaky_spec()
    store = RunStore(tmp_path)
    for row in advice_of(spec):
        advice_rules.append(store.root, row)
    path = analyze.digest(spec, leaky_rows(spec), store)
    for text in (path.read_text(encoding="utf-8"), path.with_suffix(".json").read_text(encoding="utf-8")):
        assert [c for c in HIDDEN if c in text] == [] and ISSUE in text


def test_a_library_device_s_rows_reach_the_digest_by_part_and_obs_id_never_by_path(tmp_path):
    """T18.2B: the library's root and every sNp path -- the row's in the library, its copy beside the point -- stay out of
    the digest; the rows are named by part and obs id. A point off the table (``failed:pick``) says which values, not
    where the library is."""
    import itertools

    from ic_opt import space
    from ic_opt.blocks.evaluate import evaluate
    from ic_opt.blocks.netlist import import_netlists
    from ic_opt.library import link
    from ic_opt.space import Point
    from ic_opt.spec import Spec
    from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
    from tests.ic_opt.library_device_fixtures import NETLIST, library_spec_dict, xfm_library
    from tests.ic_opt.test_blocks import maestro_export

    link.clear()
    try:
        root = xfm_library(tmp_path / "LEAK_PATH_library")
        export = maestro_export(tmp_path / "LEAK_PATH_exports", "tb", params="temperature=27 F=20")
        (export / "netlist" / "input.scs").write_text(NETLIST, encoding="utf-8")
        spec = Spec.model_validate(library_spec_dict(root, export=export))
        store = RunStore(tmp_path / "proj")
        executor = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0 + int(p["F"]) / 100})
        linked = link.resolve(spec)["xfmr"]
        points = [Point(p, "user") for p in itertools.islice(space.valid_points(spec), 8)]
        empty = next(c for c in itertools.product(range(16), range(16), range(6)) if c not in linked.table.cells)
        off = {n: space.format_value(space.parse_scalar(v.lower)[0] + k * space.parse_scalar(v.step)[0], space.parse_scalar(v.lower)[1])
               for n, k, v in zip(linked.names, empty, [next(v for v in spec.variables if v.name == n) for n in linked.names],
                                  strict=True)}
        points.append(Point({"F": "20", **off}, "user"))
        obs = evaluate(spec, points, executor, store, deck=import_netlists(spec, executor, store), limits=FAKE_HOST)
        assert obs[-1].status == "failed:pick" and all(o.status == "ok" for o in obs[:-1])
        assert any(p.suffix == ".s4p" for p in store.root.rglob("*.s4p"))            # the copies are there, beside the points
        d = dg.digest(spec, obs)
        assert d["library"]["top"] and all(p["rows"]["xfmr"]["part"] == "xfm" for p in d["library"]["top"])
        path = analyze.digest(spec, obs, store)
        for text in (json.dumps(d, ensure_ascii=False, allow_nan=False), dg.markdown(d), path.read_text(encoding="utf-8"),
                     path.with_suffix(".json").read_text(encoding="utf-8")):
            assert "LEAK_PATH" not in text and ".s4p" not in text and ".icopt" not in text and str(tmp_path) not in text
    finally:
        link.clear()
