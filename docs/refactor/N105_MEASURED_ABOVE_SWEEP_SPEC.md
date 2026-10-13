# N-105: a measured row with no resonance in its sweep meets an SRF minimum below the sweep's end (2026-10-13)

Found by the `lib.region` demonstration of 2026-10-13 (`ic-opt-accept/region_demo_80g/RESULT_CN.md`, gap 4): the
query `Lp@80` 120 pH +-8 %, `Qp@80` >= 12 on an inductor table adds `SRF_p >= 100 GHz` (the margin). The row at outer
diameter 60 um, width 2 um measures Lp@80 120.1 pH and a Q peak of 19.6, and its sweep to 250 GHz shows no resonance:
its `SRF_p` is empty. `region._hits` requires a finite value for every goal, so the row is not among the `measured`
designs that already meet every target, while the prediction path treats the same row as settled above the sweep
(`suggest.srf_floors`, `_settled`) and lists it as the first candidate. The user decided on 2026-10-13 to fix it. Same
family as N-94 (a library device's empty SRF failing a point).

## 1. What changes

- `ic_opt/library/region.py`, `_hits(ds, goals)`: a goal on an SRF quantity (`quantity.startswith("SRF")`) whose window
  has no upper end (`kind == "min"`, or a window / target whose upper end is +inf) is met by a row whose value for it
  is missing **and** whose `stop_hz` is at or above the window's lower end: no resonance up to the sweep's end means
  the resonance lies above it. A missing value under a goal with an upper end (`max`, a finite window, a target with
  tolerance) is not met, as today: the row's resonance is somewhere above the sweep, which says nothing about an upper
  bound. Every other quantity keeps today's rule (a missing value is not met).
- `_measured`: the entry of such a row keeps `values[q]` null (it is not measured) and gains `"above_sweep": {q:
  stop_hz}` for every SRF goal the sweep's end settled, so a reader sees why the row counts; rows without such a
  quantity have no `above_sweep` key.
- `_any_measured` follows `_hits`; `_unanswered` is unchanged (its own gate already uses `srf_floors`).
- `ic_opt/library/suggest.py`: verify that `suggest` already ranks such a measured row among `measured_ranked` (the
  `floor[j] = ds.rows[i].stop_hz` line); add the test of section 2 for it. If it does not, apply the same rule there and
  say so in the record.
- `lib.query`'s domain verdicts, the index, `lib.pick`: unchanged.

## 2. Tests (`tests/ic_opt/test_library_region.py` or the file that tests `region`; fakes, no EMX)

A fake stratum whose rows include one with `SRF_p` missing and `stop_hz` 250e9: (a) an `SRF_p` minimum of 100e9 counts
it in `measured`, with `above_sweep: {"SRF_p": 250e9}` and `values.SRF_p` null; (b) a minimum of 300e9 does not;
(c) a maximum, a finite window and a target on `SRF_p` do not; (d) a missing non-SRF quantity still does not;
(e) `lib.suggest` on the same targets lists the row among its measured candidates (or the record says what was found).

## 3. Documents

`docs/em/library.md` section 5b, one sentence on `measured` and `above_sweep`; `skills/ic-opt/SKILL.md` where it reads
`lib.region`'s answer, if it lists the fields of `measured`.

## 4. Working rules for the coder

Branch `n105-measured-above-sweep` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n105-measured-above-sweep`
(from `main`; `git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with the main tree's `.venv/bin/python`;
`git -C <worktree>` and absolute paths. Run `tests/ic_opt/test_library_region.py`, `test_library_suggest.py` (or the
files that test `region` / `suggest`), `test_library_query.py`, and `ruff check src tests`. One commit for the fix
with its tests and documents, one for the `## Record` section at the end of this file. No merge, no push, no BACKLOG
edit. Messages in the repository's style, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Record

Branch `n105-measured-above-sweep`, from `main` at 8f2ed29 (`git merge --ff-only main`: already up to date). One commit for the fix
with its tests and documents (642dcbd), one for this record; nothing merged, nothing pushed, `docs/refactor/BACKLOG_CN.md`
untouched.

| file | change |
| --- | --- |
| `src/ic_opt/library/region.py` | `_settled_by_sweep_end(row, t)`: `t` is on a quantity starting with `SRF`, its window has no upper end (`t.window()[1] == inf`: a minimum, or a window reaching +inf), the row's value is missing (`row.values.get(q) is None`) and `row.stop_hz >= ` the window's lower end. `_hits` is now per goal, `(finite and satisfied) or settled`, ANDed over the goals; every other quantity, and an SRF goal with an upper end, keep "a missing value is not met". `_any_measured` follows `_hits`. `_measured` adds `"above_sweep": {q: stop_hz}` (after `values`, before the footprint) for every SRF goal the row's sweep end settled, and no key for a row without one; `values[q]` stays null. `_unanswered` and the module docstring (item 6) unchanged / extended by one sentence. |
| `src/ic_opt/library/suggest.py` | **not changed** (see 1). |
| `tests/ic_opt/test_library_region.py` | four tests (below); one assertion of an existing test changed (see 3). |
| `tests/ic_opt/test_library_suggest.py` | two tests (below). |
| `docs/em/library.md` section 5b | one sentence on `measured` and `above_sweep`. |
| `skills/ic-opt/SKILL.md` | **not changed**: it names "the measured rows that meet all" and lists no field of `measured` (see 4). |

New tests (fakes, no EMX; the two last use the synthetic inductor library of `library_fixtures.build_library`, 105 rows):

- `test_a_row_with_no_resonance_meets_an_srf_minimum_its_sweeps_end_reaches` -- a fake stratum of seven rows (Lp 120 pH, Q 19.6, the SRF
  and `stop_hz` varied; one with `SRF_p` missing and `stop_hz` 250e9): (a) a minimum of 100e9 counts it, with `above_sweep {"SRF_p": 250e9}` and
  `values.SRF_p` null, and a row ending exactly at 100e9 counts too; the row that measured a resonance has no `above_sweep` key; the key
  order is `part, obs_id, params, values, above_sweep, footprint`; (b) a minimum of 300e9 counts none, 250e9 counts the 250 GHz sweep and
  not the 100 GHz one, 90e9 not the row whose sweep ends at 80 GHz.
- `test_only_a_goal_without_an_upper_end_is_settled_by_the_sweeps_end` -- (c) `max`, a finite window and a `target`/`tol` on `SRF_p` do not
  count a missing value (they still count the measured ones), a `Target("SRF_p", "window", 100e9)` with its default upper end +inf does;
  (d) a missing `Lp_lf` or `Qp_peak` is not met, with or without a settled SRF beside it.
- `test_the_system_srf_and_each_srf_goal_the_sweeps_end_settled_are_named` -- two SRF goals (`SRF_p` and the system `SRF`): `above_sweep`
  names exactly the quantities whose value is missing, with each row's own stop.
- `test_region_lists_the_measured_designs_lib_suggest_ranks_for_an_srf_minimum_the_sweep_reaches` -- inductor library, 29 single-turn rows
  without a resonance up to 60 GHz: for `SRF_p >= 40e9` `lib.region`'s `measured` is exactly those 29 plus the 6 rows that resonate above
  40 GHz inside the Lp window, and the same set as `lib.suggest`'s (`n=100, min_spacing=0`); for `70e9`, beyond every sweep, both are empty.
- (suggest) `test_a_settled_srf_meets_a_minimum_its_floor_reaches_and_nothing_with_an_upper_end` -- (e) at `suggest.score` level with the
  specification's numbers (floor 250e9, value missing): `min` 100e9 and 250e9 are met, `min` 300e9, `max` 400e9, a window and a target are not.
- (suggest) `test_suggest_lists_a_measured_row_with_no_resonance_when_its_sweeps_end_reaches_the_minimum` -- (e) at `lib.suggest` level, on a
  `Library` whose dataset has one no-resonance row's `stop_hz` set to 250e9 in memory: `SRF_p >= 100e9` lists exactly that row under
  `measured` (`predicted.SRF_p = {value 250e9, lo 250e9, hi inf, rel_sigma 0}`), `>= 300e9` none, `>= 40e9` all 29.

The six new tests fail on the base for the right reason (the four region ones, run with the fix stashed: 4 failed; the two suggest ones pass
on the base, as they should, see 1). Tests (`PYTHONPATH=<worktree>/src`, the main tree's `.venv`, `OMP_NUM_THREADS=2`; "before" = base 8f2ed29):

| suite | before | after |
| --- | --- | --- |
| `test_library_region.py` | 20 | 24 |
| `test_library_suggest.py` | 5 | 7 |
| `test_library_query.py` | 33 | 33 |
| together | 58 | 64 |

`test_library_anyfreq.py`, `test_library_composed.py`, `test_library_ceiling.py` and `test_library_docs.py` (they call `region`) also pass
after (53). `ruff check src tests`: all checks passed.

### Readings and deviations

1. **`lib.suggest` already did it; nothing to change.** For a pool point that is a measured row, `exact[q]` is NaN where the row's value is
   `None` and the `floor[j] = ds.rows[i].stop_hz` line sets the floor to the row's own stop; `predict_all` takes `above` (`mu = lo = floor`,
   `hi = inf`, `sigma = 0`, `rel_sigma` 0) and `satisfy` at the robust level then meets `min` by `lo >= value` and fails `max`, a window and
   a target by `hi = inf`. That is the rule `region._hits` now follows, so the two paths agree on the same targets. The two suggest tests
   pin it (they pass on the base). The inductor-library test above found the sets equal, 35 designs each (29 + 6).
2. **A difference left alone: a missing value of a non-SRF quantity.** `suggest` treats it as unknown, not as unmet: `exact[q]` is NaN, no
   floor exists, and the row is predicted by the model like any pool point, so a measured row whose `Lp_lf` is missing is listed under
   `measured` with a *predicted* value when the domain guard and the confidence gate accept its point (tried in memory on the 150 / 5 / 3 / 2
   row: listed; on the corner row 100 / 4 / 2 / 2 the guard refuses, not listed). `lib.region` keeps its rule (d): not met. The
   specification keeps every other quantity as it is, so this is only noted.
3. **An existing assertion changed.** `test_every_measured_design_on_the_grid_lies_in_the_mean_set[anchored]` asserted
   `m["values"]["SRF"] > 12.5e9` for every `measured` row of the transformer fixture. 172 of its 224 rows show no resonance up to the 60 GHz
   stop, so with the fix 16 of the 40 `measured` rows (it was 24) have `SRF` null and `above_sweep {"SRF": 60e9}`; the assertion now splits
   them (the 24 with a value exceed 12.5 GHz and carry no `above_sweep`; the 16 carry `{"SRF": 60e9}`). The mean-set check before it
   (every on-grid measured row lies in the mean set) holds for the new rows too.
4. **`skills/ic-opt/SKILL.md` not edited.** The condition was "if it lists the fields of `measured`"; it says "the measured rows that meet
   all" and no field, so there is nothing to extend.
5. **"Missing" is `values.get(q) is None`**, the test `srf_floors`, `suggest` and `query._predict` already use for "no resonance in the
   sweep", not "not finite": a stored NaN or inf is not a missing resonance and keeps today's not-met.
6. **`_hits` with no goals** returns every row (it ANDs over the goals) where the old expression returned an empty array; `region` refuses a
   call without targets, so nothing reaches it.
7. **The 250 GHz case is made in memory.** The synthetic libraries sweep to 60 GHz, so the specification's `stop_hz` 250e9 is set on one row of
   a `Library`'s dataset in the suggest test (a new `Library` reads its rows again; models and caches key on the observations and are
   untouched, the row's `SRF_p` is missing either way); the region test uses the natural data (60 GHz) and the fake stratum has 250e9.
8. **Trailer.** The commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`, as the specification's section 4 says.
