# T19.2 — the ground fixture on a metal the device uses only for internal shapes (opt-in rule `shared`)

Status: specification (2026-10-03), for the coding subagent; follows T19.1 (`T19_1_FIXTURE_METAL_SPEC.md`, merged
`361c60d`). Decision behind it: the second-generation library (T19, `ic-opt-accept/t19_build/RESULT_CN.md`) measures
every row with the fixture on a thick metal; T19.1's `auto` takes the highest metal the device draws *nothing* on. For
a family whose multi-turn winding has crossunders one level below it, and whose other winding sits one level above,
that rule leaves only a thin metal two levels down: the measured Q then gains 16 % where every other table gains 45 %
(the pilot in `ic-opt-accept/t19_pilot/PLAN_CN.md`). EMX's own constraint is narrower: a port's reference stub may not
lie on the port lead's metal (`ports "G05" and "CTP" are not allowed to be on the same edge`). Nothing stops the ring
from sharing a metal with crossunders or bridges, which are internal shapes 15 µm and more away from it. What T19.1
also leaned on was the footprint: it tells the fixture from the device *by layer*. T19.2 gives the footprint its own
record, so that the fixture may share a metal with internal shapes when the configuration says so.

Read before writing: `src/ic_opt/em/pcell/fixture.py` (`GroundFixtureConfig`, `fixture_metal`, `_device_on`,
`_drawing_bbox_um`, `add_ground_fixture`), `generator_plugin.py` (`CleanPortGroundFixtureConfig`, its `metal`
validators and serializer, `_build_fixture`, `_write_geometry_outputs_in_stack`, the manifest), `footprint.py`,
`drc_audit.py` (`fixture_exemptions`), `reference.py` (`FIXTURE_METAL_NOTE`), `docs/em/devices.md`,
`tests/ic_opt/pcell/test_fixture_metal.py` (T19.1's tests: reuse its fixtures and families), `T19_1_FIXTURE_METAL_SPEC.md`.

## 1. What changes

`ground_fixture` gains one field, `metal_rule`, next to `metal`:

```yaml
ground_fixture: {inner_margin_um: 15, ring_width_um: 50, stub_length_um: 2, stub_chamfer_um: 0, metal: auto, metal_rule: shared}
```

- `metal_rule` absent or `free` (the default): exactly T19.1 -- `auto` is the highest metal the device draws nothing
  on; an explicit metal is refused when it carries anything of the device. Every existing configuration, golden GDS,
  manifest and library row is unchanged, byte for byte; `GEOMETRY_VERSION` does not change.
- `metal_rule: shared`: the metal may carry device shapes **that are not port leads**. `auto` is then the highest metal
  of the stack that carries no port lead (no port in `cell.emx_ports` has it as its lead metal) and has a pin layer for
  the G pins; an explicit metal is refused only when it carries a port lead (the EMX constraint, the ports named).
  Shapes on its drawing layer and labels on its pin layer no longer refuse it.
- `metal_rule` given (either spelling) without `metal` (absent or `null`) is refused at validation: the rule says how
  a metal is chosen, and the default fixture chooses none. `metal_rule: free` spelled out serializes like the absent
  field does for `metal` today (the serializer drops the default), so a config that spells the default gives the same
  manifest as one that does not.

`fixture_metal(cell, config, process)` reads `config.metal_rule` (`GroundFixtureConfig.metal_rule: str = "free"`); the
refusal messages name the rule in force ("under metal_rule 'free' the metal must hold nothing of the device; 'shared'
allows internal shapes").

## 2. The footprint gets its own record

Today `footprint.footprint()` is the box around every shape not on the fixture's layer. Under `shared` that layer also
holds device shapes, so the generator records the device's box itself:

- `_write_geometry_outputs_in_stack` (or wherever the fixture is drawn, *before* `add_ground_fixture`) takes the drawing
  bbox of the cell (`fixture._drawing_bbox_um(cell)`: every shape on every layer, leads included -- the same set the
  by-layer footprint sees today) and writes it to the manifest as `geometry.device_bbox_um: [x0, y0, x1, y1]` (µm,
  rounded to 0.001), for every build, whatever the rule.
- `footprint.footprint()`: when the manifest beside the GDS records `device_bbox_um`, the footprint is that box's width,
  height and area (rounded as today) and the GDS is not opened; otherwise (a row of an older generation) the by-layer
  measurement as today. `GeometryGenerationResult` gains `device_bbox_um` so that `em_chain` and tests see it without
  reading the manifest.
- Equality: for every built-in family, under `metal` absent and under `metal: auto` (rule `free`), the recorded box
  gives the same footprint as the by-layer measurement of the same GDS, to 0.001 µm. This is a test (section 5).

## 3. The DRC gate

`fixture_exemptions(profile, conductor)` is unchanged: `max_width` on the fixture's metal is exempt. Under `shared` this
also exempts the device's own shapes on that metal from `max_width`; every other rule on that metal, and every rule on
the other metals, still counts. State this in the docstring and in `docs/em/devices.md`: the built-in families' internal
shapes (crossunders, bridges) are as wide as a winding, far below any metal's `max_width`, so nothing is hidden in
practice; a plugin family with wide internal shapes should not use `shared`.

## 4. PGS

Unchanged: a shielded device refuses a fixture metal other than the default (T19.1's validator), `metal_rule` included
(`shared` with `pgs` is refused by the same validator, since it requires `metal`).

## 5. Tests (`tests/ic_opt/pcell/test_fixture_metal.py`, demo_6m; add to the T19.1 file or a sibling `test_fixture_metal_shared.py`)

On the demo stack the ms family has its primary on M6, its secondary on M5 and the secondary's crossunder on M4
(T19.1's `HIGHEST_FREE` gives M3 for it under `free`).

1. `auto` + `shared` on the ms family picks M4 (the crossunder's metal): `result.fixture_metal == "M4"`, the ring, the
   stubs and the G pin labels are on M4's layers, the bottom metal is empty, the manifest says `metal_rule: shared`.
   `auto` + `free` on the same device still picks M3 (the existing test keeps passing).
2. Explicit `M4` + `free` is refused (existing test); `M4` + `shared` is accepted; `M5` and `M6` + `shared` are refused
   naming the port lead (EMX's constraint), for the ms and bs families.
3. `metal_rule` without `metal`, and with `metal: null`, is refused at validation (the message names both fields);
   `metal_rule: free` spelled out gives the same manifest config block as leaving it out, and the same GDS bytes as T19.1's
   `metal: auto` build.
4. Footprint: for every family and for `metal` absent / `auto`, `footprint()` (now from the record) equals `measure()` by
   layer on the same GDS; for the ms `shared` build the footprint equals the `free` build's (same device); the manifest
   records `device_bbox_um`; with the manifest removed, `footprint()` falls back to the by-layer measurement.
5. DRC: the ms `shared` build passes the product-scope verdict with `fixture_exemptions(PROFILE, "M4")`; a min-space
   violation drawn on M4 still fails it (mirror T19.1's test).
6. T19.1's byte-for-byte test for the default fixture keeps passing (the manifest gains `device_bbox_um`: compare the
   GDS bytes and the ports as before, and the manifest with that key removed -- or update the expectation; say which).

## 6. Documents

- `reference.py` `FIXTURE_METAL_NOTE`: a paragraph on `metal_rule` (`free` / `shared`, what each refuses, the DRC note);
  regenerate `docs/em/devices.md` (`python -m ic_opt.em.pcell.reference > docs/em/devices.md`).
- `docs/refactor/BACKLOG_CN.md` 0.12: the T19.2 row -- done, commit, tests.
- Section 7 of this file: the record (what was done, the commit, test counts), as T19.1's spec has.

## 7. Working rules for the coder

Branch `t19-2-fixture-metal-shared` in its own worktree from `main` (`git merge --ff-only main` first to confirm the
base); `PYTHONPATH=<worktree>/src` for every test run (the main tree is the editable install); the pcell suite with
the private profile is not needed -- demo_6m covers everything here; run `tests/ic_opt/pcell/` and
`tests/ic_opt/test_executor.py` is irrelevant; `ruff check src tests`. One commit for the feature, one for the docs if
you prefer. Do not touch `GEOMETRY_VERSION`, the golden GDS files, or any file under `ic-opt-library`. Nothing of the
private process (table names, metal thicknesses, rule values, paths) goes into code, tests or docs: demo_6m only. Commit
trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not merge; report the commit ids, the test counts
and anything you changed that this specification did not ask for.
