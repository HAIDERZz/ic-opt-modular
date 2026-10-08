# N-100 — the OCEAN waveform export: a real CSV at full precision, fast for long records; operating points read without probing every instance

Status: specification (2026-10-09), for the coding subagent. Source: the bug report of the THz DPA project
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/THz_TX_Dual_band_DPA/bug_report_20261009/IC_OPT_BUG_REPORT_CN.md`, read-only,
items B02, B03, B04, with `transient_findings.md`, `ac_optimization_findings.md` and `reproduce_transient_export_precision.py`
beside it), verified on main 925cf72: the waveform files are whitespace tables with a two-line header, six significant
digits (`2.00000e+10  1.03014e-02`), named `.csv`; the operating-point reader probes `pv`, `OP(inst)` and `OP("/"+inst)`
on every instance `outputs()` lists, which on a netlist with thousands of PDK-internal diodes writes thousands of
OCN-6043 warnings. The user's decision (2026-10-09): fix all three.

Read before writing: `src/ic_opt/sim/ocean.py` (whole: `WaveformExport`, the script generator, the `ocnPrint` export,
`OP_QUANTITIES`, `icoptOpValue`, the `outputs()` loop, how `oppoints.tsv` is read back), `src/ic_opt/stages/spectre_chain.py`
(`Extract`: how scalars, waveforms and operating points are read into `ChildResult`; every reader of a waveform file in
`src/` -- grep `waveform`), `src/ic_opt/spec.py` (`waveforms` in the spec, if declared there), `src/ic_opt/digest.py` /
`blocks/analyze.py` (any use of waveform files), `tests/ic_opt/test_ocean*.py`, `test_em_measure.py` if it touches
OCEAN, the fake OCEAN of the tests, `docs/` pages that describe waveform export (grep), `README.md`. For the real
checks (section 4): the Cadence environment `/home/zzchen/Agent_virtuoso/cadence_ic231_env.csh` (`ocean -nograph`), and
these existing PSF results (read-only; copy nothing into them):
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/THz_TX_Dual_band_DPA/simulations/ideal_omn_20261008/calibration/hb_convention_run/psf`
(a transient result `tran.tran.tran`) and
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/THz_TX_Dual_band_DPA/simulations/ideal_omn_20261008/diagnostics/attempt1_interrupted/obs_0001/b36_I_only/nominal/psf`
(a `dcOpInfo` result with 1 008 MOS and about 2 000 PDK-internal diodes; its `metrics/ocean.log` beside it holds the
4 045 OCN-6043 lines of the old reader). The project's own exporter
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/THz_TX_Dual_band_DPA/transient_6tap_20261009/export_transient.py` (read-only)
shows the `drGetWaveformXVec` / `drGetWaveformYVec` + `%.16g` route that worked there.

## 1. The waveform file (B02)

A requested waveform is written as a real CSV: comma separated, no padding, one header row, then one row per sample.
Columns: a real waveform `x,y`; a complex one `x,re,im`; the header names the x quantity and unit as OCEAN reports
them when it can (`time_s`, `freq_Hz`), else `x`. Values `%.16g` (sixteen significant digits: a time axis of 0.4 ps
steps over 300 ns round-trips exactly). A waveform family or a sweep: one file per member, `<name>__<index>.csv`, with
an index file `<name>.families.json` naming the sweep values; nil: no file and the child's issue as today. A
`<name>.meta.json` beside the file: columns, units, the expression, the result selected, the point count, the precision.
`WaveformExport` gains nothing the user must set; the format is the contract (document it in `docs/`).

## 2. Long records (B03)

The export does not go through `ocnPrint` (its own warning PRINT-1048 says it slows past 10 000 points; four pilot
records of 102 401 points cost about 300 s of OCEAN each). Write the vectors directly: `drGetWaveformXVec` /
`drGetWaveformYVec` (or the equivalent that `export_transient.py` uses), `drGetElem` over the length, `fprintf` with
`%.16g`, complex values through `real()` / `imag()`. Measure on the transient PSF above: the old script's export and the
new one of the same signal (say what `ocean` reported, start to exit, for each; two runs each), and record the numbers;
do not state a speed-up you did not measure. If the direct route is not faster on that PSF, say so and keep the fastest
correct one. The trace / log lines of the child say how long the export took, separate from the metrics.

## 3. Operating points (B04)

The reader does not probe quantities on instances that have none. Find the way OCEAN lets a script list the parameters
of an instance's operating-point result without a warning (candidates: `outputs(?result ...)` with an instance,
`ocnGetParamNames`, the instance's `type` / `model` entry of `dcOpInfo`, or reading the result's parameter names once
and keeping only instances that carry `gm`); try them on the `dcOpInfo` PSF above and keep what works. Acceptance on
that PSF: the `oppoints.tsv` holds the same 1 008 instances x 12 quantities with the same values as the old reader's
(compare against a run of the old script on the same PSF), and the `ocean.log` holds no OCN-6043 for the diodes.
Genuine problems stay visible: an instance that has `gm` but lacks another quantity still gets its warning; a result
that cannot be selected still fails as today. No global suppression of OCEAN warnings. The `with` / `without` leading
`/` handling and the two result names (`dcOpInfo`, `dcOpInfo-info`) keep working.

## 4. Tests and real checks

Fakes: `tests/ic_opt/test_ocean*.py` -- the generated script's export block (the CSV contract, the family case, nil),
the Extract reader on CSV fixtures (real, complex, family), the operating-point reader on an `oppoints.tsv` fixture
unchanged. Real (OCEAN only, no Spectre; read-only on the PSFs named above; outputs under your scratchpad): (a) the
new export of the transient signal: the CSV reads with `csv.reader` into the declared columns, times strictly
increasing, 102 401 rows, last time equal to the PSF's, values equal to `drGetWaveformYVec` to 16 digits; (b) the
timing of section 2; (c) the operating-point comparison of section 3 with the warning count before and after. Put the
scripts, logs and numbers into the record. `ruff check src tests` clean.

## 5. Documents

`docs/` page on waveform export (or the README section that holds it): the contract; `README.md`; append `## 6. Record`.

## 6. Working rules for the coder

Branch `n100-ocean-export` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n100-ocean-export` (from
main; `git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`
with `PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_ocean*.py`, `test_em_circuit.py`,
`test_engine.py`, `test_blocks.py`, `test_saturation_margin.py`, `test_cli_recipes.py` and `ruff check src tests`; all
clean before committing. OCEAN on existing PSFs is allowed (it runs in seconds); Spectre and EMX are not. Nothing under
`THz_TX_Dual_band_DPA`, `ic-opt-accept` or `ic-opt-library` written; nothing private in code, tests or docs (no instance
names, device names or numbers of the user's circuit beyond the counts above). Do not edit `docs/refactor/BACKLOG_CN.md`.
Private scratchpad subdirectory for helper files. Commit style of the repository, one commit per item is fine (B02+B03
together, B04 apart), trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not merge or push.

## 6. Record

Done 2026-10-09 on branch `n100-ocean-export` (from main eb32bc8, `git merge --ff-only main`: already up to date), two
commits, not merged, not pushed:

- `45a5802` fix(ocean): B02 + B03 -- the waveform file and its writer, the timing, the extract stage's reader,
  `docs/waveform_export.md`, README "Results", the skill's fix_run line, the fake OCEAN.
- `c77bb93` fix(ocean): B04 -- the operating points read only on the instances of a type that reports `gm`, the read
  timed; README's operating-points paragraph.

**What was done.** `sim/ocean.py`: the replay script defines a writer once (`icoptWriteWave`, `icoptExportWave`,
JSON helpers) and writes each export from its vectors (`drGetWaveformXVec` / `drGetWaveformYVec`, `drGetElem`,
`fprintf "%.16g"`, complex through `real()` / `imag()`): `<name>.csv` (`x,y` / `x,re,im`, x column `time_s`,
`freq_Hz`, else `x`) and, after it, `<name>.meta.json`; a family `<name>__<i>.csv` per member (nested sweeps flattened),
`<name>.families.json`, a meta file listing the members. nil: no file, issue as before; a non-waveform or an
unwritable type: no file and the outcome (`not_a_waveform:flonum`, `unsupported_x:...`). The expression runs inside
`errset` as a metric's. Each export is timed with `measureTime` (wall clock): a line in `ocean.log`, a row in
`metrics/ocean_timing.tsv`, read by the OCEAN stage into the child's trace (`ocean:waveform:<name>`). The extract stage
reads every export back (`read_waveform`: `csv.reader`, header, field count, numbers, point count, a family's index);
a mismatch is `waveform X unreadable: ...`. B04: `icoptOpInstances()` takes `dataTypes()`, keeps the types whose
`outputParams(type)` hold `gm`, takes `outputs(?type type)`, filters `outputs()` by them (same order); a result whose
types cannot be listed is probed as before with a log line; the lookup of the result and the pv / OP chain unchanged;
rows collected with `cons` and reversed once (the old `append` to a growing list was quadratic); the read timed
(`ocean:oppoints`). A script with neither waveforms nor operating points is byte for byte what it was.

**Tests.** The specification's suites -- `tests/ic_opt/test_ocean*.py` matched no file on main; the new
`test_ocean_export.py` is that file now -- plus `test_em_circuit`, `test_engine`, `test_blocks`,
`test_saturation_margin`, `test_cli_recipes`: 109 passed, 1 skipped before; 122 passed, 1 skipped after `45a5802`;
123 passed, 1 skipped after `c77bb93`. With the other OCEAN suites run too (`test_netlist_and_ocean`,
`test_operating_points`, `test_em_measure`): 165 passed, 2 skipped before, 179 passed, 2 skipped after. `ruff check src
tests` clean. `test_cli_recipes`' fix_run test and `test_netlist_and_ocean`'s script test now check the new files.

**Real checks** (OCEAN `IC23.1-64b.ISR14.32`, `ocean -nograph -replay`, no Spectre, no EMX). Scripts and runs under
the private scratchpad `/tmp/claude-1011/-home-zzchen-Agent-virtuoso-EDA-AI-AGENT/55cb7f93-9395-4aa8-a70d-81df0065845b/scratchpad/n100/real/`:
`harness.py` (a fresh directory per run with `psf` a symlink to the PSF, OCEAN's own start and exit read from its log;
a run whose license checkout failed is rerun in a new directory), `op_old.py`, `wave_check.py`, `op_check.py`,
`smoke_new.py`; numbers in `results_op_old.json`, `results_wave_r2.json`, `results_op_r2.json`; every run's
`metrics/ocean.log`, `probe.ocn` and files under `runs/`. The old script is main's `ocean.py` (sha256 `755fac64...`, the
bug report's) copied to `baseline_src/`. Both PSF directories were unchanged afterwards (no file newer than this
specification under `simulations/`, `bug_report_20261009/`, `transient_6tap_20261009/`).

(a) The transient PSF's record holds **2 501** points, not 102 401 (see below). Checked: the PSF's own record of a node
voltage (`getData("<node>" ?result "tran")`) and the same signal resampled over the PSF's whole span to 102 401 points
(`sample(w x0 xN "linear" (xN - x0) / 102400)`, x0 / xN the first and last element of its x vector). Reference: the
vectors of the same expression dumped at `%.17g`. Both exports, both runs: `csv.reader` gives `time_s,y` (the meta
file's columns) and 2 fields on every row; 2 501 / **102 401** rows (the meta file's `points`); times strictly
increasing; the last time `5.000000000000044e-09`, equal to the PSF's last time; every time and value equal to the
vectors to 16 significant digits (largest relative difference 4.8e-16 for times, 5.5e-16 for values). Bit-exact
round-trip: 1 368 of 2 501 times and 2 092 of 2 501 values; 56 649 of 102 401 times and 85 211 of 102 401 values (see
below). The old script's file of the same signals: 3 header lines, `0.00000e+00       0.00000e+00`, one field per row
for `csv.reader`. Smoke runs (`smoke_new.py`) also wrote, on the AC result of the second PSF, a complex waveform
(`freq_Hz,re,im`, 551 rows), a one-member string-swept family, on the transient a two-member family built with
`famCreateFamily` (`v__0.csv`, `v__1.csv`, index with the sweep values), a scalar (`not_a_waveform:flonum`, no file) and a
nil (no file); `read_waveform` read every written export.

(b) OCEAN start to exit (its log's `Program start time` to `Memory report: on exit`), one export per script, no metric:

| script | run 1 | run 2 | export inside OCEAN (`measureTime`) |
|---|---:|---:|---:|
| only `openResults` (start-up) | 2.558 s | 2.556 s | -- |
| old, 2 501 points (ocnPrint) | 2.735 s | 2.876 s | not measurable |
| new, 2 501 points | 2.564 s | 2.606 s | 0.004 s, 0.004 s |
| old, 102 401 points (ocnPrint, PRINT-1048 once) | 156.791 s | 157.733 s | not measurable |
| new, 102 401 points | 2.717 s | 2.712 s | 0.160 s, 0.158 s |

An earlier set (`runs/wave_r1`, stopped by a license failure of one new run, `ELI-00133`, not a script error) had the
old 102 401-point script at 159.191 s and the 2 501-point ones at 2.904 s (old) and 2.571 s (new). The direct route is
faster on this PSF: on the long record about 157 s against 2.7 s start to exit, of which 2.56 s is start-up.

(c) The `dcOpInfo` result (5 053 outputs: `dataTypes()` = vsource 7, mutual_inductor 2, inductor 4, diode 4 032,
`bsim4~instparams` 1 008; only the last lists `gm`):

| reader | OCN-6043 | start to exit | `ocean.log` | `oppoints.tsv` |
|---|---:|---:|---:|---|
| old, run 1 / 2 | 4 045 / 4 045 | 43.765 s / 43.427 s | 1 676 430 B, 17 396 lines | 1 008 instances x 12, identical to the one in the PSF's `metrics/` |
| new, run 1 / 2 | 0 / 0 | 19.909 s / 19.941 s | 8 760 B, 241 lines | byte for byte the old one's (12 096 values) |

The read itself took 17.34 s / 17.37 s inside OCEAN (`ic-opt operating points: 1008 instance(s) read, 1008 with gm`).
The 4 045 warnings are one per non-transistor output (4 032 + 7 + 4 + 2): it is `pv` that warns (OCN-6043) on a
missing quantity, `OP` returned nil silently here. How much of the 24 s saved is the probing left out and how much the
`cons` in place of `append` was not separated (no run with only one of the two changes).

**Deviations.**
- (a) and (b) on 102 401 points use a resample of the PSF's signal: the named transient result holds 2 501 points.
  The resample is OCEAN's own waveform from the PSF, over its whole span; the long-record numbers are about the
  export of 102 401 points, not about a 102 401-point simulation.
- The `<name>-info` result form could not be checked on real data: a view of the PSF whose `logFile` names the result
  `icoptOpInfo-info` (`runs/op_r2/*_info_view`) still listed `dcOpInfo` in `results()` (OCEAN takes the name from
  the data, not only from `logFile`), so both readers found no result and wrote an empty file (old and new alike, no
  warning: "a result that cannot be selected" behaves as before). The lookup code is unchanged; the fakes keep testing
  the name. The leading-`/` handling is unchanged too; this PSF's instance names have none.
- The B04 commit also replaces the quadratic `append` with `cons` + one `reverse` (same file), which the specification
  did not ask for.
- The time of the metrics is not measured inside OCEAN: wrapping each metric's line would change the line the parser
  sees (an unbalanced expression is skipped per line today). The trace has the OCEAN run (`ocean#<n>`), each export and
  the operating-point read; metrics and start-up are the rest.
- The trace records exist only in the stage context (the engine keeps no trace today); the persistent record of the
  timing is `metrics/ocean.log` and `metrics/ocean_timing.tsv` in the child's directory.

**What the specification did not foresee.**
- `%.16g` is equal to the vectors to 16 digits, but does not give back every double: 45 % of the 102 401 resampled
  times and 17 % of the values differ in the last bit after the round trip (17 digits, `%.17g`, would give every
  double back). The times stay strictly increasing here; whether "round-trips exactly" should mean bit for bit is for
  the user (changing `WAVEFORM_PRECISION` and the writer's three format strings would be all).
- A waveform expression that returns a number used to give a file (ocnPrint printed PRINT-1067 and nothing useful);
  it now gives no file and an issue `not written: not_a_waveform:<type>`, so such a child is `metric_failed`.
- OCEAN's `xmin` / `xmax` are the x of the minimum / maximum y, not the ends of the axis (the first resample tried
  with them ran backwards); the real check uses the x vector's first and last elements.
- Two of the 36 OCEAN starts in this work failed to check out `Virtuoso_Adv_Node_Framework` (ELI-00133 / LMF-02002,
  LMF-02005) and succeeded when run again; the OCEAN stage's own three attempts cover that.
- `outputs(?type "diode")` lists 4 032 diodes; the bug report counted 2 016 by two name suffixes.
- `ocnGetInstancesModelName()` unselects the current result (OCN-6038, then OCN-6034); not used.
