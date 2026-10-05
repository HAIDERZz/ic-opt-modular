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
