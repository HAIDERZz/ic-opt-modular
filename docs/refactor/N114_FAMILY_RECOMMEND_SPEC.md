# N-114: one recommendation request across every table of a device kind (2026-10-14)

User, 2026-10-14: "我不太能接受就是我想要某个值的电感和变压器然后我需要推荐范围的时候得到的答案是拒答。那这个推荐功能感觉就没用".

`lib.suggest`, `lib.region` and `lib.pick` each take one `stratum`. A target outside that table comes back as an empty
list, without a reason and without pointing at the table that has it. Measured on 2026-10-14
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/refusal_check/`): 60 GHz, `Lp@60` = `Ls@60` =
60 pH +-8 %, `max:Qp@60` -- `xfm_bs_m10` (port opening 8 um, outer diameters 60-240 um): `lib.suggest` satisfying 0,
`lib.region` 0 points; `xfm_bs_m10_hf` (opening 2 um, 20-60 um): 50 satisfying, 37 of them measured rows. The sixteen
tables together cover wide ranges (`table_coverage.json` there: e.g. the M10/M9 single-turn transformers reach 75-1001 pH
at 60 GHz with opening 8 um and 25-148 pH with opening 2 um).

N-50 (2026-09-27): `lib.suggest`'s pool is the measured rows plus 8192 Sobol points over the whole achieved box; a small
target region (on `xfm_bs_m10`, Lp / Ls / k windows met robustly by about 30 grid points) yields no candidate, while
`lib.region`'s grid finds them.

User decision 2026-10-14 (list items 4 and 5): do the cross-table request, with N-50 folded in. "多表查询" was shelved on
2026-10-07 (restraint batch); this reopens it.

## 1. Interface

- `lib.suggest` and `lib.region` take, instead of `stratum=`, either `family=` or `strata=` (exactly one of the three):
  - `family=ind_sym | xfm_bs | xfm_ms`: every stratum whose `generator` is that family's (`clean_port_ind_sym`,
    `clean_port_xfm_bs`, `clean_port_xfm_ms` in today's library; resolve by the generator name, not by the stratum
    name), filtered by `taps=`;
  - `taps=none` (default) | `any` | `only`: a stratum is tapped when its parts' device carries centre-tap ports (read
    from the part spec: ports `CTP` / `CTS`, or `ct_*` fixed fields -- pick the robust one and say which in the record);
  - `strata=a,b,c`: an explicit list (no tap filter).
- With `stratum=` the call and its answer are unchanged, field for field (existing tests pass untouched).
- `lib.region`'s `out=` (N-106) needs one stratum: with `family` / `strata` it is refused, unless only one stratum has
  points, with a message naming the strata that have points and telling to rerun with `stratum=`.
- The CLI (`ic-opt call lib.suggest LIB family=xfm_bs targets=... objective=...`) prints the JSON as today.

## 2. The family answer

```
{"family": ..., "taps": ..., "strata": {name: <the single-stratum answer, schema unchanged> | {"error": text}},
 "facts": {name: {"generator": ..., "fixed": {the part spec's fixed fields that tell the tables apart: port
          opening(s), metals, ground fixture metal, taps}, "rows": n}},
 "leaders": [... the best n across the strata, each entry the per-stratum entry plus "stratum" and that stratum's
            "fixed" ...],
 "unreachable": {name: {"reason": ..., "ranges": {quantity: [min, max]}, "outside": [...], "closest": {...}}},
 "notes": [...]}
```

- `leaders`: measured designs first, then predicted ones -- as each per-stratum answer orders them -- merged across
  strata by the objective (or by closeness to the targets when there is none); `n` as given. For `lib.region`,
  `leaders` lists per stratum the robust count and ranges, the strata with points first.
- `unreachable`, for each stratum with no satisfying design, says why in one of three ways, computed from the
  stratum's measured rows (the rows that have the quantity under the library's own rules, e.g. the SRF margin):
  (a) a target lies outside the stratum's measured range of its quantity: give the range and the target window in SI
  units ("Lp@60 window 55.2-64.8 pH lies below this table's measured 75-1001 pH"); (b) every target lies inside its
  range but no design meets them together: say so and give the closest measured row (smallest distance in targets
  scaled by their windows) with its values; (c) the stratum cannot take the request (a quantity it does not have, a
  frequency outside its sweeps): the error text. Keep this in one function, so that N-116 (answers outside the measured
  range, if adopted) can change its wording in one place.
- `notes` names, when some strata are unreachable and others are not, which strata have designs ("60 pH at 60 GHz:
  designs in xfm_bs_m10_hf, xfm_bs_ap_hf; xfm_bs_m10 and xfm_bs_ap reach 75 pH at the least").

## 3. N-50: the pool of a small target region

In `suggest`, when no predicted candidate of the Sobol pool satisfies the targets, draw a second pool on the grid of
`region`'s coarse pass for the same targets (reuse `region`'s grid machinery and its `relax`), score it the same way
and mark each candidate's origin (`"pool": "sobol"` or `"region_grid"`). Acceptance: a case where `lib.region` finds a
few dozen robust points and the Sobol pool finds none now yields candidates. Reconstruct the 2026-09-27 case if it is
recorded (search `ic-opt-accept/` and the docs); otherwise build one on the real library and record its targets.

## 4. Tests (fakes, no EMX)

Two small fake strata of one family differing only in a fixed field (an opening): a target reachable in one only ->
leaders from that stratum, labelled, and `unreachable` reason (a) for the other with its range; a target pair that is
each in range but not jointly -> reason (b) with the closest row; a tapped third stratum excluded by `taps=none`,
included by `any`, alone by `only`; `strata=` explicit; `stratum=` answers unchanged; `region` with `family` and `out=`
refused as in 1; the N-50 second pool.

## 5. Real check (read-only on the library; threads <= 8 per call, `OMP_NUM_THREADS=8`)

Library `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2` with
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`. Ten tables are
being pre-fitted until both `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/prewarm_20261014/prefit_A.exit` and
`prefit_B.exit` exist: do not query a table whose `lib.cached` still shows `none` before then (it would fit in the
foreground). Checks: (1) `family=xfm_bs`, 60 GHz `Lp@60` = `Ls@60` = 60 pH +-8 %, `max:Qp@60`: leaders from the opening
2 um tables, reason (a) for the opening 8 um ones; (2) the same with 100 pH: leaders from both openings; (3)
`family=ind_sym`, 80 GHz `Lp@80` 120 pH +-8 %, `max:Qp@80`; (4) the N-50 case. Answers saved under
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n114_family_recommend/`.

## 6. Documents

`docs/em/library.md` (the family request, the answer's fields, the three reasons), the ic-opt skill's library section
and the author-spec skill's flow from pre-simulation targets to `lib.region` (start from `family=`, then write the
combinations of the chosen stratum with `stratum= out=`).

## 7. Working rules for the coder

Branch `n114-family-recommend` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n114-family-recommend` (from
`main`; `git merge --ff-only main` first; N-113 changes `query._predict` and `suggest.predict_all` -- merge `main` again
before the record if N-113 has landed, and resolve). `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C
<worktree>` and absolute paths. Targeted tests only: the files that test `suggest`, `region`, `query`, `pick` /
`index`, the library blocks and the CLI, plus `ruff check src tests`; pytest with `OMP_NUM_THREADS=2` and no `-n`. At most
8 threads in all. Commits: the feature with tests and documents (one or a few), then one for the `## Record` section at
the end of this file. No merge into `main`, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the
repository's style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

Branch `n114-family-recommend` from `main` at 24ceb99 (worktree
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n114-family-recommend`). Commits: 2c52305 (the feature and its tests),
e613637 (documents), 5386662 (reason (b) wording; generic examples in the docs), 3c31a54 (merge of `main` at 562d01c,
N-94 / N-53), 467e2a1 (one more test), 6f00a8b (merge of `main` at 79c70d3, N-113: one textual conflict in the ic-opt
skill's library paragraph, both texts kept; `suggest.py` / `region.py` merged cleanly and checked by hand -- the second
pool goes through `_coarse` / `_inside`, which N-113 moved to `srf_floors_for`, and is scored with those floors, so
N-113's resonance gate holds there as in `lib.region`), bc16fb4 (merge of `main` at 112507e, N-93: the pcell's via-stack
tap check, nothing of the library), then this record. No merge touched `gp.py`, `measure.py`, `composed.py` or
`dataset.py`: no dataset or model cache key changed.

### Files and why

- `src/ic_opt/library/family.py` (new): the family request -- `request` (exactly one of `stratum=`, `family=` with
  `taps=`, `strata=`), `members`, `facts`, `suggest` / `region` (every table asked exactly as `stratum=` asks it, the
  answers side by side), the leaders' merge, `unreachable` (the one function that words the three reasons, for N-116)
  and the notes line.
- `src/ic_opt/library/suggest.py`: N-50 -- when no predicted point of the Sobol pool meets the targets, a second pool
  (`_grid_pool`: `region.grid_pool` less the library's rows, scored by `score`); every candidate carries `pool` (`sobol`
  / `region_grid`); `region_grid` and a note only when the second pool was drawn; both pools predicted under the one
  BLAS limit and in the same byte budget.
- `src/ic_opt/library/region.py`: `grid_pool` -- the coarse pass, the grid over its survivors and the in-domain filter
  `region` runs, at `region`'s defaults, now named constants (`POOL_SIZE` 32768, `LEVELS_PER_DIM` 20, `MAX_POINTS`
  2 000 000; `RELAX` 0.10). `region()` and its answer are unchanged.
- `src/ic_opt/blocks/library.py`: `lib.suggest` / `lib.region` take `family`, `taps`, `strata` (`stratum` and `targets`
  keep their places, now optional); with `stratum=` they call the same functions with the same arguments as before;
  `targets` is checked as a JSON object (`_targets`).
- `tests/ic_opt/library_fixtures.py`: `build_family_library` -- ind_o8 (port opening 8 um, OD 140-200, 42 rows), ind_o2
  (opening 2 um, OD 80-130, 36 rows), ind_ct (opening 8 um, OD 80-130, a centre tap CT on a decoupled third port, no
  `Qp_peak` column).
- `tests/ic_opt/test_library_family.py` (new, 14 tests); `tests/ic_opt/test_library_suggest.py` (one assertion: see
  deviation 1).
- `docs/em/library.md` (5: the candidate pools and `region_grid`, "Every table of a device kind: `family=`"; 5b:
  `family=` and `out=` with several tables), `skills/ic-opt/SKILL.md` (library paragraph, cheatsheet line),
  `skills/author-spec/SKILL.md` (pre-simulation targets -> `lib.region family=` -> the chosen table with `stratum=
  out=`).

### Family resolution and tap detection

A table is of family F when its manifest `generator` is `clean_port_<F>` (`family=xfm_bs` and `family=clean_port_xfm_bs`
are the same; a generator without the prefix is a family of its own name); the table's name is never read. A table is
tapped when a part's device has a port whose name starts with `CT` (`CT`, `CTP`, `CTS`). The ports, not the `ct_*`
fields: they are what EMX measured and the sNp holds, the rule is the one `Device.default_topology` grounds the taps
by, the same for every family, while the fields are named per family (`ct_metal`, `ct_primary_metal`, ...); the
built-in generators refuse a tap port without its tap metal and the other way round, so on a built part the two agree.
`taps=none` (default) / `any` / `only`; `taps=` with `stratum=` or `strata=` is refused. On n28_v2
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n114_family_recommend/families.json`): ind_sym and xfm_ms 4 untapped tables each, xfm_bs 4 untapped and 4 tapped (the `_ct` tables,
ports CTP / CTS); `taps=only` on ind_sym / xfm_ms is refused with the list of tables.

`facts.fixed` holds the fields ending in `opening_um` and `metal`, `ground_fixture_metal` (the part spec's value: `auto`
where the spec says so -- the generator resolves it per drawing), `taps` (the tap ports) and the `ct_*` fields; a field
the parts set differently is `{part: value}` (ind_sym_ap: `ground_fixture_metal` `{ind_sym_ap: auto, ind_sym_ap_nt1:
M9}`).

### Tests

`OMP_NUM_THREADS=2`, no `-n`, on the final tree (bc16fb4): test_library_family, _suggest, _region, _query, _index, _docs, _cache,
_ceiling, _anyfreq, _footprint, _resonance (N-113's), test_combinations, test_skill_author_spec, test_cli_recipes,
test_library -- 222 passed (197 at the base for the same files less the two new ones; +14 family, +11 N-113); `ruff check
src tests` clean. The family file covers section 4's list: a target one table reaches (leaders labelled; reason (a) for
the other with its range; the table's answer equal to `stratum=`'s, field for field), a pair each in range but not
jointly (reason (b), the closest row checked against a direct computation), `taps` none / any / only, `strata=` (a table
without the objective's column: reason (c)), the one-way naming refusals, a column no row of a table has (refused before
any fit) together with an implied resonance no row meets (named in (b)), a table whose own answer raises, the leaders'
merge order (objective and closeness), `lib.region` with `family=` and `out=` (refused with two tables holding points,
written with one, refused with none), the N-50 second pool (its candidates are robust points of `lib.region`'s own grid
at the defaults, its count that grid's robust points off the library's rows), the command line.

### Real checks

In `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n114_family_recommend/` (`realcheck.py`, `run_all.sh`): the library opened with limits of 8 threads and `cache_dir=./cache` (it took
only two footprint files, of xfm_bs_ap and xfm_bs_ap_hf; every model came from the library's own `.cache`), after
`prefit_A2.exit` and `prefit_B.exit` both held 0 (`cached.json`: the cache state then). The answers below come from
6f00a8b (`code_commit.txt`) and equal, timings aside, those of the first runs made while the code was written
(`first_run/`) and of a rerun on 3c31a54, before N-113 (`rerun_pre_n113/`; `compare_runs.py`); checks 1 and 3 run again
on bc16fb4, after N-93, give the same (`spot_bc16fb4/`). `lib.suggest` at its defaults (n 5, verify_build: every
candidate drawn by the real generator; 91 drawn in all, none refused).

1. `xfm_bs_60.json` (18.5 s): `family=xfm_bs`, `Lp@60` = `Ls@60` = 60 pH +-8 %, `max:Qp@60`. Designs in the 2 um tables:
   xfm_bs_m10_hf 50 satisfying (37 measured), xfm_bs_ap_hf 20 (18). Leaders: five measured (xfm_bs_m10_hf OD 44/44, W 6/6,
   CS 13.2: Lp@60 59.95 pH, Ls@60 59.85 pH, Qp@60 15.55; then 14.63, 14.43, xfm_bs_ap_hf 14.24, 14.17), five predicted
   (Sobol pool, Qp@60 lower bounds 13.98-13.45). Reason (a) for both 8 um tables: xfm_bs_m10 "Lp@60 window 55.2-64.8 pH
   lies below this table's measured 75.03-1001 pH; Ls@60 window 55.2-64.8 pH lies below this table's measured
   74.91-1050 pH", xfm_bs_ap 69.49-854 pH and 74.91-1003 pH. Note: "...: designs in xfm_bs_ap_hf, xfm_bs_m10_hf;
   xfm_bs_ap reaches Lp@60 69.49 pH at the least; xfm_bs_m10 reaches Lp@60 75.03 pH at the least".
2. `xfm_bs_100.json` (148.4 s): the same at 100 pH. Every table has designs (`unreachable` empty, no note):
   xfm_bs_m10_hf 76 satisfying (47 measured), xfm_bs_ap_hf 33 (21), xfm_bs_m10 20 measured and xfm_bs_ap 14 measured --
   the last two with no Sobol point meeting the targets, so their candidates come from the second pool (512 and 11 390
   grid points meeting them). Merged by Qp@60's lower bound, the leaders are the 8 um tables' (measured: xfm_bs_m10 OD
   60/56, W 8/4, CS 0: Qp@60 21.72, then 21.03, 20.72, 20.34, xfm_bs_ap 20.28; predicted, region_grid: 21.05-20.33); the
   2 um tables' best measured designs have 18.67 (xfm_bs_m10_hf) and 16.73 (xfm_bs_ap_hf). `xfm_bs_100_n10.json` (n = 10, 155.0 s): both
   openings among the leaders, xfm_bs_m10_hf measured ninth (18.67).
3. `ind_sym_80.json` (3.2 s): `family=ind_sym`, `Lp@80` 120 pH +-8 %, `max:Qp@80`. Designs in all four tables (ind_sym_ap 85
   satisfying, 3 measured; ind_sym_ap_hf 15, 1; ind_sym_m10 77, 3; ind_sym_m10_hf 116, 9). Leaders: measured ind_sym_m10
   OD 80, W 10, turns 1 (Lp@80 117.7 pH, Qp@80 31.18), ind_sym_m10 W 9 (29.95), ind_sym_ap W 9 / 8 / 7 (29.61, 28.74,
   27.79); predicted ind_sym_m10 OD 81, W 9.9 (lower bound 30.50), OD 76 (30.37), ind_sym_ap OD 88 (30.28), 30.15, 30.08.
4. `n50.json` (146.7 s, the N-50 case below): `lib.region` at its defaults (threads 8, workers 4) finds 288 robust grid
   points (mean 22 935; 1 151 920 grid points, 771 668 in domain, 726 646 confident -- grid steps and bracket the same as
   2026-09-27's); `lib.suggest`'s Sobol pool (8192 points + 1576 rows) meets nothing, its second pool -- that grid, 16
   coarse survivors -- has 288 satisfying points, and the answer gives five built candidates, all `region_grid`, e.g. OD
   60/60, W 8.6/8.4, CS 8.0: Lp_lf 88.2-88.9 pH, Ls_lf 88.8-89.5 pH, k_lf 0.417-0.433, Qp_peak at least 21.17.

### The N-50 case

Reconstructed from its record (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/xfmr_cs_switch_5/suggest.json` and
`MEASURE_CN.md`, 2026-09-27: the mixer transformer XFMR_CS_Switch_5): xfm_bs_m10, `Lp_lf` 93.65 pH +-6 %, `Ls_lf` 84.67 pH
+-6 %, `k_lf` 0.447 +-7 %, `max:Qp_peak`, the same targets for `lib.region` and `lib.suggest`. Then: Sobol pool 9858, 0
satisfying; `lib.region` (window form Lp 88-99.5, Ls 79.5-90 pH, k 0.42-0.48) 30 robust points. Today the same window
form gives 188 robust points
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n114_family_recommend/n50_region_window.json`, 38.5 s; mean 12 311) -- N-109 moved the table's OD 20-60 rows to
xfm_bs_m10_hf and its models were refitted -- and the suggest call's targets 288; the Sobol pool still meets nothing,
and the second pool yields the candidates above.

### xfm_ms columns with few or no rows (N-120)

Not asked on the real library (the coordinator's instruction). A family request naming a column no row of a table has
(xfm_ms_m10 / xfm_ms_ap at 140 and 170 GHz) gets for that table `{"error": "<table>: no row has a value of <column>;
..."}` and reason `cannot_take`, before anything is fitted, the other tables answering as usual (test: Lp@50 on ind_o8).
A column with a handful of rows (112 GHz: 2 / 4) passes that check; the table's own `lib.suggest` fits it, and the fit's
scikit-learn `ValueError` (N-120's) becomes that table's `cannot_take` with scikit-learn's text -- unclear, but the
other tables still answer (test with the failure injected). `stratum=` on such a column fails as before.

### Deviations

1. One existing test changed: `test_the_prediction_budget_is_a_share_of_the_machines_memory` counts six predictions in the
   budget instead of two. Its 1.2 nH target lies between the one-turn rows (up to 1.02 nH) and the two-turn ones (from
   1.50 nH), so no Sobol point meets it and N-50's second pool is predicted too (coarse pass and grid), in the same
   budget and under the same BLAS limit (still one limit per call).
2. The single-table `lib.suggest` answer changes where N-50 asks it to: every candidate gains `pool`, and an answer whose
   Sobol pool met nothing gains `region_grid`, a note and, possibly, candidates; `pool` and `satisfying` stay the first
   pool's. Otherwise field for field as before (the family test compares a family's per-table answer with `stratum=`'s);
   `lib.region`'s answer is unchanged.
3. An `unreachable` entry has `kind` (`outside_range` / `not_met_together` / `cannot_take`) beside `reason` (the
   sentence, in SI units); `ranges` for (a) and (b), empty for (c); `closest` null except for (b).
4. `lib.suggest`'s `leaders` hold up to n measured designs, then up to n predicted ones (2n at most), as one table's
   answer lists both; "the best n" read as n of each.
5. The second pool runs `lib.region`'s coarse pass and grid at `lib.region`'s defaults (32768 Sobol points in the coarse
   pass, 20 levels per dim, 2 000 000 points at most, relax 0.10), not at `lib.suggest`'s `pool_size`, so its candidates
   are robust points of a default `lib.region` call; it costs about what that call costs (1.15 M grid points in the N-50
   case, 1.9 M for xfm_bs_ap at 100 pH); its grid points that are library rows are left out.
6. `lib.region`'s family `unreachable`: a table with neither a robust grid point nor a measured design meeting every
   target; one with mean-level points only is listed, its (b) sentence counting them.
7. A column no row of a table has a value of is that table's refusal before any fit (family requests only).
8. The `lib.suggest` block checks `targets` as a JSON object (a quoted-away string raised AttributeError before).
9. Check 2's "leaders from both openings" holds for the designs (no table unreachable) but not for the five leaders, all
   from the 8 um tables, whose Qp@60 is higher; with n = 10 a 2 um design is among them.
10. N-50's case gives 188-288 robust points today, not "a few dozen"; the Sobol pool still meets nothing.
11. `docs/em` and the skills give generic examples (no table name or value of the N28 library).
