"""CLI surface, built-in recipes through a fake Cadence host, and 0.1 -> 0.2 migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ic_opt import __version__, blocks, recipe
from ic_opt.cli import app
from ic_opt.recipes import coarse_to_fine, fix_run, optimize, signoff
from ic_opt.site import Site
from ic_opt.spec import load_spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, minimal_spec, needs_turbo
from tests.ic_opt.test_blocks import maestro_export

TEMPLATES = sorted((Path(__file__).parent / "fixtures" / "legacy").glob("opt_requirement*.md"))
runner = CliRunner()


def project(tmp_path: Path, *, corners=(), metrics=None, export_params="F=20 W=0.6u", waveform_metrics=True) -> Path:
    """A project directory whose Maestro export lives on the local disk."""
    d = minimal_spec()
    export = maestro_export(tmp_path / "maestro", "tb", params=export_params)
    d["testbenches"][0]["maestro_point_root"] = str(export)
    d["corners"] = [{"id": c} for c in corners]
    if metrics is not None:
        d["metrics"], d["constraints"], d["objective"] = metrics, [], None
    d["simulator"] = {"parallel_jobs": 2, "threads_per_run": 4, "timeout_s": 60, "license_check": True}
    d["budget"] = {"max_simulations": 200}
    root = tmp_path / "proj"
    root.mkdir()
    (root / "spec.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    return root


def fake_run(root: Path, metric_fn=lambda p, tb, c: {"NF": 5.0 + int(p["F"]) / 10}, **fake_kwargs) -> recipe.Run:
    spec = load_spec(root / "spec.yaml")
    store = RunStore(root)
    return recipe.Run(root, spec, store, FakeSpectreExecutor(store.root / "sims", metric_fn, **fake_kwargs), None, Site(max_threads=16))


# -- CLI ----------------------------------------------------------------------------


def test_version_blocks_and_describe():
    assert runner.invoke(app, ["--version"]).output.strip() == f"ic-opt {__version__}"
    listing = runner.invoke(app, ["blocks"])
    assert listing.exit_code == 0 and set(blocks.REGISTRY) == {line.split()[0] for line in listing.output.splitlines()}
    described = runner.invoke(app, ["describe", "sim.evaluate"])
    assert described.exit_code == 0 and described.output.startswith("sim.evaluate(spec:") and "pipeline" in described.output
    assert runner.invoke(app, ["describe", "no.such"]).exit_code == 2


def test_run_plan_prints_shape_and_simulates_nothing(tmp_path):
    root = project(tmp_path, corners=("tt", "ss"))
    result = runner.invoke(app, ["run", "optimize", str(root), "--plan", "budget=12", "batch=4", "strategy=turbo"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "recipe optimize  spec demo" in out and "params {'budget': 12, 'batch': 4, 'strategy': 'turbo'}" in out
    assert "[plan] [FAIL] tools" in out                       # this machine has no spectre; the preview still completes
    assert "[plan] netlist.import tb:" in out and "corners ['tt', 'ss']" in out
    assert ("[plan] opt.optimize step='optimize' strategy=turbo: 0/12 points done, up to 12 more in batches of 4 × "
            "(2 testbench sims) = 2 simulations per point on local, 2 workers × 4 threads (spectre)") in out
    assert not (root / ".icopt" / "observations.jsonl").exists() and not any((root / ".icopt" / "decks").iterdir())


def test_run_rejects_unknown_recipe_and_bad_params(tmp_path):
    root = project(tmp_path)
    assert runner.invoke(app, ["run", "nope", str(root)]).exit_code != 0
    assert runner.invoke(app, ["run", "optimize", str(root), "--plan", "budget"]).exit_code != 0


def test_call_block_by_name(tmp_path):
    root = project(tmp_path)
    result = runner.invoke(app, ["call", "points.sobol", str(root), "n=4", "seed=1"])
    assert result.exit_code == 0, result.output
    points = json.loads(result.output)
    assert 1 <= len(points) <= 4 and all(set(p) == {"F", "W"} for p in points)


def test_doctor_command_exit_code_reflects_checks(tmp_path):
    root = project(tmp_path)
    result = runner.invoke(app, ["doctor", str(root)])
    assert result.exit_code == 1 and "[FAIL] tools" in result.output and "[ok] export:tb" in result.output


# -- recipes ------------------------------------------------------------------------


def test_optimize_recipe_end_to_end(tmp_path, capsys):
    run = fake_run(project(tmp_path, corners=("tt", "ss")))
    optimize.main(run, strategy="random", budget=6, batch=3, seed=1)
    obs = run.store.observations()
    assert len(obs) == 6 and all(o.status == "ok" and set(o.children) == {"tb/tt", "tb/ss"} for o in obs)
    assert (run.store.reports_dir() / "report.md").exists()
    printed = capsys.readouterr().out
    assert "[doctor] [ok] tools: /cad/bin/spectre, /cad/bin/ocean" in printed and "[doctor] [ok] license: spectre version 23.1.0.242.isr4 64bit; lmstat 1 features (spectre 2/10)" in printed
    assert "[run] report:" in printed
    assert run.jobs == 2                                       # spec parallel_jobs 2, site 16/4 = 4 slots


def test_optimize_recipe_stops_when_doctor_fails(tmp_path):
    run = fake_run(project(tmp_path))
    run.spec.simulator.parallel_jobs = 8                       # 8 × 4 threads > site max 16
    with pytest.raises(RuntimeError, match="envelope"):
        optimize.main(run, strategy="random", budget=2)
    assert not run.store.observations()


def test_fix_run_recipe_with_waveforms_and_no_metrics(tmp_path):
    root = project(tmp_path, corners=("tt", "ss"), metrics=[])
    (root / "points.json").write_text(json.dumps([{"F": "22", "W": "0.8u"}, {"F": "30", "W": "1.2u"}]))
    (root / "waveforms.json").write_text(json.dumps([{"name": "nf_curve", "expression": 'getData("NF" ?result "pnoise")'},
                                                     {"name": "gain", "expression": 'getData("G" ?result "pac")'}]))
    run = fake_run(root, lambda p, tb, c: {}, nil_waveforms={"gain"})
    fix_run.main(run, points="points.json", waveforms="waveforms.json")
    obs = run.store.observations()
    assert [o.params["F"] for o in obs] == ["22", "30"] and all(o.step == "fix_run" for o in obs)
    child = obs[0].children["tb/tt"]
    assert child.status == "failed:extract" and child.issues == ["waveform gain returned nil"]
    assert (root / child.sim_dir / "metrics" / "waveforms" / "nf_curve.csv").read_text().startswith("freq,value")
    assert not (root / child.sim_dir / "metrics" / "waveforms" / "gain.csv").exists()


def test_signoff_recipe_searches_one_corner_then_checks_all(tmp_path):
    run = fake_run(project(tmp_path, corners=("tt", "ss", "ff")))
    signoff.main(run, corner="tt", budget=4, batch=2, top=2, strategy="random", seed=2)
    obs = run.store.observations()
    search, check = obs.by_step("search@tt"), obs.by_step("signoff")
    assert len(search) == 4 and all(set(o.children) == {"tb/tt"} for o in search)
    assert len(check) == 2 and all(set(o.children) == {"tb/tt", "tb/ss", "tb/ff"} for o in check)
    assert {o.key for o in check} <= {o.key for o in search}


@needs_turbo
def test_coarse_to_fine_recipe_warm_starts_the_fine_step(tmp_path):
    run = fake_run(project(tmp_path))
    coarse_to_fine.main(run, coarse_budget=4, fine_budget=3, batch=2, seed=3)
    obs = run.store.observations()
    assert len(obs.by_step("coarse")) == 4 and len(obs.by_step("fine")) == 3
    assert all(o.origin.startswith("suggest:turbo") for o in obs.by_step("fine"))


def test_user_recipe_file_composes_blocks(tmp_path):
    root = project(tmp_path)
    recipe_file = tmp_path / "sweep.py"
    recipe_file.write_text(
        "from ic_opt import blocks as b\n"
        "def main(run, *, per_dim=2):\n"
        "    deck = b.import_netlists(run.spec, run.executor, run.store)\n"
        "    obs = b.evaluate(run.spec, b.points_grid(run.spec, per_dim=per_dim), run.executor, run.store, deck=deck, step='sweep')\n"
        "    run.note(f'best {b.best(run.spec, obs)[0].params}')\n"
    )
    main = recipe.load_recipe(str(recipe_file))
    run = fake_run(root)
    main(run, per_dim=2)
    assert len(run.store.observations().by_step("sweep")) == 4
    with pytest.raises(ValueError, match="unknown recipe"):
        recipe.load_recipe("nope")


# -- migration ----------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES, ids=[t.stem for t in TEMPLATES])
def test_migrate_every_legacy_template(tmp_path, template):
    old = tmp_path / "old"
    old.mkdir()
    (old / "opt_requirement.md").write_text(template.read_text())
    new = tmp_path / "new"
    result = runner.invoke(app, ["migrate", str(old), str(new)])
    assert result.exit_code == 0, result.output
    spec = load_spec(new / "spec.yaml")
    note = (new / "MIGRATION.md").read_text()
    assert spec.testbenches and spec.variables
    if "fix_run" in template.stem:
        assert f"ic-opt run fix_run {new} points=points.json" in note
        points = json.loads((new / "points.json").read_text())
        assert points and all(set(p) == {v.name for v in spec.variables} for p in points)
        assert ("waveforms=waveforms.json" in note) == ("metrics_only" not in template.stem)
    else:
        assert f"ic-opt run optimize {new} strategy=" in note and spec.metrics and spec.objective is not None
    assert len(TEMPLATES) == 11


# -- 0.1 shim -----------------------------------------------------------------------


def test_legacy_command_line_is_translated_and_project_migrated_in_place(tmp_path):
    from ic_opt.cli import legacy_argv

    old = tmp_path / "legacy"
    old.mkdir()
    (old / "opt_requirement.md").write_text((TEMPLATES[0].parent / "opt_requirement.turbo.md").read_text())
    assert legacy_argv(["run", "optimize", str(old)]) is None and legacy_argv([str(old)]) is None

    argv = legacy_argv([str(old), "--doctor", "--ssh-profile", "lab", "--cadence-cshrc", "/env.csh"])
    assert argv == ["doctor", str(old), "--ssh-profile", "lab", "--cshrc", "/env.csh"]
    assert (old / "spec.yaml").exists() and (old / "MIGRATION.md").exists()

    assert legacy_argv([str(old), "--real", "--dry-orchestration"]) == \
        ["run", "optimize", str(old), "strategy=turbo_trust_region", "budget=40", "batch=8", "seed=20260528", "--plan"]
    assert legacy_argv([str(old), "--real", "--continue", "5"])[:4] == ["run", "optimize", str(old), "strategy=turbo_trust_region"]
    assert "budget=5" in legacy_argv([str(old), "--real", "--continue", "5"])      # 0 done so far + 5

    fix = tmp_path / "fix"
    fix.mkdir()
    (fix / "opt_requirement.md").write_text((TEMPLATES[0].parent / "opt_requirement.fix_run.md").read_text())
    assert legacy_argv([str(fix), "--real"]) == ["run", "fix_run", str(fix), "points=points.json", "waveforms=waveforms.json"]
    assert (fix / "points.json").exists()
