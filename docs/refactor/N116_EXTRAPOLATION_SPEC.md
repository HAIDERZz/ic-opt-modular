# N-116: answers a little outside the measured range along the outer diameters, labelled (2026-10-14)

User, 2026-10-14: "预测和推荐功能本质上就是要用模型去做外推的功能" and, after N-115, "第 2 项：范围外带标注的外推，做不做。做".

Today the domain guard refuses every point outside the achieved per-dim box (criterion 1) or outside the hull of its level
(criterion 3), whatever the model says. N-115 (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n115_extrapolation_study/RESULT_CN.md`,
tables_CN.md table 1b) measured, per dim, what the models give one and two level steps outside the range:

- along the outer diameters -- the inductor's, the transformer's primary and secondary -- one step (10-25 % of the range) gives
  p90 L 0.5-1.7 %, k 0.2-1.3 %, Q 3.1-4.4 %, SRF 6-9.5 %, calibrated 2 sigma coverage 94-100 %; two steps p90 roughly doubles,
  coverage 89-100 %;
- along the widths coverage falls to 75-89 % (the model's sigma too small), along the spacing (3 levels) p90 is 50-160 %, the low
  side of the offset is concentric devices predicted from off-centre ones (p90 20-50 %);
- sigma / mu alone cannot be the gate (Q2); criterion 3 alone refuses every edge row (table 6).

Decision: extrapolate along the outer diameters only, a bounded distance, answers labelled; every other dim refuses as today.

## 1. The rule

- **Dims**: `outer_diameter_um`, `primary_outer_diameter_um`, `secondary_outer_diameter_um` -- whichever the stratum has. Nothing
  else (widths, offsets, spacings, turns) is extrapolated.
- **Allowance per side**, for each such dim of a model, from that model's own rows: `min(F x range, F x boundary)` beyond the
  achieved minimum and beyond the maximum, with `range` = max - min and `boundary` = the min (low side) or the max (high side),
  `F = 0.15` by default (`EXTRAPOLATE_FRACTION`). On `xfm_bs_m10` (50-240 um after N-117): down to 42.5 um, up to 268.5 um; on an
  `_hf` table (20-60 um): 17 and 66 um. The relative bound keeps the small end conservative (one step at the low end of a base table
  was 25 % of the boundary).
- **Hull**: a point beyond the range along those dims, within the allowance, passes criterion 3 when the point with those dims
  clamped onto the achieved range passes it (the device is a scaled version of one the data surround). A point inside the range
  keeps today's hull test.
- **Criterion 2** (turns level) and **criterion 4** (sigma / mu ceiling, `uncertain`) unchanged; the N-113 resonance rule unchanged
  (the resonance itself may be extrapolated).
- **Intervals**: from N-115's edge rows (`edge_rows.csv.gz`, the outer-diameter dims only), measure the calibrated 2 sigma coverage
  of each column class at the distances inside the allowance; where a class is below 0.95, widen the interval of extrapolated
  answers by a factor that grows with the distance (e.g. k x (1 + a x d / allowance)), with `a` fitted per class so that the
  coverage reaches 0.95; record the factors and their evidence. Answers inside the range are unchanged bit for bit.

## 2. Answers

- `lib.query`: an extrapolated answer keeps its status (`predicted` / `uncertain`) and gains `extrapolated: {dim: {"side":
  "below" | "above", "beyond": <um>, "allowance": <um>, "measured_range": [lo, hi]}}`; a point beyond the allowance, or beyond the
  range along any other dim, is `out_of_domain` as today, with the reason saying which dim and how far, and the allowance where one
  applies.
- `suggest.predict_all` (and through it `lib.suggest`, `lib.region`, `lib.densify`, `lib_refine`, `lib_design`): rows within the
  allowance are in domain and carry the label; `lib.suggest`'s and `lib.region`'s pools and grids extend along those dims by the
  allowance, so designs just outside the measured range can be proposed; each such design is marked extrapolated, and measured
  designs still come first. `lib.region`'s ranges say which part lies beyond the measured range.
- The family request (N-114): a table whose designs are extrapolated has designs (marked); reason (a) of `unreachable` says
  "even with up to <F> extrapolation of the outer diameter" when it still applies.
- `lib_signoff`: extrapolated candidates are predicted and scored like the others; its report carries the label.
- Off switch: `extrapolate=0` (a call parameter of `lib.query`, `lib.suggest`, `lib.region`, `lib.densify`, the CLI blocks) gives
  today's answers exactly; another fraction replaces `F` for that call. No manifest field (a stratum's dump is part of its dataset
  key: editing `library.yaml` would refit every model).
- **Do not change `src/ic_opt/library/gp.py` or `composed.py`**: their source is part of every cached model's key, and a change
  would refit every model of every table (hours). The guard lives in `domain.py`, the answers in `query.py`, `suggest.py`,
  `region.py`, `family.py`.

## 3. Tests (fakes, no EMX)

A fake stratum with outer diameters 60-120 and widths 4-8: a point at 55 (inside the allowance) answered with the label and the
widened interval, at 40 refused with the allowance in the reason, at width 3 refused as today; a transformer stratum with both
outer diameters below their minima; the hull clamp (a point beyond the range whose clamped point is outside the hull: refused);
`extrapolate=0` equal to today's answers field for field; `predict_all` / `suggest` / `region` proposing a design beyond the range,
marked; the N-113 rule on an extrapolated point; the family reason wording.

## 4. Real check with EMX truth and no EMX

N-117 adopted 160 rows into `xfm_bs_m10`; before that, 13 of its 40 independent test points (outer diameters below 60 um) were
refused out of domain. Their EMX values are in
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n117_space_filling/lib_signoff_before.json` (test list `batch/points_test.json`).
Build a scratch library root whose `xfm_bs_m10` part holds the pre-N-117 observations
(`<library>/xfm_bs_m10/.icopt/observations.jsonl.bak-n117-20261014T075753Z`, the other files and `sims` by symlink; every other
part by symlink) and open it with `cache_dir` = the real library's `.cache`, where the old models (dataset key
`0a70b174ed75f2947bd7`, kept on purpose) still are: nothing is fitted -- stop and report if anything starts fitting. Query the 40
test points with extrapolation on and off: which formerly refused points are answered, their errors against EMX and the interval
coverage, per column class; the in-range answers must be identical on and off. Also on the live library: `lib.query` on
`xfm_bs_ap` at outer diameters 51 / 51 um, widths 4 / 4, offset 0 (labelled, no truth), and `lib.suggest family=xfm_bs` for 60 GHz
`Lp@60` = `Ls@60` = 60 pH +-8 %, `max:Qp@60` (which tables now have designs, marked). Evidence:
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n116_extrapolation/`. At most 8 threads.

## 5. Documents

`docs/em/library.md` (the domain section, the new field, the allowance, `extrapolate=`), the ic-opt and author-spec skills where they
describe refusals and recommendation ranges.

## 6. Working rules for the coder

Branch `n116-extrapolation` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n116-extrapolation` (from `main`; `git merge
--ff-only main` first). N-120 is developed at the same time and touches `query.py` (`Library.models`, `cached`, `_predict`): merge
`main` again before your record if it has landed, and resolve. `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C <worktree>` and
absolute paths; `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`. Targeted
tests only (query, suggest, region, family, densify, signoff, refine, resonance, domain, docs, CLI) plus `ruff check src tests`;
pytest with `OMP_NUM_THREADS=2`, no `-n`; at most 8 threads in all. The library is read-only: never write its `library.yaml`, its
part stores or its `.cache`. Commits: the feature with tests and documents (one or a few), then one for the `## Record` section
below. No merge into `main`, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

Branch `n116-extrapolation` from `main` at 3542196 (worktree
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n116-extrapolation`). Commits: 48a45cf (the feature, its tests, the
widening script and its summary), dee2d80 (documents), 18dd5ff (merge of `main` at 58e6924, N-119: no conflict, nothing of the
library), c6460a0 (merge of `main` at a2e169f, N-120: conflicts in `query.py`, `suggest.py`, `region.py`, `lib_signoff.py`,
`docs/em/library.md` and the ic-opt skill's library paragraph, resolved by hand with both texts kept; `_predict` answers
`no_model` before the resonance and the guard, `densify` refuses or leaves out a `no_model` column before it passes
`extrapolate` on, both checked by hand), e661f72 (merge of `main` at 18c478f, N-121 and the backlog: no conflict, the pcell's
tap guards, nothing N-116 changed). The branch up to e661f72 was merged into `main` (e5bbc29) and released as 0.9.0 (43f4769)
while this record was being finished; the branch was then fast-forwarded to 43f4769 (README, release notes and the version
only: the code is e661f72's) and this record committed on top. No commit touches `gp.py`, `composed.py`, `measure.py` or
`dataset.py`: no dataset or model cache key changes.

### Files and why

- `src/ic_opt/library/domain.py`: `EXTRAPOLATE_DIMS`, `EXTRAPOLATE_FRACTION` = 0.15, `allowances` (the per-side rule, shared
  by the guard, `suggest.pool` and `lib.region`'s grid); `DomainGuard.check` / `inside` take `extrapolate` (their default 0
  is the guard of before), clamp the extrapolated dims onto the measured range for the fixed-dim and hull tests (`inside`
  column by column, no copy of a large grid), `Verdict.extrapolated` / `depth`, `depth()` and `extrapolation()` for many
  points / a point in domain; the refusal says how far and the allowance, and its second fill point is the query clamped
  onto the box the guard accepts (0: the reason and fill point of before).
- `src/ic_opt/library/query.py`: `COLUMN_CLASS`, `WIDENING`, `widening`, `check_extrapolate`; `query(extrapolate=)` through
  `_predict` / `_answer`; an extrapolated answer's bounds `prediction_bounds(mu, sigma, k x k_scale x widening)` (an answer
  inside the range takes the code path of before); `srf.extrapolated` on an `above_resonance` curve.
- `src/ic_opt/library/suggest.py`: `in_domain` / `predict_all` / `score` / `suggest` take `extrapolate`; `pred[q]` gains
  `depth` and `widening`; `pool(extrapolate=)`; `extrapolation` (a design's label) and `predicted_entry`; the candidates'
  note. `region.py`: `region` / `grid_pool` / `_coarse` / `_grid` (the bracket's reach) / `_inside` / `_edge` / `_level`
  (`extrapolated`) / `_candidates` / `_sample` / `_unanswered`, the note. `densify.py`: `extrapolate` to the gate and the
  picks' labels, the pool unchanged. `family.py`: reason (a)'s clause.
- `src/ic_opt/library/stage.py`, `recipes/lib_design.py`: `extrapolate=`, named in the stage's identity when not 0.
  `recipes/lib_signoff.py`, `lib_refine.py`: the label kept in their reports, `_z` against the model's own sigma.
  `blocks/library.py`: `extrapolate=` on `lib.query`, `lib.suggest`, `lib.region`, `lib.densify`.
- `docs/refactor/reports/library_query/extrapolation_widening.py` and `.json`: the factors and their evidence (below).
- Tests: `library_fixtures.build_extrapolate_library` (ind_ext: two-turn inductors, outer diameters 60-120 um every 10,
  widths 4 / 6 / 8, spacings 2 / 3, 42 rows), `test_library_extrapolate.py` (new, 12); `test_library_query.py` (the fill
  point: 218 = 200 + the allowance, 200 with `extrapolate=0`), `test_library_resonance.py` (BEYOND moved from 170 to 190 um:
  170 lies inside the 12 um allowance now, and the test is about a resonance model that refuses), `test_library_family.py`
  (reason (a)'s clause), `test_library_stage.py` / `test_library_ceiling.py` (the stage's kwargs and identity).
- `docs/em/library.md` (4: "Beyond the measured range: `extrapolate`", the `out_of_domain` row, criterion 1; 5, 5b, 5c, 6,
  7, 7c), `skills/ic-opt/SKILL.md`, `skills/author-spec/SKILL.md`.

### The rule as built

Section 1 of this specification, with these choices where it leaves one open: the depth of a point is the largest, over
its extrapolated dims, of the distance beyond the range over that side's allowance at 0.15 (so with `extrapolate=0.3` the
widening keeps growing past 1 + a); the label's lengths are rounded to 1e-9. A design (`lib.suggest`, `lib.region`,
`lib.densify` candidates) carries `extrapolated` -- per dim the farthest of its predicted quantities' labels, a settled
value (measured, above the sweep) not counting -- and each predicted quantity that was extrapolated its `widening`. A
`lib.region` level holding such points adds `extrapolated: {count, beyond_measured: {dim: {measured, below, above}}}`
(below / above: the stretches of the level's range outside the table's measured range); `edge` measures against the
allowance's end. Wording of a refusal: `outer_diameter_um=40 outside the measured range [60, 120]: 20 below it, beyond the
allowance of 9 there (extrapolate=0.15 x min(range, boundary))`; `width_um=3 outside the measured range [4, 8]: 1 below
it; only outer_diameter_um is extrapolated`; `outside the convex hull at all rows (judged with
primary_outer_diameter_um=80: clamped onto the measured range)`.

### The widening factors and their evidence

`extrapolation_widening.py` (N-115's evidence, `ic-opt-accept/n116_extrapolation/widening_rows.json` per row): for each of
N-115's 1,556 (table, column, edge) hold-outs along the outer diameters the training rows were rebuilt from the study's
dataset copies (every box equal to the job's), the product's guard built on them, and every held-out row with a
measurement scored (78,033 rows; "needs no widening" equals N-115's `inside` flag on all of them). The guard with
`extrapolate=0.15` answers 5,811: a base table's level step (20-30 um) lies beyond its 9-12 um allowance, so the rows inside
come from the `_hf` tables (2-4 um levels) and the base `xfm_bs` tables' secondary diameter (4 um levels); none from a base
table's primary or inductor diameter.

| class | rows answered | inside today's interval | `a` | inside widened | median / p90 error |
| --- | --- | --- | --- | --- | --- |
| L_lf (Lp_lf, Ls_lf) | 895 | 96.4 % | 0 | 96.4 % | 0.18 / 1.16 % |
| k_lf | 425 | 87.8 % | 0.75 | 95.1 % | 0.17 / 1.02 % |
| SRF, SRF_p, SRF_s | 390 | 89.0 % | 2.64 | 95.1 % | 2.10 / 8.67 % |
| Q_peak (Qp, Qs) | 895 | 91.6 % | 3.09 | 95.1 % | 1.12 / 3.27 % |
| L curves | 1,334 | 98.7 % | 0 | 98.7 % | 0.13 / 0.37 % |
| Q curves | 1,278 | 99.8 % | 0 | 99.8 % | 1.07 / 3.15 % |
| k curves | 594 | 89.6 % | 0.36 | 95.3 % | 0.05 / 0.27 % |

The misses do not grow with the distance: they sit where the model's sigma is smallest, above all the base `xfm_bs`
tables' largest secondary diameters (248-252 um, depth 0.13-0.27: Q peaks rel. sigma 0.35 %, errors 1.1-1.5 %). Q_peak
covers 63 % of its rows at depth <= 0.25 and 100 % beyond 0.5; the pooled slope lifts the near ones to 73 % and widens the
far ones up to 4.09x. A constant factor per class would reach 0.95 narrower (Q_peak 1.56, SRF 1.41, k_lf 1.32); the
specification's form, growing with the distance, was kept. `Lp_res` / `Ls_res` (not in N-115) take L_lf's class.

### Tests

`OMP_NUM_THREADS=2`, no `-n`, on the final tree (e661f72): test_library_extrapolate, N-120's test_library_few_rows, _query,
_suggest, _region, _family, _densify, _signoff, test_lib_refine, _resonance, _model (the guard), _docs, test_cli_recipes,
_stage, _ceiling, _anyfreq, _composed, _cache, _footprint, _xfm, _index, test_combinations, test_skill_author_spec,
test_library, _gates -- 342 passed, 6 skipped (the gates without `IC_OPT_LIBRARY`, and the like). The same files at the base
3542196 (a `git archive` copy): 314 passed, 6 skipped; the difference is the 12 new tests, N-120's 13 and N-119's 3 in
test_cli_recipes. Before the merges, on 48a45cf: 326 passed, 6 skipped. `ruff check src tests` clean, and on the new report
script.

### Real checks

Evidence `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n116_extrapolation/` (`realcheck.py`, `run_check.sh`,
`logs/`). First run on 48a45cf (`first_run/`); run again on c6460a0 (after N-119 and N-120) and on the final tree e661f72
(after N-121, whose pcell draws the family request's candidates): `query40.json`, `live.json` and `live_reach_60ghz.json`
equal the first run's both times, timings aside (`compare_runs.py`, `compare_runs.json`), and the fake-table comparison
below holds on both. Every library was opened with `cache_dir=<evidence>/cache` and limits of 8
threads; `Library._fit`, `_fit_direct`, `_fit_composed` and `_fit_composed_here` were replaced by a function that raises,
so nothing could be fitted; `cache/` stayed empty, and the library's `.cache` listing (names, sizes, mtimes),
`library.yaml` and `xfm_bs_m10`'s observations are unchanged (`library_state_before.txt` = `library_state_after.txt`).

1. **The 40 N-117 test points on the library before N-117** (`scratch.json`, `query40.json`, `query40_summary.json`,
   `analyze_query40.py`): `scratch_root/` holds `xfm_bs_m10` with the pre-N-117 observations (the backup, copied: 1,576
   lines, sha256 equal) and its `spec.json` / `sims` linked, every other part linked, `library.yaml` copied (byte-equal) and
   `.cache` a link to the library's own; the dataset key is `0a70b174ed75f2947bd7` (a cache hit), all 43 columns' models
   load (6.2 s). Every column at the 40 points and at N-112's (51 / 51 / 4 / 4 / 0), on and off:
   - off = the base commit's code (`query40_base.json`, run from `base_code/src`, `git archive 3542196`): 1,763 of 1,763
     answers equal, field for field; off = the predictions N-117's sign-off recorded before adoption
     (`lib_signoff_before.json`: status, value, lo, hi): 1,720 of 1,720.
   - on = off for every answer that is not extrapolated (1,538); 167 refusals stay refusals with another reason, fill point
     or criterion (a point now within the allowance refused by the clamped hull, a resonance model refusing first).
   - Of the 13 points below 60 um, 2 were answered already (secondary 57-58 um, inside its 48-252 um range) and 11 refused:
     3 of these lie inside the box but outside the hull (unchanged by N-116), 8 below the primary's 60 um -- of which 7 are
     now answered (primary 55-59 um, 1-5 um below, depth 0.11-0.56) and 59 / 102 um stays refused, its clamped twin (60 /
     102, a secondary 1.7x the primary) outside the hull. N-112's point (9 um below: the allowance's end) is answered in its
     five scalars (errors 0.30-0.84 %, all inside); its SRF models refuse (clamped to 60 / 51 / 4 / 4 / 0 it lies outside
     the hull of the rows that resonate in the sweep), so do its curves.
   - The answers that were refused and are given now (195: 167 `predicted`, 28 `uncertain`; 190 of them have an EMX value;
     30 more curve answers are now `above_resonance`), against EMX:

     | class | given (pred. / unc.) | with EMX | median / p90 / max error | inside the widened interval | inside before widening |
     | --- | --- | --- | --- | --- | --- |
     | L_lf | 16 / 0 | 16 | 0.41 / 1.18 / 1.28 % | 12 of 16 | 12 of 16 |
     | k_lf | 5 / 3 | 8 | 2.25 / 7.07 / 8.69 % | 8 of 8 | 8 of 8 |
     | SRF | 14 / 1 | 15 | 5.00 / 9.94 / 11.5 % | 15 of 15 | 15 of 15 |
     | Q_peak | 12 / 4 | 16 | 1.78 / 4.19 / 8.56 % | 16 of 16 | 16 of 16 |
     | L curves | 48 / 8 | 54 | 0.80 / 2.87 / 6.78 % | 51 of 54 | 51 of 54 |
     | Q curves | 50 / 6 | 54 | 3.50 / 11.1 / 26.7 % | 51 of 54 | 51 of 54 |
     | k curves | 22 / 6 | 27 | 1.12 / 5.80 / 12.8 % | 27 of 27 | 27 of 27 |

     180 of 190 inside (94.7 %); the `predicted` ones alone 154 of 164. Of the 916 answers refused before, 691 stay refused:
     560 curves whose resonance model refuses, 126 by the hull (22 of them judged at a clamped point), 5 beyond a range. The
     four L_lf misses are 1.0-1.3 % off with intervals of +-0.6 % (a = 0: N-115 had no row of a base table's primary diameter
     inside the allowance).
2. **`lib.query` on `xfm_bs_ap` at 51 / 51 / 4 / 4 / 0** (`live.json`; no truth): 9 um below the 60 um rows, the allowance's
   end. Lp_lf 91.30 pH [89.73, 92.90], Ls_lf 99.34 pH [97.17, 101.57] (widening 1), k_lf 0.4677 [0.4596, 0.4760] (1.75),
   Qp_peak 20.35 [19.14, 21.65], Qs_peak 17.01 [15.73, 18.39] (4.09), each labelled `primary_outer_diameter_um: below, 9,
   allowance 9, [60, 240]`. SRF / SRF_p / SRF_s are refused by the clamped hull (as at N-112's point on `xfm_bs_m10`), and
   with them every curve (`resonance model: ...`). With `extrapolate=0` the answer equals the base code's.
3. **`lib.suggest family=xfm_bs`, Lp@60 = Ls@60 = 60 pH +-8 %, max:Qp@60** (`live.json`, 17.5 s with extrapolation, 16.6
   s without, every candidate drawn by the real generator): designs in the same tables as before, `xfm_bs_ap_hf` (5
   measured, 4 candidates) and `xfm_bs_m10_hf` (5 measured, 5 candidates), none of them extrapolated (inside 20-60 um);
   their candidates differ from those without extrapolation because the Sobol pool now spans the wider box (satisfying 22 /
   20 and 47 / 50). `xfm_bs_ap` and `xfm_bs_m10` still have none; reason (a) now reads "Lp@60 window 55.2-64.8 pH lies
   below this table's measured 69.49-854 pH; Ls@60 window 55.2-64.8 pH lies below this table's measured 74.91-1003 pH,
   even with up to 15% extrapolation of the outer diameter" (`xfm_bs_m10`: 75.03-1001 and 74.91-1050 pH). Why
   (`live_reach_60ghz.py` / `.json`, the same pools): on `xfm_bs_m10` none of the 2,455 pool points beyond the measured
   range is in every model's domain (Lp@60, Ls@60, SRF) -- its rows reach 51 um through a few of N-117's scattered points,
   so a point clamped onto 51 um must fall inside the hull those few span; on `xfm_bs_ap` 2 of 2,759 are (106 / 145 pH).
   The least max(Lp@60, Ls@60) stays 75.0 pH on both. With `extrapolate=0` the whole family answer equals the base
   code's (`live_base.json`), field for field.
4. **`extrapolate=0` against the base code on the fake tables** (`fake_equality.py`, `fake_compare.json`): 22 block calls
   -- `lib.query` inside and outside the range on ind_ext and xfm_demo, `lib.suggest` / `lib.region` on both,
   `lib.densify` on both, `lib.suggest` / `lib.region family=ind_sym` -- equal field for field (timings dropped).

### Deviations

1. The scratch library was opened with `cache_dir` = the evidence's `cache/` and its own `.cache` linked to the library's,
   not with `cache_dir` = the library's `.cache`: the same files are read (`Cache.also`), and no write could reach the
   library's cache (none happened in either place).
2. A refusal's second fill point, with `extrapolate` > 0, is the query clamped onto the box the guard accepts (the achieved
   box widened by the allowances); one existing test changed accordingly (218 instead of 200 um).
3. `lib_design` and the Predict stage take `extrapolate` too, and the stage's identity names it with `WIDENING` when it is
   not 0: surrogate predictions made with extrapolation are a new generation (existing `lib_design` stores re-predict once).
   `lib_refine` has no switch (it extrapolates at the default; its report keeps the labels).
4. `lib.densify`'s pool is not widened (the specification names `lib.suggest`'s and `lib.region`'s pools and grids); its
   models answer to their own allowances, which matters where a model's rows stop short of the table's box.
5. `Lp_res` / `Ls_res` were not in N-115; they take the L_lf class (a = 0).
6. The widening's evidence form: see "The widening factors" -- the linear slope was kept although the pooled misses sit at
   small depths.
7. Widening the Sobol box changes which in-range candidates `lib.suggest` / `lib.region` return even when none is
   extrapolated (a question's answer inside the range, `lib.query`, is unchanged bit for bit).

### Doubts

- L_lf (and the L curves) take no widening from N-115's rows, yet at the real extrapolated points L_lf held 12 of 16: the
  errors are small (<= 1.3 %) but the intervals smaller. The evidence for a base table's primary diameter inside the
  allowance is these 7 points alone.
- The clamped hull extrapolates from a well-sampled face (pre-N-117 `xfm_bs_m10`: 7 of the 8 refused points below the box
  answered) and from almost nothing on a sparse one (live `xfm_bs_m10`: 0 of 2,455); and the SRF models, built on the rows
  that resonate inside the sweep, refuse the small corner (narrow widths) where the scalars answer, so the curves stay
  refused there.
- The pooled per-class slope over-widens far answers of the Q peaks and SRF (4.09x and 3.64x at the allowance's end).
