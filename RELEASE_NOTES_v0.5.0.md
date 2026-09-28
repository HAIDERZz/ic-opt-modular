# IC-Opt 0.5.0

0.4.0 searched with OpenBox and TuRBO as they came, fed in a way that hid most
of what a simulation had found. This release is the first stage of the
optimizer work: a benchmark to measure a strategy on, the existing strategies
fed honestly and searching on the right scale, a new strategy for a spec
without EM devices at one condition, and the means for whoever reads a run (a
person, an agent) to see what it found and to advise it. ic-opt itself calls
no language model.

Stores written by 0.4.0 are reused as they are. There is no migration step.

## Changes in behaviour

Read these before upgrading a project that runs.

1. **The default strategy is `auto`.** A run without `strategy=` uses
   `metric_gp` when the spec has no EM devices and the run covers one condition
   (no corners, or one corner: `corners='["tt"]'`, the `signoff` recipe's
   search), and `openbox_gp_eic` otherwise. A line says which and why, under
   `--plan` too. A run started by 0.4.0 without `strategy=` and continued by
   this version may therefore continue with another strategy (its observations
   are its history either way); name `strategy=openbox_gp_eic` to keep the old
   one.
2. **OpenBox and TuRBO are fed each point's true objective.** A point that
   misses a constraint no longer arrives as a penalty of 1e6; a point whose
   metrics failed is a failed trial for OpenBox and the worst target for
   TuRBO. `failure_penalty` is accepted and ignored. The same seed proposes
   other points than 0.4.0 did.
3. **The initial design is ic-opt's own, and the design as exported comes
   first.** One seeded Sobol design per spec and seed, served in order
   whatever the batch size. `opt.optimize` evaluates the current values of the
   exported netlists first (`current=false`: not), then the rows of
   `start=FILE`, once. An observation's `origin` says where a point came from
   (`start`, `...:init`, `...:acq`, `...:tr:<r>:<k>`, ...).
4. **A variable whose range spans a decade is searched on a logarithmic
   scale** (lower bound positive, `upper / lower >= 10`) by `metric_gp`,
   `openbox_*` and `turbo`, the initial design included. A spec without such a
   variable is searched as before. `random`, `sobol` and the `points.*` blocks
   stay linear.
5. **A Spectre netlist gets the statements for operating points** (a DC
   analysis and an `info what=oppoint`, after the netlist's own analyses, where
   the export has none). The metrics are not affected: measured on a real
   periodic-steady-state testbench, the result is identical to the last digit
   with and without them. `simulator.operating_points: false` renders the
   netlist as 0.4.0 did. An observation's line grows with the number of
   transistors.
6. **`opt.optimize` holds the project for its whole run** (`.icopt/run.lock`):
   a second run on the same project is refused when it starts, not at its
   first batch, and `ic-opt advise` is refused while a run goes.
7. **The report's "Space compression advisory" is "Where the best points
   are"**: per variable the span of the best feasible points, one level wider.
   The report no longer needs OpenBox installed, and its importance model runs
   on one thread (it took every physical core of the machine before, whatever
   the spec's thread settings said).

## New

- **The strategy `metric_gp`** (`docs/refactor/T17_1_METRIC_GP_SPEC.md`): one
  Gaussian process per metric, the spec's own formulas for the objective and
  the constraints applied to their samples, no penalty number anywhere; a
  model of whether a point gives a value at all; a search region around the
  best point that grows and shrinks with its success; points on the spec's
  grid, never a point twice. It needs numpy, scipy and scikit-learn, which the
  package already depends on. Named explicitly it refuses EM devices and more
  than one corner; those stay with `openbox_gp_eic` until the next stages.
- **`ic-opt digest PROJECT`**: what a run found, every number computed from
  its observations — how far the run is, which constraints bind, where the
  good points are, where points gave no value and what they said, the
  strategy's state, the advice given and how it fared, the operating points of
  the best point. It takes no lock and reads a run that is still going. Block
  `analyze.digest`.
- **`ic-opt advise PROJECT FILE`**: advice as data — start rows, narrower
  ranges, variables to hold — with an author and a reason, recorded in
  `.icopt/advice.jsonl`, revocable (`--revoke ID --reason "..."`, `--list`).
  Every strategy evaluates the start rows first; `metric_gp` looks inside the
  ranges with four fifths of each batch, and a fifth chooses as the run would
  without advice. An advice cannot widen the spec's ranges or change
  constraints, objective or budget. Blocks `opt.advise`, `opt.revoke_advice`.
- **Operating points** of every transistor with each observation (region,
  ids, vgs, vds, vbs, vth, vdsat, gm, gds, gm/id, cgs, cgd), for reading; no
  strategy uses them. `ic-opt doctor` says per testbench whether the export
  asks for them.
- **`benchmarks/`** (in the checkout, not in the package): eight synthetic
  problems and 20 open-source amplifier and regulator circuits (AnalogGym,
  SKY130, ngspice), every strategy driven through the product's own
  `suggest`, a split into development and held-out circuits made before any
  strategy ran, and the measures and conditions a strategy is judged by.
- **`skills/ic-opt`** gained how to read a digest and how to advise a run,
  from eight recorded sessions in which an agent did both.

## Measured

Every number below is from runs of 200 points in batches of 10 on the
benchmark; "better" and "worse" mean beyond what the seeds differ (one-sided
rank test at 5 %); four measures per problem (first feasible point, best at 50,
100 and 200 points). Details, tables and what was tried and dropped:
`docs/refactor/T17_OPTIMIZER_PLAN_CN.md`, section 7.

- **Feeding** (change 2), eight synthetic problems, 20 seeds, against 0.4.0 as
  released: OpenBox better on 21 of 32 measures and worse on 1; TuRBO better on
  15 and worse on 2.
- **`metric_gp` on the eight held-out circuits** (16 problems, 10 seeds; the
  held-out circuits were not used to set anything), against OpenBox with the
  feeding fixed: no measure's median worse on 16 of 16 problems; at least one
  measure better on 15; the worst quarter of the runs no worse on 16. Wide
  ranges, share of runs with a feasible point within 50 / 100 / 200 points:
  `metric_gp` 80 % / 99 % / 100 %, OpenBox 18 % / 51 % / 81 %, TuRBO 35 % /
  71 % / 78 %.
- **Where the lead comes from**, by switching one part off at a time
  (development circuits, wide ranges, 48 measures): the logarithmic scale (23
  measures worse without it) and the search region (20). Using points that
  gave part of their metrics, modelling each metric on its own, and the model
  of whether a point gives a value showed no contribution on these circuits.
- **The logarithmic scale for the existing strategies** (change 4), same 48
  measures: TuRBO better on 23 and worse on none, OpenBox better on 14 and
  worse on 1. With it, `metric_gp` against TuRBO: better on 9, worse on 3;
  against OpenBox: better on 25, worse on none. `metric_gp` is the best of the
  three; its lead over TuRBO is small.
- **Advice**, scripted, adopted at 40 points: right ranges ended better than
  no advice on 4 of 12 development circuits and worse on none; wrong ranges
  worse on 1. On the held-out circuits, looked at once with the released
  code: right ranges better on 30 of 40 runs; wrong ranges, never revoked,
  worse on 3 of 8 circuits. The digest's suggested ranges taken as they are
  as an advice helped nowhere. Reviewing an advice every 40 points, and
  revoking one whose points do no better, took most of the loss out of wrong
  advice.
- **Sessions**: on four development circuits an agent read the digest and the
  netlist every 40 points and advised with the commands above, twice per
  circuit. Seven of the eight runs ended better than the run of the same seed
  without advice, one worse. Eight sessions show that the interface works, not
  how much advice is worth: most results lie within what runs without advice
  differ by from seed to seed.

## Verified on real simulators

- **Operating points**: a real Spectre run of a periodic-steady-state
  testbench with the added statements; the metric identical to the last digit;
  the table read back for every transistor. The DC analysis appended after the
  netlist's analyses gives the operating points of one placed before them (48
  of 48 values identical). Not covered: a netlist that changes parameters
  between analyses (`alter`).
- **`metric_gp` and `openbox_gp_eic` on two real mixer projects**, one
  condition, 100 points, three seeds each, 3,600 Spectre simulations, no
  simulation lost. The two problems could not tell the strategies apart: one is
  solved by the initial design both share, the other has no feasible point
  inside its ranges (there `metric_gp` came closer to feasibility on every
  seed). The runs found the thread defect of change 7.

## Not verified on a real machine

Tests and the benchmark cover these; no real Spectre run has.

- `ic-opt advise` and the digest on a Spectre project (the sessions ran on the
  benchmark's circuits, simulated with ngspice).
- The logarithmic scale of `openbox_*` and `turbo` on a real project, and on
  the variables of an EM device (no benchmark problem has one).
- Everything the 0.4.0 notes list as not verified.

## Known limits

- `metric_gp` takes no EM devices and one condition. Several corners and EM
  devices are the next stages of the optimizer work.
- When the models are sure, a batch of 10 is the neighbours of one predicted
  optimum. Three remedies were measured; none helped.
- An advice that is not reviewed costs its share: four fifths of every batch
  look where it says. The review compares its points with the free fifth's,
  which choose in the same search region, so an advice that holds the search
  away from a better region can pass its review (one of the eight sessions).
- The strategies' own computations use as many threads as the BLAS and OpenMP
  environment variables allow: every core when they are unset.
- The benchmark's thresholds come from a fixed recipe, not from a designer: on
  5 of the 12 development circuits the phase-margin threshold is below 45
  degrees. "Feasible" there is not "a usable amplifier".
- The limits the 0.4.0 notes list remain, except that `opt.optimize` now
  evaluates the design as exported.

## Compatibility

- Stores written by 0.4.0 or 0.3.0: reused as they are. Spec and pipeline
  fingerprints are identical under both versions' code (four example specs and
  six real projects). Stores written by 0.2.0 or earlier: `ic-opt
  migrate-store`.
- `spec.yaml`: every 0.4.0 spec validates. New optional key:
  `simulator.operating_points` (default `true`; outside the fingerprint).
- Observations: a Spectre child may hold `operating_points`; `origin` may end
  in `@<advice id>`. Rows without them read as before.
- Recipes: `optimize`, `signoff` and `coarse_to_fine` take `strategy=auto`
  (their default), `current=`, `start=`.

## Test numbers at the tag

- The suite outside `tests/ic_opt/pcell` (`tests/ic_opt`, `tests/benchmarks`): 706 passed, 16 skipped.
- The pcell suite: 975 passed, 1 skipped with a private process profile
  (`IC_OPT_PROFILE_DIRS`); 341 passed, 635 skipped without one, which is what
  a checkout alone runs.
- `ruff check`: clean.
- `scripts/check_clean_install.sh`: PASS.
