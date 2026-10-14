# N-121: the same tap-pad gap check for the multi-turn transformer (and a look at the balun) (2026-10-14)

N-93 (merged 3cdf697; spec `N93_N94_N53_SMALL_ITEMS_SPEC.md` section C and its record) made `clean_port_xfm_bs` refuse, before the
fixture and the DRC gate, a via-stack centre tap whose pad (or lead, when the tap metal is that metal) on the other winding's metal
sits closer to that winding than the profile's spacing for those widths over that run (`_pcell_xfm_bs._bs_tap_stack_gap`, measured on
the drawn sub-cells, `_pcell_core._required_parallel_spacing`, strict like the audit, skipped in reference mode). Its coder reported
that `clean_port_xfm_ms` has the same case unguarded -- on demo_6m a tapped build that the audit fails with `min_space` on M5, x2 --
and that `xfm_balun`'s taps were not examined. User decision 2026-10-14: do it.

## 1. What changes

- `clean_port_xfm_ms` (`_pcell_xfm_ms.py`): the same check for its taps -- the single-turn primary's tap (same-metal or via-stack,
  T19.6) and the secondary's via-tower taps (at least two layers below) -- wherever a tap's shapes on another winding's metal face
  that winding: measured on the drawn sub-cells before the device cell, the fixture and the gate, refused with `PortError` naming the
  gap, the parallel run, the rule, the widths and the opening(s), like N-93. Reuse N-93's helper where it fits (move it to a shared
  module if both generators need it) without changing a byte of what `xfm_bs` builds or refuses.
- `xfm_balun`: find out whether its taps can face the other winding's metal the same way. If they can, the same check; if not, say
  in the record why not (with the drawing's facts).
- Every build that passes today builds the same bytes; a build refused today by the gate on the crossed metal is refused by the
  generator instead, with numbers; nothing else changes.

## 2. Acceptance

(a) the demo_6m case the N-93 coder found, refused with numbers (reconstruct it; the N-93 scratch evidence may still be in
`/tmp/claude-1011/-home-zzchen-Agent-virtuoso-EDA-AI-AGENT/22a251ad-03d3-4a55-8437-b0a4e97a3152/scratchpad/n93/`); (b) every golden
GDS and the library replay byte-identical with `main` for demo_6m and the private profile (the record of N-93 shows how: main's tests
run with main's and the branch's sources; `docs/refactor/reports/pcell_plan/pcell_byte_replay.py library`); (c) parity with the DRC
gate on a sweep of xfm_ms (and balun, if checked) tapped configurations, built with the check and with it switched off: the check
refuses exactly those whose audit finds `min_space` / `wide_parallel_spacing` on the crossed metal; (d) the pcell suite with and
without the private profile.

## 3. Working rules for the coder

Branch `n121-xfm-ms-tap-gap` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n121-xfm-ms-tap-gap` (from `main`; `git merge
--ff-only main` first). `PYTHONPATH=<worktree>/src` with `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`;
never `uv run` or `uv sync`; `git -C <worktree>` and absolute paths; the private profile with
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles` -- its numbers are confidential:
none in committed code, docs or tests (tests use demo_6m or read the profile at run time). Targeted tests plus `ruff check src
tests`; pytest with `OMP_NUM_THREADS=2`; at most 8 threads in all; no EMX. Scratch work in the session scratchpad, not committed. One
commit for the fix with tests and documents, one for the `## Record` section below. No merge, no push, no edit of
`docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

Branch `n121-xfm-ms-tap-gap`, from `main` at 3542196; `git merge --ff-only main` (run before committing) brought it to 58e6924
-- N-119 merged, nothing under `src/ic_opt/em`, `tests/ic_opt/pcell` or `docs/em` -- and main's tests were compared at both. One
commit for the fix with its tests and documents (7574da0), one for this record; nothing merged, nothing pushed,
`docs/refactor/BACKLOG_CN.md` untouched.

| file | change |
| --- | --- |
| `src/ic_opt/em/pcell/_pcell_guards.py` | `_tap_stack_gap`: N-93's measurement, moved here from `_pcell_xfm_bs` with `_drawn_on` and `_run_side_by_side_um` (verbatim), for the metals the caller names and in the family's own words. On each metal where both draw, the tapped winding's shapes (a single-turn winding draws nothing below its own metal but its tap) against the other winding's: every pair of facing edges closer than the widest spacing those widths can need, judged against `_required_parallel_spacing`; the largest shortfall over those metals raises `PortError`. |
| `src/ic_opt/em/pcell/_pcell_xfm_bs.py` | `_bs_tap_stack_gap` keeps its signature, its condition (the tap metal at or below the other winding's metal, its winding above it) and its message, and measures its one metal through `_tap_stack_gap`. Docstrings of `_bs_center_tap` and `_bs_tap_stack_gap`. |
| `src/ic_opt/em/pcell/_pcell_xfm_ms.py` | `_ms_tap_stack_gap`, called by `xfm_ms` right after `_xfm_net_short`: a via-stack `CT_P_ME` tap measured on every metal it reaches from the secondary's own metal down -- the secondary's metal (turns and leads), its crossunder's, the metals of its own tap's tower and lead -- each named so in the message. The `xfm_ms` docstring. |
| `src/ic_opt/em/pcell/_pcell_xfm_balun.py` | `_balun_tap_stack_gap`, called inside the balun's existing inter-net refusal when the nets do not touch (below). The docstrings of `xfm_balun` and `_add_balun_ct`. |
| `src/ic_opt/em/pcell/reference.py`, `docs/em/devices.md` | The xfm_ms tap paragraph (`XFM_MS_TAP_NOTE`) states the rule, the metals, the numbers the refusal names, the gap `secondary_opening_um` minus half `primary_width_um` only where the pad lies alongside the leads, reference mode unchecked; `devices.md` regenerated (`python -m ic_opt.em.pcell.reference`). The balun has no tap paragraph there; none added. |
| `src/ic_opt/em/pcell/README.md` | xfm_bs: the measurement is shared; xfm_ms: one paragraph on the check; balun: a bullet for the inter-net gate and the tap's numbered refusal. |
| `tests/ic_opt/pcell/test_xfm_ms_tap_pad_gap.py` | 26 tests, below. |

The refusals on demo_6m:

- xfm_ms, N-93's coder's case (primary on M6, OD 100, W 10; secondary on M5, OD 76, NT 3, W 3, opening 5.2; the primary tapped to
  M4), which main built and the audit failed with `min_space` M5 x2: `xfm_ms: the single-turn primary's via-stack tap (CT_P_ME=M4)
  draws a 10 x 10 um pad on M5, the multi-turn secondary's metal, 0.200 um from the multi-turn secondary's own M5 metal over a
  10.000 um parallel run, below the 0.4 um profile demo_6m asks between M5 lines W_S=10 and W_M=3 um wide over that run; the
  multi-turn secondary's leads lie OPENING_M=5.2 um off the centre line (a wider OPENING_M or a narrower W_S moves the pad away
  from them; a tap on the single-turn primary's own metal draws nothing on M5)`. At opening 5.4 (the gap equal to the rule) it
  builds. The pcell's names, as N-93's message has them: W_S is the single-turn primary's width, W_M and OPENING_M the
  secondary's (`primary_width_um`, `secondary_width_um`, `secondary_opening_um`).
- xfm_balun, the golden balun with its primary tapped to M4 at OPENING_S 2.6: `xfm_balun: the primary's via-stack tap
  (CT_P_ME=M4) draws a 5 x 5 um pad on M5, the secondary's escape metal, 0.100 um from the secondary's own M5 metal over a 5.000 um
  parallel run, below the 0.4 um profile demo_6m asks between M5 lines W_P=5 and W_S=5 um wide over that run; the secondary's
  escape bars lie OPENING_S=2.6 um off the centre line (a wider OPENING_S or a narrower W_P moves the pad away from them)`, where
  main said `xfm_balun: primary/secondary nets are closer than the inter-net (incl. wide-parallel) spacing floor on a shared
  layer and the NT_P=1 primary has no compact re-plan DOF; a larger secondary opening usually moves the escape bars off the
  primary's crossunder legs (currently OPENING_S=2.600, W=5.000), before resorting to OD/W/S changes`.

### What the balun showed

- Its taps are via-stack taps only (the tap metal strictly below the balun plane) and single-turn only (`_add_balun_ct` refuses
  NT >= 2). The primary opens left and its tap leaves its closed column rightward, toward the secondary's escape: the escape's two
  bars run on ESCAPE_ME (the metal directly below the plane; the generator never sets another) at +-[OPENING_S, OPENING_S + W_S],
  from the secondary's arm tips out under the primary's ring to just beyond it. A primary tap through ESCAPE_ME draws its
  W_P x W_P pad between them -- tapped to ESCAPE_ME, its lead too, which runs on between them -- OPENING_S - W_P/2 away where it
  lies alongside: N-93's case on the balun (demo_6m, the golden balun tapped to M4: pad at +-2.5 um, bars from +-OPENING_S).
- Unlike xfm_ms it was never unguarded. Since design-region issue 04 the balun runs `_xfm_nets_are_drc_separate` on the two
  complete nets, taps included, before the fixture -- every shared metal, the metal's min space and the audit's own
  `wide_parallel_spacing_violations` -- and every build that builds passes it (a primary without the compact planner, the
  single-turn one among them, is refused there; a compact two-turn primary is re-planned against it). So the DRC gate never saw
  such a build; the refusal named no gap and blamed "the primary's crossunder legs", which a single-turn primary does not have.
  Switched off, the golden balun at OPENING_S 2.6 builds and its audit has `min_space` M5 x2 between the pad and the bars.
- The same check: `_balun_tap_stack_gap` measures the primary's tap on every metal it reaches below the plane against the
  secondary (its escape bars and end pads, its own tap), as `_tap_stack_gap` does for xfm_bs and xfm_ms, and runs inside that
  refusal when the nets do not touch: where the tap is what comes too close, the refusal names the gap, the run, the rule, W_P,
  W_S and OPENING_S; otherwise, and for a contact, the message is main's. Nothing that builds changes and nothing it refused
  builds, by construction.
- The secondary's tap leaves the secondary's closed column leftward, toward the primary's leads, which run on the plane, not
  below it. Below the plane a single-turn primary draws only its own tap, at its closed column on the far side, and a multi-turn
  primary (no primary tap is possible then) only its crossunders, whose landings lie on its own rings (`_check_landing_pads`): no
  nearer to the secondary's pad than the two windings are on the plane, which the inter-net gate holds. In the sweeps below, 718
  demo_6m / 673 private-profile configurations tap only the secondary: built without the inter-net gate, none has a spacing
  finding below the plane from that tap (the two private ones with a finding there are a two-turn primary's compact bridge against
  the escape bars on the right, with the secondary's tap and without it -- the compact re-plan's case). So the secondary's tap is
  not measured on its own; the inter-net gate still covers it.

### xfm_ms: the metals, and the secondary's tap

The single-turn primary lies strictly above the multi-turn secondary (`SINGLE_ME > MULTI_ME`), and draws nothing below its own
metal but its via-stack tap. The secondary draws on its own metal (turns, leads), on its crossunder's (the conductor below it) and,
tapped, on the metals of its tap's via tower down to its tap metal, at least two levels below its own. So the check measures every
metal the primary's stack reaches from the secondary's metal down, against everything the secondary draws there, and the message
names which of the three it is ("the multi-turn secondary's metal", "... crossunder metal", "a metal the multi-turn secondary's
tap reaches"); a tie goes to the secondary's own metal. The secondary's tap never reaches the primary's metal: it can meet only
the primary's own stack, below the secondary, and is measured there as part of the secondary. In every sweep refusal the metal
named is the secondary's own: on demo_6m and the private profile the secondary's metal asks for at least the spacing of the metals
below it, and where the stack meets the crossunder or the secondary's tower lower down it meets the secondary's turns or leads as
closely on the secondary's metal. On a profile whose lower metals ask for more (test 4), the check names them, and the audit agrees.

### Tests

`tests/ic_opt/pcell/test_xfm_ms_tap_pad_gap.py`, on demo_6m unless noted:

1. `test_a_pad_between_the_secondarys_leads_is_refused_with_its_numbers` (NT_M 3, 2, 4) -- the case above, the message's parts,
   the gap equal to the rule and the golden opening build.
2. `test_the_rule_follows_the_widths_and_the_parallel_run` -- tapped to M5, the primary's 8 um lead runs 36 um between the
   secondary's 6 um leads (40 um long): 1.5 um refused under M5's wide-line rule, a gap equal to it builds; the same 1.5 um from a
   pad (an 8 um run) or from 5 um leads (not wider than the threshold) builds.
3. `test_the_gap_is_measured_from_the_actual_placement` -- a smaller primary moved into the secondary's turns (OD 60, centre
   spacing 10.2): its pad 0.2 um from the innermost turn's closed column where the opening keeps the leads 2.5 um away (10.4
   builds, 10.0 touches); an inward jog of the secondary's port pair (13.4 refused, 14.0 builds); the straight extension.
4. `test_every_metal_of_the_secondarys_the_stack_reaches_is_measured` -- on demo_6m with M4 / M3 asking 1.0 / 1.2 um (written to
   a temporary profile), a two-turn secondary tapped to M2 and a 4 um primary 0.6 um outside its tower: tapped to M4, refused on
   M4 ("the multi-turn secondary's crossunder metal"); tapped to M3, on M3 ("a metal the multi-turn secondary's tap reaches", the
   larger shortfall); built without the check, the GDS written and audited, exactly `min_space` M4, and M4 + M3; 1.2 um away both
   build; on demo_6m itself all build.
5. `test_contact_stays_a_short_and_nothing_else_changes` -- contact is still the short gate's; reference mode builds the refused
   geometry; a same-metal tap, an untapped primary and the secondary's own tap alone build at a 3 um opening; beside the
   secondary's tap, the same refusal.
6. `test_the_balun_names_its_primary_taps_gap` -- the pad (tapped to M4) and the lead (tapped to M5) between the escape bars,
   refused with their numbers; the gap equal to the rule builds; no same-metal advice in the balun's message.
7. `test_the_balun_keeps_its_other_refusals` -- a pad touching the bars keeps main's message; untapped primaries, with or without
   the secondary's tap, build at OPENING_S 2.6.
8. `test_the_pcell_refuses_exactly_what_the_drc_gate_would_fail` (16 cases, 12 xfm_ms, 4 balun) -- built through the generator
   with the check and with it switched off (the balun's inter-net gate too), the unchecked GDS audited: refused exactly when the
   audit finds `min_space` / `wide_parallel_spacing` on the metals the stack reaches; what builds passes the product gate and is
   byte for byte the unchecked drawing.
9. `test_the_private_profiles_ms_is_refused_with_its_rule` (the private profile; skipped without it) -- the product's ms (AP over
   M10, crossunder M9) with a 10 um primary tapped to M9, half a rule from the leads: refused, the rule read from the profile at
   run time and not written down; a full rule away builds.

Runs (`PYTHONPATH=<worktree>/src`, the main tree's `.venv`, `OMP_NUM_THREADS=2`): `tests/ic_opt/pcell` with the nine files that
build transformers (`test_em_pcell`, `test_em_circuit`, `test_em_measure`, `test_library_footprint`, `test_library_xfm`,
`test_library_composed`, `test_spec_library`, `test_lib_tap`, `test_lib_refine`): demo_6m 653 passed, 637 skipped on main
(3542196 and 58e6924 alike), 678 passed, 638 skipped on the branch (the new file's 25, its private-profile test skipped); with the
private profile 1288 passed, 2 skipped and 1314 passed, 2 skipped. `ruff check src tests`: all checks passed.

### Byte for byte with `main`

As N-93 did (scratch, not committed):

- main's tests run once with main's sources and once with the branch's -- the suites above, each temporary directory kept with
  `--basetemp` -- and every GDS, `emx_ports.txt`, `geometry_manifest.json` and coordinates file at the same path compared. At
  3542196: demo_6m 7006 files (2345 GDS, 2332 port files, 2329 manifests) identical, none differing, none on one side only; the
  private profile 7487 files (2642 GDS, 2409 port files, 2402 manifests, 30 coordinates files) identical, 4 GDS that tests write
  with KLayout's default writer differing only in their BGNLIB / BGNSTR time. At 58e6924, after the fast-forward, the same:
  7006 / 7487 files identical but for 2 / 3 GDS of that kind. Among them 86 / 103 xfm_ms generator builds, 4 / 7 with a primary
  stack that reaches the secondary's metal (measured, passed, unchanged), and 19 / 23 baluns. The test outcomes are the same on
  both sides except main's `test_reference.py` (`devices.md` regenerated).
- The 13 golden GDS are among them; `test_golden.py` passes (the golden ms and balun are untapped; `xfm_bs_ct` is N-93's).
- Library replay: every build recorded in the libraries on this machine (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library`:
  19532 `geometry_manifest.json`, 2293 ind_sym, 9155 xfm_bs, 8084 xfm_ms -- none tapped -- and no balun) regenerated from its
  recorded config with the same GDS name, as `docs/refactor/reports/pcell_plan/pcell_byte_replay.py library` does, with main's
  sources (3542196) and with the branch's: the same outcome and the same sha256 of GDS, manifest and port file for all 19532,
  none refused, every GDS equal to the recorded one on both sides (14592 manifests differ from the recorded ones on both sides
  alike: rows that predate fields the manifest has gained since).
- xfm_bs: N-93's parity configurations (5145 demo_6m, 8000 private profile) through main's sources and the branch's: 2835 / 4178
  built with the same sha256 of GDS, manifest and port file, 2310 / 3822 refused with the same message -- N-93's 278 / 510
  refusals among them.

### Parity with the DRC gate (scratch)

Each configuration built through the generator with the check and with it switched off, the unchecked GDS audited, and built
again with main's sources (the scripts and their results in the session scratchpad, `.../scratchpad/n121/`, not committed):

- xfm_ms: 9000 demo_6m and 18000 private-profile configurations (9000 over the whole stack, 9000 on its thick metals, where the
  thin metals' width limit does not stop a wide stack): every metal pairing with a primary tap (same-metal and via-stack, down to
  the secondary's tap metals) and with only the secondary's tap, NT_M 2-4, ODs 50-160 and unequal, widths 2-15, the opening
  around each crossed metal's min space and wide-line rule, centre spacing up to the overlap bound, inward and outward port-spacing
  jogs, the straight extension, same-metal tap widths. The check refuses exactly those whose audit has a `min_space` or
  `wide_parallel_spacing` finding on the metals the stack reaches (156 / 266), all of them on the secondary's own metal, and no
  other; 26 / 105 more are refused by the check where the unchecked build is refused later by the ground fixture (the tap stubs
  overlapping); 6000 / 14409 are refused by an earlier rule either way; 2818 / 3220 build, byte for byte the unchecked build and
  main's. Against main: every configuration the same except those 182 / 371.
- xfm_balun: 4500 demo_6m and 4500 private-profile tapped configurations (both taps, either, on every metal below the plane; NT_P
  1-3; the secondary's opening around the escape metal's rules; centre spacing; port spacing), the unchecked variant without the
  numbered check and without the inter-net gate. The numbered refusal comes exactly where that build's audit fails on the
  metals the primary's stack reaches (646 / 901), on the escape metal every time, and nowhere else; 0 / 7 where the unchecked
  build is refused later by the fixture; 92 / 82 keep main's message (their findings lie on the plane); against main, every
  configuration the same except those 646 / 908, which main refused with the general message.

### Deviations

1. **The balun's check runs inside its inter-net refusal.** The specification says "if they can, the same check". They can, but
   the balun already refused those builds before the gate; a second gate in front of it could only refuse builds the first
   passes where the two read a width differently (the nominal widths here, the drawn edges there). Inside the refusal it changes
   the message and nothing else.
2. **Every metal the stack reaches, for xfm_ms.** N-93 measures one metal; the secondary draws on three kinds, so the stack is
   measured on each where the secondary draws. xfm_bs keeps its one metal (below it the other winding draws only its own tap, on
   the far side of the device), so nothing of xfm_bs moves.
3. **The secondary's taps are not measured on their own** (xfm_ms: they never reach the primary's metal; the balun's: above).
4. **The message names, for xfm_ms, "the single-turn primary" and "the multi-turn secondary"** and the pcell's parameter names
   (W_S, W_M, OPENING_M), as N-93's does for xfm_bs; the balun's says "escape bars" and drops the same-metal advice (it has no
   same-metal tap).
5. **Refused earlier than before:** 131 xfm_ms and 7 balun sweep configurations that main refused at the ground fixture (tap
   stubs overlapping) are refused by the check first -- refused either way, as in N-93.
6. **The README** (a paragraph for xfm_ms, a bullet for the balun) beyond the specification's list.
