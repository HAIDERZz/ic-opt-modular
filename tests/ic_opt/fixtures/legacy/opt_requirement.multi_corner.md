# Single-Testbench Multi-Corner Optimization Requirement

Use this template when one optimizer candidate is evaluated by one testbench
across several process corners.

## Workflow

```yaml
schema_version: "1.0"
mode: optimize
```

## Project

```yaml
project_name: mixer_cg_nf_corner_opt
description: Optimize one Mixer CG/NF testbench across TT/SS/FF corners
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

## Process Corners

```yaml
objective_policy: worst_case
constraint_policy: all_corners
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

## Metrics

```yaml
- name: NF_3G
  unit: dB
  ocean_expression: 'value(getData("NF" ?result "pnoise") 3e+09)'
```

## Constraints

```yaml
- metric: NF_3G
  op: lt
  value: 9 dB
```

## Objective

```yaml
direction: minimize
expression: NF_3G
```

## Spectre Settings

```yaml
engine: spectre_x
preset: ax
output_format: psfxl
threads_per_run: 10
parallel_jobs: 10
timeout_s: 7200
require_license_check: true
keep_failed_runs: true
keep_successful_runs: true
```

## Optimizer Settings

```yaml
algorithm: openbox
strategy: openbox_prf_eic
initialization: sobol
max_evaluations: 30
batch_size: 10
random_seed: 20260528
optimizer_cpu_threads: 32
failure_penalty: 1000000.0
deduplicate_candidates: true
```

## Approval Checklist

```yaml
metric_formulas_user_approved: true
maestro_source_user_approved: true
variable_bounds_user_approved: true
spectre_resource_settings_user_approved: true
```
