"""The run digest (T17.1.5 specification, section 5.5): a made-up run of known structure, too little data, a store of
0.4.0, origins under advice, the order of the observations, the command, and the report without OpenBox."""

from __future__ import annotations

import itertools
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ic_opt import digest as dg
from ic_opt.blocks import analyze
from ic_opt.cli import app
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.sim.corner import aggregate
from ic_opt.space import split_origin
from ic_opt.store import RunStore
from ic_opt.suggesters.metric_gp import MetricGpSuggester
from tests.ic_opt.fakes import make_spec, minimal_spec

runner = CliRunner()


class OpChild(ChildResult):
    """A child as T17.5 will record it (section 4 of the specification): the field is not in this commit's base."""

    operating_points: dict[str, dict[str, float]] | None = None


def spec_abc(**extra):
    """A decides the constraint alone (m1 = A >= 5), B above 0.7 gives no value, the objective m2 grows with C (log
    scale, 1 .. 100): the best point lies at C's lower bound."""
    return make_spec(
        variables=[{"name": "A", "kind": "integer", "lower": "0", "upper": "9", "step": "1"},
                   {"name": "B", "kind": "continuous_step", "lower": "0", "upper": "1", "step": "0.1"},
                   {"name": "C", "kind": "integer", "lower": "1", "upper": "100", "step": "1"}],
        metrics=[{"name": "m1", "unit": "V", "expression": "m1()"}, {"name": "m2", "unit": "Hz", "expression": "m2()"},
                 {"name": "spare", "unit": "1", "expression": "spare()"}],
        constraints=[{"metric": "m1", "op": "ge", "value": "5"}],
        objective={"direction": "minimize", "expression": "m2"}, **extra)


def abc_metrics(p):
    a, b, c = int(p["A"]), float(p["B"]), int(p["C"])
    if b > 0.7:
        return None
    return {"m1": float(a), "m2": c + a / 100 + b / 1000, "spare": 1.0}


def observe(spec, rows, metrics_of, *, op_of=None, step="optimize"):
    """What the engine records: one child per point at the single condition, aggregated by the product's own rule."""
    out = Observations()
    for i, (params, origin) in enumerate(rows):
        metrics = metrics_of(params)
        op = op_of(params) if op_of else None
        child = (OpChild(unit="tb", status="failed:spectre", issues=["spectre did not finish"]) if metrics is None
                 else OpChild(unit="tb", status="ok", metrics=metrics, operating_points=op))
        agg = aggregate(spec, {"tb/nominal": child})
        out.append(Observation(obs_id=f"obs_{i:04d}", params=params, origin=origin, children={"tb/nominal": child},
                               metrics=agg.metrics, fom=agg.fom, objective=agg.objective, feasible=agg.feasible,
                               constraint_penalty=agg.constraint_penalty, status=agg.status, issues=agg.issues,
                               spec_fingerprint=spec.fingerprint(), pipeline_fingerprint="p", step=step, simulations=1,
                               started_at="t", finished_at="t"))
    return out


def abc_rows():
    """120 points: A in 6 levels x B in 5 x C in 4; the first is the start point, then an initial design of 19, then
    metric_gp batches of ten."""
    grid = [{"A": str(a), "B": b, "C": str(c)}
            for a, b, c in itertools.product((0, 2, 4, 5, 7, 9), ("0", "0.2", "0.5", "0.8", "1"), (1, 5, 20, 100))]
    random.Random(3).shuffle(grid)
    origins = ["start"] + ["suggest:metric_gp:init"] * 19
    origins += [f"suggest:metric_gp:tr:0:{20 + 10 * (i // 10)}" for i in range(len(grid) - 20)]
    return list(zip(grid, origins, strict=True))


OP = {"/M1": {"region": 2.0, "ids": 1e-4, "vgs": 0.6, "vds": 0.4, "gm": 1.2e-3, "gmoverid": 12.0, "cgs": 2e-15},
      "/I0/M3": {"region": 1.0, "ids": 2e-5, "gm": 3e-4}}


# -- 1. a made-up run of known structure --------------------------------------------------------------------------------


def test_every_entry_on_a_run_of_known_structure():
    spec = spec_abc()
    rows = observe(spec, abc_rows(), abc_metrics, op_of=lambda p: OP)
    d = dg.digest(spec, rows)

    assert d["digest_version"] == 2 and d["top"] == 5
    p = d["problem"]
    assert [(v["name"], v["levels"], v["scale"]) for v in p["variables"]] == [("A", 10, "linear"), ("B", 11, "linear"),
                                                                             ("C", 100, "log")]
    assert p["constraints"][0]["text"] == "m1 ≥ 5 V" and p["objective"] == {"direction": "minimize", "expression": "m2"}
    assert [m["modelled"] for m in p["metrics"]] == [True, True, False] and p["corners"] == ["nominal"]

    assert d["counts"] == {"points": 120, "by_status": {"constraint_failed": 36, "failed:spectre": 48, "ok": 36},
                           "simulations": 120, "per_step": {"optimize": 120}, "stopped_early": 0, "simulations_not_run": 0,
                           "stopped_at": {}, "notes": {}}

    prog = d["progress"]
    first = next(i for i, o in enumerate(rows) if o.feasible)
    assert prog["first_feasible"] == {"index": first + 1, "id": rows[first].obs_id}
    best = prog["best"]
    assert best["params"] == {"A": "5", "B": "0", "C": "1"} and best["objective"] == pytest.approx(1.05)
    assert best["metrics"]["m2"] == {"value": pytest.approx(1.05), "unit": "Hz"}
    assert len(prog["batches"]) == 12                          # 20 points in tens (no batch key), 10 batches of metric_gp
    assert [b["points"] for b in prog["batches"]] == list(range(10, 121, 10))
    assert prog["batches"][-1]["best"] == pytest.approx(1.05)
    bests = [b["best"] for b in prog["batches"] if b["best"] is not None]
    assert bests == sorted(bests, reverse=True)                 # minimize: the best only improves

    (c,) = d["constraints"]
    assert (c["scored"], c["met"], c["fails_only_this"]) == (72, 36, 36)
    assert c["spread"] == 5.0                                   # IQR of m1 over the points that have it: 7 - 2
    assert c["best_margin"] == {"id": best["id"], "margin": 0.0, "normalized": 0.0}
    assert c["closest_failing"]["margin"] == -1.0 and c["closest_failing"]["normalized"] == pytest.approx(-0.2)
    assert rows[[o.obs_id for o in rows].index(c["closest_failing"]["id"])].params["A"] == "4"

    a, b, cvar = d["variables"]
    assert (a["levels_visited"], b["levels_visited"], cvar["levels_visited"]) == (6, 5, 4)
    assert a["top_span"] == ["5", "7"] and b["top_span"] == ["0", "0.5"] and cvar["top_span"] == ["1", "1"]
    assert (a["at_bound"], b["at_bound"], cvar["at_bound"]) == (None, "lower", "lower")
    assert b["no_value"] == {"lower": {"points": 48, "no_value": 0, "share": 0.0},
                             "middle": {"points": 24, "no_value": 0, "share": 0.0},
                             "upper": {"points": 48, "no_value": 48, "share": 1.0}}
    assert a["no_value"]["lower"]["share"] == pytest.approx(0.4)   # A does not matter for failures: 2 of 5 B levels fail
    assert a["correlation"]["m1"] == pytest.approx(1.0) and b["correlation"]["m1"] == pytest.approx(0.0, abs=1e-12)
    assert cvar["correlation"]["m2"] > 0.9 and set(a["correlation"]) == {"m1", "m2"}   # modelled metrics only

    f = d["failures"]
    assert f["by_status"] == {"constraint_failed": 36, "failed:spectre": 48} and f["partial_points"] == 0
    (split,) = f["separating"]                                 # A and C split the failures evenly: no decrease
    assert (split["variable"], split["at_most"], split["from"]) == ("B", "0.5", "0.8")
    assert split["below"] == {"points": 72, "no_value": 0, "share": 0.0}
    assert split["above"] == {"points": 48, "no_value": 48, "share": 1.0}
    assert f["messages"] == [{"text": "tb/nominal: spectre did not finish", "count": 48}]

    ranges = {r["variable"]: r for r in d["suggested_ranges"]["ranges"]}
    assert ranges["A"]["suggested"] == ["4", "8"] and ranges["A"]["reaches_bound"] is None
    assert ranges["B"]["suggested"] == ["0", "0.6"] and ranges["B"]["reaches_bound"] == "lower"
    assert ranges["C"]["suggested"] == ["1", "2"] and ranges["C"]["reaches_bound"] == "lower"
    assert ranges["C"]["suggested_levels"] == 2 and d["suggested_ranges"]["points"] == 5

    s = d["strategy"]
    assert s["origins"] == {"metric_gp": 119, "start": 1} and s["metric_gp"]["design_points"] == 20
    state = MetricGpSuggester().region_state(spec, rows)
    assert s["metric_gp"]["region"]["index"] == state.index and s["metric_gp"]["region"]["side"] == state.length
    assert (s["metric_gp"]["region"]["successes"], s["metric_gp"]["region"]["failures"]) == (state.successes, state.failures)
    assert d["advice"] == []

    op = d["operating_points"]
    assert op["best"]["id"] == best["id"] and op["best"]["children"]["tb/nominal"]["/M1"]["gm"] == 1.2e-3
    assert op["start"]["id"] == "obs_0000" and list(op["best"]["children"]["tb/nominal"]) == ["/I0/M3", "/M1"]
    assert op["recorded"] == 72                                 # the points that ran; a failed child records none

    md = dg.markdown(d)
    assert md.startswith("# Run digest — demo\n\n120 points · constraint_failed 36 · failed:spectre 48 · ok 36")
    assert "| `m1 ≥ 5 V` | 36 / 72 | 36 | 0 V (+0 IQR) | -1 V (-0.2 IQR) `" in md
    assert "| B | 5 of 11 | 0 .. 0.5 | 0 .. 0.6 | lower | lower | 0/48 · 0/24 · 48/48 |" in md
    assert "| B | 0.5 | 0.8 | 0 / 72 (0%) | 48 / 48 (100%) |" in md
    assert "| /M1 | 2 | 100 µA | 600 mV | 400 mV | 1.2 mS | 12 1/V | 2 fF |" in md
    said = md.split("## What these numbers are not\n\n")[1]
    assert "It is not a cause" in said and "not where the optimum is" in said


def test_split_origin():
    assert split_origin("suggest:metric_gp:tr:0:40@a2") == ("suggest:metric_gp:tr:0:40", "a2")
    assert split_origin("start") == ("start", None) and split_origin("advice:a1") == ("advice:a1", None)
    assert dg.batch_key("suggest:metric_gp:tr:0:40@a2") == 40 and dg.batch_key("suggest:openbox_gp_eic:acq") is None
    assert dg.origin_source("suggest:turbo:tr:0:30@a1") == "turbo" and dg.origin_source("advice:a1") == "advice"


def test_a_spec_of_twelve_variables_and_eight_metrics_fits_in_200_lines():
    spec = make_spec(
        variables=[{"name": f"v{i}", "kind": "integer", "lower": "1", "upper": "40", "step": "1"} for i in range(12)],
        metrics=[{"name": f"m{j}", "unit": "V", "expression": f"m{j}()"} for j in range(8)],
        constraints=[{"metric": f"m{j}", "op": "gt", "value": "0.3"} for j in range(7)],
        objective={"direction": "minimize", "expression": "m7"})
    rng = random.Random(0)
    grid = [{f"v{i}": str(rng.randint(1, 40)) for i in range(12)} for _ in range(200)]
    grid.sort(key=lambda p: sum(int(v) for v in p.values()))       # the best improves in every batch: 20 progress rows
    rows = [(p, f"suggest:metric_gp:tr:0:{10 * (k // 10)}@a1" if k % 3 else f"suggest:metric_gp:tr:0:{10 * (k // 10)}")
            for k, p in enumerate(grid)]

    def metrics(p):
        x = [int(p[f"v{i}"]) / 40 for i in range(12)]
        if x[0] > 0.9:
            return None
        return {f"m{j}": x[j] + x[j + 1] / 2 for j in range(7)} | {"m7": 200 - sum(x) * 10}

    op = {f"/X{i}/M{j}": dict(OP["/M1"]) for i in range(3) for j in range(4)}
    advice = [{"id": "a1", "event": "adopt", "at": "t", "since": 20, "author": "someone", "reason": "narrower",
               "start": [], "ranges": {"v1": ["2", "30"]}, "fixed": {}, "vary": [], "spec_fingerprint": "s"}]
    d = dg.digest(spec, observe(spec, rows, metrics, op_of=lambda p: op), advice=advice)
    md = dg.markdown(d)
    assert len({b["best_id"] for b in d["progress"]["batches"]}) == 20 and d["failures"]["separating"]
    assert md.count("\n") <= 200, md.count("\n")


# -- 2. too little data -------------------------------------------------------------------------------------------------


def test_no_feasible_point_few_scored_points_and_no_observation():
    spec = spec_abc()
    rows = [(p, o) for p, o in abc_rows() if p["A"] in ("0", "2")][:12]
    few = observe(spec, rows, abc_metrics)
    d = dg.digest(spec, few)
    assert not any(o.feasible for o in few) and sum(o.status in dg.SCORED for o in few) < 10
    assert d["progress"]["best"] is None and d["progress"]["first_feasible"] is None
    assert d["progress"]["notes"]["best"] == "no feasible point in 12"
    assert d["suggested_ranges"]["ranges"] is None and "at least 3 feasible" in d["suggested_ranges"]["notes"]["ranges"]
    assert d["constraints"][0]["best_margin"] is None and d["constraints"][0]["closest_failing"] is not None
    assert all(v["correlation"] is None and v["top_span"] is None and v["at_bound"] is None for v in d["variables"])
    assert d["operating_points"]["best"] is None
    md = dg.markdown(d)
    assert "- best feasible point: no feasible point in 12" in md and "_needs at least 10 scored points_" in md

    empty = dg.digest(spec, [])
    assert empty["counts"]["points"] == 0 and empty["progress"]["batches"] == [] and empty["failures"]["separating"] is None
    assert empty["failures"]["notes"]["separating"] == "no observation" and empty["strategy"]["metric_gp"] is None
    json.dumps(empty, allow_nan=False)
    assert "0 points · none · 0 simulations" in dg.markdown(empty)


# -- 3. a store of 0.4.0 ------------------------------------------------------------------------------------------------


def test_a_store_of_040_gives_a_digest(tmp_path):
    """No advice file, no operating points, origins without suffix; one line as 0.2.0 wrote a child (``testbench``)."""
    spec = spec_abc()
    store = RunStore(tmp_path)
    rows = observe(spec, abc_rows()[:30], abc_metrics)
    lines = []
    for o in rows:
        row = json.loads(o.model_dump_json())
        for child in row["children"].values():
            child.pop("operating_points", None)
            child["testbench"] = child.pop("unit")
        lines.append(json.dumps(row))
    store.observations_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path = analyze.digest(spec, RunStore(tmp_path).observations(), store)
    d = json.loads((path.parent / "digest.json").read_text(encoding="utf-8"))
    assert d["counts"]["points"] == 30 and d["advice"] == [] and not (store.root / "advice.jsonl").exists()
    assert d["operating_points"]["best"]["children"] == {"tb/nominal": None}
    assert d["operating_points"]["start"]["children"] == {"tb/nominal": None}
    assert d["operating_points"]["recorded"] == 0
    section = path.read_text(encoding="utf-8").split("## Operating points of the best point")[1]
    assert "_No point of this run holds operating points" in section and "_none recorded for `" not in section


# -- 4. origins under advice --------------------------------------------------------------------------------------------


def test_origins_with_an_advice_suffix_count_under_their_advice_and_their_strategy():
    spec = spec_abc()
    base = abc_rows()[:80]
    rows = []
    for k, (p, origin) in enumerate(base):
        if 40 <= k < 60:                   # a1 in effect from 40: its start point, then a batch half under it
            origin = "advice:a1" if k == 40 else origin + ("@a1" if k % 2 else "")
        elif 60 <= k < 70:                 # a2 from 60, revoked at 70
            origin += "@a2"
        rows.append((p, origin))
    obs = observe(spec, rows, abc_metrics)
    advice = [
        {"id": "a1", "event": "adopt", "at": "t", "since": 40, "author": "someone", "reason": "look near A=5",
         "start": [dict(rows[40][0])], "ranges": {"A": ["4", "7"]}, "fixed": {}, "vary": [], "spec_fingerprint": "s"},
        {"id": "a2", "event": "adopt", "at": "t", "since": 60, "author": "agent: made-up", "reason": "hold C",
         "start": [], "ranges": {}, "fixed": {"C": "1"}, "vary": [], "spec_fingerprint": "s"},
        {"id": "a2", "event": "revoke", "at": "t", "since": 70, "reason": "did not help"},
    ]
    d = dg.digest(spec, obs, advice=advice)
    a1, a2 = d["advice"]
    assert a1["status"] == "superseded by a2" and a2["status"] == "revoked" and a2["revoke"]["reason"] == "did not help"
    assert a1["under"]["points"] == 10 and a2["under"]["points"] == 10
    assert [s["id"] for s in a1["start_points"]] == ["obs_0040"] and a1["start_points"][0]["status"] == obs[40].status
    under = [o for o in obs if split_origin(o.origin)[1] == "a1"]
    feasible = [o for o in under if o.feasible]
    assert a1["under"]["best"] == (min(o.fom for o in feasible) if feasible else None)
    assert (a1["period"], a2["period"]) == ([40, 60], [60, 70])
    assert a1["others"]["points"] == 60 - 40 - 11 and a2["others"]["points"] == 0     # the points after 70 are no one's
    assert d["strategy"]["origins"] == {"metric_gp": 78, "advice": 1, "start": 1}
    # the region replay reads the origins without their suffix: the digest's region is region_state's on stripped rows
    stripped = Observations(o.model_copy(update={"origin": split_origin(o.origin)[0]}) for o in obs)
    state = MetricGpSuggester().region_state(spec, stripped)
    region = d["strategy"]["metric_gp"]["region"]
    assert (region["index"], region["side"], region["successes"], region["failures"]) == state.state()
    # the suffix keeps a point in its batch, and the advice's start point joins the batch it was proposed first in
    assert [b["points"] for b in d["progress"]["batches"]] == [10, 20, 30, 40, 50, 60, 70, 80]
    md = dg.markdown(d)
    assert "| a1 | someone | 40 to 60 | superseded by a2 | start 1; ranges A 4..7 | 10 points, " in md
    assert "- a2: hold C" in md


# -- T17.3b: an advice's period, both sides counted, its start points, the best point at its bound ----------------------


def advised_run(spec, later=""):
    """120 points of known outcome. 0-39: the initial design and two batches, none feasible (A = 2). An advice a1 adopted
    at 40: three start points (40: feasible 10.05, the run's first feasible point; 41: feasible 20.06; 42: fails the
    constraint), then batches 40 to 70 whose odd points carry ``@a1`` and are none of them feasible (9 give no value, 10
    fail the constraint) and whose even points are feasible at 30.07 but for point 44, which gives no value. 80-119:
    batches 80 to 110, all feasible at 2.09 (A at the spec's upper bound, C = 2); ``later`` is appended to the odd ones."""
    rows = []
    starts = {40: {"A": "5", "B": "0", "C": "10"}, 41: {"A": "6", "B": "0", "C": "20"}, 42: {"A": "4", "B": "0", "C": "5"}}
    for k in range(120):
        origin = "suggest:metric_gp:init" if k < 20 else f"suggest:metric_gp:tr:0:{10 * (k // 10)}"
        if k < 40:
            params = {"A": "2", "B": "0", "C": "50"}
        elif k in starts:
            params, origin = starts[k], "advice:a1"
        elif k < 80 and k % 2:
            params = {"A": "3", "B": "0.9" if k % 4 == 1 else "0", "C": "40"}
            origin += "@a1"
        elif k < 80:
            params = {"A": "7", "B": "0.8" if k == 44 else "0", "C": "30"}
        else:
            params = {"A": "9", "B": "0", "C": "2"}
            origin += later if k % 2 else ""
        rows.append((params, origin))
    return observe(spec, rows, abc_metrics)


def adopt(ident, since, **parts):
    return {"id": ident, "event": "adopt", "at": "t", "since": since, "author": "someone", "reason": f"reason of {ident}",
            "start": [], "ranges": {}, "fixed": {}, "vary": [], "spec_fingerprint": "s", **parts}


def revoke(ident, since):
    return {"id": ident, "event": "revoke", "at": "t", "since": since, "reason": "did not help"}


A1_STARTS = [{"A": "5", "B": "0", "C": "10"}, {"A": "6", "B": "0", "C": "20"}, {"A": "4", "B": "0", "C": "5"}]


def test_an_advice_s_period_ends_at_its_revocation_or_the_next_adoption():
    spec = spec_abc()
    revoked = dg.digest(spec, advised_run(spec), advice=[adopt("a1", 40, start=A1_STARTS), revoke("a1", 80)])
    (a1,) = revoked["advice"]
    assert a1["period"] == [40, 80] and a1["status"] == "revoked"
    # the others are the 18 even points of batches 40 to 70; the 40 feasible points proposed after the revocation are not
    assert a1["others"] == {"points": 18, "feasible": 17, "no_value": 1, "best": pytest.approx(30.07)}

    superseded = dg.digest(spec, advised_run(spec, later="@a2"), advice=[adopt("a1", 40, start=A1_STARTS),
                                                                          adopt("a2", 80, ranges={"A": ["8", "9"]})])
    a1, a2 = superseded["advice"]
    assert a1["period"] == [40, 80] and a1["status"] == "superseded by a2" and a1["others"]["points"] == 18
    assert a2["period"] == [80, None] and a2["status"] == "in effect"
    assert a2["under"] == {"points": 20, "feasible": 20, "no_value": 0, "best": pytest.approx(2.09)}
    assert a2["others"] == {"points": 20, "feasible": 20, "no_value": 0, "best": pytest.approx(2.09)}
    json.dumps(superseded, allow_nan=False)


def test_both_sides_of_an_advice_are_counted_alike_and_its_start_points_are_told_apart():
    spec = spec_abc()
    d = dg.digest(spec, advised_run(spec), advice=[adopt("a1", 40, start=A1_STARTS), revoke("a1", 80)])
    (a1,) = d["advice"]
    assert a1["under"] == {"points": 19, "feasible": 0, "no_value": 9, "best": None}    # nothing feasible under it
    assert a1["others"] == {"points": 18, "feasible": 17, "no_value": 1, "best": pytest.approx(30.07)}
    assert set(a1) >= {"period", "under", "others", "start_points", "best_at_bound"}
    assert not set(a1) & {"points", "best", "others_since", "best_others_since"}
    # the best when evaluated; feasible but not the best; failing a constraint (its objective is not printed as a result)
    assert a1["start_points"] == [
        {"id": "obs_0040", "status": "ok", "objective": pytest.approx(10.05), "best_then": True},
        {"id": "obs_0041", "status": "ok", "objective": pytest.approx(20.06), "best_then": False},
        {"id": "obs_0042", "status": "constraint_failed", "objective": None, "best_then": False}]


def test_the_best_point_at_an_advice_s_bound():
    """The best point is obs_0080: A = 9 (the spec's upper bound), C = 2 (one level above the spec's lower bound)."""
    spec = spec_abc()
    rows = advised_run(spec)
    advice = [adopt("a1", 40, ranges={"C": ["2", "50"]}),        # at the advice's lower bound, not the spec's
              adopt("a2", 50, ranges={"C": ["1", "3"]}),         # inside
              adopt("a3", 60, ranges={"A": ["5", "9"]}),         # at a bound that is the spec's too
              adopt("a4", 70, ranges={"A": ["3", "6"]}),         # neither under it nor inside its ranges
              adopt("a5", 75, start=A1_STARTS)]                  # no ranges: no bound to be at
    d = dg.digest(spec, rows, advice=advice)
    assert d["progress"]["best"]["id"] == "obs_0080"
    assert [a["best_at_bound"] for a in d["advice"]] == [[{"variable": "C", "side": "lower", "value": "2"}], [], [],
                                                         None, None]
    # proposed under the advice: at its bound even though it lies outside another of its ranges
    under = [o.model_copy(update={"origin": o.origin + "@a1"}) if o.obs_id == "obs_0080" else o for o in rows]
    d = dg.digest(spec, under, advice=[adopt("a1", 40, ranges={"A": ["3", "6"], "C": ["2", "3"]})])
    assert d["advice"][0]["best_at_bound"] == [{"variable": "C", "side": "lower", "value": "2"}]
    assert dg.digest(spec, rows[:40], advice=advice)["advice"][0]["best_at_bound"] is None      # no feasible point


def test_what_the_unscored_points_said():
    spec = spec_abc()
    rows = observe(spec, abc_rows()[:60], abc_metrics)
    said = [["x", "y"]] * 5 + [["y", "z", "z"]] * 3 + [["w"]] * 2
    unscored = [o for o in rows if o.status not in dg.SCORED]
    assert len(unscored) >= len(said)
    texts = {o.obs_id: issues for o, issues in zip(unscored, said)}
    rows = [o.model_copy(update={"issues": texts.get(o.obs_id, [] if o.status not in dg.SCORED else ["x"])})
            for o in rows]
    messages = dg.digest(spec, rows)["failures"]["messages"]
    # counted once per point that holds a text; a scored point's issues are not counted; the three most frequent
    assert messages == [{"text": "y", "count": 8}, {"text": "x", "count": 5}, {"text": "z", "count": 3}]
    every = dg.digest(spec, [o for o in rows if o.status in dg.SCORED])
    assert every["failures"]["messages"] == [] and "said" not in dg.markdown(every).split("## What failed")[1]


def test_a_point_s_own_text_for_a_metric_a_child_explains_is_not_counted_twice():
    spec = spec_abc()
    rows = observe(spec, abc_rows()[:60], abc_metrics)
    unscored = [o for o in rows if o.status not in dg.SCORED]
    child, point = "tb/nominal: metric q failed: no_value:nil", "nominal: metric q missing or non-finite"
    alone = "nominal: metric r missing or non-finite"
    texts = {o.obs_id: [child, point, alone] for o in unscored[:4]} | {o.obs_id: [point] for o in unscored[4:6]}
    rows = [o.model_copy(update={"issues": texts.get(o.obs_id, [])}) if o.status not in dg.SCORED else o for o in rows]
    # with the child's text the point's own is the same cause; alone, or for another metric, it is counted
    assert dg.digest(spec, rows)["failures"]["messages"] == [
        {"text": alone, "count": 4}, {"text": child, "count": 4}, {"text": point, "count": 2}]


def test_the_block_reads_this_problem_s_rows_and_advice_only(tmp_path):
    from ic_opt import advice as advice_rules
    from ic_opt.blocks import analyze
    from ic_opt.store import RunStore

    spec = spec_abc()
    rows = observe(spec, abc_rows()[:60], abc_metrics)
    store = RunStore(tmp_path)
    other = [o.model_copy(update={"obs_id": f"old_{i:04d}", "spec_fingerprint": "the spec before an edit"})
             for i, o in enumerate(rows[:20])]
    ranges = {"A": [spec.variables[0].lower, spec.variables[0].upper]}
    fields = {"event": "adopt", "at": "t", "author": "a", "reason": "r", "start": [], "fixed": {}, "vary": []}
    advice_rules.append(store.root, {"id": "a1", "since": 10, "ranges": ranges, "spec_fingerprint": "the spec before an edit",
                                     **fields})
    advice_rules.append(store.root, {"id": "a2", "since": 30, "ranges": ranges, "spec_fingerprint": spec.fingerprint(),
                                     **fields})
    path = analyze.digest(spec, other + rows, store)
    d = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert d["counts"]["points"] == 60 and [a["id"] for a in d["advice"]] == ["a2"]


def test_the_markdown_of_an_advice_the_region_s_side_and_a_run_without_operating_points():
    from ic_opt.suggesters.metric_gp import region

    spec = spec_abc()
    d = dg.digest(spec, advised_run(spec), advice=[adopt("a1", 40, start=A1_STARTS, ranges={"C": ["2", "50"]}),
                                                    revoke("a1", 80)])
    md = dg.markdown(d)
    advice = md.split("## Advice\n\n")[1].split("\n## ")[0]
    assert ("| advice | given by | period | status | what | under it | others in its period | its start points |"
            in advice)
    assert ("| a1 | someone | 40 to 80 | revoked | start 3; ranges C 2..50 | 19 points, 0 feasible, 9 no value, best — "
            "| 18 points, 17 feasible, 1 no value, best 30.07 "
            "| `obs_0040` ok 10.05 (best so far), `obs_0041` ok 20.06, `obs_0042` constraint_failed |") in advice
    assert ("- the best point `obs_0080` lies at a1's bound, which is not the spec's: C = 2 (lower). Better points may "
            "lie beyond it; only a wider advice looks there.") in advice
    assert "- a1: reason of a1" in advice
    strategy = md.split("## Strategy\n\n")[1].split("\n## ")[0]
    assert "side × weight / 2 of the centre, in unit coordinates" in strategy and "of the unit cube" not in strategy
    assert "lies between 0.2 and 5; where side × weight reaches 2 the region holds every level" in strategy
    assert dg.REGION_WEIGHTS == region.WEIGHT_CLIP
    failures = md.split("## What failed and where\n\n")[1].split("\n## ")[0]
    assert "  - 10 × `tb/nominal: spectre did not finish`" in failures
    assert d["operating_points"]["recorded"] == 0
    assert md.split("## Operating points of the best point\n\n")[1].startswith(
        "_No point of this run holds operating points (`ic-opt doctor` says per testbench whether the netlist asks for "
        "them)._")
    without = dg.markdown(dg.digest(spec, advised_run(spec)))
    assert "## Advice\n\n_no advice given_" in without


# -- 5. the order of the observations -----------------------------------------------------------------------------------


def test_the_digest_does_not_depend_on_the_order_of_the_observations():
    spec = spec_abc()
    rows = observe(spec, abc_rows(), abc_metrics, op_of=lambda p: OP)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert json.dumps(dg.digest(spec, shuffled), sort_keys=True) == json.dumps(dg.digest(spec, rows), sort_keys=True)
    unpadded = [o.model_copy(update={"obs_id": f"obs_{int(o.obs_id[4:])}"}) for o in shuffled]   # obs_9 before obs_10
    assert dg.digest(spec, unpadded)["progress"]["first_feasible"]["index"] == dg.digest(spec, rows)["progress"]["first_feasible"]["index"]


# -- 6. the command -----------------------------------------------------------------------------------------------------


def project_with_run(tmp_path, n=40):
    root = tmp_path / "proj"
    root.mkdir()
    d = minimal_spec()
    spec = spec_abc()
    d.update(variables=[v.model_dump(mode="json") for v in spec.variables],
             metrics=[m.model_dump(mode="json", exclude_none=True) for m in spec.metrics],
             constraints=[c.model_dump(mode="json") for c in spec.constraints], objective=spec.objective.model_dump(mode="json"))
    (root / "spec.yaml").write_text(yaml.safe_dump(d), encoding="utf-8")
    store = RunStore(root)
    for o in observe(spec, abc_rows()[:n], abc_metrics):
        store.append(o)
    return root, store


def test_the_command_writes_both_files_prints_json_and_works_while_the_project_is_locked(tmp_path):
    root, store = project_with_run(tmp_path)
    result = runner.invoke(app, ["digest", str(root)])
    assert result.exit_code == 0, result.output
    md = (store.reports_dir() / "digest.md").read_text(encoding="utf-8")
    assert result.output == md and md.startswith("# Run digest — demo")
    as_json = runner.invoke(app, ["digest", str(root), "--json", "--top", "3"])
    d = json.loads(as_json.output)
    assert as_json.exit_code == 0 and d["top"] == 3 and d["counts"]["points"] == 40
    assert json.loads((store.reports_dir() / "digest.json").read_text(encoding="utf-8")) == d
    assert runner.invoke(app, ["digest", str(root), "--step", "nothing"]).output.startswith("# Run digest — demo\n\n0 points")
    # A run holds the project. The lock is an flock on its own open file: any other handle is refused, in this process
    # too, so a command that took it would fail here.
    with store.lock():
        with pytest.raises(RuntimeError, match="locked by another run"), store.lock():
            pass
        held = runner.invoke(app, ["digest", str(root)])
    assert held.exit_code == 0 and held.output == md
    assert not list(store.reports_dir().glob(".*.tmp"))


def test_the_command_refuses_an_invalid_spec(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "spec.yaml").write_text("project: demo\n", encoding="utf-8")
    result = runner.invoke(app, ["digest", str(root)])
    assert result.exit_code == 2 and "is not a valid spec" in result.output


# -- 7. the report without OpenBox --------------------------------------------------------------------------------------


def test_the_report_needs_no_openbox(tmp_path, monkeypatch):
    for name in [m for m in sys.modules if m == "openbox" or m.startswith("openbox.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "openbox", None)              # `import openbox` now raises ImportError
    spec = spec_abc()
    md = analyze.report(spec, observe(spec, abc_rows(), abc_metrics), RunStore(tmp_path)).read_text(encoding="utf-8")
    section = md.split("## Where the best points are\n\n")[1].split("\n## ")[0]
    assert "| B | 0 .. 1 | 0 .. 0.5 | 0 .. 0.6 | 7 of 11 | lower |" in section and "Space compression" not in md
    source = Path(analyze.__file__).read_text(encoding="utf-8")
    assert "import openbox" not in source and "from openbox" not in source
    few = analyze.report(spec, observe(spec, abc_rows()[:3], abc_metrics), RunStore(tmp_path / "few")).read_text(encoding="utf-8")
    assert "_needs at least 3 feasible points" in few


def test_the_digest_module_imports_no_optimizer_library():
    """numpy, scipy and ic-opt's core: not OpenBox, torch, BoTorch or scikit-learn (the metric_gp region, taken only when
    an origin names metric_gp, is the one exception), and no block."""
    spec = spec_abc()
    rows = observe(spec, [(p, "suggest:sobol:sobol") for p, _ in abc_rows()], abc_metrics)
    lines = "\n".join(o.model_dump_json() for o in rows)
    code = ("import json, sys\nfrom ic_opt import digest\nfrom ic_opt.observation import Observation\nfrom ic_opt.spec import Spec\n"
            f"spec = Spec.model_validate(json.loads({spec.model_dump_json()!r}))\n"
            f"rows = [Observation.model_validate_json(line) for line in {lines!r}.splitlines()]\n"
            "text = digest.markdown(digest.digest(spec, rows))\n"
            "print(sorted(m for m in ('openbox', 'torch', 'botorch', 'sklearn', 'ic_opt.blocks', 'ic_opt.suggesters') "
            "if m in sys.modules))\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
    assert out.stdout.strip() == "[]", out.stdout + out.stderr
