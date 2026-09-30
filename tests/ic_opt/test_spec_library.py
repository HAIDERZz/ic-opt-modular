"""T18.2B section 1: a device of a circuit spec may come from a library table (``devices[*].library``). The forms the spec
takes and refuses, the identity (``library.root`` is where the library sits, not which problem; every existing spec keeps
its dump and both fingerprints) and ``space.si_value``, which reads the electrical variables' Spectre scale suffixes."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
import yaml

from ic_opt import space
from ic_opt.spec import Spec
from tests.ic_opt.fakes import minimal_spec


def library_device(**library) -> dict:
    return {"id": "xfmr", "ports": ["P1", "N1", "P2", "N2"], "variables": {"Lp": "xfmr.Lp", "Ls": "xfmr.Ls", "k": "xfmr.k"},
            "library": {"root": "/path/to/library", "stratum": "xfm_demo", "frequency_hz": 20e9, **library}}


def library_spec(device: dict | None = None, **overrides) -> dict:
    """A circuit spec whose transformer comes from a library table: a testbench, the device, its three electrical
    variables beside the circuit's F, the binding and a device metric."""
    d = minimal_spec(variables=[{"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"},
                                {"name": "xfmr.Lp", "kind": "continuous_step", "lower": "150p", "upper": "900p", "step": "50p"},
                                {"name": "xfmr.Ls", "kind": "continuous_step", "lower": "150p", "upper": "900p", "step": "50p"},
                                {"name": "xfmr.k", "kind": "continuous_step", "lower": "0.3", "upper": "0.8", "step": "0.1"}])
    d["devices"] = [device or library_device()]
    d["bindings"] = [{"testbench": "tb", "instance": "NPORT0", "device": "xfmr", "terminals": ["P1", "N1", "P2", "N2"]}]
    d["metrics"] = d["metrics"] + [{"name": "Qp", "unit": "1", "device": "xfmr", "quantity": "Qp", "frequency_hz": 20e9}]
    d.update(overrides)
    return d


def test_a_library_device_takes_its_variables_as_electrical_values_on_ordinary_grids():
    spec = Spec.model_validate(library_spec())
    (device,) = spec.devices
    assert spec.library_devices == [device] and device.library.stratum == "xfm_demo" and device.library.frequency_hz == 20e9
    assert device.generator is None and device.profile is None and device.topology is None     # the table's, not defaulted
    assert spec.device_fields(device) == {"Lp": "xfmr.Lp", "Ls": "xfmr.Ls", "k": "xfmr.k"}
    assert spec.circuit_variables == ["F"]                          # consumed by the device: they never reach a netlist
    stated = Spec.model_validate(library_spec(library_device() | {"topology": {"drives": [["P1", "N1"], ["P2", "N2"]]}}))
    assert stated.devices[0].topology.drives == [("P1", "N1"), ("P2", "N2")]
    with pytest.raises(ValueError, match="topology must use each port exactly once"):
        Spec.model_validate(library_spec(library_device() | {"topology": {"drives": [["P1", "N1"]]}}))
    home = Spec.model_validate(library_spec(library_device(root="~/libraries/demo", srf_margin=1.5, prefer="min:area")))
    assert home.devices[0].library.srf_margin == 1.5 and home.devices[0].library.prefer == "min:area"
    default_plugin = library_device() | {"plugin": "builtin:clean_port"}           # the default, stated: nothing to refuse
    assert Spec.model_validate(library_spec(default_plugin)).devices[0].plugin == "builtin:clean_port"
    assert Spec.model_validate(yaml.safe_load(yaml.safe_dump(spec.model_dump(mode="json")))) == spec   # the dump loads back


@pytest.mark.parametrize(("change", "message"), [
    ({"generator": "clean_port_xfm_bs"}, r"generator, profile, fixed fields and plugin are the table's -- leave out generator \["),
    ({"generator": "clean_port_xfm_bs", "profile": "demo_6m"}, "leave out generator, profile"),
    ({"fixed": {"primary_metal": "6"}}, "the table's -- leave out fixed"),
    ({"plugin": "/opt/plugins/mine.py"}, "the table's -- leave out plugin"),
    ({"variables": {}}, r"give its variables, index column -> spec variable"),
])
def test_a_library_device_leaves_what_is_the_table_s_to_the_table(change, message):
    with pytest.raises(ValueError, match=message):
        Spec.model_validate(library_spec(library_device() | change))


def test_a_device_is_drawn_or_taken_from_a_library_and_the_spec_does_not_mix_them():
    drawn = {"id": "xfmr", "ports": ["P1", "N1", "P2", "N2"], "variables": {"primary_width_um": "xfmr.Lp"}}
    with pytest.raises(ValueError, match=r"device xfmr: generator and profile missing -- give generator and profile .* or "
                                         r"library \(a device taken from a library table's rows\)"):
        Spec.model_validate(library_spec(drawn))
    with pytest.raises(ValueError, match="device xfmr: profile missing"):
        Spec.model_validate(library_spec(drawn | {"generator": "clean_port_xfm_bs"}))
    d = library_spec()
    d["devices"].append({"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"],
                         "variables": {"width_um": "F"}})
    with pytest.raises(ValueError, match=r"all come from a library table or none does: xfmr from a library, ind drawn"):
        Spec.model_validate(d)


def test_the_electrical_variables_are_read_with_spectre_s_scale_suffixes_and_each_is_one_column_s():
    d = library_spec()
    d["variables"][1] = {"name": "xfmr.Lp", "kind": "continuous_step", "lower": "150pH", "upper": "900pH", "step": "50pH"}
    with pytest.raises(ValueError, match=r"device xfmr: variable xfmr.Lp lower: '150pH': 'pH' is not a Spectre scale suffix"):
        Spec.model_validate(d)
    shared = library_device()
    shared["variables"] = {"Lp": "xfmr.Lp", "Ls": "xfmr.Lp", "k": "xfmr.k"}
    with pytest.raises(ValueError, match=r"maps Ls to xfmr.Lp, which device xfmr \(Lp\) maps already"):
        Spec.model_validate(library_spec(shared))
    unknown = library_device()
    unknown["variables"] = {"Lp": "xfmr.Lp", "Ls": "xfmr.Ls", "k": "xfmr.kk"}
    with pytest.raises(ValueError, match="device xfmr maps k to unknown variable xfmr.kk"):
        Spec.model_validate(library_spec(unknown))


@pytest.mark.parametrize(("library", "message"), [
    ({"root": "libraries/demo"}, "must be an absolute path"),
    ({"prefer": "best:Qmin"}, "expected max:<column> or min:<column>"),
    ({"srf_margin": 0.9}, "greater than or equal to 1"),
    ({"frequency_hz": 0}, "greater than 0"),
    ({"frequency_hz": float("inf")}, "finite number"),
])
def test_the_library_source_is_checked_where_it_is_written(library, message):
    with pytest.raises(ValueError, match=message):
        Spec.model_validate(library_spec(library_device(**library)))


def test_the_library_root_is_where_the_library_sits_not_which_problem():
    base = Spec.model_validate(library_spec())
    moved = Spec.model_validate(library_spec(library_device(root="/mnt/elsewhere/library")))
    assert moved.fingerprint() == base.fingerprint() and moved.model_dump(mode="json") != base.model_dump(mode="json")
    assert base.problem()["devices"][0]["library"] == {"stratum": "xfm_demo", "frequency_hz": 20e9}    # unset: left out too
    assert base.model_dump(mode="json")["devices"][0]["library"]["root"] == "/path/to/library"
    for what in ({"stratum": "xfm_other"}, {"frequency_hz": 28e9}, {"srf_margin": 1.5}, {"prefer": "max:Qp"}):
        assert Spec.model_validate(library_spec(library_device(**what))).fingerprint() != base.fingerprint(), what


# The complete em_circuit example of skills/author-spec/SKILL.md as the tree before T18.2B stood (f303222): a spec with an
# EMX device keeps its dump and both fingerprints, pinned from that tree.
EM_CIRCUIT = yaml.safe_load("""
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
""")


def test_a_spec_with_an_emx_device_keeps_its_dump_and_both_fingerprints():
    spec = Spec.model_validate(copy.deepcopy(EM_CIRCUIT))
    dump = spec.model_dump(mode="json")
    assert "library" not in dump["devices"][0] and "library" not in spec.problem()["devices"][0]
    assert hashlib.sha256(json.dumps(dump, sort_keys=True).encode()).hexdigest()[:16] == "1335639b59eb80c7"
    assert (spec.fingerprint(), spec._legacy_fingerprint()) == ("a4b7fbb601779b09", "cf908caff3b143ed")
    assert spec.devices[0].topology.drives == [("P1", "N1"), ("N2", "P2")]      # defaulted at load, as before


@pytest.mark.parametrize(("text", "value"), [("150p", 1.5e-10), ("0.15n", 1.5e-10), ("28G", 2.8e10), ("2T", 2e12),
                                             ("1M", 1e6), ("1m", 1e-3), ("5k", 5e3), ("40u", 4e-5), ("3f", 3e-15),
                                             ("7a", 7e-18), ("0.3", 0.3), ("1e-10", 1e-10), ("-2.5n", -2.5e-9), (" 10p ", 1e-11)])
def test_si_value_reads_a_number_as_spectre_does(text, value):
    assert space.si_value(text) == value


@pytest.mark.parametrize("text", ["150pH", "1meg", "1K", "2x", "u", "p150", ""])
def test_si_value_refuses_anything_else(text):
    with pytest.raises(ValueError):
        space.si_value(text)
