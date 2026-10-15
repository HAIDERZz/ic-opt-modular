# IC-Opt 0.10.0

0.9.0 let the device-query library answer a value a table does not reach; this release lets it answer at any frequency at
once. A curve asked at a frequency nobody had asked before was an extension column fitted on first use: minutes per table,
and on the reference library's two largest transformer tables about an hour for ten new frequencies. Now a library may
declare a frequency grid that `lib.prefit` fits ahead, a curve between two of the grid's frequencies is answered from their
models without a fit, and every first fit that remains costs about two and a half times less. Each item has its
specification and record under `docs/refactor/` (`N125_SHARED_PART_FOLDS_SPEC.md`, `N126_FREQUENCY_GRID_SPEC.md`,
`N127_BETWEEN_KNOTS_SPEC.md`). ic-opt itself still calls no language model.

Stores written by 0.9.0 are read as they are and there is no migration step: every spec fingerprint recorded by earlier
releases is recomputed unchanged, and the pipeline fingerprints are unchanged except `lib_design`'s on a library that
declares a grid (item 3).

## Changes in behaviour

Read these before upgrading a project that runs on a device library or a script that reads its answers.

1. **A composed curve calibrated from now on uses stable folds** (N-125). A curve built from other columns (`model: ratio`
   or `resonance`) is calibrated on five folds. Fold s now holds out the same fifth of the stratum's rows for every column,
   and each part's model of a fold -- the low-frequency value or the Q peak, and the SRF -- is fitted once, kept as a
   `fold-*` file in the cache and shared by every curve built on it, at every frequency. A calibration cached before is
   still found under its earlier name, so a column 0.9.0 calibrated answers bit for bit as before; a curve too sparse for
   the stable folds is calibrated on its own rows as before, so which columns can have a model is unchanged. A column
   calibrated the new way may get a different `k_scale` than 0.9.0's folds would have drawn, within the seed-to-seed spread
   of either. On the largest stacked-transformer table of the reference library a new frequency of its five curves takes
   about 2.4 minutes at 16 threads, where its four composed curves alone took 5.7.
2. **A library that declares a frequency grid answers a curve between two of its knots without a fit** (N-127). A curve's
   knots are its anchors and the grid's frequencies where its column can have a model. Asked at an f between two of them,
   the column is no longer measured and fitted: its answer is the PCHIP interpolation in frequency of the knots' models at
   the point (two knots below f, up to two above), with the knots' own calibrated bounds interpolated likewise and widened
   by an allowance -- the interpolation error measured on the stratum's own rows, per knot interval and per how close the
   next knot lies to the row's resonance. The answer adds `interpolated: {"knots": [...], "allowance": a}`. Refused, as
   `out_of_domain` with criterion `interpolation` and a reason that names `exact=true`: a point whose next knot lies at or
   above its resonance margin, a proximity band with too few rows to measure an allowance on, and an allowance above 5 %.
   A library row still answers its measured value. `exact=true` on `lib.query`, `lib.suggest`, `lib.region`, `lib.densify`
   and `lib_design` gives 0.9.0's answers: the frequency's own column, fitted when not cached. On the reference library,
   against the EMX of 101 designs (334 answers), the interpolated answers lie a median 0.002 % (at most 0.33 %) from the
   exact columns' and match their errors and coverage; a transformer request at a new frequency takes 0.5-5 minutes
   instead of about 45 of first fits. Two costs: where a frequency's own column is already cached, interpolation predicts
   about 2.2 times slower than that column (four knots per curve), and next to the resonance it refuses more -- 92 % of the
   points the exact columns answer are answered.
3. **`lib_design` on a library that declares a grid** names the grid and the interpolation in its prediction stage's
   identity unless `exact=true`: a `lib_design` store written by 0.9.0 on such a library predicts its points once more (the
   earlier rows stay as history). On a library without a grid nothing changes.
4. **A manifest with `frequency_grid` is refused by 0.9.0** (its fields are checked strictly): a library that declares a
   grid needs 0.10.0 to be read.

## New

5. **A frequency grid in `library.yaml`** (N-126): the optional library-level entry
   `frequency_grid: {from_ghz: 1, ratio: 1.1, until_ghz: 50, step_ghz: 5}` -- below `until_ghz` the whole GHz
   round(from x ratio^n), then every `step_ghz`, inside the parts' sweeps: 67 frequencies over 0-250 GHz, between which
   PCHIP follows the rows' own measured curves to a p99 of at most 1.7 % on every table and curve of the reference library.
   It is in no stratum's definition, so declaring it changes no dataset key and no cache file name. `Library.knots`
   names a curve's knots; the grid's columns are measured from the rows in one pass.
6. **`lib.prefit`** (N-126): `ic-opt call lib.prefit <root> [strata=] [curves=] [threads=] [workers=] [cache_dir=]` fits
   every knot column the cache does not hold, stratum after stratum on the whole `hosts.local` entry (`threads=` is the total,
   `workers=` at most that), printing per stratum the columns cached, fitted and skipped with the reason, and the minutes;
   resumable. On the reference library's 16 tables: 3,682 knot columns in 141 minutes at 64 threads.
7. **`grid n/m`** in `lib.cached`, `lib.coverage` and `lib.load` per curve: how many of its knots have their model cached.

## Checks

The whole test suite in 12 groups (with the recorded runs), `ruff`, the pcell suite without the private profile, a clean
install of the wheel, and the spec fingerprints of the real projects recomputed: see the release record
(`ic-opt-accept/release_v0.10.0/RELEASE_RECORD_CN.md`, outside the repository).
