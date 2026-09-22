# Native TuRBO Optimization Requirement

Use this template when legal variable steps are fine enough that snapping a
continuous trust-region suggestion to the configured grid is a small change.
Prefer OpenBox PRF-EIC for coarse integer or duplicate-heavy spaces.

## Workflow

```yaml
schema_version: "1.0"
mode: optimize
```

## Project

```yaml
project_name: mixer_cg_nf_turbo_opt
description: Optimize one Mixer CG/NF testbench with native TuRBO
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
- name: W
  kind: continuous_step
  lower: 0.6u
  upper: 1.2u
  step: 0.02u
- name: L
  kind: continuous_step
  lower: 30n
  upper: 40n
  step: 1n
- name: VB_LO
  kind: continuous_step
  lower: 280m
  upper: 400m
  step: 10m
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
algorithm: turbo
strategy: turbo_trust_region
initialization: sobol
max_evaluations: 40
batch_size: 8
random_seed: 20260528
optimizer_cpu_threads: 8
failure_penalty: 1000000.0
deduplicate_candidates: true
turbo:
  snap_to_step: true
  duplicate_handling: resample
```

## Approval Checklist

```yaml
metric_formulas_user_approved: true
maestro_source_user_approved: true
variable_bounds_user_approved: true
spectre_resource_settings_user_approved: true
```
