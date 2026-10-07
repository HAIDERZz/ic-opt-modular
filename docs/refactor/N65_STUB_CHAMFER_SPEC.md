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
