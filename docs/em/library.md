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
  .cache/                        # datasets and calibration, keyed by content; safe to delete
```

The blocks take the library root as their directory: `ic-opt call lib.<name>
<library root> key=value ...`. Dict answers print as JSON.

## 1. Build the parts (real EMX)

A part is an em_only project: one device (`generator`, `profile`, `fixed`
fields), `variables` that become the library's dims, an `em:` section, and a
budget large enough for the sweep. Sweep it with a few-line recipe:

```python
# sweep.py -- space-filling points on this part
from ic_opt import blocks as b


def main(run, n: int = 200, seed: int = 0):
    points = b.points_sobol(run.spec, int(n), seed=int(seed))
    b.evaluate(run.spec, points, run.executor, run.store, step="sweep", cshrc=run.cshrc, parallel_jobs=run.jobs, site=run.site)
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

For a transformer, query `SRF` rather than `SRF_p` / `SRF_s`: the secondary's
resonance reflects into the primary's impedance as a sharp dip, and whether
that dip crosses zero decides whether `SRF_p` lands on it or on the primary's
own, much higher resonance. Two neighbouring geometries can differ by tens of
GHz in `SRF_p` while `SRF` stays continuous. For the same reason every
anchored curve of a coupled pair is used only below the system SRF.

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
| `uncertain` | predicted, but sigma / mu exceeds 0.15: reported with its numbers, never used silently |
| `out_of_domain` | the domain guard refused: `criterion`, `reason`, the nearest measured rows |
| `above_sweep` | SRF only: most nearest rows did not resonate inside their sweep; `lower_bound` is that sweep's stop |

The domain guard's criteria: (1) inside the achieved box, and a dim that is
fixed within a turns level must match it; (2) a model exists for this turns
level (at least 25 usable rows); (3) inside the level's convex hull over the
dims that vary there; (4) sigma / mu at most 0.15. The model is a Matern 5/2
GP per turns level on log targets. Bounds are mu +- k sigma (`k=2`) widened by
a calibration factor from held-out residuals (`max(1, q95(|z|) / 2)`), so
about 95% of held-out measurements fall inside.

## 5. Inverse questions: `lib.suggest`

```bash
ic-opt call lib.suggest <library root> stratum=ind_sym_top \
    'targets={"Lp_lf": {"target": 1.2e-9, "tol": 0.03}, "SRF_p": {"min": 60e9}}' objective=max:Qp_peak n=5
```

Targets are per quantity, in SI units: `{"min": v}`, `{"max": v}` or
`{"target": v, "tol": relative}`. The answer lists `measured` designs that
meet the targets (exact, already simulated) apart from `candidates`
(interpolated geometries snapped to `steps`). A candidate must pass the domain
guard, meet every target with its whole calibrated interval, and build: the
real generator draws it and the product DRC audit checks it. An anchored
target such as `Lp@28` adds `SRF >= 1.25 x 28 GHz` unless SRF is already
constrained.

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

Fitting shares the host with EMX: cap BLAS threads for query and suggest
work next to a running sweep (`OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
MKL_NUM_THREADS=4`).
