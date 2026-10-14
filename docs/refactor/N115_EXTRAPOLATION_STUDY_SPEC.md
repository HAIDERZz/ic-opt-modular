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

Done 2026-10-14 on branch `n115-extrapolation-study` from `main` 24ceb99 (`git merge --ff-only main`: already up to
date). No EMX, no product code; the library only read -- its `library.yaml` and `.cache` were not written (the study's
`Library` objects used a cache directory under the evidence folder, which stayed empty; the only files the library's
cache gained after the pre-fit, a footprint file at 12:47, belong to another session -- the study never computes
footprints). Script `docs/refactor/reports/library_query/extrapolation_study.py` (b8b0444); results
`extrapolation_study.json` and `figs/EXTRAPOLATION_{error_coverage,kscale,sigma}.png` (4a610df). Write-up in Chinese with
every table, the logs and the run commands: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n115_extrapolation_study/`
(`RESULT_CN.md`, `logs/commands.log`, `progress.log`, `edge_rows.csv.gz` with every scored edge row).

- **Smoke, pilot, estimate, full.** Smoke `xfm_bs_m10_ct`, 886 jobs, 0.5 min, 12 x 2 threads, 0 failed. Pilot
  `xfm_bs_m10`, `Lp_lf` and `Qp_peak`, edge primary OD 60 um: 5 jobs, 4.1 min, plus two `xfm_ms_m10` timing jobs (its
  7-feature joint SRF and per-turns `Lp_lf` calibrations), 3.8 min. Estimate: the whole protocol 11,097 jobs, 70.7 h of
  fitting, 5.9 h on 12 workers; with the drop order 3.34 h. Full run 12:01:10-15:51:50 (3 h 51 min): the main run
  (9,281 jobs, 186.7 min, ended 15:07:54) and, from 14:24, an extra run of `xfm_bs_m10`'s curve columns in column
  order with no job started after 15:50 (377 of 866 jobs). In all 10,550 analysed jobs, 61.9 h of fitting, 0 failed.
- **Threads** (2 BLAS threads per worker, the cap re-read every 20 s): 22 from 12:01 (2 kept for the analysis), 30 from
  12:05 (`prefit_A2.exit` and `prefit_B.exit` both 0 at 12:04:40); the user raised the total cap to 80 at 14:13 and the
  coordinator gave this study 40 (14:15): the main run went to its own ceiling of 16 workers (32 threads), the extra
  run started beside it with 8 (14:24); after the main run 38 (15:07:58), then 32 (15:10, the coordinator: N-117's EMX
  took the other 40).
- **Dropped.** By the drop order -- from the end of the column list, applied first to the costliest tables and, between
  them, from the end of the table list: `xfm_bs_ap`'s ten curve columns, and of `xfm_bs_m10`'s curves k@60, k@28,
  Qs@60, Qs@28, Qp@60, Qp@28 and Ls@60 (Lp@28, Lp@60, Ls@28 were restored by the extra run; Ls@60 was cut at the
  deadline and is not analysed). Left out for rows: `ind_sym_ap_hf` `SRF_p` (3 usable rows). The 112 / 140 / 170 GHz
  anchors (0-4 rows on the multi-turn base tables) are outside the study's anchors (28 and 60 GHz on every table).
  Analysed: 226 (table, column) pairs on all 16 tables, 272 edge hold-outs, 173 levels of the main size dim.
- **Checks.** The study's full-table calibration equals the library's cached one (k_scale and sigma floor) in 218 of
  226 columns, the other 8 within 9.1e-5 relative. The in-memory "library without those rows" gives bit-identical
  predictions to a real scratch library built without them (re-measured from sNp, `Library.models`): `xfm_ms_m10_hf`
  OD_S 28 (6 columns, composed `Lp@28` among them), `ind_sym_m10_hf` OD 20+24 (5 columns). The datasets were copied by
  their keys before N-117 adds rows to `xfm_bs_m10`; the report from the copies reproduces the JSON.
- **Q1.** Along the main size dim (one step = 12-22 % of the remaining range, two = 27-50 %; 20-30 um on the base
  tables, 2-4 um on the `_hf` ones) the models hold: medians over the class's pairs of p90 at one -> two steps
  `L_lf` 1.1 % -> 2.7-3.1 %, `k_lf` 0.5-1.1 % -> 3.2-3.4 %, L curves 0.5-0.8 % -> 1.6-2.2 %, k curves 0.2-0.8 % ->
  1.2-1.7 %, Q peaks 3.1-4.2 % -> 6.9-8.0 %, Q curves 3.6-4.4 % -> 8.2-9.2 %, SRF 9.0-9.5 % -> 9.5-16 %; calibrated
  coverage 89-100 %. The secondary OD alike. Along the widths (one step = 50-90 % of the range) errors stay moderate
  (`L_lf` 0.9-1.0 % -> 2.8 %, Q curves 4.9-7.4 % -> 11-16 %) but the intervals cover only 75-78 % (`L_lf`) to 81-89 %
  (k). The offset extrapolates upwards (p90 0.2-9 %) but not below its minimum 0 (concentric windings predicted from
  offset ones: p90 20-50 %; no query lies there); the 3-level spacing does not extrapolate (p90 4-166 %).
- **Q2.** The relative sigma follows the error in rank (Spearman 0.39-0.77 at one step) but is too small: criterion 4
  at 0.15 passes 61-84 % of the one-step edge rows and 12-49 % of those miss by more than twice the in-range p90
  (27-74 % at two steps); at 0.005 the share is still 9-50 %. Against the rule's 5 % floor a ceiling of 0.02 would
  answer 11-56 % of the one-step rows with 0-10 % beyond the limit. Criterion 4 cannot replace criterion 1.
- **Q3.** The LOLO k_scale (median 1.00-1.03) is today's (1.00-1.08): coverage (i) 95-97 % under both, (ii) 95-98 %
  under both (also with each level's scale computed without it), (iii) 82-92 % today against 78-89 % LOLO; width
  ratio inside the range 0.96-1.05. Neither makes the edge intervals honest; no reason to switch.
- **Q4.** No unphysical L, Q or SRF answer (log-space models), none for `k_lf`; 8,813 of 70,176 k-curve answers at the
  edges have k >= 1, nearly all past the resonance -- the N-113 rule leaves 52 (50 on rows with a measured k). The
  rule removes 97.9-98.6 % of the answers at rows past the resonance (90,264 of 92,236 L, 85,561 of 86,806 Q, 40,207
  of 40,796 k), wrongly removes 3.9-4.8 % of the answers that exist and flags 3,513-14,275 as near the resonance.
- **The rule's verdict per column class** (the rows of all crossed dims together, as written): 0 steps on both sides
  for all seven classes -- every class has pairs failing one step out (below: 193 of 226 pairs fail, 24 reach one
  step, 8 two; above: 107 fail, 69 one, 50 two). Per crossed dim the main size dim mostly reaches one or two steps
  (above: `L_lf` 16 of 24 two steps, 8 one; L curves 35 of 43 two; Q curves 16 two, 19 one of 40; below: `L_lf` 13 of
  24 two, 10 zero; L curves 28 of 43 two, 9 zero); widths, the offset below and the spacing mostly 0. The rule's limit
  follows the in-range p90, so `SRF_p` / `SRF_s` and some multi-turn curves (in-range p90 above 10 %) pass only
  because they are poor inside too.
- **N-112 point and N-111's weak spots.** At OD 51 / 51 um the 18 full-table models (the library's cached ones,
  guard bypassed) contain every EMX truth, and so do the 11 with a LOLO scale. Weak spot 1: in-range p90 of `Lp_lf`
  1.15 % (`ind_sym_m10`), 1.89 % / `Ls_lf` 2.06 % (`xfm_bs_m10`), as N-111 found; one OD step out 3.4-5.2 %, with
  `ind_sym_m10`'s lower side covered only 32 %. Weak spot 3: `xfm_bs_m10_hf` Qp@28 / Qs@28 in range 1.15 / 1.43 %,
  one OD step out 3.3-5.4 % with 100 % coverage.
- **Deviations and readings.** (1) The drop order was applied per table, the costliest first (above), and the extra
  run restored three of `xfm_bs_m10`'s curves when the threads were raised. (2) The pooled verdict is the
  specification's; per-dim verdicts, the lower side without the offset and rows inside the box in every other dim
  are reported besides (one step differs widely between dims). (3) "Calibrated" is the query's interval (sigma
  floored, times k_scale); with the unfloored one 2 of the 452 pooled verdicts drop from one step to 0 and 11 of
  the 2,054 per-dim ones drop (its coverage is in the JSON). (4) The LOLO scale on the edges is the full table's
  with the held-out rows' own z left out, not refitted on each edge's rows (those rows remain in the LOLO fits'
  training data, which tends to lower its coverage; it equals today's anyway). (5) Criterion 3 alone refuses every
  edge row (the hull lies inside the box); the hull without the crossed dim admits 53-59 % of them. (6) The pilot
  added two timing jobs. (7) At 14:46 I stopped my own watcher by PIDs found with a name match that also hit two
  idle wrapper shells of mine and my own command shell; no study process was affected; the watcher was restarted.
