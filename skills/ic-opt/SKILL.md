---
name: ic-opt
description: Drive IC-Opt 0.2 from a project directory — write or migrate spec.yaml, preview with --plan, run built-in or custom recipes (optimize, fix_run, coarse_to_fine, signoff), read .icopt results and reports, and advise on Spectre/OCEAN design optimization, locally or over SSH.
---

# IC-Opt operator

You run Spectre/OCEAN optimizations with `ic-opt` and explain the results.
The user owns the design question; you own the mechanics and the evidence.

## Mental model

- `spec.yaml` = WHAT: testbenches, corners, variables (grid), metrics, constraints, objective, simulator limits, budget.
- Recipe = HOW: `main(run, **params)` composing blocks. Built-ins: `optimize`, `fix_run`, `coarse_to_fine`, `signoff`.
- Blocks: `env.doctor`, `netlist.import`, `points.{fixed,sobol,grid,one_at_a_time,from}`, `sim.evaluate`, `opt.suggest`, `opt.optimize`, `analyze.best`, `analyze.report`. `ic-opt blocks` lists them, `ic-opt describe NAME` shows a signature.
- Results: `PROJECT/.icopt/observations.jsonl` (fact table), `steps.jsonl`, `sims/<obs>/<tb>/<corner>/`, `reports/report.md|html`.
- Re-running continues: `opt.optimize` stops when its step holds `budget` observations; raise `budget` to go on.

## Procedure

1. **Spec.** New project: write `spec.yaml` from the user's testbenches (Maestro export roots must contain `netlist/input.scs`), variables with bounds and step, OCEAN metric expressions, constraints, objective. Old project (`opt_requirement.md`): `ic-opt migrate OLD NEW`, then read `NEW/MIGRATION.md` for the matching command. Ask before inventing bounds, metric formulas or constraints.
2. **Environment.** `ic-opt doctor PROJECT [--ssh-profile P] [--cshrc F]` — tools, license, exports, site envelope, budget must all be `[ok]`. Cadence env file lives on the simulation host and is given explicitly: `--cshrc`, `IC_OPT_CADENCE_CSHRC` or `cshrc:` in `~/.ic-opt/site.yaml`.
3. **Plan.** `ic-opt run RECIPE PROJECT [params] --plan`. Show the user the printed block sequence, simulation count, `jobs × threads` and host. This is the approval point — get a yes before dropping `--plan`.
4. **Run.** Same command without `--plan`. Long runs: run in the background and read `steps.jsonl` / `observations.jsonl` for progress. Never exceed the site envelope (`~/.ic-opt/site.yaml`, default 128 threads / 128 GB); `run.jobs` already trims `parallel_jobs` to fit.
5. **Read.** `reports/report.md`: Best observed, Top feasible candidates, Constraint margins, Parameter importance (SHAP), Corners, Space compression advisory; figures `feasible_convergence`, `constraint_margins`, `bottleneck_weighted_score`, `convergence`. Cross-check with `observations.jsonl` (`status`, `issues`, per-child `sim_dir`).
6. **Advise.** Tie every recommendation to observations: which constraint binds (margins), which variables matter (importance), whether the search saturated (convergence), which corner fails (corners). Suggest the next recipe or spec change, not a vague "run more".

## Recipe cheatsheet

```bash
ic-opt run optimize PROJECT budget=60 batch=10 strategy=openbox_gp_eic|turbo|random seed=0 [corners='["tt"]']
ic-opt run fix_run  PROJECT points=points.json [waveforms=waveforms.json] [corners='["tt","ss"]']
ic-opt run coarse_to_fine PROJECT coarse_budget=40 fine_budget=40
ic-opt run signoff  PROJECT corner=tt budget=60 top=5
ic-opt run my_recipe.py PROJECT key=value          # custom composition
ic-opt call analyze.best PROJECT k=5               # any block, spec/store/observations filled in
```

Custom recipe skeleton:

```python
from ic_opt import blocks as b
def main(run, *, n=12):
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.evaluate(run.spec, b.points_sobol(run.spec, n), run.executor, run.store, deck=deck,
                     step="screen", cshrc=run.cshrc, parallel_jobs=run.jobs)
    run.note(f"report: {b.report(run.spec, obs, run.store)}")
```

Point-level status: `ok`, `constraint_failed`, `metric_failed`, `failed:<stage>` (render / spectre / ocean / extract) — the child's `issues` and `sim_dir` (`spectre.stderr`, `metrics/ocean.log`) say why.

## Reading failures

- `doctor` `[FAIL] tools/license` → wrong or missing cshrc; `export:<tb>` → Maestro export not at `maestro_point_root/netlist/input.scs` on that host.
- `netlist.import` `ValueError: <var> was not found` → the variable is not a top-level `parameters` entry in the exported netlist (only those may be swept).
- `failed:spectre` with a license message → retry later; `failed:ocean` → check the metric expression in `metrics/probe.ocn`; `metric_failed` → the OCEAN expression returned nil/non-finite (see `ocean_scalars.tsv`).
- `BudgetExceeded` → raise `budget.max_simulations` in `spec.yaml` (the spec fingerprint changes; previous observations still reuse by point).

## Remote hosts

`--ssh-profile P` (an OpenSSH alias or `user@host`) runs every simulation on that host; the spec's paths are remote paths, netlists are fetched through SSH, results come back to `.icopt/`. Nothing on the controller reads remote paths directly (see `docs/adr/0001-remote-filesystem-boundary.md`).

## 0.1 command line

`ic-opt PROJECT --real|--doctor|--continue N [--ssh-profile P] [--cadence-cshrc F]` is translated and the project migrated in place; prefer the 0.2 form it prints.
