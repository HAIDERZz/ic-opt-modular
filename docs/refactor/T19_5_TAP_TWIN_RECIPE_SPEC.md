# T19.5 — `lib_tap`: tapped twins of a table's rows, per window

Status: specification (2026-10-05), for the coding subagent; builds on T19.4 (same-metal center taps on `xfm_bs`,
`T19_4_SAME_METAL_TAP_SPEC.md`). Decision behind it: the user's instructions of 2026-10-02 and 2026-10-05 ("做 然后先用
同层抽头"). The library does not carry a full tapped copy of every table: taps move L and k by under 3 % (367 tapped rows
against the untapped model: Lp/Ls median −0.5 %, 10–90 percentiles ±2.5 %, k +1.2 %), so geometries are found on the
untapped tables; but taps cost Q (same-metal about −3 %, via-stack about −10 %), so a circuit that binds a tapped
device needs real tapped S-parameters. `lib_tap` builds them on demand: for the rows of one window of an untapped
table, the same geometry with taps, through real EMX, row for row, then adopted into the library as a new table.
The first time this was done by hand (2026-10-01, `ic-opt-accept/t18_ct_table/`: `ct_grid.py` → `ct_audit.py` →
`ct_build.py` → `ct_check.py` → `ct_adopt.py`); this ticket makes it a recipe.

Read before writing: `src/ic_opt/recipes/lib_signoff.py` (the closest recipe: part specs, EMX overrides, `_adopt`),
`src/ic_opt/library/index.py` (`build`, `IndexRow`, `table`, `table_json`, `row_json`), `src/ic_opt/library/dataset.py`
(`_spec`, `part_devices`, `_row`, `footprints`), `src/ic_opt/library/manifest.py` (`Library`, `Stratum`, `Part`),
`src/ic_opt/library/query.py` (`Library`), `src/ic_opt/stages/em_chain.py` (`em_only_pipeline`, `_audit`),
`src/ic_opt/em/pcell/fixture.py` (`fixture_metal`, `metal_rule`), `footprint.py` (`recorded_fixture_metal`),
`_pcell_primitives.py` (`base_lead_pair`: `PORT_SPACING`, the jog and its spacing floor), `generator_plugin.py`
(`CleanPortXfmBsConfig`: tap metals, tap widths, `primary_port_spacing_um` / `secondary_port_spacing_um`),
`src/ic_opt/recipe.py` (`Run`), `docs/em/library.md`, the hand-built scripts above, and the tests
`tests/ic_opt/test_library_xfm.py`, `test_library.py`, `test_library_footprint.py` and `tests/ic_opt/fakes.py` (the
fake EMX and how transformer parts are built in tests).

## 1. The call

```
ic-opt run lib_tap PROJECT library=<root> stratum=<untapped stratum> taps=<json> (window=<json> | rows=<file>)
    [port_room=auto|off] [adopt=<new stratum name>] [threads=N] [memory_gb=G] [process_file=/abs/path.proc] [cache_dir=DIR] --plan
```

`PROJECT` is the run's project directory (its store holds the twin observations; its spec gives the run's resources),
as for `lib_signoff`. Real EMX: `--plan` first is the approval point.

### 1.1 `taps` (JSON, required)

```json
{"primary": "same", "secondary": "same", "primary_width_um": 3, "secondary_width_um": 3, "measure": "grounded"}
```

- `primary` / `secondary`: `"same"` (a same-metal tap on that winding: the tap metal is the winding's own metal, T19.4),
  a metal spelling below the winding (a via-stack tap), or `null` (no tap on that winding); at least one tap.
- `primary_width_um` / `secondary_width_um`: optional, same-metal taps only (T19.4's rule).
- `measure`: `"grounded"` (both taps AC-grounded, as a mixer uses them: the device topology's `grounded` lists the tap
  ports) or `"floating"` (taps open). Required: it decides what the table measures.

The stratum's generator must be `clean_port_xfm_bs` (other families: refused, naming the one supported; a later ticket
extends it).

### 1.2 Which rows: `window` or `rows`

- `window` (JSON): `{"frequency_ghz": 112, "srf_margin": 1.5, "ranges": {"Lp": [7e-11, 1.6e-10], "Ls": [7e-11, 1.6e-10], "k": [0.35, 0.75]}}`
  → `index.build(library, stratum, frequency, srf_margin=...)`, then every index row whose values lie inside every
  range (inclusive, the column's unit). `srf_margin` optional (the index's own rule otherwise). Ranges over index columns
  only (refused otherwise, naming the columns).
- `rows` (a JSON file): a `lib.index out=` table (each cell's best row), a `lib.pick` answer, or a list of
  `{"part": ..., "obs_id": ...}` naming rows of the stratum (refused if any is not a row of it).

The selected rows are listed in the plan per part (count), with the window or file they came from.

### 1.3 The twin of a row

For each selected row, one twin point: the row's own geometry (its params), built with the spec of the part the row
belongs to (`dataset._spec(library / part)`: generator, plugin, profile, fixed fields, EMX physics) and these changes:

1. **Taps**: `ct_primary_metal` / `ct_secondary_metal` (`"same"` resolved to the part's `primary_metal` /
   `secondary_metal`), the tap widths, the device's `ports` extended with the tap ports in the generator's order
   (`CTP`, `CTS`), the topology's `grounded` list = the tap ports when `measure` is `"grounded"`.
2. **The ground fixture stays where the row's was**: `ground_fixture.metal` = the conductor the row's build recorded
   (`footprint.recorded_fixture_metal` on the row's GDS; every row of a part must agree, else refused naming the part);
   `metal_rule` is `"free"`, or `"shared"` when a via-stack tap's stack passes through that metal (stack positions
   between the tap metal and its winding). A same-metal tap adds no metal, so `"free"` always holds for it (T19.4). The
   point: a twin differs from its row by the taps only, not by a fixture moved to another metal (T19 record: with `auto`
   a via-stack-tapped device's fixture drops one or two metals).
3. **Room for a tap port** (`port_room=auto`, the default): a tap leaves through the other winding's port side and its
   port sits between that winding's port pair; the three ground stubs run side by side on the fixture's metal. When the
   other winding's lead gap at the port end (its port spacing minus its width; the port spacing is the row's, else the
   natural `2 x opening + width`) is smaller than `tap width + 2 x s` (s: the fixture metal's minimum spacing, from the
   process profile's rules, the same source the DRC gate reads), the twin sets that winding's port spacing
   (`primary_port_spacing_um` / `secondary_port_spacing_um`, the existing lead jog) to `width + tap width + 2 x s`
   rounded up to 0.01 µm. This is per row (a point parameter of the twin, not a fixed field), and reported per row
   ("secondary port spacing 7 → 10 µm"). `port_room=off`: never widen; such rows fail the preflight (1.4) and are listed.
   Rows from tables with an 8 µm opening never need it; rows with a 2 µm opening (the high-band parts) usually do.
4. **EMX settings**: the part's, with this run's `threads` / `memory_gb` / `process_file` (as `lib_signoff`): not physics,
   so the twin is the library's generation of measurement.

Each twin point's `origin` is `tap:<part>:<obs id>` -- the row it is the twin of; it travels into the observation and,
after adoption, into the library: every tapped row names its untapped row.

### 1.4 Preflight (always; the only work under `--plan`)

Every twin point is drawn by the generator and passed through the same product DRC gate as the pcell stage
(`stages.em_chain._audit`; factor a public helper out of it if needed, without changing its behaviour), no EMX, on the
controller within its `hosts.local` budget. Outcome per point: buildable and clean, refused by the generator (its
message), or refused by the DRC gate (the violations). Only clean points go to EMX; the others are listed with reasons
in the plan and in the report. The plan also prints: points per part, the envelope (jobs, threads, memory cap), how many
rows got a widened port spacing, and the untapped rows' own EMX peak memory (median, max, from their `emx.log`) so the
user can set `memory_gb`.

### 1.5 Run, check, report

The clean points go through `em_only_pipeline` into the run's store, one step per part (`lib_tap:<part>`). Then each ok
twin is measured with the library's own definitions (`dataset._row`, the source stratum's quantities) and compared with
its untapped row: twin / row for `Lp_lf`, `Ls_lf`, `k_lf`, `Qp_peak`, `Qs_peak`, `SRF`, and with a `window`, the window
columns at its frequency. The report `reports/lib_tap.json` has: the call, the selected rows, the preflight outcomes, per
twin the ratios and its port-spacing change, and a summary (per quantity median and 10–90 percentiles of the ratio; rows
whose L or k moved by more than 5 % flagged). One note line: ok / attempted, the medians, the report's path.

### 1.6 Adopt (`adopt=<new stratum name>`)

Only after a run with every attempted point ok or explicitly reported failed (failed points are not adopted):

- one part store per source part, `<library>/<new stratum>__<source part>`, created fresh (refused if it exists),
  holding the twin spec (`spec.json`) and the ok observations with their sims directories (`lib_signoff._adopt`'s way:
  fresh obs ids; the origin kept);
- a stratum entry `<new stratum>`: the source stratum's `generator`, `dims`, `nt_dim`, `steps`, `quantities`
  (the same bands, anchors, models, feature maps), the new parts, and `note` = what it is: "tapped twins of
  `<source stratum>` (n rows, the window or file): taps primary `<same|M?>` width w, secondary ..., measured
  `<grounded|floating>`, port room `<auto|off>` (k rows widened); lib_tap, `<date>`";
- `library.yaml`: refused if the stratum name exists. Back it up first (`library.yaml.bak_<UTC yyyymmddThhmmss>`), then
  append the entry as YAML text at the end of the file when `strata` is the file's last top-level key, and verify by
  parsing before and after that the result is the old manifest plus exactly the new stratum; when that cannot be
  ensured, leave `library.yaml` untouched, write the entry to `<library>/<new stratum>.stratum.yaml` and say so. After a
  successful insert, build the new stratum's dataset (`Library(...).dataset`) and report its rows and exclusions.

## 2. Tests (demo_6m, the fake EMX; `tests/ic_opt/test_lib_tap.py`)

Build a small transformer library the way `test_library_xfm.py` does (an untapped `xfm_bs` part on demo_6m metals, the
ground fixture on `auto`, a few geometries, one part with a small opening). Then:

1. `window` selects exactly the index rows inside the ranges; `rows` accepts a `lib.index out=` table, a `lib.pick`
   answer and a list of part / obs id; a row not of the stratum is refused.
2. `--plan` runs the preflight only: no EMX, the plan lists the points, refusals with reasons, the widened rows and the
   untapped rows' peak memory.
3. Twins: same-metal taps resolve to the winding metals; the twin's fixture is on the row's recorded metal (and
   `shared` appears only with a via-stack tap through it); the tap ports and the topology's `grounded` follow
   `measure`; `origin` is `tap:<part>:<obs id>`.
4. `port_room=auto` widens the other winding's port spacing exactly when the lead gap is below `tap width + 2 x s` on the
   fixture's metal (the narrow-opening part), and the widened twin passes the DRC gate; `port_room=off` leaves it to fail
   the preflight.
5. A run: the check report has a ratio per quantity per twin and the summary; failed points are listed, not adopted.
6. `adopt`: new part stores and stratum; `library.yaml` backed up, the new manifest equals the old plus the stratum;
   the new stratum's dataset builds and its rows carry the origins; an existing name is refused; when `strata` is not
   the last top-level key, `library.yaml` is untouched and the snippet file is written.
7. Refusals with clear messages: a non-`xfm_bs` stratum; a tap above its winding; a width with a via-stack tap; missing
   `measure`; neither or both of `window` / `rows`; a range over a non-column; a part whose rows disagree on the fixture
   metal.

The fake EMX's synthetic sNp for 4- and 6-port devices is enough for the plumbing; if a coupled-pair fake with
geometry-dependent L and k is needed for the check step, add it to `tests/ic_opt/fakes.py` beside `rlc_snp`.

## 3. Documents

- `docs/em/library.md`: a section "Tapped twins: `lib_tap`" -- why (the 3 % / Q data), the call, what a twin is
  (taps, fixture kept, port room), preflight, check, adopt; and in the recipes list wherever the other library recipes
  are listed.
- The recipe module's docstring in the style of `lib_signoff.py` (it is what `ic-opt` shows).
- `docs/refactor/BACKLOG_CN.md` section 0.12: the `抽头` row → `T19.5` done on the branch (commit, test counts).
- Section 5 of this file: the record.

## 4. Working rules for the coder

Branch `t19-5-lib-tap` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t19-5-lib-tap`, created
from main after T19.4 is merged (run `git merge --ff-only main` first and confirm that `ct_primary_width_um` exists).
Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t19-5-lib-tap/src` for every run; never `uv run`,
`uv sync` or pip. Run `tests/ic_opt/test_lib_tap.py`, the library tests (`tests/ic_opt/test_library*.py`), the pcell
tests touched by any helper you factor out, and `ruff check src tests`; all clean before committing. No real EMX, no
private profile in tests or docs, nothing under `ic-opt-library` touched. Commit trailer
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not merge or push; report the commits, the exact test
counts, and every deviation from this specification.
