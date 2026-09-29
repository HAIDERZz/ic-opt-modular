"""T17.11 (``docs/refactor/T17_11_SATURATION_MARGIN_SPEC.md``, section 4): a metric read from the operating points -- the
worst saturation margin of named transistors, the smallest ``|vds| - |vdsat|`` over them.

Every netlist and operating-point table here is made up: made-up models, instance names and values."""

from __future__ import annotations

import copy
import json

import pytest

from ic_opt import objective
from ic_opt.blocks import analyze
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.stage import StageContext, pipeline_fingerprint
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult
from ic_opt.sim import ocean
from ic_opt.sim.ocean import Scalars
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages import spectre_pipeline
from ic_opt.stages.spectre_chain import Extract
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec, minimal_spec
from tests.ic_opt.test_operating_points import WITHOUT_DC, WITHOUT_STATEMENT
from tests.ic_opt.test_schedule import bench, child, row

SAT = {"name": "SAT_MARGIN", "unit": "V", "testbench": "dc", "saturation_margin": {"instances": ["M1", "M2"]}}


def sat_spec(**overrides) -> dict:
    """Two testbenches: ``tb`` gives NF by an OCEAN expression, ``dc`` the saturation margin of M1 and M2 from its
    operating points. NF < 9 dB and SAT_MARGIN >= 0.05 V (a constraint's number takes no SI prefix: "50m V"
    reads as 50 V); minimize NF."""
    d = minimal_spec(testbenches=[bench("tb"), bench("dc")],
                     metrics=[{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"}, copy.deepcopy(SAT)],
                     constraints=[{"metric": "NF", "op": "lt", "value": "9 dB"},
                                  {"metric": "SAT_MARGIN", "op": "ge", "value": "0.05 V"}],
                     budget={"max_simulations": 1000})
    d.update(overrides)
    return d


def with_sat(**changes) -> dict:
    """``sat_spec()`` with its saturation metric changed: a value None removes that key."""
    d = sat_spec()
    for key, value in changes.items():
        if value is None:
            d["metrics"][1].pop(key, None)
        else:
            d["metrics"][1][key] = value
    return d


# -- 1. the spec --------------------------------------------------------------------------------------------------------


def test_the_good_form_loads():
    spec = Spec.model_validate(sat_spec())
    metric = spec.metrics[1]
    assert metric.saturation_margin.instances == ["M1", "M2"] and metric.testbench == "dc" and metric.expression is None
    assert spec.metrics_for("dc") == [] and [m.name for m in spec.metrics_for("tb")] == ["NF"]    # OCEAN's metrics only
    assert spec.problem()["metrics"][1] == {"name": "SAT_MARGIN", "unit": "V", "testbench": "dc", "required_signals": [],
                                            "saturation_margin": {"instances": ["M1", "M2"]}}
    names = Spec.model_validate(with_sat(saturation_margin={"instances": [" /I0/M3 ", "M\\<0\\>"]})).metrics[1]
    assert names.saturation_margin.instances == ["/I0/M3", "M\\<0\\>"]      # any name the table uses, stripped


@pytest.mark.parametrize(("changes", "message"), [
    ({"expression": "x()"}, "metric SAT_MARGIN: a saturation_margin metric takes no expression"),
    ({"device": "xfmr"}, "metric SAT_MARGIN: a saturation_margin metric takes no device"),
    ({"quantity": "Lp", "device": "xfmr"}, "metric SAT_MARGIN: a saturation_margin metric takes no device, quantity"),
    ({"testbench": None}, "metric SAT_MARGIN: a saturation_margin metric must name its testbench"),
    ({"saturation_margin": {"instances": []}}, "metric SAT_MARGIN: saturation_margin needs at least one instance"),
    ({"saturation_margin": {"instances": ["M1", "M2", "M1"]}},
     r"metric SAT_MARGIN: saturation_margin instances must be distinct \(M1 twice\)"),
    ({"saturation_margin": {"instances": ["M1", " "]}}, "metric SAT_MARGIN: saturation_margin instances must be non-empty"),
    ({"saturation_margin": {}}, r"saturation_margin\.instances\s+Field required"),
    ({"saturation_margin": {"instances": ["M1"], "vds": "0.1"}}, "Extra inputs are not permitted"),
    ({"testbench": "ac"}, "metric SAT_MARGIN references unknown testbench ac"),
])
def test_what_is_refused(changes, message):
    with pytest.raises(ValueError, match=message):
        Spec.model_validate(with_sat(**changes))


def test_it_names_its_testbench_in_a_spec_of_one_testbench_too():
    """An expression metric of a one-testbench spec is given that testbench; a saturation metric states it (section 2:
    ``testbench`` present), and then loads."""
    alone = {k: v for k, v in SAT.items() if k != "testbench"}
    with pytest.raises(ValueError, match="must name its testbench"):
        make_spec(metrics=[minimal_spec()["metrics"][0], alone])
    spec = make_spec(metrics=[minimal_spec()["metrics"][0], {**alone, "testbench": "tb"}])
    assert spec.metrics[1].testbench == "tb" and spec.metrics[0].testbench == "tb"


def test_operating_points_off_is_refused_at_load():
    d = sat_spec()
    d["simulator"]["operating_points"] = False
    with pytest.raises(ValueError, match="SAT_MARGIN reads the operating points; set simulator.operating_points true"):
        Spec.model_validate(d)
    d["metrics"] = d["metrics"][:1]
    d["constraints"] = d["constraints"][:1]
    assert Spec.model_validate(d).simulator.operating_points is False           # without the metric: as before


def test_the_metric_is_part_of_the_problem_and_a_spec_without_it_keeps_its_identity():
    """The fingerprint changes as for any metric; a spec without the field keeps its dump and both its fingerprints
    (pinned in ``test_operating_points``: the new field stays out of the legacy dump while unset)."""
    spec = make_spec()
    assert (spec.fingerprint(), spec._legacy_fingerprint()) == ("ba0f5751e8b31248", "1d1cf8fb28678108")
    assert "saturation_margin" not in json.dumps(spec.model_dump(mode="json"))
    with_it = Spec.model_validate(sat_spec())
    without = Spec.model_validate(sat_spec(metrics=sat_spec()["metrics"][:1], constraints=sat_spec()["constraints"][:1]))
    other = Spec.model_validate(with_sat(saturation_margin={"instances": ["M1", "M2", "M7"]}))
    assert len({without.fingerprint(), with_it.fingerprint(), other.fingerprint()}) == 3
    assert without._legacy_fingerprint() != with_it._legacy_fingerprint()
    assert Spec.model_validate(json.loads(json.dumps(with_it.model_dump(mode="json")))) == with_it     # round trip


# -- 2. the value -------------------------------------------------------------------------------------------------------

NMOS = {"gm": 1.25e-3, "region": 2.0, "vds": 0.6, "vdsat": 0.12}         # 0.48 V beyond its saturation voltage
PMOS = {"gm": 9.0e-4, "region": 2.0, "vds": -0.5, "vdsat": -0.2}         # a PMOS's table: negative voltages, 0.3 V
TRIODE = {"gm": 2.0e-3, "region": 1.0, "vds": 0.02, "vdsat": 0.15}      # a switch: in the table, not in the metric
TABLE = {"/M1": NMOS, "/M2": PMOS, "/S1": TRIODE, "/M3": {**NMOS, "vds": 0.25}}    # M3: 0.13 V


def dc_spec() -> Spec:
    """``sat_spec`` whose DC testbench also gives a current by OCEAN, and a second saturation metric, over M3."""
    d = sat_spec()
    d["metrics"] += [{"name": "IDD", "unit": "A", "expression": 'IDC("/V0/PLUS")', "testbench": "dc"},
                     {"name": "SAT_TAIL", "unit": "V", "testbench": "dc", "saturation_margin": {"instances": ["M3"]}}]
    return Spec.model_validate(d)


def extract(tmp_path, table, *, unit="dc") -> ChildResult:
    """The extract stage on OCEAN's scalars for ``unit`` (IDD = 1 mA) and ``table`` written as the replay script writes
    ``oppoints.tsv``; ``table`` None: no file (no operating points asked for), ``{}``: an empty one (none in the results)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = None
    if table is not None:
        path = tmp_path / "oppoints.tsv"
        rows = [f"{name}\t{q}\t{v!r}" for name, quantities in table.items() for q, v in quantities.items()]
        path.write_text("".join(f"{r}\n" for r in (["instance\tquantity\tvalue", *rows] if rows else [])))
    ctx = StageContext(spec=dc_spec(), executor=LocalExecutor(tmp_path), store=RunStore(tmp_path), obs_id="obs_0001",
                       workdir=tmp_path, remote_dir=str(tmp_path), unit=unit)
    rows = {"IDD": ocean.ScalarRow(1e-3, "A", "pass"), "NF": ocean.ScalarRow(8.0, "dB", "pass")}
    return Extract().run(Scalars(rows, {}, 1, oppoints=path), ctx)


def test_the_value_is_the_smallest_margin_of_nmos_and_pmos_alike():
    assert ocean.saturation_margin(TABLE, ["M1", "M2"]) == (pytest.approx(0.3), [])       # the PMOS, by absolute values
    assert ocean.saturation_margin(TABLE, ["/M1"]) == (pytest.approx(0.48), [])
    assert ocean.saturation_margin(TABLE, ["M1", "M2", "M3"]) == (pytest.approx(0.13), [])
    assert ocean.saturation_margin(TABLE, ["S1"]) == (pytest.approx(-0.13), [])          # listed, a switch reads negative
    # the name as the table has it, or with the leading "/" added or taken off (the table: /M1; a spec: M1)
    assert ocean.saturation_margin({"M1": NMOS, "I0/M4": PMOS}, ["/M1", "/I0/M4"]) == (pytest.approx(0.3), [])


def test_what_the_extract_stage_gives(tmp_path):
    child = extract(tmp_path, TABLE)
    assert child.status == "ok" and child.issues == [] and child.operating_points == TABLE
    assert child.metrics == {"IDD": 1e-3, "SAT_MARGIN": pytest.approx(0.3), "SAT_TAIL": pytest.approx(0.13)}
    other = extract(tmp_path / "tb", TABLE, unit="tb")             # the other testbench: its own metrics, none of these
    assert other.metrics == {"NF": 8.0} and other.status == "ok"


@pytest.mark.parametrize(("table", "missing"), [
    ({"/M1": NMOS, "/M3": TABLE["/M3"]}, "M2"),                                              # not in the table
    ({"/M1": NMOS, "/M2": {k: v for k, v in PMOS.items() if k != "vdsat"}, "/M3": TABLE["/M3"]}, "M2"),   # no vdsat
    ({"/M1": {k: v for k, v in NMOS.items() if k != "vds"}, "/M3": TABLE["/M3"]}, "M1, M2"),   # one issue names both
])
def test_a_transistor_without_its_operating_point_fails_the_metric_and_keeps_the_others(tmp_path, table, missing):
    child = extract(tmp_path, table)
    assert child.status == "metric_failed" and "SAT_MARGIN" not in child.metrics
    assert child.issues == [f"metric SAT_MARGIN failed: no operating point for {missing}"]
    assert child.metrics == {"IDD": 1e-3, "SAT_TAIL": pytest.approx(0.13)}                 # the other metrics stay


@pytest.mark.parametrize("table", [None, {}], ids=["no file", "an empty file"])
def test_no_operating_points_at_all_fail_the_metric_the_same_way(tmp_path, table):
    child = extract(tmp_path, table)
    assert child.status == "metric_failed" and child.operating_points is None and child.metrics == {"IDD": 1e-3}
    assert child.issues == ["metric SAT_MARGIN failed: no operating point for M1, M2",
                            "metric SAT_TAIL failed: no operating point for M3"]


# -- 3. the OCEAN script -------------------------------------------------------------------------------------------------


def test_the_script_holds_the_expressions_only():
    spec = dc_spec()
    paths = {"psf_dir": "psf", "scalars_file": "metrics/s.tsv", "waveform_dir": "metrics/w", "oppoint_result": "dcOpInfo",
             "oppoints_file": "metrics/oppoints.tsv"}
    script = ocean.replay_script(spec.metrics_for("dc"), [], **paths)
    assert "; metric: IDD" in script and "SAT_" not in script and "icoptOpResult" in script    # the table is still read
    assert ocean.replay_script([m for m in spec.metrics if m.testbench == "dc"], [], **paths) == script


# -- 4. end to end through sim.evaluate ------------------------------------------------------------------------------------


def m2_at(f: str) -> dict[str, float] | None:
    """M2's operating point at F: 0.3 V of margin at F=22, 30 mV (below the 50 mV constraint) at F=20 and F=24; at F=26
    the table has no row for it."""
    return {"20": {**PMOS, "vds": -0.23}, "24": {**PMOS, "vds": -0.23}, "26": None}.get(f, PMOS)


def fake_host(store: RunStore) -> FakeSpectreExecutor:
    """OCEAN gives NF = 8 dB on tb and nothing on dc; the operating points of either are M1 and M2 as ``m2_at`` says."""
    def table(params, tb, corner):
        m2 = m2_at(params["F"])
        return {"/M1": NMOS, **({"/M2": m2} if m2 else {})}
    return FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0} if tb == "tb" else {}, oppoints_fn=table)


DECK = Deck(templates={("tb", None): WITHOUT_DC, ("dc", None): WITHOUT_STATEMENT})    # tb: an AC analysis; dc: a DC one


def at(*fs: str) -> list[Point]:
    return [Point({"F": f, "W": "0.6u"}, "user") for f in fs]


def test_the_observation_holds_both_kinds_of_metric(tmp_path, capsys):
    spec = Spec.model_validate(sat_spec())
    store = RunStore(tmp_path / "proj")
    good, lost = evaluate(spec, at("22", "26"), fake_host(store), store, deck=DECK, limits=FAKE_HOST)
    assert good.status == "ok" and good.feasible and list(good.children) == ["tb/nominal", "dc/nominal"]
    assert good.metrics == {"NF": 8.0, "SAT_MARGIN": pytest.approx(0.3)} and good.objective == 8.0
    assert good.children["dc/nominal"].metrics == {"SAT_MARGIN": pytest.approx(0.3)}
    probe = {tb: (tmp_path / "proj" / good.children[f"{tb}/nominal"].sim_dir / "metrics" / "probe.ocn").read_text()
             for tb in ("tb", "dc")}
    assert "; metric: NF" in probe["tb"] and "; metric:" not in probe["dc"] and "SAT_MARGIN" not in probe["dc"]

    # F=26: the table has no M2 -- the point is metric_failed, NF stays, the issue names M2, the batch line counts it
    assert lost.status == "metric_failed" and lost.metrics == {"NF": 8.0} and not lost.feasible
    assert lost.children["dc/nominal"].issues == ["metric SAT_MARGIN failed: no operating point for M2"]
    assert "dc/nominal: metric SAT_MARGIN failed: no operating point for M2" in lost.issues
    assert ("[evaluate] step='evaluate': metric SAT_MARGIN failed on 1 of 2 points (no operating point for M2 1)"
            in capsys.readouterr().out)


def test_the_schedule_runs_the_dc_testbench_first_and_stops_the_point_there(tmp_path):
    spec = Spec.model_validate(sat_spec())             # the spec's order: tb, then dc
    store = RunStore(tmp_path / "spec_order")
    first, = evaluate(spec, at("20"), fake_host(store), store, deck=DECK, limits=FAKE_HOST, stop_at_first_failure=True)
    assert list(first.children) == ["tb/nominal", "dc/nominal"] and first.not_run == []    # no history: tb ran first
    assert first.status == "constraint_failed" and first.issues == ["nominal: SAT_MARGIN ge 0.05 V violated by 0.03"]

    store = RunStore(tmp_path / "learned")
    for i in range(10):                                 # dc failed on every recorded point, in a fifth of tb's time
        recorded = row(spec, i, child("tb", NF=8.0, seconds=1.0), child("dc", SAT_MARGIN=0.01, seconds=0.2),
                       status="constraint_failed")
        store.append(recorded.model_copy(update={"params": {"F": str(20 + 2 * (i % 6)), "W": "1.2u" if i < 6 else "1.0u"}}))
    host = fake_host(store)
    stopped, passed = evaluate(spec, at("24", "22"), host, store, deck=DECK, limits=FAKE_HOST, stop_at_first_failure=True)
    assert list(stopped.children) == ["dc/nominal"] and stopped.not_run == ["tb/nominal"] and stopped.simulations == 1
    assert stopped.status == "constraint_failed" and stopped.metrics == {"SAT_MARGIN": pytest.approx(0.03)}
    assert stopped.issues == ["nominal: SAT_MARGIN ge 0.05 V violated by 0.03",
                              "not simulated: 1 of 2 children (stopped after dc/nominal)"]
    assert passed.status == "ok" and passed.not_run == [] and list(passed.children) == ["tb/nominal", "dc/nominal"]
    assert sum(c.startswith("spectre ") for c in host.commands) == 3                    # 1 for the stopped point, 2 for the other


def test_a_point_recorded_without_the_metric_is_simulated_again(tmp_path):
    """Section 2, the cache: the spec's fingerprint separates the two (the metric is part of the problem); the pipeline's
    is the same, so a spec without the metric reuses what it recorded before."""
    with_it = Spec.model_validate(sat_spec())
    without = Spec.model_validate(sat_spec(metrics=sat_spec()["metrics"][:1], constraints=sat_spec()["constraints"][:1]))
    assert pipeline_fingerprint(spectre_pipeline(with_it, DECK)) == pipeline_fingerprint(spectre_pipeline(without, DECK))
    store = RunStore(tmp_path / "proj")
    host = fake_host(store)
    old, = evaluate(without, at("22"), host, store, deck=DECK, limits=FAKE_HOST)
    assert old.metrics == {"NF": 8.0} and old.children["dc/nominal"].operating_points is not None
    new, = evaluate(with_it, at("22"), host, store, deck=DECK, limits=FAKE_HOST)
    assert new.obs_id != old.obs_id and new.metrics == {"NF": 8.0, "SAT_MARGIN": pytest.approx(0.3)}
    assert sum(c.startswith("spectre ") for c in host.commands) == 4                  # simulated again
    again, = evaluate(with_it, at("22"), host, store, deck=DECK, limits=FAKE_HOST)
    assert again.obs_id == new.obs_id and sum(c.startswith("spectre ") for c in host.commands) == 4     # reused


# -- 5. the digest and the report -------------------------------------------------------------------------------------------


def test_the_digest_and_the_report_list_it_with_its_unit(tmp_path):
    spec = Spec.model_validate(sat_spec())
    store = RunStore(tmp_path / "proj")
    obs = evaluate(spec, at("22", "20", "26"), fake_host(store), store, deck=DECK, limits=FAKE_HOST)
    analyze.digest(spec, obs, store)
    d = json.loads((store.reports_dir() / "digest.json").read_text(encoding="utf-8"))
    assert {"name": "SAT_MARGIN", "unit": "V", "modelled": True} in d["problem"]["metrics"]
    assert d["progress"]["best"]["metrics"]["SAT_MARGIN"] == {"value": pytest.approx(0.3), "unit": "V"}
    assert d["failures"]["messages"] == [{"text": "dc/nominal: metric SAT_MARGIN failed: no operating point for M2",
                                          "count": 1}]                  # the point's own "missing" line is the same cause
    md = (store.reports_dir() / "digest.md").read_text(encoding="utf-8")
    assert "| NF | SAT_MARGIN |" in md and "| 8 dB | 300 mV |" in md and "SAT_MARGIN ≥ 50 mV" in md

    report = analyze.report(spec, obs, store).read_text(encoding="utf-8")
    assert "- metrics: NF=8 dB, SAT_MARGIN=300 mV" in report
    assert "- binding constraints: SAT_MARGIN ≥ 50 mV (1 of 3)" in report


# -- the documents ------------------------------------------------------------------------------------------------------------


def test_the_constraint_is_written_in_volts_as_the_documents_say():
    """The skills and the README write the constraint ``ge 0.05 V``: a constraint's number takes no SI prefix, so the
    specification's ``50m V`` reads as 50 V and fails every point (``objective`` keeps the number, drops the unit)."""
    volts = Spec.model_validate(sat_spec())
    prefixed = Spec.model_validate(sat_spec(constraints=[{"metric": "SAT_MARGIN", "op": "ge", "value": "50m V"}]))
    assert objective.constraint_violations(volts, {"NF": 8.0, "SAT_MARGIN": 0.3}) == (0.0, [])
    assert objective.constraint_violations(prefixed, {"SAT_MARGIN": 0.3})[1] == ["SAT_MARGIN ge 50m V violated by 0.3"]
