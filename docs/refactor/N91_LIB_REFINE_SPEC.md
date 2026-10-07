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
