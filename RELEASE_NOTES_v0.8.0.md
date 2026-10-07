# IC-Opt 0.8.0

0.7.0 let a circuit take its devices from a library table. This release
polishes that chain rather than extending it: a point whose device fails a
constraint no longer runs its circuit simulations, the table can grow around a
run's best point with a few real EMX runs (`lib_refine`, the last link of the
chain), the single-turn primary of `xfm_ms` takes a tap on its own metal (so
`lib_tap` builds tapped twins of both transformer families), the ground
fixture no longer closes its opening into a sharp wedge, the benchmark harness
treats an unfinished ngspice run as the failure it is, and the metric models'
hyperparameter search has a direct likelihood behind a switch (off by
default). One real comparison of the three strategies on the mixer library
platform is recorded. The decisions behind the batch are in
`docs/refactor/BACKLOG_CN.md`, section 0.13; each item has its specification
and record under `docs/refactor/`. ic-opt itself still calls no language model.

Stores written by 0.7.0 are reused as they are. There is no migration step:
no spec field changed its meaning, the new `ct_primary_width_um` of an
`xfm_ms` device is dropped from the fingerprint when absent, and the batches
every strategy proposes are pinned unchanged by tests (the recorded-run
replays now run in the release gate instead of being skipped).

## Changes in behaviour

Read these before upgrading a project that runs.

1. **The device is measured first, and a device that fails stops the point**
   (`docs/refactor/N63_DEVICE_FIRST_SPEC.md`). A point's device children (a
   library row's measurement, or the measurement of the sNp the point-level
   EMX stage produced) run before every testbench child; when one fails a
   constraint written on its metrics (`SRF_p > ...`, `Qp_112g > ...`) or its
   measurement fails, the point stops before any circuit simulation, whatever
   the count rule for testbench stops says. The record is the stopped point's
   of 0.6.0: the children that ran, `constraint_failed` (or `failed:<stage>`
   / `metric_failed`), `feasible` false, `not_run` naming every testbench
   child, the device's metrics kept for the models. `--plan` says `(the device
   measured first: a point whose device fails a constraint stops before any
   testbench simulation)`; the batch line reads `K of N points stopped early
   (D at the device), S simulations not run`; the digest's counts gain
   `stopped_at_device` (the digest stays version 4). `simulator.stop_at_first_failure:
   false`, or `signoff full=true`, runs every child of every point as before.
   A circuit-only run below the count rule builds no schedule and records what
   it recorded before, byte for byte. OpenBox and TuRBO receive a
   device-stopped point as a failed trial (0.6.0's rule for stopped points);
   `metric_gp` trains its device-metric models on the device's metrics.
2. **`xfm_ms` takes a same-metal tap on its single-turn primary**
   (`docs/refactor/T19_6_MS_SAME_METAL_TAP_SPEC.md`): `ct_primary_metal` equal
   to `primary_metal` (by stack position) was refused and now draws the tap of
   `xfm_bs` (0.7.0's T19.4): no via stack, the lead on the primary's own
   metal, `CTP` at its far tip, optionally `ct_primary_width_um` wide. Below
   the primary the via-stack tap is what it was, above it is still refused.
   The multi-turn secondary's tap keeps its rule (a via stack at least two
   levels down). Every existing configuration builds byte for byte (1 522 and
   1 621 geometry files compared on the demo and on a private profile).
3. **Two neighbouring ground stubs no longer close the ring's opening into a
   wedge** (`docs/refactor/N65_STUB_CHAMFER_SPEC.md`): when their chamfers
   would leave less than the fixture metal's minimum spacing at the ring's
   inner edge, both facing chamfers are shortened to the largest value on the
   manufacturing grid that keeps that spacing (never below 0; the outer sides
   and every other stub unchanged), and the manifest records
   `geometry.stub_chamfers_um` -- only then. A pair already at or above the
   minimum, `stub_chamfer_um: 0`, and reference mode (no profile) are
   unchanged, so every existing build is byte for byte except the ones the
   DRC gate or the layout used to refuse for exactly this wedge, which now
   build and pass. The library's rows all have `stub_chamfer_um` 0 and are not
   affected.
4. **`lib_tap` builds tapped twins of `xfm_ms` tables too**: `taps.primary`
   may be `"same"` or a metal below the primary; `taps.secondary` a metal at
   least two levels below the secondary or `null` (`"same"` and
   `secondary_width_um` are refused before anything is built, naming why).
5. **Benchmarks** (`benchmarks/icopt_bench/analoggym.py`, N-82): an ngspice
   run that hit its timeout, crashed or exited nonzero is `failed:ngspice`
   and none of the files it left is read -- a waveform cut at a line boundary
   used to analyse as a short, plausible transient. An ac/dc run with a
   nonzero exit that still wrote its results keeps them.

## New

- **`ic-opt run lib_refine PROJECT [device=<id>] [steps=1] [n=8] [prefer=<max|min:column>] [threads=N] [memory_gb=G] --plan`**
  (`docs/refactor/N91_LIB_REFINE_SPEC.md`; `docs/em/library.md`, "Refining
  around a run's best point"): from the run's best feasible point (a
  re-checked one first) and the library row each device took, the geometries
  within `steps` steps of the row (the stratum's `steps` in `library.yaml`,
  the turns dim held, the row and the table's own rows excluded) are predicted
  by the library's models, kept when every variable's column is `predicted`
  inside the device's window and the predicted system SRF clears the margin,
  ranked by `prefer`, taken through the generator and the DRC gate, simulated
  with EMX under the part's own spec, compared with the predictions (z,
  inside) and adopted into the row's part (origin `refine:<project>:<obs>`),
  the stratum's dataset rebuilt; the last line is the command that continues
  the run. `--plan` prints all of it and runs nothing. The plan and the report
  say for each candidate which combination of the run's grid it lands on and
  whether the run has already evaluated that combination: an adopted row
  reaches the circuit only on a combination the run can still propose.
- **`metric_gp.models.Likelihood`** (`docs/refactor/N81_FAST_GP_SPEC.md`): the
  metrics' log marginal likelihood and gradient computed without sklearn's
  `(n, n, parameters)` gradient array, behind `models.DIRECT_LIKELIHOOD`
  (default `False`: the fit is sklearn's, byte for byte). With the switch on
  the search runs on it with the same kernel, bounds, priors, starts and
  random draws, and the regressor is fitted once at the result, so everything
  read from a model is computed as before.
- **`tests/ic_opt/test_lib_refine.py`, `test_fixture_chamfer.py`,
  `pcell/test_xfm_ms_same_metal_tap.py`, `test_metric_gp_fit.py`,
  `tests/benchmarks/test_analoggym_timeout.py`**, and the schedule's device
  tests; the release gate runs the recorded-run replays.

## Measured

Every number below comes from the user's private device library, the mixer
platform on Spectre and the recorded runs; none of them is in this repository.

- **The device first, replayed on the 0.5.0 joint run** (80 points, pcell +
  EMX, three testbenches at three corners): the rule stops exactly the 22
  points whose `SRF_p` failed, no other, none of them feasible; they had
  spent 198 of the run's 861 simulations on circuit simulations of a device
  that could not be used (`ic-opt-accept/n63_accept/replay_n51.txt`).
- **The device first on the mixer library platform** with a deliberately
  tight device constraint (`Qp_112g > 14`, which 15 of the 45 rows the 0.7.0
  run visited fail), the device rule on against `stop_at_first_failure: false`,
  same seed, budget and start rows: with the rule on, 5 of 45 points stopped
  at the device (0 simulations each, their 15 circuit simulations not run),
  150 simulations against 165, 549 s against 604 s; both found the same best
  point feasible at every corner (-0.697 against -0.695, the two runs'
  proposals diverging slightly after the first stopped point), 21 of 40
  search points feasible at tt in both (`ic-opt-accept/n63_accept/RESULT_CN.md`).
- **Three strategies on the mixer library platform** (the 0.7.0 run's spec
  and start rows, `signoff` with 40 search points at tt in batches of 10 and
  the top 5 re-checked at tt/ss/ff, two seeds each, 8 jobs of 11 threads):
  the best point feasible at every corner was -0.702 and -0.680 with
  `metric_gp` (24 and 29 of 40 search points feasible at tt, 5 of 5 re-checked
  points feasible at every corner), -0.609 and -0.641 with `turbo` (20 and 24;
  4 and 3 of 5), -0.595 and -0.661 with `openbox_gp_eic` (24 and 26; 4 and 5
  of 5); every run 45 points, 157 to 165 simulations, 10 minutes. Both
  `metric_gp` seeds beat every other seed by 0.02 to 0.11 on this platform;
  two seeds per strategy is evidence, not a verdict
  (`ic-opt-accept/n76_compare/RESULT_CN.md`).
- **`lib_refine --plan` on that run's best point** (row OD 48/44 um, widths
  2/4 um, Qmin 11.69): with `steps=1` 242 neighbours, 181 outside the table's
  domain, 61 kept, the first 8 clean through the generator and the DRC gate,
  predicted Qmin 12.1 to 12.5; with `steps=2` 3 124 neighbours, 508 kept,
  predicted Qmin up to 12.8, two of the eight on a combination the table does
  not hold. The 8 EMX runs (the part's own rows peak at 4.6 GB) have not been
  run: that is the user's call.
- **The direct likelihood against sklearn's fit**: its value is sklearn's to
  the last bit at every point sklearn's optimizer visits; on 34 recorded
  `metric_gp` runs (1 131 fits of up to 92 points) 82 % of the hyperparameter
  vectors agree to 1e-8 in log and 99.3 % to 1e-4, one fit reached a higher
  local optimum, and all 249 proposals are the same; on synthetic histories
  of 100 to 400 points 2 of 28 proposals differ (at 300 points two points of
  a batch swap places, at 350 three of ten are others), because L-BFGS-B
  amplifies the gradient's rounding -- sklearn's own fit changes one of the
  same 28 proposals when the BLAS thread count goes from one to two. CPU: 4
  times less on 300 points and 8 variables, 3.3 to 5.9 times less for a
  proposal's fits at 400 points. Hence the switch, off by default.
- **The chamfer rule on the finding's geometry** (demo profile, tapped
  `xfm_bs`, chamfer 2 um): primary widths 6.85 and 7 um used to fail the DRC
  gate and 7.05 to 12 um were refused at layout; all build now with the two
  facing chamfers shortened (1.95 um at width 7, 1.2 um at 10) and a 0.1 um
  flat at the ring, every other width byte for byte.

## Known limits

- `lib_refine` has run with the fake EMX and, in plan mode, on a real run;
  its real EMX run and the continuation of the run on the grown table have not
  been made. An adopted row helps only on a combination of the run's grid the
  run can still propose; a run that has evaluated that combination never
  proposes it again. The first call on a table fits its models (minutes per
  column), in plan mode too.
- With the device rule on, OpenBox and TuRBO take a device-stopped point as a
  failed trial; the effect on an EMX run (`strategy=auto` with EM devices)
  has not been measured.
- Proposals are reproducible to the optimizer's tolerance only: beyond a few
  hundred points the direct likelihood, and sklearn's own fit across BLAS
  thread counts, may propose a different batch.
- On an `xfm_ms` table built with `auto`, the fixture sits exactly on the
  secondary's highest allowed tap metal, so a tapped secondary goes one level
  lower and makes the twin's fixture `shared`; both taps on an `xfm_ms` need
  an odd secondary turn count (the tap leads would otherwise overlap at the
  ring).
