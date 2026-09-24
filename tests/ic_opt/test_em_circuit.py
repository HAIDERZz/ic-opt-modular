"""T9.5: nport binding and the em_circuit pipeline end to end on the fake host."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ic_opt.blocks.evaluate import default_pipeline, evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.deck import Deck
from ic_opt.em import nport
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
from tests.ic_opt.test_blocks import maestro_export
from tests.ic_opt.test_em_pcell import demo_spec

pytest.importorskip("klayout.db")

NETLIST = ('simulator lang=spectre\ninclude "/pdk/models.scs" section=tt\nparameters temperature=27 F=20\n'
           'NPORT0 ( net4 0 net3 0 ) nport \\\n        file="/old/ind.s2p" interp=bbspice\n'
           'X1 ( a b ) sub\ntran tran stop=10n\n')


def test_patch_replaces_only_the_quoted_path_and_checks_the_terminal_count():
    out = nport.patch(NETLIST, instance="NPORT0", replacement="models/ind.s2p", n_ports=2)
    assert 'file="models/ind.s2p" interp=bbspice' in out.text and out.original_file == "/old/ind.s2p" and out.signal_nodes == ["net4", "net3"]
    assert out.text.count("\n") == NETLIST.count("\n") and out.text.replace('models/ind.s2p', '/old/ind.s2p') == NETLIST
    with pytest.raises(nport.NportError, match="expected 8 for 4 ports"):
        nport.patch(NETLIST, instance="NPORT0", replacement="models/ind.s4p", n_ports=4)
    with pytest.raises(nport.NportError, match="found 0"):
        nport.patch(NETLIST, instance="NPORT9", replacement="models/ind.s2p", n_ports=2)
    two = NETLIST + 'NPORT1 ( a 0 b 0 c 0 d 0 ) nport file="/old/x.s4p"\n'
    assert nport.patch(two, instance="NPORT1", replacement="models/x.s4p", n_ports=4).signal_nodes == ["a", "b", "c", "d"]


def em_circuit_spec(tmp_path: Path, *, corners=()) -> Spec:
    export = maestro_export(tmp_path / "maestro", "tb", params="temperature=27 F=20")
    (export / "netlist" / "input.scs").write_text(NETLIST)
    d = demo_spec().model_dump(mode="json")
    d["testbenches"] = [{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c", "test_name": "t"}]
    d["devices"][0]["variables"] = {"outer_diameter_um": "ind.od", "width_um": "ind.w"}
    d["variables"] = [{"name": "ind.od", "kind": "continuous_step", "lower": "80", "upper": "120", "step": "10"},
                      {"name": "ind.w", "kind": "continuous_step", "lower": "3", "upper": "6", "step": "0.5"},
                      {"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"}]
    d["em"] = {"process_file": "/site/n28.proc", "frequencies": {"start_hz": 0, "stop_hz": 200e9, "step_hz": 1e9}, "three_d_metals": ["M6", "M5"],
               "threads": 4, "memory_gb": 32, "timeout_s": 600}
    d["bindings"] = [{"testbench": "tb", "instance": "NPORT0", "device": "ind", "terminals": ["P1", "N1"]}]
    d["metrics"] = [{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"}]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9"}]
    d["objective"] = {"direction": "minimize", "expression": "NF"}
    d["corners"] = [{"id": c, "model_section": c} for c in corners]
    d["budget"] = {"max_simulations": 60}
    return Spec.model_validate(d)


def test_em_circuit_pipeline_binds_the_snp_and_runs_spectre(tmp_path):
    spec = em_circuit_spec(tmp_path, corners=("tt", "ss"))
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0 + int(p["F"]) / 100 + (1.0 if c == "ss" else 0.0)})
    deck = import_netlists(spec, ex, store)
    assert "F={{F}}" in deck.template("tb", "tt") and "ind.od" not in deck.template("tb", "tt")
    pipeline = default_pipeline(spec, deck)
    assert [s.name for s in pipeline] == ["pcell", "emx:ind", "bind_nport", "spectre", "ocean", "extract"]

    points = [Point({"ind.od": "100", "ind.w": "5", "F": "20"}, "user"), Point({"ind.od": "100", "ind.w": "5", "F": "22"}, "user")]
    obs = evaluate(spec, points, ex, store, deck=deck, parallel_jobs=1, limits=FAKE_HOST)
    assert [o.status for o in obs] == ["ok", "ok"] and set(obs[0].children) == {"tb/tt", "tb/ss"}
    assert obs[0].metrics["NF"] == pytest.approx(8.2) and obs[1].metrics["NF"] == pytest.approx(8.22)   # worst case: ss
    assert ex.emx_runs == 1 and obs[1].cache == {"emx:ind": "hit"}                   # same geometry, EMX cached across points
    steps = [json.loads(line) for line in (store.root / "steps.jsonl").read_text().splitlines()]
    assert steps[-1]["simulations"] == 5                                              # 2 points × 2 testbench sims + 1 EMX run (the hit is free)
    from ic_opt.blocks.evaluate import plan_shape
    assert plan_shape(spec, pipeline, "all", ex, 4, FAKE_HOST).startswith("(1 EMX runs + 2 testbench sims) = 3 simulations per point")
    netlist = (store.root / "sims" / "obs_0001" / "tb" / "tt" / "netlist" / "input.scs").read_text()
    assert 'file="models/ind.s2p" interp=bbspice' in netlist and "parameters temperature=27 F=20" in netlist
    assert (store.root / "sims" / "obs_0001" / "tb" / "tt" / "netlist" / "models" / "ind.s2p").read_text().startswith("! Touchstone")
    assert obs[0].children["tb/tt"].sim_dir.endswith("obs_0001/tb/tt")


def test_the_em_circuit_spectre_takes_the_license_queue_wait_from_the_spec(tmp_path):
    """R-17: the EM circuit's Spectre stage passes ``+lqtimeout`` as the plain chain does, only when the spec states it."""
    spec = em_circuit_spec(tmp_path)
    stated = Spec.model_validate({**spec.model_dump(mode="json"),
                                  "simulator": {**spec.simulator.model_dump(mode="json"), "license_queue_timeout_s": 1200}})

    def spectre_argv(s: Spec) -> list[str]:
        return next(stage for stage in default_pipeline(s, Deck()) if stage.name == "spectre").argv()

    assert "+lqtimeout" not in spectre_argv(spec)
    argv = spectre_argv(stated)
    assert argv[argv.index("+lqtimeout") + 1] == "1200"


def test_doctor_asks_an_em_circuit_host_for_spectre_and_emx(tmp_path):
    """R-18: EM devices bound into testbenches run both chains: Spectre, OCEAN and the license, and the EMX binary."""
    from ic_opt.blocks.doctor import doctor

    host = FakeSpectreExecutor(tmp_path / "sims")
    checks = {c.name: c for c in doctor(em_circuit_spec(tmp_path), host, limits=FAKE_HOST).checks}
    assert checks["tools"].ok and checks["license"].ok and checks["emx"].detail == "/cad/bin/emx"
    assert {"which spectre ocean", "spectre -V", "lmstat -a", "which emx"} <= set(host.commands)


def test_binding_failures_are_child_failures(tmp_path):
    spec = em_circuit_spec(tmp_path)
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0})
    deck = Deck(templates={("tb", None): NETLIST.replace("NPORT0", "NPORTX").replace("F=20", "F={{F}}")})
    obs = evaluate(spec, [Point({"ind.od": "100", "ind.w": "5", "F": "20"}, "user")], ex, store, deck=deck, limits=FAKE_HOST)
    assert obs[0].status == "failed:bind_nport" and "expected exactly one nport instance NPORT0" in obs[0].issues[0]


def test_two_devices_bound_into_two_testbenches(tmp_path):
    export_a = maestro_export(tmp_path / "maestro", "tb_a", params="temperature=27 F=20")
    export_b = maestro_export(tmp_path / "maestro", "tb_b", params="temperature=27 F=20")
    (export_a / "netlist" / "input.scs").write_text(NETLIST.replace("net4 0 net3 0", "p 0 n 0 s1 0 s2 0").replace("ind.s2p", "x.s4p"))
    (export_b / "netlist" / "input.scs").write_text(NETLIST + 'NPORT1 ( a 0 b 0 c 0 d 0 ) nport file="/old/x.s4p"\n')
    d = demo_spec(two_devices=True).model_dump(mode="json")
    d["testbenches"] = [{"id": "tb_a", "maestro_point_root": str(export_a), "virtuoso_library": "l", "cell": "c", "test_name": "t"},
                        {"id": "tb_b", "maestro_point_root": str(export_b), "virtuoso_library": "l", "cell": "c", "test_name": "t"}]
    d["variables"].append({"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"})
    d["em"] = {"process_file": "/site/n28.proc", "frequencies": [4e10], "three_d_metals": ["M6", "M5"], "threads": 4, "memory_gb": 32, "timeout_s": 600}
    d["bindings"] = [{"testbench": "tb_a", "instance": "NPORT0", "device": "xfm", "terminals": ["P1", "N1", "P2", "N2"]},
                     {"testbench": "tb_b", "instance": "NPORT0", "device": "ind", "terminals": ["P1", "N1"]},
                     {"testbench": "tb_b", "instance": "NPORT1", "device": "xfm", "terminals": ["P1", "N1", "P2", "N2"]}]
    d["metrics"] = [{"name": "A", "unit": "dB", "expression": "a()", "testbench": "tb_a"}, {"name": "B", "unit": "dB", "expression": "b()", "testbench": "tb_b"}]
    d["objective"] = {"direction": "minimize", "expression": "A + B"}
    spec = Spec.model_validate(d)
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"A": 1.0} if tb == "tb_a" else {"B": 2.0})
    deck = import_netlists(spec, ex, store)
    obs = evaluate(spec, [Point({"ind.outer_diameter_um": "90", "ind.width_um": "4", "xfm.primary_width_um": "6", "xfm.secondary_width_um": "5", "F": "20"}, "user")],
                   ex, store, deck=deck, limits=FAKE_HOST)
    assert obs[0].status == "ok" and obs[0].metrics == {"A": 1.0, "B": 2.0} and ex.emx_runs == 2
    tb_b = (store.root / "sims" / "obs_0001" / "tb_b" / "nominal" / "netlist")
    assert 'file="models/ind.s2p"' in (tb_b / "input.scs").read_text() and 'file="models/xfm.s4p"' in (tb_b / "input.scs").read_text()
    assert {p.name for p in (tb_b / "models").iterdir()} == {"ind.s2p", "xfm.s4p"}
