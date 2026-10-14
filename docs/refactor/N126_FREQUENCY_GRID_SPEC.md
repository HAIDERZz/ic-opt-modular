# N-126: a frequency grid declared in the manifest, and `lib.prefit` to fit it ahead (2026-10-14)

A curve answers at any frequency inside the parts' sweeps: at a frequency that is no anchor, an extension column is measured
from the rows (seconds) and its model fitted on first use -- N-122 measured an hour for the two large transformer tables at
ten frequencies. To answer any frequency at once, the models have to cover the frequency axis ahead. How dense
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n122_random_requirements/freq_grid/RESULT_CN.md`, measured on the
library's own rows, no model, no EMX): interpolating each row's measured curve between grid frequencies with PCHIP, a grid of
about 67 frequencies -- below 50 GHz each about 10 % above the previous, from 50 GHz every 5 GHz -- keeps the p99 error at or
below 1.7 % for every table and curve (median 0.13 %); a uniform 5 GHz grid misses the large multi-turn devices (p99 9-24 %), a
uniform 2 GHz one (125 frequencies) is not better. User decision 2026-10-14 ("按这三步执行"): step 2 of 3 (N-125 makes each
fit cheaper; N-127 answers between the grid's frequencies).

## 1. What changes

- **Manifest**: an optional library-level entry (absent: no grid, today's behaviour)

  ```yaml
  frequency_grid: {from_ghz: 1, ratio: 1.1, until_ghz: 50, step_ghz: 5}
  ```

  Its frequencies, in whole GHz: the distinct values round(from x ratio^n) below `until` (n = 0, 1, ...), then `until`,
  `until + step`, ... -- each inside a part's sweep. For a 0-250 GHz sweep: 1-14, 16, 17, 19, 21, 23, 26, 28, 31, 34, 37, 41,
  45 and 50, 55, ..., 250 (67). Validate: ratio > 1, step > 0, 0 < from < until. Rerun
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n122_random_requirements/freq_grid/interp_density.py` with this list
  (a copy in your scratch directory; it reads the library only) and put its p99 per table and curve in the record; stop and
  report if any exceeds 2 %.
- The entry is library-level: it is in no stratum's dump, so no dataset key and no cache file name changes (test it). An
  install of 0.9.0 refuses a manifest that has it (extra fields are forbidden): say so in the documents.
- **`Library.knots(stratum, curve)`**: the frequencies (GHz) at which the curve's column has or will have a model -- the curve's
  anchors and the grid's frequencies inside the stratum's sweeps, less those where the column can have none (no row with a
  value there, or `no_model`); from the extension measurement (`anchors`, cached, seconds per frequency).
- **A new block `lib.prefit`** (`ic-opt call lib.prefit <root> [strata=a,b] [curves=Lp,Qp] [threads=] [workers=]
  [cache_dir=]`): measures and fits every knot column not cached yet, stratum after stratum, each stratum with the whole limits
  entry (as `lib_signoff` fits); prints per stratum the columns fitted, already cached and skipped (with why) and the minutes;
  resumable (what is cached is not fitted again; an interrupted run continues). Its answer per stratum: knots per curve, fitted,
  cached, skipped, seconds.
- **`lib.cached` / `lib.coverage`** report per curve how many of its knots are cached (`grid n/m`).
- Do not change `gp.py` / `composed.py`, nor how a column is fitted or calibrated (N-125 changes the calibration of composed
  curves at the same time).

## 2. Tests (fakes, no EMX)

The grid rule (the 67 frequencies for 0-250 GHz, clipped to a part's sweep, the validation); with and without the entry the same
dataset keys and cache file names; `knots` (anchors and grid, less columns without rows or with `no_model`); `lib.prefit` on a
small fake library with a grid of a few frequencies (fits the missing knot columns, a second run fits nothing, skips with the
reason, honours `strata=` and `curves=`); `cached` / `coverage` counts.

## 3. Real check (no EMX; the real library is not written to)

Make a scratch library root under your scratch directory: a copy of
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2/library.yaml` with the entry above, and a symbolic link per part
directory (never write through them). Its `.cache` is its own and empty, so everything is fitted there: run `lib.prefit` for
`ind_sym_m10_hf` (114 rows, direct models) over the whole grid and report the time and the counts; print `knots` for every
stratum (measuring is reading only). At most 16 threads; read the machine's busy logical CPUs from `/proc/stat` before (another
user's Spectre batches take up to about 130 of 256; above 150, wait).

## 4. Documents

`docs/em/library.md` (section 2 the entry, "Asking at any frequency", Compute with the real check's cost, Cache), the ic-opt
skill.

## 5. Working rules for the coder

Branch `n126-frequency-grid` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n126-frequency-grid` (from `main`; `git
merge --ff-only main` first). N-125 is developed at the same time (the calibration path of composed curves in `query.py`): keep
clear of it. `PYTHONPATH=<worktree>/src` with `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never
`uv run` or `uv sync`; `git -C <worktree>` and absolute paths. Targeted tests plus `ruff check src tests`; pytest with
`OMP_NUM_THREADS=2`, no `-n`. Scratch files only under `<session scratchpad>/n126/`; one line to `<session
scratchpad>/n126/progress.log` at every milestone. One commit for the change with tests and documents, one for the `## Record`
section. No merge, no push, no edit of `docs/refactor/BACKLOG_CN.md` or of the real `library.yaml`. Messages in the repository's
style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

**Branch and base.** `n126-frequency-grid` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n126-frequency-grid`, from
`main` at 25cc40d (`git merge --ff-only main`: already up to date). The feature with its tests and documents: 2a2deef; this
record: the commit after it. `gp.py` and `composed.py` are unchanged (`git diff 25cc40d 2a2deef --
src/ic_opt/library/gp.py src/ic_opt/library/composed.py` is empty), and nothing in the calibration path N-125 changes
(`models`, `_fit_direct`, `_fit_composed`, `_composed_*`, the fold workers) was touched. Nothing under the real library was
written (below). Evidence -- scripts, outputs, logs -- in the session scratchpad
`/tmp/claude-1011/-home-zzchen-Agent-virtuoso-EDA-AI-AGENT/22a251ad-03d3-4a55-8437-b0a4e97a3152/scratchpad/n126/`
(`interp/`, `real/`, `tests/`, `progress.log`).

### Files and why

- `src/ic_opt/library/manifest.py`: `FrequencyGrid` (`from_ghz` > 0, `ratio` > 1, `until_ghz` > 0, `step_ghz` > 0, all
  finite, `from_ghz` < `until_ghz`, extra fields refused; `frequencies(sweeps)` gives the rule's frequencies inside any of
  the parts' sweeps) and `Library.frequency_grid` (optional, None by default), a library-level field: no stratum's dump
  holds it, so `dataset._cache_key` and every name built on the dataset key are what they were.
- `src/ic_opt/library/dataset.py`: `anchors_at` measures several frequencies in one pass over the rows' sNp files and writes
  each frequency's own `anchors-<stratum>-<key>-<f>.json`, exactly as `anchors` writes it (byte for byte, tested), holding
  every such file's lock meanwhile (taken in name order; the files re-read once held); `_anchor_passes` is the shared body
  -- `anchors` itself computes through it, unchanged in what it writes and how it locks; `_anchors_valid`.
- `src/ic_opt/library/query.py`: `Library.grid(stratum)` (the grid inside the stratum's sweeps), `Library.knots(stratum,
  curve)`, `_knot_candidates`, `_extend_at` (the grid's extension columns into the dataset's rows, the frequencies not
  measured yet in one pass); `cached` adds `"grid <n>/<m>"` per declared curve when library.yaml has a grid; docstrings.
- `src/ic_opt/library/prefit.py` (new): `prefit` -- the strata and curves checked, per stratum the knots, the candidates that
  are no knot with `no_model`'s reason, the knot columns cached (`_model_file`), the others to `Library.models` in calls of
  `ROUNDS` (2) x the worker count `fit_plan` gives the stratum, each call's models dropped from memory after it; stderr
  lines; the answer.
- `src/ic_opt/blocks/library.py`: the `lib.prefit` block (`strata`, `curves`, `threads`, `workers`, `cache_dir`; strict JSON)
  and `lib.coverage`'s help; `src/ic_opt/blocks/__init__.py`: registered.
- `docs/em/library.md`: section 2 (the entry in the example; "A frequency grid, fitted ahead: `lib.prefit`": the rule, its
  evidence, knots, the command, resumability, `grid <n>/<m>`, that 0.9.0 refuses the entry), Compute (this record's cost and
  N-122's for the large tables), Cache (`grid <n>/<m>`, the anchors files, the models' size); `skills/ic-opt/SKILL.md` (the
  block list, a cheatsheet line, a sentence in the library paragraph).
- Tests: `tests/ic_opt/test_library_grid.py` (new, 17 with the parametrized ones), `test_library_docs.py` (the example's
  grid gives the 67 frequencies; the text block is the same entry).

### The rule as built

Where the specification leaves a choice open:

- The low part is `round(from_ghz x ratio^n)` with Python's `round` (a tie to the even neighbour; {1, 1.1, 50, 5} has no
  tie), kept while below `until_ghz` (the values never decrease, so the first one at or above it ends the part); a rounded 0
  is no frequency and is left out (`from_ghz` below 0.5). The high part is `until_ghz + k x step_ghz` written with at most
  six significant digits, the precision of a column's name (`50 + 3 x 0.1` is 50.3). For {1, 1.1, 50, 5} over 0-250 GHz:
  1-14, 16, 17, 19, 21, 23, 26, 28, 31, 34, 37, 41, 45, 50, 55, ..., 250 -- 67, as the specification lists.
- "Inside a part's sweep": inside the first and last frequency of any part's `em.frequencies` (`dataset.sweeps`), with the
  tolerance `Library.anchors` refuses by. A stratum none of whose parts states its sweep has no grid frequency, and its knots
  are its anchors.
- Knots: the anchors and the grid's frequencies, less the columns `no_model` names -- which covers both of the
  specification's cases, a column without a row (`0 usable rows: too few for a model of this column`) and one with too few;
  `lib.prefit`'s `skipped` gives that reason.
- `cached`: the curve's entry is keyed by the curve's own name (`Lp`, never a declared column's name); n counts the knots
  whose model file exists (a query needs no fit there), not those with only a calibration. `lib.load` shows it too: it
  answers with the same `cached`.
- `lib.prefit`: strata in the order given, else `Library.strata()`'s; curves in `manifest.CURVES` order; a curve named that
  none of the strata declares is refused, one that some do not declare is done where it is declared. The knot columns go to
  `Library.models` two per worker per call, curve after curve: a call ends with its slowest fit, so an interruption loses at
  most one call's fits (with `main`'s `_fit_composed` a composed curve's calibration is written only when all the call's
  folds are done) and the parent holds at most one call's models (a large transformer table's are up to 39 MB each).
  `fitted` lists the knot columns; the models they need (the resonance's, a composed curve's parts) are fitted in the
  first call and named in `Library.models`' own `[lib] ... fitting` line. Without a grid it fits the anchors' columns and its
  `notes` say so.

### The interpolation density with the rule's frequencies

`interp_density.py` copied to `interp/`; its grids replaced by the rule's list
(`manifest.FrequencyGrid(from_ghz=1, ratio=1.1, until_ghz=50, step_ghz=5).frequencies([(0, 250e9)])`, asserted 67 long),
and -- a deviation, see below -- the library opened with `cache_dir=` so that it does not probe the real `.cache` with a
temporary file; it found every dataset there and wrote nothing. 16 tables (N-122's 12 and the four tapped ones), one
single-threaded process each, 14 at once, 21:59:35-21:59:47 (busy logical CPUs at 21:57: 0.5 of 256). The p99 of |PCHIP / measured
- 1| in % per table and curve, in brackets the earlier "hybrid" grid's where it differs (that grid's low part was x1.10 with
at least 1 GHz per step: 15, 18, 24, 29, 35, 38, 42, 46 where the rule has 14, 17, 23, 28, 34, 37, 41, 45):

| table | rows | Lp | Ls | Qp | Qs | k |
| --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap | 511 | 0.341 (0.351) | - | 0.101 (0.105) | - | - |
| ind_sym_ap_hf | 83 | 0.004 | - | 0.011 (0.013) | - | - |
| ind_sym_m10 | 527 | 0.350 (0.378) | - | 0.114 (0.142) | - | - |
| ind_sym_m10_hf | 114 | 0.005 (0.006) | - | 0.024 | - | - |
| xfm_bs_ap | 1576 | 0.208 (0.213) | 0.214 (0.218) | 0.127 (0.130) | 0.132 (0.146) | 0.020 (0.021) |
| xfm_bs_ap_hf | 635 | 0.015 | 0.015 | 0.021 (0.023) | 0.017 | 0.001 |
| xfm_bs_m10 | 1736 | 0.218 (0.200) | 0.219 (0.200) | 0.136 (0.146) | 0.123 (0.130) | 0.023 |
| xfm_bs_m10_hf | 635 | 0.018 | 0.018 | 0.021 | 0.019 (0.020) | 0.001 |
| xfm_ms_ap | 1400 | 0.233 (0.256) | 0.456 (0.538) | 0.583 (0.605) | 0.222 (0.293) | 0.113 (0.123) |
| xfm_ms_ap_hf | 225 | 0.034 | 0.055 | 0.163 | 0.182 | 0.010 |
| xfm_ms_m10 | 1400 | 0.248 (0.279) | 0.456 (0.540) | 0.474 (0.570) | 0.186 (0.219) | 0.107 (0.127) |
| xfm_ms_m10_hf | 225 | 0.158 | 0.254 | 1.168 | 1.675 | 0.081 |
| xfm_bs_ap_ct | 78 | 0.024 | 0.025 | 0.037 | 0.027 | 0.001 |
| xfm_bs_ap_ct_hf | 183 | 0.023 | 0.023 | 0.030 | 0.025 | 0.001 |
| xfm_bs_m10_ct | 47 | 0.027 | 0.027 | 0.031 | 0.028 | 0.002 |
| xfm_bs_m10_ct_hf | 200 | 0.024 | 0.024 | 0.027 | 0.025 | 0.001 |

None above 2 %: the worst is xfm_ms_m10_hf's Qs, 1.675 %, then its Qp, 1.168 %, then xfm_ms_ap's Qp, 0.583 %; half of
the 68 pairs at or below 0.03 %; the median over N-122's 48 pairs 0.119 % (the hybrid's 0.130 %). The tapped tables,
new to the study, stay at or below 0.037 %. The largest single-point errors are where a row's resonance limit comes
close (up to 49 % on xfm_bs_ap_hf Ls, 0.07 % of its points above 1 %): N-127's rule on the next knot above f matters there.

### Real check (no EMX; the real library not written to)

Scratch root `<scratchpad>/n126/lib`: n28_v2's `library.yaml` with `frequency_grid: {from_ghz: 1, ratio: 1.1, until_ghz: 50,
step_ghz: 5}` inserted after `process_profile` (`diff`: that one line), a symbolic link per part store (18), its own empty
`.cache`; `IC_OPT_PROFILE_DIRS` as given.

1. `ic-opt call lib.prefit <scratch root> strata=ind_sym_m10_hf threads=16 workers=8` (`OMP_NUM_THREADS=2`; busy logical
   CPUs 25.3 of 256 before, 40.7 in a 5 s sample during): from the empty cache in **30.2 s** wall, 22:19:22-22:19:52 (254.6 s
   user + 148.0 s system, 13.3 cores on average; 126 MB the largest resident set of any of its processes). The dataset built, the 62 grid frequencies
   that are no anchor measured in one pass, **136 knot columns (Lp 68, Qp 68: the 67 grid frequencies and the anchor at
   112 GHz) and SRF_p fitted, 0 cached, 0 skipped**, in 9 `Library.models` calls (16 columns each, the last 8). The cache then: 1 dataset, 62
   anchors files (0.7 MB), 137 calibrations, 137 models (9.1 MB, the largest 73 kB). A second run: 0 fitted, 136 cached,
   0.7 s. `lib.coverage` then: `"Lp": "grid 68/68"`, `"Qp": "grid 68/68"`; a query of Lp and Qp at 3, 23, 45 and 115 GHz at a
   point that is no row (30.5 / 2 / 2 um, 2 turns): eight `predicted` answers in 0.01 s, no fit.
2. `Library.knots` for every stratum and curve (`real/knots_all.py`: one process per stratum, one thread, 14 at once,
   22:20:3x-22:20:58, busy CPUs 3.0 before; each dataset built from scratch into the scratch cache, then the grid measured;
   no model or calibration file of any other stratum written):

   | stratum | rows | knots per curve | skipped: usable rows (count) | dataset + measuring, s |
   | --- | --- | --- | --- | --- |
   | ind_sym_ap | 511 | Lp 68, Qp 68 | none | 1.8 + 1.9 |
   | ind_sym_ap_hf | 83 | Lp 68, Qp 68 | none | 0.3 + 0.4 |
   | ind_sym_m10 | 527 | Lp 68, Qp 68 | none | 1.9 + 1.9 |
   | ind_sym_m10_hf | 114 | Lp 68, Qp 68 | none | cached |
   | xfm_bs_ap | 1576 | Lp, Qp, Ls, Qs, k 69 each | none | 8.9 + 11.3 |
   | xfm_bs_ap_hf | 635 | 69 each | none | 3.6 + 4.9 |
   | xfm_bs_m10 | 1736 | 69 each | none | 9.8 + 12.5 |
   | xfm_bs_m10_hf | 635 | 69 each | none | 3.6 + 4.9 |
   | xfm_ms_ap | 1400 | Lp, Ls, k 41; Qp, Qs 43 (top 112 / 120 GHz) | 0 (130), 1 (3), 2 (3) | 7.8 + 8.4 |
   | xfm_ms_ap_hf | 225 | 69 each | none | 1.3 + 1.7 |
   | xfm_ms_m10 | 1400 | Lp, Ls, k 40; Qp, Qs 43 (top 110 / 120 GHz) | 0 (130), 1 (3), 2 (6) | 7.8 + 8.4 |
   | xfm_ms_m10_hf | 225 | 69 each | none | 1.3 + 1.6 |
   | xfm_bs_ap_ct | 78 | 57 each (top 190 GHz) | 1 (60) | 0.7 + 0.8 |
   | xfm_bs_ap_ct_hf | 183 | 69 each | none | 1.5 + 1.9 |
   | xfm_bs_m10_ct | 47 | 55 each (top 180 GHz) | 1 (65), 2 (5) | 0.4 + 0.6 |
   | xfm_bs_m10_ct_hf | 200 | 69 each | none | 1.7 + 2.0 |

   4,279 knot columns in all. Inductor tables: the 67 grid frequencies and the anchor at 112 GHz; transformer tables: and the
   anchor at 40 GHz. The multi-turn secondaries resonate low, so above 110-120 GHz no row of xfm_ms_ap / xfm_ms_m10 has a
   value; the two small tapped tables answer at the top of the band with one or two rows, too few for their joint models.
3. Same keys on the real data: the 16 dataset files the scratch cache built have the real cache's names, so declaring the
   entry changes no dataset key. 10 are byte-identical; 6 differ in the last bits of measured values (relative <= 3e-12)
   and in `stored_match` flags: that is the BLAS thread count of the measuring process, not the entry -- ind_sym_ap_hf rebuilt
   in memory with `OMP_NUM_THREADS=1` gives the scratch file's rows, with 4, 8 or unset the real file's.
4. The real library: `find n28_v2 -newer <marker set at 21:57>` lists nothing; its `.cache` holds 4,649 files with mtime
   21:19:35 before and after, `library.yaml` 03:35.

### Tests

The new tests (fakes, no EMX; `big`: six two-turn inductors resonating at 16-20 GHz, `tiny`: six single-turn ones resonating
above the 60 GHz sweep, joint models, Lp / Qp anchored at 10 GHz, the grid {2, 2, 16, 22} giving 2, 4, 8, 16, 38, 60 GHz):
the rule (the 67 frequencies, clipped to one or two sweeps, none without a sweep, the zero dropped, the six digits); the
entry's checks (six refusals); with and without the entry the same dataset keys (main's constants for the inductor and the
transformer fixtures, a composed table's key and calibration name) and the same cache files, JSON byte for byte, after a
declared column's, an extension column's and their resonance's fits; `anchors_at` (one pass for three frequencies, the
files byte-identical to `anchors`', read back without measuring, a mixed request measuring only the new one, nothing
written without a cache); two spawned processes asking for the knots at once measure once; `knots` (anchors and grid,
16 GHz's one row and 38 / 60 GHz's none left out with their reasons, one pass, nothing fitted, refusals, without a grid the
anchors); `cached` / `lib.coverage` / `lib.load` counts before and after fits, without a grid as before; `lib.prefit`
(`strata=` / `curves=` honoured, the resonance fitted with the first call, skipped with why, the stderr lines, a second run
fits nothing), an interrupted prefit continuing with the columns not cached, the refusals and the no-grid note, spawned
workers with two calls of four and three columns and no knot model left in memory, the block on the command line (and a
`workers=` above the limit refused with exit 2). `OMP_NUM_THREADS=2`, no `-n`: the 25 `test_library*.py` files,
`test_lib_refine.py`, `test_lib_tap.py`, `test_spec_library.py` and `test_cli_recipes.py` -- 412 passed, 6 skipped in 510 s
(25cc40d collects 401 in the same files: the 17 new ones are `test_library_grid.py`'s); `test_library_docs.py` with the
grid assertions 4 passed; `ruff check src tests` clean.

### Deviations and doubts

1. The density script: besides its grid list, `cache_dir=` (a `Library` opened on the real root probes its `.cache` with a
   temporary file, `cache.writable`, which would be a write) and the import for `FrequencyGrid`; run on 16 tables, not only
   N-122's 12.
2. `lib.load` reports `grid <n>/<m>` too (it answers with `cached`, as `lib.coverage` does); both measure the grid's columns
   when they first meet a stratum -- one pass, at most 12.5 s here on one thread for the largest table.
3. The grid's columns are measured in one pass (`dataset.anchors_at`), not one `anchors` pass per frequency: 67 passes over
   1,736 rows would take minutes, one takes seconds. The files are the same; the pass holds up to 67 lock files at once.
4. Cost of the whole library's grid: 4,279 knot columns. The two large stacked-transformer tables' curve models in the real
   cache average 12 MB (up to 39 MB), so their 345 knot columns each would take about 4 GB, the whole grid roughly 11 GB;
   at N-122's rate (64 and 73 minutes for ten frequencies at 16 threads with `main`'s calibration) those two tables need
   7-8.5 hours each -- N-125's shared fold models should cut the composed curves' share of it.
5. Merging with N-125 (its working tree, not committed when this was written, changes `query.py` around the composed
   calibration only, no hunk near this branch's): `docs/em/library.md` Compute / Cache and the ic-opt skill's library
   paragraph -- one line -- may need a merge by hand.
6. ic-opt 0.9.0 refuses a library.yaml with the entry: stated in the documents, not testable here; the entry belongs
   before `strata:` (lib_tap appends a new stratum only when `strata:` is the last top-level key; elsewhere it writes a
   snippet file instead).
