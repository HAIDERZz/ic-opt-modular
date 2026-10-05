# T19.4 — same-metal center taps on the broadside transformer (xfm_bs)

Status: specification (2026-10-05), for the coding subagent. Decision behind it: the user's instruction of 2026-10-05
("做 然后先用同层抽头"): the per-window tapped-twin recipe (T19.5) is to build its tapped rows with the construction the
user's own transformers use -- each winding's center tap drawn on the winding's OWN metal -- not the via-stack tap the
product draws today. Data behind it (`ic-opt-accept/t18_qdiag/RESULT_CN.md`): on the same geometry a same-metal tap costs
about 3 % of Q, a via-stack tap about 10 %; L and k move by under 3 % either way.

Read before writing: `src/ic_opt/em/pcell/_pcell_xfm_bs.py` (`xfm_bs`, `_bs_winding`, `_bs_center_tap`,
`ct_lead_to_edge`), `_pcell_primitives.py` (`base_lead`, `base_lead_pair`, `base_lead_jog`, `vias`),
`generator_plugin.py` (`CleanPortXfmBsConfig`: `_ct_metals_not_m1`, `_ct_below_windings`, the serializer, how
`ct_primary_metal` / `ct_secondary_metal` reach the pcell; `_FixedXfmPortOrderMixin` for the six-port order),
`_pcell_core.py` (`_xfm_net_short`, `finalize_emx_ports`), `fixture.py` (`add_ground_fixture`, `fixture_metal`),
`reference.py` (the xfm_bs description that `docs/em/devices.md` is generated from), the existing xfm_bs tap tests under
`tests/ic_opt/pcell/` (grep `ct_primary_metal`, `CT_P_ME`, `CTP`).

## 1. What changes

### 1.1 The tap metal may be the winding's own metal

Today `ct_primary_metal` / `ct_secondary_metal` must sit strictly below their winding (`_ct_below_windings`, and the
pcell's fail-closed guard in `xfm_bs`): the tap is a W x W via stack at the winding's closed column dropping to the tap
metal, then a lead on the tap metal. After this ticket:

- tap metal **below** the winding: exactly today's via-stack tap, byte for byte;
- tap metal **equal to** the winding's metal (compared by stack position, so `"10"`, `"M10"` and the position agree):
  a **same-metal tap** -- no via stack; the tap lead is drawn on the winding's metal, from the winding's closed column
  outward along the same centre line and to the same far end as today's tap lead (`ct_lead_to_edge`), overlapping the
  column so it is one polygon with the winding; the tap port (`CTP` / `CTS`) is registered at the lead's far tip on the
  winding's metal and its pin layer;
- tap metal **above** the winding: refused, as today (message: "must sit at or below").

Only `xfm_bs` changes. The other families that take tap metals (`ind_sym`, `xfm_ms`, `xfm_tw`, `xfm_il`, `xfm_balun`)
keep their rules; `xfm_ms` also calls `_bs_center_tap` for its primary -- keep its guard refusing an equal metal (a
later ticket may lift it; say in the record whether that would be as small as here).

### 1.2 The tap width

Two new optional fields, `ct_primary_width_um` and `ct_secondary_width_um` (> 0, `multiple_of=0.01`, default None):

- None: the tap lead is as wide as its winding (today's width) -- byte for byte;
- a value: the width of a **same-metal** tap's lead (the user's devices use a 3 µm tap on a 5 µm winding);
- refused with a via-stack tap (its W x W stack and lead keep the winding width: that construction does not change), and
  refused when the matching tap metal is absent;
- the serializer drops an absent width, so existing configs keep their manifests.

The pcell gets matching keyword arguments (`CT_P_W`, `CT_S_W`, default None); the cell name and `params` carry them
only when set (unique names for distinct geometries, unchanged names otherwise).

### 1.3 What does not change

- The two windings, their leads and openings, the port order `[P1, N1, P2, N2, CTP, CTS]`, `ct_lead_to_edge`.
- `_xfm_net_short` still fails closed on any overlap between the two windings' nets on one layer. A same-metal tap lies
  on its own winding's metal; where it crosses the other winding it is on a different metal (the primary's tap leaves
  through the secondary's opening side and vice versa) -- that is a crossing, not a short.
- The ground fixture: a same-metal tap adds no metal to the device, so `ground_fixture.metal: auto` picks the same metal
  for a tapped device as for the untapped one (a test; with via-stack taps it does not -- T19 record).
- The DRC gate. Note for the documents and the record: the tap port sits between the other winding's port pair, and its
  ground stub runs between theirs on the fixture's metal; when the other winding's lead gap is narrow (a small opening)
  the three stubs can violate that metal's minimum spacing. The existing `primary_port_spacing_um` /
  `secondary_port_spacing_um` (M3.1, the lead jog) widen the other winding's port pair to make room. This ticket does
  not choose a spacing automatically; the recipe of T19.5 does, per row.

## 2. Tests (demo_6m; a new `tests/ic_opt/pcell/test_xfm_bs_same_metal_tap.py`)

1. Same-metal taps on both windings build: ports `CTP` on the primary's metal, `CTS` on the secondary's, at the same x
   as the via-stack build's tap ports (the far ends do not move); the device draws nothing on any metal below the two
   windings and no via layer (compare with the via-stack build of the same geometry, which does).
2. Widths: `ct_*_width_um = 3` gives a tap lead whose y extent is +-1.5 µm about the centre line; absent gives the
   winding width.
3. Refusals at validation, with messages naming the fields: tap metal above its winding; a width with a via-stack tap;
   a width without its tap metal. The pcell refuses a tap metal above its winding too (fail closed, as today).
4. Byte for byte: every existing xfm_bs configuration in the tests and the golden cases -- no taps, via-stack taps --
   gives the same GDS bytes, port file and manifest as before the change.
5. The fixture's metal under `auto` is the same for an untapped and a same-metal-tapped device of one geometry, and
   differs (lower) for the via-stack-tapped one.
6. The product DRC gate passes for a representative same-metal-tapped device with its fixture on the `auto` metal; and,
   if demo_6m's rules allow constructing it, a narrow-opening same-metal-tapped device fails on the fixture metal's
   minimum spacing with the natural port spacing and passes with the other winding's port spacing widened (otherwise say
   in the record why the case cannot be built on demo_6m).
7. `xfm_ms` still refuses a primary tap metal equal to its winding.

## 3. Documents

- `reference.py`: the xfm_bs tap paragraph (same-metal or via-stack, the widths, where the tap port sits and the
  port-spacing note of 1.3); regenerate `docs/em/devices.md` (`python -m ic_opt.em.pcell.reference > docs/em/devices.md`).
- `docs/refactor/BACKLOG_CN.md` section 0.12: a row `T19.4` -- done on the branch, the commit, the test counts (Chinese,
  the style of the neighbouring rows).
- Section 5 of this file: the record (what was done, the commits, test counts, deviations).

## 4. Working rules for the coder

Branch `t19-4-same-metal-tap` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t19-4-same-metal-tap`
(created from main; run `git merge --ff-only main` first to confirm the base). Python
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t19-4-same-metal-tap/src` for every run (the main tree is
the editable install). Never `uv run`, `uv sync` or pip. Run `tests/ic_opt/pcell` and `ruff check src tests`; both clean
before committing. Do not change `GEOMETRY_VERSION`, the golden GDS files, or anything under `ic-opt-library`. Nothing of
the private process (metal names beyond demo_6m's, thicknesses, rule values, private paths) in code, tests or docs.
Commit trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not merge or push; report the commits, the
exact test counts, and every deviation from this specification.

## 5. Record

Status: done on branch `t19-4-same-metal-tap` (2026-10-05), not merged. Feature, tests and the regenerated
`docs/em/devices.md`: commit `2b8bf03`; this record and the backlog row: the commit after it.

What was done (sections 1-3):

1. The pcell (`_pcell_xfm_bs.py`). `xfm_bs` takes `CT_P_W` / `CT_S_W` (default None) and checks each tap before it draws
   anything (`_bs_tap_guard`): the tap metal at or below its winding by stack position, above refused ("xfm_bs: CT_S metal
   M6 must sit at or below the secondary M5"); a width only with a same-metal tap, positive, and in process mode not below
   the winding metal's min width (the max width stays the lead primitive's own check). `_bs_center_tap` takes `TAP_W`:
   with the tap metal equal to the winding's it draws only the `base_lead`, on the winding's metal, over the via-stack
   lead's x span (the closed column plus the lead run on to `ct_lead_to_edge`), `TAP_W` wide (None: W) about y = 0, and
   registers the port at the far tip on that metal and its pin layer; below the winding it draws the M13 via stack and
   lead as before and refuses a `TAP_W`. The widths enter the cell's params and its name (`_CTPW<w>` / `_CTSW<w>`) only
   when set. xfm_ms calls `_bs_center_tap` as before and keeps its guard.
2. The config (`CleanPortXfmBsConfig`). `ct_primary_width_um` / `ct_secondary_width_um` (> 0, multiple of 0.01, default
   None). `_ct_below_windings` is now `_ct_at_or_below_windings` and refuses only a tap above its winding
   ("ct_secondary_metal '6' must sit at or below secondary_metal '5': ..."); `_tap_widths_need_same_metal_taps` refuses a
   width without its tap metal ("ct_secondary_width_um 3.0 needs ct_secondary_metal ...") and a width with a via-stack tap
   ("ct_primary_width_um 3.0 applies to a same-metal tap only ...; ct_primary_metal '4' sits below primary_metal '6', a
   via-stack tap, which keeps the winding width"). The serializer drops an absent width (`DROPPED_WHEN_ABSENT`, with
   `pgs`), so a config without them keeps its manifest. The generator passes the widths to the pcell.
3. Documents: `reference.py` renders a paragraph after the xfm_bs table (`XFM_BS_TAP_NOTE`, through a `NOTES` table: the
   model has no docstring paragraph to carry it): same-metal or via-stack, the widths, where the tap port sits, the `auto`
   fixture metal, and the port-spacing note of 1.3. `docs/em/devices.md` regenerated.

Additions and deviations (the commit message says the same):

- **`requires_vias` follows the via-stack taps** (needed, not in this specification). The generator declared vias
  required whenever a tap was set; a build with only same-metal taps has no via, and the via landing audit refuses an
  empty audit when vias are declared. `CleanPortXfmBsConfig.has_tap_stack()` -- a tap metal below its winding's (or one
  no position resolves) -- decides it now; untapped and via-stack builds declare what they declared before.
- **The automatic stub width of a tap port follows the tap width** (`_auto_stub_widths`): with `stub_width_um` left out
  each stub is as wide as the lead it lands on (M13 ticket 09), so a 3 µm tap gets a 3 µm stub. Absent widths change
  nothing.
- **Half the tap width is a coordinate**: the lead is centred on the centre line, so the two widths joined
  `HALF_IS_A_COORDINATE`; on a profile with another grid they must be a multiple of twice it (D9), as `multiple_of=0.01`
  says for the 0.005 µm grid.
- The pcell's tap messages name the metals by the profile's names (`_metal_name`), no longer `M<position>`.
- Three existing tests said "a tap on its winding's own metal is refused" and now say "above is refused, equal builds":
  `test_config_metal_positions.py::test_metals_not_called_m_n_get_every_rule` (demo profiles) and, in the files that need
  the private profile, `test_pcell_inductor_python_port_clean.py::test_xfm_bs_failclosed_same_metal_and_ct_rules` and
  `test_clean_port_generator_plugin.py::test_xfm_bs_ct_metal_validators` (the changed lines use M6 / M5 only).
- Beyond section 3: two sentences in `src/ic_opt/em/pcell/README.md` (its xfm_bs paragraph said the tap goes "down to a
  lower metal") and one clause in the docstring of `drc_audit._ct_chain` (a same-metal tap adds no expected conductor:
  its chain is empty, so the gate expects the two winding metals only). `FUNCTION_MAPPING`'s xfm_bs entry is not
  extended (it already leaves out `STRAIGHT_EXTENSION` and the port spacings).

Tests: `tests/ic_opt/pcell/test_xfm_bs_same_metal_tap.py`, 24 tests, demo_6m. Section 2, point by point:

1. `test_same_metal_taps_run_on_the_windings_own_metals` (with and without a straight extension; each winding with its
   tap is one merged polygon) and `test_the_taps_are_on_their_windings_nets` (`connectivity.nets` on the written GDS);
2. `test_the_tap_width_sets_the_lead_about_the_centre_line` (the drawn lead, the port, the cell's name and params) and
   `test_the_automatic_stub_of_a_tap_port_is_as_wide_as_its_lead`;
3. `test_the_config_refuses_with_the_fields_named`, `test_the_pcell_refuses_fail_closed`,
   `test_half_the_tap_width_stays_on_the_profile_grid`;
4. `test_the_golden_bs_cases_are_unchanged_byte_for_byte` (the GDS is the golden file's bytes, the port files as these
   cases always wrote them, no width key in the manifest) and `test_widths_left_out_or_null_change_no_byte` (nine
   existing configurations); the before/after of everything else is the comparison below;
5. `test_auto_takes_the_same_fixture_metal_with_same_metal_taps` (M4 untapped and same-metal; M2 via-stack and one of
   each);
6. `test_the_drc_gate_passes_a_same_metal_tapped_device_on_the_auto_metal` and
   `test_a_narrow_opening_needs_the_other_windings_port_spacing_widened`;
7. `test_xfm_ms_still_refuses_a_primary_tap_on_its_own_metal`.

Runs (`PYTHONPATH=<worktree>/src`): `tests/ic_opt/pcell` 427 passed, 635 skipped (403 passed, 635 skipped before);
`ruff check src tests` clean; the seven em / library files that build xfm_bs (`test_em_pcell`, `test_em_circuit`,
`test_em_measure`, `test_library_footprint`, `test_library_xfm`, `test_library_composed`, `test_spec_library`) 114
passed, 1 skipped; the rest of `tests/ic_opt` 902 passed, 12 skipped. With the private profiles of this machine on
`IC_OPT_PROFILE_DIRS` (not asked for; run because three changed tests live in files that need them): `tests/ic_opt/pcell`
1061 passed, 1 skipped.

Byte for byte, a scratch comparison (not committed): main's test files run once with main's sources (`545cb91`) and once
with the branch's, every temporary directory kept, and every GDS, `emx_ports.txt`, `geometry_manifest.json` and
`*.coordinates.json` at the same path compared byte for byte. demo_6m (the pcell suite and the seven files above): 882
files, 44 xfm_bs generator builds among them (8 with via-stack taps). With the private profiles (the pcell suite): 1 236
files, 55 xfm_bs generator builds among them (12 with via-stack taps) and 20 GDS of pcell-level xfm_bs builds (the five
xfm_bs demos of `generate_all` and their coordinate files among them). All identical, except 9 GDS that tests write
themselves with klayout's default writer (a toy plugin's strip, an RDL via audit input, the empty and two-top-cell audit
inputs), which differ only in their BGNLIB / BGNSTR timestamps. Main's tests failed on the branch's sources exactly where
intended: the three tests above and `test_reference.py` (`devices.md` regenerated).

Not foreseen here:

1. **The narrow-opening case on demo_6m** (section 2, point 6). Under `auto` a same-metal-tapped bs on M6 / M5 has its
   fixture on M4, min space 0.1 µm. The gate fails only while the tap's stub stays within 0.1 µm of a neighbouring stub
   without touching it: stubs that touch or overlap merge into one polygon (one ground net) and pass. Built: a 1.55 µm
   secondary opening, a 3 µm primary tap, automatic stub widths -- the CTP stub (±1.5 µm) is 0.05 µm from the P2 and N2
   stubs (1.55 to 6.55 µm), two `min_space` findings on M4; `secondary_port_spacing_um` 10.1 µm (natural 8.1 µm) passes,
   and so does 8.2 µm (a gap of exactly 0.1 µm). With chamfered stubs (`stub_chamfer_um` c) the stubs close in toward the
   ring and the failing window widens to tip gaps between 0 and 2c + min space: with c = 1 µm and 5 µm stubs, secondary
   openings of 2.55, 3 and 4.55 µm fail (tip gaps 0.05, 0.5 and 2.05 µm), 2.5 µm (touching) and 4.6 µm pass, and the
   3 µm opening still fails with the port pair 2 µm wider. For T19.5: choose the other winding's port spacing so that the
   gap at the ring end, tip gap − 2 × chamfer, is at least the fixture metal's min space (microns on a thick fixture metal
   of a real stack, not 0.1 µm). In the terms of T19.5's port-room rule (G, the other winding's lead gap: its port spacing
   minus its width): G ≥ t + 2s + 4c, with t the width of the tap port's stub, s the min space and c the stub chamfer.
   Its `tap width + 2 x s` is the case c = 0 with automatic stub widths, where t is the tap width; an explicit
   `stub_width_um` makes the tap's stub that wide instead (`stub_width_by_port_um` can set it per port).
2. **The via-stack tap cannot build that geometry at all**: its stack's pad on the secondary's metal sits in the 1.55 µm
   opening and `_xfm_net_short` refuses it (test 6 checks this). Same-metal taps make small openings of the other winding
   buildable, which is where the stub spacing starts to matter.
3. **xfm_ms: lifting the guard would be as small as here.** The single-turn primary sits strictly above everything the
   multi-turn secondary draws (its winding, its crossunder one level down, its own tap at least two levels down), so a
   same-metal primary tap never meets the other net. In a scratch run with only the pcell's comparison relaxed (`>=` to
   `>`), demo_6m xfm_ms with `CT_P_ME` on the single-turn metal built DRC-clean for NT_M 2 and 3, CTP on the primary's
   net, the `auto` fixture metal unchanged (M3: the secondary's crossunder already holds M4). The change would be that
   comparison, the P side of `_ct_metal_rules` and the tests; for a width, `ct_primary_width_um` → `CT_P_W` →
   `_bs_center_tap(TAP_W=...)` (already there), and `_auto_stub_widths` already reads the field by name. `requires_vias`
   stays True (the multi-turn crossunder always has vias). The CTS side is ind_sym's tap, a different construction.
4. `docs/refactor/analysis/em/02_pcell_geometry_layer.md` still says the xfm_bs tap metals sit below their windings: a
   dated study (2026-09-22), left as it is.
