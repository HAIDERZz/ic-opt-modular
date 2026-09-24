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
  .cache/                        # datasets, calibration and fitted models, keyed by content; safe to delete
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
    dims: [outer_diameter_um, width_um, spacing_um, turns]
    nt_dim: turns                                # the integer turns dim: one model per turns level
    parts:
      - {store: ind_sym_top}
      - {store: ind_sym_top_nt1}                 # pipeline_fingerprint: pins a generation (default: the part's most common one)
    steps: {outer_diameter_um: 1, width_um: 0.1, spacing_um: 0.1, turns: 1}   # candidate resolution for lib.suggest
    quantities:                                  # names of the measure kernel
      Lp_lf: {}                                  # low-frequency inductance
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
parts swept to different stop frequencies still answer on one basis.

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

## 3. Check it

```bash
ic-opt call lib.load <library root>                          # per stratum: rows per part, usable rows per quantity, integrity evidence
ic-opt call lib.coverage <library root> stratum=ind_sym_top  # rows per turns level, achieved range of every dim, value range per quantity
```

`lib.load` refuses a declaration error instead of answering from a partial
table: a part without observations or with more than one device, rows that
lack a declared dim, a quantity the kernel does not know, a peak band beyond
a part's sweep, a transformer quantity on a device with one port pair.

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
| `uncertain` | predicted, but sigma / mu exceeds `rel_sigma_max` (0.15 unless given): reported with its numbers, never used silently |
| `out_of_domain` | the domain guard refused: `criterion`, `reason`, the nearest measured rows |
| `above_sweep` | SRF only: most nearest rows did not resonate inside their sweep; `lower_bound` is that sweep's stop |

The domain guard's criteria: (1) inside the achieved box, and a dim that is
fixed within a turns level must match it; (2) a model exists for this turns
level (at least 25 usable rows); (3) inside the level's convex hull over the
dims that vary there; (4) sigma / mu at most `rel_sigma_max` (a parameter of
`lib.query`, `lib.suggest`, `lib.region` and `lib_design`; 0.15 unless
given). The model is a Matern 5/2 GP per turns level on log targets. Bounds
are mu +- k sigma (`k=2`) widened by
a calibration factor from held-out residuals (`max(1, q95(|z|) / 2)`), so
about 95% of held-out measurements fall inside; sigma is never reported below
the quantity's held-out median relative error, because the GP is overconfident
at the edge of the sampled box, where designs recommended for a maximum tend
to sit.

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
SRF ≥ 35 GHz) unless SRF is already constrained.

## 5b. Region questions: `lib.region`

```bash
ic-opt call lib.region <library root> stratum=<stratum> \
    'targets={"<quantity>@<f>": {"min": <a>, "max": <b>}, "<quantity>@<f>": {"min": <c>}}' \
    group_by=<dim>,<dim> trend=<quantity>:<dim>
```

Every value in angle brackets is a placeholder. For example, an inductance
window and a minimum Q at one frequency: `Lp@<f>` between `<a>` and `<b>`
henries, `Qp@<f>` at least `<c>`. An anchored column exists only at its
curve's anchors, so `<f>` must be one of the `anchors_ghz` the stratum
declares in `library.yaml` (the manifest of section 2 answers `Lp@10`,
`Lp@28`, `Qp@10` and `Qp@28`).

`lib.suggest` names a few good geometries; `lib.region` describes all of
them, the region of the stratum whose predictions meet the targets, so that
a sweep can be bounded by it. Targets are written as for `lib.suggest`. A
point is `robust` when its whole calibrated interval lies inside every
window (the `lib.suggest` test: centre a sweep there) and `mean` when its
predicted value does (the optimistic envelope). A coarse
pass over the library rows and a Sobol pool, with the stated windows 10%
wider, brackets the region. Inside the bracket the grid lies on multiples
of the manifest `steps`, about 20 values per dim (turns by level), every
step multiplied until the grid fits in `max_points` (2 million);
`steps={...}` sets steps by hand, and `grid` says what was used.

The answer gives each level's point count and per-dim ranges; `binding`,
the points meeting each target alone (the smallest count binds); `edge`,
per dim, whether the mean set reaches the library's coverage, beyond which
no model answers; `group_by`, the counts and the other dims' ranges for
every combination of the named dims; `trend`, one quantity's min, median
and max along one dim over the points meeting every other target;
`candidates` in `lib.suggest`'s shape; the `measured` rows that already
meet every target; and a `points_sample` to plot. The per-dim ranges are
projections. The dims are correlated (a width leaves a short stretch of
diameters), so take a sweep from the `group_by` rows, not from the ranges
alone. Fitted models are cached under `.cache/`; the uncached ones are
fitted in parallel processes sized from `hosts.local` (see
[Compute](#compute)). `workers` and `threads` cap the fitting processes and
the BLAS threads; a value above what that entry allows is refused.

## 6. Design on the library: `lib_design`

The same em_only spec you would optimize with EMX (device, variables,
metrics, constraints, objective) can be optimized on predictions instead:

```bash
ic-opt run lib_design <project> library=<library root> budget=200 strategy=turbo top=5
```

The pipeline is `pcell -> predict`: every point is still built by the
generator, and each device metric comes from the library (a metric such as
`{quantity: Lp, frequency_hz: 28e9}` reads the `Lp@28` column). A point the
library cannot vouch for fails as `failed:predict` with the reason.
Predictions spend none of the spec's simulation budget and get their own
pipeline fingerprint, so they never pass for EMX measurements. The leaders
and their intervals land in `.icopt/reports/lib_design.json`.

## 7. Sign off and grow: `lib_signoff`

```bash
ic-opt run lib_signoff <project> library=<library root> candidates=<project>/.icopt/reports/lib_design.json top=10 --plan
ic-opt run lib_signoff <project> library=<library root> candidates=<project>/.icopt/reports/lib_design.json top=10 adopt=true
```

`candidates` is a `lib_design` report, a `lib.suggest` answer or a list of
parameter dicts. Each candidate is simulated with the EMX settings of the part
that holds its turns level. The predictions are recorded before EMX runs, and
the report (`.icopt/reports/lib_signoff.json`) gives, per quantity, the
predicted value, bounds, measurement, z score and whether it fell inside.
`adopt=true` copies the ok observations, sNp included, into the part store
under fresh ids. The next dataset build includes them, and the content key
of every cache and every `predict` stage changes with it.

## Compute

The library computes on the machine running ic-opt, within that machine's
entry `hosts.local` of `~/.ic-opt/site.yaml` (`max_threads`,
`max_memory_gb`) and nothing else: no core count is read from the machine
and no machine size is built in. The entry is read when a model has to be
fitted or a batch predicted. Datasets, coverage, measured rows and models
already cached in `.cache/` need no entry; without one, a fit is refused with
the entry to add. `lib_design` and `lib_signoff` fit on the same entry
(`run.site.host("local")`), never on the simulation host's; `lib_design` fits
every model it needs once, before the search starts.

- Fitting: one worker process per uncached model, at most
  `max_threads // 2` (a fit keeps about two cores busy whatever BLAS gets),
  at most `max_memory_gb` over one fit's peak (3 x rows² x (dims + 2) x 8
  bytes for the largest model, rounded up to 0.1 GB) and at most 61 on
  Windows. A single fit whose peak exceeds `max_memory_gb` is refused, not
  run alone: raise the entry or fit fewer rows. Each worker gets
  `max_threads // workers` BLAS threads, at least one. With one worker the
  fits run in the calling process, one after another.
- Prediction (`lib.suggest`, `lib.region`): up to `max_threads` BLAS
  threads, in chunks that keep each GP call within 10% of `max_memory_gb`
  (a call over m rows holds about 7 x m x training rows x 8 bytes).
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

## A new process

The library is only as good as the process profile the generator drew with.
To bring up a process, write `<profile>/rule.yaml` with
[skills/author-process-rule/SKILL.md](../../skills/author-process-rule/SKILL.md)
and prove it:

```bash
ic-opt call em.validate_profile /path/to/profiles/<profile> proc=/path/to/site.proc generate=true
```

This runs schema, consistency, the site proc (names, conductor thicknesses,
and every drawing and pin layer in the proc's `define` of its EMX name), and one
device per family through the generator and its DRC audit. The command
exits 1 on any failed stage. The pcell finds the profile through
`IC_OPT_PROFILE_DIRS`. The audit covers the generator's core rules only
(width, space, maximum width, via enclosure, wide-parallel spacing, port
connectivity). It is not foundry sign-off DRC.
