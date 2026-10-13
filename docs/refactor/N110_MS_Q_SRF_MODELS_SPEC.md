# N-110: the multi-turn tables' Q and SRF columns take the two methods proven on the single-turn tables (2026-10-14)

The user pointed out (2026-10-14) that the weakest columns of the second-generation library are the multi-turn
transformer tables' Q and SRF -- random hold-out median 1.7-2.2 % (Qs_peak, SRF, Qs@28), the SRF's leave-one-level-out
median 7.7 % / 9.4 % (N-107's GP numbers, before the N-109 split) -- and that two methods already proven on the
single-turn tables are not used there: (1) the Q curves as "Q peak x ratio" (`model: ratio`; N-19 option 2 on the
single-turn tables: the offset band's Qs@40 maximum error 23 % -> 14 %, coverage 80 % -> 100 %); (2) the dimensionless
coordinates for SRF and the Q peaks (`feature_map: xfm_ms_dimensionless`, a manifest change only; with it the column is
one joint model instead of one per secondary-turns level; on k the change cut p90 to about a quarter). The single-turn
tables' Qp@28 between outer-diameter levels (median 5.8 % / 7.2 %) is N-19's open item "the Q columns' interpolation
between levels, a matter of coordinates". The user decided: proceed -- measure on the library itself, adopt what holds,
change the real manifest only for that. No EMX, no product code change (the manifest validation already allows both:
`_check_model` takes `ratio` on any curve with a Q peak in the stratum, and the map needs exactly the stratum's dims).

## 1. Tables, variants, columns

Run on the library as N-109 leaves it (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`, the `_hf`
parts in strata of their own), each variant on a scratch library (a temporary root whose `library.yaml` holds the
variant and whose part stores link to the real ones, the method of `docs/refactor/reports/library_query/xfm_bs_q_ratio_acceptance.py`
and `xfm_ms_ratio_acceptance.py`: read both and build the new script from them).

Multi-turn tables `xfm_ms_ap`, `xfm_ms_m10` (and their `_hf` strata, columns at the anchors their rows support):

| variant | SRF, Qp_peak, Qs_peak | Qp, Qs curves |
| --- | --- | --- |
| M0 | as today (direct, one model per secondary-turns level) | as today (direct) |
| M1 | `feature_map: xfm_ms_dimensionless` | as today |
| M4 | as today | `model: ratio` |
| M2 | `feature_map: xfm_ms_dimensionless` | `model: ratio` |
| M3 | `feature_map: xfm_ms_dimensionless` | `model: ratio, feature_map: xfm_ms_dimensionless` |

Columns scored: SRF, Qp_peak, Qs_peak, Qp@28, Qs@28, Qp@60, Qs@60 (the `_hf` strata: their anchors with at least 50
usable rows, e.g. 60 and 112).

Single-turn tables `xfm_bs_ap`, `xfm_bs_m10` (and their `_hf` strata):

| variant | Qp_peak, Qs_peak | Qp, Qs curves |
| --- | --- | --- |
| B0 | as today (direct) | as today (`model: ratio`) |
| B1 | `feature_map: xfm_bs_dimensionless` | as today |
| B3 | as today | `model: ratio, feature_map: xfm_bs_dimensionless` |
| B2 | `feature_map: xfm_bs_dimensionless` | `model: ratio, feature_map: xfm_bs_dimensionless` |

Columns scored: Qp_peak, Qs_peak, Qp@28, Qs@28, Qp@112, Qs@112.

## 2. Protocols and statistics

- (i) the library's own calibration (5 x 20 %, `gp.holdout` / `composed.holdout`): median, p90, max relative error,
  2-sigma coverage before calibration, `k_scale` (`gp.calibration_scale`), median calibrated relative half-width.
- (ii) leave one outer-diameter level out (secondary OD for xfm_ms, primary OD for xfm_bs; up to 8 levels with at least
  25 usable rows strictly inside the column's range, spread evenly over the eligible ones; the same levels for every
  variant): median, p90, max relative error, coverage of the interval calibrated with (i)'s `k_scale`.
- Every number from the library's own fitting and calibration code on the scratch libraries; GP settings exactly as
  `Library._fit_inputs` / the composed settings give them.

## 3. Decision rule (fixed before the run)

Per table, decide in this order, each against the configuration chosen so far:
1. SRF: mapped or not, on SRF's own numbers.
2. Q peaks: mapped or not, on Qp_peak and Qs_peak.
3. Q curves, given the peaks' choice: among {as today, ratio} if the peaks stay direct (M0 / M4), among {as today,
   ratio, ratio-mapped} if the peaks are mapped (M1 / M2 / M3); for xfm_bs among {as today, ratio-mapped} (B0/B1 or
   B3/B2).
A change wins when, over the columns it changes: (a) the median over those columns of p90(ii) changed / p90(ii) today
is at most 0.9; (b) no changed column's p90(i) is more than 10 % above today's; (c) every changed column's calibrated
(ii) coverage is at least 0.90, or, where today's is below 0.90, not below today's by more than 0.02. Several winners:
the lowest median p90(ii) ratio. No winner: keep today's.

## 4. Applying it

Adopted changes go into the real `library.yaml` (back it up first as `library.yaml.bak_<UTC>`), then the changed
columns are refitted and calibrated by the library (`ic-opt call lib.coverage` / `lib.query` on them) and their cached
calibration (median_rel, coverage, k_scale) checked against the study's (i) numbers. Nothing adopted: the manifest is
not touched.

## 5. Deliverables and rules

- `docs/refactor/reports/library_query/ms_q_srf_models_study.py` (run / report), `ms_q_srf_models_study.json`,
  `figs/MS_Q_SRF_MODELS_p90.png`; `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n110_ms_q_srf_models/RESULT_CN.md`
  (plain Chinese: each statistic explained in one sentence where first used; the decisions per table and why; the
  before/after numbers; what changed in the manifest) with `run.log`; the INDEX line; the `## Record` of this file.
- Branch `n110-ms-q-srf-models` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n110-ms-q-srf-models` (from
  `main`; `git merge --ff-only main` first); `PYTHONPATH=<worktree>/src` with the main tree's `.venv/bin/python`;
  `git -C <worktree>` and absolute paths everywhere (a `cd` inside a compound command does not take effect);
  `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`; scratch
  libraries and caches under `ic-opt-accept/n110_ms_q_srf_models/scratch/`.
- Resources: at most 32 threads in all (another study runs beside it with the other 32 of the user's 64): workers x
  BLAS threads <= 32. Pilot one table first, write the estimate into `run.log`; above 3 hours drop M4 and B3 first, then
  the `_hf` strata, then levels to 4, and say so. Long runs detached (`setsid`, `run.log`, a `DONE` file), polled with
  sleeps; never match your own shell with `pgrep -f` (use bracketed patterns or the launcher's pid).
- Commits in the repository's style ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`: the script,
  the results and INDEX line, the record. No merge, no push, no BACKLOG edit, no `src/` edit.
