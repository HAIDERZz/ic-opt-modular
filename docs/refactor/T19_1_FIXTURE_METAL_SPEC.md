# T19.1 — the ground fixture's metal: a thick metal the device does not draw on (opt-in), not always the bottom metal

Status: specification (2026-10-02), for the coding subagent. Decision behind it: the user's instruction of 2026-10-02
("地环口径是对的，换成 M6 或更高层的 GND 环") after the Q diagnosis in `ic-opt-accept/t18_qdiag/RESULT_CN.md`: the
ground fixture (an M1 ring, one 2 µm stub per port, each port referenced to its stub) adds the ring's and stubs' own
resistance to every measurement. On the 1P10M stack M1 to M6 are 0.09 µm thin: a 50 µm transformer measured with the
ring on M1, M3 or M6 has Qp 14.6, on M8 16.3, on M9 20.2 -- the same as with no fixture at all (20.1, ports against
the global ground), while a fixture on a thick metal keeps the local reference the user wants (it matches HFSS). EMX
refuses a port whose reference stub lies on the port lead's own metal (`ports "G05" and "CTP" are not allowed to be
on the same edge`), so the fixture metal must be one no port lead uses.

Read before writing: `src/ic_opt/em/pcell/fixture.py` (`GroundFixtureConfig`, `add_ground_fixture`, `_body_bbox_um`),
`generator_plugin.py` (`CleanPortGroundFixtureConfig`, `_build_fixture`, `_write_geometry_outputs_in_stack`, the
manifest), `drc_audit.py` (`fixture_exemptions`, `product_scope_record`), `stages/em_chain.py` (`_audit`),
`footprint.py` (how the fixture is told from the device), `pgs.py`, `process_rules.py` (`fixture_conductor`,
`metal_stack`), `stack.py`, `docs/em/devices.md` (the ground_fixture section), the pcell golden / byte-replay tests
under `tests/ic_opt/pcell/`.

## 1. What changes

`ground_fixture` gains one field, `metal`:

```yaml
ground_fixture: {inner_margin_um: 15, ring_width_um: 50, stub_length_um: 2, stub_chamfer_um: 0, metal: auto}
```

- `metal` absent or `null`: exactly today's geometry, byte for byte -- the ring and stubs on the profile's fixture
  conductor (the bottom metal; `layer_catalog.ground_fixture_conductor`), reference mode included. Every existing
  config, golden GDS and library replay is unchanged; `GEOMETRY_VERSION` does not change.
- `metal: auto`: the ring, the stubs and their `G<nn>` pin labels go on the **highest metal of the profile's stack on
  which the device draws nothing** -- no winding, bridge, crossunder, tap stack, lead or port. (On 1P10M+AP: AP for a
  device on M10 / M9 with M8 taps; M9 for a device on AP / M10.) Reference mode (no profile) has no stack to choose
  from: `auto` is refused there with a message.
- `metal: <name or position>` (the spellings `_metal_index` takes: `"AP"`, `"9"`, `"M9"`): that metal. Refused,
  before anything is drawn, with a `PortError` naming the port, when the device draws anything on it -- a port lead on
  it is what EMX refuses, and any device shape on it would make the fixture indistinguishable from the device for the
  footprint (point 4).

`add_ground_fixture(cell, fixture, process)` takes the metal from the config (a `metal` field on the dataclass
`GroundFixtureConfig`, position in the stack, None = the fixture conductor) and draws the ring and stubs on it, the
pins on that metal's pin layer; the port references stay `G<nn>`. The manifest (`geometry_manifest.json`) records
`fixture_metal` (the conductor's name; also the GDS layer) next to the fixture's other dimensions, for the DRC gate,
the footprint and the reader.

## 2. The DRC gate

`drc_audit.fixture_exemptions(profile, conductor=None)`: `max_width` is exempted on the conductor the fixture was
drawn on (from the generator's result / manifest), the profile's fixture conductor when none is given (today's
behaviour). `stages/em_chain._audit` and every other caller of `fixture_exemptions` pass the fixture metal of the
build. Every other rule on that metal and every rule on the other metals still counts. `expected_conductors` does not
list the fixture metal (it is not the device's).

## 3. PGS

`pgs` (the patterned ground shield tied to the ring) stays on the bottom metal: a config with `pgs` and a fixture
metal other than the fixture conductor is refused with a message (the shield's strips tie to the ring).

## 4. The footprint

`footprint.footprint` tells the fixture from the device by its layer. With the fixture on another metal it reads the
metal from the manifest beside the GDS (`fixture_metal`) when the manifest is there, and keeps today's rule (the
profile's fixture conductor) when it is not (older rows). The layer is excluded whole -- allowed because point 1
guarantees the device draws nothing on it.

## 5. Where the metal is chosen

One place: a function in `fixture.py` (say `fixture_metal(cell, config, process) -> int`) that resolves `None`,
`"auto"` and an explicit spelling against the cell's drawn layers and the stack, and refuses as point 1 says; every
built-in family and `_build_fixture` go through it. A plugin generator that calls `add_ground_fixture` gets the same
behaviour.

## 6. Documents

`docs/em/devices.md`, the ground_fixture section: the field, the rule, the EMX reason, the numbers above in one
sentence, and that the library's measurement convention is the fixture's metal (a table built with `metal: auto` is
not comparable row for row with one built on M1). `skills/author-spec/SKILL.md` where `ground_fixture` is written.
`README.md` only if it shows a ground_fixture block.

## 7. Tests

1. Default unchanged: the existing golden GDS and byte-replay tests pass untouched; a new test builds one device of each
   family with `metal` absent and with `metal: null` and compares the GDS byte for byte.
2. `auto` on the demo profile: a two-metal-winding device gets the highest metal it does not draw on; its manifest says
   so; the pins are on that metal's pin layer; the port lines are unchanged in form.
3. An explicit metal that carries a port lead, or any device shape, is refused before the GDS is written, naming the
   port or the shape's layer.
4. `auto` in reference mode is refused.
5. The DRC gate on a build with the fixture on another metal: `max_width` exempted on that metal only; a real
   violation on it (a test shape) still fails.
6. `pgs` with a non-default fixture metal is refused.
7. The footprint of a build with the fixture on another metal equals the footprint of the same device with the
   default fixture.

Targeted run: the new tests, `tests/ic_opt/pcell/` (the whole pcell suite, with and without the private profile dir),
`tests/ic_opt/test_em_pcell.py`, `test_em_circuit.py`, `test_library_footprint.py`; then `ruff check src tests`.

## 8. Working rules for the coder

- Branch `t19-1-fixture-metal` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t19-1-fixture-metal -b t19-1-fixture-metal main`);
  commits on that branch only; never touch `main`, never push.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`;
  never `uv run`, `uv sync`, `pip`. No simulator, no network. The private profile dir may be used read-only for the
  pcell suite's private half: `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`;
  nothing of it goes into the repository (no layer numbers, metal names of a real process, rule values, paths).
- Tests count as load on a machine that runs the user's simulations: `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
  MKL_NUM_THREADS=1`, at most two test processes at a time, never `-n`.
- One commit per coherent piece; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); what the specification left open
  and how it was read (a numbered list); what could not be done and why.
