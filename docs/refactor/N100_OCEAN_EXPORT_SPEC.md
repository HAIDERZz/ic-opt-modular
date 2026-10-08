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
