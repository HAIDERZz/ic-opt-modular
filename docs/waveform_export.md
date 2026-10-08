# Waveform export

`fix_run` (and any recipe that passes `waveforms=` to `sim.evaluate`) asks OCEAN for
waveforms besides the metrics. A waveform export is `{name, expression, testbench?}`
(`waveforms.json`); there is nothing else to set: the format below is the contract.

The files land in the child's `metrics/waveforms/` directory
(`.icopt/sims/<obs>/<testbench>/<corner>/metrics/waveforms/`).

## One waveform

`<name>.csv` is a real CSV: comma separated, no padding, one header row, then one
row per sample, every value written with `%.16g` (sixteen significant digits).

| the expression gives | columns |
|---|---|
| a real waveform | `x,y` |
| a complex waveform (AC, S-parameters, ...) | `x,re,im` |

The first column is named after the x quantity and its unit as OCEAN reports
them, `<name>_<unit>`: `time_s` for a transient, `freq_Hz` for an AC or noise
analysis; the quantity alone when OCEAN gives no unit, `x` when it gives no name.
Characters other than letters, digits and `_` become `_`.

```text
time_s,y
0,0
4e-13,0.001234567890123457
8e-13,0.002469135780246914
...
```

`<name>.meta.json`, beside it and written after it:

```json
{"name": "vout", "expression": "getData(\"/out\" ?result \"tran\")", "result": "tran",
 "kind": "waveform", "file": "vout.csv", "columns": ["time_s", "y"], "units": ["s", "V"],
 "points": 102401, "separator": ",", "precision": "%.16g"}
```

`result` is the result selected before the expression (the `?result "..."` it
names), `null` when it names none; `units` are OCEAN's, one per column (`""` where
OCEAN gives none); `points` is the number of rows after the header.

## A family or a sweep

An expression that gives a waveform family (a parametric sweep, a corner sweep,
`famCreateFamily`) is written one file per member, `<name>__<i>.csv`, each as
above, `i` from 0 in the order of the sweep values (nested sweeps flattened,
outermost first). `<name>.families.json` names the sweep values of each member:

```json
{"name": "vout", "sweeps": ["VDD"],
 "members": [{"index": 0, "file": "vout__0.csv", "values": [0.9]},
             {"index": 1, "file": "vout__1.csv", "values": [1.0]}]}
```

and `<name>.meta.json` has `"kind": "family"`, `"index": "<name>.families.json"`
and, in place of `file` / `columns` / `units` / `points`, a `members` list holding
them for each member file. An export may not be named like a member file of
another export (`vout__0` beside `vout`): the script is refused.

## Nothing written

| the expression | files | the child |
|---|---|---|
| returned nil (or failed) | none | `metric_failed`, issue `waveform <name> returned nil` |
| gave something that is not a waveform (a number, a string) | none | `metric_failed`, issue `waveform <name> not written: not_a_waveform:<SKILL type>` |
| gave a waveform whose x is not a number or whose y is neither a number nor complex | none | `metric_failed`, issue `waveform <name> not written: unsupported_x:<type>` / `unsupported_y:<type>` |

The other metrics of the child stand, as with an expression that returned nil.
The extract stage reads every file back against its meta file (`csv.reader`, the
header, the field count of every row, numbers only, the point count, a family's
index): a file that does not match makes the child `metric_failed` with
`waveform <name> unreadable: <file>: <what>`.

Reading one in Python:

```python
from ic_opt.sim.ocean import read_waveform

wave = read_waveform(sim_dir / "metrics/waveforms/vout.meta.json")
for table in wave.tables:              # one for a waveform, one per member for a family
    table.columns, table.units, table.values, table.rows    # rows: tuples of floats
```

## How it is written, and how long it takes

The replay script writes the vectors directly (`drGetWaveformXVec` /
`drGetWaveformYVec`, `drGetElem`, `fprintf` with `%.16g`, a complex value through
`real()` / `imag()`); it does not use `ocnPrint`, whose output is a whitespace
table of six significant digits and which slows down past 10 000 points (its own
warning PRINT-1048).

Each export is timed inside OCEAN (wall clock, `measureTime`), apart from the
metrics:

- a line in the child's `metrics/ocean.log`:
  `ic-opt waveform export vout: written, 102401 point(s), 0.155 s (expression 0.099 s, file 0.056 s)`;
- a row in `metrics/ocean_timing.tsv` (`part`, `seconds`, `outcome`, tab separated,
  no header): `waveform:vout	0.154901	written`.
