"""T17.5: operating points of every transistor, read from the Spectre results and kept with each observation.

Every netlist here is made up: made-up models (``nfet`` / ``pfet``), instance names and values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ic_opt.blocks.doctor import doctor
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.stage import StageContext, pipeline_fingerprint
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult, Observation
from ic_opt.sim import netlist, ocean
from ic_opt.sim.ocean import Scalars
from ic_opt.site import HostLimits
from ic_opt.space import Point
from ic_opt.spec import Metric
from ic_opt.stages import spectre_pipeline
from ic_opt.stages.spectre_chain import Extract
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec

HEAD = "simulator lang=spectre\ninclude \"models.scs\" section=tt\nparameters temperature=27 F={{F}} W={{W}}\n" \
       "M1 (d g 0 0) nfet w=W nf=F\nM2 (d g vdd vdd) pfet w=W\nR0 (d out) resistor r=1k\n"
DC = 'dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status\n'
INFO = "dcOpInfo info what=oppoint where=rawfile\n"
TAIL = "ac ac start=1k stop=1G dec=10\nsaveOptions options save=allpub\n"
WITH_STATEMENT = HEAD + DC + INFO + TAIL          # 7.1 case 1: the export asks for them
WITHOUT_STATEMENT = HEAD + DC + TAIL              # case 2: a DC analysis, no statement
WITHOUT_DC = HEAD + TAIL                          # case 3: neither
TABLE = {
    "/M1": {"gm": 1.25e-3, "region": 2.0, "ids": 1.0e-4, "vgs": 0.55, "vds": 0.6, "vbs": 0.0, "vth": 0.41,
            "vdsat": 0.12, "gds": 2.0e-5, "gmoverid": 12.5, "cgs": 3.0e-15, "cgd": 1.0e-15},
    "/M2": {"gm": 9.0e-4, "region": 2.0, "vth": -0.43},
}


def lines_added(before: str, after: str) -> list[str]:
    """The lines of ``after`` that are not in ``before``, when ``after`` is ``before`` with lines inserted (else fails)."""
    a, b = before.splitlines(), after.splitlines()
    added = []
    i = 0
    for line in b:
        if i < len(a) and line == a[i]:
            i += 1
        else:
            added.append(line)
    assert i == len(a), "the original lines are not all there, in order"
    return added


# -- recognising the statement ---------------------------------------------------------------------------------------

def test_the_four_cases_of_a_netlist():
    assert netlist.operating_points(WITH_STATEMENT).mode == "export"
    assert netlist.operating_points(WITH_STATEMENT).result == "dcOpInfo"
    plan = netlist.operating_points(WITHOUT_STATEMENT)
    assert (plan.mode, plan.result, plan.after) == ("statement", "icoptOpInfo", "dcOp")
    assert netlist.operating_points(WITHOUT_DC).mode == "analysis"


@pytest.mark.parametrize("statement", [
    "dcOpInfo info where=rawfile what=oppoint\n",                           # any order
    'dcOpInfo info what="oppoint" where = "rawfile"\n',                    # quoted, spaces around =
    "dcOpInfo info what=oppoint \\\n    where=rawfile  // ADE writes this\n",   # continued, comment at the end
])
def test_the_statement_is_recognised_in_any_form(statement):
    plan = netlist.operating_points(HEAD + DC + statement + TAIL)
    assert (plan.mode, plan.result) == ("export", "dcOpInfo")


@pytest.mark.parametrize("text", [
    HEAD + DC + "// dcOpInfo info what=oppoint where=rawfile\n" + TAIL,             # commented out
    HEAD + DC + "* dcOpInfo info what=oppoint where=rawfile\n" + TAIL,              # a * comment line
    HEAD + DC + "dcOpInfo info what=oppoint where=logfile\n" + TAIL,                # not into the results
    HEAD + DC + "dcOpInfo info what=models where=rawfile\n" + TAIL,                 # not the operating point
    HEAD + DC + "tran tran stop=1n\nfinalTimeOP info what=oppoint where=rawfile\n",   # after tran: its final time point
    HEAD + DC + "subckt blk (a)\n  xOpInfo info what=oppoint where=rawfile\nends blk\n",   # inside a subckt
    HEAD + DC + "swp sweep param=temp values=[0 50] {\n  dcIn dc\n  inOpInfo info what=oppoint where=rawfile\n}\n",
])
def test_what_is_not_the_statement(text):
    plan = netlist.operating_points(text)
    assert plan.mode == "statement" and plan.after == "dcOp"


def test_what_is_not_a_dc_analysis():
    sweep = HEAD + "dcSwp dc param=temperature start=0 stop=100 step=10\n" + TAIL
    nested = HEAD + "subckt blk (a)\n  dcIn dc\nends blk\n" + "inline subckt blk2 (a)\n  dcIn2 dc\nends blk2\n" + TAIL
    source = HEAD + "V0 dc 0 vsource dc=1\n" + TAIL          # an instance whose first node is called dc
    library = HEAD + "library lib\nsection tt\n  dcL dc\nendsection tt\nendlibrary lib\n" + TAIL
    spice = HEAD + "simulator lang=spice\n.op\nsimulator lang=spectre\n" + TAIL
    for text in (sweep, nested, source, library, spice):
        assert netlist.operating_points(text).mode == "analysis", text
    after_sweep_info = HEAD + "dcSwp dc param=temperature start=0 stop=100 step=10\nswpInfo info what=oppoint where=rawfile\n"
    assert netlist.operating_points(after_sweep_info).mode == "analysis"


# -- what is added -----------------------------------------------------------------------------------------------------

def test_the_statement_goes_right_after_the_dc_analysis_and_nothing_else_changes():
    text, plan = netlist.with_operating_points(WITHOUT_STATEMENT)
    assert lines_added(WITHOUT_STATEMENT, text) == [
        "// ic-opt: operating points of dcOp (simulator.operating_points: false leaves them out)",
        "icoptOpInfo info what=oppoint where=rawfile",
    ]
    assert text == HEAD + DC + "".join(f"{line}\n" for line in plan.added) + TAIL
    assert netlist.operating_points(text).mode == "export"          # rendering it again adds nothing
    assert netlist.with_operating_points(text)[0] == text


def test_a_dc_analysis_and_the_statement_are_appended_at_the_end():
    text, plan = netlist.with_operating_points(WITHOUT_DC)
    assert text.startswith(WITHOUT_DC)
    assert text[len(WITHOUT_DC):].splitlines() == [
        ("// ic-opt: operating points, from a DC analysis run after the netlist's analyses (simulator.operating_points: "
         "false leaves them out)"),
        "icoptDcOp dc",
        "icoptOpInfo info what=oppoint where=rawfile",
    ]
    assert netlist.operating_points(text).result == "icoptOpInfo"
    no_newline, _ = netlist.with_operating_points(WITHOUT_DC.rstrip("\n"))
    assert no_newline == text
    spice_end = WITHOUT_DC + "simulator lang=spice\nr1 a b 1k\n"
    text, plan = netlist.with_operating_points(spice_end)
    assert plan.spice_at_end and text[len(spice_end):].splitlines()[1:] == [
        "simulator lang=spectre", "icoptDcOp dc", "icoptOpInfo info what=oppoint where=rawfile"]


def test_an_export_that_asks_for_them_is_left_as_it_is():
    assert netlist.with_operating_points(WITH_STATEMENT)[0] == WITH_STATEMENT


# -- the replay script ---------------------------------------------------------------------------------------------------

METRICS = [Metric(name="GAIN", unit="dB", expression="ymax(db(VF))", result="ac", testbench="tb")]


def test_the_script_reads_them_after_the_metrics_file_is_closed():
    plain = ocean.replay_script(METRICS, [], psf_dir="psf", scalars_file="metrics/s.tsv", waveform_dir="metrics/w")
    script = ocean.replay_script(METRICS, [], psf_dir="psf", scalars_file="metrics/s.tsv", waveform_dir="metrics/w",
                                 oppoint_result="dcOpInfo", oppoints_file="metrics/oppoints.tsv")
    assert "operating points" not in plain
    head, _, part = script.partition("close(out)\n")
    assert plain == head + "close(out)\nexit()\n"                     # the metrics part is what it was
    assert part.endswith("exit()\n") and "(out " not in part and "(out)" not in part     # never touches the metrics file
    assert "when(errset(selectResult('dcOpInfo))" in part and "car(errset(outputs()))" in part
    assert "pv(inst q ?result 'dcOpInfo)" in part and "OP(inst q)" in part
    assert 'icoptOpOut = car(errset(outfile("metrics/oppoints.tsv" "w")))' in part
    assert "when(icoptOpRead && icoptOpRows" in part            # rows are written only once the whole read went through
    for q in ocean.OP_QUANTITIES:
        assert f'"{q}"' in part
    # the SKILL escapes: backslash, tab, newline in a name; tab-separated rows
    assert r'cond((equal(c "\\") "\\\\") (equal(c "\t") "\\t") (equal(c "\n") "\\n") (t c))' in part
    assert r'sprintf(nil "%s\t%s\t%.16g\n" icoptOpEscape(name) q float(v))' in part
    assert r'fprintf(icoptOpOut "instance\tquantity\tvalue\n")' in part
    with pytest.raises(ValueError, match="safe OCEAN result selector"):
        ocean.replay_script(METRICS, [], psf_dir="psf", scalars_file="s", waveform_dir="w", oppoint_result="a b",
                            oppoints_file="o")


def test_instance_names_with_special_characters_come_back_as_they_were(tmp_path):
    names = ["/I0/M3", "/M\\<0\\>", "/odd\tname", "/two\nlines", "/cr\rin"]
    path = tmp_path / "oppoints.tsv"
    from tests.ic_opt.fakes import _skill_escape

    rows = [f"{_skill_escape(n)}\tgm\t{i + 1}e-3" for i, n in enumerate(names)]
    path.write_bytes(("instance\tquantity\tvalue\n" + "".join(f"{r}\n" for r in rows)).encode())
    assert ocean.parse_oppoints(path) == {n: {"gm": (i + 1) * 1e-3} for i, n in enumerate(names)}


def test_parsing_the_file(tmp_path):
    path = tmp_path / "oppoints.tsv"
    assert ocean.parse_oppoints(path) is None                                  # never written
    path.write_text("")
    assert ocean.parse_oppoints(path) is None                                  # the results hold none
    path.write_text("instance\tquantity\tvalue\n/R0\tregion\t0\n/M1\tgm\t1e-3\n/M1\tvth\tnan\n/M1\tids\t2e-4\n")
    assert ocean.parse_oppoints(path) == {"/M1": {"gm": 1e-3, "ids": 2e-4}}   # no gm: not a transistor; nan left out
    path.write_text("instance\tquantity\tvalue\n/R0\tregion\t0\n")
    assert ocean.parse_oppoints(path) is None
    for bad in ("metric\tvalue\n", "instance\tquantity\tvalue\n/M1\tgm\n", "instance\tquantity\tvalue\n/M1\tfoo\t1\n",
                "instance\tquantity\tvalue\n/M1\tgm\tabc\n"):
        path.write_text(bad)
        with pytest.raises(ValueError):
            ocean.parse_oppoints(path)


# -- through the pipeline, with the fake Spectre host ----------------------------------------------------------------------

def run_point(tmp_path: Path, template: str, *, oppoints=lambda p, tb, c: TABLE, **simulator):
    spec = make_spec(simulator={"parallel_jobs": 1, "threads_per_run": 2, "timeout_s": 60, **simulator})
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0}, oppoints_fn=oppoints)
    obs = evaluate(spec, [Point({"F": "22", "W": "0.8u"}, "user")], ex, store, deck=Deck(templates={("tb", None): template}),
                   limits=FAKE_HOST)[0]
    child = obs.children["tb/nominal"]
    ran = (tmp_path / "proj" / child.sim_dir / "netlist" / "input.scs").read_text()
    return obs, child, ran, ex, store


def plain_render(template: str) -> str:
    return netlist.render(template, {"F": "22", "W": "0.8u"})


def test_case_1_the_export_asks_for_them(tmp_path):
    obs, child, ran, ex, _ = run_point(tmp_path, WITH_STATEMENT)
    assert ran == plain_render(WITH_STATEMENT)                 # byte for byte
    assert ex.oppoint_results == ["dcOpInfo"]
    assert obs.status == "ok" and child.metrics == {"NF": 8.0} and child.operating_points == TABLE


def test_case_2_the_statement_is_added(tmp_path):
    _, child, ran, ex, _ = run_point(tmp_path, WITHOUT_STATEMENT)
    assert lines_added(plain_render(WITHOUT_STATEMENT), ran)[1:] == ["icoptOpInfo info what=oppoint where=rawfile"]
    assert ex.oppoint_results == ["icoptOpInfo"] and child.operating_points == TABLE


def test_case_3_a_dc_analysis_and_the_statement_are_added(tmp_path):
    _, child, ran, ex, _ = run_point(tmp_path, WITHOUT_DC)
    assert ran.startswith(plain_render(WITHOUT_DC))
    assert ran.splitlines()[-2:] == ["icoptDcOp dc", "icoptOpInfo info what=oppoint where=rawfile"]
    assert ex.oppoint_results == ["icoptOpInfo"] and child.operating_points == TABLE


def test_case_4_switched_off(tmp_path):
    _, child, ran, ex, store = run_point(tmp_path, WITHOUT_DC, operating_points=False)
    assert ran == plain_render(WITHOUT_DC)
    assert ex.oppoint_results == [] and child.operating_points is None
    assert "operating points" not in (tmp_path / "proj" / child.sim_dir / "metrics" / "probe.ocn").read_text()
    assert not (tmp_path / "proj" / child.sim_dir / "metrics" / "oppoints.tsv").exists()
    assert "operating_points" not in store.observations_path.read_text()


def test_results_without_operating_points_fail_nothing(tmp_path):
    obs, child, _, ex, _ = run_point(tmp_path, WITHOUT_DC, oppoints=lambda p, tb, c: None)
    assert ex.oppoint_results == ["icoptOpInfo"]
    assert (tmp_path / "proj" / child.sim_dir / "metrics" / "oppoints.tsv").read_text() == ""
    assert obs.status == "ok" and child.issues == [] and child.operating_points is None and child.metrics == {"NF": 8.0}


def test_an_unreadable_file_fails_nothing(tmp_path):
    spec = make_spec()
    store = RunStore(tmp_path)
    ctx = StageContext(spec=spec, executor=LocalExecutor(tmp_path), store=store, obs_id="obs_0001", workdir=tmp_path,
                       remote_dir=str(tmp_path), unit="tb")
    bad = tmp_path / "oppoints.tsv"
    bad.write_text("garbage\n")
    rows = {"NF": ocean.ScalarRow(8.0, "dB", "pass")}
    child = Extract().run(Scalars(rows, {}, 1, oppoints=bad), ctx)
    assert child.status == "ok" and child.issues == [] and child.operating_points is None and child.metrics == {"NF": 8.0}


# -- the cache, the store, the identity ---------------------------------------------------------------------------------

OLD_LINE = ('{"obs_id":"obs_0001","params":{"F":"22","W":"0.8u"},"origin":"user","children":{"tb/nominal":{"unit":"tb",'
            '"corner":null,"status":"ok","metrics":{"NF":8.22},"issues":[],"sim_dir":"sims/obs_0001/tb/nominal",'
            '"seconds":0.5}},"metrics":{"NF":8.22},"fom":null,"objective":8.22,"feasible":true,"constraint_penalty":0.0,'
            '"status":"ok","issues":[],"spec_fingerprint":"ba0f5751e8b31248","pipeline_fingerprint":"1c79f40effa5daab",'
            '"step":"evaluate","cache":{},"simulations":1,"started_at":"2026-09-01T00:00:00Z",'
            '"finished_at":"2026-09-01T00:00:01Z"}')           # as 0.4.0 (30f2692) wrote it, for make_spec()'s problem


def test_the_identity_of_the_problem_and_the_pipeline_is_what_it_was():
    """Pinned on 30f2692: the spec without the field, with it on, and with it off are one problem (operating points
    change no metric); the Spectre pipeline's fingerprint is unchanged, so every observation stays reusable."""
    spec = make_spec()
    assert (spec.fingerprint(), spec._legacy_fingerprint()) == ("ba0f5751e8b31248", "1d1cf8fb28678108")
    base = {"parallel_jobs": 2, "threads_per_run": 2, "timeout_s": 60}
    on = make_spec(simulator={**base, "operating_points": True})
    off = make_spec(simulator={**base, "operating_points": False})
    assert on.fingerprint() == off.fingerprint() == "ba0f5751e8b31248" and on._legacy_fingerprint() == "1d1cf8fb28678108"
    assert "operating_points" not in json.dumps(spec.problem()) and off.model_dump()["simulator"]["operating_points"] is False
    assert pipeline_fingerprint(spectre_pipeline(spec, Deck())) == "1c79f40effa5daab"


def test_a_store_written_before_reads_as_it_did_and_its_points_are_reused(tmp_path):
    store = RunStore(tmp_path / "proj")
    store.observations_path.parent.mkdir(parents=True, exist_ok=True)
    store.observations_path.write_text(OLD_LINE + "\n")
    old = store.observations()[0]
    assert old.children["tb/nominal"].operating_points is None
    assert old.model_dump_json() == OLD_LINE                                  # written back byte for byte

    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 9.0}, oppoints_fn=lambda p, tb, c: TABLE)
    deck = Deck(templates={("tb", None): WITHOUT_DC})
    obs = evaluate(make_spec(), [Point({"F": "22", "W": "0.8u"}, "user")], ex, store, deck=deck, limits=FAKE_HOST)[0]
    assert obs.obs_id == "obs_0001" and obs.metrics == {"NF": 8.22}           # reused: no simulation
    assert obs.children["tb/nominal"].operating_points is None and not any(c.startswith("spectre") for c in ex.commands)


def test_a_reused_point_carries_the_operating_points_stored_with_it(tmp_path):
    first, _, _, _, store = run_point(tmp_path, WITHOUT_DC)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 1.0}, oppoints_fn=lambda p, tb, c: None)
    again = evaluate(make_spec(simulator={"parallel_jobs": 1, "threads_per_run": 2, "timeout_s": 60}),
                     [Point({"F": "22", "W": "0.8u"}, "user")], ex, RunStore(tmp_path / "proj"),
                     deck=Deck(templates={("tb", None): WITHOUT_DC}), limits=FAKE_HOST)[0]
    assert again.obs_id == first.obs_id and ex.commands == []
    assert again.children["tb/nominal"].operating_points == TABLE


def test_the_observation_round_trips_through_json():
    child = ChildResult(unit="tb", status="ok", metrics={"NF": 8.0}, operating_points=TABLE)
    obs = Observation(obs_id="obs_0001", params={"F": "22"}, origin="user", children={"tb/nominal": child,
                      "tb/ss": ChildResult(unit="tb", corner="ss", status="ok")}, status="ok", spec_fingerprint="s",
                      pipeline_fingerprint="p", started_at="a", finished_at="b")
    line = obs.model_dump_json()
    assert Observation.model_validate_json(line) == obs
    data = json.loads(line)
    assert data["children"]["tb/nominal"]["operating_points"] == TABLE and "operating_points" not in data["children"]["tb/ss"]


def test_no_strategy_reads_them():
    """D8: operating points are shown, never used by a numeric strategy."""
    root = Path(__file__).resolve().parents[2] / "src" / "ic_opt" / "suggesters"
    assert [p for p in root.rglob("*.py") if "operating_points" in p.read_text(encoding="utf-8")] == []


# -- the doctor ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "simulator", "detail"), [
    (WITH_STATEMENT, {}, "in the export (dcOpInfo)"),
    (WITHOUT_STATEMENT, {}, "added by ic-opt (statement): `icoptOpInfo info what=oppoint where=rawfile` after dcOp"),
    (WITHOUT_DC, {}, ("added by ic-opt (DC analysis and statement): `icoptDcOp dc`, "
                      "`icoptOpInfo info what=oppoint where=rawfile` at the end")),
    (WITHOUT_DC, {"operating_points": False}, "off (simulator.operating_points: false)"),
])
def test_the_doctor_says_per_testbench(tmp_path, capsys, text, simulator, detail):
    export = tmp_path / "maestro" / "tb"
    (export / "netlist").mkdir(parents=True)
    (export / "netlist" / "input.scs").write_text(text.replace("{{F}}", "20").replace("{{W}}", "0.6u"))
    spec = make_spec(testbenches=[{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c",
                                   "test_name": "t"}],
                     simulator={"parallel_jobs": 1, "threads_per_run": 2, "timeout_s": 60, "license_check": False, **simulator})
    report = doctor(spec, LocalExecutor(tmp_path / "scratch"), limits=HostLimits(max_threads=8, max_memory_gb=16))
    check = next(c for c in report.checks if c.name == "operating points:tb")
    assert check.ok and check.detail == detail
    assert f"[ok] operating points:tb: {detail}" in str(report).splitlines()


def test_the_doctor_notes_an_export_it_cannot_read(tmp_path):
    spec = make_spec(simulator={"parallel_jobs": 1, "threads_per_run": 2, "timeout_s": 60, "license_check": False})
    report = doctor(spec, LocalExecutor(tmp_path / "scratch"), limits=HostLimits(max_threads=8, max_memory_gb=16))
    check = next(c for c in report.checks if c.name == "operating points:tb")
    assert not check.ok and check.level == "note" and not check.blocking
    assert check.detail == "export not read (/x/netlist/input.scs on local)"
