# T19.6 — the same-metal tap on xfm_ms's single-turn primary; `lib_tap` builds xfm_ms twins

Status: specification (2026-10-07), for the coding subagent; follows T19.4 (`T19_4_SAME_METAL_TAP_SPEC.md`, whose
record, section 5 item 3 under "Not foreseen", measured this change as small) and T19.5 (`lib_tap`). Decision behind it:
the user's "应当支持同层抽头" (2026-10-07) and the polish batch (`BACKLOG_CN.md` 0.13, item 5).

Read before writing: `src/ic_opt/em/pcell/_pcell_xfm_ms.py` (`xfm_ms`: its CT_P guard at line ~103, the
`_bs_center_tap` call for the primary), `_pcell_xfm_bs.py` (`_bs_center_tap` with `TAP_W`, `_bs_tap_guard` -- the T19.4
code), `generator_plugin.py` (`CleanPortXfmMsConfig`: `_ct_metal_rules`, `requires_vias`, how `CleanPortXfmBsConfig`
got `ct_primary_width_um` and `_tap_widths_need_same_metal_taps` in T19.4; `_auto_stub_widths`),
`src/ic_opt/recipes/lib_tap.py` (`SUPPORTED`, `WINDINGS`, how the twin config is built), `reference.py` (the xfm_bs tap
note of T19.4), `tests/ic_opt/pcell/test_xfm_bs_same_metal_tap.py`, `tests/ic_opt/test_lib_tap.py`.

## 1. What changes

1. **xfm_ms primary tap on its own metal.** `ct_primary_metal` equal to `primary_metal` (by stack position) draws
   T19.4's same-metal tap on the single-turn primary: no via stack, the tap lead on the primary's metal from the
   closed column outward to `ct_lead_to_edge`, the `CTP` port at the far tip. Below the primary: today's via-stack tap,
   byte for byte. Above: refused ("must sit at or below"). The pcell guard (`>=` → `>`) and the config's
   `_ct_metal_rules` P side change accordingly; `requires_vias` stays True (the multi-turn secondary's crossunder always
   has vias). The single-turn primary sits above everything the secondary draws, so a same-metal primary tap never
   meets the other net (`_xfm_net_short` still checks).
2. **`ct_primary_width_um`** on `CleanPortXfmMsConfig` (> 0, multiple of 0.01, default None): the same-metal tap's
   lead width, as T19.4 defines it for xfm_bs (refused with a via-stack tap or without the tap metal; the serializer
   drops an absent width; `_auto_stub_widths` reads it; half the width is a coordinate). Passed to the pcell as `CT_P_W`
   → `_bs_center_tap(TAP_W=...)`.
3. **The secondary's tap is not changed**: it is ind_sym's tap on the multi-turn winding, which must reach the midpoint
   turn through a via stack (a same-metal lead would cross the turns). Its rules stay (at least two levels below).
4. **`lib_tap` builds xfm_ms twins.** `SUPPORTED` becomes the two generators. For an xfm_ms stratum `taps.primary`
   may be `"same"` or a metal below the primary; `taps.secondary` may be a metal at least two levels below the
   secondary or `null` (never `"same"`: refused with the reason of item 3); `secondary_width_um` refused for xfm_ms.
   The twin's fixture metal follows the same rule as for xfm_bs (the row's recorded metal; `shared` when a via-stack
   tap passes through it). Everything else of the recipe (window, rows, preflight, check, adopt) is the same.

## 2. Tests (demo_6m; `tests/ic_opt/pcell/test_xfm_ms_same_metal_tap.py`, additions to `tests/ic_opt/test_lib_tap.py`)

1. xfm_ms with `ct_primary_metal` = `primary_metal`: builds; `CTP` on the primary's metal at the via-stack build's x;
   nothing drawn below the two windings except the secondary's own crossunder and its tap stack when set; the
   DRC gate passes on the `auto` fixture metal; `connectivity.nets`: CTP on the primary's net.
2. Width: `ct_primary_width_um` sets the lead's y extent; refused with a via-stack tap or without the tap metal.
3. Refusals: a primary tap above the primary; `ct_secondary_metal` equal to the secondary (unchanged rule, message
   unchanged).
4. Byte for byte: every existing xfm_ms configuration (golden cases, the T19.4 and T19.5 tests' xfm_ms builds) gives
   the same GDS bytes, port file and manifest; the xfm_bs behaviour of T19.4 untouched (its tests pass).
5. `lib_tap` on an xfm_ms part (the fake EMX): `taps.primary: "same"` twins build and pass preflight; `secondary:
   "same"` is refused naming why; a via-stack secondary tap two levels down builds; the adopted stratum carries the
   ms quantities.
6. `ruff check src tests` clean; `docs/em/devices.md` regenerated if `reference.py` changes (the ms tap note).

## 3. Working rules for the coder

Branch `t19-6-ms-same-metal-tap` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t19-6-ms-same-metal-tap`
(from main; `git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`
with `PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/pcell`, `tests/ic_opt/test_lib_tap.py`,
`tests/ic_opt/test_library*.py`, `ruff check src tests`; all clean before committing. Do not change `GEOMETRY_VERSION`,
golden GDS files, anything under `ic-opt-library`; nothing private (demo_6m only). Do not edit `docs/refactor/BACKLOG_CN.md`.
Commit style of the repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; append `## 4. Record`
to this file. Do not merge or push.

## 4. Record

Status: done on branch `t19-6-ms-same-metal-tap` (2026-10-07), not merged. The pcell, the config, their tests and the
regenerated `docs/em/devices.md`: commit `a718125`; `lib_tap` for xfm_ms, its tests and documents: `ade1ff2`; this record:
the commit after it.

What was done (sections 1-2):

1. The pcell (`_pcell_xfm_ms.py`). `xfm_ms` takes `CT_P_W` (default None). Its CT_P guard is now xfm_bs's `_bs_tap_guard`,
   which gained a `family` argument for its messages (xfm_bs's own messages unchanged): the tap metal at or below the
   single-turn primary by stack position, above refused ("xfm_ms: CT_P metal M6 must sit at or below the single-turn
   primary M5"); a width only with a same-metal tap, positive, and in process mode not below the primary metal's min
   width. `_bs_center_tap` gets `TAP_W=CT_P_W`. The width enters the cell's params and name (`_CTPW<w>`) only when set.
   The secondary's tap (`_ind_ct_adjacency_guard`, ind_sym's CT path) is untouched.
2. The config (`CleanPortXfmMsConfig`). `ct_primary_width_um` (> 0, multiple of 0.01, default None), after
   `ct_secondary_metal`. `_ct_metal_rules`, P side `>=` → `>`: "ct_primary_metal '6' must sit at or below primary_metal
   '5': a tap runs on the primary's own metal (a same-metal tap) or drops to a lower one through a via stack"; the S side
   and its message unchanged. `_tap_width_needs_a_same_metal_tap` refuses a width without `ct_primary_metal` and with a
   via-stack tap (xfm_bs's messages). The serializer (`DROPPED_WHEN_ABSENT`), `_auto_stub_widths` and
   `_halves_on_the_profile_grid` already read `ct_primary_width_um` by name: only their comments changed. The generator
   passes `CT_P_W`; `requires_vias=True` stays.
3. `lib_tap` (`recipes/lib_tap.py`). `SUPPORTED` is the two generators; the stratum's and the part's generator are checked
   against both ("lib_tap builds tapped twins of clean_port_xfm_bs and clean_port_xfm_ms tables only"). On an xfm_ms part
   `_multi_turn_tap` refuses before anything is built, each naming why: the secondary's own metal (`"same"` or spelled
   out), a metal less than two levels below it (the crossunder's), and `secondary_width_um`. The primary follows the
   existing rules, its width included. The twin's fixture is the existing code (the rows' recorded metal; `shared` when a
   via-stack tap passes through it). The plan's adopt check appends an entry with the stratum's own generator.
4. Documents: `reference.py` `XFM_MS_TAP_NOTE` (in `NOTES`), `docs/em/devices.md` regenerated; the pcell README's xfm_ms
   tap paragraph; `drc_audit._ct_chain`'s docstring; lib_tap's module docstring, `docs/em/library.md` section 7b, the
   README's library paragraph and `skills/ic-opt/SKILL.md`'s lib_tap sentence (which tables, the secondary's rule).

Tests, section 2 point by point:

1. `tests/ic_opt/pcell/test_xfm_ms_same_metal_tap.py` (25 tests, demo_6m):
   `test_a_same_metal_primary_tap_runs_on_the_primarys_own_metal` (NT 3, NT 2, NT 3 with a 6 µm extension: CTP on M6 and
   its pin layer at the via-stack build's point, the other ports where the untapped build has them, every layer but M6
   the untapped build's shape for shape, no M3 or via added, one polygon on M6),
   `test_beside_a_secondary_tap_only_the_primarys_metal_changes`, `test_the_primarys_tap_is_on_the_primarys_net`
   (`connectivity.nets`, CTP alone and with CTS on M3),
   `test_the_drc_gate_passes_a_same_metal_tapped_ms_on_the_auto_metal` (NT 3 and 2: `auto` M3 as for the untapped
   device, conductors M6 / M5 / M4, the via audit the untapped build's, pass);
2. `test_the_tap_width_sets_the_lead_about_the_centre_line` (absent and 3 µm),
   `test_the_automatic_stub_of_the_tap_port_is_as_wide_as_its_lead`, `test_half_the_tap_width_stays_on_the_profile_grid`;
   the width refusals in the two tests of point 3;
3. `test_the_config_refuses_with_the_fields_named`, `test_the_pcell_refuses_fail_closed` (the secondary's own metal and
   one level below: rule and messages unchanged);
4. `test_the_golden_ms_cases_are_unchanged_byte_for_byte` (nt3, nt2: the golden GDS bytes, the port file, no width key)
   and `test_a_width_left_out_or_null_changes_no_byte` (nine configurations); the T19.4 bs tests pass; the comparison
   with main below;
5. `tests/ic_opt/test_lib_tap.py`, 4 new tests on a demo_6m xfm_ms library built through the real em_only chain and the
   fake EMX -- part `ms` (4 rows, NT 2 and 3, the fixture on `auto`: M3) and part `ms_m1` (2 rows, the default fixture,
   recorded as M1): `test_xfm_ms_twins_tap_the_primary_on_its_own_metal` (`"same"` resolves to M6, the width, CTP added and
   grounded, the fixture kept on M3 under `free`, every twin clean in the preflight);
   `test_xfm_ms_secondary_taps_are_via_stacks_at_least_two_levels_down` (a tap on M3 builds on `ms_m1` and is refused on
   `ms`, see "Not foreseen" 1; on M2 it is `shared` and builds beside a same-metal primary tap on the three-turn rows, the
   two-turn rows refused, "Not foreseen" 2); `test_xfm_ms_secondary_taps_that_cannot_be_drawn_are_refused_with_why`
   (`"same"`, `"M5"`, `"4"`, a secondary width; no EMX); `test_a_run_adopts_xfm_ms_twins_as_an_xfm_ms_table` (6 twins, L and
   k as the row's, Qp down by the tap, adopted as a stratum with the source's definition -- generator, dims with the turns,
   `nt_dim`, quantities with the `xfm_ms_dimensionless` maps -- whose dataset has 6 rows of 5 ports). One existing message
   updated (`test_calls_that_cannot_be_carried_out_are_refused_with_what_to_change`: two generators);
6. `ruff check src tests` clean; `docs/em/devices.md` regenerated and `test_reference.py` passes.

Two existing tests said a primary tap on the primary's own metal is refused: `test_xfm_bs_same_metal_tap.py` point 7
(`test_xfm_ms_still_refuses_a_primary_tap_on_its_own_metal`) is replaced by a comment pointing to the new file, and
`test_pcell_inductor_python_port_clean.py::test_xfm_ms_ct_p_must_sit_below_single_plane` (private profile; it tapped the
default AP primary on AP) is now `..._at_or_below_...`: above refused, equal builds, the changed lines on M6 / M5 / M4 only.

Runs (`PYTHONPATH=<worktree>/src`; before: main `bf25119` with the same commands): `tests/ic_opt/pcell` 457 passed, 635
skipped (433 / 635 before); `tests/ic_opt/test_lib_tap.py` 17 passed (13); `tests/ic_opt/test_library*.py` 221 passed, 6
skipped (the same); `ruff check src tests` clean; commit `a718125` alone: pcell + lib_tap 470 passed, 635 skipped. With
the private profiles of this machine on `IC_OPT_PROFILE_DIRS` (not asked for; run because one changed test needs them):
pcell + lib_tap 1 108 passed, 1 skipped (main 1 080 / 1).

Byte for byte, a scratch comparison as T19.4's (not committed): main's test files run once with main's sources and once
with the branch's (`ade1ff2`), every temporary directory kept, and every GDS, `emx_ports.txt`, `geometry_manifest.json`
and `*.coordinates.json` at the same path compared byte for byte.

- demo_6m (`tests/ic_opt/pcell`, `test_lib_tap.py`, `test_library*.py`, `test_em_pcell.py`, `test_em_circuit.py`,
  `test_em_measure.py`, `test_spec_library.py`): 1 522 files (517 GDS, 504 port files, 501 manifests); among them 171
  xfm_bs generator builds (92 untapped, 16 with via-stack taps, 62 with T19.4's same-metal taps, 1 with one of each) and
  31 xfm_ms, all untapped. All identical except 2 GDS that tests write with klayout's default writer (they differ only in
  their BGNLIB / BGNSTR timestamps) and 1 manifest that main's lib_tap refusal test edits itself after its cases (with
  the branch's sources it stops at its first case, whose message changed; the file is then the untouched row's). Main's
  tests failed on the branch's sources exactly where intended: `test_reference.py` (devices.md), the bs file's point 7,
  lib_tap's generator message.
- With the private profiles (`tests/ic_opt/pcell`, `test_lib_tap.py`): 1 621 files (690 GDS, 454 port files, 447
  manifests, 30 coordinates files); 182 xfm_bs generator builds (99 untapped, 20 via-stack, 62 same-metal, 1 mixed), 48
  xfm_ms (3 with both taps, via stacks). The same two kinds of exception (2 timestamp-only GDS, the same manifest); the
  same intended failures plus the private test changed above.
- Main's tests build no tapped xfm_ms on demo_6m, so a scratch script built 400 existing configurations with both sources:
  xfm_ms over NT 2-5 with no tap, a via-stack primary tap (M4, M3), a secondary tap (M3, M2) and both, each on the bottom
  fixture, `auto` under `free` and `auto` under `shared`, plain, with a 6 µm extension, a 10 µm centre spacing and wider
  port spacings, plus a shielded one; xfm_bs with its taps on the same fixtures; and 9 pcell-level builds (reference mode
  and demo_6m). 316 built (253 xfm_ms and 54 xfm_bs generator builds, 9 pcell builds): 939 files, all identical; the 84
  refused gave the same message on both sides.

Not foreseen here:

1. **On an `auto` table the secondary's lowest-allowed tap lands on the fixture's metal.** For an xfm_ms whose primary
   sits right above its secondary, `auto` takes the highest metal the untapped device leaves empty: the secondary's
   crossunder holds secondary-1, so the fixture goes to secondary-2 -- exactly the highest metal the secondary's tap may
   use. A twin with its secondary tapped there would carry the CTS lead on the fixture's metal, and the fixture refuses it
   ("ground fixture: metal 'M3' (M3) carries the lead of port CTS"; the preflight lists it). So "a via-stack secondary tap
   two levels down builds" (section 2 point 5) holds where the rows' fixture is elsewhere (the test's `ms_m1`, on the
   bottom metal); on an `auto` table the secondary's tap goes at least one level lower and passes through the fixture's
   metal (`shared`). On the user's AP / M10 tables (ring on M8, accepted 2026-10-07) that means CTS on M7 or below, not M8.
   Section 1 point 4 keeps the fixture rule as it is, so lib_tap does not refuse this up front (as for an xfm_bs via-stack
   tap onto the fixture metal since T19.5); the docstring and `library.md` say it. Refusing it before the preflight,
   naming the lowest metal, would be a small addition if wanted.
2. **Both taps need an odd `secondary_turns`.** ind_sym's tap leaves the multi-turn winding on the right for an even turn
   count, the side the primary's tap always takes, on the same centre line, so the CTP and CTS stubs overlap (by 4.5 µm
   on the golden geometry with automatic stub widths) and the fixture refuses the build. That is main's behaviour with a
   via-stack primary tap too (checked: NT 2 and 4 refused, 3 and 5 built, either primary tap), not something this ticket
   adds; moving one of the taps would change existing tapped geometries, so nothing changed. Consequence for lib_tap: on
   an xfm_ms table a twin with both taps of an even-turn row is refused in the preflight. The xfm_ms paragraph of
   `devices.md` says it.
3. Section 2 point 3, "a primary tap above the primary": demo_6m's golden ms primary is on M6, the top metal, so the
   refusal is tested on an M5 / M4 ms (config and pcell).
4. Not changed: `_pcell_demo.py`'s M13 ticket 06 entry still says "CT_P_ME strictly below SINGLE_ME" (a dated record of
   that ticket), and `FUNCTION_MAPPING`'s xfm_ms entry does not list `CT_P_W` (it already leaves out `STRAIGHT_EXTENSION`
   and the port spacings; T19.4 did not add `CT_P_W` / `CT_S_W` to xfm_bs's either).
