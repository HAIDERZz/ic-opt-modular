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
