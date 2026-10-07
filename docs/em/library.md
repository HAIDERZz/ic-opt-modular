# Device library

A device library turns EM characterization runs into something you can ask:
"what are L, Q and SRF of this geometry?" and "which geometries give 1.2 nH
with the most Q at 28 GHz?". It is not a separate database. A library is a
directory of ordinary ic-opt run stores (em_only projects that `sim.evaluate`
filled) plus one manifest, `library.yaml`. Every row the library answers
from is an `ok` observation with its sNp on disk.

```text
<library root>/                  # outside the repository: measured data never enters it
  library.yaml                   # strata, parts, dims, quantities
  ind_sym_top/                   # a part: spec.yaml + .icopt/ (observations.jsonl, sims/<obs>/em/<device>/*.sNp)
  ind_sym_top_nt1/               # another part of the same stratum (e.g. single turns, swept further)
  .cache/                        # datasets, calibration and fitted models, keyed by content; safe to delete (see Cache)
```

The blocks take the library root as their directory: `ic-opt call lib.<name>
<library root> key=value ...`. Dict answers print as JSON.

## 1. Build the parts (real EMX)

A part is an em_only project: one device (`generator`, `profile`, `fixed`
fields), `variables` that become the library's dims, an `em:` section, the
resource fields every spec states (`simulator.parallel_jobs` /
`threads_per_run` / `timeout_s`, `em.threads` / `memory_gb` / `timeout_s`:
yours, with no defaults), and a budget large enough for the sweep. Sweep it
with a few-line recipe:

```python
# sweep.py -- space-filling points on this part
from ic_opt import blocks as b


def main(run, n: int = 200, seed: int = 0):
    points = b.points_sobol(run.spec, int(n), seed=int(seed))
    b.evaluate(run.spec, points, run.executor, run.store, step="sweep", cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
```

```bash
ic-opt run sweep.py <library root>/ind_sym_top n=400 --plan    # points, EMX runs, jobs x threads: the approval point
ic-opt run sweep.py <library root>/ind_sym_top n=400
```

Keep one EMX setting per part: the same accuracy, full-wave mode and
frequency sweep for every row. A stratum that needs a different sweep for
part of its range (single-turn inductors resonate far above the others)
gets a second part. EMX runs are cached by GDS bytes, ports, physics and
the process file's content, so a re-run only simulates new points.

## 2. Declare the library

```yaml
# <library root>/library.yaml
schema_version: ic-opt-library-v1
process_profile: demo_6m
strata:
  ind_sym_top:                                   # one device family on one metal body
    generator: clean_port_ind_sym
    note: primary on M6, the thick top metal        # optional, free text: what the parts are physically; lib.coverage echoes it
    dims: [outer_diameter_um, width_um, spacing_um, turns]
    nt_dim: turns                                # the integer turns dim: one model per turns level
    parts:
      - {store: ind_sym_top}
      - {store: ind_sym_top_nt1}                 # pipeline_fingerprint: pins a generation (default: the part's most common one)
    steps: {outer_diameter_um: 1, width_um: 0.1, spacing_um: 0.1, turns: 1}   # candidate resolution for lib.suggest
    quantities:                                  # names of the measure kernel
      Lp_lf: {rel_sigma_max: 0.05}               # low-frequency inductance; confident only within 5% (default 0.15)
      Lp_res: {}
      Qp_peak: {band_ghz: 60}                    # peak searched in 0 < f <= band, the same band for every part
      SRF_p: {}                                  # modeled by the GP; no resonance in the sweep -> "above_sweep"
      Lp: {anchors_ghz: [10, 28]}                # curve columns Lp@10, Lp@28
      Qp: {anchors_ghz: [10, 28]}                # a row counts at f0 only if its SRF > srf_margin x f0 (default 1.25)
```

Scalars: `Lp_lf Lp_res Qp_peak SRF_p` and, for transformers, `Ls_lf Ls_res
Qs_peak SRF_s k_lf`, plus `SRF`, the system SRF (the lowest resonance over
all drives; `SRF_p` for an inductor). Curves sampled at anchors: `Lp Qp Ls
Qs k`. Every value is recomputed from the sNp with these definitions, so
parts swept to different stop frequencies still answer on one basis. An
anchor reads the sample at that frequency; between two samples, the curve
interpolated linearly between them, on a uniform sweep and on any frequency
list alike (before T16.6: the nearest sample, up to half a step away). The
0 Hz sample of a sweep has no L, Q or k, so below the first positive sample
L and k keep that sample's value, because inductance and coupling are flat
at low frequency, and Q rises linearly from 0 at 0 Hz, because Q = wL / R
grows about in proportion to frequency there. An anchor outside a part's
sweep, below its first sample or above its last, leaves that column empty;
the row keeps its other columns.

`rel_sigma_max` is a quantity's confidence ceiling on sigma / mu, for every
anchor of a curve: a prediction less sure than that is `uncertain` (section
4), and no block uses it as an answer. A call's `rel_sigma_max=` overrides
it for every quantity; a quantity without one has 0.15. The ceiling only
decides which predictions are trusted, so setting or changing it refits
nothing.

The low-frequency scalars (`Lp_lf`, `Ls_lf`, `k_lf`) average the samples
up to 3 GHz; a stratum's `low_freq_max_hz` sets another top, in Hz or
`relative` for min(3 GHz, SRF / 10), over the parts' own
`topology.low_freq_max_hz`, and a row swept entirely above that top keeps
its other columns: only these (and `Lp_res` / `Ls_res` when nothing lies
below SRF / 5) stay empty.

For a transformer, query `SRF` rather than `SRF_p` / `SRF_s`: the secondary's
resonance reflects into the primary's impedance as a sharp dip, and whether
that dip crosses zero decides whether `SRF_p` lands on it or on the primary's
own, much higher resonance. Two neighbouring geometries can differ by tens of
GHz in `SRF_p` while `SRF` stays continuous. For the same reason every
anchored curve of a coupled pair is used only below the system SRF, and a
peak (`Qp_peak`, `Qs_peak`) is searched only below it: a multi-turn
secondary resonates inside the sweep, and above that the primary's Q curve
can climb again to the band edge.

Each quantity may also say how its model is built, with two optional fields.

`feature_map` (transformers: `xfm_bs_dimensionless`, `xfm_ms_dimensionless`)
gives the model other inputs than the raw dims: the mean outer diameter, the
ratio of the two outer diameters, each width over its diameter, the centre
offset over the mean radius (and, for `xfm_ms`, the secondary's spacing over
its diameter and its turns). Two stacked windings couple, and load each
other with capacitance, according to how far their outlines are apart, so
the coupling, the system SRF and every column near that resonance change
fast across the diameter ratio and slowly with the overall size. On the raw
dims that fast change runs diagonally between the primary's and the
secondary's diameter; with the ratio as an input of its own, what the model
learns at one sampled primary diameter carries over to the next, and its
answers between the sampled diameters hold up.

`model` (curves only; default `direct`) says what the curve's model fits.
A column such as `Lp@40` holds two things at once: the winding's
low-frequency inductance, which changes smoothly with the geometry, and the
rise of the apparent inductance towards the self-resonance, which is steep
where the resonance comes close to the anchor. `direct` fits one model to
the column and has to learn both. The other two build the column from the
stratum's own models and fit only what is left, with the curve's
`feature_map`:

- `ratio`: a base scalar -- the low-frequency value for `Lp`, `Ls` and `k`
  (`Lp_lf`, `Ls_lf`, `k_lf`), the peak for `Qp` and `Qs` (`Qp_peak`,
  `Qs_peak`) -- times a model of the measured ratio, say `Lp@40 / Lp_lf`
  or `Qp@40 / Qp_peak`;
- `resonance` (`Lp` and `Ls` only): the low-frequency value, times the rise
  of an ideal parallel resonance `1 / (1 - (f0 / SRF)^2)` at the SRF that the
  stratum's `SRF` model predicts, times a model of what is left.

```text
# a stacked-transformer stratum's quantities, the setup that holds up between sampled diameters
      Lp_lf: {}
      Ls_lf: {}
      SRF: {feature_map: xfm_bs_dimensionless}
      Lp: {anchors_ghz: [40], model: resonance, feature_map: xfm_bs_dimensionless}
      Ls: {anchors_ghz: [40], model: resonance, feature_map: xfm_bs_dimensionless}
```

The parts must be among the stratum's quantities (the manifest refuses the
option otherwise), and they are the very models that answer those columns,
fitted once however many curves share them. A `resonance` curve is only as
good as its SRF model: where the table has no rows like the design asked
about, the SRF is off and the curve follows it. The interval adds the parts'
uncertainties as independent ones, the SRF's scaled by how steeply the rise
depends on it, and is calibrated like any other (below) -- except that each
held-out fold refits every part, so it takes about two (`ratio`) or three
(`resonance`) times the fits of a direct column; the folds run as parallel
jobs. Changing `model` or `feature_map` of any quantity is a new definition
of the stratum -- unlike `rel_sigma_max`, it changes what is fitted: its
dataset and every model of that stratum are rebuilt once. A library that
never names `model` keeps its caches.

### Asking at any frequency

A declared curve answers at any frequency inside its parts' sweeps, not only
at its anchors. Name the column `<curve>@<f>`, `<f>` in GHz with at most six
significant digits: `Lp@<f>`, `Qp@<f>`; `Lp@28.0` is `Lp@28`. At a frequency
the curve does not anchor this is an *extension column*: every row's sNp is
measured again at `<f>` by the rule of an anchor above -- empty outside the
row's sweep and where the row's system SRF is at or below the curve's
`srf_margin` times `<f>` -- in one pass over the rows, which also gives the
stratum's other curves there, and the values are kept in the cache as
`anchors-<stratum>-<dataset key>-<f>.json`. From then on the column is used
like a declared one: a library point answers with its row's value, and
elsewhere the column has its own model, calibrated and cached under the
column's name like any other; the curve's `srf_margin`, `feature_map`,
`model` and `rel_sigma_max` apply unchanged. `lib.query` (`quantities`),
`lib.suggest` and `lib.region` (targets, objective, trend: an extension
target implies `SRF ≥ srf_margin × f0` as an anchored one does),
`lib.densify` (`quantities`) and a `lib_design` device metric at a
frequency all take it.

Nothing is measured or fitted for a column nobody asks for, and the dataset,
its key and every cache file the library already has stay as they are. A
curve the stratum does not declare is refused with the curves it does
declare; a frequency outside every part's sweep, or one where no row has a
value, is refused with the parts' sweeps (it is not an empty answer).
`lib.coverage` keeps listing the declared columns and adds one line,
`any_frequency`, naming the curves that answer anywhere and the parts'
sweeps.

## 3. Check it

```bash
ic-opt call lib.load <library root>                          # per stratum: rows per part, usable rows per quantity, integrity evidence
ic-opt call lib.coverage <library root> stratum=ind_sym_top  # rows per turns level, achieved range of every dim, value range per quantity
```

`lib.load` refuses a declaration error instead of answering from a partial
table: a part without observations or with more than one device, rows that
lack a declared dim, a quantity the kernel does not know, a peak band beyond
a part's sweep, a transformer quantity on a device with one port pair.

For a stratum with a `k_lf` column, `negative_k_lf` counts the rows measured
with `k_lf < 0` (null without that column). The built-in families measure a
positive `k` under the default topology; a row with a negative one usually
belongs to a part whose generator winds its secondary the other way. State the
device's `topology` in that part's spec ([devices.md](devices.md), measurement
topology): the dataset measures the stored sNp again with it, without EMX.

## 4. Forward questions: `lib.query`

```bash
ic-opt call lib.query <library root> stratum=ind_sym_top \
    'params={"outer_diameter_um": 150, "width_um": 5, "spacing_um": 3, "turns": 2}' quantities=Lp_lf,Qp_peak,Lp@28
```

Each quantity comes back with a `status`:

| status | meaning |
| --- | --- |
| `measured` | the point is a library row: the measured value |
| `predicted` | GP mean `value` with calibrated bounds `lo` / `hi`, plus the three nearest measured rows as evidence |
| `uncertain` | predicted, but sigma / mu exceeds the quantity's ceiling (criterion 4 below; the answer gives it as `rel_sigma_max`): reported with its numbers, never used silently |
| `out_of_domain` | the domain guard refused: `criterion`, `reason`, the nearest measured rows |
| `above_sweep` | SRF only: most nearest rows did not resonate inside their sweep; `lower_bound` is that sweep's stop |

The domain guard's criteria: (1) inside the achieved box, and a dim that is
fixed within a turns level must match it; (2) a model exists for this turns
level (at least 25 usable rows); (3) inside the level's convex hull over the
dims that vary there; (4) sigma / mu at most the quantity's ceiling: the
call's `rel_sigma_max` (a parameter of `lib.query`, `lib.suggest`,
`lib.region`, `lib.densify` and `lib_design`) when given, else the
quantity's `rel_sigma_max` in `library.yaml`, else 0.15. The model is a
Matern 5/2 GP per turns level on log targets. Bounds
are mu +- k sigma (`k=2`) widened by
a calibration factor from held-out residuals (`max(1, q95(|z|) / 2)`), so
about 95% of held-out measurements fall inside; sigma is never reported below
the quantity's held-out median relative error, because the GP is overconfident
at the edge of the sampled box, where designs recommended for a maximum tend
to sit.

A curve built with `model: ratio` or `model: resonance` also answers where its
value came from, in `composition`: the base value under its own name (the
low-frequency value such as `Lp_lf`, or for `Qp` / `Qs` the peak `Qp_peak` /
`Qs_peak`), and the `ratio`, or the `SRF` (in Hz), the `resonance_factor`
1 / (1 - (f0/SRF)^2) and the `residual`. Their product is `value`.

`quantities` may name a declared curve at any frequency inside the parts'
sweeps, `Lp@<f>` ([Asking at any frequency](#asking-at-any-frequency)); the
answer names each column in its canonical spelling.

### Footprint

Answers carry the footprint of a design: the bounding box of what the
generator draws for the device -- windings, crossovers, leads -- without the
ground fixture it adds for EMX (the ring, the stubs and a shield's strips),
as `{"width_um": .., "height_um": .., "area_um2": ..}` rounded to 0.001.
Every built-in family draws that fixture last, and only it, on the process
profile's fixture conductor, the bottom metal of its stack, and refuses that
conductor as a product metal (directly and through a crossunder below a
winding); the footprint is the box around every shape on the other layers.
A generator of another plugin gives no such guarantee: its footprint is
null, never a box that may hold the fixture. A null footprint comes with
`footprint_why`.

A library row's footprint comes from the GDS kept beside its sNp, read once
per stratum when an answer needs it and cached as
`footprint-<stratum>-<dataset key>.json`; a row without its GDS has none.
`lib.query` gives a library point its row's; elsewhere `footprint` is null
unless the call says `footprint=true`, which draws the geometry with the
stratum's generator (no EMX). `lib.suggest` gives it for its `measured`
designs, and for its `candidates` when `verify_build` drew them; `lib.region`
likewise (its default draws nothing). The rows and picks of section 5d carry
it, and so do the leaders of `lib_design`, from the GDS each leader's pcell
drew. It is part of the answer, not of the search: `lib.suggest` and
`lib.region` neither rank nor filter by it.

## 5. Inverse questions: `lib.suggest`

```bash
ic-opt call lib.suggest <library root> stratum=ind_sym_top \
    'targets={"Lp_lf": {"target": 1.2e-9, "tol": 0.03}, "SRF_p": {"min": 60e9}}' objective=max:Qp_peak n=5
```

Targets are per quantity, in SI units: `{"min": v}`, `{"max": v}` or
`{"target": v, "tol": relative}`. Both bounds together make a window:
`{"min": a, "max": b}`. The answer lists `measured` designs that
meet the targets (exact, already simulated) apart from `candidates`
(interpolated geometries snapped to `steps`). A candidate must pass the domain
guard, meet every target with its whole calibrated interval, and build: the
real generator draws it and the product DRC audit checks it. An anchored
target such as `Lp@28` adds `SRF ≥ srf_margin × f0` (the manifest's
`srf_margin`, 1.25 in the example manifest, which keeps the default: here
SRF ≥ 35 GHz) unless SRF is already constrained; a target at a frequency the
curve does not anchor (an extension column, section 2) does the same.

## 5b. Region questions: `lib.region`

```bash
ic-opt call lib.region <library root> stratum=<stratum> \
    'targets={"<quantity>@<f>": {"min": <a>, "max": <b>}, "<quantity>@<f>": {"min": <c>}}' \
    group_by=<dim>,<dim> trend=<quantity>:<dim>
```

Every value in angle brackets is a placeholder. For example, an inductance
window and a minimum Q at one frequency: `Lp@<f>` between `<a>` and `<b>`
henries, `Qp@<f>` at least `<c>`. `<f>` may be any frequency inside the
parts' sweeps: at one of the curve's `anchors_ghz` (the manifest of section 2
declares 10 and 28 GHz for `Lp` and `Qp`) it reads the declared column,
anywhere else an extension column measured from the rows
([Asking at any frequency](#asking-at-any-frequency)).

`lib.suggest` names a few good geometries; `lib.region` describes all of
them, the region of the stratum whose predictions meet the targets, so that
a sweep can be bounded by it. Targets are written as for `lib.suggest`. A
point is `robust` when its whole calibrated interval lies inside every
window (the `lib.suggest` test: centre a sweep there) and `mean` when its
predicted value does (the optimistic envelope). A coarse
pass over the library rows and a Sobol pool, with the stated windows
`relax` wider (0.10, that is 10%, unless given), brackets the region. How
much wider they need to be depends on the models' sigma and on how densely
the pool covers the region. When the mean set runs up to the edge of
`grid.bracket` in some dim while `edge` says it stops short of the
library's coverage there, the bracket may have cut it off: raise `relax`.
`grid` echoes the value. Inside the bracket the grid lies on multiples
of the manifest `steps`, about 20 values per dim (turns by level), every
step multiplied until the grid fits in `max_points` (2 million);
`steps={...}` sets steps by hand, and `grid` says what was used.

The answer states what its levels mean: `k`, the number of sigmas in the
calibrated interval behind `robust` (`k=2` unless given), and
`rel_sigma_max`, the confidence ceiling each quantity was held to (section
4). It gives each level's point count and per-dim ranges; `binding`,
the points meeting each target alone (the smallest count binds); `edge`,
per dim, whether the mean set reaches the library's coverage, beyond which
no model answers; `group_by`, the counts and the other dims' ranges for
every combination of the named dims; `trend`, one quantity's min, median
and max along one dim over the points meeting every other target;
`candidates` in `lib.suggest`'s shape; the `measured` rows that already
meet every target; and a `points_sample` to plot. The per-dim ranges are
projections. The dims are correlated (a width leaves a short stretch of
diameters), so take a sweep from the `group_by` rows, not from the ranges
alone. Fitted models are cached (see [Cache](#cache)); the uncached ones are
fitted in parallel processes sized from `hosts.local` (see
[Compute](#compute)). `workers` and `threads` cap the fitting processes and
the BLAS threads; a value above what that entry allows is refused.

## 5c. Where to simulate next: `lib.densify`

```bash
ic-opt call lib.densify <library root> stratum=<stratum> n=<n> quantities=<quantity>,<quantity> out=<file>
ic-opt call lib.densify <library root> stratum=<stratum> n=<n> 'bounds={"<dim>": <v>, "<dim>": {"min": <a>, "max": <b>}}' out=<file>
ic-opt run lib_signoff <project> library=<library root> candidates=<file> top=<n> adopt=true --plan
ic-opt run lib_signoff <project> library=<library root> candidates=<file> top=<n> adopt=true
```

`lib.suggest` and `lib.region` answer target windows, and how well they
can depends on where the library has rows. `lib.densify` asks where new
rows would help the models most, whatever the targets. It proposes `n`
geometries whose simulation lowers the uncertainty of the named quantities
(default: every column) the most over the whole sampled domain. The whole
domain counts: where most rows hold a dim at one value and a few rows
reach further, the models know little of the region those few rows open,
and the picks go there first.

`bounds` keeps the part of the domain you care about: per dim a fixed value
or a window `{"min": a, "max": b}`, either end optional and clipped to the
range the rows reach. For example, fix a dim at the value most rows share
to densify there and nowhere else. A dim the stratum lacks, or a window or
value outside the rows' range, is refused and the message gives that range.
The answer echoes the effective bounds (`{}` without) and `pool` counts
the points they keep (`in_bounds`).

The candidates are `pool_size` Sobol points (65 536) inside the bounds,
snapped to the manifest `steps`, off the measured rows and inside every
model's domain. Each is scored by the largest, over the quantities, of the
model's own posterior sigma divided by a norm. With `score=ceiling` (the
default) the norm is the quantity's confidence ceiling (`rel_sigma_max`,
section 4): how far the model is above the sigma the library calls usable
for that quantity, so the picks go where the library cannot answer yet. With
`score=typical` it is the quantity's held-out median relative error: how
many typical errors the model may be off, which lets a quantity with a tiny
typical error lead the ranking even where its sigma is already below the
ceiling. `method` names the score and each quantity's norm. The sigma is
relative (log-space for a positive quantity) and has no floor: the floor of
section 4 is the same everywhere and says nothing about where the model is
unsure.
The `top` best (4000; fewer when their covariance matrices would not fit
the prediction budget, see [Compute](#compute)) get each model's posterior
covariance. Then, `n` times, the best is picked and every other
candidate's variance is updated as if the pick had been measured, so the
next pick goes where uncertainty remains instead of next to the last one.
A GP's variance does not depend on the measured value, so for the fitted
hyperparameters the update is exact. Turns levels are separate models and
never share an update.

`before` and `after` give, per quantity, the median, p90 and maximum
relative sigma over the pool and the share above its ceiling, without
and with the picks measured (`method` names each quantity's ceiling). The library refits after adoption and
optimizes the hyperparameters again, so read `after` as the order of the
gain, not a promise. Each candidate carries its score, its sigma before any
pick and when it was picked, the current prediction with its calibrated
interval (`k`), and the three nearest measured rows. Where most of the
nearest rows resonate above their sweep, an SRF is settled there
(`above_sweep` in `lib.query`): it neither scores nor counts in its
statistics, since a simulation there would not yield one.

`out=` writes the answer to a file that `lib_signoff` takes as its
`candidates` (section 7). `--plan` lists the EMX runs and is the approval
point; the same command without it simulates, compares each measurement
with the prediction made before it and, with `adopt=true`, copies the rows
into the part stores. The next dataset build includes them, and the
calibrations and models are refitted on first use because their cache keys
follow the data. A second `lib.densify` with the same `seed` then shows
what the batch bought: its `before` against the first one's `after`.

## 5d. Rows by electrical values: `lib.index`, `lib.pick`

```bash
ic-opt call lib.index <library root> stratum=<stratum> frequency_ghz=<f>
ic-opt call lib.index <library root> stratum=<stratum> frequency_ghz=<f> 'grid={"Lp": [<lower>, <upper>, <step>]}' out=<file>
ic-opt call lib.pick <library root> stratum=<stratum> frequency_ghz=<f> 'targets={"Lp": <v>, "Ls": <v>, "k": <v>}' \
    'grid={"Lp": [<lower>, <upper>, <step>], "Ls": [<lower>, <upper>, <step>], "k": [<lower>, <upper>, <step>]}' n=<n>
```

`lib.suggest` asks for geometries that meet windows. `lib.index` shows the
rows themselves by their electrical values at one working frequency `<f>`:
measured values, no model. Its columns are every declared curve at `<f>`
under its bare name (`Lp`, `Qp`, and for a coupled pair `Ls`, `Qs`, `k`),
every declared scalar under its own name, `Qmin` (the smaller of `Qp` and
`Qs`, for a stratum with both) and `area` (the footprint's, in um²; null
without one). Its rows are the rows that have every curve at `<f>`: each
curve's rule already leaves out a row whose sweep does not reach `<f>` or
whose resonance lies within the curve's `srf_margin`; `srf_margin=<m>` above
the manifest's also leaves out the rows whose system SRF is at or below
`<m>` × `<f>` (a row with no resonance inside its sweep stays). Without
`grid` the answer counts the rows kept and dropped, and why, and gives each
column's range, how many rows have a footprint and the parts' sweeps.

`grid` puts the rows on a grid of electrical values: per column
`[<lower>, <upper>, <step>]` in the column's unit (H, 1, Hz), levels
`<lower> + i × <step>`. A range whose lower end is positive and whose upper
end is at least ten times it is searched on a logarithmic scale, as the
optimizer searches such a variable; any other linearly. Each row goes to the
cell of its nearest levels in that scale, a tie to the lower level -- the
rule the optimizer's own grid follows -- and a row beyond an end by more
than half the end interval stays out of the table. Within a cell the rows
are ranked by `prefer`, `max:<column>` or `min:<column>` (default
`max:Qmin`, else `max:Qp`); a row without that value comes last, and ties go
by part and obs id. The answer adds `table`: the cells occupied of how many,
the rows per occupied cell (median and largest), and per coordinate the span
of occupied levels and how many levels hold no row. `out=<file>` writes the
table: per occupied cell its levels, their values and its best row (values,
geometry, footprint, part, obs id and the sNp path relative to the library
root).

`lib.pick` answers one target on such a grid: the occupied cell nearest to
`targets` -- the Euclidean distance over the grid's coordinates, each mapped
to [0, 1] in its search scale, a tie to the smaller cell -- and its best `n`
rows with their values, geometry, footprint, part, obs id, sNp path and the
sNp's port labels. `exact` says whether the targets' own cell holds a row,
and `distance` how many levels the picked cell lies from it per coordinate.
`grid` is required: it says what counts as the same value. A frequency
outside the parts' sweeps, or one where no row keeps every curve, is refused
with the parts' sweeps.

## 6. Design on the library: `lib_design`

The same em_only spec you would optimize with EMX (device, variables,
metrics, constraints, objective) can be optimized on predictions instead:

```bash
ic-opt run lib_design <project> library=<library root> budget=200 strategy=turbo top=5
```

The pipeline is `pcell -> predict`: every point is still built by the
generator, and each device metric comes from the library (a metric such as
`{quantity: Lp, frequency_hz: 28e9}` reads the `Lp@28` column; at a
frequency the stratum does not anchor, its extension column). A point the
library cannot vouch for fails as `failed:predict` with the reason.
Predictions spend none of the spec's simulation budget and get their own
pipeline fingerprint, so they never pass for EMX measurements. The leaders,
their intervals and their footprints (from the GDS each one's pcell drew)
land in `.icopt/reports/lib_design.json`.

## 7. Sign off and grow: `lib_signoff`

```bash
ic-opt run lib_signoff <project> library=<library root> candidates=<project>/.icopt/reports/lib_design.json top=10 --plan
ic-opt run lib_signoff <project> library=<library root> candidates=<project>/.icopt/reports/lib_design.json top=10 adopt=true
```

`candidates` is a `lib_design` report, a `lib.suggest` or `lib.densify`
answer or a list of parameter dicts. Each candidate is simulated with the EMX settings of the part
that holds its turns level. The predictions are recorded before EMX runs, and
the report (`.icopt/reports/lib_signoff.json`) gives, per quantity, the
predicted value, bounds, measurement, z score and whether it fell inside.
`adopt=true` copies the ok observations, sNp included, into the part store
under fresh ids. The next dataset build includes them, and the content key
of every cache and every `predict` stage changes with it.

## 7b. Tapped twins: `lib_tap`

```bash
ic-opt run lib_tap <project> library=<library root> stratum=<stratum> \
    'taps={"primary": "same", "secondary": "same", "primary_width_um": 3, "secondary_width_um": 3, "measure": "grounded"}' \
    'window={"frequency_ghz": <f>, "ranges": {"Lp": [<lower>, <upper>], "Ls": [<lower>, <upper>], "k": [<lower>, <upper>]}}' --plan
ic-opt run lib_tap <project> library=<library root> stratum=<stratum> \
    'taps={"primary": "same", "secondary": "same", "primary_width_um": 3, "secondary_width_um": 3, "measure": "grounded"}' \
    rows=<file> adopt=<new stratum>
```

Center taps move a transformer's inductances and coupling by a few per
cent, but they cost Q. On one process, 367 tapped rows against the untapped
table's model: Lp and Ls -0.5 % at the median, within ±2.5 % for 80 % of the
rows, k +1.2 %; a tap drawn on its winding's own metal cost about 3 % of Q, a
via-stack tap about 10 %. So geometries are found on the untapped tables, and
a circuit that binds a tapped device needs that device's own S-parameters.
`lib_tap` builds them: for the rows of one window of an untapped
`clean_port_xfm_bs` or `clean_port_xfm_ms` table, the same geometry with taps,
through real EMX, row for row, adopted into the library as a new table when
asked.

`taps` says which taps: `primary` / `secondary` is `"same"` (a same-metal tap,
on the winding's own metal), a metal below the winding (a via-stack tap) or
null (no tap on that winding); the widths are optional and apply to
same-metal taps only ([devices.md](devices.md), xfm_bs). On an xfm_ms table
the single-turn primary takes either tap; the multi-turn secondary is tapped
as an inductor is, through a via stack to a metal at least two levels below
it (its crossunder holds the level in between), so its `"same"` and its width
are refused before anything is built ([devices.md](devices.md), xfm_ms: both
taps together need an odd `secondary_turns`, which the preflight shows as a
refusal of the even rows). `measure` is
required: `"grounded"`, both taps AC-grounded as a mixer uses them -- the
device's topology lists the tap ports under `grounded`. `"floating"` (taps
open) is refused for now: a topology holds every port in a drive or at 0 V.

The rows are a `window` -- every row of the stratum's index at `frequency_ghz`
(section 5d, `srf_margin` optional) whose values lie inside every range,
inclusive, in the column's unit -- or `rows=<file>`: a `lib.index out=` table
(each cell's best row), a `lib.pick` answer, or a list of `{"part": ...,
"obs_id": ...}`. A row that is not the stratum's is refused.

A twin is its row's own geometry built with the spec of the row's part, with
three changes and no other:

- the taps: the tap metals (`"same"` is the winding's metal), the widths, the
  tap ports `CTP` / `CTS` added to the device's ports and grounded;
- the ground fixture stays where the row's was: on the conductor the row's
  build recorded beside its GDS (the selected rows of a part must agree),
  under `metal_rule: free`, or `shared` when a via-stack tap's stack passes
  through that metal ([devices.md](devices.md), ground fixture). A via-stack
  tap that ends on that metal would put its port lead there, which the
  fixture refuses: an xfm_ms table built with `ground_fixture.metal: auto`
  whose primary sits right above its secondary has its fixture exactly two
  levels below the secondary, the highest metal the secondary's tap may use,
  so a twin of it taps the secondary at least one level lower (`shared`);
- this run's `threads=`, `memory_gb=` and `process_file=`, which are not
  physics: the twins are the library's generation.

The device itself is not changed to make room for a tap port. A tap port sits
between the other winding's two ports, so with a small opening its ground stub
comes closer to theirs than the fixture metal's minimum spacing allows. That
spacing is the fixture's own -- on a metal chosen under `free` it holds the
ring and the stubs and nothing else -- and the DRC gate exempts it there;
stubs that would touch are refused. Widening the other winding's port pair
instead would change the device's leads and mix their effect into the twin's
comparison with its row. Each twin's `origin` is `tap:<part>:<obs id>`, the
row it is the twin of; after adoption every tapped row names its untapped row.

The preflight runs every time and is all that `--plan` does: every twin is
drawn by the generator and passed through the pcell stage's DRC gate, on the
machine running ic-opt within its `hosts.local` entry, without EMX. A twin the
generator or the gate refuses is listed with the reason and not simulated.
The plan prints the rows per part, the taps, the fixture each part keeps, the
outcomes, the envelope (jobs, EMX threads and memory cap) and the rows' own
EMX peak memory from their `emx.log` (median, max), which is what
`memory_gb=` should cover. Real EMX: run with `--plan` first; it is the
approval point.

`<project>` is a directory with a `spec.yaml`, any valid em_only spec (a copy
of a part's will do): its store holds the twins, its
`simulator.parallel_jobs` caps the EMX runs at once and its `budget` counts
them. The clean twins run through `em_only`, one step per part
(`lib_tap:<part>`); a twin already in the store is reused. Every ok twin is
then measured with the library's definitions -- the stratum's bands, anchors
and low-frequency limit -- and compared with its row: twin / row for `Lp_lf`,
`Ls_lf`, `k_lf`, `Qp_peak`, `Qs_peak`, `SRF` and, with a window, its columns
at its frequency. `.icopt/reports/lib_tap.json` holds the call, the rows, the
preflight's outcomes, every twin's values and ratios, and the summary: per
quantity the median and the 10th and 90th percentiles of the ratio, and the
twins whose L or k moved by more than 5 %. The run's last line gives ok /
attempted, the medians and the report's path.

`adopt=<new stratum>` takes the ok twins into the library after the run;
failed ones are not adopted. Each source part's twins go into a new part
store `<library root>/<new stratum>__<part>`, with the twin spec
(`.icopt/spec.json`) and their sims directories, under fresh obs ids, their
origins kept. The new stratum takes the source stratum's definition --
generator, dims, steps, quantities with the same bands, anchors, models and
feature maps -- with the new parts and a `note` saying what it is: whose
twins, how many, the window or file, the taps, how they were measured, the
date. `library.yaml` is backed up first (`library.yaml.bak_<UTC time>`), the
entry is appended at its end, and the file is read back to check that it is
the old manifest plus exactly the new stratum; when `strata` is not the
file's last top-level block that cannot be ensured, and the entry goes to
`<library root>/<new stratum>.stratum.yaml` instead, the file left as it was.
The run then builds the new stratum's dataset and reports its rows and
exclusions. A stratum or part store of that name is refused before anything
runs.

## 7c. Refining around a run's best point: `lib_refine`

```bash
ic-opt run lib_refine <project> n=8 --plan
ic-opt run lib_refine <project> device=<id> steps=1 n=8 prefer=max:Qmin threads=<N> memory_gb=<G>
```

A circuit run whose devices come from a library table (section 8) moves
between the rows the table holds and nothing else. After such a run,
`lib_refine` measures the geometries next to the best point's row that the
table does not hold, with a few real EMX runs, and adopts them into the row's
part, so that the run's next round has rows between the table's own. Every
adopted row is a real measurement of the library's generation, and the circuit
re-optimizes on it directly: no model is corrected at the device's ports.

`<project>` is the circuit run's project. Its best point is the feasible
observation with the best objective over every step of this problem, a point
the `signoff` recipe re-checked at every corner (`signoff`, `signoff#<k>`)
first; without a feasible point the call is refused. Each library device of
the spec is refined in its own pass (`device=<id>` for one), from the row the
best point took (`library_row` in its device child: part, obs id, geometry,
values).

The neighbours are every geometry whose dims differ from the row's by at
most `steps` steps of the stratum's `steps` in `library.yaml` -- the turns dim
keeps the row's level (another turns level is another model, not a
neighbour), and so does a dim without a step -- less the row itself and every
row the table holds: at most 3^d - 1 over the d dims with a step for
`steps=1`, 5^d - 1 for `steps=2`. Each goes through `lib.query` for the
columns the device's variables map to, at the device's frequency (`Lp` is the
column `Lp@<f>`, `Qmin` the smaller of `Qp@<f>` and `Qs@<f>`, `area` the
footprint of the geometry drawn by the generator, no EMX), for the `prefer`
column and for the system `SRF`. A neighbour is kept when

- every variable's column is `predicted`: otherwise it is dropped as
  `out_of_domain`, or as `uncertain` (too uncertain, or no value); the SRF
  likewise;
- each predicted value lies inside its variable's range as the table takes a
  row onto the grid (the end levels reach half an end interval beyond the
  bounds): otherwise `outside_range`. A row adopted inside it is a candidate
  of the run;
- the predicted SRF lies above the index's margin x the frequency, the margin
  every row of the device's table satisfies (the manifest's curves', or the
  device's `srf_margin` when larger); an SRF above the sweep passes: otherwise
  `srf_margin`. A stratum without an `SRF` quantity has no such rule, and the
  plan says so.

The first rule a neighbour fails drops it, in that order, and the plan counts
the drops per rule. The kept ones are ranked by `prefer` on the predicted
value (`max:<column>` / `min:<column>` over the index's columns; default the
device's own `prefer`, else `max:Qmin`) and preflighted in that order through
the generator and the DRC gate without EMX, as `lib_tap` preflights its twins:
a refused one is listed with the reason and the next in rank takes its place,
until `n` are clean or the list runs out. Each candidate names the
combination of the device's variables its predicted values sit on, whether
the table holds that combination already (an adopted row there competes with
the rows it holds, by `prefer`) and whether the run evaluated it (an
evaluated combination keeps its observation and is not proposed again).

`--plan` prints the best point, each device's row, the local grid and what
each rule dropped, the candidates with their predicted values, combinations
and preflight outcomes, the envelope (jobs, EMX threads and memory cap), the
part's rows' own EMX peak memory from their `emx.log` (median, max: what
`memory_gb=` should cover) and the budget, and runs no EMX. Real EMX: run with
`--plan` first; it is the approval point. The models of the columns asked are
fitted first when they are not cached, under `--plan` too (the device's
frequency is often no anchor of the manifest, and a library device's run
fits no model).

The run: the clean candidates go through `em_only` into the project's store,
one step per device (`lib_refine:<part>`), with the spec of the row's part --
generator, fixed fields, EMX physics, ground fixture -- and this run's
`threads=`, `memory_gb=` and `process_file=`, which are not physics, so the
candidates are the library's generation. The project's
`simulator.parallel_jobs` caps the EMX runs at once and its
`budget.max_simulations` counts them: a shortfall is refused before any EMX
runs. The predictions are taken before EMX; each ok candidate is then
measured with the library's definitions and compared, as `lib_signoff`
compares (predicted value, calibrated bounds, measurement, z, inside), and
with the row: every index column at the device's frequency, candidate / row.
Every ok candidate is adopted into the row's part store under a fresh obs id,
with the origin `refine:<project>:<best obs id>` (the part's `adopted.yaml`
lists them); `library.yaml` is not touched. The stratum's dataset is built
again and its rows reported; a failed candidate is reported and not adopted.
`.icopt/reports/lib_refine.json` holds the call, the best point, and per device
the row, the grid, the drops, the candidates, every measured candidate's
values, ratios, z and inside, the adopted obs ids and the dataset after; one
line per device gives candidates, ok and adopted, and the row's `prefer` value
against the best adopted one. `cache_dir=` is where the library's cache files
go.

The last line says what next. The grown table is a new generation for the
next process (section 8: the index's content is part of the pipeline
fingerprint). The run continues with its own recipe and a larger budget: the
line gives the command with the project's path -- the recipe read from the
best point's step (`optimize`, `coarse_to_fine`, `signoff`), the budget the
points of its search step so far plus one per adopted row -- to which the
run's own strategy, batch and seed are added. Its observations stay (the same
spec fingerprint) and the adopted rows are candidates of its next batch.
`lib_refine` does not continue the run itself; a `lib_refine` after that round
is the next round.

## 8. Library devices in a circuit spec

A device of a circuit spec may come from a library table instead of being
drawn and simulated with EMX at every point: the table's rows are its
candidates, and every point binds a row's own sNp into the testbenches.

```yaml
# part of a circuit spec: a transformer taken from a library table
devices:
  - id: xfmr
    library:
      root: <library root>        # the directory holding library.yaml, on the machine running ic-opt (absolute)
      stratum: <stratum>          # one table
      frequency_hz: 28e9          # the working frequency the electrical values are taken at
      srf_margin: 1.5             # optional, >= 1: rows whose system SRF is at or below margin x frequency are out
      prefer: max:Qmin            # optional: which row of a combination is taken (default max:Qmin, else max:Qp)
    ports: [P1, N1, P2, N2]       # the table's port labels: the bindings and the topology use them
    variables: {Lp: xfmr.Lp, Ls: xfmr.Ls, k: xfmr.k}     # index column -> spec variable
variables:
  - {name: xfmr.Lp, kind: continuous_step, lower: 150p, upper: 600p, step: 10p}
  - {name: xfmr.Ls, kind: continuous_step, lower: 150p, upper: 600p, step: 10p}
  - {name: xfmr.k,  kind: continuous_step, lower: "0.3", upper: "0.9", step: "0.05"}
```

The device's variables are electrical values: columns of the table's index at
`frequency_hz` (section 5d: the curves `Lp`, `Qp`, and for a coupled pair
`Ls`, `Qs`, `k`; the scalars; `Qmin`; `area`). They are ordinary grid
variables, and their text may carry Spectre's scale suffixes (`T G M k m u n
p f a`: `M` is mega, `m` milli; a unit such as `pH` is refused). Each row
sits on the level nearest its own value, per variable, in the variable's
search scale (logarithmic where the range spans a decade), and only the
combinations a row sits on exist: every strategy proposes those and no
others. Two rows on one combination: `prefer` decides which one a point
takes. A finer step tells them apart -- with steps like the ones above most
combinations hold one row, so the candidates are the rows one by one; a
coarser grid is fewer candidates, each the best of its rows by `prefer`.

The rest of the spec is a circuit spec's: testbenches, the circuit's own
variables, `bindings` (a binding names each of the device's ports once, in
the instance's order; the sNp is written in that order), device metrics
(`{quantity: Lp, frequency_hz: 28e9}` and the like) beside the testbenches'.
The device's `generator`, `profile`, `fixed` fields and `plugin` are the
table's and are refused in the spec; its `topology`, when it states none,
is the table's part's (with the stratum's `low_freq_max_hz`), so its metrics
are measured as the library measures its rows. A spec's devices all come
from a library or none does in this first version. An `em` section beside
them is unused (`doctor` notes it). `library.root` says where the library
sits, not which problem this is: a library moved or mounted elsewhere keeps
the project's observations; the stratum, the frequency, the margin and
`prefer` are part of the problem.

A point runs `pick -> bind_nport -> spectre -> ocean -> extract`, and
`measure` for the device: `pick` takes the row of the point's combination and
copies its sNp next to the point (a byte copy, or its ports reordered to the
bindings' order), and `measure` computes the spec's device metrics from that
sNp. The device's child in `observations.jsonl` names the row it took
(`library_row`: stratum, part, obs id, geometry, electrical values,
footprint). No EMX runs; the budget counts the testbench simulations (a
row's measurement is none); the device's child runs first, so a point that
fails one of its constraints (an SRF, a Q) runs no simulation, unless
`simulator.stop_at_first_failure: false` (N-63). `strategy=auto` runs `metric_gp` ("library
devices: no EMX in the loop"). The digest lists per device the table, the
frequency, the rule, the combinations on the grid and how many the run
visited, and for the best points the row each one took; the report's "Best
observed" names the row, its geometry and its footprint.

`ic-opt doctor` (and every recipe's `--plan`) prints `library:<device>` with
`<stratum> at <f> GHz: <n> rows on the grid in <m> of <total> combinations,
prefer <rule>`, and the plan's point line says `no EMX runs`. It fails with
what to change: no `library.yaml` at the root, an unknown stratum (the ones
there are listed), a variable's column that is no column of the index (they
are listed), ports that are not the table's (both are said), no row on the
grid (each column's range in the index beside its variable's range), or a
frequency no row can give.

`failed:pick`: the point's combination holds no row of the table -- a point
handed in from elsewhere (a `fix_run` row, rows of another project) -- and
the message names the values and the nearest combination the table holds.
A table without centre taps cannot be bound to a tapped instance: the
device's ports must be the table's.

The library is read once per process: a run sees one table for its whole
life. A library that grows is a new generation: the pipeline's fingerprint
follows the index's content (with the grids and `prefer`), so a later run
evaluates the grown table; a combination already evaluated keeps its
observation and is not proposed again. `lib_refine` (section 7c) grows the
table around a run's best point.

## Compute

The library computes on the machine running ic-opt, within that machine's
entry `hosts.local` of `~/.ic-opt/site.yaml` (`max_threads`,
`max_memory_gb`) and nothing else: no core count is read from the machine
and no machine size is built in. The entry is read when a model has to be
fitted or a batch predicted. Datasets, coverage, measured rows and models
already cached (see [Cache](#cache)) need no entry; without one, a fit is
refused with the entry to add. `lib_design` and `lib_signoff` fit on the same entry
(`run.site.host("local")`), never on the simulation host's; `lib_design` fits
every model it needs once, before the search starts. `lib_tap` draws its
preflight there too, one twin per thread, at most `max_threads` at once, and
`lib_refine` fits, predicts and preflights there as well.

- Fitting: one worker process per uncached model, at most
  `max_threads // 2` (a fit keeps about two cores busy whatever BLAS gets),
  at most `max_memory_gb` over one fit's peak (3 x rows² x (dims + 2) x 8
  bytes for the largest model, rounded up to 0.1 GB) and at most 61 on
  Windows. A single fit whose peak exceeds `max_memory_gb` is refused, not
  run alone: raise the entry or fit fewer rows. Each worker gets
  `max_threads // workers` BLAS threads, at least one. With one worker the
  fits run in the calling process, one after another.
- Prediction (`lib.suggest`, `lib.region`, `lib.densify`): up to
  `max_threads` BLAS threads, in chunks that keep each GP call within 10% of
  `max_memory_gb` (a call over m rows holds about 7 x m x training rows x 8
  bytes). `lib.densify` also keeps one `top` x `top` covariance matrix per
  quantity within that 10%, and lowers `top` until they fit (the answer
  says so in `notes`).
- `workers=` and `threads=` cap these numbers; a value above what the entry
  allows is refused. An explicit `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS` or
  `MKL_NUM_THREADS` (the smallest of those set) only ever lowers the threads
  of every process; it is never raised.

For example, a 1300-row inductor stratum over four dims with 28 columns to
fit (0.3 GB per fit): an entry of 8 threads and 16 GB gives 4 workers of 2
threads and chunks of about 23 600 rows; 32 threads and 64 GB give 16
workers of 2 threads and chunks of about 94 400 rows; from 56 threads and
8.4 GB on, the 28 models are the bound, one worker each. Each command takes
the whole entry. A second command on the same machine, such as a local EMX
sweep, needs its own share: lower `hosts.local` or set `OMP_NUM_THREADS`
(or `OPENBLAS_NUM_THREADS` / `MKL_NUM_THREADS`) for one of them.

## Cache

Datasets, calibrations and fitted models are cached as files named after
what they are made of: the rows, the quantity definitions, the model
settings and the code. So are the extension columns (`anchors-*`, one per
stratum and frequency asked) and the rows' footprints (`footprint-*`), both
keyed by the dataset's content. A file that no longer matches is never read, and
deleting the directory only costs the time to compute it again. By default
the files go to the library's own `.cache/`. Every `lib.*` block,
`lib_design`, `lib_signoff`, `lib_tap` and `lib_refine` take `cache_dir=` to put them in another
directory, for example on a local disk:

```bash
ic-opt call lib.coverage <library root> stratum=<stratum> cache_dir=<directory>
```

When the library's own `.cache/` cannot be written (a library root shared
read-only, or a `.cache/` that another user owns), the files go to
`~/.cache/ic-opt/<key>/` instead, `<key>` being the first 16 hex digits of
the SHA-256 of the library root's resolved path. Every answer then says so
in its `notes` (`lib.load` in each stratum's entry). In both cases the files
already in the library's own `.cache/` are still read: a library its owner
has queried answers another user at once, and only what the owner never
computed is computed again, into that user's directory.

Two commands that need the same model at the same time do not both fit it.
The first holds a lock file next to the model's calibration file (its name
plus `.lock`) while it calibrates and fits; the second waits for it and then
loads what the first wrote. An extension column's measurement and a
stratum's footprints hold a lock next to their own file the same way. That holds for commands on one machine and, on a
file system that honours file locks, for machines sharing a cache
directory; where locks are not supported both fit, and the last one to
finish writes the file. The lock files are empty.

`lib.coverage` and `lib.load` answer with `cached`, one entry per quantity of the stratum's current dataset: `model` (a
query needs no fit), `calibration` (the hold-out fits are cached; the first query fits the model, minutes), `none`
(both to come) or `no rows`. Read it before a query on a library you copied: the cache is keyed by content, so one
copy is as good as another, but it only holds what was fitted where it came from -- the first query of a quantity
nobody asked for yet fits it, on the copy as on the original.

## A new process

The library is only as good as the process profile the generator drew with.
To bring up a process, write `<profile>/rule.yaml` with
[skills/author-process-rule/SKILL.md](../../skills/author-process-rule/SKILL.md)
and prove it:

```bash
ic-opt call em.validate_profile /path/to/profiles/<profile> proc=/path/to/site.proc generate=true
ic-opt call em.validate_profile /path/to/profiles/<profile> proc=/path/on/lab/site.proc --ssh-profile lab
```

`proc=` is a path on the machine running ic-opt; with `--ssh-profile` it is a
path on that host, read through SSH (its site.yaml entry gives the transfer
timeout) into a temporary directory that is deleted after the check.

This runs schema, consistency, the site proc (names, conductor thicknesses,
and every drawing and pin layer in the proc's `define` of its EMX name), and one
device per family through the generator and its DRC audit. The command
exits 1 on any failed stage. The pcell finds the profile through
`IC_OPT_PROFILE_DIRS`. The audit covers the generator's core rules only
(width, space, maximum width, via enclosure, wide-parallel spacing, port
connectivity). Via enclosure is checked on every via of the metal stack,
whatever the process calls it, unless the profile lists its own set in
`layout_rules.audited_vias`. It is not foundry sign-off DRC.
