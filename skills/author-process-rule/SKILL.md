---
name: author-process-rule
description: Author and verify a process-rule-profile-v1 rule.yaml for a new process node from PDK source files (layer map, ict/itf stack file or EMX proc, DRC deck or design-rule manual), ending with a passing `ic-opt call em.validate_profile` run
---

# /author-process-rule

Turn a new process node's PDK source files into a working
`<profile_id>/rule.yaml` process rule profile, then prove it with the
deterministic validator. The extraction step cannot be a script -- every
foundry ships different file formats (Calibre SVRF vs ICV vs PVS decks,
`.ict`/`.itf`/`.nxtgrd` stack files, per-vendor layer maps, encrypted
rule blocks) -- but the *target* schema is fixed and machine-checked, so
your loop is: **read the sources -> write the YAML -> run
`em.validate_profile` -> fix -> repeat until `result: PASS`.**

## What you are producing

One file, `<profile_id>/rule.yaml`, in a directory you own -- outside this
repository:

```text
/your/profiles/
  myproc_1p8m/         <- profile_id = folder name
    rule.yaml
    README.md          <- provenance (see Discipline)
```

- A spec's device selects it with `profile: <profile_id>`; the pcell
  finds it through the `IC_OPT_PROFILE_DIRS` environment variable
  (`os.pathsep`-separated directory list, searched in order, before the
  packaged profiles). The **folder** name is the id, so keep
  `process_id:` inside the file identical to the folder name.
- Schema `process-rule-profile-v1`, strict validation: unknown keys are
  rejected (`extra="forbid"`), missing pieces make the loader raise --
  the workflow is fail-closed rather than guessing.
- Every length is micrometres. `layout_rules.manufacturing_grid_um`
  states the process's manufacturing grid (default 0.005 um, a whole
  number of nanometres); the generators snap the coordinates they
  quantize to it, so prefer rule values on it too.

Seven top-level sections:

| Section | Carries |
| --- | --- |
| `schema_version` | fixed string `process-rule-profile-v1` |
| `process_id` | the profile id (== folder name) |
| `units` | `{length: um}` |
| `coverage` | declared key-set mirrors of the rule sections (see cross-checks) |
| `layer_catalog` | conductors / vias / markers with GDS layer,datatype + EMX names |
| `emx_stack` | per-conductor thickness, geometry scaling, EMX via models |
| `layout_rules` | metal width/space, via primitives, passive-region rules |

## Inputs to request from the user

| Source file | Typical spelling | Feeds | Required? |
| --- | --- | --- | --- |
| Layer map | `*.layermap`, `*.map` | `layer_catalog` drawing/pin numbers | yes |
| DRC deck or design-rule manual (DRM) | `*.encrypt` SVRF deck, ICV/PVS runset, DRM PDF | `layout_rules` | at least one |
| Stack file | foundry `*.ict` / `*.itf` (QRC/StarRC tech), or the site EMX `*.proc` | `emx_stack` | at least one |
| Site EMX proc | `*.proc` | `emx_name` alignment, the GDS layer map EMX reads (`define` lines), conductor thicknesses (+ can substitute for the stack file) | strongly recommended |

Provenance note: a site EMX proc is *derived* from the foundry
ict/itf + layer map -- the PDK originals are the ground truth. When both
exist, take numbers from either but cross-check once and record which
you used. The proc is still the **naming and layer-map authority**: EMX
consumes it at runtime, so every `emx_name` you write must appear in it,
and every drawing layer -- and every conductor's pin layer -- must appear
in the `define` of its `emx_name` (`define M1 = fill(l<layer>t<datatype>+...,
...)`): a layer the proc does not map is geometry EMX silently ignores, and
EMX looks for a conductor's port labels (the pcell writes them on the pin
layer) only on the layers its define names. The validator checks names,
thicknesses and layers with `proc=`.

The DRM is a first-class source, not a fallback: for width/space/
enclosure numbers its tables are usually cleaner than reverse-reading a
deck, and it is the only path when the deck rules you need sit inside
encrypted blocks.

## Triage before writing anything

1. **Confirm the metal scheme string** (the metal count plus the
   thin / intermediate / thick / RDL split the foundry encodes in its
   option name) with the user, then check that EVERY candidate file
   carries that same scheme in its filename or header. A real case: a
   reference directory held a stack file for a different metal scheme
   (fewer metals, different split) right next to the correct files --
   it would have silently produced a wrong stack.
2. **Count conductors independently** in the layer map and in the stack
   file; both counts must agree with the DRM's stack description.
   (A conductor-count mismatch is what caught that wrong stack file.)
3. **Identify the deck language and its encrypted blocks.** You only
   need five rule categories (below); if any sits encrypted, use the
   DRM tables for those numbers.

## Section-by-section mapping

| rule.yaml section | Read from | What to extract |
| --- | --- | --- |
| `layer_catalog.conductors` | layer map (+ proc for `emx_name`) | one entry per metal: `drawing: [layer, dt]` and `pin: [layer, dt]` (both pairs the proc's `define` for that `emx_name` lists; a conductor without `pin` cannot carry a port), `emx_name` exactly as the proc spells it, informational `class` (`thin_metal` / `intermediate_top_metal` / `thick_top_metal` / `aluminum_pad` / `poly` / `diffusion`) |
| `layer_catalog.vias` | layer map + stack order | `drawing`, `emx_name`, `connects: [lower, upper]` (exactly two, distinct) |
| `layer_catalog.markers` | DRM passive-device chapter | the passive-region marker layer (plus any other marker `layout_rules` cites) |
| `emx_stack.conductors` | ict/itf `thickness` fields, or proc | `thickness_um` per conductor (must equal the proc's `conductor` thickness; the validator compares them) |
| `emx_stack.geometry_scaling` | proc `geometry scaling` line | usually `1.0` |
| `emx_stack.via_models` | proc via statements | one entry per *modeled* via: `{via: <catalog via>, emx_effective_size_um}`; keys are model names -- pair-style keys like `M5_M6` are fine |
| `layout_rules.manufacturing_grid_um` | DRM grid rule (the deck's off-grid check) | the manufacturing grid in um; leave it out for 0.005 |
| `layout_rules.metal_width_space` | deck `INTERNAL`/`EXTERNAL` width & space rules, or DRM tables | `min_width_um` / `max_width_um` / `min_space_um` per metal the devices can touch (every `M<n>` and AP); any bound the process does not define stays `null` |
| `layout_rules.via_primitives` | deck via rules, or DRM | `cut_size_um: [x, y]`, `min_cut_space_um`, `min_enclosure_um` for BOTH connected metals |
| `layout_rules.audited_vias` (optional) | (your choice) | the vias whose cut enclosure the DRC audit checks; leave it out to check every via of the metal stack |
| `layout_rules.passive_region` | DRM passive/inductor chapter | marker name; per-via `via_array_rules` (`min_count`, `max_space_um`) for the vias devices actually use; conditional `wide_parallel_spacing` rules (`W>`, `L>`, `space>=`); cited `via_restrictions`/`metal_restrictions` |
| `coverage` | (mirrors) | key lists that must equal the sections above -- write them last |

Deck reading hints (SVRF): `INTERNAL layer < w` is a min-width rule,
`EXTERNAL layer < s` min-space, `ENCLOSURE via metal < e` enclosure. A
few thousand deck rules exist; **only the five categories in the table
matter** (metal width/space, via primitive geometry, passive via
arrays, wide-parallel spacing, passive markers/restrictions). Antenna,
density, well/latch-up and every other category are out of scope by
design (`coverage.layout_rules: passive_generator_core_rules` declares
exactly this).

Which vias need `via_array_rules`: the generators drop via arrays only
between the metals your devices use -- top metal, one below
(crossunder), two below (`xfm_il`'s second leg), plus an RDL via if you
run devices on AP. Model those; classify everything below as
`not_yet_modeled` (an honest coverage gap, NOT a prohibition --
generation fails closed if a device ever needs an unmodeled via). A real
profile typically models only its top three via classes.

Naming rules with teeth:

- `emx_name` must match the proc **token-for-token**, and the proc's
  `define` of that name must list the entry's `drawing` and `pin` pairs;
  `em.validate_profile ... proc=` fails on any mismatch.
- Metals are the conductors with a `metal_width_space` rule; their order
  (the metal stack) is the via chain, read from `connects`: every via
  between two metals joins neighbours, from the ground-fixture metal
  upward. Any number of metals and any names work (`RDL`, `UTM`, ...) --
  but a via that skips a level, or a metal no via reaches, fails the
  profile. Key order in the file does not matter.
- The ground-fixture metal (the reference ring and stubs) is the bottom of
  the stack: the metal named `M1`, or whatever
  `layer_catalog.ground_fixture_conductor` names. Device configs reject
  `M1` as a winding metal.
- Via names are free (`VIA9`, `RV`, `VRDL`, ...): the DRC audit checks the
  cut enclosure of every via of the metal stack by default, whatever it is
  called. `layout_rules.audited_vias: [<via>, ...]` replaces that set when
  you want a different one (an empty list turns the enclosure check off,
  visibly); every via it names needs a `via_primitives` entry with the
  enclosure on both metals it joins, or the loader refuses the profile.
- Mark an aluminium-pad / redistribution top metal `class:
  aluminum_pad`: the generation smoke builds its canonical devices on the
  highest metal below it (the proven sweep territory).
- Two vias may share a drawing layer only when they land on a common
  conductor (one contact layer from diffusion and poly to M1); any other
  GDS layer claimed twice fails the consistency stage.

In device configs a metal is named as in the profile (`"AP"`, `"RDL"`),
and `"M6"` / `"6"` both name the metal called M6; a spelling that names no
metal of the profile is refused.

## Discipline

- **Fail-closed, never guess.** A number you cannot find goes missing,
  the loader rejects the profile, and you ask the user / the DRM. Do
  not invent "reasonable defaults" -- a wrong rule silently produces
  wrong silicon geometry.
- **IP containment.** Your process's real numbers live ONLY in your own
  profile directory (the one `IC_OPT_PROFILE_DIRS` points at). Never
  copy them into this repository's `src/`, docs, tests, or anything you
  redistribute. Every number in this skill's worked example is
  invented.
- **Record provenance.** Keep a short README next to your `rule.yaml`
  naming which source file (and version) each section came from, and
  archive the ict/itf you cross-checked against.

## The cross-checks the loader enforces

Write `coverage` last and keep these equalities true (the loader
rejects the file otherwise, and `em.validate_profile` prints each
failure with its YAML path):

1. `coverage.metal_width_space` == keys of
   `layout_rules.metal_width_space`
2. `coverage.via_primitives` == keys of `layout_rules.via_primitives`
3. `coverage.passive_via_arrays` == keys of
   `layout_rules.passive_region.via_array_rules`
4. `coverage.emx_via_models` == keys of `emx_stack.via_models`
5. `passive_via_array_coverage.modeled` == keys of `via_array_rules`,
   and `modeled` + `not_yet_modeled` together classify EVERY via in
   `layer_catalog.vias`, with no overlap
6. every via `connects` exactly two different conductors
7. every restriction's `applies_to` names known vias; exception vias
   stay inside `applies_to`; exception markers exist in the catalog

`em.validate_profile` adds authoring-level referential checks the
runtime loader does not make: `emx_stack.conductors` keys,
`via_models[*].via`, the passive `marker`, `metal_width_space` keys,
enclosure keys and `wide_parallel_spacing` metals must all reference
catalog entries; it warns (without failing) when `process_id` differs
from the folder id, when `units.length` is not `um`, or when an
enclosure table misses one of a via's connected metals.

## Worked example: the demo_6m profile

A complete, loadable, generation-proven profile for a fictitious
6-metal process. It ships with ic-opt at
`src/ic_opt/em/pcell/profiles/demo_6m/rule.yaml` (byte-identical to the
copy below -- a test pins the sync) and every number in it is invented.
From an installed package, its directory is

```bash
python -c "from importlib.resources import files; print(files('ic_opt.em.pcell') / 'profiles' / 'demo_6m')"
```

-- copy it as a starting point, rename the folder and `process_id`.

```yaml
# demo_6m -- fictitious demonstration process (6 metals, 5 vias).
#
# ALL VALUES ARE INVENTED. This profile describes no real foundry process
# and is safe to publish; it exists as the ONLY complete example of
# process-rule-profile-v1 (real-process profiles are NDA-bound and never
# ship). It is also a working profile: every clean-port device family can
# generate under it and pass the rule-profile DRC audit
# (`ic-opt call em.validate_profile <this directory> generate=true`).
#
# Authoring guide: skills/author-process-rule/SKILL.md keeps a
# byte-identical copy of this file as its worked example -- update both
# together (a test pins the sync).
schema_version: process-rule-profile-v1
process_id: demo_6m

units:
  length: um

coverage:
  layer_inventory: full_known_inventory
  layout_rules: passive_generator_core_rules
  metal_width_space: [M1, M2, M3, M4, M5, M6]
  via_primitives: [VIA1, VIA2, VIA3, VIA4, VIA5]
  passive_via_arrays: [VIA1, VIA2, VIA3, VIA4, VIA5]
  emx_via_models: [VIA1, VIA2, VIA3, VIA4, VIA5]

layer_catalog:
  conductors:
    M1: {drawing: [61, 0], pin: [61, 2], emx_name: M1, class: thin_metal}
    M2: {drawing: [62, 0], pin: [62, 2], emx_name: M2, class: thin_metal}
    M3: {drawing: [63, 0], pin: [63, 2], emx_name: M3, class: thin_metal}
    M4: {drawing: [64, 0], pin: [64, 2], emx_name: M4, class: thin_metal}
    M5: {drawing: [65, 0], pin: [65, 2], emx_name: M5, class: intermediate_top_metal}
    M6: {drawing: [66, 0], pin: [66, 2], emx_name: M6, class: thick_top_metal}
  vias:
    VIA1: {drawing: [71, 0], emx_name: VIA1, connects: [M1, M2]}
    VIA2: {drawing: [72, 0], emx_name: VIA2, connects: [M2, M3]}
    VIA3: {drawing: [73, 0], emx_name: VIA3, connects: [M3, M4]}
    VIA4: {drawing: [74, 0], emx_name: VIA4, connects: [M4, M5]}
    VIA5: {drawing: [75, 0], emx_name: VIA5, connects: [M5, M6]}
  markers:
    PASSIVE:
      drawing: [99, 0]
      purpose: passive-device region marker (fictitious)

emx_stack:
  geometry_scaling: 1.0
  conductors:
    M1: {thickness_um: 0.2}
    M2: {thickness_um: 0.2}
    M3: {thickness_um: 0.2}
    M4: {thickness_um: 0.2}
    M5: {thickness_um: 0.9}
    M6: {thickness_um: 3.0}
  via_models:
    VIA1: {via: VIA1, emx_effective_size_um: 0.12}
    VIA2: {via: VIA2, emx_effective_size_um: 0.12}
    VIA3: {via: VIA3, emx_effective_size_um: 0.12}
    VIA4: {via: VIA4, emx_effective_size_um: 0.32}
    VIA5: {via: VIA5, emx_effective_size_um: 0.52}

layout_rules:
  # The process's manufacturing grid: the generators snap every coordinate
  # they quantize to it (default 0.005 when left out).
  manufacturing_grid_um: 0.005
  metal_width_space:
    M1: {min_width_um: 0.1, max_width_um: 12.0, min_space_um: 0.1}
    M2: {min_width_um: 0.1, max_width_um: 12.0, min_space_um: 0.1}
    M3: {min_width_um: 0.1, max_width_um: 12.0, min_space_um: 0.1}
    M4: {min_width_um: 0.1, max_width_um: 12.0, min_space_um: 0.1}
    M5: {min_width_um: 0.4, max_width_um: 12.0, min_space_um: 0.4}
    M6: {min_width_um: 1.0, max_width_um: 30.0, min_space_um: 1.5}
  via_primitives:
    VIA1:
      cut_size_um: [0.1, 0.1]
      min_cut_space_um: 0.12
      min_enclosure_um: {M1: 0.03, M2: 0.03}
    VIA2:
      cut_size_um: [0.1, 0.1]
      min_cut_space_um: 0.12
      min_enclosure_um: {M2: 0.03, M3: 0.03}
    VIA3:
      cut_size_um: [0.1, 0.1]
      min_cut_space_um: 0.12
      min_enclosure_um: {M3: 0.03, M4: 0.03}
    VIA4:
      cut_size_um: [0.3, 0.3]
      min_cut_space_um: 0.35
      min_enclosure_um: {M4: 0.05, M5: 0.05}
    VIA5:
      cut_size_um: [0.5, 0.5]
      min_cut_space_um: 0.55
      min_enclosure_um: {M5: 0.1, M6: 0.1}
  # audited_vias (optional): the vias the DRC audit checks for cut enclosure.
  # Left out, as here, it is every via of the metal stack (VIA1..VIA5), under
  # whatever names the process uses; a list replaces that set.
  passive_region:
    marker: PASSIVE
    passive_via_array_coverage:
      modeled: [VIA1, VIA2, VIA3, VIA4, VIA5]
      not_yet_modeled: []
    via_array_rules:
      VIA1: {min_count: 2, max_space_um: 2.0}
      VIA2: {min_count: 2, max_space_um: 2.0}
      VIA3: {min_count: 2, max_space_um: 2.0}
      VIA4: {min_count: 2, max_space_um: 3.0}
      VIA5: {min_count: 2, max_space_um: 4.0}
    via_restrictions:
      DEMO.V.1:
        applies_to: [VIA1]
        scope: passive_region
        source_text: >-
          Fictitious deck text: "V1 arrays inside the PASSIVE marker shall
          keep the modeled array pitch" -- demonstration citation only.
        class_mapping_note: >-
          Demonstrates the citation format; since n28-rules-slim
          (2026-07-19) restrictions are documentation-only and never gate
          generation.
    metal_restrictions:
      DEMO.M.1:
        note: >-
          Demonstration-only note entry: fictitious guidance that wide M6
          plates inside PASSIVE should be slotted per the deck.
        source_text: >-
          Fictitious deck text: "M6 plates wider than the slotting
          threshold require slot arrays" -- demonstration citation only.
    wide_parallel_spacing:
      - metals: [M5, M6]
        when_width_gt_um: 5.0
        when_parallel_length_gt_um: 20.0
        min_space_um: 2.0
```

## Definition of Done

```bash
ic-opt call em.validate_profile /abs/path/to/your/profiles/<profile_id> \
    proc=/path/to/site.proc generate=true
```

When the site `.proc` stays on the simulation host, add `--ssh-profile
<host>`: `proc=` is then that host's path, copied over SSH into a temporary
directory for the check and deleted after it.

All five stages must pass, and the command exits 0 (1 on any failed
stage):

```text
profile: <profile_id> (...)
[schema] PASS
  - <n> conductors, <n> vias, <n> markers
[consistency] PASS
[emx-names-vs-proc] PASS
  - <n> emx_names found in <proc>; <n> conductor thicknesses agree
[gds-layers-vs-proc] PASS
  - <n> drawing and <n> pin layers mapped by the defines of <proc>
[generation] PASS
  - 6 PASS, 0 FAIL, 0 SKIP
result: PASS
```

`generate=true` builds one canonical device per family (ind_sym, xfm_bs,
xfm_ms, xfm_balun, xfm_tw, xfm_il) through the production generator
path and runs the production-scope DRC audit on each. The canonical
devices are mid-range (~100-250 um class); a stack too shallow for a
family (xfm_il needs a top metal at M4 or above) is reported as SKIP,
not a failure. Add `out=<dir>` to keep the generated GDS/ports/manifest
per family for inspection, `families=<generator_id>[,<generator_id>]` to
iterate on one family. Without `proc=` the two proc stages are SKIPPED --
acceptable only when no site proc exists yet (record that gap in the
provenance README).

Iterate fix -> re-run until `result: PASS`. Common errors:

| Error | Fix |
| --- | --- |
| `<path>: Extra inputs are not permitted` | typo or unknown key at that YAML path |
| `coverage.<X> must match ...` | re-mirror that coverage list from the section's actual keys |
| `... holds no rule.yaml` | give the profile directory itself (`<profiles>/<profile_id>`) |
| `unsupported process rule profile: <id>` (from a run) | folder name != id, or `IC_OPT_PROFILE_DIRS` not pointing at the parent dir |
| `emx_name not found in <proc>` | align the catalog `emx_name` with the proc's token |
| `emx_stack vs <proc>: <name>: profile thickness ...` | transcribe the thickness from the same stack the proc was built from |
| `<layer>: drawing layer L/D is not in the define of <name> (...)` | the catalog `drawing` disagrees with the proc's layer map: recheck that layer map row, datatype included, against the proc's `define` |
| `<layer>: <name> has no 'define <name> = ...'` | the proc uses the name but maps no GDS layer to it: pick the name the proc defines, or complete the proc |
| `<layer>: pin layer L/D is not in the define of <name> (...)` | EMX finds port labels only on the layers the define names: take the pin pair the proc includes (recheck the layer map's pin / label datatype), or complete the proc |
| `layout_rules.audited_vias names <via>, ...` | list only catalog vias whose `via_primitives` entry gives the enclosure on both metals, or leave the field out |
| `[generation] FAIL ... min_width/min_space/via_enclosure` | a transcription slip (recheck the DRM number), or the process genuinely cannot host the canonical device -- confirm against the DRM before touching anything |
| `[generation] FAIL ... PortError ... via array` | a via the devices need has no `via_array_rules` entry (it sat in `not_yet_modeled`) |

## After it passes

- In `spec.yaml`, a device selects it with `profile: <profile_id>`, and
  `em.process_file:` names the site EMX proc on the simulation host
  (EMX itself never reads rule.yaml). Export `IC_OPT_PROFILE_DIRS`
  (the parent directory) wherever the pcell runs.
- Real-EMX runs remain a separately guarded step (`--plan` first, user
  approval, resource envelope); a passing profile does not authorize
  them.
- Keep the profile under your own version control together with its
  provenance README.
