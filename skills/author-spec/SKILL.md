---
name: author-spec
description: Turn a user's design request (natural language, Maestro exports, device geometry, process) into a validated ic-opt `spec.yaml` -- decide the shape (circuit, EM device in a circuit, device only), read the facts off the exports, fill every section, then prove it with `ic-opt doctor` and `--plan`.
---

# /author-spec

You are writing `PROJECT/spec.yaml`: the WHAT of a design problem. How it is run
(recipe, strategy, fixed points, waveforms) never goes in it. The schema is
`ic_opt.spec.Spec` (`src/ic_opt/spec.py`): strict, unknown keys refused, and
`ic-opt doctor` / `ic-opt run ... --plan` are the checks. Your loop is
**ask and read -> write -> doctor -> plan -> fix -> hand the plan to the user**.
The user owns the design question and every number about their machine; you
own the mechanics and the evidence.

## 1. Decide the shape first

| the request says | shape | sections |
| --- | --- | --- |
| tune circuit parameters of a schematic (transistor sizes, biases, ...) | **circuit** | `testbenches`, `variables`, `metrics` (OCEAN), `constraints`, `objective`, `corners`?, `simulator`, `budget` |
| the transformer / inductor in the circuit is a real EM device to size | **EM in a circuit** | the above + `devices`, `em`, `bindings`, device `metrics` |
| characterize or sweep a device by itself (a library part, a device study) | **device only** | `devices`, `em`, `variables`, device `metrics`, `simulator`, `budget` -- no `testbenches` |

One spec is one circuit. Every circuit variable must be a top-level
`parameters` entry of **every** testbench's exported netlist, so testbenches of
different cells (a mixer and its LO transformer's own testbench) do not share
a spec: two specs, two projects. A device's variables are consumed by
`devices` and are not netlist parameters.

## 2. Collect the facts, and where each one comes from

From the **user** (never invented, never defaulted):
- the goal and its priorities (what to maximize / minimize, what must hold);
- the sweep ranges and steps of every variable, or the bounds to explore;
- the corners they want (names in their PDK's model file) and the temperatures;
- the resources: `simulator.parallel_jobs`, `threads_per_run`, `timeout_s`;
  for EM `em.threads`, `memory_gb`, `timeout_s`, and how many EMX runs may be
  spent; the host and its `~/.ic-opt/site.yaml` entry bound them;
- the device geometry that stays fixed (outer diameters, openings, leads,
  metals, ground fixture) and the process: profile id (`IC_OPT_PROFILE_DIRS`),
  the EMX `.proc` path on the simulation host, the metal names.

From the **Maestro export** (`<maestro_point_root>/netlist/input.scs`, read it
on the host with `ssh HOST cat ...` or through `--plan`, which fetches it):
- the `parameters ...` line at top level: the **only** names a variable may
  have, with their current values (the design as exported; `--plan` reports a
  variable the export lacks: `[plan] netlist.import <tb>: FAIL variable X was
  not found in top-level parameters`);
- the `include "<model file>" section=<name>` line: the model file and the
  section a corner's `model_section` replaces; the PDK's other sections
  (`section ...` lines in that file) are the corner names available;
- the `nport` instances (`NPORT0 ( P1 0 P2 0 net6 0 net4 0 ) nport ...`): their
  names go in `bindings.instance`, and their terminal order says which device
  port each terminal takes;
- the analyses' sweeps (`pac ... start= stop=`, `sp` / `noise` ranges): a metric
  can only report what the sweep covers -- a bandwidth beyond the window is
  censored at the window's edge, a value at a frequency outside it is nil;
- `simulatorOptions options temp=27 ...`: ADE exports write the simulation
  temperature there as a literal and leave the `temperature` parameter
  declared but unused, so a corner `variables: {temperature: ...}` changes
  nothing (`--plan` warns: `WARNING the corners set ['temperature'], which
  this export declares ... but never uses`). Set it with the corner's
  `options: { temp: "125" }`, which rewrites that statement; never edit the
  export.

From the **package** for a device: `docs/em/devices.md` (every family's fields,
constraints and retired names); `ic-opt describe` for a block; the examples
below.

## 3. Sentences to sections

| the user says | you write |
| --- | --- |
| "sweep W from 0.6u to 1.2u in 0.2u steps", "F is 20 to 30, even" | `variables`: `continuous_step` with a shared unit suffix, or `integer`; `(upper - lower)` divisible by `step` |
| "NF at 3 GHz below 9 dB", "gain over 5.5 dB" | `metrics` (an OCEAN expression that returns **one number**) + `constraints` (`lt / le / gt / ge`, value with the metric's unit) |
| "maximize IIP3", "weigh NF and gain, the worst one counts most" | `objective`: `direction` + an expression over metric names with `+ - * / ** %`, `min`, `max`, `ln` (the bottleneck form: `-(a*min(z1..zn) + b*(w1*z1 + ... ))` with each `z` a clipped normalized margin) |
| "at tt / ss / ff", "at 125 degrees" | `corners`: `model_section` per corner (the PDK's section names); the temperature through `options: { temp: "125" }` (the `simulatorOptions` statement); `variables` only for names on the `parameters` line the netlist uses; `corner_policy` says how corners score |
| "the input transformer is a real EM device", "let EMX size the primary width" | `devices` (+ `em`, + a `bindings` entry per nport it feeds) and `variables` named `<device>.<field>` |
| "what are L, Q and k of the transformer at 60 GHz" | device `metrics`: `quantity` (`Lp / Qp / Ls / Qs / k` with `frequency_hz`, or the scalars `Lp_lf / Lp_res / Qp_peak / SRF_p / k_lf` and their `s` / system `SRF` forms) |
| "12 points", "no more than 300 simulations" | `budget.max_simulations` (every simulation the project's store holds counts, also the ones a later spec edit stops reusing); the point count is the recipe's `budget=` |

## 4. Section reference

### `project`, `description`
`project` is a name (`[A-Za-z_][A-Za-z0-9_]*`); `description` free text.

### `testbenches`
One entry per Maestro export: `id` (a name you choose; metrics and bindings
refer to it), `maestro_point_root` (the export directory on the **simulation
host**, holding `netlist/input.scs`), `virtuoso_library`, `cell`, `test_name`;
`design_view` (`schematic`), `maestro_view` (`maestro`) and `corner`
(`Nominal`) only when the export used other names.

### `corners`, `corner_policy`
Each corner: `id`, `model_section` (replaces the `section=` of the model
include; there must be one such include line), `model_file` (an absolute host
path, only to swap the file), `options` (`key=value` on the netlist's
top-level `simulatorOptions` statement -- rewritten in place, appended when
missing; `temp: "125"` is how the simulation temperature of an ADE export is
set, since the export carries it as a literal), `variables` (top-level
parameter values the netlist actually uses).
`corner_policy.objective`: `worst_case` (the worst corner's objective is the
point's) or `nominal`; `corner_policy.constraints`: `all_corners` (every corner
must satisfy them) or `nominal`. Corners act on PDK devices: a testbench of
ideal elements and nports (an sNp bound into an ideal balun) gives the same
numbers at every corner, and needs none. EM devices are measured once per
point, corner-independent, and count for every corner.

### `variables`
`name`, `kind` (`integer`: whole numbers without units; `continuous_step`:
values with one shared SI suffix such as `0.6u`, `40n`, `280m`, or plain),
`lower`, `upper`, `step` (strings; the grid is `lower + k*step`, so the range
must divide by the step). Put the bounds around the export's current values
and make sure the current design sits on the grid, so it can be evaluated as
a point and compared with (an exported `VB_RF=0` outside a `300m..440m` range
cannot). Circuit variables are top-level parameters of every testbench;
device variables are `<device>.<field>` (or listed under the device's
`variables`).

### `metrics`
Testbench metric: `name`, `unit`, `expression` (OCEAN, must evaluate to a
scalar -- `value(...)`, `ymax(...)`, `ymin(...)`, `xmax(...)`, `xmin(...)`,
`cross(...)`, `rapidIIPN(...)`, `compressionVRI(...)`; `bandwidth(...)` and
other waveform-returning calls fail as `non_scalar:<type>`, a call that finds
nothing as `no_value:nil`), `testbench`
(required with more than one testbench). Replacements that keep the meaning:

| ADE gives | write |
| --- | --- |
| `bandwidth(<g> 3 "low")` (a waveform object) | `if(car(errset(cross(<g> (ymax(<g>) - 3) 1 "either") t)) then car(errset(cross(<g> (ymax(<g>) - 3) 1 "either") t)) else xmin(<g>))` -- the -3 dB crossing, or the window's far edge when the gain never falls 3 dB inside it |
| a curve `w` at one frequency | `value(w 3e+09)` |
| a curve's peak / its frequency | `ymax(w)` / `xmax(w)` |
| `cross(...)` alone | partial: it errors where there is no crossing -- wrap it as above so every point yields a number |
| `compressionVRI(<v> '1 ?rport <r> ?gcomp 1)` alone | partial: it returns nil where the gain never falls 1 dB inside the swept input power (`no_value:nil`) -- `or(compressionVRI(<v> '1 ?rport <r> ?gcomp 1) xmax(xval(harmonic(<v> '1))))`: the compression point, or the last swept input power when there is none |

The wrapped bandwidth is right-censored: where the gain never falls 3 dB
inside the swept window it returns the window's far edge (the pac window's
width, 32 GHz on a 80-112 GHz sweep), so that value means "at least", a
constraint on it stops discriminating there, and its normalized score sits at
full marks. Widen the sweep or say so in the report (N-42: 49 of 80 points).
The wrapped compression point is right-censored the same way: a point whose
gain has not fallen 1 dB at the sweep's last input power reports that power (0
dBm on a -40..0 dBm sweep), meaning "at least". Such points are usually the
low-gain ones (N-51: 6 of 80, all in the first three batches), so check the
gain before reading the value as linearity. The fallback reads the sweep from
the data: with the signal missing it fails as `expression_error`, it does not
turn into a number. A number typed in its place would.

YAML: an OCEAN expression carries quotes, so write it as a plain scalar on its
own line (`expression: value(getData("NF" ?result "pnoise") 3e+09)`) or as a
block scalar; inside a flow mapping (`{ name: ..., expression: "..." }`) the
inner quotes end the string early. Copy the expression verbatim; do not
normalize `'-1` into `(quote -1)` or the like.

A metric that fails on some points does not just lose a number: the point is
`metric_failed`, has no objective, and the optimizer takes it as a failed point
(OpenBox and TuRBO: the worst value they have seen; `metric_gp`: a point that
gave no value), so it steers the search away from exactly those points (N-35: the
wide-band ones). `sim.evaluate` prints `metric X failed on N of M points` after
a batch; stop and fix the expression before spending more budget. Device metric: `name`,
`unit`, `device`, `quantity` (a curve `Lp / Qp / Ls / Qs / k` needs
`frequency_hz`; scalars `Lp_lf / Lp_res / Qp_peak / SRF_p / Ls_lf / Ls_res /
Qs_peak / SRF_s / k_lf / SRF` do not). A metric whose expression returns nil or
a waveform makes the point `metric_failed` (its other metrics are kept).

### `constraints`
`metric`, `op` (`lt`, `le`, `gt`, `ge`), `value` ("28e9 Hz", "-6 dBm": the
number, then the unit). Every metric named must exist.

### `objective`
`direction` (`minimize` / `maximize`) and `expression` over metric names with
`+ - * / ** %`, `min(...)`, `max(...)`, `ln(...)` and numbers; nothing else.
Omit the section for a pure feasibility / waveform run. A bottleneck form with
clipped margins (`max(0, min(1, ...))`) is flat wherever a constraint is far
violated: with targets most of the space cannot meet, most objective values tie
(N-35: 37 of 50) and the surrogate has no gradient to follow. Set the targets
so a fair share of the space is feasible, or keep the margins unclipped until
it is.

### `devices`
`id`, `generator` (`clean_port_ind_sym`, `clean_port_xfm_bs`,
`clean_port_xfm_ms`, ... -- `docs/em/devices.md`), `profile` (the process rule
profile id found through `IC_OPT_PROFILE_DIRS`), `ports` (the family's fixed
port set, in order: `[P1, N1]`, `[P1, N1, P2, N2]`, plus `CT` / `CTP` / `CTS`
with a tap metal), `fixed` (every generator field that is not a variable,
including `ground_fixture`), `variables` (field -> spec variable; default
`<id>.<field>`; names without the prefix are the device's only in a spec of
one device and no testbench -- beside testbenches they are circuit
variables), `topology` only for a generator whose secondary winds the
other way (`drives: [[P1, N1], [P2, N2]]`).

### `em`
`process_file` (absolute path **on the simulation host**), `frequencies`
(`{start_hz, stop_hz, step_hz}` or `num_steps`, or a list), `mode`
(`quasistatic` default, `full_wave`), `accuracy` (`standard` ... `highest`, or
a grid `{edge_width_um, max_splits, thickness_um}`), `three_d_metals` (the
windings' metals by their EMX names), `threads`, `memory_gb`, `timeout_s`
(optional `parallel_jobs`: EMX runs at once across the workers, when the
user caps them below `simulator.parallel_jobs`)
(required, the user's), `simultaneous_frequencies: 0` (keep it). One `em`
section serves every device. `process_file` is a path on the simulation host
(POSIX); `IC_OPT_PROFILE_DIRS` is read on the machine running ic-opt, so on a
Windows controller it is a Windows path (`D:/work/profiles`).

### `bindings`
One per nport instance that takes a device's S-parameters: `testbench`,
`instance` (the nport's name in that netlist), `device`, `terminals` (the
device's ports in the order of the nport's terminals = the sNp column order;
a permutation of `ports`). Read the instance line in the export to order them.
A `null` entry drops that nport terminal (a tap the export wired to two
terminals, a grounded terminal): the instance is rewritten to the kept ones,
so a six-port tapped transformer binds into a ten-terminal nport as
`[P1, N1, CTP, null, P2, N2, CTS, null, null, null]`; every port of the
device still appears exactly once.

### `simulator`
`preset` (`ax` default; `cx`, `mx`, `lx`, `vx`), `threads_per_run`,
`parallel_jobs`, `timeout_s` (required, the user's; `parallel_jobs x
threads_per_run` must fit the host's site.yaml entry), `strategy_threads`
(default 1: the threads the strategy's own computation may use while it
proposes a batch, on the machine running ic-opt; more only when the user gives
a number, within that machine's `hosts.local` entry; not in the fingerprint),
`license_check` (default true), `license_queue_timeout_s` (only when the user
gives it),
`keep_failed_runs` / `keep_successful_runs` (raw psf retention),
`operating_points` (default true: every transistor's operating point is kept per
child; ic-opt adds the `info what=oppoint where=rawfile` statement -- and a DC
analysis when the export has none -- to the rendered netlist; `false` only when
the user does not want the netlist touched; not in the fingerprint),
`stop_at_first_failure` (unset by default: a point stops at its first simulation
that shows it cannot be feasible when the run evaluates it at several corners, and
runs every simulation at one; `true` stops it at one corner too, `false` runs
every simulation of every point, for a characterization run; not in the
fingerprint).

### `budget`
`max_simulations`: the ceiling on simulations the project may hold. Size it
as points × simulations per point (testbenches × corners, plus EMX runs and
device measurements) with headroom for a re-run of a few points; a spec edit
keeps the earlier simulations counted. It is not part of the problem's
identity, so raising it continues a run.

## 5. Complete examples (kept valid by `tests/ic_opt/test_skill_author_spec.py`)

Circuit, two testbenches, two corners:

```yaml
# complete: circuit
project: lna_opt
description: LNA noise figure and gain across tt / ss with the bias and width swept
testbenches:
  - id: nf
    maestro_point_root: /home/user/simulation/LNA/NF_Test/results/maestro/Interactive.3/1/NF_Test
    virtuoso_library: LNA_lib
    cell: LNA_NF
    test_name: NF_Test
  - id: gain
    maestro_point_root: /home/user/simulation/LNA/Gain_Test/results/maestro/Interactive.3/1/Gain_Test
    virtuoso_library: LNA_lib
    cell: LNA_Gain
    test_name: Gain_Test
corners:
  - { id: tt, model_section: Post_simu_top_tt, options: { temp: "27" } }
  - { id: ss, model_section: Post_simu_top_ss, options: { temp: "125" } }
corner_policy: { objective: worst_case, constraints: all_corners }
variables:
  - { name: W,     kind: continuous_step, lower: 0.6u, upper: 1.2u, step: 0.2u }
  - { name: VB,    kind: continuous_step, lower: 280m, upper: 400m, step: 20m }
  - { name: F,     kind: integer,         lower: "20", upper: "30", step: "2" }
metrics:
  - { name: NF_3G, unit: dB, testbench: nf,   expression: 'value(getData("NF" ?result "pnoise") 3e+09)' }
  - { name: GAIN,  unit: dB, testbench: gain, expression: 'ymax(db20(getData("gain" ?result "sp")))' }
constraints:
  - { metric: NF_3G, op: lt, value: 9 dB }
  - { metric: GAIN,  op: gt, value: 5.5 dB }
objective:
  direction: minimize
  expression: -(0.1*min(max(0,min(1,(9-NF_3G)/0.7)),max(0,min(1,(GAIN-5.5)/2)))+0.8*(0.5*max(0,min(1,(9-NF_3G)/0.7))+0.5*max(0,min(1,(GAIN-5.5)/2))))
simulator: { preset: ax, threads_per_run: 10, parallel_jobs: 6, timeout_s: 3600 }
budget: { max_simulations: 300 }
```

An EM transformer inside a circuit testbench, with device metrics:

```yaml
# complete: em_circuit
project: lo_xfmr_opt
description: LO transformer widths sized by EMX inside its S-parameter testbench
testbenches:
  - id: lo_tb
    maestro_point_root: /home/user/simulation/LO_XFMR_TB/maestro/results/maestro/Interactive.12/1/XFMR_TB
    virtuoso_library: LO_lib
    cell: LO_XFMR_TB
    test_name: XFMR_TB
devices:
  - id: xfmr
    generator: clean_port_xfm_bs
    profile: demo_6m
    ports: [P1, N1, P2, N2]
    fixed:
      primary_outer_diameter_um: 73
      secondary_outer_diameter_um: 58
      primary_opening_um: 8.75
      secondary_opening_um: 7.5
      primary_lead_length_um: 20.75
      secondary_lead_length_um: 21.5
      center_spacing_um: 0
      primary_metal: "6"
      secondary_metal: "5"
      ground_fixture: { inner_margin_um: 4, ring_width_um: 70, stub_length_um: 2, stub_chamfer_um: 2 }
    variables: { primary_width_um: xfmr.wp, secondary_width_um: xfmr.ws }
em:
  process_file: /opt/pdk/demo_6m/demo.proc
  frequencies: { start_hz: 0, stop_hz: 200e9, step_hz: 1e9 }
  mode: full_wave
  accuracy: standard
  three_d_metals: [M6, M5]
  threads: 4
  memory_gb: 8
  timeout_s: 1800
  simultaneous_frequencies: 0
bindings:
  - { testbench: lo_tb, instance: NPORT0, device: xfmr, terminals: [P1, N1, P2, N2] }
variables:
  - { name: xfmr.wp, kind: continuous_step, lower: "5.0", upper: "8.0", step: "0.1" }
  - { name: xfmr.ws, kind: continuous_step, lower: "4.0", upper: "6.5", step: "0.1" }
metrics:
  - { name: S21_100G, unit: dB, testbench: lo_tb, expression: "value(db(spm('sp 2 1)) 1e+11)" }
  - { name: Qp_60g,   unit: ratio, device: xfmr, quantity: Qp, frequency_hz: 60e9 }
  - { name: k_60g,    unit: ratio, device: xfmr, quantity: k,  frequency_hz: 60e9 }
  - { name: Lp_lf,    unit: H,     device: xfmr, quantity: Lp_lf }
constraints:
  - { metric: k_60g, op: ge, value: 0.43 ratio }
objective: { direction: maximize, expression: S21_100G }
simulator: { preset: cx, threads_per_run: 4, parallel_jobs: 4, timeout_s: 3600 }
budget: { max_simulations: 96 }
```

Circuit and device together (the joint problem: the circuit's parameters and
the transformer's geometry are variables of one search), a tapped transformer
bound into the ten-terminal nport the export carries, three corners. Circuit
variables carry no prefix, the device's are `<id>.<field>`; the device is
simulated once per point and its sNp serves every testbench and corner, so a
point costs 1 EMX run + testbenches x corners Spectre runs, and a geometry
that comes up again costs no EMX run:

```yaml
# complete: joint
project: mixer_xfmr_joint_opt
description: Mixer bias and device sizes together with its tapped input transformer's widths, across tt / ss / ff
testbenches:
  - id: cg_nf
    maestro_point_root: /home/user/simulation/Mixer/CG_NF/maestro/results/maestro/Interactive.7/1/Mixer_CG_NF
    virtuoso_library: Mixer_lib
    cell: Mixer_CG_NF
    test_name: Mixer_CG_NF
  - id: p1db
    maestro_point_root: /home/user/simulation/Mixer/P1dB/maestro/results/maestro/Interactive.4/1/Mixer_P1dB
    virtuoso_library: Mixer_lib
    cell: Mixer_P1dB
    test_name: Mixer_P1dB
corners:
  - { id: tt, model_section: Post_simu_top_tt, options: { temp: "27" } }
  - { id: ss, model_section: Post_simu_top_ss, options: { temp: "125" } }
  - { id: ff, model_section: Post_simu_top_ff, options: { temp: "-40" } }
corner_policy: { objective: worst_case, constraints: all_corners }
devices:
  - id: xfmr
    generator: clean_port_xfm_bs
    profile: demo_6m
    ports: [P1, N1, P2, N2, CTP, CTS]
    fixed:
      primary_outer_diameter_um: 73
      secondary_outer_diameter_um: 58
      primary_opening_um: 8.75
      secondary_opening_um: 7.5
      primary_lead_length_um: 20.75
      secondary_lead_length_um: 21.5
      center_spacing_um: 0
      primary_metal: "6"
      secondary_metal: "5"
      ct_primary_metal: "4"
      ct_secondary_metal: "4"
      ground_fixture: { inner_margin_um: 4, ring_width_um: 70, stub_length_um: 2, stub_chamfer_um: 0 }
em:
  process_file: /opt/pdk/demo_6m/demo.proc
  frequencies: { start_hz: 0, stop_hz: 200e9, step_hz: 1e9 }
  mode: full_wave
  accuracy: standard
  three_d_metals: [M6, M5]
  threads: 4
  memory_gb: 8
  parallel_jobs: 4
  timeout_s: 3600
  simultaneous_frequencies: 0
bindings:
  - { testbench: cg_nf, instance: NPORT0, device: xfmr, terminals: [P1, N1, CTP, null, P2, N2, CTS, null, null, null] }
  - { testbench: p1db,  instance: NPORT0, device: xfmr, terminals: [P1, N1, CTP, null, P2, N2, CTS, null, null, null] }
variables:
  - { name: WCS,   kind: continuous_step, lower: 0.6u, upper: 1.2u, step: 0.2u }
  - { name: VB_RF, kind: continuous_step, lower: 300m, upper: 440m, step: 20m }
  - { name: FCS,   kind: integer,         lower: "40", upper: "56", step: "2" }
  - { name: xfmr.primary_width_um,   kind: continuous_step, lower: "5.0", upper: "8.0", step: "0.1" }
  - { name: xfmr.secondary_width_um, kind: continuous_step, lower: "4.0", upper: "6.5", step: "0.1" }
metrics:
  - { name: NF_3G, unit: dB,    testbench: cg_nf, expression: 'value(getData("NF" ?result "pnoise") 3e+09)' }
  - { name: GAIN,  unit: dB,    testbench: cg_nf, expression: 'ymax(db(getData("gain" ?result "pac")))' }
  - name: P1dB
    unit: dBm
    testbench: p1db
    expression: or(compressionVRI((v("/IF_P" ?result "pss_fd") - v("/IF_N" ?result "pss_fd")) '1 ?rport resultParam("PORT2:r" ?result "pss_fd") ?gcomp 1) xmax(xval(harmonic((v("/IF_P" ?result "pss_fd") - v("/IF_N" ?result "pss_fd")) '1))))
  - { name: k_lf,  unit: ratio, device: xfmr, quantity: k_lf }
  - { name: SRF_p, unit: Hz,    device: xfmr, quantity: SRF_p }
constraints:
  - { metric: NF_3G, op: lt, value: 12.5 dB }
  - { metric: GAIN,  op: gt, value: -3 dB }
  - { metric: P1dB,  op: gt, value: -8.5 dBm }
  - { metric: SRF_p, op: gt, value: 150e9 Hz }
objective:
  direction: minimize
  expression: -(0.1*min(max(0,min(1,(12.5-NF_3G)/3.5)),max(0,min(1,(GAIN+3)/5)),max(0,min(1,(P1dB+8.5)/4.5)))+0.8*(0.4*max(0,min(1,(12.5-NF_3G)/3.5))+0.2*max(0,min(1,(GAIN+3)/5))+0.4*max(0,min(1,(P1dB+8.5)/4.5))))
simulator: { preset: ax, threads_per_run: 10, parallel_jobs: 10, timeout_s: 7200 }
budget: { max_simulations: 600 }
```

A device by itself (a library part, a geometry study): no testbenches, the
variables are the generator's fields:

```yaml
# complete: em_only
project: ind_sym_top_part
description: symmetric inductors on the top metal, swept for a device library
devices:
  - id: ind
    generator: clean_port_ind_sym
    profile: demo_6m
    ports: [P1, N1]
    fixed:
      opening_um: 8
      lead_length_um: 20
      metal: "6"
      ground_fixture: { inner_margin_um: 4, ring_width_um: 70, stub_length_um: 2, stub_chamfer_um: 2 }
em:
  process_file: /opt/pdk/demo_6m/demo.proc
  frequencies: { start_hz: 0, stop_hz: 150e9, step_hz: 1e9 }
  mode: full_wave
  three_d_metals: [M6]
  threads: 4
  memory_gb: 8
  timeout_s: 1800
  simultaneous_frequencies: 0
variables:
  - { name: outer_diameter_um, kind: continuous_step, lower: "60", upper: "240", step: "1" }
  - { name: width_um,          kind: continuous_step, lower: "4.0", upper: "10.0", step: "0.1" }
  - { name: spacing_um,        kind: continuous_step, lower: "2.0", upper: "4.0", step: "0.1" }
  - { name: turns,             kind: integer,         lower: "1", upper: "4", step: "1" }
metrics:
  - { name: Lp_lf,   unit: H,     device: ind, quantity: Lp_lf }
  - { name: Qp_peak, unit: ratio, device: ind, quantity: Qp_peak }
  - { name: SRF_p,   unit: Hz,    device: ind, quantity: SRF_p }
simulator: { threads_per_run: 1, parallel_jobs: 4, timeout_s: 600 }
budget: { max_simulations: 400 }
```

## 6. Prove it, then hand it over

1. `ic-opt doctor PROJECT [--ssh-profile HOST]`: every check `[ok]` (tools,
   license, `export:<tb>` per testbench, `device:<id>` per device, `emx`,
   `em:process_file`, envelope, budget). `operating points:<tb>` says whether the
   export asks for them, what ic-opt adds, or `off`; tell the user when ic-opt
   adds a DC analysis.
2. `ic-opt run <recipe> PROJECT <params> --plan [--ssh-profile HOST]`: read every
   `[plan]` line -- `netlist.import <tb>` per testbench (a `FAIL` names the
   variable or corner to fix), the block sequence, `N simulations per point`,
   `jobs x threads` on which host, the `strategy auto: <name> (<why>)` line
   when no strategy is named (`metric_gp` for a spec without EM devices at one
   condition, else `openbox_gp_eic`), how many points the strategy's model
   proposes after its initial design (a warning means none: a larger budget, a
   smaller batch or a smaller `initial_trials`), and the `current design` line
   (the design as exported, evaluated first, or why there is none).
3. Show the user the plan and the resource numbers they gave; the plan is the
   approval point (what every plan line means: `skills/ic-opt/SKILL.md`,
   Procedure step 3). No placeholder may remain: a path, a resource or a bound
   you did not get from the user or the export is a question, not a guess.

A 0.1 `opt_requirement.md` is converted, not rewritten: `ic-opt migrate OLD
NEW`, then read `NEW/MIGRATION.md` (it lists the resources it filled with 0.1
defaults for the user to confirm). Failures at the run: the "Reading failures"
section of `skills/ic-opt/SKILL.md`.
