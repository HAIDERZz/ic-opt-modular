# T18.2B — a device of a circuit spec comes from the library: its rows are the candidates, no EMX in the loop

Status: specification (2026-10-01), for the coding subagent; starts after `T18.1` (`T18_1_LIBRARY_INDEX_SPEC.md`) and
`T18.2A` (`T18_2A_ALLOWED_COMBINATIONS_SPEC.md`) are merged. Where this text names a function of those two tasks, the
merged code is the authority: read it and use what is there. Decision behind it: the user's answers of 2026-10-01 to
`T18_LIBRARY_DIRECTIONS_CN.md` section 7 (question 1: rows are the candidates).

What it gives. Today a circuit spec with an EM device draws the device and runs EMX for every point
(`em_circuit_pipeline`). After this task a device may instead say it comes from a library table: its variables are
electrical values (an inductance, a coupling coefficient) on ordinary grids; the combinations that exist are the ones
a library row sits on; evaluating a point binds that row's real sNp into the testbenches and runs Spectre. Every
observation is a real measurement, no EMX runs, and the strategy, the schedule and the verdict are untouched.

Read before writing: `src/ic_opt/spec.py` (Device, Binding, `_cross_references`, `problem`, the two fingerprints),
`src/ic_opt/stages/em_chain.py`, `src/ic_opt/library/{index,link,stage}.py`, `src/ic_opt/space.py`,
`src/ic_opt/blocks/{evaluate,doctor,optimize}.py`, `src/ic_opt/eval/{engine,stage}.py`, `src/ic_opt/observation.py`,
`src/ic_opt/digest.py`, `src/ic_opt/blocks/analyze.py`, `src/ic_opt/suggesters/{__init__,metric_gp/__init__}.py`,
`src/ic_opt/em/{touchstone,nport,measure}.py`.

## 1. The spec

```yaml
devices:
  - id: xfmr
    library:
      root: /path/to/library          # the directory holding library.yaml, on the machine running ic-opt
      stratum: <table>                # one table
      frequency_hz: 28e9              # the working frequency the electrical values are taken at
      srf_margin: 1.5                 # optional, >= 1: rows whose system SRF is at or below margin x frequency are out
      prefer: max:Qmin                # optional: which row of a combination is taken (the index's rule and default)
    ports: [P1, N1, P2, N2]           # the labels the bindings and the topology use: the table's ports
    variables: {Lp: xfmr.Lp, Ls: xfmr.Ls, k: xfmr.k}     # index column -> spec variable
variables:
  - {name: xfmr.Lp, kind: continuous_step, lower: 150p, upper: 600p, step: 10p}
  - {name: xfmr.Ls, kind: continuous_step, lower: 150p, upper: 600p, step: 10p}
  - {name: xfmr.k,  kind: continuous_step, lower: "0.3", upper: "0.9", step: "0.05"}
```

- `Device.library: LibrarySource | None`. With it: `generator`, `profile`, `fixed` and a non-default `plugin` must be
  absent (they are the table's; refuse with that sentence); `ports` and a non-empty `variables` are required; a
  `topology` may be stated and otherwise is the table's (section 2), so the load-time defaulting and its port check
  are skipped for a library device that states none. Without it, `generator` and `profile` are required as today
  (the two fields become optional in the model; the refusal names the device and both ways).
- A spec's devices are all library devices or none is: a mixture is refused at load in this first version. An `em`
  section beside library devices is allowed and unused; `--plan` says so in one line.
- Identity. `Device` leaves `library` out of its dump while None, so every existing spec keeps its dump and both
  fingerprints (the pinned tests stay green; add one that pins a spec with an EMX device before and after).
  `library.root` says where the library sits, not which problem this is: it is left out of `Spec.problem()` (as
  `em.binary` is); the stratum, the frequency, the margin and `prefer` are part of the problem.
- The electrical variables are ordinary grid variables; their text may carry a Spectre scale suffix. A new
  `space.si_value(text) -> float` reads it (`T G M k m u n p f a`, as Spectre does: `M` is mega, `m` milli; anything
  else is refused); a generator field still refuses a suffix. They are consumed by the device
  (`Spec.device_fields`), so they never reach a netlist.

## 2. Resolving a library device (`ic_opt/library/link.py`, replacing T18.2A's stub)

```python
def tables(spec: Spec) -> list[space.Table]: ...          # what space.tables(spec) returns
def resolve(spec: Spec) -> dict[str, Linked]: ...          # device id -> the index, the table, the rows by combination
def row_for(spec: Spec, device_id: str, params: dict[str, str]) -> index.IndexRow | None: ...
def identity(spec: Spec) -> str: ...                       # per device: the index's content key, the grids, prefer
def clear() -> None: ...                                   # forget what this process resolved (tests)
```

- Per device: open the library (`query.Library`, no calibration, no limits needed: nothing is fitted), build the
  index at `frequency_hz` with `srf_margin` (`index.build`), the table on the grid of the device's variables read in
  SI units (`index.table`, `prefer`), and the `space.Table` over those variables in the spec's order with the
  occupied combinations. The topology of a device that states none is the part's, with the stratum's
  `low_freq_max_hz` when the manifest sets one, so the device's metrics are measured as the library measures them.
- Refusals, each a `ValueError` a user can act on: no `library.yaml` at the root; an unknown stratum (list them); a
  variable's coordinate that is no column of the index (list them); the device's `ports` not the table's (say
  both); no row on the grid (say each coordinate's range in the index beside the variable's range).
- A process resolves a device once and keeps the answer: a run sees one table for its whole life (say so in the
  docstring). The key of what is kept: the resolved root, the stratum, the frequency, the margin, `prefer`, the
  variables' grids.
- The library's cache files go where `cache.locate` puts them, so a read-only shared library works.

## 3. The stage that takes the row (`Pick`, point level)

- `Pick(spec)`: `name = "pick"`, `level = "point"`, no resources beyond a thread, `runs = 0` (no EMX run), never
  cached by the engine (`fingerprint` None), `identity = link.identity(spec)` so the pipeline fingerprint follows
  the library's content, the grids and `prefer`.
- `run(point, ctx) -> Geometry`: for each device the row of the point's combination (`link.row_for`). No row -- a
  point handed in from elsewhere, or a library that changed -- is a `StageFailure` naming the device, the values and
  the nearest combination: the point's status is `failed:pick`.
- The row's sNp goes to `<workdir>/em/<device>/<device>.s<N>p` with its ports in the order the bindings take them
  (`snp_order(spec, device)`). The same order as the file's: a byte copy. Another order: the S matrices are permuted
  (rows and columns) and written by a new `touchstone.write` (version 1, full matrix, the number format of
  `touchstone.format_number`, the read file's frequency unit, format and reference impedance; `read(write(x))`
  gives `x` back to the text's precision). `Geometry.sparams[device]` is filled as the EMX stage fills it, so
  `BindNport`, Spectre, OCEAN and `Extract` run unchanged.
- `Geometry` gains `picks: dict[str, dict]`: per device the stratum, the part, the obs id, the row's geometry, its
  electrical values, its footprint and the combination's values as text; also written to `<workdir>/em/pick.json`.
- The device child (`Measure`) computes the spec's device metrics from that sNp as it does after EMX. A library
  pipeline always has the device child, metrics or not, and its `ChildResult` carries `library_row` -- a new
  optional field, left out of the dump while None so that every other child is written byte for byte as before --
  with the stratum, the part, the obs id, the geometry and the footprint (no path).

## 4. Pipelines, checks, plan, strategy

- `library_circuit_pipeline(spec, deck, waveforms)`: `Pick`, `BindNport`, Spectre, OCEAN, `Extract`, `Measure`;
  `library_only_pipeline(spec)`: `Pick`, `Measure`. `blocks.evaluate.default_pipeline` returns them for a spec whose
  devices are library devices; every recipe (`optimize`, `signoff`, `coarse_to_fine`, `fix_run`) then works on such
  a spec unchanged.
- `env.doctor`: for a library device no generator / profile check and none of the EMX checks; instead
  `library:<device>`, ok with "`<stratum>` at `<f>` GHz: `<n>` rows on the grid in `<m>` of `<total>` combinations,
  prefer `<rule>`", failing with the resolution's message. The envelope counts no EMX job. `--plan` prints the same
  sentence per device and "no EMX runs".
- `metric_gp` takes a spec whose devices are all library devices: `stage_one_refusal` / `_refuse` refuse only a
  device simulated in the loop, and the refusal says both ways out (name `openbox_gp_eic`, or take the device from
  the library). `resolve_auto` gives `metric_gp` with the reason "library devices: no EMX in the loop".
- The schedule needs nothing: the device child is cheap and its constraints (an SRF, a Q) are known at once, so
  with the stop on it runs first and a point that fails a device constraint runs no simulation. A test shows it.
- The budget counts what it counts today: the testbench simulations.

## 5. What a reader sees

- Digest (version 4; every entry of version 3 stays): `library`, per device the table, the frequency, the rule, the
  combinations on the grid and how many the run visited; for the best feasible point and each of the top points the
  row taken (part, obs id), its geometry, its electrical values and its footprint. `digest.md` gets a section for
  it. The leak test of T17.10 is extended: no path -- the library's root, an sNp file -- reaches the digest.
- `report.md`: "Best observed" names, per library device, the row, the geometry and the footprint.
- The skill's description of `observations.jsonl` gains `library_row`.

## 6. Documents

- `README.md`: the devices section shows the library form beside the generator form, and the `auto` sentence.
- `docs/em/library.md`: a section "Library devices in a circuit spec" -- the block above with placeholders, what the
  grid means (two rows on one combination: `prefer` decides; a finer step tells them apart), what `failed:pick`
  means, that a table without centre taps cannot be bound to a tapped instance, that a library that grows is a new
  generation (the pipeline fingerprint changes; a combination already evaluated keeps its observation).
- `skills/author-spec/SKILL.md`: the device's library form, when to choose it over EMX in the loop, a complete small
  example; `skills/ic-opt/SKILL.md`: the plan line, the `library:<device>` check, reading the rows in the digest.
- No name, number, layer or path of a real process anywhere.

## 7. Tests (fake Spectre, the library fixtures; no simulator, no EMX)

1. The spec: the forms accepted and refused (generator and library both; neither; a mixture of device kinds; a
   library device with `fixed`); existing fingerprints unchanged; `library.root` outside the problem; `si_value`.
2. `link`: the table of a fixture library on a grid; each refusal; one resolution per process and `clear`.
3. `touchstone.write`: round trip; a permuted file read back equals the permuted matrices.
4. `Pick`: byte copy in the file's order; permuted otherwise (the bound netlist's instance sees the right ports: check
   through `Measure` that the device's L and k are what the row's are); `pick.json`; `failed:pick` off the table.
5. `sim.evaluate` end to end on a circuit spec with one library device bound into a testbench: the rendered netlist
   points at `models/<device>.s<N>p`; the observation holds the testbench's metrics, the device's metrics measured
   from the row's sNp, and `library_row`; no `emx.cmd` anywhere; `engine.point_runs(pipeline) == 0`.
6. `opt.optimize` with `strategy=auto`: the line says `metric_gp`; every point of several batches is a valid point;
   the same seed twice gives the same points; the plan lines; the doctor check, passing and failing.
7. The schedule: with the stop on and a history in which a device constraint fails often, the device child runs first
   and the point stops there.
8. Digest and report entries; the leak test.
9. The documents' examples load (`tests/ic_opt/test_skill_author_spec.py`, `test_library_docs.py` style).

Targeted run: the new test files, `tests/ic_opt/test_em_*.py`, `test_library*.py`, `test_spec*.py`, `test_metric_gp.py`,
`test_space_tables.py`, `test_digest*.py`, `test_cli_recipes.py`, `test_schedule.py`, the skill tests; then
`ruff check src tests`.

## 8. Working rules for the coder

- Branch `t18-2b-library-device` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t18-2b-library-device -b t18-2b-library-device main`);
  commits on that branch only; never touch `main`, never push.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `import ic_opt` resolves to the worktree); never `uv run`, `uv sync`, `pip`. No simulator, no network, no
  file of the user's private library.
- Tests count as load on a machine that runs the user's simulations: `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
  MKL_NUM_THREADS=1`, at most two test processes at a time, never the whole suite, never `-n`.
- One commit per coherent piece (the spec model; link; write + Pick + pipelines; checks, plan and strategy; digest
  and report; documents), each message saying what and why; trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); what the specification left
  open and how it was read (a numbered list); what could not be done and why.
