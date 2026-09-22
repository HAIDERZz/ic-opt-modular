# Single-Testbench Multi-Corner History Warm-Start Requirement

Use this template for a new OpenBox optimization project that evaluates one
testbench across several process corners and reuses compatible observations
from previous same-circuit projects. This is not continuation.

## Workflow

```yaml
schema_version: "1.0"
mode: optimize
```

## Project

```yaml
project_name: mixer_cg_nf_corner_history_opt
description: OpenBox PRF-EIC multi-corner optimization with history warm start
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
parallel_jobs: 8
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
max_evaluations: 40
batch_size: 8
random_seed: 20260528
optimizer_cpu_threads: 8
failure_penalty: 1000000.0
deduplicate_candidates: true
```

## History Warm Start

```yaml
enabled: true
sources:
  - path: /absolute/path/to/previous_same_circuit_project
    label: round1
max_observations: 80
warm_start_strategy: topk
```

## Approval Checklist

```yaml
metric_formulas_user_approved: true
maestro_source_user_approved: true
variable_bounds_user_approved: true
spectre_resource_settings_user_approved: true
```
