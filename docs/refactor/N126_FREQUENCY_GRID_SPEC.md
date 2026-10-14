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

(to be filled by the coder)
