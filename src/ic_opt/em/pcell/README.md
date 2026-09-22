# Clean-port PCell implementation and history

Current product entry: `plugin_module: builtin:clean_port`, six registered
generators. The parameter reference is generated from the config models:
[docs/em/devices.md](../../../../docs/em/devices.md) (`python -m ic_opt.em.pcell.reference`);
the family docstrings in `generator_plugin.py` state each family's limits. The M7
milestone descriptions below retain construction provenance; they are not a claim
that every process/parameter combination has been qualified.

Original Python construction code, licensed with the repository (MIT). Its
function names and coordinate conventions follow the reference SKILL PCells the
chain was first studied against (see the provenance table in
`pcell_inductor_port_clean.py`); it reuses **no** code
from the failed M7 generators or earlier port prototypes.

## Construction modes

- **Reference mode** (`process=None`, the default): reproduces the
  PCell/`ind_ref.gds` shapes with the reconstructed reference via rule
  (cut/spacing/enclosure constants defined in this module) on datatype-0
  layers. This mode is for
  PCell/ind_ref **shape study only** — it is explicitly **not N28 DRC
  proof** (the N28 rule profile mandates its own VIA8/VIA9 cut, spacing and
  datatype geometry, read from the profile).
- **N28 process-backed mode**
  (`process=process_rule_context("n28_1p10m")`): every conductor and via
  layer/datatype, cut size, spacing, enclosure, minimum count and maximum
  spacing comes from
  `process_data/profiles/n28_1p10m/rule.yaml` (repo-only, resolved via
  `EM_IC_OPT_PROFILE_DIRS`)
  through the existing `GeometryRuleAdapter.plan_passive_via_array`. The
  M8<->M9 crossover endpoints come out on the profile's own VIA8 layer with
  the profile's cut geometry. The reference via constants are never
  consulted in this mode.
- **N28 center tap**: an `ind_sym(CT_ME=...)` M9->M8->M7 stack requires
  VIA7. Generator enforcement is **geometric-only** (user directive
  2026-07-19, `.scratch/n28-rules-slim/`): the deck's cited IND.R.1
  restriction is retained in `rule.yaml` as documentation but no longer
  gates construction, so the stack builds with VIA7's own profile
  geometry read from the rule profile; demos
  `ind_sym_ct_3t_m7_n28` and `ind_sym_ct_3t_m10m9_ct_m8_n28`.

## Generic metals

`TOP_ME` selects the coil plane. For multiple turns the construction uses
that plane plus a derived lower crossunder; a CT-less single turn uses the
direct ring and has no crossunder/vias. `ind_sym`'s optional
`CT_ME` (M13: the standalone `ind_sym_ct` generator was unified away)
selects the CT metal (at least two levels below `TOP_ME` per the N1
adjacency guard; the tap stack spans every level in between). Examples:

- `TOP_ME="10", CT_ME="8"` — M10/M9 coil, M8 CT: **N28
  process-constructible today** (VIA9/VIA8 are the modeled passive
  arrays); demo `ind_sym_ct_3t_m10m9_ct_m8_n28`.
- `TOP_ME="9", CT_ME="5"` — M9/M8 coil, M5 CT: builds the via5..via8 tap
  stack in both modes; N28 process mode draws each via class at its own
  profile geometry (geometric-only enforcement, 2026-07-19).

Process-backed dimensions come from the selected rule adapter, but loading a
profile does not prove that every generator's layer traversal supports its
stack. As of 2026-09-12, ind_sym and the related xfm_ms bridge/tap paths
use actual profile neighbours: AP crosses under on M10 in N28 and M9 in N65,
while the compatibility AP code stays 11. N65 AP CT=M9 is rejected at build;
CT=M8 or lower must still meet tap/via geometry rules. Limited ind 1..5-turn
and MS 2..5-turn geometry audits cover common N28/N65 stacks, including taps.
This does not establish the remaining TW/IL/balun cross-process coverage or RF
performance. BS AP tap-via traversal was fixed separately.

## EMX ports

EMX does **not** infer ports from metal shapes; it reads labels/pins from
the GDS (`gdsgen_ref/emx_pin_port_gds_rules.md`). Each terminal therefore
carries a text label on the metal's **pin** layer (the proc
`drawing + pin` split: M8 = `l38t20` + `l138t0`, etc.), so every
drawing-layer polygon stays byte-identical and the BASE↔HEAD drawing XOR
is empty.

- `ind_sym`: **P1/N1** on the `M(TOP_ME)` pin layer at the right lead-tip
  midpoints `(OD/2+LEAD, ±(OPENING+W/2))` — since M13 the default port
  names equal the physical roles. The current product plugin fixes the
  CT-less order to `[P1,N1]`; it no longer accepts arbitrary two-name lists.
- `ind_sym(CT_ME=...)`: adds **CT** on the `M(CT_ME)` pin layer at the far
  lead tip — `(OD/2+LEAD, 0)` for even NT (lead side), `(-OD/2-LEAD, 0)`
  for odd NT (crossover side) — on top of P1/N1 (`port_order` default
  `["P1","N1","CT"]`).
- Pin layers: reference mode `(130+m, 0)`; N28 process mode the rule
  profile `pin` (M8 `138/0`, M9 `139/0`, M10 `140/0`). A metal whose
  profile leaves `pin` unset fails closed with a `PortError`.
- `<basename>.emx_ports` holds one `-p name=signal[:reference]` line per
  port (sorted by name). Without a ground fixture every line is
  `-p name=signal`; with a fixture it becomes `-p name=signal:G0n`. Both forms are
  consumable as-is by the product
  `em_candidate_preparation._parse_emx_port_line`. Every port `signal` and
  `reference` is present among the cell's GDS labels (the product M2
  `_validate_configured_ports_against_geometry` invariant).
- `<basename>.coordinates.json` records the labels and an `emx_ports`
  block (now carrying `reference`) alongside the polygons.

## Ground reference fixture

`ind_sym` (with or without `CT_ME`) gains an optional `ground_fixture:
GroundFixtureConfig | None = None`. When set, `add_ground_fixture` draws
an **M1 ground reference layer** around the device — a four-rectangle M1
ring (drawing `(31,0)`; process mode the rule-profile M1 drawing) plus one
**chamfered stub** per port rooted on the ring side nearest the port
(left ring for ports at `x <= center`, right ring otherwise) — and places a
**`G{index:02d}` local-ref pin label** on the M1 pin layer (`(131,0)`;
process mode the rule-profile M1 pin) directly under each signal port
`(x, y)`. Each port's `reference` is then wired to its G-pin name, so the
EMX lines become `name=signal:G0n` local-ref ports instead of edge ports
referenced to the infinite ground plane. This mirrors the product
`single_turn_transformer._add_ground_fixture` model.

```python
GroundFixtureConfig(inner_margin_um, ring_width_um,
                    stub_width_um, stub_length_um, stub_chamfer_um,
                    stub_width_by_port_um=None)
```

- `stub_width_by_port_um` (optional, M7W) overrides the stub width for
  specific ports, keyed by the generated cell's semantic name (for example
  `P2`; not a later product EMX override such as `p03`). Ports not
  listed fall back to the global `stub_width_um`, so one fixture can give
  `xfm_ms` a 6 um stub on the W_S=6 side and a 3 um stub on the W_M=3
  side. Unknown port names fail closed with `PortError`.
- `add_ground_fixture(cell, fixture, process=None)` operates generically on
  any `Cell` with `emx_ports`; it fails closed with `PortError` when the
  cell has no ports or M1 has no pin layer.
- With `CT_ME` the fixture is applied after the CT port is placed, so the
  tap port receives its own G-stub like every signal port.
- Every side obeys two independent bounds: the port tip plus
  `stub_length_um`, and the non-port winding-body envelope plus
  `inner_margin_um`; the farther-out bound wins. A normal outward lead that
  already clears the body therefore keeps the exact historical stub length,
  while an unequal-OD `xfm_bs`/`xfm_ms` body can push the ring outward instead
  of lying underneath M1. A side with no ports uses only the body-margin
  bound (the inductor's left side is empty for even NT).
- Without a fixture the drawing-layer geometry is byte-identical to M7L:
  the port rename touches only pin-layer label text, never a drawing
  polygon. The fixture and G labels are pure M1 additions.
- Demo `ind_sym_ct_3t_m10m9_ct_m8_n28_gnd` exercises the fixture in N28
  process mode on the M10/M9 coil + M8 CT device (M1 `(31,0)` ring +
  `(131,0)` G labels).

This is a reference/experiment port and is still **not N28 DRC proof**.

## Balun secondary primitive (base_balun_sec, M7P)

`base_balun_sec` is a single `base_oct(OD, W=WI, OPENING=LOP)` ring on MET=9,
ported in M7P as the transformer/balun secondary primitive and still used by
`xfm_balun`. Demos: `base_balun_sec` (reference) and `base_balun_sec_n28`
(process).

The rest of the high-k transformer family (M7N primary body, M7O diagonal
crossover, M7P full primary, M7Q top assembly `xfm_highk`) was removed in M13
(2026-07-17 decision: it was never wired into the product path; the ported
sources remain in git history and `gdsgen_ref/`).

## Broadside single-turn transformer (M7R / M7R2)

`xfm_bs` is a **clean-room composition** of the already-ported primitives
(`base_oct` + `base_lead_pair` + `vias` + `base_lead`) — **not** a `.il`
transcription: no cell in `gdsgen_ref/pcell/` models a broadside two-layer
single-turn transformer (the reference transformers are all same-plane
octagon-nested high-k/balun). It **replaces the gdsfactory product
placeholder `src/.../single_turn_transformer.py`** with the clean-port
inductor modelling methodology (klayout.db + ported primitives + N28
`process_rule_context` + fail-closed + EMX/ground).

It builds two single-turn open-octagon windings on **two different,
parameter-selected metals** (`PRI_ME`/`SEC_ME`) — the broadside vertical
coupling — with **opposite, outward** openings (primary at
`(-CENTER_SPACING/2, 0)` opens left, exits -x; secondary at
`(+CENTER_SPACING/2, 0)` opens right, exits +x), matching the product
`single_turn_transformer.py` convention. Four EMX ports: P1/N1 (left, on the
`PRI_ME` pin), P2/N2 (right, on the `SEC_ME` pin).

- **Intuitive `CENTER_SPACING` (outward revision):** larger spacing moves both
  coil centers *and* lead terminals farther apart — the P1↔P2 terminal distance
  is `OD_P/2+OD_S/2+LEAD_P+LEAD_S + CENTER_SPACING` (= 130+CENTER_SPACING for
  the demo parameters). The old inward convention produced
  `constant − CENTER_SPACING` (counterintuitive).

- **Independent placement (M7R2):** `OD_P`/`OD_S` are separate outer
  diameters and `CENTER_SPACING` sets the center-to-center x distance
  (primary at `(-CENTER_SPACING/2, 0)`, secondary at `(+CENTER_SPACING/2, 0)`),
  matching the product `single_turn_transformer.py`'s
  `primary/secondary_inner_diameter_um` + `center_spacing_um` knobs.
  `CENTER_SPACING=0` with `OD_P==OD_S` reduces exactly to the M7R concentric
  build.
- Metals are fully parameterized (never hardcoded): `PRI_ME`/`SEC_ME` must
  differ (else `PortError`); an optional per-winding center tap
  (`CT_P_ME`/`CT_S_ME`) taps that winding's **closed column** down to a lower
  metal, the CT lead exiting outward. Since M13 ticket 05 each winding (plus
  its tap) is built in its own sub-cell and the **layer-complete net gate**
  (`_xfm_net_short`, shared with `xfm_balun`) intersects the two complete
  nets on every drawn layer — tap leads and winding leads included. This
  replaced the earlier box-only `_bs_ct_short` clearance, whose ring-only
  probe missed two proven short classes (a CT lead crossing the other
  winding on a shared metal, and two same-metal CT leads crossing each
  other). The gate is strictly stronger — it fails closed only
  on a real short.
- **N28 default `PRI=M10 / SEC=M9 / CT=M8` is fully constructible**: the coil
  has no vias (same-metal leads); the CT stacks use only VIA8/VIA9 (IND.R.4
  legal passive arrays), unlike the high-k under-pass (M5, IND.R.1).
- `ground_fixture` (M7M) wires every port to its `G0n` local-ref pin
  (`-p P1=P1:G01 …`); without it, plain `-p P1=P1 …`.
- The opening-side straight octagon edge must provide at least one complete
  trace width of landing before the diagonal begins. This rejects compact
  OD / wide-trace combinations that remain electrically connected but show
  a visibly truncated pin-to-coil joint; the bound is derived from
  `OD`/`W`/`OPENING`, not from a process-specific constant.
- Demos: `xfm_bs`/`xfm_bs_n28` (independent OD_P=100/OD_S=76 +
  CENTER_SPACING=8), `xfm_bs_ct`/`xfm_bs_ct_n28` (concentric OD_P=OD_S=90,
  dual CT on M8 — the M7R backward-compat build).

Transformer-type roadmap (agreed 2026-07-03): M7R → M7R2 (independent OD +
center spacing) → **M7S (this)** — one winding multi-turn + the other
single-turn, different metals (impedance transformation) → M7T — classic
balun, same metal (ports the `base_xfm_half` family).

## AP layer support (M7S Part A)

The top metal **AP** is now metal index **11** (reference drawing `(41,0)`,
pin `(141,0)`, via M10↔AP `(60,0)`; N28 drawing `(74,0)`, pin `(126,0)`,
RV `(85,0)`). Two helpers — `_metal_index("AP")==11` and `_metal_name(11)=="AP"` —
let every metal-aware function (`metal_layer`, `process_metal_layer`, the
winding helpers, `xfm_bs`, etc.) accept `"AP"` alongside numeric metals.
The refactor is **output-preserving** for numeric metals (XOR-verified on
every existing cell). `xfm_bs(PRI_ME="10", SEC_ME="AP")` now builds the
product's M10/AP single-turn transformer (demo `xfm_bs_m10ap`).

## Multi + single-turn transformer (M7S Part B)

`xfm_ms` composes a **single-turn** winding on a higher metal (`SINGLE_ME`,
via `_bs_winding`, opens left/outward, ports P1/N1) + a **multi-turn**
winding on a lower metal (`MULTI_ME`, via `ind_sym` with crossover on the
actual adjacent lower conductor, instanced R0 so it opens right/outward,
ports P2/N2). Because
`SINGLE_ME` is strictly above `MULTI_ME`, the multi crossover never reaches
the single plane (no cross-winding short). Inherits `OD_S`/`OD_M` and
`CENTER_SPACING` from M7R2.

Since M13 ticket 06 both windings carry optional center taps: `CT_P_ME`
taps the **single** winding's closed column (`_bs_center_tap`, strictly
below `SINGLE_ME`, port `CTP`); `CT_S_ME` taps the **multi** winding
through `ind_sym`'s `CT_ME` path (port `CTS`), so it obeys the inductor's
N1 adjacency rule (at least two actual profile levels below `MULTI_ME` —
the crossunder occupies the immediately lower conductor). Each winding (plus its tap) builds in its own
sub-cell and the layer-complete `_xfm_net_short` gate fails closed on any
overlap; taps append after the fixed base as `[P1,N1,P2,N2,CTP,CTS]`.

- N28 SINGLE=AP / MULTI=M10 / crossover=M9 is the historical constructive
  example; specific dimensions still need current build/geometry checks.
- Fail-closed: `SINGLE_ME <= MULTI_ME`, `NT_M < 2`, `MULTI_ME < 3`,
  unavailable geometric via rules → `PortError`. The multi-turn body metal must also
  contain exactly `NT_M` disconnected winding segments before the lower-metal
  bridges join them; fewer segments means adjacent turns have self-shorted
  and bypassed the crossover, while more means an open body.
- M3 is now accepted as the multi-turn body (for example M4/M3 with an M2
  crossunder); M1/M2 bodies remain forbidden. Compact two-turn leg2 stays on
  the body plane, and its metadata records that actual plane.
- Demos: `xfm_ms` (AP+M10 concentric), `xfm_ms_spaced` (+CENTER_SPACING=12),
  `xfm_ms_n28` (N28), `xfm_bs_m10ap` (xfm_bs M10/AP single+single).

## Classic same-layer (coplanar) balun (M7T / M7T2)

`xfm_balun` composes two octagon windings on **one metal-generic plane
`BALUN_ME`** — the classic balun where primary and secondary couple
coplanarly. This is the only balun-family milestone with real `.il` porting:
`base_xfm_half.il` → metal-generic stacked half-ring primitive.

- **`base_xfm_half`**: one `base_oct_half` per metal level in
  `[BTM_ME, TOP_ME]`, optionally stitched by one axis-aligned `vias()` block
  (`via_to_next`). The `via_diag` branch uses the sourceless `vias_diagonal`
  PCell → `PortError`. `A_TK`/`B_TK` move only via placement; `WI`/`BB`/`PA`/
  `PB` are dead (signature fidelity).
- **`CENTER_SPACING`** offsets the two centres (`xP=-CS/2`, `xS=+CS/2`) but
  the secondary must stay nested inside the primary: coplanar rings that
  do not overlap are two inductors, not a balun, and are refused (user
  directive 2026-09-22 retired the former side-by-side mode together with
  the pcell-only `PRI_BTM_ME`/`SEC_BTM_ME` stacked-ring options, which
  cannot coexist with the nested escape). Primary opens left, secondary
  opens right (outward convention).
- **Concentric crossunder escape (M7T2)**: in nested (concentric) mode the
  inner (secondary) winding's leads escape via `base_ind_under` crossunders on
  **`ESCAPE_ME`** — a parameter, never a pinned layer. Default is derived one
  level below `BALUN_ME` (`_metal_index(BALUN_ME) − 1`); overridable to any
  strictly lower metal (non-adjacent spans verified clean; via legality
  delegated to `vias()`). Near pad lands on the inner arm tip (same net); far
  pad exits outside the outer ring + a `BALUN_ME` `base_lead` to the EMX port.
  Side-by-side (non-nested) uses direct leads, unchanged.
- **Layer-complete full-net gate (M7T2)**: `_xfm_net_short` (generalized
  from `_balun_net_short` in M13 ticket 05, now shared with `xfm_bs`)
  intersects the
  two windings' COMPLETE nets (ring + leads + crossunder pads/bridge) on
  **every layer either net draws** (union of layer keys — no pinned list). Any
  overlap → `PortError` with layer and area. This replaces M7T's ring-only
  `_coplanar_ring_short` which missed the concentric lead short.
- **NT per winding**: NT=1 uses `base_xfm_half` R0+MX; NT≥2 uses `ind_sym`
  (crossover on `BALUN_ME-1`). Nested + NT_S≥2 → `PortError` (ind_sym bakes
  its own leads). Multi-turn legal only at `BALUN_ME=9` (VIA8).
- **M7T recall**: M7T's initial concentric mode shorted (inner leads crossed
  the outer ring on the shared metal — 50 µm² on the default instance); the
  ring-only gate and the M7T acceptance both missed it. M7T2 fixes it with the
  crossunder + layer-complete gate. Side-by-side output is byte-identical.
- Demos: `xfm_balun`/`xfm_balun_n28` (concentric OD200/186, now with
  crossunder), `xfm_balun_2t1t_n28` (2t+1t, `OPENING_S=16`). The former
  `xfm_balun_sidebyside` demo is gone: coplanar rings that do not overlap
  are two inductors, not a balun (user directive 2026-09-22), and
  `xfm_balun` now refuses them.

## OPENING upper-bound guard (M7U)

- `OPENING` has a geometric upper bound `max_opening(OD, W)` — the point where
  `base_oct_quad` stops honouring the opening (its `OP > (BA - C)` branch) and
  the octagon opening leg detaches from `base_lead_pair`, silently floating the
  P/N ports. The bound is bit-exact with that branch
  (`BA - C`, `BA = floortogrid(B/2-0.005)`, `B = OD - 2*roundtogrid(OD/(2+√2))`,
  `C = ceiltogrid(W*tan(π/8)+0.005)`) and depends only on `OD` and `W`
  (metal-independent).
- Three winding sites fail closed with a `PortError` above the bound:
  `ind_sym` (with or without `CT_ME`) and the single-turn transformer windings via
  `_bs_winding`/`_ci_winding` (`xfm_bs`, `xfm_ms`, `xfm_balun`). The guard
  is purely additive — no demo OPENING exceeds its bound, so every existing
  output was byte-identical in that historical comparison. `OPENING = 0`
  is available to internal closed-ring primitives, but current product
  generator opening fields require values strictly greater than zero. TW's
  total-gap upper-bound inconsistency is tracked in the current inventory.

## Status

- These modules are the in-package product implementation (original code under
  the repository's MIT license; the provenance table in
  `pcell_inductor_port_clean.py` records which reference PCell each construction
  was first studied against). This README claims no foundry manufacturing signoff.

## Files

- `pcell_inductor_port_clean.py` — the port (KLayout `klayout.db` for GDS
  I/O; no gdstk). Function-by-function mapping to the PCell sources and the
  full deviation list live in the module docstring and in the generated
  report.
- `outputs/` (generated, gitignored) — per-primitive `*.gds`, `*.png`
  (rendered from the written GDS via KLayout parsing), `*.coordinates.json`
  (polygon coordinates exactly as present in the GDS, the pin-layer labels,
  the `emx_ports` block and the recursive instantiation log),
  `*.emx_ports` (for cells that carry EMX ports) and
  `pcell_inductor_python_port_report.{md,json}`.

## Usage

```bash
./.venv/bin/python - <<'PYCODE'
from em_ic_opt_workflow.devices.clean_port.generator_plugin import PLUGIN_GENERATORS
for name, generator in PLUGIN_GENERATORS.items():
    print(name, list(generator.config_model.model_fields))
PYCODE
```

## Crossover layer relationship

`ind_sym.il` hardcodes `TOP_ME="9"` / `BTM_ME="8"` and
`base_ind_hud_cross.il` hardcodes the octagon ring on MET 9, so with PCell
defaults the local crossover is an **M9 same-layer mirrored diagonal over an
M8 underpass diagonal**, with via8 arrays only at the underpass endpoints
and never at the central crossing. The independent reference
`/home/zzchen/Prj/Prj_For_N65/ind_ref.gds` shows the same relationship
(M9=39 diagonals, M8=38, via8=58 arrays; zero via area on the projected
diagonal overlap) and contains no M10.

## User-directed corrections vs the .il sources (referenced on ind_ref.gds)

- **Flush crossover joints**: every ring opening that faces a crossover uses
  the exact cross endpoint edge `cross_endpoint_offset(W, S) = OOCH+OOCHD`,
  so the endpoint via blocks are fully covered by the ring arms (in
  ind_ref.gds every via pad sits under ring metal). The .il values
  (`LOP = OOCH + roundtogrid(2*sqrt(2)-S) + 0.01`, inner `OPENING = 2*W`)
  leave the pads protruding and are documented as replaced.
- **Center tap on M7**: `ind_sym(CT_ME=...)` places a `W x W` vias
  stack at the closed column of the innermost turn (the winding symmetry
  point) and routes the CT lead on `CT_ME` (e.g. **M7**) beneath the
  turns out on that column's side — the crossover side for odd NT; for
  even NT (the PCell default `NT=2`) the closed column sits on the P1/N1
  lead side and the CT lead exits there. This mirrors the ind_ref.gds
  center tap (M9 -> via8 -> M8 -> via7 -> M7 stack at the tap only). The
  .il's TOP_ME-level lead at `(OD/2-P, -W/2)` is documented as replaced.

## Notes for consumers

- Import the module via `importlib` **with a `sys.modules` registration**
  (see the top of `tests/test_pcell_inductor_python_port_clean.py`); a bare
  `spec.loader.exec_module` fails inside the dataclass decorators.
- GDS layers use datatype 0. `tsmcN28_1p10m.proc` maps M7/M8 to datatype 20
  and M9/M10 to datatype 80 (and `ind_ref.gds` uses its own datatypes), so
  any automated layer/datatype-keyed diff against those files must remap
  datatypes first or it will silently match nothing.
