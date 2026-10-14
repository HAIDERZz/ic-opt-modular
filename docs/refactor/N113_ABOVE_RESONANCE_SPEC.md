# N-113: no curve answer above the device's own resonance (2026-10-14)

Found by N-112 (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/RESULT_CN.md`, finding 3).
`lib.query` on `xfm_bs_m10_hf` at primary/secondary outer diameter 51/51 um, width 4/4 um, offset 0 answers, with status
`predicted` and a relative sigma of 0.3-0.4 %:

| column | answer | EMX at that geometry |
| --- | --- | --- |
| `SRF` | 162.8 GHz [162.7, 163.0] | 162.9 GHz |
| `Lp@170` | 307 pH | past the resonance: the L curve is negative (-614 pH) |
| `Qp@170` | 10.1 | negative |
| `k@170` | 1.022 [1.013, 1.031] | k above 1 is impossible |
| `Lp@140` | 260 pH | the curve reads 259 pH, but a measured row with this SRF has no `Lp@140`: 162.9 GHz <= 1.25 x 140 GHz |

Two mechanisms let these through. A `model: resonance` curve holds (f0 / SRF)^2 at `composed.U_MAX` (the rise stays
at 5) and relies on the SRF's uncertainty to widen the interval until the confidence gate flags it; a confident SRF
(as here) leaves a narrow interval. A direct curve model (`k@170`) is guarded by the achieved box and hull of every row
that has the column, which here includes other widths and offsets at outer diameters up to 60 um, while the rows of
this family (same widths, no offset) have `k@170` only up to 40 um.

User decision 2026-10-14 (list item 3): fix it, and before any answer outside the measured range is allowed (N-116).

## 1. The rule

A curve column `<curve>@<f>` (a declared anchor or an extension column) is answered at a point only where the
resonance predicted there lies above `srf_margin x f` -- the condition under which a measured row has a value for that
column (`dataset._anchor_value`). The resonance is the stratum's system `SRF` when the stratum declares it; otherwise
per drive: `SRF_p` for `Lp` / `Qp`, `SRF_s` for `Ls` / `Qs`, both for `k` -- the choice `suggest.implied_srf` already
makes. `srf_margin` is the curve's own (`stratum.quantities[curve].srf_margin`). SRF is fitted in GHz and answered in Hz
(`query.fit_unit`): compare in Hz.

## 2. What changes

1. `ic_opt/library/query.py`, `_predict` (so `lib.query`, `lib_signoff`'s predictions and every caller of `query`): for a
   curve column, first take the resonance answer at the point through the same `_predict` path (so `above_sweep` and an
   exact measured row behave as today), then:
   - resonance `above_sweep` whose `lower_bound` is at or above `srf_margin x f`: answer the curve as today;
   - resonance `predicted` or `uncertain` with value at or below `srf_margin x f`: the curve's answer is the new status
     `above_resonance`, without `value` / `lo` / `hi`, with `srf` (the resonance answer's `value`, `lo`, `hi`),
     `threshold_hz` (= `srf_margin x f`), `frequency_hz`, `srf_margin`, `unit`, `nearest` (as `out_of_domain` gives it)
     and `reason`: "the resonance predicted here (<X> GHz) is at or below srf_margin x f (<Y> GHz): a measured row
     there has no value for this column";
   - resonance value above the threshold but its lower bound at or below it: answer the curve as today and add
     `near_resonance: {"srf_lo_hz": ..., "threshold_hz": ...}`; the status does not change;
   - resonance `out_of_domain`: the curve's answer is `out_of_domain` with the resonance model's criterion and nearest
     rows, its reason prefixed `"resonance model: "` (the rule cannot be checked, so the answer fails closed).
   An exact measured row answers as today (its value is already empty where the rule cut it).
2. `ic_opt/library/suggest.py`, `predict_all` (the gate of `lib.suggest`, `lib.region`, `lib.densify`, `lib_refine`,
   `lib_design`): for every anchored column among the models it predicts, a row whose predicted resonance (mean) is at
   or below `srf_margin x f` is not ok -- as an out-of-domain row is -- whether or not SRF is among the targets.
   Rows settled above the sweep (`srf_floors`, `_settled`) pass. Targets and objectives already get the robust
   version of this through `implied_srf`; this closes trends and any caller that predicts an anchored column without
   it. An answer that `implied_srf` already constrained must not change: say in the record whether any test answer
   changed, and why.
3. Every other place under `src/ic_opt/library` and `src/ic_opt/recipes` that predicts a curve column (grep for
   `predict_all`, `.gp.predict`, `query(`): apply the rule, or say in the record why it does not need it.
4. `lib.coverage` / `lib.load` / the index / `lib.pick`: unchanged (measured rows only).

## 3. Tests (fakes, no EMX)

(a) a `model: resonance` curve at a point whose predicted SRF is below `srf_margin x f` -> `above_resonance`, no value,
the fields of 2.1; (b) predicted SRF above the threshold, lower bound below -> `predicted` with `near_resonance`;
(c) SRF `above_sweep` -> the curve is answered; (d) a direct `k@f` model at such a point -> `above_resonance`; (e) a
stratum without system SRF uses `SRF_p` / `SRF_s` per drive (both for `k`); (f) the SRF model out of domain -> the curve
`out_of_domain` with the prefixed reason; (g) `predict_all` drops the rows for an anchored trend quantity; (h) existing
`suggest` / `region` answers with anchored targets are unchanged.

## 4. Real check (read-only on the library; its models are cached, nothing is fitted)

`lib.query` on `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`, stratum `xfm_bs_m10_hf`, primary and
secondary outer diameter 51, widths 4, offset 0: `Lp@170`, `Ls@170`, `Qp@170`, `Qs@170`, `k@170`, `Lp@140` are
`above_resonance`; `Lp@60`, `Qp@60`, `k@60`, `SRF` give the values they give on `main` (bit for bit). Run with
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles` and
`OMP_NUM_THREADS=4`; write the before / after answers into the record.

## 5. Documents

`docs/em/library.md` where the answer statuses are listed (add `above_resonance`, `near_resonance`); the ic-opt and
author-spec skills where they list `lib.query`'s statuses, if they do.

## 6. Working rules for the coder

Branch `n113-above-resonance` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n113-above-resonance` (from `main`;
`git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C
<worktree>` and absolute paths. Targeted tests only: the files that test `query`, `suggest`, `region`, `composed`,
`densify`, `lib_signoff`, `lib_refine`, plus `ruff check src tests`; pytest with `OMP_NUM_THREADS=2` and no `-n`. At most
8 threads in all. One commit for the fix with its tests and documents, one for the `## Record` section at the end of
this file. No merge, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending
with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

**Branch and base.** `n113-above-resonance`, created from `main` at 24ceb99 (`git merge --ff-only main` was a no-op then).
`main` has since moved to 562d01c (N-94 / N-53); those commits touch no file under `src/ic_opt/library`, `src/ic_opt/recipes`
or `src/ic_opt/em/measure.py`, and `git merge-tree` of this branch with 562d01c reports no conflict. The fix,
its tests and documents: e26f071; this record: the commit after it.

**Files changed and why.**

- `src/ic_opt/library/query.py`: `DRIVE_SRF` (the per-drive resonance, now the one table `suggest.implied_srf` reads too);
  `Resonance` (the resonance columns, `frequency_hz`, `srf_margin`, `threshold_hz`, and the columns' models once attached);
  `Model.resonance`; `Library.resonance(stratum, column)` -- section 1's choice: the system `SRF` where the stratum declares
  it, else per drive, the declared ones with usable rows; None for any other column. `Library.models` loads (or fits) a curve
  column's resonance models with it (`_needs` / `_with_needs`, which replace `_with_parts`) and attaches them (`_limit`).
  `_predict` is section 2.1: it takes the resonance's answer through the same path (kept per call in `found`, so a resonance
  several curves share is predicted once) and answers `above_resonance` / `out_of_domain` / as before with `near_resonance`;
  `_answer` is the former `_predict` body, unchanged. Neither `gp.py` nor `composed.py` changed: their source is part of
  every cached model's key.
- `src/ic_opt/library/suggest.py`: `below_resonance` (section 2.2, per anchored column: the rows where its resonance --
  `pred`'s own when SRF is among the models, else predicted here -- lies above `srf_margin x f`, or is settled above the
  sweep, or where the column itself is measured; a resonance without a mean leaves the row out); `predict_all` ands it into
  `ok`; `srf_floors_for` gives the callers the floors of the resonance columns too; `suggest` uses it; docstrings.
- `src/ic_opt/library/region.py`: its three floor computations (`_coarse`, `_inside`, `_unanswered`) use `srf_floors_for`,
  so a trend's resonance has its above-the-sweep floors; docstrings.
- `src/ic_opt/library/densify.py`: floors through `srf_floors_for`; an anchored column is not informed where
  `below_resonance` fails (it neither scores nor counts there, a pick there does not update its model -- as an SRF above
  the sweep), its `predicted` value / lo / hi at such a pick are null, and a note counts those candidates per column.
- `src/ic_opt/recipes/lib_refine.py`: `_worse` knows `above_resonance` (so `Qmin` is that when either Q is); `verdict`
  drops a column answered `above_resonance` by `srf_margin`, in that rule's place (after `out_of_domain`, `uncertain`,
  `outside_range`); docstring.
- `src/ic_opt/library/stage.py`, `src/ic_opt/blocks/library.py`: docstrings (the new status; `prefit` loads what the
  answers need).
- `docs/em/library.md`: section 4's table gains `above_resonance`, and a paragraph gives the rule, `near_resonance` and the
  resonance model's `out_of_domain`; sentences in sections 5, 5b (trend), 5c (densify) and 7c (lib_refine).
- `skills/ic-opt/SKILL.md`: `lib.query`'s statuses. `skills/author-spec/SKILL.md` lists none of them: unchanged.

**Tests.** New `tests/ic_opt/test_library_resonance.py`, 11 tests: (a) + (d) `Lp@20` (`model: resonance`) and `k@20`
(direct) at (150, 150, 6.5, 6.5, 0) of the resonance table with the curves' `srf_margin` at 1.9 -- the SRF model's 33.1 GHz
<= 38 GHz, inside both curves' own domains, where each model alone gives a value -- are `above_resonance` with every field
of 2.1, and at (110, 104, 6, 7, 0) (about 65 GHz) answer as before; (b) `near_resonance` with given resonance answers
(predicted / uncertain, a lower bound at the threshold included), and a value at the threshold is `above_resonance`;
(c) a single-turn inductor's `SRF_p` `above_sweep` (60 GHz): `Lp@10` answers as before, `Lp@50` (62.5 GHz) too with
`near_resonance` at the sweep's end; (e) `Library.resonance` -- `SRF` where declared, else `SRF_p` / `SRF_s` per drive
and both for `k`, the same columns `implied_srf` adds, none where no SRF column is declared -- and through `lib.query`
on a per-drive `k@10`, either drive's resonance deciding (above, out of domain, the lowest lower bound flagged); (f) past
the measured diameters the SRF model refuses the point: both curves `out_of_domain` with its criterion, nearest rows and
fill points and the prefixed reason; (g) `predict_all` on stand-in models (SRF not among them; both drives for `k`; floors,
measured values, a resonance without a mean) and through `lib.region` with a trend on `k@20` (no point past the
resonance left in the region; the control with the gate off has them); `Library.models` attaching the resonance (and
fitting it on a fresh copy); `lib.densify` not scoring `k@20` past the resonance (694 of 769 pool rows informed in the
exploration; a note; null predictions there); (h) `lib.suggest` and `lib.region` with an anchored target on the
inductor table (`Lp@15`, implied `SRF_p >= 18.75 GHz`) give the same answers with the gate and without it.
`tests/ic_opt/test_lib_refine.py`: one test (`verdict` and `Qmin` with an `above_resonance` column). Changed, the resonance
model now coming with a curve's: `test_library_stage.py` (two tests: 3 -> 4 models fitted, `Lp@10`'s resonance `SRF_p`)
and `test_library_anyfreq.py` (two tests: the cache files listed now include `SRF_p`'s calibration and model).

Runs (`OMP_NUM_THREADS=2`, no `-n`): at 24ceb99, the 11 files that test query, suggest, region, composed, densify,
lib_signoff, lib_refine and the Predict stage, ceilings, extension columns and transformer strata: 176 passed. After the
fix, the same 11 before the test edits: 172 passed, 4 failed (the four fit-count / cache-file assertions above); final,
the 11 + `test_library_resonance.py` + `test_library_docs.py`: 192 passed (`test_library_query` 33, `test_library_suggest`
7, `test_library_region` 29, `test_library_composed` 27, `test_library_densify` 25, `test_library_signoff` 3,
`test_lib_refine` 15, `test_library_stage` 5, `test_library_anyfreq` 12, `test_library_ceiling` 10, `test_library_xfm` 11,
`test_library_resonance` 11, `test_library_docs` 4). The new module run against 24ceb99's code fails 11 of 11 (with
`_answer` mapped to the old `_predict`): `Lp@20` answers `predicted` where it should be `above_resonance`, the
out-of-domain reason has no prefix, and so on. `ruff check src tests`: clean.

**Whether any test answer changed (2.2).** None of the existing tests' answers changed; the four edits are fit counts and
cache-file lists. Gate on / off on the fixtures with anchored targets: `xfm_demo` (`k@10 >= 0.66`, `Lp_lf` window: the
region test's ANCHORED; and `lib.suggest` `k@10 >= 0.5`): identical, counts included. Inductor `Lp@15` window: `lib.suggest`
identical; `lib.region`'s levels, candidates, measured rows, samples and trend identical, while two counts and one note
move -- `binding["Lp@15"]` 195 -> 193 and `grid.confident` 312 -> 310 (points counted by a target alone or by the
confidence gate that lie past the resonance; the implied target kept them out of every level already), and the note that
nt2/obs_0058 cannot be in the region (measured `SRF_p` 18.81 GHz, the model's 18.21 GHz at or below 18.75 GHz: the
implied target excluded that point before too, the note now says so).

**Real check (section 4).** `lib.query` on n28_v2 / `xfm_bs_m10_hf` at 51 / 51 um, widths 4 / 4 um, offset 0, read-only:
`cache_dir` in a scratch directory (the library's own `.cache` read, nothing written: no file appeared in the scratch
cache) and every fit forbidden in the script; `IC_OPT_PROFILE_DIRS` as specified, `OMP_NUM_THREADS=4`.

| column | before (24ceb99) | after |
| --- | --- | --- |
| `Lp@170` | `predicted` 306.756 pH [304.769, 308.757] | `above_resonance`, no value; `srf` 162.8 GHz [162.7, 163], `threshold_hz` 212.5 GHz |
| `Ls@170` | `predicted` 307.906 pH [305.863, 309.964] | `above_resonance`, no value; `srf` 162.8 GHz [162.7, 163], `threshold_hz` 212.5 GHz |
| `Qp@170` | `predicted` 10.0508 [9.33215, 10.8248] | `above_resonance`, no value; `srf` 162.8 GHz [162.7, 163], `threshold_hz` 212.5 GHz |
| `Qs@170` | `predicted` 9.75713 [9.10969, 10.4506] | `above_resonance`, no value; `srf` 162.8 GHz [162.7, 163], `threshold_hz` 212.5 GHz |
| `k@170` | `predicted` 1.02215 [1.01341, 1.03097] | `above_resonance`, no value; `srf` 162.8 GHz [162.7, 163], `threshold_hz` 212.5 GHz |
| `Lp@140` | `predicted` 260.369 pH [259.627, 261.114] | `above_resonance`, no value; `srf` 162.8 GHz [162.7, 163], `threshold_hz` 175 GHz |
| `Lp@60` | `predicted` 94.8753 pH [94.6576, 95.0936] | `predicted` 94.8753 pH [94.6576, 95.0936] |
| `Qp@60` | `predicted` 13.7542 [13.5298, 13.9823] | `predicted` 13.7542 [13.5298, 13.9823] |
| `k@60` | `predicted` 0.692425 [0.692132, 0.692717] | `predicted` 0.692425 [0.692132, 0.692717] |
| `SRF` | `predicted` 162.846 GHz [162.734, 162.958] | `predicted` 162.846 GHz [162.734, 162.958] |

`Lp@60`, `Qp@60`, `k@60` and `SRF`: the whole answers (bounds, `composition`, `nearest`) equal before and after, bit for
bit. Each `above_resonance` answer's reason reads "the resonance predicted here (162.8 GHz) is at or below srf_margin x f
(212.5 GHz): a measured row there has no value for this column" (175 GHz for `Lp@140`).

**Every place found under 2.3.**

- `query.py` `query` -> `_predict` (the `.gp.predict` / `predict_bounds` hits): section 2.1, done.
- `suggest.py` `predict_all` (and `score` and `suggest` through it): section 2.2, done; `srf_floors`' `cKDTree(...).query`
  is a nearest-row lookup, no prediction.
- `region.py`: three `predict_all` calls (`_coarse`, `region`, `_unanswered`) -- the gate applies through `predict_all`;
  their floors now cover the resonance columns (`srf_floors_for`), without which a trend's resonance above the sweep would
  have been judged by its model's mean.
- `densify.py`: `_candidates` calls `predict_all` with `check_domain=False` and does not use `ok`, so its values are
  nulled where the column fails the gate; its selection predicts each model's raw sigma (`select` on `models[q].gp`, not a
  grep hit) -- the column is not informed there; `_nearest`'s `cKDTree(...).query` is a lookup.
- `stage.py` `Predict.run` -> `query.query`: 2.1 applies (such a metric fails the child as `failed:predict`, its issue
  `metric <name>: above_resonance (<reason>)`); `prefit` loads the resonance models through `Library.models`, so no
  evaluation thread fits one.
- `recipes/lib_signoff.py` -> `query.query`: 2.1 applies; its report keeps each status and compares z / inside for
  `predicted` only: nothing else needed.
- `recipes/lib_refine.py` -> `query.query`: 2.1 applies; `_worse` / `verdict` changed as above.
- `recipes/lib_design.py`: through the Predict stage, no prediction of its own.
- `composed.py` (`ComposedGP.fit` / `predict` / `posterior_cov`, `self.srf.predict`) and `gp.py` (`predict`, `holdout`): the
  models' own computation, not an answer -- unchanged (editing them would also invalidate every cached model).
- `lib_tap.py`, `signoff.py`, `index.py`, `link.py`, `combinations.py`: measured rows only (2.4).

**Deviations from this specification, and what it left open.**

1. An `above_sweep` resonance whose `lower_bound` lies below `srf_margin x f` (2.1 names only the case at or above it):
   answered as before -- a measured row without a resonance in its sweep keeps the column -- with `near_resonance`
   (`srf_lo_hz` = the sweep's end). `predict_all` lets such settled rows pass, as 2.2 says.
2. `srf` and `near_resonance` also carry `column`, the resonance column: `k` of a stratum without the system SRF has two.
   With two, one at or below the threshold makes the curve `above_resonance` (the lower named), else one out of its domain
   makes it `out_of_domain`, else the lowest lower bound is the one flagged.
3. A stratum that declares none of the resonance columns (or none with a usable row) has no rule: its curves answer as
   before (the composed-model tests' `scalars=("Qp_peak",)` table is one). `lib.suggest` / `lib.region` already refuse an
   anchored target there (the implied SRF column is unknown).
4. `predict_all` judges by the resonance's mean, as 2.2 says, not by its model's domain guard (where SRF is among the models
   `in_domain` checks it anyway); `lib.query` fails closed on the guard, as 2.1 says.
5. `Library.models` now loads or fits a curve column's resonance models with it (the Predict stage must not fit in an
   evaluation thread): four existing tests changed their fit counts / file lists, nothing else.
6. `lib_refine` counts an `above_resonance` column under `srf_margin`, in that rule's place, not under `uncertain`.
7. Left for a decision: the Predict stage's identity, and so the pipeline fingerprint of `lib_design` runs, is unchanged --
   a surrogate run continued after this change keeps its earlier observations, some possibly answered past the resonance.
   `implied_srf` takes the smallest `srf_margin` of the stratum's quantities, the gate the curve's own: with uneven margins
   (none in n28_v2, all 1.25) the implied target asks less than the gate.
