# Fix-Run 15-Corner Waveform CSV Requirement

This template follows the structure used in the verified local and remote
15-corner fix-run workflow. It runs one user-specified design point across
three model sections and five corner variable values per section, then exports
the requested OCEAN waveform to CSV. It does not run an optimizer.

## Workflow

```yaml
schema_version: "1.0"
mode: fix_run
starting_run_id: real_001
```

## Project

```yaml
project_name: mixer_fix_run_15corner
description: Fixed Mixer point across 15 process/corner-variable combinations with NF waveform CSV export
backend: maestro_exported_spectre_deck
```

## Maestro Source

```yaml
maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_CG_Noise/maestro/results/maestro/Interactive.N/1/Mixer_CS_CG_NF
virtuoso_library: Virtuoso_Bridge_test
cell: MixerCS_PSS_CG_Noise
design_view: schematic
maestro_view: maestro
test_name: Mixer_CS_CG_NF
corner: Nominal
```

## Design Variables

```yaml
- name: F
  kind: integer
  lower: '20'
  upper: '30'
  step: '2'
- name: W
  kind: continuous_step
  lower: 0.6u
  upper: 1.2u
  step: 0.2u
- name: L
  kind: continuous_step
  lower: 30n
  upper: 40n
  step: 10n
- name: VB_LO
  kind: continuous_step
  lower: 280m
  upper: 400m
  step: 20m
- name: FCS
  kind: integer
  lower: '40'
  upper: '56'
  step: '2'
- name: WCS
  kind: continuous_step
  lower: 0.6u
  upper: 1.2u
  step: 0.2u
- name: LCS
  kind: continuous_step
  lower: 30n
  upper: 50n
  step: 10n
- name: VB_RF
  kind: continuous_step
  lower: 300m
  upper: 440m
  step: 20m
```

## Spectre Settings

```yaml
engine: spectre_x
preset: ax
output_format: psfxl
threads_per_run: 10
parallel_jobs: 8
timeout_s: 7200
require_license_check: true
keep_failed_runs: true
keep_successful_runs: true
```

## Process Corners

```yaml
corners:
  - id: tt_m25
    model_section: Post_simu_top_tt
    variables:
      temperature: '-25'
  - id: tt_0
    model_section: Post_simu_top_tt
    variables:
      temperature: '0'
  - id: tt_27
    model_section: Post_simu_top_tt
    variables:
      temperature: '27'
  - id: tt_85
    model_section: Post_simu_top_tt
    variables:
      temperature: '85'
  - id: tt_125
    model_section: Post_simu_top_tt
    variables:
      temperature: '125'
  - id: ss_m25
    model_section: Post_simu_top_ss
    variables:
      temperature: '-25'
  - id: ss_0
    model_section: Post_simu_top_ss
    variables:
      temperature: '0'
  - id: ss_27
    model_section: Post_simu_top_ss
    variables:
      temperature: '27'
  - id: ss_85
    model_section: Post_simu_top_ss
    variables:
      temperature: '85'
  - id: ss_125
    model_section: Post_simu_top_ss
    variables:
      temperature: '125'
  - id: ff_m25
    model_section: Post_simu_top_ff
    variables:
      temperature: '-25'
  - id: ff_0
    model_section: Post_simu_top_ff
    variables:
      temperature: '0'
  - id: ff_27
    model_section: Post_simu_top_ff
    variables:
      temperature: '27'
  - id: ff_85
    model_section: Post_simu_top_ff
    variables:
      temperature: '85'
  - id: ff_125
    model_section: Post_simu_top_ff
    variables:
      temperature: '125'
```

`variables` are generic netlist parameter overrides. This example uses a
parameter named `temperature`; the workflow does not special-case that name.

## Fixed Points

```yaml
schema_version: "1.0"
points:
  - candidate_id: user_point_001
    parameters:
      F: '24'
      W: 0.8u
      L: 30n
      VB_LO: 340m
      FCS: '48'
      WCS: 0.8u
      LCS: 40n
      VB_RF: 360m
```

## Waveform Exports

```yaml
schema_version: "1.0"
exports:
  - name: nf_pnoise
    expression: 'getData("NF" ?result "pnoise")'
    output_format: csv
    nil_policy: fail
```

## Approval Checklist

```yaml
metric_formulas_user_approved: true
maestro_source_user_approved: true
variable_bounds_user_approved: true
spectre_resource_settings_user_approved: true
```
