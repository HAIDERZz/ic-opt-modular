"""T18.4 (``docs/refactor/T18_4_TIGHTEN_RECIPE_SPEC.md``): the ``signoff`` recipe's rounds -- a constraint the re-check
across all corners missed is tightened by the miss and the search at one corner goes again, bounded by ``rounds``."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from ic_opt import blocks, space
from ic_opt import site as site_module
from ic_opt.cli import app
from ic_opt.library import link
from ic_opt.recipes import signoff
from ic_opt.recipes.signoff import (
    ROUNDS_FILE,
    _best_rechecked,
    _crossed,
    _form,
    _Move,
    _tightened,
    _unwritable,
    _worst,
    _write,
)
from ic_opt.sim.corner import worst_metrics
from ic_opt.spec import Constraint, Spec
from tests.ic_opt.fakes import minimal_spec
from tests.ic_opt.library_device_fixtures import xfm_library
from tests.ic_opt.test_cli_recipes import fake_run, project, runner
from tests.ic_opt.test_library_device_run import run_of, spec_at
from tests.ic_opt.test_schedule import at, child, point

CORNERS = ("tt", "ss", "ff")
START = [{"F": f, "W": "0.6u"} for f in ("30", "28", "22", "20")]      # the first search: exactly these four points


def worse_at_ss(p, tb, c):
    """NF = 5 + F / 8 dB at tt and ff (7.5 to 8.75: every point passes NF < 9 dB there) and 0.75 dB more at ss (F = 28
    and F = 30 miss it, by 0.25 and 0.5 dB); G = F dB. Binary fractions: every number below is exact."""
    f = int(p["F"])
    return {"NF": 5 + f / 8 + (0.75 if c == "ss" else 0.0), "G": float(f)}


def gain_project(tmp_path: Path, *, extra_metrics=(), extra_constraints=()) -> Path:
    """The fake's project at tt, ss and ff: NF < 9 dB and G > 10 dB, maximize G (the best points of a search at tt are
    the widest F, the ones ss fails), a start file of four points."""
    root = project(tmp_path, corners=CORNERS)
    d = yaml.safe_load((root / "spec.yaml").read_text(encoding="utf-8"))
    d["metrics"] = [{"name": name, "unit": unit, "expression": f'value(getData("{name}"))'}
                    for name, unit in (("NF", "dB"), ("G", "dB"), *extra_metrics)]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9 dB"}, {"metric": "G", "op": "gt", "value": "10 dB"},
                        *extra_constraints]
    d["objective"] = {"direction": "maximize", "expression": "G"}
    (root / "spec.yaml").write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    (root / "start.json").write_text(json.dumps(START), encoding="utf-8")
    return root


RECIPE = {"corner": "tt", "budget": 4, "batch": 2, "top": 1, "strategy": "random", "seed": 2, "current": False,
          "start": "start.json"}


def spy_on_optimize(monkeypatch) -> list[dict]:
    """Every ``opt.optimize`` call the recipe makes, its arguments recorded (the spec, ``initial`` as obs ids)."""
    calls, real = [], blocks.optimize

    def recorded(spec, *args, **kwargs):
        calls.append({"spec": spec, "initial": [o.obs_id for o in kwargs.get("initial", ())],
                      **{k: v for k, v in kwargs.items() if k != "initial"}})
        return real(spec, *args, **kwargs)

    monkeypatch.setattr(blocks, "optimize", recorded)
    return calls


def rounds_file(run) -> dict:
    return json.loads((run.store.reports_dir() / ROUNDS_FILE).read_text(encoding="utf-8"))


def worst_nf(o) -> float:
    return max(c.metrics["NF"] for c in o.children.values())


# -- 1. rounds=1: the recipe as it was ------------------------------------------------------------------------------------


def test_one_round_is_the_recipe_as_it_was(tmp_path, capsys):
    """The expectations of ``test_cli_recipes.test_signoff_recipe_searches_one_corner_then_checks_all``, with rounds=1
    named: nothing of the rounds is printed or written (the byte-for-byte comparison with the recipe before T18.4 is in
    the record of the change)."""
    run = fake_run(project(tmp_path, corners=CORNERS))
    signoff.main(run, corner="tt", budget=4, batch=2, top=2, strategy="random", seed=2, rounds=1)
    obs = run.store.observations()
    search, check = obs.by_step("search@tt"), obs.by_step("signoff")
    assert len(search) == 4 and all(set(o.children) == {"tb/tt"} for o in search)
    assert len(check) == 2 and all(set(o.children) == {"tb/tt", "tb/ss", "tb/ff"} for o in check)
    assert {o.key for o in check} <= {o.key for o in search}
    assert {o.step for o in obs} == {"search@tt", "signoff"} and {o.spec_fingerprint for o in obs} == {run.spec.fingerprint()}
    assert "signoff round" not in capsys.readouterr().out
    assert not (run.store.reports_dir() / ROUNDS_FILE).exists()
    assert (run.store.reports_dir() / "report.md").read_text(encoding="utf-8").startswith("# demo — sign-off across all corners\n")


# -- 2. a miss at another corner tightens the next search by that much ----------------------------------------------------


def test_a_miss_at_another_corner_tightens_the_next_search_by_that_much(tmp_path, capsys, monkeypatch):
    """Seed 0: the second search proposes an F=30 point, which passes NF < 9 dB at tt (8.75 dB) and only the tightened
    limit refuses, so its verdict shows which constraints the search ran under."""
    root = gain_project(tmp_path)
    written = (root / "spec.yaml").read_text(encoding="utf-8")
    calls = spy_on_optimize(monkeypatch)
    run = fake_run(root, metric_fn=worse_at_ss)
    signoff.main(run, **{**RECIPE, "seed": 0}, rounds=2)
    out = capsys.readouterr().out

    # round 1: the search's best point (F=30) passes at tt, misses NF < 9 dB at ss by 0.5 dB
    obs = run.store.observations()
    first = obs.by_step("signoff")
    assert [(o.obs_id, o.params["F"], o.status) for o in first] == [("obs_0005", "30", "constraint_failed")]
    assert ("[run] signoff round 1: NF lt 9 dB missed by 0.5 dB (obs_0005 at ss: 9.5 dB); round 2 searches under "
            "NF lt 8.5 dB\n") in out
    assert "G gt 10 dB missed" not in out

    # round 2 searches under the limit tightened by exactly the miss: a copy of the spec, another problem
    tightened = run.spec.model_copy(update={"constraints": [Constraint(metric="NF", op="lt", value="8.5 dB"),
                                                             run.spec.constraints[1]]})
    assert tightened.fingerprint() != run.spec.fingerprint()
    assert [c["step"] for c in calls] == ["search@tt", "search@tt#2"]
    second = calls[1]
    assert second["spec"].constraints == tightened.constraints and second["spec"].fingerprint() == tightened.fingerprint()
    assert second["initial"] == ["obs_0001", "obs_0002", "obs_0003", "obs_0004"]     # the first search's rows, handed over
    assert second["current"] is False and second["budget"] == 4 and second["corners"] == ["tt"] and not second.get("start")
    rows = obs.by_step("search@tt#2")
    assert len(rows) == 4 and {o.spec_fingerprint for o in rows} == {tightened.fingerprint()}
    assert all(set(o.children) == {"tb/tt"} and o.feasible == (o.metrics["NF"] <= 8.5) for o in rows)    # its verdicts
    assert [(o.params["F"], o.metrics["NF"]) for o in rows if not o.feasible] == [("30", 8.75)]   # 8.75 <= 9 as written
    assert not {o.key for o in rows} & {o.key for o in obs.by_step("search@tt")}

    # its best point under the tightened limit (F=28: F=30 no longer passes) re-checked at every corner, as written
    check = obs.by_step("signoff#2")
    assert [o.params["F"] for o in check] == ["28"] and {o.key for o in check} <= {o.key for o in rows}
    assert all(o.spec_fingerprint == run.spec.fingerprint() and o.feasible == (worst_nf(o) <= 9 and not o.not_run)
               for o in check)
    assert check[0].status == "constraint_failed" and worst_nf(check[0]) == 9.25
    assert (f"[run] signoff round 2: the 2 rounds are made and no re-checked point is feasible at every corner; the best of "
            f"round 2, {check[0].obs_id} (constraint_failed), misses NF lt 9 dB by 0.25 dB\n") in out
    assert {o.step for o in obs} == {"search@tt", "signoff", "search@tt#2", "signoff#2"}
    assert (root / "spec.yaml").read_text(encoding="utf-8") == written                 # the user's spec is not rewritten

    record = rounds_file(run)
    assert record["rounds_allowed"] == 2 and record["tighten"] == 1.0 and record["spec_fingerprint"] == run.spec.fingerprint()
    one, two = record["rounds"]
    assert one["search"] == {"step": "search@tt", "spec_fingerprint": run.spec.fingerprint(), "points": 4,
                             "constraints": [{"metric": "NF", "op": "lt", "value": "9 dB"},
                                             {"metric": "G", "op": "gt", "value": "10 dB"}]}
    assert one["recheck"] == {"step": "signoff", "points": 1}
    assert one["best"]["obs_id"] == "obs_0005" and one["best"]["status"] == "constraint_failed" and not one["best"]["feasible"]
    assert one["misses"] == [{"metric": "NF", "op": "lt", "limit": "9 dB", "searched": "9 dB", "value": 9.5, "corner": "ss",
                              "miss": 0.5, "move": 0.5, "next": "8.5 dB"}]
    assert one["no_value"] == []
    assert two["search"]["step"] == "search@tt#2" and two["search"]["spec_fingerprint"] == tightened.fingerprint()
    assert two["search"]["constraints"][0] == {"metric": "NF", "op": "lt", "value": "8.5 dB"}
    assert two["recheck"] == {"step": "signoff#2", "points": 1} and two["best"]["obs_id"] == check[0].obs_id
    assert [(m["searched"], m["miss"], m["next"]) for m in two["misses"]] == [("8.5 dB", 0.25, None)]   # no round follows
    assert record["outcome"] == "rounds_used" and record["report_round"] == 2
    report = (run.store.reports_dir() / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# demo — sign-off across all corners (round 2)\n\n1 observations")   # the last re-check


# -- 3. the rounds stop when a re-checked point is feasible at every corner ------------------------------------------------


def test_the_rounds_stop_when_a_rechecked_point_is_feasible_at_every_corner(tmp_path, capsys, monkeypatch):
    calls = spy_on_optimize(monkeypatch)
    run = fake_run(gain_project(tmp_path), metric_fn=lambda p, tb, c: {"NF": 8.0, "G": float(p["F"])})
    signoff.main(run, **RECIPE, rounds=3)
    out = capsys.readouterr().out
    assert "[run] signoff round 1: obs_0005 is feasible at every corner (objective 30): done\n" in out
    assert [c["step"] for c in calls] == ["search@tt"]                                   # no second search
    obs = run.store.observations()
    assert {o.step for o in obs} == {"search@tt", "signoff"} and obs.by_step("signoff")[0].feasible
    record = rounds_file(run)
    assert record["outcome"] == "feasible" and record["report_round"] == 1 and len(record["rounds"]) == 1
    assert record["rounds"][0]["best"]["feasible"] and record["rounds"][0]["misses"] == []
    assert (run.store.reports_dir() / "report.md").read_text(encoding="utf-8").startswith(
        "# demo — sign-off across all corners\n")


# -- 4. tighten scales the move; what nobody missed, or what has no value, stays ------------------------------------------


def lost_at_the_first_recheck(p, tb, c, cwd):
    """``worse_at_ss`` and P = 1: at the first re-checked point (obs_0005) P has no value at any corner."""
    return {**worse_at_ss(p, tb, c), "P": None if Path(cwd).parent.parent.name == "obs_0005" else 1.0}


def test_tighten_scales_the_move_and_what_was_not_missed_or_has_no_value_stays(tmp_path, capsys):
    root = gain_project(tmp_path, extra_metrics=[("P", "mW")], extra_constraints=[{"metric": "P", "op": "lt", "value": "5"}])
    run = fake_run(root, metric_fn=lost_at_the_first_recheck)
    signoff.main(run, **RECIPE, rounds=2, tighten=0.5, full=True)
    out = capsys.readouterr().out
    first = run.store.observations().by_step("signoff")[0]
    assert first.obs_id == "obs_0005" and first.status == "metric_failed" and set(first.children) == {"tb/tt", "tb/ss", "tb/ff"}
    assert ("[run] signoff round 1: P lt 5 cannot be tightened: P has no value at obs_0005 (a failed simulation, or one the "
            "re-check did not run); left as it is\n") in out
    assert ("[run] signoff round 1: NF lt 9 dB missed by 0.5 dB (obs_0005 at ss: 9.5 dB); round 2 searches under "
            "NF lt 8.75 dB\n") in out                                                  # half the miss
    assert not [line for line in out.splitlines() if "signoff round" in line and "G gt" in line]
    one, two = rounds_file(run)["rounds"]
    assert one["misses"] == [{"metric": "NF", "op": "lt", "limit": "9 dB", "searched": "9 dB", "value": 9.5, "corner": "ss",
                              "miss": 0.5, "move": 0.25, "next": "8.75 dB"}]
    assert one["no_value"] == [{"metric": "P", "op": "lt", "limit": "5"}]
    assert two["search"]["constraints"] == [{"metric": "NF", "op": "lt", "value": "8.75 dB"},
                                            {"metric": "G", "op": "gt", "value": "10 dB"},
                                            {"metric": "P", "op": "lt", "value": "5"}]
    rows = run.store.observations().by_step("search@tt#2")
    assert rows and all(o.feasible == (o.metrics["NF"] <= 8.75) for o in rows)


def test_the_moves_add_up_over_the_rounds(tmp_path, capsys):
    """Every point misses NF < 9 dB at ss by 0.5 dB: round 2 searches under 8.5 dB, round 3 under 8 dB -- from the limit
    the round searched under, not from the one as written -- and after round 3 the rounds are used up."""
    run = fake_run(gain_project(tmp_path), metric_fn=lambda p, tb, c: {"NF": 9.5 if c == "ss" else 8.0, "G": float(p["F"])})
    signoff.main(run, **RECIPE, rounds=3)
    out = capsys.readouterr().out
    assert "; round 2 searches under NF lt 8.5 dB\n" in out
    assert "; round 3 searches under NF lt 8 dB (round 2 searched under 8.5 dB)\n" in out
    record = rounds_file(run)
    assert [r["search"]["constraints"][0]["value"] for r in record["rounds"]] == ["9 dB", "8.5 dB", "8 dB"]
    assert [(m["searched"], m["miss"], m["next"]) for r in record["rounds"] for m in r["misses"]] == [
        ("9 dB", 0.5, "8.5 dB"), ("8.5 dB", 0.5, "8 dB"), ("8 dB", 0.5, None)]
    assert record["outcome"] == "rounds_used" and record["report_round"] == 3
    assert "[run] signoff round 3: the 3 rounds are made and no re-checked point is feasible at every corner; the best of " \
           "round 3" in out and "misses NF lt 9 dB by 0.5 dB\n" in out
    steps = {o.step for o in run.store.observations()}
    assert steps == {"search@tt", "signoff", "search@tt#2", "signoff#2", "search@tt#3", "signoff#3"}


def window(p, tb, c):
    """``worse_at_ss`` and V: 1 V at tt, 0 V at ss, 2.1 V at ff -- a spread of 2.1 V for a window 0.5 V to 1.5 V wide."""
    return {**worse_at_ss(p, tb, c), "V": {"tt": 1.0, "ss": 0.0, "ff": 2.1}[c]}


@pytest.mark.parametrize(("case", "outcome", "line"), [
    ("nothing_to_recheck", "nothing_to_recheck",
     "no point of step search@tt is feasible under the constraints it searched under: nothing to re-check"),
    ("nothing_to_tighten", "nothing_to_tighten",
     "nothing to tighten: the best re-checked point, obs_0005 (failed:spectre), misses no constraint that has a value there"),
    ("limits_cross", "limits_cross",
     "the tightened limits would cross (V ge 1 V and V le 0.9 V): no point can meet them"),
])
def test_the_rounds_end_early_when_a_round_cannot_go_on(tmp_path, capsys, monkeypatch, case, outcome, line):
    """No point of the search passes (nothing to re-check); the re-check's point failed a simulation at ss and misses
    nothing where it has values (nothing to tighten); both bounds of V missed by more than its window allows."""
    calls = spy_on_optimize(monkeypatch)
    if case == "nothing_to_recheck":
        run = fake_run(gain_project(tmp_path), metric_fn=lambda p, tb, c: {"NF": 9.5, "G": float(p["F"])})
    elif case == "nothing_to_tighten":
        run = fake_run(gain_project(tmp_path), metric_fn=lambda p, tb, c: {"NF": 8.0, "G": float(p["F"])},
                       fail_spectre=lambda tb, corner: corner == "ss")
    else:
        bounds = [{"metric": "V", "op": "ge", "value": "0.5 V"}, {"metric": "V", "op": "le", "value": "1.5 V"}]
        run = fake_run(gain_project(tmp_path, extra_metrics=[("V", "V")], extra_constraints=bounds), metric_fn=window)
    signoff.main(run, **RECIPE, rounds=3, full=True)
    assert f"[run] signoff round 1: {line}\n" in capsys.readouterr().out
    assert [c["step"] for c in calls] == ["search@tt"]                                   # no second search
    record = rounds_file(run)
    assert record["outcome"] == outcome and record["why"] == line and len(record["rounds"]) == 1
    assert record["report_round"] == (None if case == "nothing_to_recheck" else 1)


# -- 5. --plan ------------------------------------------------------------------------------------------------------------


@pytest.fixture
def site_file(tmp_path: Path, monkeypatch) -> Path:
    """The command line reads this site.yaml, never the developer's own."""
    path = tmp_path / "site.yaml"
    path.write_text("hosts:\n  local: {max_threads: 16, max_memory_gb: 64}\n", encoding="utf-8")
    monkeypatch.setattr(site_module, "SITE_FILE", path)
    return path


def test_plan_prints_every_round_and_changes_nothing(tmp_path, site_file):
    root = gain_project(tmp_path)
    result = runner.invoke(app, ["run", "signoff", str(root), "--plan", "rounds=3", "budget=8", "batch=4", "top=2"])
    assert result.exit_code == 0, result.output
    out = result.output
    for k, search, check in ((1, "search@tt", "signoff"), (2, "search@tt#2", "signoff#2"), (3, "search@tt#3", "signoff#3")):
        assert f"[plan] signoff round {k} of 3" in out
        assert f"[plan] opt.optimize step='{search}' strategy=metric_gp: 0/8 points done, up to 8 more" in out
        assert f"[plan] sim.evaluate step='{check}': 0 points × (3 testbench sims)" in out
    assert ("[plan] signoff round 2 of 3, only if no point re-checked in round 1 is feasible at every corner: the search at tt "
            "again (step search@tt#2) under the constraints that round's best point missed, each moved by 1 × its miss, "
            "then the re-check of its top 2 at every corner under the constraints as written (step signoff#2)") in out
    assert out.count("current design (the exported netlists) first") == 1              # the first round's search only
    assert not (root / ".icopt" / "observations.jsonl").exists() and not (root / ".icopt" / "reports" / ROUNDS_FILE).exists()
    assert not any((root / ".icopt" / "decks").iterdir())


# -- a spec with library devices (T18.2B): nothing special ---------------------------------------------------------------


@pytest.fixture
def fresh_links():
    link.clear()
    yield
    link.clear()


def test_a_spec_with_library_devices_runs_the_rounds_the_same_way(tmp_path, capsys, fresh_links):
    root = xfm_library(tmp_path / "lib")
    spec = spec_at(root, tmp_path / "signoff", corners=[{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss"}])
    run = run_of(spec, tmp_path / "signoff", metric_fn=lambda p, tb, c: {"NF": 9.5 if c == "ss" else 8.5})
    signoff.main(run, corner="tt", budget=6, batch=3, top=2, seed=1, rounds=2)
    out = capsys.readouterr().out
    assert out.count("[optimize] strategy auto: metric_gp (library devices: no EMX in the loop)") == 2
    assert "NF lt 9 dB missed by 0.5 dB (" in out and "at ss: 9.5 dB); round 2 searches under NF lt 8.5 dB\n" in out
    tightened = spec.model_copy(update={"constraints": [Constraint(metric="NF", op="lt", value="8.5 dB")]}).fingerprint()
    obs = run.store.observations()
    rows, check = obs.by_step("search@tt#2"), obs.by_step("signoff#2")
    assert len(rows) == 6 and {o.spec_fingerprint for o in rows} == {tightened} and len(check) == 2
    assert {o.spec_fingerprint for o in check} == {spec.fingerprint()} and all(o.corners() == {"tt", "ss"} for o in check)
    for o in [*rows, *check]:
        space.check(spec, o.params)                                                  # a valid point: a row's combination
        assert o.children["xfmr/nominal"].library_row["obs_id"] == link.row_for(spec, "xfmr", o.params).obs_id
    assert rounds_file(run)["outcome"] == "rounds_used"


# -- the parameters and the limits, checked before anything runs ----------------------------------------------------------


@pytest.mark.parametrize(("params", "message"), [
    ({"rounds": 0}, "rounds must be a whole number of at least 1, got 0"),
    ({"rounds": 1.5}, "rounds must be a whole number of at least 1, got 1.5"),
    ({"rounds": True}, "rounds must be a whole number of at least 1, got True"),
    ({"rounds": 2, "tighten": 0}, "tighten must be a number above 0"),
    ({"rounds": 2, "tighten": -0.5}, "tighten must be a number above 0"),
    ({"rounds": 2, "tighten": math.nan}, "tighten must be a number above 0"),
    ({"rounds": 2, "tighten": "half"}, "tighten must be a number above 0"),
])
def test_rounds_and_tighten_are_refused_before_anything_runs(tmp_path, params, message):
    run = fake_run(gain_project(tmp_path), metric_fn=worse_at_ss)
    with pytest.raises(ValueError, match=message):
        signoff.main(run, **RECIPE, **params)
    assert not run.store.observations() and not any(run.store.root.joinpath("decks").iterdir())


def test_a_limit_is_written_back_in_its_constraint_s_own_form():
    def spec_with(value: str, unit: str = "dB") -> tuple[Spec, Constraint]:
        spec = Spec.model_validate(minimal_spec(metrics=[{"name": "M", "unit": unit, "expression": "m()"}],
                                                constraints=[{"metric": "M", "op": "lt", "value": value}],
                                                objective={"direction": "minimize", "expression": "M"}))
        return spec, spec.constraints[0]

    for value, unit, number, text in (("9 dB", "dB", "8.662188", "8.662188 dB"), ("9dB", "dB", "8.5", "8.5dB"),
                                      ("28e9 Hz", "Hz", "28.5e9", "28.5e9 Hz"), ("1.5E-3 A", "A", "0.00125", "1.25E-3 A"),
                                      ("-6 dBm", "dBm", "-5.5", "-5.5 dBm"), ("9", "1", "8", "8"), ("150e9 Hz", "Hz", "0", "0e9 Hz"),
                                      (".5 ratio", "ratio", "0.25", "0.25 ratio"), ("50 mV", "mV", "49.9", "49.9 mV"),
                                      ("12", "dB", "150500000000", "150500000000")):
        spec, c = spec_with(value, unit)
        assert _unwritable(spec, c) is None, value
        assert _write(_form(c), Decimal(number)) == text                               # no digit rounded away
    own_unit = "; write the number in the metric's own unit"
    for value, unit, why in (("50m V", "V", f"takes no SI prefix, so it reads as 50 V{own_unit} (V)"),
                             ("28 GHz", "Hz", "takes no SI prefix, so it reads as 28 Hz"),
                             ("0.6u", "A", "takes no SI prefix, so it reads as 0.6 A"),
                             ("0.6u", "1", f"takes no SI prefix, so it reads as 0.6{own_unit} (1)"),
                             ("9 e3", "dB", "not a number followed by a unit"),
                             ("1 000 Hz", "Hz", "not a number followed by a unit"),
                             ("about 9", "dB", "not a number followed by a unit")):
        spec, c = spec_with(value, unit)
        assert why in (_unwritable(spec, c) or ""), value


def test_a_limit_the_recipe_cannot_write_back_is_refused_with_rounds_only(tmp_path, capsys):
    """``50m V``: a constraint's value takes no prefix (it reads as 50 V, README). With rounds above 1 the recipe refuses
    it before anything runs, naming every such constraint; with rounds=1 it runs as it always did."""
    root = gain_project(tmp_path, extra_metrics=[("V", "V")], extra_constraints=[{"metric": "V", "op": "ge", "value": "50m V"}])
    run = fake_run(root, metric_fn=lambda p, tb, c: {**worse_at_ss(p, tb, c), "V": 0.3})
    with pytest.raises(ValueError, match=r"signoff rounds=2 writes a tightened limit in its constraint's own form and cannot "
                                         r"for V ge '50m V': a constraint's value takes no SI prefix, so it reads as 50 V"):
        signoff.main(run, **RECIPE, rounds=2)
    assert not run.store.observations()
    signoff.main(run, **RECIPE)
    assert len(run.store.observations().by_step("search@tt")) == 4 and "signoff round" not in capsys.readouterr().out


# -- the pieces: the best re-checked point, each bound's own worst value, limits that cross --------------------------------


def two_sided() -> Spec:
    """One testbench at tt, ss and ff: NF < 9 and 0.5 <= V <= 1.5."""
    return Spec.model_validate(minimal_spec(
        corners=[{"id": c} for c in CORNERS],
        metrics=[{"name": "NF", "unit": "dB", "expression": "nf()"}, {"name": "V", "unit": "V", "expression": "v()"}],
        constraints=[{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "V", "op": "ge", "value": "0.5 V"},
                     {"metric": "V", "op": "le", "value": "1.5 V"}],
        objective={"direction": "minimize", "expression": "NF"}))


def test_each_bound_takes_its_own_worst_corner():
    spec = two_sided()
    o = point(spec, 0, at("20"), child("tb", "tt", NF=8.0, V=1.0), child("tb", "ss", NF=9.25, V=0.4),
              child("tb", "ff", NF=8.5, V=1.6))
    nf, lower, upper = spec.constraints
    assert _worst(spec, nf, o) == (9.25, "ss") and worst_metrics(spec, o)["NF"] == 9.25    # one-sided: worst_metrics' value
    assert _worst(spec, lower, o) == (0.4, "ss") and _worst(spec, upper, o) == (1.6, "ff")
    assert worst_metrics(spec, o)["V"] in (0.4, 1.6)                                        # one value for both bounds
    failed = point(spec, 1, at("22"), child("tb", "tt", status="failed:spectre"), child("tb", "ss", status="failed:spectre"),
                   child("tb", "ff", status="failed:spectre"))
    assert _worst(spec, nf, failed) == (None, None)
    measured_once = point(spec, 2, at("24"), child("dev", None, V=0.3), *(child("tb", c, NF=8.0) for c in CORNERS))
    assert _worst(spec, lower, measured_once) == (0.3, None)                             # a corner-less child's: no corner
    assert _worst(spec, nf, measured_once) == (8.0, "tt")                               # the first of equal ones


def test_the_best_rechecked_point_is_judged_before_one_that_gave_no_value():
    spec = two_sided()
    lost = point(spec, 0, at("20"), child("tb", "tt", NF=8.0, V=1.0), child("tb", "ss", status="metric_failed", NF=8.0),
                 child("tb", "ff", NF=8.0, V=1.0))
    far = point(spec, 1, at("22"), child("tb", "tt", NF=8.0, V=1.0), child("tb", "ss", NF=9.9, V=1.0),
                child("tb", "ff", NF=8.0, V=1.0))
    near = point(spec, 2, at("24"), child("tb", "tt", NF=8.0, V=1.0), child("tb", "ss", NF=9.1, V=1.0),
                 child("tb", "ff", NF=8.0, V=1.0))
    assert lost.status == "metric_failed" and lost.constraint_penalty == 0.0 and near.status == far.status == "constraint_failed"
    assert _best_rechecked([lost, far, near]) is near
    assert _best_rechecked([lost]) is lost
    good = point(spec, 3, at("26"), *(child("tb", c, NF=7.0, V=1.0) for c in CORNERS))
    better = point(spec, 4, at("28"), *(child("tb", c, NF=6.0, V=1.0) for c in CORNERS))
    assert _best_rechecked([near, good, better]) is better


def test_limits_that_would_cross_are_found():
    spec = two_sided()
    _, lower, upper = spec.constraints

    def move(index: int, c: Constraint, amount: str) -> _Move:
        return _Move(index, c, _form(c), c.value, Decimal(0), None, Decimal(amount), Decimal(amount))

    apart = _tightened(spec, [move(1, lower, "0.4"), move(2, upper, "0.4")])
    assert [c.value for c in apart.constraints] == ["9", "0.9 V", "1.1 V"] and _crossed(apart) == ""
    touching = _tightened(spec, [move(1, lower, "0.5"), move(2, upper, "0.5")])
    assert _crossed(touching) == ""                                                      # V = 1 meets both
    crossed = _tightened(spec, [move(1, lower, "0.5"), move(2, upper, "0.6")])
    assert _crossed(crossed) == "V ge 1 V and V le 0.9 V"
    assert spec.constraints[1].value == "0.5 V"                                          # the spec itself is left as it is
