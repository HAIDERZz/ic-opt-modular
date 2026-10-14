# IC-Opt 0.9.0

0.8.1 fixed what two uses of ic-opt found. This release is about the device-query library answering what a designer
asks: a value a table does not reach is now looked for in every table of that device kind, an answer a little outside
the measured range is given and labelled instead of refused, and the answers that were wrong or crashed are gone. It
also makes three failure paths say what happened, and checks two more ways a centre tap can be drawn too close to the
other winding. Each item has its specification and record under `docs/refactor/` (`N101_103_ADVISING_SMALL_ITEMS_SPEC.md`,
`N105_MEASURED_ABOVE_SWEEP_SPEC.md`, `N106_REGION_COMBINATIONS_SPEC.md`, `N93_N94_N53_SMALL_ITEMS_SPEC.md`,
`N113_ABOVE_RESONANCE_SPEC.md`, `N114_FAMILY_RECOMMEND_SPEC.md`, `N116_EXTRAPOLATION_SPEC.md`, `N119_FAILURE_MESSAGES_SPEC.md`,
`N120_FEW_ROWS_COLUMNS_SPEC.md`, `N121_XFM_MS_TAP_GAP_SPEC.md`); the studies behind them are N-115 (how far outside the
measured range the models hold) and N-117 (a space-filling batch on one table). ic-opt itself still calls no language
model.

Stores written by 0.8.1 are read as they are and there is no migration step: every spec fingerprint recorded by earlier
releases is recomputed unchanged (27 real projects), and the pipeline fingerprints are unchanged except one, item 1's
`lib_design` with extrapolation.

## Changes in behaviour

Read these before upgrading a project that runs or a script that reads the library's answers.

1. **The library answers a little outside the measured range, along the outer diameters, labelled** (N-116). Along
   `outer_diameter_um`, `primary_outer_diameter_um` and `secondary_outer_diameter_um` -- and no other dim -- a point up to
   `min(0.15 x range, 0.15 x boundary)` beyond a model's measured range is answered, its hull test judged at the point
   clamped back into the range. The answer keeps its status and gains `extrapolated: {dim: {side, beyond, allowance,
   measured_range}}`; its interval is widened with the distance where N-115's held-out edges showed coverage below 95 %
   (`k_lf`, SRF, the Q peaks, the k curves). `lib.suggest`'s and `lib.region`'s pools and grids reach as far, so a design
   just outside a table can be proposed, marked; a family request's reason says "even with up to 15 % extrapolation of
   the outer diameter" when a table still cannot. `extrapolate=0` on `lib.query`, `lib.suggest`, `lib.region`,
   `lib.densify` and `lib_design` gives 0.8.1's answers field for field. Two consequences: the candidates `lib.suggest` and
   `lib.region` return inside the range may differ from 0.8.1's, since the pool spans a wider box; and `lib_design`
   (default `extrapolate=0.15`) names the extrapolation in its prediction stage's identity, so a `lib_design` store
   written earlier predicts its points once more (the earlier rows stay as history).
2. **No curve answer above the device's own resonance** (N-113). `<curve>@<f>` is answered only where the resonance
   predicted at the point lies above the curve's `srf_margin x f` -- where a measured row has a value for that column.
   Below it: the new status `above_resonance` (no value, the resonance and the threshold given); near it: the answer with
   `near_resonance`; where the resonance model refuses the point: `out_of_domain` with its reason. `suggest.predict_all`
   holds every anchored column to the same rule. 0.8.1 answered, for instance, a coupling coefficient of 1.02 at 170 GHz on
   a transformer resonating at 163 GHz.
3. **A column with too few usable rows is reported, not fitted into a crash** (N-120). Where its fit or its 5 x 20 %
   hold-out would raise, the column answers `no_model` with its row count, `lib.cached` lists it as `too few rows (<n>)`,
   and `lib.suggest`, `lib.region` and `lib.densify` refuse a target on it. In 0.8.1, `lib_signoff` and a `lib.query`
   without named columns crashed on the multi-turn transformer tables whose 112-170 GHz columns have 0-4 rows.
4. **A device's SRF metric with no resonance inside its sweep is a lower bound** (N-94). Where the reactance stays
   inductive up to the sweep's last frequency, `SRF`, `SRF_p` or `SRF_s` takes that frequency as its value and the child
   stays `ok`, with the issue `metric <name>: no resonance inside the sweep up to <X> GHz; reported as <X> GHz, a lower
   bound`. A minimum is judged against the bound; a maximum says nothing about such a device. Before, the point failed
   (`failed:measure`) although its device resonates highest of all.
5. **One run per project, refused in one line** (N-119). `ic-opt run` takes `.icopt/run.lock` before anything writes to
   the store and holds it to the end; a second run on the same project ends at once with exit code 2 and `error: project
   is locked by another run: <path> (pid <n>)`. Before, it ran the doctor and imported the netlists first and then failed
   with a traceback. `--plan` takes no lock and now writes nothing (it used to append a `doctor` row to `steps.jsonl`).
6. **Ctrl-C prints one line** (N-119): `interrupted: <r> points recorded, <s> stopped, <n> not started; the same command
   continues the run`, exit code 130, nothing else changed.
7. **A failed Spectre run says why** (N-119): the first `ERROR` entry of `psf/spectre.out` (else of Spectre's output), on one
   line of at most 300 characters with long quoted paths written `.../<file name>`, comes before `spectre exited <code>`
   in the child's issues. An OCEAN run that wrote no scalars carries its log's first error the same way.
8. **Two more centre-tap checks** (N-93, N-121). `clean_port_xfm_bs` and `clean_port_xfm_ms` refuse, before the fixture and
   the DRC gate, a via-stack tap whose pad (or lead) on the other winding's metal sits closer to that winding than the
   profile's spacing for those widths over that run, naming the gap, the run, the rule, the widths and the opening;
   `xfm_balun`'s existing inter-net refusal names those numbers when the tap is what comes too close. Every build that
   passed before builds the same bytes (the goldens and a replay of every library build checked); the refused ones were
   refused by the DRC gate before, later and without numbers.

## New

9. **One recommendation request for a device kind** (N-114). `lib.suggest` and `lib.region` take `family=ind_sym | xfm_bs
   | xfm_ms` (with `taps=none | any | only`) or `strata=a,b,...` instead of `stratum=`: every table of that kind is asked,
   the best designs are merged and each is labelled with its table's port opening, metals and taps, and every table without
   a design says why -- a target outside its measured range (with the range), targets each reachable but not together
   (with the closest measured row), or a request it cannot take. `lib.suggest` draws a second pool from `lib.region`'s grid
   when its Sobol pool meets nothing (N-50).
10. **A pcell device's allowed combinations from a file** (N-106): `devices[*].combinations: FILE` limits the points a run
    proposes to the combinations listed (part of no identity); `lib.region ... out=FILE` writes a level's grid points as
    such a file and answers with the device and variables blocks to paste.
11. **`lib.region` counts a measured row with no resonance in its sweep** as meeting an SRF minimum up to the sweep's end
    (N-105), with `above_sweep` naming it.
12. **Each finished child keeps its trace** as `trace.json` in its directory: the commands it ran, the nport binding's
    rewrite of the netlist, the OCEAN timing (N-53).
13. **Smaller items**: `points.md` of the digest carries every spec variable (N-101); a start file's duplicate rows are
    said in one line (N-103); the ic-opt skill revokes an advice that holds only start rows once they have run (N-102).

## Checks

The whole test suite in 12 groups (with the recorded runs), `ruff`, the pcell suite without the private profile, a clean
install of the wheel, and the spec fingerprints of 27 real projects recomputed: see the release record
(`ic-opt-accept/release_v0.9.0/RELEASE_RECORD_CN.md`, outside the repository).
