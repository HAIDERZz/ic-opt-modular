# IC-Opt 0.2.0

A subtractive rewrite: the fixed 17-step workflow with its modes and
`opt_requirement.md` templates is replaced by composable blocks, a `spec.yaml`
that states only the design problem, and recipes that state how to run it.

## What changed

- `spec.yaml` replaces `opt_requirement.md` + `config/*.yaml`; `ic-opt migrate` converts (11/11 legacy templates).
- One CLI: `ic-opt run RECIPE PROJECT [params] [--plan] [--ssh-profile P] [--cshrc F]`, `blocks`, `describe`, `call`, `doctor`, `migrate`.
- `--plan` is the only approval point (blocks, simulation count, concurrency, host).
- Evaluation = generic engine + stage pipeline (`render → spectre → ocean → extract`); stages are the extension point for other simulators.
- Suggesters (TuRBO, OpenBox GP/PRF + EIC, sobol/LHS/random) are stateless: every call rebuilds from the observation table, so re-running continues and any recipe can warm-start from any observations.
- One results store: `.icopt/observations.jsonl` + `steps.jsonl` + `sims/` + `reports/` (six report sections, four figures).
- Site envelope (`~/.ic-opt/site.yaml`) caps threads / memory per machine.
- 38k lines of `hermes_workflow` and 52k lines of tests removed; the new package is 3.8k lines with 70 tests (fake Spectre host).

## Verified

- Replay parity: 180 recorded 0.1.10 points re-aggregated bit-identically.
- Real Spectre smoke, local and over SSH: 2 points × 3 testbenches, metrics / fom / penalty identical to the 0.1.10 recording.
- Isolated remote acceptance (ADR-0001): the controller ran in a sandbox with the Maestro exports, the Cadence installation, the cshrc and the scratch hidden; the SSH run still completed with identical results, so no controller code path reads a remote path off the local disk (the 0.1.8 / 0.1.9 "fake remote" defect class). Script: `docs/refactor/analysis/isolated_ssh_smoke.sh`.

## Compatibility

`ic-opt PROJECT --real|--doctor|--continue N` still works for this release
(the project is migrated in place and the 0.2 command is printed). It will be
removed in 0.3.
