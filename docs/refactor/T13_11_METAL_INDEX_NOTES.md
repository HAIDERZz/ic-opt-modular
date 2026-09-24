# T13.11 metal positions from the profile (design record)

Status: done (2026-09-24), after the N28 transformer production run.

## What changed

- **The metal stack is the via chain.** `ProcessRuleProfile.metal_stack` = the catalog conductors that carry a
  `metal_width_space` rule, ordered by walking the vias between them upward from the ground-fixture metal
  (`layer_catalog.ground_fixture_conductor`, default the metal named M1). Key order in the file does not matter —
  an earlier draft read the catalog's listing order and broke on any `yaml.safe_dump` with sorted keys. A via that
  skips a level, a metal no via reaches, or a via naming an unknown conductor fails the profile.
- **One mapping, `ic_opt.em.pcell.stack`.** Name ↔ position lives in one module used by both the generator core
  (`_pcell_core._metal_index / _metal_name`) and the DRC audit (whose own copy is gone). Inside `use_stack(profile)`
  positions are the profile's stack; outside it (reference mode) the fixed convention "M<n>"/"<n>" → n, "AP" → 11
  stays. The active stack is a context variable, so threaded builds never see each other's profile.
- **Where the stack is opened:** the six family entry points (`@builds_on_profile_stack`, keyed on their `process`
  argument), the generator's output stage (`_write_geometry_outputs`: shield, audits), and the audit's expected-layer
  recipes (`require_layers_from_config`). The fixture finds each port's lead layer by the port's conductor name, not
  by a position computed inside the build.
- **Loops over "all 11 metals"** (`_conductor_for_drawing`, the inter-net spacing guard) follow the active stack.
- **Error messages** name metals through the stack (a refused AP device now says "on AP", not "on M11").
- **Config boundary:** a metal a spec names must be a metal of its profile by name ("AP", "RDL", "M6", or "6" for
  M6). Inside the pcell digits are positions; without this check a user's "10" on a stack without M10 would land on
  the tenth metal.
- **Profile validation:** the smoke's top is the highest metal below any `class: aluminum_pad` layer (N28 M10, N65
  M9, demo M6 — as before); canonical configs spell metals by name; the consistency stage reports a GDS layer that
  two catalog entries claim (vias may share a cut layer only when they land on a common conductor, as the diffusion
  and poly contacts to M1 do).

## Verification

- 13 golden GDS byte-identical (test_golden.py).
- Recorded N28 library replayed through the current generator: 6990 / 6990 devices (inductors and transformers,
  AP and M10 bodies) with identical GDS and manifest bytes (`docs/refactor/reports/pcell_plan/pcell_byte_replay.py
  library`).
- Frozen baselines recorded before the change (`pcell_byte_replay.py record`, 288 demo_6m / 240 N28 / 240 N65
  configs over every family, the top three metals by name and digit, AP bodies included) re-checked after it: every
  generated device byte-identical; the only difference is the refusal message of one AP-body balun per private
  profile ("on M11" → "on AP").
- tests/ic_opt/pcell/test_metal_stack.py: chain independent of key order; fixture metal default / declared / invalid;
  broken chains; reference and profile positions; no leakage between threads; the config name check; a fictitious
  12-metal profile topped by RDL passes validate_profile's generation smoke and builds all six families on RDL
  through the product DRC audit.
- Private-profile pcell suite, EM and library tests, G1/G2 gates: pass.
