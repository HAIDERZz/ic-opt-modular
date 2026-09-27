# IC-Opt 0.4.0

0.3.0 was tested on the machine it was written on. Since then the same
checkout was installed on a Windows laptop six times by an agent that had the
repository's documents and a task, nothing else, and drove real Spectre and
EMX runs on a Linux host through SSH: from a two-point remote check to an
80-point search over a mixer's circuit parameters and its transformer's
geometry together, on three testbenches and three corners. This release is
what those runs found and what was fixed, plus the second milestone of the
device query library.

Stores written by 0.3.0 are reused as they are. There is no migration step.

## Changes in behaviour

Read these before upgrading a project that runs.

1. **A metric that yields no number no longer fails the point's stage.** A
   point whose OCEAN expression returns nil, a waveform or a non-finite value
   is `metric_failed` and keeps the metrics that did extract; the status
   `failed:extract` is gone. The scalar file says which it was:
   `no_value:nil` (the call found nothing on this point, so it comes and goes
   with the design point) or `non_scalar:<SKILL type>` (every point fails).
   Such a point has no objective, the optimizer scores it with the failure
   penalty, and `sim.evaluate` prints per batch which metrics failed and on
   how many points.
2. **`+lqtimeout` is passed only when the spec says so.** 0.3.0 always passed
   `+lqtimeout 900`. `simulator.license_queue_timeout_s` is optional; left out,
   Spectre waits for a license as it does by itself. `ic-opt migrate` writes
   900 into a converted 0.1 project, as that version ran.
3. **The OpenBox strategies size their initial design by the budget.** The
   default is `max(1, min(2 x variables, budget // 2))`; an explicit
   `initial_trials` still wins. A run with 0.3.0's seed therefore proposes
   other points. `--plan` prints how many points the surrogate proposes, and
   an observation's origin ends in `:init` or `:acq`.
4. **Beside testbenches, a variable without a device prefix is a circuit
   variable.** In a spec of one device, 0.3.0 took every unprefixed variable
   for a generator field, which left the netlists without their parameters
   once the spec also had testbenches. The unprefixed form now holds only for
   a spec of one device and no testbench; elsewhere name device variables
   `<device>.<field>` or list them under the device's `variables`.
5. **`steps.jsonl`.** `seconds` is the points' own durations added up, as
   before; `wall_seconds` is new and is what the batch took on the clock.
6. **Windows installs keep torch below 2.9** (the `turbo` extra): OpenBox's
   scikit-learn pin loads an older `msvcp140.dll` that torch 2.9 cannot
   initialise on.

## New

- **Circuit and device in one search.** A binding's `terminals` may hold
  `null` where the export's nport instance has a terminal the device has no
  port for (a tap wired to two terminals, a grounded one); the instance is
  rewritten to the kept terminals. `em.parallel_jobs` caps the EMX runs at
  once below the Spectre workers. Points of one batch that share a geometry
  run EMX once: the later ones wait for the first one's result. The plan line
  says so, and a run uses at most the planned simulations.
- **Corner `options`** set values on the netlist's `simulatorOptions`
  statement. An ADE export writes the simulation temperature there as a
  literal that the declared `temperature` parameter never drives, so a corner
  could change the model section but not the temperature; `--plan` warns when
  a corner sets a parameter the export never uses.
- **`--plan` checks every export** for every corner without touching the
  store: a variable an export lacks is a `FAIL` line before anything runs.
- **Reports.** A summary (feasible count, best point, binding constraints,
  worst corner), values with their units, constraints as `BW > 26 GHz`, the
  best observation per corner as a table, figures under the sections they
  illustrate, tables that scroll at phone width. Constraint margins follow
  the corner policy (each point's worst scored corner under `all_corners`). A
  device quantity is ranked among its own device's variables.
- **Device query library, second milestone** (`docs/em/library.md`).
  `lib.densify` proposes where to simulate next by model uncertainty. A curve
  quantity can be modelled as a composition (`model: ratio` on its
  low-frequency or peak value, `model: resonance` on the resonance factor)
  instead of directly. A confidence ceiling per quantity (`rel_sigma_max`),
  `relax=` for `lib.region`'s coarse pass, a cache directory of the caller's
  choice with a fallback for a library that cannot be written, and
  `lib.coverage` / `lib.load` say what the cache already holds, so the wait
  of a first query is known before asking.
- **Process profiles.** The manufacturing grid is a profile field
  (`layout_rules.manufacturing_grid_um`, optional); generators declare their
  conductors to the DRC gate, plugin generators included; the via-enclosure
  audit and the configuration's metal checks follow the profile's stack;
  `em.validate_profile` reads `proc=` on the `--ssh-profile` host.
- **Measurement.** A curve is interpolated between its samples on any sweep
  and refuses a frequency outside it; a four-port device measured with
  `k_lf < 0` carries a warning about its winding direction.
- **Skills.** `skills/author-spec` turns a design request into a `spec.yaml`:
  the shape to pick, where each fact comes from, every section's rules, the
  OCEAN forms that yield a number on every point, and four complete examples
  that a test keeps valid (circuit, EM device in a circuit, circuit and
  device together, device only). `skills/ic-opt` was rewritten from the
  process logs of the agents that used it.

## Fixed

- A command past its deadline is ended with its whole process group, on the
  remote host too, and fails its point instead of the run. After Ctrl-C the
  run starts nothing more and records no interrupted point.
- Windows controllers: Maestro export trees are handled through
  extended-length paths (an export carries a file name that ends in a dot),
  also in `--plan`; output is UTF-8 whatever the console code page; output is
  line-buffered, so a log the run is redirected to shows the per-batch lines
  while the run goes.
- A spec of devices without testbenches runs through `opt.optimize`.
- The report's corner section scores device metrics for every corner; the
  parameter-importance hint names the `report` extra.
- A note in `library.yaml` no longer changes a dataset's cache key.
- `examples/spec.yaml`: the bandwidth and the compression point are written
  so that every point yields a number; the P1dB export is named as the cells
  name it.

## Verified

- Tests at the tag: see the numbers at the end of this file. `ruff check`
  clean.
- **Compatibility with 0.3.0**, by running both versions' code on the same
  specs: the spec fingerprint and the pipeline fingerprint of the Spectre
  chain, the device-only chain and the device-in-circuit chain are identical
  (five specs, three of them real projects, one recorded by the 0.3.0 release
  itself). The pcell geometry version is unchanged. A real device library
  (six tables, eight run stores, 9,250 rows) loads with every row.
- **A Windows controller, six rounds on real simulators**, each installed
  from the checkout by an agent: remote check (results identical to the
  host's own, digit for digit); `coarse_to_fine` with OpenBox and TuRBO and
  the EM chain with two transformers (GDS identical byte for byte, EMX cache
  hit on every point); library queries, multi-corner `fix_run` with EM and
  the report; 50 and 80 points on three testbenches and three corners, the
  second with real corner temperatures; 80 points of circuit parameters and a
  tapped transformer's geometry together (861 simulations, 61 EMX runs for 55
  geometries, 48 feasible points). The last round found no defect particular
  to Windows.
- **Device library**: three real densification rounds on the single-turn
  transformer tables, 260 EMX runs, all successful: 230 designs adopted, 30
  kept as independent test designs. On the test designs the median error of
  the resonance frequency fell from about 9 % to about 2 % and that of the
  low-frequency coupling from 4-7 % to about 1 %; the quality-factor curves
  near resonance did not improve by more rows alone and are now modelled as
  their peak times a ratio.
- **No private process facts** in the repository's changes since 0.3.0, the
  wheel or the sdist (the same scan as
  `docs/refactor/N28_DESENSITISATION_AUDIT_2026-09-25_CN.md`, repeated for
  this release; one invented test value that coincided with a real one was
  replaced).

## Not verified on a real machine

Tests cover these; no real run has.

- The recipes `signoff` and `lib_design`; the strategies `random`, `sobol`,
  `latin_hypercube`; `points.sobol`, `points.one_at_a_time`, `points.from`.
- Stopping a run (Ctrl-C and resuming; a controller that is killed), a
  command that reaches its timeout, a second run refused by the project lock.
- `fix_run` waveform export, corner `variables` and `model_file`, a custom
  `plugin:`, `keep_failed_runs: false`.
- A macOS controller.
- The fixes that came out of the last round (one EMX run per geometry inside
  a batch, line-buffered output, the unprefixed-variable rule, the ranking of
  device quantities, `wall_seconds`): the next real run with a device covers
  them.

## Known limits

- `ic_opt.em.touchstone.read` reads Touchstone 1 full matrices; a Touchstone 2
  file with a triangular matrix is not read.
- One `em` section serves every device of a spec.
- `opt.optimize` starts from a space-filling design and does not evaluate the
  design as exported; a point that already fails a device constraint still
  runs its circuit simulations. Both belong to the optimizer work that
  follows this release.
- A tapped transformer with chamfered ground stubs and a wide primary can
  close the ground opening between two stubs into an acute corner; the DRC
  gate stops such a point (`failed:pcell`). `stub_chamfer_um: 0` avoids it.

## Compatibility

- Stores written by 0.3.0: reused as they are. Stores written by 0.2.0 or
  earlier: `ic-opt migrate-store`, as 0.3.0 described.
- `spec.yaml`: every 0.3.0 spec validates, except the case of change 4 above,
  which `--plan` reports as a variable the export lacks. New optional keys:
  a corner's `options`, `em.parallel_jobs`, `null` in a binding's
  `terminals`, `simulator.license_queue_timeout_s`.
- `library.yaml`: schema `ic-opt-library-v1` unchanged. New optional keys per
  quantity: `model`, `feature_map`, `rel_sigma_max`. Changing `model` or
  `feature_map` is a new definition of the table: its models are fitted
  again.
- Process profiles: `layout_rules.manufacturing_grid_um` is optional.

## Test numbers at the tag

- The suite outside `tests/ic_opt/pcell`: 452 passed, 12 skipped.
- The pcell suite: 975 passed, 1 skipped with a private process profile
  (`IC_OPT_PROFILE_DIRS`); 341 passed, 635 skipped without one, which is what
  a checkout alone runs.
- `ruff check`: clean.
- `scripts/check_clean_install.sh`: PASS (two throwaway environments, the
  packaging, library and blocks tests inside each).
