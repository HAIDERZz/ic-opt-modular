# IC-Opt 0.6.0

0.5.0 gave `metric_gp` one condition and simulated every point at every corner.
This release is the second stage of the optimizer work: a schedule that runs a
point's simulations in a learned order and stops at the first one that fails
it, `metric_gp` at several corners, a metric read from the operating points, a
digest that says whether a run stalled and what an advice did, and thread
accounting that counts what a run really uses. The literature behind it (106
papers of 2024 to 2026, read in full) and every measurement are recorded in
`docs/refactor/T17_OPTIMIZER_PLAN_CN.md`, section 7. ic-opt itself still calls
no language model.

Stores written by 0.5.0 are reused as they are. There is no migration step.

## Changes in behaviour

Read these before upgrading a project that runs.

1. **`auto` is `metric_gp` at any corners.** A run without `strategy=` on a
   spec without EM devices uses `metric_gp` whatever the corners (0.5.0 used
   `openbox_gp_eic` at several). At several corners each metric reaches the
   models at its worst over the corners a point simulated; the verdict is the
   point's own. Name `strategy=openbox_gp_eic` to keep the old strategy.
2. **A point stops at its first failing simulation when it needs 20 or more
   simulations** (testbenches × corners), in an order learned from the
   project's observations; below 20 every simulation of every point runs, as
   0.5.0 did. `simulator.stop_at_first_failure: true` stops whatever the count,
   `false` never stops (a characterization run); both are now written to the
   spec's dump. The `signoff` recipe's re-check stops at the first failing
   corner whatever the count, unless `full=true` or the spec says `false`. A
   stopped point is recorded with the simulations it ran and `not_run` naming
   the others; it is never feasible; `sim.evaluate` prints per batch how many
   points stopped and how many simulations were not run.
3. **Each testbench job counts `threads_per_run + 1` threads** (the process
   that extracts the metrics runs beside the simulator). A spec that filled the
   host's `max_threads` exactly may now have `parallel_jobs` trimmed, or be
   refused with a message that names the +1; `--plan` and the run's header
   line show `jobs × (threads + 1)`. EMX jobs are counted as before.
4. **The strategy's own computation uses `simulator.strategy_threads`** (default
   1) threads: BLAS and OpenMP through threadpoolctl, TuRBO's torch through
   `set_num_threads`. 0.5.0 let those libraries take every core. The same seed
   proposes the same points; a run that relied on the machine's cores for the
   strategy is slower to propose until the field is raised. An environment
   variable that caps lower keeps its effect.
5. **The digest is version 3.** New entries (below); the issue texts it quotes
   are cut to 200 characters. `ic-opt advise` records an advice it refused in
   `.icopt/advice.jsonl` as a row with `event: refuse` (ids `r1`, `r2`, ...);
   code that reads that file must pass over such rows, as the tool's own
   readers do.

## New

- **The evaluation schedule** (`docs/refactor/T17_8_SCHEDULE_SPEC.md`,
  `T17_9_MULTI_CORNER_SPEC.md`): the order of a point's simulations is
  computed once per batch from the observations, `(failures + 1) / (reached +
  2) / seconds` per simulation, cheap and often-failing first; the stop rule
  and its default are change 2. Nothing is stored: the next batch computes the
  order again.
- **A metric read from the operating points**
  (`docs/refactor/T17_11_SATURATION_MARGIN_SPEC.md`): `saturation_margin:
  {instances: [...]}` on a testbench gives the smallest `|vds| − |vdsat|` over
  the named transistors, computed by ic-opt from the operating-point table,
  no OCEAN expression. A transistor the table lacks makes the point
  `metric_failed` with an issue naming it. With a DC-only testbench and the
  schedule on, it is the first gate a point meets. A constraint's value takes
  no SI prefix: write `0.05 V`, not `50m V`.
- **Digest version 3** (`docs/refactor/T17_10_DIGEST_SPEC.md`): a stall line
  (batches since the best improved, where the `metric_gp` region last
  restarted); per advice whether the run's best improved under it, the share
  of points that came from it and a verdict (`helped` / `no_help` / `mixed`);
  the variables by importance (mutual information with each metric, no model
  fitted); what failed the points by stage; the advice that was refused; and,
  from the schedule, after which simulation points stopped. A leak test pins
  that no file path, netlist text, metric expression or simulator setting
  reaches the digest.
- **`skills/ic-opt`** gained how to read the new digest entries, how a DC
  testbench with a saturation-margin metric is written, and what an agent may
  compute on its own side before advising (a gm/ID lookup of the process, the
  ranges a constraint implies). **`skills/author-spec`** describes the
  `saturation_margin` metric and the `simulator` fields `strategy_threads` and
  `stop_at_first_failure`.

## Measured

Every number below comes from the research benchmark (six AnalogGym circuits
at 31 PVT corners, ngspice, one simulation as the unit of cost, 3100
simulations per run, the same proposer everywhere) or from the user's two
28 nm mixers on Spectre; "better" and "worse" count paired runs (same circuit,
same seed) and the p value is a sign test. Details and every table:
`docs/refactor/T17_OPTIMIZER_PLAN_CN.md`, section 7 (2026-09-29 and 30).

- **The schedule against "every point at every corner"**, five seeds: better
  on the final objective in 27 of 30 paired runs (p < 0.001), a feasible design
  found in 30 of 30 runs against 22; against "search at the nominal corner,
  then verify" (the `signoff` recipe's shape) 21 better, 9 worse (p = 0.043);
  against four schedules taken from papers (every corner, nominal first,
  clustered corners with tightened thresholds, the last worst corner first)
  never worse and mostly better.
- **Where the stop helps** (why change 2 has a threshold): at 62 simulations
  per point the stop was better in 27 of 30; at 18 even (11 to 7); at 6 worse
  in 15 of 18 (p = 0.008); at 2 worse in 12 of 18. Between 20 and 61 nothing
  was measured.
- **On the user's mixers at three corners** (one seed per arm): the stop kept
  21 % of the simulations (191 of 900) and reached the same best point on the
  112G mixer, but found its first feasible point later (60th against 13th);
  on MixerCS the arm without the stop found the one feasible point, the arm
  with it none. 40 stopped points simulated in full afterwards: none feasible.
- **Not adopted, measured and recorded**: a registry of each constraint's
  worst corner as the first simulations of a point (16 better, 14 worse
  against the learned order); a corner model that estimates a point's
  unsimulated corners (8 / 10, 11 / 7, 5 / 13 in three forms); modelling
  each metric's rank instead of its value (5 / 0 on the development circuits,
  0 / 2 on the held-out ones); a batch rule that scores a candidate by its
  expected gain against the batch's earlier picks (0 / 1); three other batch
  and share rules (none better).

## Known limits

- The stop's threshold of 20 simulations per point is the smallest count at
  which no harm was measured; the range 20 to 61 is unmeasured.
- The +1 thread per testbench job is an accounting rule from one measurement
  (a run of ten one-thread jobs occupied about twelve cores); it was not
  measured again after the change.
- The `saturation_margin` metric was tested against fake simulators; the
  operating-point tables of real Spectre runs name transistors as `/M1`, and
  the lookup takes the name with or without the leading slash.
- `metric_gp` still refuses a spec with EM devices; the EM stage is deferred.
