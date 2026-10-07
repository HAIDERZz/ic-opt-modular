# N-65 — two chamfered stubs that meet: no sharp wedge in the ground ring's opening

Status: specification (2026-10-07), for the coding subagent; the polish batch (`BACKLOG_CN.md` 0.13, item 6).

The finding (N-65, 2026-09-28): demo_6m, `clean_port_xfm_bs` with both center taps, `stub_chamfer_um: 2`, primary width
≥ 7 µm: the tap port's stub and a neighbouring winding port's stub sit close enough that their chamfers meet; the ring
opening between the two stubs ends in a sharp wedge on the fixture metal, and the DRC audit reports `[min_space] M1 x2`,
so the point is `failed:pcell`. At width 6 the wedge's end is a 0.5 µm flat and passes; with `stub_chamfer_um: 0` every
width passes. Figure: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n51/figures/demo6m_tapped_m1_space_violation.png`
(read-only, outside the repository).

Read before writing: `src/ic_opt/em/pcell/fixture.py` (`add_ground_fixture`: the stub outlines with the chamfer `ch`,
the ring's four sides, `_refuse_touching_stubs` of T19.5, `_auto_stub_widths`, the `GroundFixtureConfig` fields),
`drc_audit.py` (`fixture_exemptions`: which spacing findings are exempt and when -- a fixture on the bottom metal with
`metal` absent has no exemption, this is the case here), `tests/ic_opt/pcell/test_fixture_spacing.py`,
`test_fixture_metal.py`, the golden tests under `tests/ic_opt/pcell/`.

## 1. The rule

When two stubs on one side of the ring are neighbours and their chamfered outlines leave a gap at the ring's inner
edge that is smaller than the fixture metal's minimum spacing (the process profile's `min_space` for that metal; in
reference mode, no profile, the rule does not apply), **both stubs' chamfers on the facing side are shortened** to the
largest value at which that gap equals the minimum spacing, never below 0 (at 0 the stubs are plain rectangles, which
never form a wedge). The chamfer on the outer sides and on every other stub is unchanged. A stub pair whose gap is
already at or above the minimum spacing is unchanged, so every existing configuration builds byte for byte (the
golden cases and the T19.1 / T19.2 / T19.4 / T19.5 tests prove it). Stubs that would touch are still refused before
anything is drawn (T19.5), and the chamfer rule runs before that check (a shortened chamfer can only widen the gap).

The adjustment is recorded: `GeometryGenerationResult` / the manifest's `geometry` gain `stub_chamfers_um`, the
chamfer per port and side actually drawn, written only when some stub differs from `stub_chamfer_um` (so an unchanged
build's manifest is unchanged).

Why shorten the chamfer rather than clip the opening: the opening's end is defined by the two stubs; clipping would
add a fifth kind of fixture shape, while a smaller chamfer keeps every shape what it is (ring sides, stubs) and the
stub's electrical role unchanged (the chamfer is a drawing nicety at the ring junction).

## 2. Tests (demo_6m; `tests/ic_opt/pcell/test_fixture_chamfer.py`)

1. The N-65 geometry (tapped xfm_bs, chamfer 2, primary width 7 and 10): builds, the DRC gate passes, the manifest
   records the shortened chamfers for the two stubs that met, the other stubs keep 2; width 6 is unchanged byte for
   byte against main (gap already at the minimum).
2. Chamfer 0 configurations unchanged byte for byte; the golden cases unchanged (the existing golden tests).
3. The rule never produces a gap below the minimum spacing: a sweep over primary widths 4 to 12 on the N-65 geometry
   passes the gate at every width.
4. Reference mode (no profile): no adjustment, the build is what it was.
5. `ruff check src tests` clean.

## 3. Working rules for the coder

Branch `n65-stub-chamfer` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n65-stub-chamfer` (from
main; `git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`
with `PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/pcell`, `tests/ic_opt/test_lib_tap.py`
and `ruff check src tests`; all clean before committing. Do not change `GEOMETRY_VERSION`, golden GDS files, anything
under `ic-opt-library`; nothing private (demo_6m only). Do not edit `docs/refactor/BACKLOG_CN.md`. Commit style of the
repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; append `## 4. Record` to this file.
Do not merge or push.

## 4. Record

Status: done on branch `n65-stub-chamfer` (2026-10-07, from main `bf25119`), not merged. The rule, the record, the
documents and the tests: commit `796cc8b`; this record: the commit after it.

What was done:

1. The rule (`fixture.py`). `add_ground_fixture` lays out every stub first (port, ring side, position, half width) and
   asks `_facing_chamfers` for each stub's two side chamfers, then draws the outlines with them and runs T19.5's
   `_refuse_touching_stubs` on what it drew. `_facing_chamfers` sorts the stubs of each ring side along that side; for
   each pair of neighbours it measures the gap between their chamfered outlines at the ring's inner edge in drawn
   nanometres (each end snapped as `Cell.add_polygon` snaps it, from the very float expressions the outline uses); below
   the minimum, both facing sides get one value: the largest multiple of the profile's manufacturing grid at which the
   gap is at least the minimum, never below 0, never above `stub_chamfer_um`. Each side faces one neighbour at most, so
   each is decided once; outer sides and stubs without a close neighbour keep `stub_chamfer_um`. The minimum is
   `fixture_min_space_um(conductor, process)`: the profile's `metal_width_space[<fixture metal>].min_space_um`; None --
   no adjustment -- in reference mode and for a metal the profile gives no minimum. An unchanged side is drawn from the
   same expression as before (`y + half + ch`), so an unchanged build is main's float for float.
2. The record. `Cell.stub_chamfers_um`, `GeometryGenerationResult.stub_chamfers_um` and the manifest's
   `geometry.stub_chamfers_um`: `{port: {side: chamfer_um}}` for every port, sides named by the way they face (`bottom` /
   `top` for a stub on the ring's left or right, `left` / `right` on its bottom or top: `fixture.STUB_SIDES`), written
   only when some side differs from `stub_chamfer_um`; otherwise the result carries None and the manifest nothing.
3. Documents: `FIXTURE_METAL_NOTE` (reference.py) says it beside T19.5's sentence, `docs/em/devices.md` regenerated; one
   bullet in the pcell README.

The N-65 geometry: the author-spec skill's joint example (demo_6m, xfm_bs, windings M6 / M5, both taps on M4, `inner_margin_um`
4, `ring_width_um` 70, `stub_length_um` 2) with `stub_chamfer_um: 2` and secondary width 6.5 -- the figure's case. The
primary tap's stub runs between the P2 and N2 stubs, whose inner edges sit 7.5 µm off the centre line; at the ring that
leaves 3.5 − w/2 µm on either side of it for a primary width w. Built on main's sources and on these, fixture on M1:

| primary width (µm) | main | this branch |
|---|---|---|
| 4 … 6.5, 6.8 (gap ≥ 0.1) | builds, gate passes | byte for byte (GDS, port file, manifest) |
| 6.85 | gate refuses: `[min_space] M1 x2` | facing chamfers 1.985 (1.9875 floored to 5 nm), gap 0.105, passes |
| 7 | gate refuses: `[min_space] M1 x2` (the N-65 finding) | facing chamfers 1.95, gap 0.1, passes |
| 7.05, 7.5 … 12 | refused at layout: P2 / CTP stubs overlap (T19.5) | (7.5 − w/2 − 0.1) / 2: 10 → 1.2, 12 → 0.7; passes |

Of the 94 builds main makes in that family (widths 4-12 by 0.5 plus 6.8 / 6.85 / 7.05; chamfers 0, 1, 2; fixture metal
absent or `auto`), 88 are identical in all three files and 6 differ in GDS and manifest (the port file is the same):
chamfer 2 at 6.85 and 7, chamfer 1 at 11, each on both metals -- exactly those with a gap between 0 and 0.1 µm. The 26
builds main refused now build.

Tests: `tests/ic_opt/pcell/test_fixture_chamfer.py`, 27 (section 2): widths 7 and 10 build, pass the gate (`_audit`), record
CTP's two sides, P2's lower and N2's upper at 1.95 / 1.2 and every other side at 2, leave exactly 0.1 µm at the ring
(from the manifest's ports, and on the GDS: no M1 edge pair under 0.1 µm, two under 0.101), and are refused as on main
with the rule taken out; widths 6 and 6.8 (the exact minimum) and chamfer 0 at 7 and 12 byte for byte against the build
without the rule, nothing recorded; the grid floor at 6.85; widths 4 to 12 by 0.5 all pass the gate; xfm_tw's top and
bottom stubs (sides `left` / `right`); `_facing_chamfers` alone (three neighbours, order, chamfer 0, no minimum, a pair
that touches even as rectangles gets 0); reference mode adjusts nothing. Counts: `tests/ic_opt/pcell` 433 passed, 635
skipped before, 460 passed, 635 skipped after; `tests/ic_opt/test_lib_tap.py` 13 passed before and after; `ruff check src
tests` clean.

Byte for byte: main's tests (all of `tests/ic_opt`) run with main's sources and with these, every GDS write and every
generator output captured per test (705 a side: 579 GDS, 63 port files, 63 manifests in 298 tests): 703 identical by
content. The two others: T19.5's chamfered case (below; the only build in the whole run with a shortened chamfer), and
the candidate `test_lib_suggest_shows_measured_footprints_and_the_drawn_candidates_own` draws, a different device on
different runs of main itself (three runs of that test on main gave both hashes). The 1582 GDS, `emx_ports.txt` and
`geometry_manifest.json` at the same basetemp path (537 / 524 / 521) are identical except two GDS the tests write with
klayout's default writer (`fixture.gds`, a toy plugin's `strip.gds`): four timestamp bytes, the layouts equal. Main's tests
fail on these sources only in T19.5's chamfered case and `test_reference` (devices.md regenerated); 1346 passed, 647
skipped (main: 1348, 647). The 19356 manifests under `ic-opt-library` on this machine (read only) all have
`stub_chamfer_um` 0, so no library row changes.

Deviations and what the specification did not foresee:

1. **T19.5's chamfered case now builds.** The rule takes the profile's minimum for any fixture metal (only reference mode
   is excepted), so it also runs on a metal chosen under `free`, where the gate exempts spacing among fixture shapes, and,
   running before the touching check, it rescues overlapping chamfers there: `test_chamfered_stubs_are_judged_by_their_outlines`
   (3 µm secondary opening, chamfer 1, M4: tips 0.5 µm apart, roots 1.5 µm into each other) was refused and now builds
   with 0.2 µm facing chamfers, M4's 0.1 µm at the ring. The test now asserts that, and keeps the refusal across the
   roots in reference mode (the pcell called without a profile). On `auto` (M3) the N-65 family changes the same widths
   as on M1, though main's gate passed those builds there. If spacing on a `free` metal should stay untouched, the rule
   needs one more condition (skip when the build's metal rule is `free`); I followed the wording.
2. **Since T19.5 the finding reaches the gate only at 7 µm (and 6.85 … 7).** Above it the stubs overlap and were refused
   at layout. At exactly 7 the CTP and P2 outlines share one corner on the ring, and KLayout's `interacting` does not count
   two collinear root edges that meet end to end (two diagonal box corners it does), so T19.5's check let it through to
   the gate. With a profile the rule now covers that case; in reference mode such a corner contact still builds. It is no
   EMX concern (the G pins sit at the tips; every stub joins the ring anyway), so it is left as is.
3. "The two stubs that met": in this geometry the CTP stub meets P2 and N2 alike, so four sides of three stubs change;
   the tests assert all of them, and that P1, N1, CTS and the outer sides keep 2.
4. Width 6 leaves a 0.5 µm flat, not the minimum; the exact boundary is 6.8 (gap 0.1, unchanged), tested as well.
5. On the grid: the gap equals the minimum when (tip gap − minimum) is an even number of grid steps, else it is one step
   more (6.85: 0.105 µm). Exactly the minimum would need a half-grid chamfer, off the manufacturing grid.
6. Only `min_space` is read, as specified; a fixture metal with a `wide_parallel_spacing` rule is not considered (demo_6m
   has such rules on M5 / M6 only, which carry port leads and so never hold the fixture here; the rule's 20 µm parallel
   length is far above the stubs' lengths in this geometry, 8.75 µm at most).
