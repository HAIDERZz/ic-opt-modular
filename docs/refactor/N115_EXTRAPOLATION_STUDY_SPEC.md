# N-115: how far outside the measured range the library's models hold (2026-10-14)

User, 2026-10-14: "预测和推荐功能本质上就是要用模型去做外推的功能 。你这个说法不是等于说明我们这个东西本质查找表么？ 外推功能相当弱".

Today the domain guard's criterion 1 refuses any point outside the achieved per-dim box of a model's rows, whatever the
model says there. N-112 bypassed it once: on `xfm_bs_m10` (outer diameters 60-240 um) at primary / secondary outer
diameter 51 / 51 um, widths 4 / 4, offset 0 -- 15 % below the table's minimum -- the cached models gave, against the EMX
truth of that geometry (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/refusal_check/gp_extrapolation_od51.json`):

| column | model, guard bypassed (2 sigma, calibrated) | EMX |
| --- | --- | --- |
| `Qp@60` | 15.00 [11.87, 18.95] | 15.68 |
| `Qs@60` | 14.46 [11.71, 17.86] | 15.58 |
| `Lp@60` | 98.8 pH [95.3, 102.4] | 98.6 pH |
| `k@60` | 0.57 [0.55, 0.58] | 0.571 |
| `SRF` | 179.4 GHz [175.4, 183.5] | 180.2 GHz |
| `Qp_peak` | 17.73 [17.39, 18.08] | 17.80 |

Every truth lies inside its interval; the relative sigma of `Qp@60` grows from 0.5 % at 60 um to 11.7 % at 51 um
(below the 0.15 ceiling). One point is no rule. User decisions 2026-10-14 (list items 1, 6, 9): measure extrapolation
systematically before any product change (N-116); fold in the calibration of the interval scale by leaving whole
levels out (N-110's open candidate); look at N-111's weak spots 1 (low-frequency inductance between levels) and 3
(`xfm_bs_m10_hf` Q at 28 GHz) on the way. No EMX. No product code change.

## 1. Questions

- Q1. Per table and column: how do the error and the interval coverage grow as a point leaves the achieved box along
  one continuous dim, below the minimum and above the maximum, by one and by two level steps?
- Q2. Does the model's own relative sigma follow that error -- could criterion 4 (sigma / mu <= ceiling) be the gate
  outside the box, instead of criterion 1?
- Q3. Which interval scale (`k_scale`) gives honest intervals between levels and beyond the edges: today's (random
  5 x 20 % hold-out) or one calibrated by leaving whole levels of the main size dim out in turn? At what cost in
  width inside the range?
- Q4. Are extrapolated answers physical (L > 0, Q > 0, 0 < k < 1, SRF > 0), and what does the N-113 rule (no curve
  answer above the predicted resonance / margin) remove?

## 2. Protocol

- Tables, in this priority (all sixteen if the estimate allows): `xfm_bs_m10`, `ind_sym_m10`, `xfm_ms_m10`,
  `xfm_bs_m10_hf`, `ind_sym_m10_hf`, `xfm_ms_m10_hf`, then the AP tables, then the tapped ones.
- Columns, in this priority: `Lp_lf`, `Ls_lf`, `k_lf`, `SRF` (and `SRF_p` / `SRF_s` where declared), `Qp_peak`,
  `Qs_peak`, then the curves (`Lp`, `Ls`, `Qp`, `Qs`, `k`) at two anchors per table: the lowest declared anchor at or
  above 28 GHz and the table's 60 GHz anchor (112 GHz where 60 is not declared). Every column modelled as the library
  models it today (`library.yaml` as it is: `model`, `feature_map`, per-turns splits), with the library's own fitting
  code (`ic_opt.library.gp`, `composed`, the dataset rows) -- not a re-implementation.
- Edge hold-outs: for each continuous dim with at least three levels (outer diameters, widths, offset, spacing; turns is
  discrete and is not crossed), hold out (a) the lowest level, (b) the highest level, (c) the two lowest, (d) the two
  highest; fit on the rest -- the `k_scale` from the training rows' own 5 x 20 % calibration, so nothing leaks -- and
  predict the held-out rows. Record each held-out row's distance from the training box in level steps and as a
  fraction of the training range, and whether the guard's criterion 3 (hull) alone would have refused it.
- Q3: on the same tables and columns, a second `k_scale` from leaving each level of the primary size dim out in turn
  (the outer diameter, or the primary outer diameter); coverage of the 2 sigma interval under both scales on (i) the
  random hold-out, (ii) leave-one-level-out between levels, (iii) the edge hold-outs; the median interval width ratio.
- Independent checks: the N-112 point above (`xfm_bs_m10`, truth in
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/xfm_result.json`) under the full-table fit.
- Rule, fixed now, for N-116 to adopt or not: a (table, column, side) is extrapolable to distance d when, at d, the
  held-out p90 relative error is at most max(2 x that column's leave-one-level-out p90 inside the range, 5 %), the
  calibrated 2 sigma coverage is at least 0.85, and no answer is unphysical. Report per column class the largest d that
  meets it (0, one step, two steps) and, for Q2, the share of held-out rows with sigma / mu <= 0.15 whose error exceeds
  twice the in-range p90.

## 3. Budget and housekeeping

- At most 32 threads in all (processes x BLAS threads); until both
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/prewarm_20261014/prefit_A.exit` and `prefit_B.exit` exist
  (another job is fitting with 32 threads), at most 24. Full run at most 4 h of wall time: smoke, then a pilot (one
  table, two columns, one edge), then an estimate, then the full run; if the estimate is over, drop columns from the
  end of the priority list, then tables from its end, and say so.
- A heartbeat: append one line (time, jobs done / total, estimate of the end) to
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n115_extrapolation_study/progress.log` at least every 5 minutes
  while the full run is going.
- Read-only on the library: never write `library.yaml` or the library's `.cache`; fits stay in memory or in a cache
  directory under the evidence folder. Every script keeps its work under `if __name__ == "__main__":` (spawned workers
  import the main module).
- `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`. Never `pkill`
  by pattern; stop your own processes by PID.

## 4. Outputs

- Script(s) `docs/refactor/reports/library_query/extrapolation_study.py` (and helpers beside it); results
  `extrapolation_study.json`; figures `docs/refactor/reports/library_query/figs/EXTRAPOLATION_*.png` (error and coverage
  against distance per column class; the two `k_scale` side by side).
- Evidence `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n115_extrapolation_study/` (logs, the run
  commands) with `RESULT_CN.md` for the user: plain Chinese, every term explained the first time it appears, no names
  made up during the work, absolute paths, the answer to each of Q1-Q4 first, then the tables.

## 5. Working rules for the coder

Branch `n115-extrapolation-study` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n115-extrapolation-study`
(from `main`; `git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C
<worktree>` and absolute paths. `ruff check docs/refactor/reports` for the scripts. Commits: the scripts, then the
results with figures, then the `## Record` section at the end of this file. No merge, no push, no edit of
`docs/refactor/BACKLOG_CN.md`, no product code. Messages in the repository's style, ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: branch and base commit, smoke / pilot / estimate / full with times and threads, what was
dropped, the answers to Q1-Q4 with the numbers, the rule's verdict per column class, deviations)
