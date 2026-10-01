# IC-Opt 0.7.0

0.6.0 optimized a circuit whose EM devices were drawn and simulated by EMX at
every point. This release lets a circuit take its devices from a **device
library table** instead: the rows of the table are the candidates, each point
binds a row's own measured S-parameters into the testbenches, and no EMX runs
during the search. Around it: the library answers at any frequency and with
each device's footprint, an electrical index of a table, tables of allowed
combinations in the search space, a digest that names the rows the best points
took, and a `signoff` recipe that tightens a constraint its re-check missed and
searches again. The proposal, the user's decisions and every measurement are
recorded in `docs/refactor/T18_LIBRARY_DIRECTIONS_CN.md` and the four
specifications it names. ic-opt itself still calls no language model.

Stores written by 0.6.0 are reused as they are. There is no migration step:
a spec without library devices keeps its fingerprint, and the batches every
strategy proposes on such a spec are pinned unchanged by tests.

## Changes in behaviour

Read these before upgrading a project that runs.

1. **`auto` is `metric_gp` for a spec whose devices come from a library.**
   Such a spec has no EMX in the loop, so the rule of 0.6.0 (`metric_gp` for a
   spec without EM devices, `openbox_gp_eic` with them) extends to it, and the
   line says so: `[optimize] strategy auto: metric_gp (library devices: no EMX
   in the loop)`. A spec with devices EMX simulates at every point is unchanged.
   `strategy=metric_gp` named on an EMX spec is still refused, and the message
   now names both ways out (`openbox_gp_eic`, or the device taken from a
   library table).
2. **The digest is version 4.** Every entry of version 3 stays; a `library`
   entry is added (`null` for a spec without library devices, else per device
   its table, working frequency, the ranking rule, the combinations on the grid
   and how many the run visited, and for the best feasible points the row each
   device took). `digest.md` gets a "Library devices" section only for such a
   spec.
3. **A spurious `nominal` corner is gone** from the digest's corner table and
   from `ic-opt advise --corners` on an EMX circuit spec whose device metrics
   are judged at corners: a device is measured once and counts at every corner,
   so it adds no corner of its own. Both places were wrong before.
4. **`lib.query`, `lib.region` and `lib.suggest` answers carry a `footprint`**
   (`{width_um, height_um, area_um2}` of what the generator draws, without the
   ground fixture EMX needs; `null` with a `footprint_why` when this machine
   cannot load the process profile or the generator is another plugin's). The
   block output of `lib.query` is strict JSON. A quantity may name a declared
   curve at any frequency inside the parts' sweeps (`Lp@33`), not only at the
   anchors `library.yaml` declares; the first answer at a new frequency reads
   every row's sNp once and caches the column beside the dataset
   (`anchors-<stratum>-<dataset key>-<f>.json`).
5. **A constraint's value takes no SI prefix** -- as in 0.6.0, but it matters
   more now: an inductance limit is written `90e-12 H`, not `90p H` (which the
   verdict reads as 90 H). The `signoff` recipe with `rounds` above 1 refuses
   such a limit before anything runs.

## New

- **Devices from a library table in a circuit spec**
  (`docs/refactor/T18_2B_LIBRARY_DEVICE_SPEC.md`; README, "Devices from a
  library table"; `docs/em/library.md`, "Library devices in a circuit spec").
  `devices[*].library: {root, stratum, frequency_hz, srf_margin, prefer}` with
  `variables: {<index column>: <spec variable>}`: the device's variables are
  electrical values (`Lp`, `Ls`, `k`, ...) on ordinary grids, the combinations
  that exist are the ones a row of the table sits on (the row with the best
  `prefer` column per combination, default `max:Qmin`), and a point's device
  child `Pick` takes that row's sNp, reorders its ports to the binding and hands
  it to the binding and the testbenches as before. The row's measurement is not
  a simulation (the budget does not count it); `ChildResult.library_row`
  records the row (stratum, part, obs id, geometry, electrical values,
  footprint; no path). `ic-opt doctor` prints `library:<device>` with the
  table's rows on the grid; `--plan` says `no EMX runs (the devices are library
  rows)`. The recipes take such a spec unchanged. A device the library cannot
  resolve (a port the table lacks, a column that is not in the index) is an
  error line before anything runs, in plan mode too.
- **Tables of allowed combinations in the search space**
  (`docs/refactor/T18_2A_ALLOWED_COMBINATIONS_SPEC.md`): some variables may
  take only the combinations a table lists. `space.snap`, `space.check` and the
  grid size honour them, every strategy proposes valid points only
  (`metric_gp`'s design, candidates, region and advice included), `points.*`
  hand out valid points, and a batch short of candidates is completed from the
  valid points not yet taken (origin `fill`). Without tables nothing changes.
- **The electrical index** (`docs/refactor/T18_1_LIBRARY_INDEX_SPEC.md`):
  `ic-opt call lib.index LIBRARY stratum=S frequency_ghz=F 'grid={...}'` lists
  a table's rows by their measured electrical values at one frequency (rows
  whose system SRF lies within the margin of the frequency are dropped and
  counted), one cell per occupied grid combination; `lib.pick` returns the rows
  nearest to target values with geometry, footprint and sNp.
- **`signoff rounds=N tighten=T`** (`docs/refactor/T18_4_TIGHTEN_RECIPE_SPEC.md`):
  when the re-check at every corner finds no feasible point, each constraint
  its best point misses is tightened by `T × miss` (an upper limit down, a
  lower limit up, from the limit that round searched under, so the moves add
  up), and the search at `corner` runs again with `budget` more points on a
  copy of the spec with those limits, handed the earlier searches' rows; the
  new best points are re-checked under the constraints as written. The rounds
  end on a feasible point, after N searches, or when a round cannot go on (no
  feasible search point, nothing to tighten, two-sided limits that would
  cross). `rounds=1` is the recipe as it was, byte for byte. The spec file is
  never rewritten; `.icopt/reports/signoff_rounds.json` holds every round.
- **`skills/ic-opt`** gained the library-device form of a spec and the
  `signoff` rounds (the cheatsheet and step 6).

## Measured

Every number below comes from the user's private device library and
transformer testbenches on Spectre; the tables, geometries and process are not
part of this repository. Details: `docs/refactor/T18_LIBRARY_DIRECTIONS_CN.md`,
sections 9 to 12.

- **A one-transformer measurement fixture, 100 points, 3 minutes, no EMX**:
  the fixture's Lp, Ls, Qp, Qs and k at 60 GHz, read from the Z-parameters
  after the row's sNp went through `Pick`, the port reordering, the binding and
  Spectre, equal the library's own values of the row to 1e-7 relative (the L
  difference is entirely the `3.141593` in the OCEAN expression). The search
  found the best row of the constraint window (68 rows of the table) at its
  37th point; 47 of 100 points were feasible, every point a different row.
- **Refilling the table near the optimum**: ten rows predicted by the library
  model and signed off with EMX fell inside the model's calibrated interval 83
  % of the time against a target of 95 %: candidates chosen at the model's most
  optimistic values measure lower (a winner's-curse effect the intervals do not
  account for). Refilling where the table is already dense did not move the
  optimum.
- **On the private tables at steps of 10 pH, 10 pH and 0.05**, a transformer
  table of about 1 700 rows occupies about 1 400 grid combinations, one row in
  most of them: with such steps a device's candidates are its rows one by one.
- **Not adopted, measured and recorded** (`T17_OPTIMIZER_PLAN_CN.md`,
  section 7): an "independent fifth" of each batch free of the advice in effect
  (worse under a wrong advice without review, within noise with it); the
  schedule's threshold was re-measured at 26 and 42 simulations per point (the
  stop better in 13 of 18 at both), so the default of 20 stands; the +1 thread
  per testbench job was re-measured (the count stays; the reason is corrected
  in the README).

## Known limits

- The `signoff` rounds ran against fake simulators and, in plan mode, on a real
  library-device project; they have not run on a real corner spread, since no
  library-device platform with corners exists yet.
- The library's calibrated intervals do not account for the selection of
  candidates by their predicted best value (83 % coverage against 95 % on the
  ten rows above).
- `lib.suggest` draws its candidate pool at random over the whole table; a
  narrow target window may get few or no candidates with the default pool and
  interval (ten were reached with `k=1.0, pool_size=65536`).
- Only metric expressions from the testbench's own quantities are meaningful on
  a measurement fixture (ideal baluns between 50-ohm ports): S-parameters of an
  unmatched device are not a figure of merit there.
- A comparison of the library-device search against a search that runs EMX at
  every point has not been made.
