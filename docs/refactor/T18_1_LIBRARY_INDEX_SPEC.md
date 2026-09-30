# T18.1 — the library answers at any frequency, carries footprints, and indexes its rows by electrical values

Status: specification (2026-10-01), for the coding subagent. Decisions behind it: the user's answers of 2026-10-01 to
`T18_LIBRARY_DIRECTIONS_CN.md` section 7 (1: rows are the candidates; 2: any frequency first; 3: answers carry the
footprint; 7: the order L1 then L2). This is stage L1: everything stays inside the library (`ic_opt/library`, its
blocks, its documents). Stage L2 (`T18_2A_ALLOWED_COMBINATIONS_SPEC.md` and the device specification that follows it)
builds on the index defined here.

Words: a *table* is a stratum; a *row* is one real EMX result with its sNp; a *result column* is a value computed from
the sNp; a *model* is a fit on one table. Read `docs/em/library.md`, `src/ic_opt/library/{manifest,dataset,query,
suggest,stage}.py` and `src/ic_opt/blocks/library.py` before writing anything.

## 1. Result columns at any frequency

Today a curve (`Lp`, `Ls`, `Qp`, `Qs`, `k`) has a column only at the anchors the manifest declares (`Lp@28`). Every
row keeps its whole sNp, so a column at another frequency is a matter of measuring again, not of simulating.

- An *extension column* is `<curve>@<f>` where `<curve>` is a curve the stratum declares and `<f>` (GHz, written
  `%g`: `Lp@33`, `Lp@27.5`; `Lp@28.0` is `Lp@28`) is not one of its declared anchors. Its value for a row is defined
  exactly as a declared anchor's (`dataset._row`): None when `<f>` lies outside the row's sweep or the row's system SRF
  is at or below the curve's `srf_margin` × f; else `measure.Quantities.at(curve, f)` when finite, else None. The
  curve's manifest rule applies unchanged: `srf_margin`, `feature_map`, `model` (`direct` / `ratio` / `resonance`),
  `rel_sigma_max`. A curve the stratum does not declare is refused with the curves it does declare.
- Computing: one pass over the rows' sNp files gives every declared curve at that frequency; the result is cached in
  the library's cache (`cache.locate`, the atomic write the dataset uses) under a name made of the stratum, the
  dataset's content key and the frequency, e.g. `anchors-<stratum>-<dataset key>-<f>.json`, values in the order of
  the dataset's rows. The dataset itself, its key and every cache file a library has today are untouched: a library
  that never asks for an extension column reads and writes exactly the files it did (a test pins the dataset key and
  the cache file names of the fixture library before and after).
- Using: everything that takes a column name takes an extension column: `lib.query` (`quantities=`), `lib.suggest`
  and `lib.region` (targets, objective, trend; the implied `SRF >= srf_margin × f0` applies as for a declared anchor),
  `lib.densify` (`quantities=`), `Library.model` / `Library.models`, and the `Predict` stage (a device metric
  `{quantity: Lp, frequency_hz: 33e9}` on a stratum that declares `Lp` with other anchors). For a library point the
  answer is `measured`, the value read from that row's sNp; elsewhere the model of that column, fitted and calibrated
  and cached like a declared column's (its cache names already carry the column name). `lib.coverage` and `lib.load`
  keep listing the declared columns; `lib.coverage` gains one line saying that a declared curve answers at any
  frequency inside the parts' sweeps and what those sweeps are.
- A frequency no row can give (outside every part's sweep, or every row's resonance below the margin) is refused with
  the parts' sweep ranges; it is not an empty answer.
- How it is wired inside (`Library.dataset` returning an extended copy, a `Library.column`, ...) is the coder's
  choice; say in the hand-back what was done. The contract is the list above plus: one process computes an extension
  at a time (the lock discipline of the model cache), and nothing is fitted or measured for a column nobody asks for.

## 2. Footprint

The user asked that answers carry the footprint. A footprint is the bounding box of what the generator draws for the
device -- windings, crossovers, leads -- **without the ground fixture** it adds for EMX (ring, strips, stubs):
`{"width_um": .., "height_um": .., "area_um2": ..}`, rounded to 0.001.

- A function in the EM layer (suggested: `ic_opt/em/pcell/footprint.py`) computes it from a GDS file the generator
  wrote. Read `em/pcell/fixture.py` and the generators to find how the fixture's shapes are told from the device's;
  say in the hand-back how. If a family's fixture cannot be told apart, the footprint is None with a reason -- never
  a box that includes the fixture silently.
- Library rows: from the GDS each row keeps beside its sNp (`.icopt/sims/<obs>/em/<device>/<device>.gds`), one pass
  per stratum, cached (`footprint-<stratum>-<dataset key>.json`); a row without its GDS has None. Computed only when
  an answer needs it.
- Where it appears: `lib.query` (a library point: its row's; elsewhere None unless the call says `footprint=true`,
  which builds the geometry with the generator, no EMX); `lib.suggest` (`measured[*]`, and `candidates[*]` when
  `verify_build` drew them); `lib.region`'s candidates likewise; the index rows and the picks of section 3; the
  leaders of `lib_design`'s report (from each leader's drawn GDS).
- No port positions, no area objective in this stage (the user chose the footprint in the answer only).

## 3. The electrical index (`ic_opt/library/index.py`)

The index is a table's rows seen by their electrical values at one working frequency: what stage L2 draws a
device's candidates from, and what `lib.index` / `lib.pick` show.

```python
@dataclass(frozen=True)
class IndexRow:
    stratum: str
    part: str
    obs_id: str
    params: dict[str, float]            # the row's geometry: the stratum's dims
    values: dict[str, float | None]     # the index's columns
    footprint: dict | None
    snp: str                            # relative to the library root
    ports: list[str]                    # the sNp's column labels, in the file's order

@dataclass(frozen=True)
class Index:
    stratum: str
    frequency_hz: float
    srf_margin: float
    columns: list[str]
    rows: list[IndexRow]
    key: str                            # content: the dataset key, the frequency, the margin, this module's version

def build(library: query.Library, stratum: str, frequency_hz: float, *, srf_margin: float | None = None) -> Index: ...
```

- Columns: every declared curve at `frequency_hz` under its bare name (`Lp`, `Qp`, and for a coupled pair `Ls`, `Qs`,
  `k`); every declared scalar under its own name (`Lp_lf`, `SRF`, `Qp_peak`, ...); `Qmin` = min(`Qp`, `Qs`) for a
  stratum with both; `area` = the footprint's area (None without a footprint).
- Rows: the dataset's rows that have every curve value at `frequency_hz` (section 1's rule already drops a row whose
  resonance is within the curve's margin). `srf_margin` given and larger than the manifest's also drops the rows
  whose known system SRF is at or below `srf_margin × frequency_hz`; a row with no resonance inside its sweep stays
  (its `SRF` value is None). A frequency with no row at all is refused as in section 1.
- `ports` is the row's sNp column order as `dataset._snp_columns` reads it (expose what is needed; do not duplicate).

```python
def level(value: float, lower: float, upper: float, step: float) -> int | None: ...
def table(index: Index, grid: dict[str, tuple[float, float, float]], prefer: str | None = None) -> Table: ...
def pick(table: Table, targets: dict[str, float]) -> Pick: ...
```

- `grid`: coordinate (an index column) -> `(lower, upper, step)` in the column's unit (H, 1, Hz). Its levels are
  `lower + i × step`, `i = 0 .. round((upper - lower) / step)`.
- `level`: the index of the nearest level **in the coordinate's search scale** -- logarithmic when `lower > 0` and
  `upper / lower >= 10` (`ic_opt.space.log_scale`: import it, do not copy the rule), else linear; a tie goes to the
  lower level. None when the value lies beyond an end by more than half the end interval in that scale. This is the
  rule `metric_gp`'s `Coords.snap` applies to a grid ("nearest level in unit coordinates, ties to the lower level"),
  so that L2's strategies and this table agree; write a test that compares `level` with `Coords.snap` on a linear
  and on a logarithmic variable (build a `Spec` variable with the same grid in the test).
- `table`: each row goes to the cell of its coordinates' levels (a row with a coordinate value None, or beyond the
  grid, is left out); within a cell the rows are ranked by `prefer`, `max:<column>` or `min:<column>` (default
  `max:Qmin` when the index has `Qmin`, else `max:Qp`; a row without that value ranks last; remaining ties by
  `(part, obs_id)`). `Table` holds the coordinates in the grid's order, the grid, `prefer`, and
  `cells: dict[tuple[int, ...], list[IndexRow]]` (best first), with `levels()` giving the occupied cells sorted.
- `pick`: the occupied cell nearest to the targets -- Euclidean distance over the coordinates in unit coordinates of
  the grid (each coordinate's levels mapped to [0, 1] in its search scale), ties to the smallest cell in sorted order
  -- with its rows (best first), `exact` (the targets' own cell is occupied) and the distance per coordinate in
  levels.

## 4. Blocks

- `lib.index ROOT stratum=S frequency_ghz=F [srf_margin=M] ['grid={"Lp": [lower, upper, step], ...}'] [prefer=max:Qmin] [out=FILE] [cache_dir=DIR]`
  Without `grid`: the index's summary -- rows kept and dropped (and why), each column's range, the parts' sweeps.
  With `grid`: also the table's summary -- cells occupied of how many, rows per occupied cell (median, largest), per
  coordinate the span of occupied levels and how many levels of the grid hold no row. `out` writes the table: per
  occupied cell its levels, the level values, and its best row (values, params, footprint, part, obs id, the sNp
  path relative to the library root).
- `lib.pick ROOT stratum=S frequency_ghz=F 'targets={"Lp": 3e-10, "Ls": 2.5e-10, "k": 0.6}' 'grid={...}' [prefer=...] [n=1] [srf_margin=M] [cache_dir=DIR]`
  The nearest occupied cell and its best `n` rows: electrical values, geometry, footprint, part and obs id, the sNp
  path relative to the library root, `exact`, the distance in levels. `grid` is required: it says what counts as the
  same value.
- Both print strict JSON like the other `lib.*` blocks and appear in `ic-opt blocks` / `ic-opt describe`.

## 5. Documents and tests

- `docs/em/library.md`: a subsection on asking at any frequency (section 2 and 4 there), a subsection on the
  footprint, and "5d. Rows by electrical values: `lib.index`, `lib.pick`" with the placeholder style the file uses
  (no value of any real process). `skills/ic-opt/SKILL.md`: the library paragraph names the two blocks and that any
  frequency can be asked. `README.md`: the block list.
- Tests (`tests/ic_opt/test_library_anyfreq.py`, `test_library_index.py`, `test_library_footprint.py`; use
  `tests/ic_opt/library_fixtures.py`): the extension column equals what a manifest declaring that anchor gives, row
  by row; declared anchors, the dataset key and the cache file names are unchanged; `lib.query` measured / predicted
  on an extension column; `lib.suggest` with an extension target; refusals (undeclared curve, frequency outside the
  sweeps); the footprint of a drawn fixture device excludes the fixture (assert against the generator's own numbers)
  and is None for a row without GDS; the index's rows, columns, `Qmin`, `area`, the margin; `level` against
  `Coords.snap`; `table` ranking and ties; `pick` exact and nearest; the two blocks' JSON.
- The targeted run: the new test files plus `tests/ic_opt/test_library*.py`; then `ruff check src tests`.

## 6. Working rules for the coder

- Branch `t18-1-library-index` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t18-1-library-index -b t18-1-library-index main`);
  commits on that branch only; never touch `main`, never push.
- Another agent works at the same time on `t18-2a-allowed-combinations` (`src/ic_opt/space.py`,
  `src/ic_opt/suggesters/`, `src/ic_opt/blocks/points.py`, `src/ic_opt/blocks/optimize.py`, a new
  `src/ic_opt/library/link.py`). Do not edit those files; read them freely.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `import ic_opt` resolves to the worktree); never `uv run`, `uv sync`, `pip`. No simulator, no network.
- The machine is running the user's simulations under a thread budget. Run tests with `OMP_NUM_THREADS=1
  OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`, at most two test processes at a time, never the whole suite, never
  `-n`. Do not open the user's private library (anything outside the repository and the test fixtures).
- No name, number, layer or path of a real process in code, tests or documents (the repository is public).
- One commit per coherent piece, each message saying what and why; trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); how the extension columns are
  wired; how the fixture is told from the device in the footprint; what the specification left open and how it was
  read (a numbered list); what could not be done and why.
