# T17.8 — the evaluation schedule, step 1: a point stops at its first failing simulation

Status: specification (2026-09-29), for the coding subagent. Decisions behind it: `T17_OPTIMIZER_PLAN_CN.md`, the
record "文献调研、论文做法的复现与多 corner 的量测（2026-09-29）", and the report page listed in `reports/INDEX_CN.md`
(section seven: the proposal). The user agreed to the direction on 2026-09-29: two new parts — a schedule and,
later, a corner model — with the proposer and the verdict unchanged.

Scope of this step: the order in which one point's children (testbench × corner simulations) run, and when the
point's evaluation stops. The same mechanism serves a single condition with several testbenches (this step's
acceptance), several corners (step 2 measures it), and later the EM chain (step 3). Nothing changes in the proposer
(`opt.optimize.suggest` and the strategies), in the verdict of a fully simulated point (`sim.corner.aggregate` on a
complete set of children), or in the shape of the record (one observation per point, `observations.jsonl`).

What it is built on (measured, see the record): one simulation exposes 94% to 99% of the infeasible designs of the
PVT benchmark; on the user's two mixers, replaying the tt runs with "stop after the first testbench that fails the
point" keeps 43% to 91% of the simulation time; on the multi-corner benchmark the schedule with a learned order beat
"every point at every corner" on the final objective in 17 of 18 paired runs.

## 1. What it does

- A point's children already run one after another (`ic_opt.eval.engine._run_point`); points run in parallel. The
  schedule (a) fixes the children's order for a batch from the history and (b) stops a point after the first child
  whose result shows the point cannot be feasible. Nothing is simulated for that point afterwards.
- The observation of a stopped point holds the children that ran and no others, `status` from the children that ran
  (`constraint_failed`, `metric_failed` or `failed:<stage>`), `feasible` false, `simulations` = children that ran
  plus point-level stages that missed the cache. Its `issues` say what stopped it and how many simulations were
  not run.
- A point that is never stopped is recorded exactly as today, byte for byte.
- The switch `simulator.stop_at_first_failure` (default on) turns it off for a run that wants every child of every
  point (a characterization run). `sim.evaluate` takes an override (`stop_at_first_failure=`) so a recipe step can
  ask for the full set.

## 2. Files

| file | content |
| --- | --- |
| `src/ic_opt/eval/schedule.py` (new) | `Schedule`: the order and the stop rule (sections 3, 4) |
| `src/ic_opt/eval/engine.py` | `run(..., schedule=None)`; `_run_point` follows the order and stops (section 5) |
| `src/ic_opt/sim/corner.py` | `aggregate(spec, children, wanted=None)`: the verdict of an incomplete set (section 6) |
| `src/ic_opt/objective.py` | `evaluate_partial(spec, metrics)` (section 6) |
| `src/ic_opt/blocks/evaluate.py` | builds the schedule from the store, the `--plan` and batch lines (section 7) |
| `src/ic_opt/spec.py` | `simulator.stop_at_first_failure` (section 8) |
| `src/ic_opt/recipes/signoff.py` | `full=` (section 9) |
| `src/ic_opt/digest.py`, `src/ic_opt/blocks/analyze.py` | stopped points read correctly (section 10) |
| `skills/ic-opt/SKILL.md`, `skills/author-spec/SKILL.md` | one sentence each (section 10) |
| `tests/ic_opt/test_schedule.py` | section 11 |

## 3. The order (`schedule.py`)

`Schedule.from_history(spec, observations, children, corner_scope)` computes, once per batch, from the observations
of the same problem in the store (every step, every corner; `initial=` rows adopted by `opt.optimize` count too), for
every child key `unit/corner` in `children`:

- `reached` = the number of observations that hold that child;
- `failed` = the number of those whose child *shows a failure* (section 4);
- `seconds` = the mean of the recorded `seconds` of that child over the observations that hold it; a child no
  observation holds takes the mean over the children of the same unit, and when the unit has none either, 1.

`score = (failed + 1) / (reached + 2) / seconds`. Children run in descending score; ties keep the spec's order
(testbenches in spec order, corners in spec order, the nominal corner first). With fewer than 10 observations that
hold any child at all, the order is the spec's. The order is the same for every point of the batch; it is not stored
anywhere (the next batch rebuilds it from the history, as the strategies rebuild their state). Device children (EM
devices, `unit_kind == "device"`) keep their present place after the testbench children; scheduling them is step 3.

`Schedule.spec_order(children)` gives the spec's order; `Schedule.order(children) -> list[Child]`.

## 4. What shows a failure (`schedule.py`)

`Schedule.stop_after(result: ChildResult, corner_id) -> str | None` returns the reason, or None:

- `result.status` is `failed:<stage>` or `metric_failed`: the point cannot become feasible → `"<unit>/<corner>: <status>"`;
- else, when the corner is in the constraint scope (every corner under `corner_policy.constraints: all_corners`;
  the nominal corner only under `nominal`): a constraint whose metric this child produced and that is violated
  (`ic_opt.objective.constraint_violations` on the child's own metrics, restricted to the constraints whose metric is
  present) → `"<unit>/<corner>: <metric> <op> <value> violated by <x>"`;
- else None. A metric of the objective that is missing from this child is not a failure: it may come from another
  testbench.

Under `constraints: nominal` a non-nominal corner never stops a point for a violation (its constraints do not count),
only for a failed simulation; the corner is still needed for the worst-case objective.

## 5. The engine (`engine.py`)

- `run(..., schedule: Schedule | None = None)`. Without a schedule the engine behaves as today (spec order, no stop).
- `_run_point` iterates over `schedule.order(children)`; after each child result it asks `schedule.stop_after`; a
  reason ends the loop: the remaining children are not run, and the result set is returned with the reason and the
  count of children not run. The point-level stages, the cache, the retention policy are unchanged.
- `evaluate_job` passes `wanted = [c.key for c in children]` to `aggregate` (section 6) and records
  `simulations = len(results) (if the children simulate) + cache misses`.
- The budget check before scheduling keeps reserving the worst case per point (`sims_per_point`), as today: it is a
  ceiling; the count recorded is what ran.
- `store.log_step(...)` gains `stopped=<points stopped early>`, `not_run=<children not run in this batch>`.
- Interrupts, reuse (an observation is reused only when `ok` with the full child set — a stopped point never is),
  retention: unchanged.

## 6. The verdict of an incomplete set (`corner.py`, `objective.py`)

`aggregate(spec, children, wanted: list[str] | None = None)`: with `wanted` None or equal to the children present,
exactly today's function. Otherwise (`not_run = [k for k in wanted if k not in children]` non-empty):

1. a child that did not run to its end (`failed:<stage>`) → today's rule (that status, its issues);
2. a child that ran but lost a metric (`metric_failed`) → `metric_failed`, the nominal corner's metrics as today;
3. else every corner present is judged with `objective.evaluate_partial(spec, metrics)`: the constraints whose
   metrics are present, and only those; a corner with a violation is `constraint_failed` with that corner's penalty;
   the point is `constraint_failed` when a corner in the constraint scope is; `metrics` = the violating corner's
   present metrics (the largest penalty when several), `fom` and `objective` None, `selected_corner` that corner,
   `corner_objectives` only for corners whose metrics are complete;
4. none of the three (nothing failed and the set is incomplete) cannot come from the engine: raise `ValueError`.

In every incomplete case `feasible` is False and `issues` gets, after the failure's own line,
`"not simulated: <n> of <m> children (stopped after <unit>/<corner>)"`.

`objective.evaluate_partial(spec, metrics) -> Evaluation`: like `evaluate` but skips the constraints whose metric is
absent or non-finite, computes no `fom`; status `constraint_failed` with the violations, else `"incomplete"`. `evaluate`
itself is unchanged.

## 7. `sim.evaluate` (`blocks/evaluate.py`)

- Signature gains `stop_at_first_failure: bool | None = None` (None: the spec's setting).
- When on: `Schedule.from_history(spec, <observations of the same problem in the store>, children, scope)` once
  per call, inside the store lock the engine takes (pass the observations the engine already reads, so the file is
  read once); the corner scope is the spec's `corner_policy.constraints`.
- `plan_shape` prints `... = up to N simulations per point (a point stops at the first simulation that fails it)`
  when on, today's text when off.
- After a batch, one line: `[evaluate] step='...': <k> of <n> points stopped early, <s> simulations not run`, only
  when `k > 0`.

## 8. The spec (`spec.py`)

`Simulator.stop_at_first_failure: bool = True`. It is how the run goes, not the problem: add it to the run-settings
set that `Spec.fingerprint()` leaves out, and leave it out of the dump when True (as `operating_points` is), so
every existing spec keeps its fingerprint and its observations stay reusable.

## 9. The `signoff` recipe

Its re-check of the top-k across all corners now stops a point at the first failing corner (the benchmark showed 550
to 1340 of 3100 simulations per run going to points already known to fail). A new keyword `full: bool = False`
passes `stop_at_first_failure=False` to that `evaluate` for a complete per-corner table. `optimize` and the other
recipes need no change.

## 10. Reading a stopped point

- `digest`: in the counts, `stopped_early: <points>` and `simulations_not_run: <sum>` from the issues line of
  section 6; the per-corner tables show the corners present (the best point is feasible, hence complete).
- `analyze` (report): "failures per corner" counts a stopped point at the corner it stopped at; corners it never
  reached are not counted; the constraint-per-corner lines likewise (they already skip a corner whose metric is
  absent).
- `skills/ic-opt/SKILL.md`: one sentence under Read (a point with fewer children than testbenches × corners was
  stopped at its first failure; the issues line says where) and one under Run (`stop_at_first_failure`, `signoff
  full=true`). `skills/author-spec/SKILL.md`: the field, one line.

## 11. Tests (`tests/ic_opt/test_schedule.py`)

1. Order: a hand-built history of 12 observations with three children of known failures and seconds — the order
   equals the hand computation in the test's comments; fewer than 10 observations → spec order; ties → spec order;
   a child without recorded seconds takes its unit's mean.
2. Stop rule: under `all_corners` a violated constraint at a non-nominal corner stops; under `nominal` it does
   not; a metric of the objective missing from a child does not; `failed:spectre` and `metric_failed` stop.
3. Engine, with a fake pipeline whose child stage returns prepared `ChildResult`s: a batch where one point's first
   child violates a constraint — that point has one child, `constraint_failed`, `feasible` False, `simulations` 1,
   the issues line of section 6; a feasible point has every child and `status ok`; with the switch off every point
   has every child; the step log carries `stopped` and `not_run`.
4. `aggregate` on an incomplete set: cases 1 to 3 of section 6, and the `ValueError` of case 4.
5. `evaluate_partial`: skips absent metrics, reports the violations, never a `fom`.
6. Fingerprint: a spec dumped before the field existed and the same spec with the field at its default give the same
   `fingerprint()` and the same dump.
7. Digest and report on a store with stopped points: no exception, the counts are right.
8. The benchmark package (`benchmarks/icopt_bench`) is untouched: its tests pass unchanged.

`ruff check` clean.

## 12. Acceptance (by the coordinator, not the coder)

1. Tests of section 11 and the existing tests green (targeted: `tests/ic_opt/test_schedule.py`, `test_engine*`,
   `test_corner*`, `test_digest*`, `test_report*`, the recipes' tests).
2. Cost against quality, single condition, on the research copy of the PVT benchmark restricted to its nominal
   corner (two testbenches; the same proposer): the schedule's order with the stop against every child of every
   point, 6 circuits × 3 seeds, compared at equal simulation counts — the first feasible design not later and the
   best objective not worse in the paired comparison. This is the coordinator's measurement, outside the repository.
3. On the user's mixers, real Spectre, after the user's approval with the numbers: one run per mixer with the
   switch on and one with it off, same seed and budget; the simulations spent and the best objective; every stopped
   point's verdict confirmed by simulating its remaining testbenches afterwards (none may turn out feasible).

## 13. Out of scope

The multi-corner view for the proposer and the corner model (steps 2 and 3), running one point's children in
parallel or in portions, scheduling EM devices, any change to a strategy.
