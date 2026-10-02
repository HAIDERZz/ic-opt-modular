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

## 8. Record

Status: done on branch `t19-2-fixture-metal-shared` (2026-10-03), not merged. Feature, tests and the regenerated
`docs/em/devices.md`: commit `8f2f5fa`; this record and the backlog row: the commit after it.

What was done (sections 1-6):

1. `ground_fixture.metal_rule` (`CleanPortGroundFixtureConfig`): `free` / `shared`, absent = `free`, read in any case
   like `auto`. Given without `metal` (absent or `null`), either spelling, it is refused at validation, the message naming
   `ground_fixture.metal_rule` and `ground_fixture.metal`; with `pgs` that refusal comes first, and `metal: auto` with
   `pgs` is refused by T19.1's validator as before. The serializer drops an absent rule and `free`. The pcell dataclass
   has `GroundFixtureConfig.metal_rule: str = "free"`; `fixture.metal_rule_of` checks it, so a plugin generator that
   builds the dataclass itself gets a `PortError` for an unknown rule or for `shared` with `metal` None.
2. `fixture_metal` honours the rule: under `shared`, `auto` is the highest metal that carries no port lead and has a pin
   layer; a named metal is refused only for a port lead. The refusals name the rule in force ("... no metal_rule allows
   it (the rule in force: 'shared')"; "under metal_rule 'free' the metal must hold nothing of the device; 'shared' allows
   internal shapes").
3. The record: `add_ground_fixture` keeps `_drawing_bbox_um(cell)`, taken before the first fixture shape, as
   `Cell.device_bbox_um`; `_write_geometry_outputs_in_stack` writes it as `geometry.device_bbox_um` (rounded to
   0.001 µm) for every build and returns it as `GeometryGenerationResult.device_bbox_um`. `footprint.footprint()` reads
   it (`recorded_device_bbox`, `box_footprint`: width, height and area from whole nanometres, the way `measure` computes
   them from the GDS's database units) without opening the GDS, and measures by layer as before when there is no record
   or it is not four ordered numbers. Another plugin's generator is still refused, record or not (an existing test pins
   it); `ProfileUnavailable` is now raised only when the footprint has to be measured.
4. The DRC gate: `fixture_exemptions` unchanged; its docstring and `docs/em/devices.md` say that under `shared` the
   by-layer `max_width` exemption also covers the device's internal shapes on that metal, why nothing is hidden for the
   built-in families, and that a plugin family with wide internal shapes should not use `shared`.
5. Documents: `FIXTURE_METAL_NOTE` gains the `metal_rule` paragraph; its first paragraph now says T19.1's refusal is the
   default rule and that a footprint measured by layer needs the fixture's layer to itself (it said the footprint tells
   the two apart by layer, which a recorded build no longer does); `docs/em/devices.md` regenerated; the `footprint.py`
   module docstring describes the record and the fallback.

Deviations and additions (the commit message says the same):

- **A contact check under `shared` (not in this specification).** Section 1 reasons about the ring, which keeps
  `inner_margin_um` from the body; a stub starts at its port. On demo_6m, golden `xfm_il_nt3` with 2 µm leads: each
  secondary lead runs under the primary on M5 and ends in a 5 µm via pad that reaches 3 µm past the port. `auto` +
  `shared` takes M5 (no port lead there), the stubs overlap the two pads (30 µm²), the G labels land on a winding's net
  (`connectivity.nets`), and the product DRC verdict is `pass`: touching shapes on one layer merge, no rule sees a short.
  `add_ground_fixture` now lays the fixture's shapes out first and, under `shared`, refuses with a `PortError` -- before
  anything is drawn, naming the metal and the place -- when any of them touches a device shape on that metal (overlap,
  edge or corner). `auto` is not changed to skip such a metal: one table keeps one fixture metal. No other case probed
  touches: every golden case under `auto` + `shared`, ms with the secondary up to 300 µm and primary leads down to
  5 µm, ind_sym NT 3 with 0.5 µm leads, balun and il variants.
- `add_ground_fixture` draws the same shapes in the same order from the laid-out list: default and `auto` builds are
  byte-identical (below).
- T19.1's byte-for-byte test (section 5, point 6) is unchanged and passes: it compares the GDS bytes, the port file,
  `fixture_metal` and the config block, never the whole manifest, so there was no key to remove and no expectation to
  update. The new key is covered by the new tests and by the comparison below.

Tests: `tests/ic_opt/pcell/test_fixture_metal_shared.py`, 49 tests. Section 5 points 1-5:
`test_auto_shared_puts_the_same_fixture_on_the_crossunders_metal` (the shared build is the free build with the fixture
moved from M3 to M4, layer for layer and label for label); `test_shared_refuses_only_a_metal_that_carries_a_port_lead`
(ms, bs); `test_a_rule_without_a_metal_is_refused_at_validation` and `test_the_rule_spelled_out_is_the_rule_left_out`;
`test_the_recorded_box_is_the_by_layer_footprint` (all 13 golden cases, every family, × `metal` absent / `auto`) and
`test_the_footprint_reads_the_record_and_falls_back_without_it`; `test_the_drc_gate_on_a_shared_build`. Beyond them:
`test_no_shared_fixture_touches_the_device` (13 golden cases), `test_a_stub_that_would_touch_a_device_shape_on_the_shared_metal_is_refused`,
`test_the_rule_in_the_pcell_dataclass`.

Runs (`PYTHONPATH=<worktree>/src`, demo_6m): `tests/ic_opt/pcell` 403 passed, 635 skipped (354 / 635 before);
`tests/ic_opt/test_library_footprint.py`, `test_em_pcell.py`, `test_em_circuit.py` 24 passed; `ruff check src tests`
clean. A scratch comparison, not committed: 42 builds (13 golden cases and the 3 T19.1 families with `metal` absent and
`auto`, and the 10 ind_sym / bs / ms cases with `pgs`) with the sources of `cb8df30` and with the branch -- GDS bytes and
`emx_ports.txt` identical, manifests identical once `device_bbox_um` is removed, and the recorded footprint equal to the
by-layer one to the last digit on all 42.

Not foreseen here, left open:

1. The library's footprints do not go through `footprint.footprint()`: `ic_opt.library.dataset.footprints` calls
   `fixture_layer(profile, generator, plugin)` once per part, without the manifest's `fixture_metal`, and `measure()`
   per row. For a row that keeps its GDS beside its sNp and whose fixture is off the bottom metal -- T19.1's
   `metal: auto` or a named metal, so the generation-2 tables if they keep their GDS, and any `shared` row -- the
   library's footprint is the ring's outer box (demo_6m ind_sym: 177 × 170 µm against 120 × 100 µm).
   This dates from T19.1. A fix: per row, the record when the manifest beside the GDS has one, else the by-layer
   measurement with `recorded_fixture_metal`; bump `FOOTPRINT_VERSION` so cached files are measured again.
2. xfm_il with a lead shorter than its via pad (2 µm against W = 5 µm): the pad reaches past the port point, so the
   port is not on the conductor's outer edge. This predates T19.2 and shows only through the contact check.
3. Not run on the private profile (demo_6m only, as asked). By the rule, an ms whose primary and secondary sit on the top
   two metals takes the metal of the secondary's crossunder, one below the secondary, under `auto` + `shared` (no
   taps); whether the contact check refuses some of its points is for the first build to show.
