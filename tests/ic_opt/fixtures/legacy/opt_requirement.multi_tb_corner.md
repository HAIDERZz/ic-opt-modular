# Multi-Testbench Multi-Corner Optimization Requirement

Use this requirement when one optimizer candidate needs metrics from multiple
Maestro/ADE testbenches and each candidate should also be checked across multiple
process corners.

Multi-corner is configured only from `Process Corners`; there is no
`--multi-corner` CLI switch. Inside one candidate, the workflow runs
`testbench x corner` serially, so `parallel_jobs` still means candidate-level
concurrency only.

Monte Carlo is intentionally deferred from this flow. Use it after optimization
as follow-up validation on the chosen candidate.

## Workflow

```yaml
schema_version: "1.0"
mode: optimize
```


## Project


```yaml
project_name: mixer_multi_tb_opt
description: Optimize one Mixer candidate across CG/NF/BW, IIP3, and P1dB testbenches plus TT/SS/FF corners
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
- id: p1db
  maestro_point_root: /home/username/simulation/Virtuoso_Bridge_test/MixerCS_PSS_P1dB/maestro/results/maestro/Interactive.N/1/P1dB_Test
  virtuoso_library: Virtuoso_Bridge_test
  cell: MixerCS_PSS_P1dB
  design_view: schematic
  maestro_view: maestro
  test_name: P1dB_Test
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


## Metrics


```yaml
- name: BW
  unit: Hz
  testbench: cg_nf
  ocean_expression: bandwidth(db((harmonic((v("/IF_P" ?result "pac") - v("/IF_N" ?result "pac")) '-1) / harmonic(drplPacVolGnExpDen("(v(\"/RF_P\" ?result \"pac\")-v(\"/RF_N\" ?result \"pac\"))" '(0) nil) '-1))) 3 "low")
- name: MAX_GAIN
  unit: dB
  testbench: cg_nf
  ocean_expression: ymax(db((harmonic((v("/IF_P" ?result "pac") - v("/IF_N" ?result "pac")) '-1) / harmonic(drplPacVolGnExpDen("(v(\"/RF_P\" ?result \"pac\")-v(\"/RF_N\" ?result \"pac\"))" '(0) nil) '-1))))
- name: NF_3G
  unit: dB
  testbench: cg_nf
  ocean_expression: value(getData("NF" ?result "pnoise") 3e+09)
- name: IIP3
  unit: dBm
  testbench: iip3
  ocean_expression: rapidIIPN("pac_ip3")
- name: P1dB
  unit: dBm
  testbench: p1db
  ocean_expression: compressionVRI((v("/IF_P" ?result "pss_fd") - v("/IF_N" ?result "pss_fd")) '1 ?rport resultParam("PORT2:r" ?result "pss_fd") ?gcomp 1)
```


## Constraints


```yaml
- metric: BW
  op: gt
  value: 28e9 Hz
- metric: MAX_GAIN
  op: gt
  value: 5.5 dB
- metric: NF_3G
  op: lt
  value: 9 dB
- metric: IIP3
  op: gt
  value: 1 dBm
- metric: P1dB
  op: gt
  value: -6 dBm
```


## Objective


```yaml
direction: minimize
expression: -(0.1*min(max(0,min(1,10*(ln(BW/28e9)/ln(10))/0.6)),max(0,min(1,(MAX_GAIN-5.5)/2)),max(0,min(1,(9-NF_3G)/0.7)),max(0,min(1,(IIP3-1)/2)),max(0,min(1,(P1dB+6)/1.5)))+0.8*(0.1*max(0,min(1,10*(ln(BW/28e9)/ln(10))/0.6))+0.10*max(0,min(1,(MAX_GAIN-5.5)/2))+0.30*max(0,min(1,(9-NF_3G)/0.7))+0.20*max(0,min(1,(IIP3-1)/2))+0.30*max(0,min(1,(P1dB+6)/1.5))))
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
max_evaluations: 10
batch_size: 10
random_seed: 20260528
optimizer_cpu_threads: 32
failure_penalty: 1000000.0
deduplicate_candidates: true
openbox:
  surrogate_type: prf
  acq_type: eic
  acq_optimizer_type: local_random
  initial_trials: auto
```


## Approval Checklist


```yaml
metric_formulas_user_approved: true
maestro_source_user_approved: true
variable_bounds_user_approved: true
spectre_resource_settings_user_approved: true
```
