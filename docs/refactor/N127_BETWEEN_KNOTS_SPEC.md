# N-127: a curve between two knots is answered from the knots' models, without a fit (2026-10-14)

Step 3 of 3 (user decision 2026-10-14, "按这三步执行"). With N-126 a library may declare a frequency grid, and `lib.prefit`
fits every knot column (a curve's anchors and the grid's frequencies) ahead. A curve asked at a frequency between two knots is
still an extension column fitted on first use. Measured on the library's own rows
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n122_random_requirements/freq_grid/RESULT_CN.md`), interpolating a
row's curve between the grid's frequencies with PCHIP errs by at most a few tenths of a percent (p99 at or below 1.7 %), less
than the models' own error, so the knots' models can answer between them.

## 1. What changes

- When the stratum has a grid and a curve column `<curve>@<f>` is asked at an f that is no knot of that curve but lies between
  two of them: no model is fitted. Its prediction at a point is the PCHIP interpolation in frequency of the knots' predictions
  at that point, over the stencil of the two knots on each side of f (one where there is no second), with the knots' own
  calibrated bounds interpolated the same way and then widened by the curve's interpolation allowance: the p99 relative error of
  PCHIP-interpolating the stratum's own rows' measured curve over the same knots in the knot interval around f -- computed from
  the rows at first use (seconds; the extension measurements), cached per dataset key, curve and knots.
- **Domain**: a point answers only where every stencil knot column is in domain and each is below its own resonance margin at
  that point (the N-113 rule, per knot), and the curve's resonance rule at f holds. A point whose next knot above f lies above its
  resonance margin is `out_of_domain` with the reason ("between the knots a and b GHz and next to its resonance; exact=true fits
  this frequency's own model").
- A library row is answered with its measured value at f (the extension measurement), as today.
- **Answer**: the statuses as today, plus `interpolated: {"knots": [...], "allowance": a}` on an interpolated answer.
  `lib.suggest`, `lib.region`, `lib.densify` and `lib_design` use the same columns (their pools and grids predict through them).
- **`exact=true`** on `lib.query`, `lib.suggest`, `lib.region`, `lib.densify` and `lib_design` gives today's behaviour: the
  frequency's own extension column, fitted when not cached. Without a grid, nothing changes.
- `lib_design`'s prediction stage names the setting in its identity when it is not exact (as N-116's extrapolation does).
- Do not change `gp.py` / `composed.py`. Think about where the interpolation lives: a model object answering like a fitted
  column for every caller (`query.query`, `suggest.predict_all`, the guard, the resonance rule), or the callers asking for it;
  say which and why in the record.

## 2. Tests (fakes, no EMX)

At a knot the answer equals the knot column's, field for field; between knots the interpolated value and bounds on a fake whose
curve is known; the allowance widens; the domain and resonance rules (a stencil knot out of domain, the next knot above the
resonance margin); `exact=true` equals `main`'s answer; a library row answers its measured value; no fit happens between knots
(count); suggest / region / densify / lib_design through an interpolated column; without a grid everything as `main`.

## 3. Real check (no EMX; needs the grid fitted -- Claude runs `lib.prefit` on the library after N-125 and N-126 are merged and
leaves `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n126_prefit/prefit.done`; until then work with fakes)

1. N-122's 104 EMX-checked designs at their requirements' frequencies (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/
   n122_random_requirements/results_I.json`, `results_T.json`, `results_R.json`; the EMX values are there): predict each with
   the exact column (cached by N-122) and interpolated; against EMX, per quantity: median and max error, coverage, for both.
2. Rerun the 20 N-122 requests (`run_request.py` there, a copy in your scratch directory, writing elsewhere) interpolated: the
   seconds and the leaders against `out/<ID>.json`.
At most 16 threads; read the machine's busy logical CPUs from `/proc/stat` before (another user's batches take up to about 130 of
256; above 150, wait).

## 4. Documents

`docs/em/library.md` ("Asking at any frequency", `exact=`), the ic-opt skill.

## 5. Working rules for the coder

Branch `n127-between-knots` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n127-between-knots` (from `main` after N-125
and N-126 are merged; `git merge --ff-only main` first). Same tools and rules as N-125 / N-126; scratch under `<session
scratchpad>/n127/` with `progress.log`. One commit for the change with tests and documents, one for the `## Record`. No merge,
no push, no edit of `BACKLOG_CN.md` or of the real library.

## Record

**Branch and base.** `n127-between-knots` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n127-between-knots`, from
`main` at 90c8d41 (N-125 and N-126 merged; `git merge --ff-only main`: already up to date). The change with its tests and
documents: c079bc9; this record: the commit after it. Not merged, not pushed; `docs/refactor/BACKLOG_CN.md` untouched;
`git diff 90c8d41 c079bc9 -- src/ic_opt/library/gp.py src/ic_opt/library/composed.py` is empty. Nothing under the real
library was written: every real check opened it with `cache_dir=` under the scratch directory (the library's own `.cache`
read: its datasets, the grid's anchors files, the models), any fit raised (`_fit`, `_fit_direct`, `_fit_composed`
replaced by a function that raises), and the library's `.cache` holds no `between-*` file; the only new files of the real
checks are the 16 strata's allowance tables in the scratch cache. Evidence -- scripts, outputs, logs -- in
`/tmp/claude-1011/-home-zzchen-Agent-virtuoso-EDA-AI-AGENT/22a251ad-03d3-4a55-8437-b0a4e97a3152/scratchpad/n127/`
(`study/`, `real/`, `real/requests/`, `real/bench/`, `tests/`, `dev/`, `progress.log`).

### Where the interpolation lives, and why

A model object for every caller, asked for one thing by the two prediction paths. `Library.models` / `model` (unless
`exact`) hand out, for a curve column between two knots, a `query.Model` whose `gp` is a `between.Between` -- the
`StratumGP` interface the callers use: `predict` (PCHIP of the knots' means; sigma from the knots around f, raw with
`floor=False`), `available`, `posterior_cov` (the two bracketing knots' covariances weighted as their variances are, so
its diagonal is `predict(floor=False)`'s; `lib.densify` selects with it), `log_target`, `k_scale`, `n_train`,
`unavailable_nt` --, whose `guard` is a `between.Guard` (the stencil core's guards; the column's own rows give the
nearest rows), whose `rows` are the column's own rows and whose `resonance` is the curve's at f with the knot above
(`Resonance.knot_hz`, `gate_hz`). So `query.query`, `suggest.predict_all`, `in_domain`, `below_resonance`, the N-113 rule,
`srf_floors_for`, the extrapolation labels, `lib.densify`'s selection, `lib.region`'s grid, `family=`, the `Predict`
stage and `lib_refine` take it like a fitted column, with no branch of their own. What a fitted column cannot give --
which knots a point may use, its allowance and its refusals -- depends on the resonance predicted at that point; the two
paths that predict curves, `query._predict` (through `_answer_between`) and `suggest.predict_all` (through
`_between_all`), predict that resonance anyway for N-113 (its above-the-sweep settling, its measured rows), so they hand it
to `Between.answer`. The interpolated bounds are no mu +- k k_scale sigma either (PCHIP of each knot's own calibrated bounds,
then widened by a fixed fraction), so the generic bounds formula could not have reproduced them. A model object that
predicted its own resonance would have duplicated N-113's settling rules and predicted SRF twice.

### Files and why

- `src/ic_opt/library/between.py` (new): `span` (the stencil's candidate knots: up to two below f, up to two above);
  `at` / `values` (the extension measurement's rule, `Quantities.at` and `dataset._anchor_value`, for many frequencies
  at once, bit for bit); `tables` / `measure_tables` / `_table` (the allowances: one pass over a stratum's rows for every
  declared curve, a file per curve under its lock, as `anchors_at` does); `band`; `Between` (the gp interface, `answer`,
  `refusal`, `used`); `Guard`; `_Shared` / `_sharing` (a composed knot's base and SRF models -- the same objects for every
  knot of a curve -- predicted once per call, ComposedGP's own arithmetic, bit for bit); `resonance_at`; `notes`.
- `src/ic_opt/library/query.py`: `Resonance.knot_hz` / `gate_hz`; `Library._between` (the span, or None: no grid, not
  an extension column of a declared curve, a knot, outside the knots), `between_tables`, `_make_between`; `model` and
  `models` take `exact` (`models` loads or fits the stencil's knot columns and their needs, then builds the column's model:
  nothing fitted for it); `query` / `_predict` / `_answer` take `exact` (passed positionally to `_answer`, which a test
  helper patches); `_answer_between`; module docstring.
- `src/ic_opt/library/suggest.py`: `predict_all` predicts the other columns first, then `_between_all`;
  `below_resonance` holds a row to `gate_hz`; `predicted_entry` adds `interpolated` (`interpolated(entry, i)`);
  `suggest(..., exact=)`, its models through `Library.models`, its notes.
- `region.py`, `densify.py`: `exact=`, the notes, the picks' labels (`densify`'s resonance note names the knot above f
  for such a column).
- `stage.py`: `Predict(..., exact=)`, `between_identity` (the grid and the method) in the identity when not exact on a
  library with a grid; `recipes/lib_design.py`: `exact=`, a note per interpolated metric; `recipes/lib_refine.py`: its
  "fitting N model(s)" note counts a between column's knots, not the column.
- `src/ic_opt/blocks/library.py`: `exact` on `lib.query`, `lib.suggest`, `lib.region`, `lib.densify` (with `family=` too).
- `docs/em/library.md` (section 2: asking at any frequency with a grid, a new subsection "Between the knots: answers from
  the knots' models" with the study's numbers; section 4: `interpolated`, `criterion: interpolation`; section 6:
  `lib_design`; Compute: the cost; Cache: the `between-*` files), `skills/ic-opt/SKILL.md` (the library paragraph, the
  `lib.prefit` line).
- Tests: `tests/ic_opt/test_library_between.py` (new, 21); `test_library_grid.py` (one line, deviation 5);
  `test_library_stage.py` (one line, deviation 5).

### The rule as built

- **Which columns.** Library.yaml declares `frequency_grid`, the column is an extension column of a declared curve (a
  declared anchor always answers from its own model), and f lies strictly between two of the curve's knots
  (`Library.knots`, N-126). Below the first knot, above the last, at a knot, or without a grid: the column's own model, as
  before.
- **The stencil at a point.** The two knots below f (one where there is no second), the knot above f, and the second
  knot above where the point's resonance lies above `srf_margin` x that knot (the N-113 rule per knot; settled above the
  sweep counts as above), its model answers at the point's turns level and its guard accepts the point. At the end of the
  knot list there is no second knot above.
- **The value and the bounds.** Each knot's model gives its mean and its own calibrated bounds at the call's k (its
  `k_scale`; beyond the measured range N-116's widening by the farthest of the core knots' depths); the answer is their
  PCHIP interpolation in f over the stencil (scipy's `PchipInterpolator`, the same as N-126's study), the bounds widened by
  the allowance a to lo / (1 + a), hi / (1 - a) and never narrower than the mean. `rel_sigma` (the confidence ceiling's
  test) is the square root of the two bracketing knots' squared relative sigmas interpolated linearly in f; `k_scale` in
  the answer is the largest of the stencil's. The answer adds `interpolated: {"knots": [...], "allowance": a}`.
- **The allowance.** Per curve, knot interval and proximity band u = `srf_margin` x (knot above f) / resonance -- edges
  0.8, 0.9, 0.95, 1; from 1 up only rows (and points) resonating above their sweep, at the bound `srf_margin` x f_next /
  the sweep's end: the 99th percentile of |PCHIP / measured - 1| at 7 points (1/8 ... 7/8) inside the interval over the
  rows in the band, each row interpolated over the stencil a point of its resonance would use (its second knot above only
  where it has a value there); a band with fewer than 20 rows takes in the bands nearer the resonance until it has them,
  else none. A point takes the band of its resonance's calibrated lower bound (`lo`; the sweep's end above the sweep),
  clipped below 1 for a resonance inside the sweep. A stratum declaring no resonance column takes the interval's
  allowance over all its rows. The "measured" value at a probe is the extension measurement's (`Quantities.at`: between
  two sweep samples the chord between them).
- **Refused** (`out_of_domain`, `criterion: interpolation`, the reason naming `exact=true`), in this order: the
  resonance at or below `srf_margin` x the knot above f ("between the knots a and b GHz and next to its resonance (r GHz,
  at or below srf_margin x b GHz = t GHz); exact=true fits this frequency's own model"); then a core knot's guard (its
  verdict, the reason starting `knot <column>:`); then a band with too few rows ("... fewer than 20 of the table's rows lie
  as near their resonance in this interval (u = ...)"); then an allowance above 5 % ("... err by x % at the 99th
  percentile, more than the 5 % an interpolated answer may add"). The curve's own N-113 rule at f comes first, as for any
  curve (`above_resonance`).
- **Cache.** `between-<stratum>-<curve>-<dataset key>-<digest>.json` (the digest over the knots, `srf_margin` and the
  method's constants), measured at the first answer between a stratum's knots, every declared curve in one pass, each
  file under its own lock; 18-32 kB each on n28_v2.
- **exact.** `exact=true` on the four blocks and `lib_design` (`exact=True` on `query.query`, `Library.models` /
  `model`, `suggest`, `region`, `densify`, `Predict`) is the column's own model, fitted when not cached, as before.
  `lib_design`'s stage identity gains `between` -- the grid and the method's constants -- when not exact on a library with
  a grid; exact, or without a grid, the identity of before.

### The data behind the allowance and the near-resonance rule

`study/near_res.py` (no model, no EMX; the library read with `cache_dir=` in scratch): every row of the 16 tables, every
declared curve, its knots (anchors and the 67 grid frequencies), 7 points inside every interval of consecutive knots where
the row has values, PCHIP over the row's own stencil, the relative error; 16,000-392,000 points per curve, 2-51 s per
table on one thread. Two facts decide the rule. (1) N-126's 49 % outliers are rows whose resonance lies just above the
250 GHz sweep, in the last intervals (240-250 GHz): nothing measures their resonance, the library gives them values up
to the sweep's end, and their curves bend steeply there. (2) Within an interval the error grows with how close the knot
above f lies to the row's resonance limit, and a row next to it, whose stencil lacks the second knot above, errs 1.5-2.2
times as much as one with the full stencil at the same closeness -- but not by tens of per cent. Per table, the
99th percentile in %: "far" u < 0.7 (intervals from 5 GHz up: below, the Q curves' PCHIP against the 1 GHz chord
dominates, up to 3.3 % at 1-2 GHz), "near" 0.95 <= u < 1 with the full stencil, "near, cut" the same without the second
knot above, the intervals from 240 GHz up over all curves (worst curve), and the banded allowance's check: allowances
measured on half of each table's rows (seeded split), the other half's points covered (worst curve, both directions) and
the share of all rows' points whose band has 20 rows ("answered"):

| table | rows | L: far / near / near, cut | Q, k: far / near / near, cut | 240-250 GHz | two-fold coverage | answered |
| --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap | 511 | 0.19 / 1.86 / 3.19 | 0.41 / 1.62 / 3.00 | 0.05 (Lp) | 98.5 % | 91.8 % |
| ind_sym_ap_hf | 83 | 0.01 / - / - | 0.17 / - / - | 0.01 (Lp) | 98.1 % | 99.8 % |
| ind_sym_m10 | 527 | 0.18 / 1.44 / 2.23 | 0.45 / 1.32 / 2.05 | 0.31 (Lp) | 98.7 % | 92.1 % |
| ind_sym_m10_hf | 114 | 0.01 / 0.04 / 0.09 | 0.11 / 0.01 / 0.02 | 1.99 (Lp) | 97.0 % | 97.7 % |
| xfm_bs_ap | 1576 | 0.04 / 0.18 / 0.39 | 0.14 / 0.13 / 0.20 | 0.35 (Lp) | 98.7 % | 99.7 % |
| xfm_bs_ap_hf | 635 | 0.01 / 0.02 / 0.04 | 0.14 / 0.01 / 0.01 | 7.05 (Ls) | 98.4 % | 99.7 % |
| xfm_bs_m10 | 1736 | 0.04 / 0.21 / 0.38 | 0.13 / 0.16 / 0.23 | 0.80 (Ls) | 98.8 % | 99.7 % |
| xfm_bs_m10_hf | 635 | 0.01 / 0.03 / 0.05 | 0.11 / 0.02 / 0.02 | 12.47 (Lp) | 98.6 % | 99.7 % |
| xfm_ms_ap | 1400 | 0.17 / 1.43 / 2.27 | 0.30 / 1.25 / 2.20 | - | 98.6 % | 98.9 % |
| xfm_ms_ap_hf | 225 | 0.01 / 0.04 / 0.07 | 0.10 / 0.39 / 0.40 | 16.29 (Ls) | 98.4 % | 95.2 % |
| xfm_ms_m10 | 1400 | 0.17 / 1.47 / 2.32 | 0.36 / 1.38 / 2.65 | - | 98.6 % | 98.9 % |
| xfm_ms_m10_hf | 225 | 0.01 / 0.49 / 0.54 | 0.10 / 4.41 / 3.39 | 22.68 (Ls) | 98.5 % | 95.9 % |
| xfm_bs_ap_ct | 78 | 0.02 / 0.01 / 0.03 | 0.13 / 0.01 / 0.02 | 0.40 (Lp) | 97.6 % | 95.0 % |
| xfm_bs_ap_ct_hf | 183 | 0.01 / 0.01 / 0.03 | 0.14 / 0.01 / 0.01 | 13.02 (Lp) | 98.2 % | 97.4 % |
| xfm_bs_m10_ct | 47 | 0.02 / 0.01 / 0.04 | 0.11 / 0.01 / 0.01 | 0.77 (Ls) | 95.7 % | 92.4 % |
| xfm_bs_m10_ct_hf | 200 | 0.01 / 0.01 / 0.03 | 0.12 / 0.01 / 0.01 | 5.86 (Ls) | 98.0 % | 99.5 % |

Hence: the next knot above past its margin is refused (spec's minimum); the second knot above is dropped rather than
refused where it is past its margin (the cut stencil errs 1.5-2.2 times the full one at the same proximity, and the
banded allowance, whose top bands hold exactly those rows, covers it); the allowance follows the measured proximity
dependence band by band instead of refusing a fixed band, so a point next to its resonance gets the larger allowance its
neighbours in the data err by; where too few rows measure it, or it exceeds 5 % (the sweep-end intervals of the 2 um
tables' L curves, 5.9-22.7 %, and the Q curves of xfm_ms_m10_hf near their resonance, up to 6.5 %), the answer is refused.
With 10 rows instead of 20 per band, 92-99.9 % of the half-split points are answered but the top bands of the small
multi-turn tables cover 0.82-0.90; with 20, every table and curve covers 95.7-99.6 % (`study/bands_n*.txt`).

On the real library (`real/tables.py`, the branch's code, 16 strata, 8 at a time on one thread each): the knots read from
the library's anchors files in 0.04-2.5 s, the allowance tables measured in 0.34-9.0 s per stratum (9.0 s for xfm_bs_m10,
1,736 rows, five curves). Of the band cells (intervals x 5 bands), 48-78 % have no allowance (no or too few rows that near
their resonance in that interval: mostly the band from 1 up below 200 GHz, where neither a row nor a point can fall, the
near bands of the low intervals and the top intervals), 10-33 over 5 % on xfm_bs_ap_hf,
xfm_bs_m10_hf, xfm_bs_m10_ct_hf (the 240-250 GHz intervals of the L curves) and xfm_ms_m10_hf (Q curves), none elsewhere.

### Tests

Fakes, no EMX: `test_library_between.py` (21) on the synthetic inductor library (`build_library`: 39 single turns
resonating above their 60 GHz sweep, 66 two-turn rows resonating at 16-60 GHz) with the grid {2, 2, 16, 22} -- Lp's knots
2, 4, 8, 10, 16, 38, 60 GHz, fitted once per module and copied with the cache into each test: `at` equals `Quantities.at`
bit for bit on a uniform and an uneven sweep; the allowances measured in one pass, once per dataset, curve and knots (a
second library reads the file), an interval's allowance over all rows equal to the p99 of PCHIP over the rows' own
extension columns (`dataset.anchors` at the probes); at a knot the knot column's answer field for field (with and without
the grid, nothing fitted, no table measured); between knots (Lp@6 at a 26 GHz resonance) value = PCHIP of the knots'
answers to 1e-12, lo = PCHIP(lo) / (1 + a) and hi = PCHIP(hi) / (1 - a) with the band's allowance, wider than the knots'
own interpolated interval, the network's own Lp at 6 GHz inside, nothing fitted for Lp@6; Lp@13 over 8, 10, 16 at that
point and over 8, 10, 16, 38 for a single turn above the sweep; the last interval (38-60 GHz) with one knot above: the
single turns' resonance just above the sweep makes its allowance 537 % -- refused, in the batch path without failing, the
interpolation itself PCHIP over the three knots; `exact=True` equal to the library without a grid at three points (and
the block's `exact="true"`); a library row's measured value at 6 and 13 GHz; the next knot above past the resonance
margin (a resonance of 18 GHz at 13 GHz) refused with the reason, `above_resonance` at 14 GHz as before; a core knot's
guard refusing (patched) gives `knot Lp@16: ...`; too few rows (`N_MIN` 10,000) and too large an allowance (`CAP` 1e-9)
refused; `predict_all` equal to `query` at six points (values, bounds, labels, ok); a knot without a model at a turns level
(Lp@38 has none for two turns) leaves those points out in both paths; two composed knots sharing their base and SRF
models predict each once and answer bit for bit; `lib.suggest`, `lib.region` and `lib.densify` through Lp@6 with nothing
fitted, their notes and labels, and `exact=True` fitting Lp@6; `lib.densify`'s covariance diagonal equal to
`predict(floor=False)`; the `Predict` stage's identity and `predictions.json`, the `lib_design` recipe's notes and
leaders with and without `exact`; without a grid as `main` (own model, no `knot_hz`, no table, the identity of before);
`ic-opt call lib.query ... exact=true|false`.

`OMP_NUM_THREADS=2`, no `-n`, the files of N-125 / N-126's runs (`tests/ic_opt/test_library*.py`,
`test_lib_refine.py`, `test_lib_tap.py`, `test_spec_library.py`, `test_cli_recipes.py`, `test_blocks.py`,
`test_lock.py`): **463 passed, 7 skipped** in 662 s on the committed code; the same files exported from 90c8d41: 442
passed, 7 skipped in 672 s (the 21 new are `test_library_between.py`'s). `ruff check src tests` clean.

### Real check (no EMX; needs the grid fitted: the coordinator's `lib.prefit`, `prefit.done` "exit 0, 141 min")

Conditions: the numbers of steps 1 and 2 come from runs after `prefit.done` (02:20), busy logical CPUs of 256 over 10 s
before them 0.5-0.8; at most 16 of my threads. Earlier partial runs on the strata the prefit had finished (the inductor
designs and requests, xfm_bs_ap / xfm_bs_ap_hf / xfm_bs_m10 / xfm_bs_m10_hf designs; guarded against any fit; busy 50-60)
found the same; the step-0 tables (model-free) and the single-table timing below were measured during the prefit (busy
4.6 and 51).

1. **N-122's designs** (`real/designs.py`, `summarise.py`; `results_I.json`, `results_T.json`, `results_R.json`): 104
   points, 101 with EMX values (3 inductor candidates the generator could not draw), at their requirements' frequencies,
   every curve N-122 measured -- 334 answers. Each predicted with `exact=true` (N-122's cached columns: all 334 equal to
   N-122's recorded predictions, bit for bit) and between the knots. None refused; none flagged near its resonance. 39
   designs sit at a knot (50, 28, 17, 12, 85 GHz: both answers identical), 62 between knots (202 answers, every one
   over four knots). Between against exact: median 0.002 %, at most 0.333 % apart (xfm_bs_m10 Qs@52, xfm_ms_m10 Ls@24);
   the interval median 1.01 times as wide, at most 1.89; allowances median 0.011 %, at most 0.258 %. Against EMX, error
   median / max and coverage of the calibrated interval:

   | quantity | answers | exact: median / max / coverage | between: median / max / coverage | between-knot designs only: exact / between coverage |
   | --- | --- | --- | --- | --- |
   | Lp | 101 | 0.088 % / 3.461 % / 0.891 | 0.087 % / 3.461 % / 0.901 | 0.919 / 0.935 (62) |
   | Ls | 44 | 0.380 % / 3.774 % / 0.909 | 0.381 % / 3.774 % / 0.909 | 0.923 / 0.923 (26) |
   | Qp | 101 | 0.401 % / 6.974 % / 0.911 | 0.424 % / 6.974 % / 0.911 | 0.887 / 0.887 (62) |
   | Qs | 44 | 1.543 % / 11.607 % / 0.909 | 1.504 % / 11.587 % / 0.909 | 0.923 / 0.923 (26) |
   | k | 44 | 0.876 % / 6.246 % / 0.864 | 0.805 % / 6.246 % / 0.864 | 1.000 / 1.000 (26) |

   Between the knots only (62 / 26 designs): medians 0.073 / 0.422 / 0.495 / 1.462 / 0.371 % (Lp / Ls / Qp / Qs / k)
   against exact's 0.073 / 0.455 / 0.467 / 1.437 / 0.373 %, maxima 1.786 / 1.606 / 5.609 / 11.587 / 1.874 % against
   1.792 / 1.928 / 5.696 / 11.607 / 1.872 %. Not worse than the exact columns. These designs lie far from their
   resonances, so the near-resonance part of the rule is backed by the rows (above), not by EMX.

2. **The 20 requests** (`real/requests/run_request.py`: N-122's script copied, `cache_dir=` in scratch, the fit guard,
   `EXACT=1` for the frequency's own columns; outputs `real/requests/out/`, `compare.py`). Inductors one after another
   at 4 threads; transformers in 4 streams of 4 threads (16), then T02 and T05 again, exact and between, in 2 streams of 4
   for a cost under the same load. N-122's own times (`out/<ID>.json`) include waiting for first-use fits (4 threads, 20
   requests at once); its re-timed suggest with every model cached was 71-120 s (T02, T05).

   | request | f0 GHz | suggest s (N-122) | region s (N-122) | leaders against N-122's |
   | --- | --- | --- | --- | --- |
   | I01 | 92 | 3.7 (14.6) | 1.5 (0.9) | the same 10, same order |
   | I02 | 118 | 3.7 (11.9) | 1.5 (0.8) | the same 10, same order |
   | I03 | 50 (knot) | 3.0 (17.9) | 0.9 (0.9) | the same 10, same order |
   | I04 | 27 | 3.4 (25.5) | 2.5 (1.3) | 9 of 10: one ind_sym_ap candidate differs (5 candidates, 3 shared; satisfying 58 vs 59); region robust ind_sym_m10 49 vs 51, mean 70 both |
   | I05 | 28 (knot) | 2.7 (2.7) | 1.1 (1.2) | the same 10, same order |
   | I06 | 85 (knot) | 2.4 (14.0) | 0.8 (0.8) | the same 10, same order |
   | I07 | 123 | 3.7 (11.0) | 1.5 (0.8) | the same 10, same order |
   | I08 | 109 | 3.2 (13.0) | 1.5 (0.8) | the same 10, same order |
   | I09 | 126 | 3.6 (11.3) | 1.5 (0.8) | the same 10, same order |
   | I10 | 17 (knot) | 2.8 (29.6) | 1.3 (1.4) | the same 10, same order |
   | T01 | 116 | 45.8 (2698.3) | 28.2 (20.5) | none in both (the same unreachable reasons) |
   | T02 | 52 | 153.7 (2863.3) | 147.2 (87.2) | the same 10, two neighbouring pairs of candidates swapped; region robust xfm_bs_ap 669 vs 695 (mean 2113 vs 2100), within 7 elsewhere |
   | T03 | 108 | 58.3 (2706.7) | 36.0 (28.3) | none in both |
   | T04 | 17 (knot) | 34.1 (2830.9) | 92.6 (119.8) | the same 10, same order |
   | T05 | 24 | 284.4 (2904.7) | 308.3 (160.1) | 6 of 9 shared: xfm_ms_ap 4 of 5 candidates, xfm_ms_m10 2 of 5 (measured designs the same); region robust xfm_ms_m10 664 vs 243 (mean 8408 vs 8085), xfm_ms_ap 104 vs 119 |
   | T06 | 101 | 61.6 (2707.8) | 39.9 (28.5) | none in both |
   | T07 | 12 (knot) | 178.0 (2818.5) | 187.4 (233.0) | the same 10, same order |
   | T08 | 50 (knot) | 56.9 (2864.6) | 100.1 (126.0) | the same 8, same order |
   | T09 | 129 | 43.5 (2698.7) | 26.3 (22.6) | none in both |
   | T10 | 46 | 100.8 (2861.2) | 72.1 (44.1) | the same 6, same order |

   Same load, 2 streams of 4 threads: T02 between suggest 151.0 s / region 144.2 s, exact 66.5 / 64.4 s; T05 between
   277.3 / 308.2 s, exact 115.8 / 127.7 s -- interpolating costs 2.2-2.4 times the frequency's own cached columns (four
   knots' models per curve: 22 model predictions per point against 10 for T02's four curves and SRF), and the exact runs'
   leaders equal N-122's for both. A single table, `real/bench/region_one.py` (xfm_bs_ap, T02, 4 threads):
   `lib.region` 67-74 s between, 30-31 s exact; `lib.suggest` without builds 72-74 s against 34-35 s. T05's differences
   come from the calibrations, not the interpolation: xfm_ms_m10's Ls@24 has its own k_scale 1.228 (median relative error
   0.0148), its knots Ls@23 / Ls@26 1.066 / 1.084 (0.0124 / 0.0157), so the interpolated intervals are narrower there and
   more grid points are robust (each column's k_scale carries N-125's seed-to-seed spread of 1.0-1.36).

   How often the interpolation refuses (`real/bench/verdicts.py`): over the 13 between-knot requests' `lib.suggest`
   pools (the rows and 8192 Sobol points per table), among the points where the frequency's own column answers (inside
   the knots' domains, resonance above `srf_margin` x f): 92.2 % answered, 6.6 % refused for too few rows that near
   their resonance, 1.2 % next to the resonance (the knot above), none for the cap; the high-frequency requests (I01, I08,
   T01, T03, T06, T09: 92-129 GHz) 79-88 % answered. Those points need `exact=true`.

### Deviations and doubts

1. *The second knot above is dropped, not refused,* where the point's resonance lies at or below `srf_margin` x that knot
   (or its model does not answer there). The specification's domain sentence reads as "each stencil knot below its own
   margin", with "one where there is no second" for the stencil; the study shows the cut stencil 1.5-2.2 times worse than
   the full one at the same proximity and the banded allowance covers it. Refusing instead would refuse one more knot
   interval below every resonance.
2. *The allowance is per knot interval and proximity band* (the coordinator's instruction), measured at 7 points per
   interval rather than at f alone, with a 20-row minimum per band and a 5 % cap -- choices of mine, from the study:
   N_MIN 10 covers worse next to the resonance; the cap removes the sweep-end intervals of the 2 um tables' L curves
   (5.9-22.7 %) and parts of xfm_ms_m10_hf's Q curves. 6.6 % of the requests' pool points are refused for too few rows;
   a lower minimum, or merging neighbouring intervals' bands, would answer more of them.
3. *The measured value between sweep samples* is the library's own chord (`Quantities.at`), so the allowance includes
   PCHIP against that chord: up to 3.3 % for the Q curves in 1-5 GHz, where PCHIP is likely closer to the device than the
   chord. The allowance is what `exact=true` would be compared with, so it is kept.
4. *`criterion: interpolation`* is a string where the guard's criteria are 1-3.
5. *Existing tests:* `test_library_grid.py::test_the_entry_changes_no_dataset_key_and_no_cache_file_name` asks Lp@15
   with `exact=True` (with the grid, Lp@15 is now interpolated, which measures the grid's columns and writes allowance
   files -- the test checks N-126's file names); `test_library_stage.py`'s pin of `surrogate_pipeline`'s arguments adds
   `"exact": False`. `_answer` takes `exact` and `srf` positionally after `extrapolate`: `test_library_resonance.py`'s
   helper replaces `_answer` with a function of positional arguments.
6. *lib_refine* interpolates too (it asks through `query.query`); it has no `exact` parameter (not asked for).
7. *lib_design's identity* names the interpolation whenever it is not exact on a library with a grid, even when none of
   its metrics lies between knots (as N-116 names its fraction whatever the points): a lib_design run on n28_v2 now that
   it declares the grid is a new generation of predictions unless `exact=true`.
8. *Cost:* 2.2-2.4 times the frequency's own cached columns in `lib.suggest` / `lib.region` on the composed transformer
   tables (four knots' models per curve, a composed knot's shared parts once per call; knots on the same rows check their
   domain once); against a frequency whose own models are not cached (N-122: 45-48 minutes for the transformer requests)
   it is minutes. Where the frequency's own columns are cached, `exact=true` is the faster.
9. *Not exercised against EMX:* the 104 designs lie far from their resonances (none refused, none flagged), so the
   near-resonance rule rests on the row study and its two-fold check.
10. *rel_sigma* (the confidence ceiling) does not include the allowance; with the 5 % cap the allowance only widens the
    interval.
11. *Fixes found by the real check:* two runs of step 2 failed and were rerun after fixes, both in
    `between._interpolate`, each with a test: `PchipInterpolator` refused a knot's NaN prediction at a turns level without
    a model (7 of the 10 inductor requests' first run), and an interval at the end of a curve's knots has no second knot
    above (T03's first run). The fakes had neither case before.
