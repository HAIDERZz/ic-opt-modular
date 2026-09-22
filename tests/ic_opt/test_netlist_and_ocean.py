from pathlib import Path

import pytest

from ic_opt.sim import netlist, ocean
from ic_opt.spec import Corner, Metric


def test_template_deck_single_line_and_instance_params_untouched():
    deck = (
        "simulator lang=spectre\n"
        "parameters temperature=27 F=4 W=0.6u\n"
        "subckt wrapped IN OUT VDD VSS\n"
        "M0 (OUT IN VSS VSS) nmos w=W*F l=45n\n"
        "ends wrapped\n"
        "X0 (IN OUT VDD VSS) wrapped F=99 W=99u\n"
        "ac ac start=1 stop=10G\n"
    )
    out = netlist.template_deck(deck, ["F", "W"])
    assert "parameters temperature=27 F={{F}} W={{W}}" in out
    assert "X0 (IN OUT VDD VSS) wrapped F=99 W=99u" in out
    assert "w=W*F" in out


def test_template_deck_handles_backslash_continuation_and_whitespace_units():
    deck = "parameters \\\n    temperature=27 \\\n    L2=45n F=4 \\\n    W=0.6 u\nI0 (A B) inv w=W*F\n"
    out = netlist.template_deck(deck, ["F", "W"])
    assert out.count("\n") == deck.count("\n")
    assert "L2=45n F={{F}} \\" in out and "W={{W}}\n" in out


def test_template_deck_fails_closed():
    with pytest.raises(ValueError, match="not found"):
        netlist.template_deck("parameters W=0.6u\n", ["F", "W"])
    with pytest.raises(ValueError, match="inside a subckt"):
        netlist.template_deck("parameters W=0.6u\nsubckt s A\nparameters F=4\nends s\n", ["F", "W"])
    with pytest.raises(ValueError, match="more than once"):
        netlist.template_deck("parameters F=4 F=5\n", ["F"])


def test_apply_corner_and_render():
    template = 'include "/p/top.scs" section=Post_simu_top_tt\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n'
    ss = netlist.apply_corner(template, Corner(id="ss", model_section="Post_simu_top_ss", variables={"temperature": "125"}))
    assert "section=Post_simu_top_ss" in ss and "temperature=125" in ss and "F={{F}}" in ss
    rendered = netlist.render(ss, {"F": "24", "W": "0.8u"})
    assert "parameters temperature=125 F=24 W=0.8u" in rendered
    with pytest.raises(ValueError, match="placeholders"):
        netlist.render(ss, {"F": "24"})
    with pytest.raises(ValueError, match="no 'include"):
        netlist.apply_corner("parameters F={{F}}\n", Corner(id="x", model_section="s"))
    with pytest.raises(ValueError, match="not found in top-level"):
        netlist.apply_corner(template, Corner(id="x", variables={"vdd": "1"}))


def test_ocean_script_and_scalar_parsing(tmp_path: Path):
    metrics = [
        Metric(name="NF", unit="dB", expression='value(getData("NF" ?result "pnoise") 3e9)', testbench="tb"),
        Metric(name="GAIN", unit="dB", expression="ymax(db(VF))", result="pac", testbench="tb"),
    ]
    waves = [ocean.WaveformExport(name="nf_curve", expression='getData("NF" ?result "pnoise")')]
    script = ocean.replay_script(metrics, waves, psf_dir="psf", scalars_file="metrics/s.tsv", waveform_dir="metrics/waveforms")
    assert 'openResults("psf")' in script and "selectResult('pac)" in script
    assert 'icoptResult = errset(value(getData("NF" ?result "pnoise") 3e9) t)' in script
    assert "selectResult('pnoise)" in script and 'outfile("metrics/waveforms/nf_curve.csv" "w")' in script
    assert script.rstrip().endswith("exit()")
    with pytest.raises(ValueError, match="outfile"):
        ocean.WaveformExport(name="bad", expression='outfile("x")')

    tsv = tmp_path / "s.tsv"
    tsv.write_text("metric\tvalue\tunit\tstatus\tmessage\nNF\t8.5\tdB\tpass\t\nGAIN\t\tdB\tfail\tnon_scalar\nX\tinf\tdB\tpass\t\n")
    rows = ocean.parse_scalars(tsv)
    assert rows["NF"].value == 8.5 and rows["GAIN"].status == "fail" and rows["GAIN"].message == "non_scalar"
    assert rows["X"].value is None and rows["X"].message == "non_finite"
