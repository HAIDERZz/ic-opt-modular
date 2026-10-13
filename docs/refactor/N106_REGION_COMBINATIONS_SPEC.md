# N-106: the region's grid as the allowed combinations of a pcell device, and the spec block to paste (2026-10-13)

The `lib.region` demonstration of 2026-10-13 (`ic-opt-accept/region_demo_80g/RESULT_CN.md`) answered an inductor
request (Lp@80 120 pH +-8 %, Qp@80 >= 12) with a band: for every width the outer diameters that give 120 pH move up by
2-4 um per 0.4 um of width. A spec can only state rectangular ranges per variable, so an `optimize` run with EMX in
the loop (the user's intended flow: ideal devices in the pre-simulation, the library recommending the pcell's
parameter ranges, then the joint optimization) would spend most EMX runs on combinations outside the band. T18.2A
gave the search space tables of allowed combinations (`space.Table`, `space.tables(spec)`), filled today only by
library devices (`library/link.py`: the rows of the table). The user decided on 2026-10-13: let a pcell device take
its allowed combinations from a file, let `lib.region` write that file from its robust grid, and let it print the
device block to paste into the spec.

Read first: `docs/refactor/T18_2A_ALLOWED_COMBINATIONS_SPEC.md` and `T18_2B_LIBRARY_DEVICE_SPEC.md`, `src/ic_opt/space.py`
(`Table`, `tables`, `snap`, `check`, `grid_size`, `placements`), `src/ic_opt/library/link.py`, `src/ic_opt/spec.py`
(`Device`, `Variable`, `problem()`), `src/ic_opt/library/region.py` (`region`, `_level`, `_group_by`), `src/ic_opt/blocks/library.py`
(the `lib.region` block), `blocks/optimize.py` (the plan lines), and `docs/em/library.md` 5b.

## 1. The combinations file

A JSON document, written by `lib.region` and read by the spec:

```json
{"format": "ic-opt-combinations-v1",
 "dims": ["outer_diameter_um", "width_um", "spacing_um", "turns"],      # generator field names, the stratum's dims
 "rows": [[52.0, 2.0, 2.0, 1], [56.0, 2.0, 2.0, 1], ...],                  # one allowed geometry per row, in dims order
 "source": {"library": "<root>", "stratum": "ind_sym_m10", "level": "robust", "targets": {...}, "written": "<UTC>"}}
```

`rows` are the grid points of the level named (robust by default, `level=mean` on request), distinct, in the answer's
order. Values are numbers in the generator's units (as the region's grid holds them).

## 2. The spec: `Device.combinations`

- `Device.combinations: str | None` (default None): the file's path, absolute or relative to the project directory
  (the spec's directory). Allowed on a pcell device (generator and profile); refused on a library device (its
  combinations are the table's rows) with a message naming both.
- `library/link.py` (or a sibling `combinations.py` that `link.tables` also calls -- keep `space.tables(spec)` the one
  entry point): for every pcell device with `combinations`, build a `space.Table` over the device's spec variables:
  each file dim that the device maps in `variables` becomes that spec variable; a file dim the device holds in `fixed`
  keeps only the rows whose value equals the fixed value (within 1e-9), and a line under `--plan` says how many rows
  that dropped; a file dim that is neither a device variable nor fixed, or a device variable missing from the file, is
  refused with a message naming the dim and the device. Each kept row's value must lie on the spec variable's grid
  (`lower + k * step` within 1e-9 of a level; the region's steps are multiples of the manifest steps, the spec's are the
  user's): a value off the grid is refused, naming the variable, the value, the step and the nearest levels. The table's
  label is `device <id> (combinations <file name>)`. Rows that fall on the same combination of levels collapse to one.
  An empty table after filtering is refused.
- Identity: `combinations` is **not** part of `Spec.problem()` and not of any fingerprint: it restricts which points
  the strategies propose, not what a point evaluates to; the observations of a run keep their meaning whether the file
  is added, changed or removed, exactly as `budget` does. State this in the README and in `problem()`'s docstring.
- `--plan`: one line per such device, `device <id>: <n> allowed combinations of <variables> from <file> (<m> rows
  dropped by fixed <dim>=<value>)`, and the existing grid-size line reflects the valid points.
- Everything T18.2A teaches the strategies and `space` applies unchanged (snap to the nearest combination, `check`,
  `grid_size`, `metric_gp` candidates, OpenBox / TuRBO through `grid_suggestions`): no strategy code changes; a test
  pins that proposals on such a spec are valid points.

## 3. `lib.region`: `out=` and the device block

- `lib.region ... out=PATH [level=robust|mean] [device=<id>]`: writes the combinations file (section 1) and adds to
  the answer `"combinations": {"path": ..., "level": ..., "rows": n}` and `"device_block"`: a YAML text to paste under
  `devices:` -- `id: <device>` (`device=` or the stratum name), `generator`, `profile` (the library's), `ports` (the
  generator's default port labels), `fixed` holding every dim whose robust set has one value (the demonstration's
  spacing 2 and turns 1) and the stratum's other fixed fields the manifest's parts share (metal names and the like,
  read from the parts' spec), `variables` mapping each remaining dim to `<device>.<dim>`, and `combinations: <PATH>`;
  and `"variables_block"`: the YAML of the matching `variables:` entries (`name`, `lower`, `upper`, `step`, `unit`
  from the generator's field units) with the robust level's per-dim ranges and the region's grid steps. The texts are
  printed to stdout after the answer when `out=` is given. The user edits the id and the fixed fields; the block is a
  start, not a guarantee -- say so in the docs.
- The `ic-opt call lib.region` command line passes `out`, `level`, `device` through.

## 4. Tests

`tests/ic_opt/test_space_tables.py` (or beside it): a pcell spec with a combinations file -- `space.tables` gives the
table, `snap` / `check` / `grid_size` honour it, a fixed dim filters rows, an off-grid value is refused with the step
named, a library device with `combinations` is refused; `test_optimize.py`: `metric_gp` and `openbox_gp_eic` proposals
on such a spec are valid points (fakes); `test_library_region.py`: `out=` writes the file with exactly the robust
grid points, the device block names the fixed dims and the variables, and a round trip (write the file, build a spec
from the block, `space.tables`) gives one combination per robust point; the plan line test.

## 5. Documents

README "EM devices" (the `combinations` field, the identity rule, the plan line); `docs/em/library.md` 5b (`out=`,
`level=`, `device=`, the blocks); `skills/author-spec/SKILL.md` (the flow: pre-simulation targets -> `lib.region
out=` -> paste the blocks -> `--plan`); `skills/ic-opt/SKILL.md` cheatsheet line.

## 6. Working rules for the coder

Branch `n106-region-combinations` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n106-region-combinations`
(from `main`; `git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with the main tree's `.venv/bin/python`;
`git -C <worktree>` and absolute paths. Targeted suites: `test_space_tables.py`, `test_optimize.py`, `test_library_region.py`,
`test_library_device_run.py`, `test_spec.py`, `test_cli_recipes.py`, `test_replay_parity.py` with
`IC_OPT_RECORDED_RUNS=/home/zzchen/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2:/home/zzchen/remote_opt/Mixer_CS_validation_second_batch_20260810`
(a spec without `combinations` must propose exactly as before), `ruff check src tests`. Commits: spec and link (with
tests), region `out=` and blocks (with tests), documents, then the `## Record`. A real check without EMX: run
`lib.region` with `out=` on the private library (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`,
read only, `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`,
the demonstration's targets on `ind_sym_m10`, `workers=4 threads=8`), paste the blocks into a copy of
`examples/spec.yaml`'s shape with the fake exports of the tests, and show `--plan` accepting it; put the outputs under
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n106_region_combinations/` and the command in the record. No
merge, no push, no BACKLOG edit. Messages in the repository's style, ending with
`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Record

Branch `n106-region-combinations`, from `main` at 8f2ed29 (`git merge --ff-only main`: already up to date). Nothing merged,
nothing pushed, `docs/refactor/BACKLOG_CN.md` untouched; `region._hits` / `_measured` (N-105's) untouched.

| commit | what |
| --- | --- |
| 3d4aaaf | `Device.combinations` (out of the dump while unset; refused beside `library`, naming both; a relative path joined to the spec's directory, which `load_spec` passes as the validation context); `_NOT_PROBLEM` and `_legacy_fingerprint` leave it out; `problem()`'s docstring says why. New `ic_opt/library/combinations.py`: the file (`write`, `read`), the table per device (`resolve`, `tables`), the plan lines (`lines`); `link.tables` returns those tables beside the library devices', `link.clear` forgets them. `opt.optimize --plan` prints the lines. Tests: `test_combinations.py` (new, 15), `test_optimize.py` (+3). |
| d555e84 | `region.region(level_points=robust/mean)` ends its answer with that level's grid points; the `lib.region` block takes `out=`, `level=`, `device=` and adds `combinations`, `device_block`, `variables_block` (`combinations.from_region`); `ic-opt call` prints the blocks after the answer. Tests: `test_library_region.py` (+5). |
| 29a13d7 | README "EM devices", `docs/em/library.md` 5b (a subsection at its end), `skills/author-spec/SKILL.md` (a row of section 3, "A device held to a region" in `### devices`, the plan line in section 6), `skills/ic-opt/SKILL.md` (one cheatsheet line). |

Tests (`PYTHONPATH=<worktree>/src`, the main tree's `.venv`, `OMP_NUM_THREADS=2`, `IC_OPT_RECORDED_RUNS` as in section 6;
"before" = 8f2ed29, "after" = 29a13d7):

| suite | before | after |
| --- | --- | --- |
| `test_space_tables.py` | 59 | 59 |
| `test_optimize.py` | 41 | 44 |
| `test_library_region.py` | 20 | 25 |
| `test_library_device_run.py`, `test_library_device.py`, `test_library_link.py` | 6 + 9 + 7 | 6 + 9 + 7 |
| `test_spec_library.py`, `test_spec_space_objective.py` (no `test_spec.py` exists) | 36 + 7 | 36 + 7 |
| `test_cli_recipes.py` | 41 | 41 |
| `test_replay_parity.py` (two recorded runs) | 2 | 2 |
| `test_combinations.py` | -- | 15 |
| together | 228 passed | 251 passed |
| with `test_library_docs.py` + `test_skill_author_spec.py` (4 + 8, unchanged) | | 263 passed |

`ruff check src tests`: all checks passed. A spec without `combinations` proposes as before: the recorded runs replay
unchanged and the pinned batches of `test_space_tables.py` (every strategy and point block without tables) hold.

### The real check (no EMX, no Spectre)

`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n106_region_combinations/run_check.sh` (log `run_check.log`):
`ic-opt call lib.region <n28_v2> stratum=ind_sym_m10 'targets={"Lp@80": {"target": 120e-12, "tol": 0.08}, "Qp@80":
{"min": 12}}' group_by=turns,width_um trend=Qp@80:outer_diameter_um workers=4 threads=8 cache_dir=<evidence>/cache
out=<evidence>/plan_project/region_ind_sym_m10.json device=L1`, with `IC_OPT_PROFILE_DIRS` as in section 6. 3.0 to 3.4 s
wall; the models came from the library's own `.cache` (nothing fitted, `cache_dir` not even created) and no file under
the library root is newer than the start. The robust level is the demonstration's: 28 grid points, outer diameter 52 to
80 um (step 4), width 2 to 10 um (step 0.4), spacing 2, one turn; the file holds those 28 rows. Printed after the answer:

```yaml
# paste under devices: (lib.region out=.../plan_project/region_ind_sym_m10.json; edit the id and the fixed fields)
  - id: L1
    generator: clean_port_ind_sym
    profile: <the library's profile>
    ports: [P1, N1]
    fixed:
      spacing_um: 2.0
      turns: 1
      lead_length_um: 20.0
      metal: '<the parts' winding metal>'
      ground_fixture: {inner_margin_um: 15.0, ring_width_um: 50.0, stub_length_um: 2.0, stub_chamfer_um: 0.0, metal: auto}
    variables: {outer_diameter_um: L1.outer_diameter_um, width_um: L1.width_um}
    combinations: .../plan_project/region_ind_sym_m10.json
  # not in fixed -- the parts differ, state the value this device takes: opening_um (2.0, 8.0)

# paste under variables:
  - {name: L1.outer_diameter_um, kind: continuous_step, lower: "52", upper: "80", step: "4"}   # um
  - {name: L1.width_um, kind: continuous_step, lower: "2", upper: "10", step: "0.4"}   # um
```

(The profile and metal are the private library's; the evidence directory has them verbatim.) `make_plan_project.py`
pasted both blocks into a spec of `examples/spec.yaml`'s shape around the tests' fake export (an nport `NPORT0`, the
circuit's `F`), with one edit -- `opening_um: 8.0`, the field the comment names -- and placeholder `em` resources;
`ic-opt run optimize plan_project budget=8 batch=4 --plan` accepted it (`plan_out.txt`; the two `FAIL` lines are the
placeholder process file):

```
[plan] strategy auto: openbox_gp_eic (metric_gp does not take EM devices yet)
[plan] opt.optimize step='optimize': device L1: 28 allowed combinations of L1.outer_diameter_um, L1.width_um from .../plan_project/region_ind_sym_m10.json (0 rows dropped by fixed spacing_um=2, turns=1)
[plan] opt.optimize step='optimize': search space: 168 valid points (the variables' grids hold 1008)
```

`ic-opt call opt.suggest plan_project n=12 strategy=openbox_gp_eic seed=1` (proposals only): 12 distinct points, every
(diameter, width) one of the file's 28 rows (`check_proposals.py`).

### Readings and deviations

1. **metric_gp.** It still refuses a spec with a device EMX simulates in the loop (`DEVICES_REFUSAL`, unchanged: no
   strategy code changes), so `strategy=auto` gives `openbox_gp_eic` on such a spec. The test pins `openbox_gp_eic`'s
   proposals over four batches, and for `metric_gp` the refusal and that its coordinates and candidates (`Coords.size`,
   `whole_grid`, `design_raw`) hold valid points only on the file's table.
2. **"The existing grid-size line"** did not exist in `--plan`. The valid points are said in a new line, `search space:
   <n> valid points (the variables' grids hold <N>)`, printed after the device lines and only when a device names a file,
   so the plan of every other spec (library specs included) is unchanged. `space.grid_size` (and what reads it: the
   digest's whole-grid note, `metric_gp`'s whole-grid choice, the space-filling design) already counts valid points.
3. **Where the lines print.** `opt.optimize`'s plan section, as its other lines (`[plan] opt.optimize step=...: `), so
   `optimize`, `signoff` and `coarse_to_fine` show them; the doctor has no new check. A bad file is a `LinkError` at the
   first use of the space, which `ic-opt run` reports as an error line, in plan mode too.
4. **The relative path** is joined to the spec file's directory at load (a pydantic validation context from `load_spec`);
   a spec built in code keeps it as written (relative to the process's directory). The model then holds the absolute
   path; it is out of every identity.
5. **Read once per process**, keyed by the path, the device's mapping, its fixed fields and its variables' grids;
   `link.clear` forgets; `combinations.write` forgets the path it writes.
6. **`unit`** is not a field of a spec variable (unknown keys are refused), so the variables block writes it as a trailing
   comment (`# um`, `# count` for turns, from the field names: every length ends in `_um`), and writes the required
   `kind` (integer for the turns dim, continuous_step otherwise).
7. **`ports`**: no generator API gives "default port labels"; the block takes the library parts' device ports (the labels
   the rows were simulated with). `generator` is the stratum's, `profile` the manifest's `process_profile`; `plugin` is
   written only when the parts' is not `builtin:clean_port`.
8. **Fields the parts disagree on** are named in a comment line under the device block (the real library: `opening_um`
   2.0 in one part, 8.0 in the other two), because the generator needs them and the block cannot choose.
9. **Indentation.** The blocks are indented as the repository's specs indent a list (`devices:` then `  - id:`). The first
   pass of the real check printed them at column 0, and pasted beside an indented `  - {name: F ...}` entry that is not
   YAML; fixed before d555e84.
10. **`combinations:` in the block** is the absolute path of `out=`, so the block works wherever the spec lives.
11. **Refusals beyond the specification's list**: `level=` or `device=` without `out=`; a level other than robust/mean;
    `out=` on a level without a point (nothing is written; the message gives the other level's count); a file that is not
    an `ic-opt-combinations-v1` document or whose rows are not one number per dim. A value outside its variable's range
    is "not on the grid" (the nearest level is the range's end).
12. **Tests' places.** The spec and space tests are a new file beside `test_space_tables.py`; the region tests are inserted
    before the T15.3 section of `test_library_region.py`, not at its end, and its imports gain `re` and `yaml`, to keep
    N-105's additions to that file mergeable. `docs/em/library.md` 5b gains a subsection at its end, after the paragraph
    N-105 adds a sentence to: a merge may meet adjacent hunks there.
