# N-91 — `lib_refine`: the library grows around a run's best point, with a few real EMX runs

Status: specification (2026-10-07), for the coding subagent; the polish batch (`BACKLOG_CN.md` 0.13, item 2). It is
the last link of the core chain (`T17_STRUCTURE_CANDIDATES_CN.md` 3.7, option C): after a circuit optimization whose
devices come from library rows (T18.2B, the chain that ran on the mixer platform with zero EMX), the geometries near
the best point's row that the table does not hold are measured with a few real EMX runs and adopted, so that the next
round of the same run can move in finer steps than the table's grid. The literature's version (space mapping with
correction coefficients at the device ports) is not needed here: the adopted rows are real measurements and the
circuit re-optimizes on them directly -- one mechanism, every observation real, as the rest of the chain.

Read before writing: `src/ic_opt/recipes/lib_signoff.py` (the whole file: candidate points, the part's spec with this
run's EMX settings, predictions before, measurement with the library's definitions, z / inside, `_adopt(origin=)`),
`src/ic_opt/recipes/lib_tap.py` (preflight through `stages.em_chain.build_device`, the rows' EMX peak memory, the
report shape, how `library.yaml` is read; `lib_refine` adopts into an existing part and never touches `library.yaml`),
`src/ic_opt/library/query.py` (`query`: statuses `measured` / `predicted` / `uncertain` / `out_of_domain` /
`above_sweep`), `src/ic_opt/library/index.py` (`build`, the columns at a frequency), `src/ic_opt/library/link.py` (how a
library device's variables map to index columns, `identity`: a grown table is a new generation for the next process),
`src/ic_opt/spec.py` (`LibrarySource`, a device's `variables` map, the spec variables' grids), `src/ic_opt/digest.py`
(`_best_of`, `_library_row`: the best point and its library row per device), `docs/em/library.md` (sections 4-7),
`tests/ic_opt/test_library_stage.py` / `test_spec_library.py` (projects with library devices and observations, the
fake EMX), `tests/ic_opt/test_lib_tap.py`.

## 1. The call

```
ic-opt run lib_refine PROJECT [device=<id>] [steps=1] [n=8] [prefer=<max|min:column>] [threads=N] [memory_gb=G]
    [process_file=/abs/path.proc] [cache_dir=DIR] --plan
```

`PROJECT` is the optimization run's project (its store holds the observations and the spec with the library device).
Real EMX: `--plan` first is the approval point; it prints everything of section 3 and runs nothing.

## 2. What it does

1. **The best point.** The run's best feasible observation (the digest's rule: over every step of this problem, a
   re-checked point first). Without one: refused, naming how many points there are. For `device` (every library
   device of the spec when not given, one pass each): its library row -- stratum, part, obs id, geometry, values --
   as the observation's child recorded it (`ChildResult.library_row`).
2. **The local grid.** Every geometry whose dims differ from the row's by at most `steps` steps of the stratum's
   `steps` (`library.yaml`), the turns dim excluded (a different turns level is another model, not a neighbour), the
   row itself and every geometry the table already holds excluded (they are candidates already). `steps=1` gives at
   most 3^d − 1 neighbours; `steps=2` 5^d − 1.
3. **Predicted, inside the window.** Each neighbour through `query.query` for the index columns the device's variables
   map to, at the device's frequency, and the system `SRF`: kept when every variable's column is `predicted` (not
   `uncertain`, not `out_of_domain`), its predicted value lies inside the spec variable's range (the device window the
   run searched), and the predicted `SRF` is at least the device's `srf_margin` × frequency (the index's own rule,
   applied to the prediction). The plan says how many neighbours each rule dropped.
4. **Ranked.** By `prefer` -- the device's own `prefer` (`max:Qmin` for a transformer) when not given -- on the
   predicted value; the first `n`. Then the preflight of `lib_tap` (generator + DRC gate, no EMX): a refused neighbour
   is listed and replaced by the next in rank until `n` clean ones or the list runs out.
5. **EMX and the check**, exactly `lib_signoff`'s: the part's spec (the row's part, so the same generator, fixed fields,
   physics, ground fixture) with this run's `threads` / `memory_gb` / `process_file`; the predictions taken before;
   after EMX each quantity measured with the library's definitions; per candidate predicted mu, calibrated bounds,
   measured value, z, inside.
6. **Adopt**, always (the point of the step): every ok candidate into the row's part store under a fresh obs id with
   origin `refine:<project>:<best obs id>` (`lib_signoff._adopt(origin=)`), `adopted.yaml` appended; then the stratum's
   dataset rebuilt (`Library(...).dataset(stratum)`: the cache) and its rows reported. A failed candidate is reported,
   not adopted.
7. **What to do next**, printed as the last line: the grown table is a new generation for the next process
   (`link.identity`); the run continues with its own recipe and a larger budget (the exact command, with the project
   path), the adopted rows being candidates of the next batch; the previous observations stay (same spec fingerprint).
   The recipe does not continue the run itself: that is the user's call, and the next call of `lib_refine` after it
   is another round.

Report `.icopt/reports/lib_refine.json`: the call, the best point and its row, the local grid (size, dropped per
rule), the ranked candidates with predictions, the preflight outcomes, per candidate the measured values, ratios to
the best row's values, z and inside, the adopted obs ids, the dataset's rows after. One note line per device:
candidates / ok / adopted, the best row's `prefer` value against the best adopted one.

## 3. `--plan`

The best point and its row (geometry and values), the local grid's size and what each rule kept, the `n` candidates
with their predicted values and the preflight outcome, the envelope (jobs, threads, memory cap), the part's rows' own
EMX peak memory (median, max) as `lib_tap` prints it, the budget line as `lib_signoff` counts it. No EMX.

## 4. Tests (`tests/ic_opt/test_lib_refine.py`, the fake EMX, demo_6m)

Build a project the way `test_library_stage.py` / `test_spec_library.py` do: a small transformer or inductor library
(analytic sNp rows on a geometry grid), a spec with a library device and a few observations, one feasible best.

1. The best point and its row are found (a re-checked point preferred; refused without a feasible point).
2. The local grid: `steps=1` neighbours exclude the row, the table's rows and other turns levels; `steps=2` grows it.
3. The window rules: a neighbour predicted outside a variable's range, below the SRF margin, `uncertain` or
   `out_of_domain` is dropped and counted; the kept ones are ranked by `prefer`.
4. `--plan` runs no EMX and prints the candidates; the run measures them (fake EMX), reports predicted vs measured,
   adopts the ok ones into the row's part with the refine origin, rebuilds the dataset (row count grows), prints the
   continuation command; a failed candidate is reported and not adopted; `library.yaml` is untouched.
5. After adoption, a new `Library` sees the rows and the device's index holds them (the next generation).
6. `ruff check src tests` clean.

## 5. Documents

`docs/em/library.md`: a section after `lib_tap`'s ("Refining around a run's best point: `lib_refine`"), the recipes
list; the recipe's docstring in the style of `lib_signoff` / `lib_tap`; `skills/ic-opt/SKILL.md` one line under Run.
Section 6 of this file: the record.

## 6. Working rules for the coder

Branch `n91-lib-refine` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n91-lib-refine` (from
main; `git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`
with `PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_lib_refine.py`,
`tests/ic_opt/test_lib_tap.py`, `tests/ic_opt/test_library*.py`, `tests/ic_opt/test_spec_library.py`, and `ruff check
src tests`; all clean before committing. No real EMX; nothing under `ic-opt-library` or `ic-opt-accept` touched; nothing
private in code, tests or docs. Do not edit `docs/refactor/BACKLOG_CN.md`. Do not change `lib_tap.py` (another
subagent is changing it; reuse through imports, or factor a helper out of `lib_signoff.py` if needed). Commit style of
the repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; append `## 7. Record` to this file.
Do not merge or push.

## 7. Record

Status: done on branch `n91-lib-refine` (2026-10-07), not merged. The recipe, its tests and the documents: `8fd07a0`; this
record: the commit after it.

What was done:

1. `src/ic_opt/recipes/lib_refine.py`, sections 1-3 with the deviations below. `best_point` (this problem's observations,
   a re-checked point first, `digest._best_of`), `row_of` (`ChildResult.library_row`), `local_grid`, `model_columns`
   (index column -> the model columns: a curve `Lp@<f>` through `Library.columns`, `Qmin` from `Qp@<f>` and `Qs@<f>`, a
   scalar itself, `area` drawn), `verdict` (the window's rules in their order), `plan_device` (one device's pass up to
   EMX, with the plan's lines), `check_device` (predicted against measured, ratios to the row), `main`. Reused through
   imports, both files unchanged: `lib_signoff._adopt(origin=)`, `_signoff_em`, `_z`; `lib_tap.preflight` (a `Twins` of
   the part's spec and the ranked points), `_peak_gb`, `_median_max`, `_per_point`, `_observations`, `_scalar`, `_strict`.
   `recipe.BUILTIN_RECIPES` lists `lib_refine`.
2. The plan prints the best point (step, re-checked or not, objective), per device the row (geometry, its index values),
   the local grid (size, the dims and their steps, the held dims, the rows of the table among them), the drops per rule
   (outside a range per variable), the preflight's outcome with each refusal's reason, each candidate's changed dims,
   predicted values, SRF and combination, the envelope with the part's rows' own EMX peak memory, the budget line, then
   `sim.evaluate`'s own plan line. The report is written before adopting and again after; the run's lines end with one
   per device (candidates / ok / adopted, the row's `prefer` value against the best adopted, where the adopted rows land
   on the device's grid, the dataset's rows before -> after) and the continuation.
3. Documents: `docs/em/library.md` section 7c "Refining around a run's best point: `lib_refine`", `lib_refine` in its
   Compute and Cache sections and one sentence in section 8; the ic-opt skill's built-ins and one line of its recipe
   cheatsheet (its `ic-opt run` lines: read as "under Run"); the module docstring.

Deviations and additions:

1. **The range rule is the table's own** (`index.Axis.level`): a predicted value is inside its variable's range when the
   table would take a row with that value onto the grid -- the end levels reach half an end interval beyond the bounds,
   in the variable's search scale -- not only inside [lower, upper]. Otherwise a neighbour predicted 0.3 pH above the
   upper bound would be dropped although a row adopted there is a candidate of the run (the best row itself may sit
   there).
2. **SRF strictly above** margin x f, the index's own rule (it drops `srf <= margin * f`), where section 2.3 says "at
   least"; the margin is the index's (`Index.srf_margin`: the manifest's curves', or the device's `srf_margin` when
   larger). An SRF above the sweep passes (the index keeps a row without a resonance); an `out_of_domain` or `uncertain`
   SRF drops the neighbour under that rule; a stratum without an `SRF` quantity has no margin rule and the plan says so.
3. **PROJECT's budget counts the EMX** (the part's spec takes PROJECT's `budget`, as lib_tap's twins do; the line is
   lib_tap's: used, needed, NOT ENOUGH; a shortfall refused before any EMX). lib_signoff keeps the part's own budget,
   which the engine would compare with every simulation of the circuit project's store. The part's constraints are kept
   (lib_signoff's way): a candidate a constraint fails is not `ok`, and the dataset would leave it out as it leaves out
   such rows of the part.
4. "Re-checked" is a point of the signoff recipe's re-check steps (`signoff`, `signoff#<k>`); the digest itself has no
   such rule. Otherwise the best over every step of this problem (the spec's fingerprints).
5. A dim without a step in `library.yaml` keeps the row's value, like the turns dim (the turns dim even when `steps`
   names it); a stratum with no step for any other dim is refused. The neighbours' values are computed in decimal (no
   float drift: 4.9, not 4.8999...), their text is the shortest exact decimal, and a point's other parameters are the
   row's own observation's.
6. Ranking ties: fewer steps from the row first, then the geometry; a neighbour without a predicted `prefer` value (not
   `predicted`, or no footprint) ranks last. `area` is drawn (`query.footprint_at(build=True)`, a pcell build, about
   0.1 s on demo_6m) only for the neighbours the models keep.
7. Added: each candidate's combination on the device's grid -- predicted in the plan, measured in the report -- whether
   the table holds it already, whether the run evaluated it, and whether the device's next index keeps the row
   (`in_index`: every curve has a value at the frequency, the system SRF above the margin). See "Not foreseen" 1.
8. Two devices on one table: a geometry an earlier device's pass chose is left out of the later passes' (it would be
   simulated once and adopted twice).
9. The continuation: the recipe read from the best point's step (`optimize`; `coarse` / `fine` -> `coarse_to_fine`;
   `search@<corner>` / `signoff` -> `signoff corner=<corner>`; anything else `optimize`), the budget the points of its
   search step so far plus one per adopted row (section 2.7 does not say how much larger); the run's strategy, batch and
   seed are not in the store, so the line asks for them; it also gives the spec budget's use.
10. The origin's `<project>` is the project directory's name (lib_signoff's `signoff:<run>` convention); the adopted rows
    carry the current best point's id also when the engine reused an ok observation of an earlier call (a call that
    stopped before adopting).
11. Besides a failed candidate, an `ok` one whose sNp the library cannot measure is reported (`check`) and not adopted.

Not foreseen:

1. **An adopted row helps the next round only on a combination the run can still propose.** The finer steps are the
   geometry's; in electrical values every adopted row lands on the run's variable grid. On a combination the table did
   not hold it is a new candidate; on one it holds it competes with that combination's rows by `prefer`, and when the run
   evaluated that combination already it is not proposed again, so even a better row there never reaches the circuit.
   Each candidate says which (plan and report), and the device's line counts the adopted rows on new and on held
   combinations and how many of those the run evaluated. A grid fine enough that most combinations hold one row (section
   8 of `library.md`) is what turns the adopted rows into candidates.
2. A library device's run fits no model and its frequency is often no anchor of the manifest: the first lib_refine on a
   table fits the models of the device's columns (extension columns at its frequency), under `--plan` too, minutes per
   column on a large stratum; the plan line names them.
3. The dataset keeps a part's most common generation only: the candidates run the part's own spec with machine facts
   changed (not physics), so they join it; a `process_file=` of another content would make them another generation,
   which the dataset leaves out (the device's line shows `excluded`). The tests therefore build their table through the
   real em_only chain (162 rows, about 30 s with its models), not as synthetic rows stamped `gen1`.
4. `lib_tap.py` is being changed in parallel (T19.6): lib_refine builds `lib_tap.Twins` by keyword and uses its private
   helpers; a change to `Twins`' fields or those helpers' signatures must be followed here at the merge.
5. Cost: one `query.query` per neighbour -- `steps=1` on a five-dim table is 242 geometries (about 1.5 s on the test's
   cached models), `steps=2` is 3124.

Tests: `tests/ic_opt/test_lib_refine.py` 14, demo_6m and the fake EMX (`fakes.coupled_snp`). Section 4, point by point:

1. `test_the_best_point_is_found_a_rechecked_one_first_and_refused_without_one`;
2. `test_the_local_grid_leaves_out_the_row_the_table_and_other_turns_levels` (a synthetic table with a turns dim: the
   row, a table row and another turns level left out, a dim without a step held, steps=2 grows, no steps refused),
   `test_the_local_grid_of_the_table`;
3. `test_the_window_rules_in_their_order` (each rule and the order, the half end interval, an SRF above the sweep),
   `test_the_window_drops_and_counts_and_the_kept_ones_rank_by_prefer` (out of domain, outside a range, below the SRF
   margin, each consistent with its prediction; ranked by Qmin and by Qp; a prefer column the index lacks refused),
   `test_a_column_too_uncertain_drops_its_neighbours`, `test_min_area_draws_the_kept_neighbours_and_ranks_them`,
   `test_a_refused_candidate_is_replaced_by_the_next_in_rank`, `test_a_second_pass_on_the_same_table_leaves_out_the_first_one_s_candidates`;
4. `test_the_plan_runs_no_emx_and_the_run_measures_adopts_and_says_what_next` (no EMX and no report under `--plan`; the
   run: a failing candidate reported and not adopted, predicted against measured, adopted with the refine origin,
   `adopted.yaml`, the dataset grown by the adopted rows, `library.yaml` byte for byte, the last line's command);
5. `test_after_adoption_a_new_library_and_the_device_s_index_hold_the_rows` (a new `Library`'s dataset, the next process's
   index and table hold the rows, `link.identity` changed);
6. `ruff check src tests`: clean.

Also `test_calls_that_cannot_be_carried_out_are_refused` (an unknown device, steps / n, the budget, a spec without a
library device), `test_the_continuation_follows_the_recipe_that_ran`, `test_lib_refine_is_a_built_in_recipe`.

Runs (2026-10-07, `PYTHONPATH=<worktree>/src`, the ic-opt-modular venv): `test_lib_refine.py` 14 passed; `test_lib_tap.py`
13 passed; `test_library*.py` 221 passed, 6 skipped (`test_library_gates.py`: needs `IC_OPT_LIBRARY`, the private library);
`test_spec_library.py` 36 passed (one run of the four: 284 passed, 6 skipped); `ruff check src tests` clean. No real EMX;
nothing under `ic-opt-library` or `ic-opt-accept` touched.
