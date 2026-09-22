# Multi-Testbench Optimization Requirement

Use this reference when one optimizer candidate needs metrics from more than
one Maestro/ADE testbench. Each `maestro_point_root` must point to a
single-point Maestro/ADE result directory that contains `netlist/input.scs`.

## Workflow

```yaml
schema_version: "1.0"
mode: optimize
```

## Project

```yaml
project_name: insight_local_mt40_history_multi_tb
description: Optimize one Mixer candidate across CG/NF/BW, IIP3, and P1dB testbenches
backend: maestro_exported_spectre_deck
```

## Maestro Source

```yaml
testbenches:
  - id: cg_nf
    maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_CG_Noise/maestro/results/maestro/Interactive.N/1/Mixer_CS_CG_NF
    virtuoso_library: Virtuoso_Bridge_test
    cell: Mixer_PSS_CG_Noise
    design_view: schematic
    maestro_view: maestro
    test_name: CG_NF_Test
    corner: Nominal

  - id: iip3
    maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_IIP3/maestro/results/maestro/Interactive.N/1/Mixer_CS_IIP3
    virtuoso_library: Virtuoso_Bridge_test
    cell: Mixer_PSS_IIP3
    design_view: schematic
    maestro_view: maestro
    test_name: IIP3_Test
    corner: Nominal

  - id: p1db
    maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_P1dB/maestro/results/maestro/Interactive.N/1/P1dB_Test
    virtuoso_library: Virtuoso_Bridge_test
    cell: Mixer_PSS_P1dB
    design_view: schematic
    maestro_view: maestro
    test_name: P1dB_Test
    corner: Nominal
```

## Design Variables

```yaml
- name: F
  kind: integer
  lower: "16"
  upper: "30"
  step: "2"
- name: W
  kind: continuous_step
  lower: "0.4u"
  upper: "2u"
  step: "0.2u"
- name: L
  kind: continuous_step
  lower: "30n"
  upper: "50n"
  step: "10n"
- name: VB_LO
  kind: continuous_step
  lower: "150m"
  upper: "350m"
  step: "20m"
```

## Metrics

```yaml
- name: BW
  unit: Hz
  testbench: cg_nf
  ocean_expression: bandwidth(db(((vh('pac "/IF_P" '-1) - vh('pac "/IF_N" '-1)) / (vh('pac "/RF_P" '(0)) - vh('pac "/RF_N" '(0))))) 3 "low")

- name: MAX_GAIN
  unit: dB
  testbench: cg_nf
  ocean_expression: ymax(ymax(db(((vh('pac "/IF_P" '-1) - vh('pac "/IF_N" '-1)) / (vh('pac "/RF_P" '(0)) - vh('pac "/RF_N" '(0)))))))

- name: NF_3G
  unit: dB
  testbench: cg_nf
  ocean_expression: value(getData("NF" ?result "pnoise") 3e+09)

- name: IIP3
  unit: dBm
  testbench: iip3
  ocean_expression: rapidIIPN("pac_ip3")

- name: P1DB
  unit: dBm
  testbench: p1db
  ocean_expression: compressionVRI((v("/IF_P" ?result "pss_fd") - v("/IF_N" ?result "pss_fd")) '1 ?rport resultParam("PORT2:r" ?result "pss_fd") ?gcomp 1)
```

## Constraints

```yaml
- metric: BW
  op: gt
  value: "19e9 Hz"
- metric: MAX_GAIN
  op: gt
  value: "4 dB"
- metric: NF_3G
  op: lt
  value: "12 dB"
- metric: IIP3
  op: gt
  value: "0 dBm"
- metric: P1DB
  op: gt
  value: "-2 dBm"
```

## Objective

```yaml
direction: minimize
expression: "NF_3G * 1e9 / (BW*MAX_GAIN*IIP3*P1DB) "
```

## Spectre Settings

```yaml
engine: spectre_x
preset: ax
output_format: psfxl
threads_per_run: 10
parallel_jobs: 20
timeout_s: 3600
require_license_check: true
keep_failed_runs: true
keep_successful_runs: true
```

## Optimizer Settings

```yaml
algorithm: openbox
strategy: openbox_auto
initialization: sobol
max_evaluations: 40
batch_size: 10
random_seed: 20260528
optimizer_cpu_threads: 4
failure_penalty: 1000000.0
deduplicate_candidates: true
```

## History Warm Start

```yaml
enabled: true
sources:
  - path: /absolute/path/to/previous_same_circuit_project
    label: local_round1_mt80
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
