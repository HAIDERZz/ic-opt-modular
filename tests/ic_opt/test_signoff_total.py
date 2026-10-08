"""N-98 (``docs/refactor/N98_SIGNOFF_TOTAL_SPEC.md``): ``signoff total=<N>`` -- the search advanced in batches, its design
sized for the whole run (N-96), the re-check, the rounds and the report on the call that reaches ``total``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ic_opt import advice as advice_rules
from ic_opt import site as site_module
from ic_opt.blocks.optimize import advise
from ic_opt.cli import app
from ic_opt.recipes import signoff
from ic_opt.recipes.signoff import ROUNDS_FILE
from tests.ic_opt.test_cli_recipes import fake_run, runner
from tests.ic_opt.test_signoff_tighten import gain_project, worse_at_ss

BATCH, TOTAL = 4, 16
RECIPE = {"corner": "tt", "batch": BATCH, "top": 2, "seed": 1}          # strategy auto: metric_gp (no EM device)
AUTHOR = {"author": "a person", "reason": "a test"}


@pytest.fixture
def site_file(tmp_path: Path, monkeypatch) -> Path:
    """The command line reads this site.yaml, never the developer's own."""
    path = tmp_path / "site.yaml"
    path.write_text("hosts:\n  local: {max_threads: 16, max_memory_gb: 64}\n", encoding="utf-8")
    monkeypatch.setattr(site_module, "SITE_FILE", path)
    return path


def wide_project(where: Path) -> Path:
    """``gain_project`` on a grid of 21 x 13 points, so that ``metric_gp``'s design is 8 points for a run of 16 (2 for a
    call of 4): NF < 9 dB and G > 10 dB at tt, ss and ff, maximize G; ss costs 0.75 dB of NF (``worse_at_ss``)."""
    root = gain_project(where)
    d = yaml.safe_load((root / "spec.yaml").read_text(encoding="utf-8"))
    d["variables"] = [{"name": "F", "kind": "integer", "lower": "20", "upper": "60", "step": "2"},
                      {"name": "W", "kind": "continuous_step", "lower": "0.6u", "upper": "3u", "step": "0.2u"}]
    (root / "spec.yaml").write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    return root


def per_batch(rows) -> list[list[tuple[dict, str]]]:
    return [[(o.params, o.origin) for o in rows[i : i + BATCH]] for i in range(0, len(rows), BATCH)]


def outcome(run) -> dict:
    """What a signoff run left, comparable between two projects: per step its points (parameters, origin, status, every
    child's metrics) in observation order, ``steps.json``, ``signoff_rounds.json`` and the report without the
    fingerprints (the spec names its export, so a fingerprint follows the project's path)."""
    obs = run.store.observations()
    steps = {}
    for o in obs:
        steps.setdefault(o.step, []).append((o.obs_id, o.params, o.origin, o.status,
                                             {k: c.metrics for k, c in sorted(o.children.items())}))
    rounds = run.store.reports_dir() / ROUNDS_FILE
    report = run.store.reports_dir() / "report.md"
    return {"steps": steps, "design": json.loads((run.store.root / "steps.json").read_text(encoding="utf-8")),
            "rounds": _without_fingerprints(json.loads(rounds.read_text(encoding="utf-8"))) if rounds.exists() else None,
            "report": report.read_text(encoding="utf-8") if report.exists() else None}


def _without_fingerprints(value):
    if isinstance(value, dict):
        return {k: _without_fingerprints(v) for k, v in value.items() if k != "spec_fingerprint"}
    if isinstance(value, list):
        return [_without_fingerprints(v) for v in value]
    return value


def stop_line(k: int) -> str:
    return (f"[run] signoff: the search holds {k} of {TOTAL} points; the re-check runs when it reaches {TOTAL} "
            f"(signoff budget={TOTAL} total={TOTAL})\n")


# -- 1. four calls give what one call gives ---------------------------------------------------------------------------------


@pytest.mark.parametrize("rounds", [1, 2])
def test_a_search_advanced_in_four_calls_is_the_one_call_run_search_and_recheck(tmp_path, capsys, rounds):
    """budget 4, 8, 12, 16 with total=16 against one call budget=16: the same search points batch by batch, the same
    re-checked points and their children, the same design record, rounds file and report. The first three calls stop
    after the search: no re-check, no round, no report. Without total the first call's design is 2 points, not 8, and
    the search parts from the second batch on."""
    whole = fake_run(wide_project(tmp_path / "whole"), metric_fn=worse_at_ss)
    signoff.main(whole, **RECIPE, budget=TOTAL, rounds=rounds)
    one_shot = capsys.readouterr().out
    assert "metric_gp initial design 8 points (1 start point first): " in one_shot and "the search holds" not in one_shot

    parts = fake_run(wide_project(tmp_path / "parts"), metric_fn=worse_at_ss)
    for budget in (4, 8, 12):
        signoff.main(parts, **RECIPE, budget=budget, total=TOTAL, rounds=rounds)
        out = capsys.readouterr().out
        if budget == 4:                     # the design exported is the first start point, evaluated on the first call
            assert f"metric_gp initial design 8 points (sized for a budget of {TOTAL}; 1 start point first): " in out
        assert out.endswith(stop_line(budget)) and "signoff round" not in out and "report:" not in out
        obs = parts.store.observations()
        assert len(obs) == budget and {o.step for o in obs} == {"search@tt"} and all(o.corners() == {"tt"} for o in obs)
        assert not any(parts.store.reports_dir().iterdir())                     # no rounds file, no report
    assert "metric_gp initial design 8 points (recorded by this step's first call, budget 16): " in out
    signoff.main(parts, **RECIPE, budget=TOTAL, total=TOTAL, rounds=rounds)
    last = capsys.readouterr().out
    assert "the search holds" not in last and "[run] report: " in last

    a, b = outcome(whole), outcome(parts)
    assert per_batch(parts.store.observations().by_step("search@tt")) == per_batch(whole.store.observations().by_step("search@tt"))
    assert a == b and b["design"]["search@tt"] == {"initial_design": 8, "budget": TOTAL}
    assert len(b["steps"]["signoff"]) == 2 and all(set(row[4]) == {"tb/tt", "tb/ss", "tb/ff"} or row[3] != "ok"
                                                    for row in b["steps"]["signoff"])
    if rounds == 2:
        assert set(b["steps"]) == {"search@tt", "signoff", "search@tt#2", "signoff#2"} and b["rounds"]["rounds"]
    else:
        assert set(b["steps"]) == {"search@tt", "signoff"} and b["rounds"] is None

    sized_by_call = fake_run(wide_project(tmp_path / "no_total"), metric_fn=worse_at_ss)
    signoff.main(sized_by_call, **RECIPE, budget=4)                             # without total: re-checked at once
    signoff.main(sized_by_call, **RECIPE, budget=TOTAL)
    assert json.loads((sized_by_call.store.root / "steps.json").read_text(encoding="utf-8")) == {
        "search@tt": {"initial_design": 2, "budget": 4}}
    split = per_batch(sized_by_call.store.observations().by_step("search@tt"))
    reference = per_batch(whole.store.observations().by_step("search@tt"))
    assert split[0] == reference[0] and split[1] != reference[1]


# -- 2. an advice adopted between two calls ------------------------------------------------------------------------------


def test_an_advice_adopted_between_two_calls_is_in_effect_from_the_next_batch(tmp_path, capsys):
    """Adopted after the first call (4 points): its start row comes first in the second batch (every strategy), its
    range narrows the model's points from the first batch after the design (8 points) -- the run equals one call
    budget=16 handed the same advice file, as for ``optimize``."""
    parts = fake_run(wide_project(tmp_path / "parts"), metric_fn=worse_at_ss)
    signoff.main(parts, **RECIPE, budget=4, total=TOTAL)
    row = advise(parts.spec, parts.store, {**AUTHOR, "start": [{"F": "24", "W": "1u"}], "ranges": {"F": ["20", "30"]}})
    assert row["id"] == "a1" and row["since"] == 4
    capsys.readouterr()
    signoff.main(parts, **RECIPE, budget=8, total=TOTAL)
    assert "[optimize] advice a1 in effect (since 4, by a person): 1 start row; ranges F [20, 30]" in capsys.readouterr().out
    for budget in (12, TOTAL):
        signoff.main(parts, **RECIPE, budget=budget, total=TOTAL)
    search = parts.store.observations().by_step("search@tt")
    assert search[4].origin == "advice:a1" and search[4].params == {"F": "24", "W": "1u"}
    advised = [o for o in search[8:] if o.origin.endswith("@a1")]
    assert advised and all(20 <= int(o.params["F"]) <= 30 for o in advised)
    assert not any("@a1" in o.origin for o in search[:8])                      # the design is not the advice's

    whole = fake_run(wide_project(tmp_path / "whole"), metric_fn=worse_at_ss)
    for given in advice_rules.read(parts.store.root):          # the same row, stamped with this project's fingerprint
        advice_rules.append(whole.store.root, {**given, "spec_fingerprint": whole.spec.fingerprint()})
    signoff.main(whole, **RECIPE, budget=TOTAL)
    assert outcome(parts) == outcome(whole)


# -- 3. --plan, and total below budget -------------------------------------------------------------------------------------


def test_plan_prints_the_search_and_the_stop_line_or_the_recheck_and_writes_nothing(tmp_path, site_file):
    root = wide_project(tmp_path)
    result = runner.invoke(app, ["run", "signoff", str(root), "--plan", "budget=4", f"total={TOTAL}", f"batch={BATCH}",
                                 "top=2", "rounds=2"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "[plan] opt.optimize step='search@tt' strategy=metric_gp: 0/4 points done, up to 4 more" in out
    assert f"metric_gp initial design 8 points (sized for a budget of {TOTAL}; 1 start point first): " in out
    assert (f"[plan] signoff: the search holds 4 of {TOTAL} points; the re-check runs when it reaches {TOTAL} "
            f"(signoff budget={TOTAL} total={TOTAL})") in out
    assert "sim.evaluate step='signoff'" not in out and "signoff round" not in out
    result = runner.invoke(app, ["run", "signoff", str(root), "--plan", f"budget={TOTAL}", f"total={TOTAL}",
                                 f"batch={BATCH}", "top=2", "rounds=2"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert f"[plan] opt.optimize step='search@tt' strategy=metric_gp: 0/{TOTAL} points done" in out
    assert "[plan] sim.evaluate step='signoff': 0 points × (3 testbench sims)" in out
    assert "[plan] signoff round 2 of 2" in out and "the search holds" not in out
    assert not (root / ".icopt" / "observations.jsonl").exists() and not (root / ".icopt" / "steps.json").exists()


def test_total_below_budget_is_refused_before_anything_runs_plan_too(tmp_path, site_file):
    root = wide_project(tmp_path)
    run = fake_run(root, metric_fn=worse_at_ss)
    with pytest.raises(ValueError, match=r"signoff: total=4 is below budget=8: total is the budget the search is meant"):
        signoff.main(run, **RECIPE, budget=8, total=4)
    with pytest.raises(ValueError, match="signoff: total must be a whole number"):
        signoff.main(run, **RECIPE, budget=8, total="all")
    assert not run.store.observations() and not any(run.store.root.joinpath("decks").iterdir())
    result = runner.invoke(app, ["run", "signoff", str(root), "--plan", "budget=8", "total=4"])
    assert result.exit_code != 0 and isinstance(result.exception, ValueError)
    assert "total=4 is below budget=8" in str(result.exception) and "[plan] opt.optimize" not in result.output
