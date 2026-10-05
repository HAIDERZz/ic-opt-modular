# T19.5 — `lib_tap`: tapped twins of a table's rows, per window

Status: specification (2026-10-05; section 1.3 item 3 and 1.7 revised after the T19.4 record and a dry run on the
user's profile), for the coding subagent; builds on T19.4 (same-metal center taps on `xfm_bs`,
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
`drc_audit.py` (`fixture_exemptions`, `product_scope_record`, the finding kinds), `generator_plugin.py`
(`CleanPortXfmBsConfig`: tap metals, tap widths), the T19.4 record (section 5 of `T19_4_SAME_METAL_TAP_SPEC.md`),
`src/ic_opt/recipe.py` (`Run`), `docs/em/library.md`, the hand-built scripts above, and the tests
`tests/ic_opt/test_library_xfm.py`, `test_library.py`, `test_library_footprint.py` and `tests/ic_opt/fakes.py` (the
fake EMX and how transformer parts are built in tests).

## 1. The call

```
ic-opt run lib_tap PROJECT library=<root> stratum=<untapped stratum> taps=<json> (window=<json> | rows=<file>)
    [adopt=<new stratum name>] [threads=N] [memory_gb=G] [process_file=/abs/path.proc] [cache_dir=DIR] --plan
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
3. **The device is not changed to make room for the tap port.** A tap leaves through the other winding's port side
   and its port sits between that winding's port pair, so its ground stub runs between theirs on the fixture's metal.
   With an 8 µm opening the stubs are far apart; with the 2 µm opening of the high-band parts they are 0.5 µm apart --
   below a thick fixture metal's minimum spacing (dry run on the user's profile, 2026-10-05: same-metal-tapped OD 36 /
   W 4 rows fail the DRC gate on four `min_space` findings on the fixture metal and nothing else). Those stubs belong to
   the measurement fixture, not to the device: widening the other winding's port spacing would change the device's leads
   and mix a lead effect of the same size into the twin's comparison with its row. Instead two product changes (section
   1.7): the DRC gate exempts spacing among the fixture's own shapes where the fixture's metal holds nothing else, and the
   fixture refuses stubs that touch or overlap one another.
4. **EMX settings**: the part's, with this run's `threads` / `memory_gb` / `process_file` (as `lib_signoff`): not physics,
   so the twin is the library's generation of measurement.

Each twin point's `origin` is `tap:<part>:<obs id>` -- the row it is the twin of; it travels into the observation and,
after adoption, into the library: every tapped row names its untapped row.

### 1.4 Preflight (always; the only work under `--plan`)

Every twin point is drawn by the generator and passed through the same product DRC gate as the pcell stage
(`stages.em_chain._audit`; factor a public helper out of it if needed, without changing its behaviour), no EMX, on the
controller within its `hosts.local` budget. Outcome per point: buildable and clean, refused by the generator (its
message), or refused by the DRC gate (the violations). Only clean points go to EMX; the others are listed with reasons
in the plan and in the report. The plan also prints: points per part, the envelope (jobs, threads, memory cap) and the
untapped rows' own EMX peak memory (median, max, from their `emx.log`) so the user can set `memory_gb`.

### 1.5 Run, check, report

The clean points go through `em_only_pipeline` into the run's store, one step per part (`lib_tap:<part>`). Then each ok
twin is measured with the library's own definitions (`dataset._row`, the source stratum's quantities) and compared with
its untapped row: twin / row for `Lp_lf`, `Ls_lf`, `k_lf`, `Qp_peak`, `Qs_peak`, `SRF`, and with a `window`, the window
columns at its frequency. The report `reports/lib_tap.json` has: the call, the selected rows, the preflight outcomes, per
twin the ratios, and a summary (per quantity median and 10–90 percentiles of the ratio; rows
whose L or k moved by more than 5 % flagged). One note line: ok / attempted, the medians, the report's path.

### 1.6 Adopt (`adopt=<new stratum name>`)

Only after a run with every attempted point ok or explicitly reported failed (failed points are not adopted):

- one part store per source part, `<library>/<new stratum>__<source part>`, created fresh (refused if it exists),
  holding the twin spec (`spec.json`) and the ok observations with their sims directories (`lib_signoff._adopt`'s way:
  fresh obs ids; the origin kept);
- a stratum entry `<new stratum>`: the source stratum's `generator`, `dims`, `nt_dim`, `steps`, `quantities`
  (the same bands, anchors, models, feature maps), the new parts, and `note` = what it is: "tapped twins of
  `<source stratum>` (n rows, the window or file): taps primary `<same|M?>` width w, secondary ..., measured
  `<grounded|floating>`; lib_tap, `<date>`";
- `library.yaml`: refused if the stratum name exists. Back it up first (`library.yaml.bak_<UTC yyyymmddThhmmss>`), then
  append the entry as YAML text at the end of the file when `strata` is the file's last top-level key, and verify by
  parsing before and after that the result is the old manifest plus exactly the new stratum; when that cannot be
  ensured, leave `library.yaml` untouched, write the entry to `<library>/<new stratum>.stratum.yaml` and say so. After a
  successful insert, build the new stratum's dataset (`Library(...).dataset`) and report its rows and exclusions.

### 1.7 Two product changes in the ground fixture's rules

1. **Spacing among the fixture's own shapes is exempt where its metal holds nothing else.** `drc_audit.fixture_exemptions`
   gains the build's `metal_rule`: when the fixture's metal was chosen (`ground_fixture.metal` set, `auto` or a name)
   under `metal_rule: free`, the device draws nothing on that metal (T19.1's rule), so every shape there is the ring or a
   stub, and a spacing finding there (`min_space`, `wide_parallel_spacing`) is between two fixture shapes: exempt, as
   `max_width` already is. Under `shared` (device shapes on the same metal) only `max_width` stays exempt, as today; with
   `metal` absent (the bottom metal, where a patterned ground shield is a real, fabricated structure tied to the ring)
   nothing changes either. `stages.em_chain._audit` passes the rule (from the build: the manifest's
   `config.ground_fixture.metal_rule`, absent = `free`, or add it to `GeometryGenerationResult` beside `fixture_metal`).
   Every existing row stays what it is (an exemption only lets more geometries through the gate); update T19.1's
   wording (`FIXTURE_METAL_NOTE`, `fixture_exemptions`' docstring, the T19.1 test that drew a min-space violation on the
   fixture metal: under `free` it now passes; keep a test that under `shared` a device shape too close to a fixture shape
   still fails).
2. **Stubs must not touch.** Two stubs that touch or overlap make one edge holding two `G` pins, which EMX refuses (it
   does not allow two ports' pins on one edge). `fixture.add_ground_fixture` already lays its shapes out before drawing
   (T19.2): refuse there, with a `PortError` naming the two ports and the gap, when two stubs touch or overlap (gap <= 0
   between their outlines, chamfers included). No existing configuration has stubs that close (the ports of a 4-port
   device are far apart): byte for byte unchanged.

## 2. Tests (demo_6m, the fake EMX; `tests/ic_opt/test_lib_tap.py`)

Build a small transformer library the way `test_library_xfm.py` does (an untapped `xfm_bs` part on demo_6m metals, the
ground fixture on `auto`, a few geometries, one part with a small opening). Then:

1. `window` selects exactly the index rows inside the ranges; `rows` accepts a `lib.index out=` table, a `lib.pick`
   answer and a list of part / obs id; a row not of the stratum is refused.
2. `--plan` runs the preflight only: no EMX, the plan lists the points, refusals with reasons and the
   untapped rows' peak memory.
3. Twins: same-metal taps resolve to the winding metals; the twin's fixture is on the row's recorded metal (and
   `shared` appears only with a via-stack tap through it); the tap ports and the topology's `grounded` follow
   `measure`; `origin` is `tap:<part>:<obs id>`.
4. The narrow case (a part with a small opening, same-metal taps): the twin's stubs sit closer than the fixture metal's
   minimum spacing; with the fixture on an `auto` metal under `free` the preflight passes (the exemption of 1.7.1), and a
   tap width that makes the stubs touch is refused by the fixture (1.7.2). Direct tests of 1.7: under `free` a spacing
   finding between fixture shapes is exempt; under `shared` a device shape too close to a fixture shape still fails; with
   `metal` absent nothing changes (a patterned ground shield's strip spacing still counts); touching stubs are refused;
   every existing configuration builds byte for byte as before.
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
  (taps, fixture kept, device unchanged), preflight, check, adopt;
- `reference.py` (`FIXTURE_METAL_NOTE`) for 1.7, and `docs/em/devices.md` regenerated; and in the recipes list wherever the other library recipes
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

## 5. Record

Status: done on branch `t19-5-lib-tap` (2026-10-05), not merged. The two rules of the ground fixture (section 1.7):
commit `0fd60a2`; the recipe, its tests and the documents: `1a46384`; this record and the backlog row: the commit after
it.

What was done:

1. Section 1.7 (`0fd60a2`).
   - `GeometryGenerationResult.fixture_metal_rule`: the rule the build chose the fixture's metal under, `"free"` or
     `"shared"`; None when `ground_fixture.metal` is absent (the fixture conductor) or a patterned ground shield shares the
     metal. The generators report it (`_write_geometry_outputs_in_stack`); nothing is added to the manifest, whose config
     block already says both, so every manifest keeps its bytes.
   - `drc_audit.fixture_exemptions(profile, conductor=None, metal_rule=None)`: under `"free"` `min_space` and
     `wide_parallel_spacing` on that conductor join `max_width` (`FIXTURE_SPACING_KINDS`); `"shared"`, None and the
     default fixture exempt `max_width` alone, as before; another rule is refused.
   - `stages.em_chain._audit` passes the build's rule. `build_device(spec, device, point, outdir)` is factored out of the
     pcell stage -- the same validation, generation, gate and port check, the same messages -- and the gate's refusal is
     `DrcRefused`, a `StageFailure` that carries the gate's record; the pcell stage calls `build_device` per device.
   - `fixture.add_ground_fixture` collects each stub's outline as it lays it out and refuses, before drawing, two that
     touch or overlap (`_refuse_touching_stubs`: KLayout's `interacting` on the outlines snapped to whole nanometres as
     `Cell.add_polygon` snaps them, a touching edge or corner counting), naming the two ports and the gap -- across the
     stubs at the ring for two on one side, the boxes' separation otherwise; 0 touching, below 0 overlapping.
   - Documents: `FIXTURE_METAL_NOTE` gains a paragraph, `XFM_BS_TAP_NOTE`'s last sentences say what now passes and what
     fails; `docs/em/devices.md` regenerated; one bullet in the pcell README.
2. The recipe (`1a46384`): `src/ic_opt/recipes/lib_tap.py`, sections 1.1-1.6 with the deviations below; `lib_signoff._adopt`
   takes `origin=` (lib_signoff's own call unchanged); `recipe.BUILTIN_RECIPES` lists `lib_tap`. The plan prints the rows
   per part (obs ids), the taps by the profile's metal names, per part the fixture it keeps, the EMX envelope and the
   rows' own peak memory (`Peak memory usage <x> <KB|MB|GB>`, the log's last such line), the preflight's outcome with the
   refusals grouped by reason, the budget, and with `adopt` where the stratum's entry will go (appended, or the snippet
   file); `sim.evaluate`'s own plan line follows per part. The report is written before adopting, so a refusal at
   adoption keeps it.
3. Documents (`1a46384`): `docs/em/library.md` section 7b "Tapped twins: `lib_tap`" (why, the call, what a twin is,
   preflight, run and check, adopt), and `lib_tap` in its Compute and Cache sections; README's library commands and one
   sentence; the ic-opt skill's built-ins and its library paragraph; the module docstring.

Deviations and additions (the commit messages say the same):

1. **`measure: "floating"` is refused** ("cannot be measured yet"). A device's topology holds every port in a drive or
   at 0 V -- the measure kernel requires 2 x drives + grounded = ports, and the spec's topology check each port exactly
   once -- so a twin whose tap ports are open has no topology, and its measure stage and the adopted table's dataset
   would fail. The 2026-09-27 floating comparison removed the tap ports in the Z domain by hand. Open ports need a
   product change (an `open` list in the topology; I = 0 rows in the kernel; the measure stage and the dataset), and any
   change to `ic_opt/em/measure.py` re-keys every dataset cache (its source is part of the key), so every model of every
   library would be fitted again: left for a ticket of its own.
2. The direct tests of 1.7 are in `tests/ic_opt/pcell/test_fixture_spacing.py`, beside the T19.1 / T19.2 fixture tests,
   so the product-change commit carries its tests; `test_lib_tap.py` tests the narrow case through the recipe.
3. A twin spec drops its part's `constraints` (a twin's status is its measurement's, not a sweep's judgement) and takes
   PROJECT's `budget` ("its spec gives the run's resources"); the run refuses before any EMX when the budget cannot hold
   the twins not already in the store (counted as the engine counts them; a twin counts as reusable when the store holds
   an ok observation of its point under its problem -- the engine checks exactly).
4. A window's rows are sorted by part (the manifest's order) and obs id: `observations.jsonl` lists a part's rows as they
   finished, so the index's order differs between builds of the same table. A rows file keeps its own order.
5. The new stratum is the source's entry as `library.yaml` spells it (its `low_freq_max_hz` included: part of the
   quantities' definition), with the new parts and the note after `generator`; the parts' generation pins are dropped.
6. Refused besides section 2.7's list: a part already tapped; a part spec without an `em` section; a name for `adopt` that
   is no plain identifier, a stratum or part store of that name (before anything runs and again before adopting); a
   stratum that appeared in `library.yaml` meanwhile. Rows whose GDS carries no fixture record (an older generation) keep
   the fixture conductor, unless their part's spec named a metal (that one) or `auto` (refused: nothing says where it
   landed).
7. The twin's fixture metal is the recorded conductor by the profile's name with `metal_rule` spelled out -- also for a row
   whose fixture was on the bottom metal by default: such a twin then has its spacing exempt on the bottom metal too, as
   1.7.1 states for a chosen metal; a shield on it turns the exemption off (`fixture_metal_rule` None with `pgs`, also
   when `metal` names the bottom metal).
8. Existing tests changed: the T19.1 test (as the specification asked) now passes the drawn min-space violation under
   `free` and keeps a min-width violation failing; the T19.2 shared-build test passes the build's rule and adds a
   device-like shape 0.05 um inside the ring; the T19.4 narrow-opening test's `gate()` mirrors `_audit` (it passes the
   build's rule): under `free` the natural pitch now passes, under `shared` it fails exactly as it did.

Tests: `tests/ic_opt/test_lib_tap.py` 13 and `tests/ic_opt/pcell/test_fixture_spacing.py` 6, demo_6m and the fake EMX.
Section 2, point by point:

1. `test_a_window_selects_exactly_the_index_rows_inside_its_ranges`, `test_rows_takes_an_index_table_a_pick_answer_and_a_list`;
2. `test_the_plan_runs_the_preflight_only` (no EMX, no observation; rows, taps, fixture, peak memory, a 3.1 um tap refused
   by the fixture with the reason, the envelope, the budget);
3. `test_twins_keep_the_rows_fixture_and_take_the_taps`;
4. `test_the_narrow_parts_twins_pass_the_preflight_on_the_fixtures_own_spacing` (the twin's raw audit has the two
   min_space findings on M4; clean under `free`, refused by the gate under `shared`); the direct tests of 1.7 in
   `test_fixture_spacing.py` (free passes, shared and the bottom metal fail, a shield's spacing counts, touching and
   overlapping stubs refused, chamfered outlines judged at the root); byte for byte: below;
5. `test_a_run_reports_ratios_and_lists_failed_twins` (EMX fails for three twins: listed, not adopted; L and k ratios 1
   within 2e-3, Q 1 / 1.03, SRF 1 where the row resonates inside the sweep);
6. `test_adopt_adds_the_part_stores_and_the_stratum`, `test_adopt_leaves_a_library_yaml_whose_strata_is_not_last_untouched`,
   `test_the_stratum_entry_is_appended_with_the_files_own_indentation`;
7. `test_calls_that_cannot_be_carried_out_are_refused_with_what_to_change`; and three of the pieces (the summary's flags,
   the peak-memory line, the recipe's registration).

`tests/ic_opt/fakes.py` gained `coupled_snp`: a coupled pair whose L, R, C and k follow the geometry in the manifest
(the larger devices resonate inside a 40 GHz sweep), whose tap splits its winding at the tap port and costs 3 % of
resistance -- with the tap grounded L, k and the resonance stay, Q drops by 1 / 1.03.

Runs (`PYTHONPATH=<worktree>/src`), before (the base commit `6c6073f`, exported) and after (`1a46384`):
`tests/ic_opt/test_lib_tap.py` - / 13 passed; `tests/ic_opt/test_library*.py` 221 passed, 6 skipped / the same;
`tests/ic_opt/pcell` 427 passed, 635 skipped / 433 passed, 635 skipped; `tests/ic_opt/test_em_*.py` 57 passed, 4 skipped
/ the same; the rest of `tests/ic_opt` 624 passed, 2 skipped / the same; `ruff check src tests` clean / clean. The
commit `0fd60a2` alone (exported): pcell 433 passed, 635 skipped, em 57 / 4, library 221 / 6, the rest 624 / 2. With the
private profiles of this machine on `IC_OPT_PROFILE_DIRS` (not asked for; the stub rule applies to every family): the
pcell suite 1061 passed, 1 skipped before; after, the pcell suite and `test_lib_tap.py` together 1080 passed, 1 skipped.
The rest of `tests/ic_opt` holds the Ctrl-C tests, which fail falsely when pytest is started as an asynchronous command
of a non-interactive shell (`cmd &`: SIGINT ignored); two such launches gave 5 failures there, all five passed in the
foreground, and the counts above come from runs without `&`.

Byte for byte, a scratch comparison (not committed): main's tests (`6c6073f`) run once with main's sources and once
with the branch's (`tests/ic_opt`, every suite, each temporary directory kept with `--basetemp`), and every GDS,
`emx_ports.txt`, `geometry_manifest.json` and coordinates file at the same path compared byte for byte: demo_6m 1 299
files -- 425 GDS, 431 port files, 428 manifests identical, and 15 GDS that the tests write themselves with KLayout's
default writer (a toy plugin's strip, audit inputs, the strips the T19.1 / T19.2 tests draw) differing only in their
BGNLIB / BGNSTR time; with the private profiles (the pcell suite) 1 335 files, all identical but 9 such GDS. The test
outcomes are the same on both sides except main's `test_reference.py` (`devices.md` regenerated). And, read-only, the
18 864 builds recorded in the libraries on this machine (`geometry_manifest.json`: ports, stub widths, chamfer): the
closest two stubs on one side of any of them are 2.0 um apart (a via-stack-tapped table with 7 um openings), so the
touching-stub refusal refuses none of them.

Not foreseen here:

1. `measure: "floating"` (deviation 1).
2. A part store lists its rows in completion order, not obs order (deviation 4).
3. The check needs geometry-dependent values and a tap that costs something, so the fake coupled pair was added
   (section 2 allowed it).
4. The preflight draws each twin in a thread of the controller (the GIL limits how much threads overlap) and the pcell
   stage draws it again during the run: two builds per twin. Not measured on a real library.
5. `ic-opt run` prints a refusal of the recipe (`TapError`, a `ValueError`) with a traceback, as it does for
   `lib_signoff`'s: the command line catches only the site, envelope and library-link errors.
6. `docs/refactor/BACKLOG_CN.md`'s T19.4 row still says "分支已做，未合入" although main holds T19.4; left as it is.
7. With a via-stack tap whose stack passes through the fixture's metal (`shared`), a same-metal tap's stub at a small
   opening is not exempt: on the test library, `taps={"primary": "same", "secondary": "3", ...}` has the narrow part's
   twins refused by the DRC gate (`[min_space] M4 x2`), as 1.7.1 decides for `shared` (the gate cannot tell the device's
   shapes from the fixture's there). The plan says so before any EMX.
