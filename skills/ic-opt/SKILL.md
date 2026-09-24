---
name: ic-opt
description: Drive IC-Opt 0.3 from a project directory — write or migrate spec.yaml, preview with --plan, run built-in or custom recipes (optimize, fix_run, coarse_to_fine, signoff), read .icopt results and reports, and advise on Spectre/OCEAN design optimization, locally or over SSH; query, design on and sign off EM device libraries.
---

# IC-Opt operator

You run Spectre/OCEAN optimizations with `ic-opt` and explain the results.
The user owns the design question; you own the mechanics and the evidence.

## Mental model

- `spec.yaml` = WHAT: testbenches, corners, variables (grid), metrics, constraints, objective, simulator limits, budget.
- Recipe = HOW: `main(run, **params)` composing blocks. Built-ins: `optimize`, `fix_run`, `coarse_to_fine`, `signoff`, and for the device library `lib_design`, `lib_signoff`.
- Blocks: `env.doctor`, `netlist.import`, `points.{fixed,sobol,grid,one_at_a_time,from}`, `sim.evaluate`, `opt.suggest`, `opt.optimize`, `analyze.best`, `analyze.report`; device library `lib.{load,coverage,query,suggest,region,densify}`; process profiles `em.validate_profile`. `ic-opt blocks` lists them, `ic-opt describe NAME` shows a signature.
- Results: `PROJECT/.icopt/observations.jsonl` (fact table), `steps.jsonl`, `sims/<obs>/<tb>/<corner>/`, `reports/report.md|html`.
- Re-running continues: `opt.optimize` stops when its step holds `budget` observations; raise `budget` to go on.

## Procedure

1. **Spec.** New project: write `spec.yaml` from the user's testbenches (Maestro export roots must contain `netlist/input.scs`), variables with bounds and step, OCEAN metric expressions, constraints, objective. Old project (`opt_requirement.md`): `ic-opt migrate OLD NEW`, then read `NEW/MIGRATION.md` for the matching command (it lists resource values filled with 0.1 defaults: confirm them with the user). A project or library part whose store an earlier ic-opt wrote: `ic-opt migrate-store PROJECT --dry-run`, then the same without `--dry-run`, once, before its next run and before any spec edit — until then its EM observations are not reused, its Spectre ones only while the spec stays unchanged, and those the 0.2.0 release wrote not at all. A resource the old spec left to its default gets that default written in first (0.2.0: `simulator.threads_per_run: 10`), then the store is restamped, then the user's own value goes in (README, "Stores written by an earlier version"). Ask before inventing bounds, metric formulas or constraints — and never invent resources: `simulator.parallel_jobs` / `threads_per_run` / `timeout_s` and `em.threads` / `memory_gb` / `timeout_s` are required and come from the user. `simulator.license_queue_timeout_s` (Spectre's `+lqtimeout`) is optional: leave it out unless the user gives a value, and Spectre waits for a license as it does by itself. A spec `ic-opt migrate` converted from a 0.1 project carries 0.1's `license_queue_timeout_s: 900` (0.1 always passed `+lqtimeout 900`; MIGRATION.md lists it): keep it, or remove it if the user wants Spectre's own wait.
2. **Environment.** `ic-opt doctor PROJECT [--ssh-profile P] [--cshrc F]` — tools, license, exports, site envelope, budget must all be `[ok]` (`[WARN] machine` is advisory: the entry exceeds what the host reports). It asks only for the tools the spec's pipeline runs: `spectre` / `ocean` / the license for testbenches, the EMX binary (`em.binary`) for devices; a pure EM spec needs no Spectre. `~/.ic-opt/site.yaml` has one entry per host — `local` (the machine running ic-opt) and one per `--ssh-profile` name — each with `max_threads` and `max_memory_gb`: required, no defaults, and a missing file, entry or field refuses with the entry to add. The numbers are the user's; never write them yourself. The simulation host's entry bounds the jobs; `hosts.local` is also the budget of the device library's own compute (model fits, BLAS threads, prediction chunks), which always runs on the machine running ic-opt, so library work needs it even when every simulation is remote. Cadence env file lives on the simulation host and is given explicitly: `--cshrc`, `IC_OPT_CADENCE_CSHRC` or `cshrc:` in that host's entry; a `.csh` / `.cshrc` / `.tcsh` / `.tcshrc` file is sourced by csh, any other by sh.
3. **Plan.** `ic-opt run RECIPE PROJECT [params] --plan`. Show the user the printed block sequence, simulation count, `jobs × threads` and host. This is the approval point — get a yes before dropping `--plan`.
4. **Run.** Same command without `--plan`. Long runs: run in the background and read `steps.jsonl` / `observations.jsonl` for progress. Never exceed the host's entry in `~/.ic-opt/site.yaml` (a job bigger than the host is refused, also under `--plan`); `run.jobs` already trims `parallel_jobs` to fit.
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
                     step="screen", cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
    run.note(f"report: {b.report(run.spec, obs, run.store)}")
```

Point-level status: `ok`, `constraint_failed`, `metric_failed`, `failed:<stage>` (render / spectre / ocean / extract) — the child's `issues` and `sim_dir` (`spectre.stderr`, `metrics/ocean.log`) say why.

## Reading failures

- `doctor` `[FAIL] tools/license/emx` → wrong or missing cshrc; `export:<tb>` → Maestro export not at `maestro_point_root/netlist/input.scs` on that host.
- `netlist.import` `ValueError: <var> was not found` → the variable is not a top-level `parameters` entry in the exported netlist (only those may be swept).
- `failed:spectre` with a license message → retry later; `failed:ocean` → check the metric expression in `metrics/probe.ocn`; `metric_failed` → the OCEAN expression returned nil/non-finite (see `ocean_scalars.tsv`).
- `failed:<stage>` with `timed out after Ns` → that point's command ran past `simulator.timeout_s` / `em.timeout_s` and was killed with its process group; the other points ran on. Ask the user before raising the timeout: a hung point can also be a pathological design point.
- Stopped by Ctrl-C → the last `steps.jsonl` row has status `interrupted` with `recorded` / `interrupted` / `not_started` point ids; interrupted points were not recorded, and running the recipe again simulates them (nothing queued had started).
- `BudgetExceeded` → raise `budget.max_simulations` in `spec.yaml`: the problem fingerprint leaves the budget out, so the observations keep being reused and counted. That holds for a store an earlier ic-opt wrote only after `ic-opt migrate-store PROJECT`: its old stamps match the spec only as it was, and raising the budget first cuts the run off from them.

## EM devices

A spec with `devices` (pcell generator instances: `generator`, `profile`, `ports`, `fixed`, `variables`), `em` (EMX settings, `process_file` on the simulation host) and `bindings` (which testbench nport instance takes which device's sNp, `terminals` in sNp column order) runs the EM circuit pipeline `pcell → emx → bind_nport → spectre → ocean → extract` with the same recipes; a spec with devices but no testbenches runs `pcell → emx → measure` (device metrics: `quantity` Lp/Qp/Ls/Qs/k at `frequency_hz`, or Lp_lf / Lp_res / Qp_peak / SRF_p / k_lf). Set `IC_OPT_PROFILE_DIRS` to the directory holding the private `<profile>/rule.yaml`. Real EMX runs need the user's approval after `--plan` (keep `simultaneous_frequencies: 0`; jobs × `threads` and jobs × `memory_gb` fit the host's site.yaml entry); EMX results are cached per device by GDS + ports + physics settings + the process file's content (never its path). `ic-opt migrate` converts em-opt's `em_opt_requirement.md`. Failures: `failed:pcell` (unbuildable geometry / DRC), `failed:emx:<device>`, `failed:bind_nport` (instance or terminal count), `failed:measure` (quantity outside the sweep, no resonance for SRF). A device's `topology` (`drives` as `[plus, minus]` pairs, `grounded`) defaults from its ports: four ports drive the secondary reversed, `[[P1, N1], [N2, P2]]`, the built-in families' winding sense; an `ok` point whose `issues` report `k_lf = … < 0` comes from a generator that winds its secondary the other way — the issue names the `topology:` line to state in the spec (docs/em/devices.md).

## Device library and process profiles

A library is a directory of em_only run stores plus `library.yaml` (strata = family × metal body, parts, dims, quantities named after the measure kernel); `ic-opt call lib.<name> LIBRARY_ROOT key=value` answers from it (dicts print as JSON). Its datasets, calibrations and fitted models are cached in `LIBRARY_ROOT/.cache/`, or in `cache_dir=DIR` (every `lib.*` block, `lib_design`, `lib_signoff`); a library the user cannot write to still answers — the cache then goes to `~/.cache/ic-opt/<key>/`, the answer's `notes` say so, and the files already in its `.cache/` are still used. `lib.load` / `lib.coverage` show what is there; `lib.query stratum=S 'params={...}'` returns per quantity `measured`, `predicted` (mu with calibrated ~95% bounds `lo`/`hi`), `uncertain` (sigma / mu above the quantity's ceiling `rel_sigma_max`: the call's `rel_sigma_max=`, else the quantity's in `library.yaml`, else 0.15), `out_of_domain` (criterion + reason + nearest rows) or `above_sweep` (SRF); `lib.suggest stratum=S 'targets={"Lp_lf": {"target": 1.2e-9, "tol": 0.03}}' objective=max:Qp_peak` lists measured designs first, then predicted candidates that meet the targets with their whole interval and pass the real generator + DRC audit. For sweep ranges use `lib.region stratum=S 'targets={"<quantity>@<f>": {"min": <a>, "max": <b>}, ...}' group_by=DIM,DIM trend=QUANTITY:DIM` — example: an inductance window and a minimum Q at one frequency; `<f>` is one of the stratum's `anchors_ghz`, the numbers (SI units) are the user's, and a `{min, max}` window works in `lib.suggest` too. It grids the region meeting every target (bracketed by a coarse pass with the windows `relax` wider, 0.10 unless given: raise it when the region looks cut short) and returns per-dim ranges at two levels, `robust` (whole calibrated `k`-sigma interval inside, where a sweep should centre; the answer echoes `k` and each quantity's `rel_sigma_max`) and `mean` (prediction inside, the optimistic envelope), plus `binding`, `edge`, `group_by` rows, `trend`, candidates and the measured rows that meet all; per-dim ranges are projections, so quote the `group_by` rows. To grow a library where its models are least sure, whatever the targets, `lib.densify stratum=S n=N [quantities=Q,Q] ['bounds={"DIM": V, "DIM": {"min": A, "max": B}}'] [score=ceiling|typical] out=FILE` proposes N geometries by model uncertainty inside the bounds (each scored by its raw posterior sigma over the quantity's confidence ceiling `rel_sigma_max`, or with `score=typical` over the quantity's held-out median error; picked greedily with the other candidates' variances updated as if each pick were measured) and reports the pool's sigma `before` / `after`; FILE is the `candidates=` of `lib_signoff ... top=N adopt=true --plan`, which is the approval point. `ic-opt run lib_design PROJECT library=ROOT` optimizes an em_only spec on predictions (no EMX, no budget spent); `ic-opt run lib_signoff PROJECT library=ROOT candidates=REPORT --plan` then real EMX compares predictions with measurements (z, inside) and `adopt=true` folds the rows back into the library. Never present a `predicted` value as measured; say which answers were out of domain and why. Model fits run in spawned worker processes: Python you write that fits library models (`Library.models`, `lib.region`, `lib.suggest`, `lib.densify`, the library recipes) must be a script file with its top-level work under `if __name__ == "__main__":` — code piped to `python -` on standard input breaks the workers; `ic-opt call` / `ic-opt run` need nothing. Guide: `docs/em/library.md`.

A new process needs `<profile>/rule.yaml` (authoring guide `skills/author-process-rule/SKILL.md`) proven by `ic-opt call em.validate_profile PROFILE_DIR proc=SITE.proc generate=true` (exit 1 on any failed stage) before any EMX run. When the `.proc` lives on the simulation host, add `--ssh-profile P`: `proc=` is then that host's path, read through SSH into a temporary directory and deleted after the check.

## Remote hosts

`--ssh-profile P` (an OpenSSH alias or `user@host`) runs every simulation on that host, within its `P` entry of `~/.ic-opt/site.yaml`; the spec's paths are remote paths, netlists are fetched through SSH, results come back to `.icopt/`. Nothing on the controller reads remote paths directly (see `docs/adr/0001-remote-filesystem-boundary.md`). A command that times out is ended with its whole process group, on the host too (it runs under `setsid`; the timeout's message says how the cleanup went), so no stray Spectre / EMX keeps the host's cores.

## 0.1 command line

`ic-opt PROJECT --real|--doctor|--continue N` (0.1) is refused since 0.3 (exit 2; nothing is migrated or run). Convert once with `ic-opt migrate PROJECT NEW` (skip it when PROJECT already has `spec.yaml`: 0.2 converted it in place, so NEW is PROJECT), then take the command `NEW/MIGRATION.md` names through Procedure steps 2–4. `--doctor` → `ic-opt doctor NEW`, `--continue N` → the same `ic-opt run` with `budget=` the points done + N, `--dry-orchestration` → `--plan`, `--cadence-cshrc F` → `--cshrc F`.
