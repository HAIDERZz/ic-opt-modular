# T17.9 — step 2 of the evaluation schedule: several corners, in the product

Status: specification (2026-09-29, evening), for the coding subagent. Decisions behind it: the user's answers of
2026-09-29 23:20 to `T17_STRUCTURE_CANDIDATES_CN.md` (3.1 A, 3.2 B, 3.3 A), recorded in `T17_OPTIMIZER_PLAN_CN.md`
section 7. Step 1 (`T17_8_SCHEDULE_SPEC.md`, merged as `f8338a5`) built the schedule; this step lets the product use it
on a run that evaluates points at several corners, with the project's own strategy.

What is measured behind it (the research benchmark, six AnalogGym circuits at 31 corners, 3 seeds, 3100
simulations per run, the same proposer everywhere): the schedule — a point's simulations in a learned order, the
point stopped at the first one that fails it — against "every point at every corner" was better on the final
objective in 17 of 18 paired runs and found a feasible design in 18 of 18 runs (12 of 18 for the other). On the
single-condition benchmark (one corner, two testbenches) the same stop was *worse* on the final objective (6 better,
12 worse): a stopped point there keeps half of its metrics. So the switch defaults on at several corners and off at
one. A corner model (estimating a point's unsimulated corners) was measured and is not built: the proposer sees, per
metric, the worst of what was simulated.

## 1. What changes

1. **The default of `simulator.stop_at_first_failure` follows the run.** Unset (`None`, the new default), the stop is
   on when the batch's points run at more than one corner and off otherwise. `true` / `false` in the spec keep their
   meaning; the recipe override `sim.evaluate(stop_at_first_failure=...)` keeps its meaning (`signoff full=`).
2. **`metric_gp` takes a run at several corners.** The corner refusals go (`CORNERS_REFUSAL`, the corner half of
   `stage_one_refusal`, the corner branch of `resolve_auto`, `_refuse`'s corner check). `strategy=auto` resolves to
   `metric_gp` for any spec without EM devices, whatever the corners. The device refusal stays as it is.
3. **What `metric_gp` is handed at several corners: one row per point, each metric at its worst.** Section 3.
4. **The digest names where points stopped.** Section 5.
5. Skill sentences, tests.

Nothing else: the schedule's order and stop rule (`eval/schedule.py`), the verdict (`sim/corner.py`), the record
(`observation.py`), OpenBox and TuRBO, `signoff`, advice, the region and the batch rule of `metric_gp` are unchanged.

## 2. The default of the stop (`spec.py`, `blocks/evaluate.py`)

- `Simulator.stop_at_first_failure: bool | None = None`. `_dump` leaves the field out of the dump when it is `None`
  (today it leaves it out when `True`); `true` and `false` are written. The field stays outside the fingerprint
  (`_NOT_PROBLEM`). A spec written with `stop_at_first_failure: true` before this step keeps its meaning.
- `sim.evaluate`: `stop = override if override is not None else spec.simulator.stop_at_first_failure`; when that is
  still `None`, `stop = len({c.corner for c in children if c.unit_kind != "device"}) > 1` for the children the run's
  corners produce (`engine.children_of`). One helper, `stop_wanted(spec, children, override) -> bool`, used by
  `evaluate` and `plan_shape`, so the plan line and the run agree. Put the rule's reason in its docstring (the two
  benchmark results above).
- `plan_shape` prints as today: "up to N simulations per point (a point stops at the first simulation that fails it)"
  when the stop is on, "N simulations per point" when off.
- `signoff`: its search step runs at one corner — off by default now — and its re-check at all corners — on; `full=`
  still forces off. Nothing to change there beyond the docstring's words.

## 3. `metric_gp` at several corners (`suggesters/metric_gp/`, `blocks/optimize.py`, `suggesters/__init__.py`)

The strategy still sees one row per point (`Observation`) and knows nothing of corners in its models, region or batch
rule. What changes is which numbers a row's metrics carry when the rows were evaluated at several corners:

- **Constrained metric**: its worst value over the *scored corners* the point simulated (`digest.constraint_value`
  computes exactly this today: the smallest for a lower bound, the largest for an upper one, over
  `digest.scored_corners`; a metric with constraints of both kinds takes the corner value with the smallest margin over
  all its constraints, margin < 0 when violated). Move that computation to one place both use (suggested:
  `sim/corner.py` — `worst_metrics(spec, o) -> dict[str, float]`, with `metrics_per_corner` and `scored_corners` beside
  it; `digest.py` imports them from there).
- **Metric named only by the objective**: its value at the corner whose objective is worst (largest, minimization
  form) among the corners where every metric of the objective is present; when no corner has them all, the metric is
  absent (nan to the models, as a missing metric is today).
- **A point evaluated at one corner** (single-condition runs, `signoff`'s search step, a spec without corners) keeps
  `o.metrics` exactly: the rule above reduces to it, and the proposals of every existing single-condition run must
  stay byte for byte what they are (test).
- Where: `compose.true_arrays(spec, rows)` reads `worst_metrics(spec, o)` instead of `o.metrics`; `metric_scales`
  and anything else that reads metric values for the models go through the same values. Status, `infeasibility_key`,
  `feasible`, `objective` stay the observation's own (the verdict is the verdict; the view only feeds the models).
- `blocks/optimize._at_corners` stays: `metric_gp` is handed the rows evaluated at exactly the run's corners (a
  point stopped early counts at every corner it was to run at, `Observation.corners()`). `history_size`,
  `advise`, `revoke_advice`: unchanged.
- `resolve_auto(spec, corners, history)`: `metric_gp` unless the spec has devices; the reason string says
  `"no EM devices"`. The `corners` and `history` parameters stay (callers pass them; the history's corner check goes).
  Update the docstrings of `blocks/optimize.optimize`, `suggesters/__init__`, `recipes/optimize`, `recipes/signoff`,
  `recipes/coarse_to_fine` where they say several corners resolve to `openbox_gp_eic`.
- `MetricGpSuggester.propose` on a history whose rows were evaluated at several *sets* of corners (a store holding a
  `signoff` search and its re-check, handed without the corner filter through `opt.suggest`) still refuses with the
  message today's `_refuse` gives for that case, reworded: the rows must share one set of corners; `opt.optimize`
  filters them, `opt.suggest` called directly must be handed one set.

Why per-metric worst and not the aggregate's selected corner: `Observation.metrics` holds one corner's values (the
worst-penalty corner, or the worst-objective corner of a feasible point); a constraint another corner violates alone
would feed the model a passing value. The benchmark's proposer was fed each constrained metric at its worst and the
worst-corner objective; this is the product's form of that view (the objective is composed from the metrics'
samples, as everywhere in `metric_gp`).

## 4. The record and the digest

- No new field. A point stopped early is as T17.8 records it (`not_run`).
- `digest.counts` adds `stopped_at`: per child key `<unit>/<corner>`, how many points the schedule stopped there
  (the child named in the point's "not simulated" issue is the stopper; read it from the children: the last child in
  the engine's order that ran, i.e. the child of `o.children` that is not followed by another ran child — simpler: the
  child whose key precedes the first `not_run` key in the run's child order is not knowable from the row alone, so
  take the *stopper* as `sim/corner.py` names it: expose `stopper(spec, o) -> str | None` there, the same rule
  `_incomplete` uses for its issue line, and use it in both). The markdown digest prints the three largest entries in
  one line under the counts ("stopped early: 37 points; at ss/ac: 20, tt/tran: 9, ...").

## 5. Skills

- `skills/ic-opt/SKILL.md`: the `strategy=auto` sentences (lines that say "metric_gp for a spec without EM devices at
  one condition ... openbox_gp_eic otherwise", "named explicitly it refuses EM devices and more than one corner", the
  `coarse_to_fine` auto line): `auto` is `metric_gp` for a spec without EM devices, at any corners; named,
  `metric_gp` refuses EM devices only. The stop sentence in step 4: "a point stops at the first simulation that fails
  it *when the run evaluates points at several corners*; at one corner every simulation of a point runs unless
  `simulator.stop_at_first_failure: true` says otherwise; `false` runs every simulation at any corners". One sentence
  in step 5 for `stopped_at`.
- `skills/author-spec/SKILL.md`: one sentence where `simulator` fields are listed, if they are.

## 6. Tests

`tests/ic_opt/test_multi_corner.py` (new) with fake stages as `test_schedule.py` uses:

1. The default: a spec without the field, run at two corners → the plan line says "up to", points stop; run at one
   corner (`corners=["tt"]`) → "N simulations per point", nothing stops; `stop_at_first_failure: true` at one corner →
   stops; `false` at two → nothing stops; the recipe override wins over the spec.
2. Dump: `None` absent from the dump, `true` and `false` present; fingerprint unchanged by any of the three.
3. `worst_metrics`: at three corners, a lower-bound metric takes its minimum, an upper-bound one its maximum, a
   both-bound metric the smallest margin, an objective-only metric the worst-objective corner's value; a stopped point
   (two of three corners ran) takes the worst of the two; a one-corner row equals `o.metrics`.
4. `metric_gp` proposes on a two-corner history (rows built through the engine with fake stages, some stopped early)
   deterministically: same history and seed, same proposal; `resolve_auto` gives `metric_gp` for the cornered spec
   without devices and `openbox_gp_eic` with devices; `optimize(strategy="metric_gp", corners="all")` on the
   two-corner spec runs (no refusal), under `--plan` too.
5. Single-condition parity: the proposal of `metric_gp` on a one-corner history equals, byte for byte, what it was
   before this step — take the golden proposal `test_metric_gp.py` already pins (or pin one there from the code
   before the change, in the first commit, and assert it after).
6. The digest's `stopped_at` on a run with stopped points; absent (empty) on a run without.

Update the tests this step makes wrong (`test_metric_gp.py` refusal tests, `test_optimize.py::resolve_auto`,
`test_schedule.py` lines that assert the old default or `resolve_auto`'s corner reason) to assert the new rule; do not
delete a test that still says something true.

Targeted run (tests count as machine load; the coordinator has other runs going — do not run the whole suite more
than once): `tests/ic_opt/test_multi_corner.py test_schedule.py test_metric_gp.py test_optimize.py test_digest.py
test_engine.py test_corner*.py test_advice.py tests of the recipes`, then `ruff check src tests`.

## 7. Working rules for the coder

- Branch `t17-9-multi-corner` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t17-9-multi-corner -b t17-9-multi-corner main`
  from the repository); commits on that branch only; never touch `main`, never push.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `python -c "import ic_opt, sys; print(ic_opt.__file__)"` shows the worktree); never `uv run`, `uv sync`, `pip`.
- No simulator runs, no network. The fake stages of the tests are the only "simulations".
- One commit per coherent piece (suggested three: the default; `metric_gp` at several corners; digest + skills), each
  message saying what and why; trailer `Co-Authored-By: <the agent's own>`.
- Hand back: the branch name and commits, the test and ruff results verbatim, and what the specification left open
  and how it was read (a list, not prose).

## 8. Acceptance (by the coordinator)

1. Section 6 green; the existing suite's targeted parts green; ruff clean; single-condition proposals byte-identical.
2. The multi-corner spec of the user's mixers under `--plan`: `strategy auto: metric_gp (no EM devices)`, "up to N
   simulations per point". The real run is batch 丁 of `T17_STRUCTURE_CANDIDATES_CN.md` section 6 (needs the user's
   approval and thread count).

## 9. Out of scope

The registry rule for the first simulations of a point (candidate structure 3.2 B) — measured first on the research
benchmark (`pvt_bench/METHODS_SPEC_3.md`), built here only if it wins. The corner model. EM devices. The digest's
other additions (T17.10).

## 10. Record (2026-09-30)

Implemented by the coding subagent on `t17-9-multi-corner` (`f6ee461`, `1aa9a2d`, `876487a`), merged `aa3a70d` after
T17.10 (one conflict in `skills/ic-opt/SKILL.md`, both edits kept); targeted tests 225 passed, ruff clean; the coder's
whole-suite run 1081 passed, 651 skipped (environment-gated files). How the open points were read (the coder's list):

- `stop_wanted(spec, children, override)` in `blocks/evaluate.py`; `evaluate` and `plan_shape` both build the children
  with `engine.children_of`. `true` is now written to the dump (a spec stating it gets another `_legacy_fingerprint`,
  the same `fingerprint`; no pre-T15.2 store can hold the field).
- `worst_metrics`: under `corner_policy.objective: nominal` an objective-only metric takes the nominal corner
  (`scored_corners(spec, policy=)`); where no scored corner holds a constrained metric, the point's own value; a metric
  neither names is left out; "every metric of the objective present" means present and finite; a one-corner point
  keeps `o.metrics` by an explicit shortcut (the general rule does not reduce to it for a failed point); at several
  corners a failed / metric_failed point gives the worst of what its other children measured, its status unchanged.
- The search region's incumbent is ranked by the objective composed from the worst values (more pessimistic than the
  verdict's worst-corner objective when metrics are worst at different corners); `Observations.best` and the digest
  keep the verdict.
- Beyond the spec, accepted: `opt.optimize` filters the `initial=` rows it hands `metric_gp` through `_at_corners`
  too (otherwise rows adopted from another set of corners would make it refuse at the second batch); such rows are
  dropped from the models and the schedule's history.
- `_refuse` names each set of corners with its count (`SETS_REFUSAL`); `stage_one_refusal(spec, corners=None)` keeps
  its name, `corners` unread. `resolve_auto`'s reason is `no EM devices`.
- `counts.stopped_at` is always present (`{}` when nothing stopped), keys `<unit>/<corner>`, ordered by count then key;
  the markdown line lists three. `stopper(spec, o)` is None for a point not stopped and for a row whose children show
  no failure under this spec (so the sum can be below `stopped_early`); `_incomplete`'s issue line and the count use
  one `_stopper`.
- A `metric_gp` proposal on a one-corner history was pinned before the change (`TT_PROPOSAL`) and is unchanged after.
- `worst_metrics` costs about 0.17 ms per 31-corner row; the region replay recomputes scales per batch (about 0.9 s
  for 300 points × 28 batches × 31 corners), left as is.
- Acceptance 8.2 by the coordinator: the 112G mixer's three-corner spec under `--plan` on the merged code prints
  `strategy auto: metric_gp (no EM devices)` and `up to 9 simulations per point (a point stops at the first simulation
  that fails it)`; batch 丁 started 2026-09-30 00:23 (`ic-opt-accept/t17_9_corners/`).

## 11. Revision 2 (2026-09-30): the default follows the simulations a point needs, not the corners

Decided by the user on 2026-09-30 ("同意") on the N-92 measurement (`T17_OPTIMIZER_PLAN_CN.md` section 7): the stop
against every point at every corner, same proposer, paired runs on the final objective -- at 62 simulations per point
better in 27 of 30; at 18 even (11 to 7, the first feasible design later in 13 of 18); at 6 worse in 15 of 18
(p = 0.008); at 2 worse in 12 of 18. The harm of a stopped point (the metrics of the simulations it did not run are
lost to the models) had disappeared by 18 simulations per point; the gain was clear at 62; nothing between 20 and 61
was measured.

- `blocks.evaluate.stop_wanted`: unset, the stop is on when a point runs at least `STOP_FROM_SIMULATIONS = 20`
  testbench simulations (testbenches × corners; EM devices do not count) and off below that. The spec's switch and a
  recipe's override decide as before.
- The `signoff` recipe's re-check keeps stopping at the first failing corner whatever the count (unless `full`, or
  the spec says `false`): it verifies and teaches no model, so the stop's only effect there is the saving
  (`recipes.signoff._recheck_stop`).
- Tests: `test_multi_corner.py` -- the two-by-two spec (4 per point) is off by default, on by the spec's switch or the
  override; a ten-corner spec (20 per point) is on, its nine-corner run (18) off; the plan line follows. The tests
  that exercise the stop's mechanics pass the override explicitly.
- Documents: the README's `auto` sentence, `skills/ic-opt/SKILL.md` step 4, `skills/author-spec/SKILL.md` (the DC
  testbench section and the `simulator` field list).
