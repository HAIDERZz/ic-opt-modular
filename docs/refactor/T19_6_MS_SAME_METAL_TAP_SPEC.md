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
