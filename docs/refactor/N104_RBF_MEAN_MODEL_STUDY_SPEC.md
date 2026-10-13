# N-104: a scipy RBF interpolator as the mean model, measured against the library's GP (2026-10-13)

`docs/refactor/PACOSYT_COMPARISON_CN.md` read PACOSYT (Passos et al. 2023) against the device library. PACOSYT found a
scipy `RBFInterpolator` the most accurate of six techniques on its own random hold-out. Our library fits one Matern 5/2
Gaussian process per (table, column) because it needs an interval that can be calibrated. The user decided on
2026-10-13: measure, on the second-generation private library and with the library's own hold-out protocols, whether an
RBF interpolator plugged into the same model interface gives lower point errors than the GP. Even if it does, it could
only serve as the mean model under the existing calibration; this study measures point errors only. No EMX, no change to
product code, the library read only.

## 1. The mean model behind the library's interface

A study-only class `StratumRBF(gp.StratumGP)` in the study script (section 4), nothing in `src/`:

- Same constructor and attributes as `gp.StratumGP` (dims, ranges, log_target, nt_mode, kernel -- ignored --, nt_dim,
  feature_map, k_scale, sigma_floor_rel), plus the RBF settings `rbf_kernel` (scipy's name, default
  `thin_plate_spline`), `degree` (the polynomial degree) and `smoothing`.
- `fit`: `per_nt` splits by turns level exactly as the base class (the same `MIN_NT_SAMPLES`, the same `unavailable_nt`),
  each level a `StratumRBF` on the other dims; the joint path fits `scipy.interpolate.RBFInterpolator` on `self._scale(x)`
  -- the base class's scaling: min-max on the ranges, or the feature map's dimensionless features -- against
  `log(y)` for a log target, else `y`.
- `_predict`: the per_nt path as the base class (sub-models); the joint path returns `exp(mu_log)` (or `mu`) and
  **sigma NaN** everywhere: an interpolator has no uncertainty, and this study scores the mean only.
- `available`, `_scale`, `predict`, `predict_bounds` inherited. `posterior_cov` is not needed and raises.
- The composed models (`composed.ComposedGP`, the manifest's `model: resonance` / `ratio` columns) build their parts
  through the module attribute `gp.StratumGP`; the study switches the mean model by rebinding that attribute
  (`gp.StratumGP = StratumRBF` / back to the original) in the process that fits -- the worker processes included, each
  fold job stating which model it runs. `composed.holdout_fold` and `ComposedGP.fit` then build every part (lf, srf,
  part) as RBF; nothing else changes. Keep a reference to the original class before rebinding; `StratumRBF` subclasses
  the original.

## 2. Variants

| name | inputs | target | degree | smoothing |
| --- | --- | --- | --- | --- |
| `gp` | the library's (`Library._fit_inputs` settings: feature map, per_nt, Matern 5/2, 4 restarts) | log | -- | -- |
| `rbf_d1` | the same inputs as `gp` | log | 1 | 1e-6 |
| `rbf_d3` | the same | log | 3 | 1e-6 |
| `rbf_d5` | the same | log | 5 | 1e-6 |
| `rbf_raw_d5` | PACOSYT's: the raw dims min-max scaled (no feature map: `feature_map=None`, `nt_mode` per_nt where the table has `nt_dim`, else joint) | log | 5 | 1e-6 |

A degree the row count cannot support (scipy raises when the polynomial has more terms than points, or the fit is
singular) is recorded as `unfit` for that fold and column, not an error of the study. `rbf_raw_d5` isolates the effect
of the coordinates: PACOSYT models in raw dimensions. Every variant sees exactly the rows, the hold-out splits and the
target transform the `gp` variant sees.

## 3. Tables, columns, protocols

Library: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2` (read only; open it with
`query.Library(root, calibrate=False, cache_dir=<scratch>)` so nothing is written under the library;
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`). Six tables:

| table | columns (the manifest's model for each applies: direct, `resonance`, `ratio`) |
| --- | --- |
| `ind_sym_ap`, `ind_sym_m10` | `Lp_lf`, `Qp_peak`, `SRF_p`, `Lp@28`, `Qp@28`, `Lp@112`, `Qp@112` |
| `xfm_bs_ap`, `xfm_bs_m10` | `Lp_lf`, `k_lf`, `Qp_peak`, `SRF`, `Lp@28`, `Qp@28`, `k@28`, `Lp@112`, `Qp@112`, `k@112` |
| `xfm_ms_ap`, `xfm_ms_m10` | `Lp_lf`, `Ls_lf`, `k_lf`, `Qs_peak`, `SRF`, `Ls@28`, `Qs@28`, `k@28`, `Ls@112` |

Data and settings of each column come from the library itself: direct columns through `Library._fit_inputs(stratum,
quantity)` (x, y in the fit unit, settings), composed columns through `Library._composed_data` and
`_composed_settings` (x, y, lf_y, srf_y and the three parts' settings). A column whose target is not all positive (so the
library would not use a log target) keeps the library's choice.

Protocols, both on the column's usable rows:

- **(i) random hold-out**: the library's calibration protocol, `gp.split` with seeds 0-4 and fraction 0.2 -- for a
  direct column the splits of `gp.holdout`, for a composed column those of `composed.holdout_fold` (the held-out rows are
  the curve's positive rows' split; every part refitted without them). Score the held-out rows' relative error.
- **(ii) leave one level out**: every level of the table's outer-diameter dim (`outer_diameter_um` for inductors,
  `primary_outer_diameter_um` for xfm_bs, `secondary_outer_diameter_um` for xfm_ms) that has at least 25 usable rows and
  lies strictly inside the column's range of that dim is held out whole, the models fitted on the rest, its rows scored;
  when a table has more than 8 such levels, take 8 spread evenly over the sorted eligible levels (the first, the last and
  six in between), the same levels for every column of the table and every variant. For a composed column write a
  `level_fold` that mirrors `composed.holdout_fold` with an explicit held mask instead of `gp.split`.

Per (table, column, protocol, variant): the pooled relative errors' median, p90, max, `n_scored`, the rows skipped
(no model for their turns level) and `unfit` folds; per (ii) also per level. Time every fit and prediction (wall clock,
per job) so the report can say what each model costs.

Verdict per (table, column, protocol): `rbf_better` when the best RBF variant's p90 is below the GP's by more than 10 %
of the GP's and its median is not above the GP's; `gp_better` the other way round; else `tie`. Summarise the counts and
list every column where the max error of an RBF variant exceeds 3 x the GP's (interpolators can overshoot between levels).
Give PACOSYT's own variant (`rbf_raw_d5`) its own line in the summary.

## 4. Deliverables

- `docs/refactor/reports/library_query/rbf_mean_model_study.py`: `run` (argparse: `LIBRARY_ROOT SCRATCH OUT_JSON
  [--tables ...] [--columns ...] [--variants ...] [--workers 16] [--threads 2] [--levels 8] [--pilot]`), the folds as jobs
  in a spawn `ProcessPoolExecutor` (`multiprocessing.get_context("spawn")`; each worker caps BLAS with
  `threadpoolctl.threadpool_limits(threads)`; the main work under `if __name__ == "__main__":` -- the library spawns too);
  `report` (`OUT_JSON --md OUT_MD --fig OUT_PNG`): the Markdown tables and one figure, p90 relative error per column for
  `gp` and the best RBF variant, protocols (i) and (ii) side by side, one panel per table. Style and structure like
  `xfm_bs_q_ratio_acceptance.py` (ruff-clean; `docs/refactor/reports` is in the ruff include). `--pilot`: one table
  (`ind_sym_ap`), two seeds, two levels, every variant, to measure the cost before the full run.
- Results: `docs/refactor/reports/library_query/rbf_mean_model_study.json` and
  `figs/RBF_MEAN_MODEL_STUDY_p90.png` (into the repository, as the other studies);
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/RESULT_CN.md` (outside the repository: the
  tables, the verdict counts, the cost per model, what was run and when, the exact commands), with `run.log` and the pilot's
  output beside it.
- `docs/refactor/reports/INDEX_CN.md`: one line under "非页面产物" naming the script, the JSON and the figure.
- This file's `## Record` section (section 5).

## 5. Working rules for the coder

- Branch `n104-rbf-mean-model-study` in the worktree
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n104-rbf-mean-model-study` (made from `main`; confirm with
  `git merge --ff-only main`). Run with `PYTHONPATH=<worktree>/src` and the main tree's `.venv/bin/python`; the main
  checkout stays untouched. Use `git -C <worktree>` and absolute paths everywhere (a `cd` inside a compound command does
  not take effect in this environment).
- Resources: at most 32 threads in all (`--workers 16 --threads 2` with `OMP_NUM_THREADS=2`); the pilot first, then
  estimate the full run from its timings and write the estimate into the log before starting it; if the estimate exceeds
  3 hours, lower `--levels` to 4 and say so. Start the full run detached (`setsid`, output to `run.log`, a `DONE` file at
  the end) and poll the `DONE` file with sleeps of a few minutes; never match your own shell with `pgrep -f` (use the pid
  the launcher printed, or `kill -0`).
- The library is read only: no EMX, no `lib_signoff`, no writes under the library root (`cache_dir` points at the scratch
  directory `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/scratch/`). No product code changes
  (`src/` untouched); no new dependencies (scipy and scikit-learn are installed).
- Commits: one for the script, one for the results, documents and the INDEX line, one for the record; messages in the
  repository's style, each ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Do not merge, do not
  push, do not touch `docs/refactor/BACKLOG_CN.md`.
- The record (`## Record` at the end of this file): what ran (tables, columns, folds, fits, wall time, threads), the
  verdict counts per protocol and per table, the three or four columns where the difference is largest in either
  direction with their numbers, the cost per model, every deviation from this specification, and anything it did not
  foresee. Numbers only from the JSON the script wrote.
