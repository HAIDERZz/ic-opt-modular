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

## Record

Done 2026-10-14; the private library changed only in `library.yaml` (backup `library.yaml.bak_20261013T193513Z`, UTC); no EMX, no product code. Full write-up (Chinese), logs and the manifest tools: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n110_ms_q_srf_models/RESULT_CN.md`; script `docs/refactor/reports/library_query/ms_q_srf_models_study.py` (86ad45f), results `ms_q_srf_models_study.json` and `figs/MS_Q_SRF_MODELS_p90.png` (0d70e8e).

- Run: 8 tables, 984 jobs (76 direct calibrations, 860 composed calibration folds, 48 level folds of one table level with all its variants), 12.7 h of fitting on 16 workers x 2 threads, 0 failed: smoke `xfm_ms_m10_hf` 1.2 min, pilot `xfm_ms_m10` plus three `xfm_bs_m10` timing jobs 10.5 min, estimate 1.0 h (below 3 h: nothing dropped), the rest 38.2 min. 251 scratch datasets checked row by row and value by value against the real ones (M0 / B0 with the real dataset key). The level-fold assembly (fits shared between variants with the same settings and rows) is bit-identical to `Library.models`; protocol (i) is the library's own `gp.holdout` / `Library._composed_fold`.
- Decisions by the rule, per table (SRF / Q peaks / Q curves): `xfm_ms_ap`, `xfm_ms_m10` mapped / mapped / as today ((a) 0.128 / 0.113 and 0.185 / 0.260; M2 and M3 fail (b) on xfm_ms_ap Qp@60, +10.3 %, or (c) on the Qs columns, 56.8-88.9 %); `xfm_ms_ap_hf`, `xfm_ms_m10_hf` mapped ((a) 0.025 / 0.022) / direct ((c): the mapped Qs_peak covers 77.0 / 73.9 % between levels) / as today (M4 (a) 1.09 / 1.13); `xfm_bs_ap`, `xfm_bs_m10` Q peaks mapped ((a) 0.056 / 0.061), curves as today (B2 (b): p90(i) of Qp@28 / Qs@28 +34-67 %); `xfm_bs_ap_hf`, `xfm_bs_m10_hf` unchanged ((c): the mapped Qp_peak covers 87.9 / 88.8 %; B3 (a) 0.933 / (b) +26-35 %).
- p90(ii) today -> adopted: multi-turn SRF 22.6 / 26.2 % -> 2.9 / 2.9 %, _hf SRF 5.6 / 8.0 % -> 0.14 / 0.17 %, multi-turn Qp_peak / Qs_peak 17.4 / 15.0 and 19.4 / 12.6 % -> 2.3 / 3.5 and 4.2 / 3.9 %, single-turn 28.5 / 40.0 and 37.2 / 34.9 % -> 1.6 / 2.2 and 2.2 / 2.2 %, the single-turn ratio curves built on the mapped peaks 24.5-34.0 % -> 13.9-24.9 %; p90(i) down in every adopted column; calibrated (ii) coverage of every changed column >= 0.91. Today's direct models are poor between OD levels (median error 7-23 % on every held-out level) while the random hold-out (1-6 % p90) cannot see it.
- Manifest: text edit by `apply_manifest.py` (comments kept): `&xfm_ms_quantities` (xfm_ms_m10, xfm_ms_ap) gets SRF / Qp_peak / Qs_peak with `feature_map: xfm_ms_dimensionless`, the new `&xfm_ms_hf_quantities` (the two _hf) SRF only; `&xfm_bs_quantities` (xfm_bs_ap, xfm_bs_m10) Qp_peak / Qs_peak with `feature_map: xfm_bs_dimensionless`, the new `&xfm_bs_hf_quantities` holds today's entries for the two _hf; every changed line commented, two header lines; a YAML-parse check shows every stratum as before except the adopted entries. The library then refitted and calibrated all 40 changed columns (the single-turn Qp / Qs at all seven anchors too, built on the peaks; two processes of 8 x 2 threads, 26.7 / 26.1 min): the cached calibration of the 20 scored ones (k_scale, median_rel, coverage before, rows) is bit-identical to protocol (i); `lib.coverage` shows them cached and `lib.query` answers from the cache.
- Readings the text leaves open, each computed both ways (none changes a decision): the ms _hf anchors are every anchor with >= 50 usable rows (the example's 60 / 112 alone: same); on xfm_bs step 2's (a) stays on the peaks while (b) / (c) also cover the ratio curves built on them; a column without levels (the _hf 170 GHz) enters (b) only; "calibrated" is the k_scale-widened unfloored interval (with the query's sigma floor: same).
- Deviations and surprises: part of `run.log` was recovered from the running process (a `sed -i` replaced the file while the full run appended to it; the job results are per-job files and unaffected); the pilot added three `xfm_bs_m10` timing jobs to the one table; `--manifest` and a SHA-256 guard were added to the script after the run, because it reads "today" from `library.yaml` (with the backup it reproduces the committed JSON); the six changed strata's previously cached models of unchanged columns (xfm_ms_m10 Lp_lf, Ls_lf, k_lf, SRF_p, SRF_s, Lp@10-60; xfm_bs_ap / xfm_bs_m10 Lp_lf, SRF, SRF_p) refit at their first use, about 30-40 min on 32 threads, not done; N-111 (`pacosyt_eval/scripts/p2_ours.py` opens the live library, xfm_bs_m10 and xfm_ms_m10 among its tables) had finished before the change at 03:35:13 local, but a rerun would see the new entries.
