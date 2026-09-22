# Multi-Testbench Multi-Corner Fix-Run Metrics and Waveform Requirement

Use this template when every fixed point must produce routed scalar metrics and
waveform CSV evidence across multiple Maestro testbenches and process corners.
Every metric and waveform export names its owning testbench.

## Workflow

```yaml
schema_version: "1.0"
mode: fix_run
starting_run_id: real_001
```

## Project

```yaml
project_name: mixer_fix_run_multi_tb
description: Two fixed Mixer points with routed metrics and waveform CSV evidence
backend: maestro_exported_spectre_deck
```

## Maestro Source

```yaml
testbenches:
  - id: cg_nf
    maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_CG_Noise/maestro/results/maestro/Interactive.N/1/Mixer_CS_CG_NF
    virtuoso_library: Virtuoso_Bridge_test
    cell: MixerCS_PSS_CG_Noise
    design_view: schematic
    maestro_view: maestro
    test_name: Mixer_CS_CG_NF
    corner: Nominal
  - id: iip3
    maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_IIP3/maestro/results/maestro/Interactive.N/1/Mixer_CS_IIP3
    virtuoso_library: Virtuoso_Bridge_test
    cell: MixerCS_PSS_IIP3
    design_view: schematic
    maestro_view: maestro
    test_name: Mixer_CS_IIP3
    corner: Nominal
```

## Process Corners

```yaml
corners:
  - id: tt
    model_section: Post_simu_top_tt
    variables:
      temperature: '27'
  - id: ss
    model_section: Post_simu_top_ss
    variables:
      temperature: '125'
  - id: ff
    model_section: Post_simu_top_ff
    variables:
      temperature: '-40'
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
```

## Spectre Settings

```yaml
engine: spectre_x
preset: ax
output_format: psfxl
threads_per_run: 10
parallel_jobs: 4
timeout_s: 7200
require_license_check: true
keep_failed_runs: true
keep_successful_runs: true
```

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
  - candidate_id: user_point_002
    parameters:
      F: '26'
      W: 1.0u
      L: 40n
      VB_LO: 360m
```

## Metrics

```yaml
- name: NF_3G
  unit: dB
  testbench: cg_nf
  ocean_expression: 'value(getData("NF" ?result "pnoise") 3e+09)'
- name: IIP3
  unit: dBm
  testbench: iip3
  ocean_expression: 'rapidIIPN("pac_ip3")'
```

## Waveform Exports

```yaml
schema_version: "1.0"
exports:
  - name: nf_pnoise
    testbench: cg_nf
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
