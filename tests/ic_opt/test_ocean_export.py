"""N-100: the waveform files of the replay script (a real CSV at %.16g, written from the vectors, not by ``ocnPrint``),
the extract stage reading them back, and the timing of the export.

Every name and number here is made up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ic_opt.eval.stage import StageContext
from ic_opt.executor import LocalExecutor
from ic_opt.sim import ocean
from ic_opt.sim.ocean import Scalars, WaveformExport
from ic_opt.stages.spectre_chain import Extract
from ic_opt.store import RunStore
from tests.ic_opt.fakes import make_spec, write_waveform

PATHS = {"psf_dir": "psf", "scalars_file": "metrics/ocean_scalars.tsv", "waveform_dir": "metrics/waveforms"}
VOUT = WaveformExport(name="vout", expression='getData("/out" ?result "tran")')


# -- 1. the script's export block ----------------------------------------------------------------------------------------


def test_a_waveform_is_written_from_its_vectors_at_sixteen_digits():
    script = ocean.replay_script([], [VOUT], **PATHS)
    assert "ocnPrint" not in script                     # whitespace table, six digits, slow past 10 000 points (B02, B03)
    assert "drGetWaveformXVec(w)" in script and "drGetWaveformYVec(w)" in script and "drGetElem(xv i)" in script
    assert 'fprintf(port "%.16g,%.16g\\n" drGetElem(xv i) drGetElem(yv i))' in script        # comma separated, no padding
    assert 'fprintf(port "%.16g,%.16g,%.16g\\n" float(drGetElem(xv i)) float(real(z)) float(imag(z)))' in script
    assert 'cols = list(icoptXColumn(xv) "re" "im")' in script and 'cols = list(icoptXColumn(xv) "y")' in script
    assert 'cond((equal(name "") "x") (equal(units "") name) (t strcat(name "_" units)))' in script   # time_s, freq_Hz, x
    # the export: its result selected, the expression inside errset as a metric's, the call with name, folder, and the
    # expression and result as JSON text for the meta file
    block = script[script.index("; waveform export: vout"):]
    assert block.startswith("; waveform export: vout\nselectResult('tran)\nicoptWave = nil\n")
    assert 'icoptWaveEval = measureTime(icoptWave = car(errset(getData("/out" ?result "tran") t)))' in block
    assert ('icoptExportWave("vout" icoptWave "metrics/waveforms" '
            '"\\"getData(\\\\\\"/out\\\\\\" ?result \\\\\\"tran\\\\\\")\\"" "\\"tran\\"")') in block
    assert script.index('icoptTiming = outfile("metrics/ocean_timing.tsv" "w")') < script.index("; waveform export: vout")
    assert script.index("close(icoptTiming)") < script.index("close(out)") < script.index("exit()")


def test_the_meta_file_says_columns_units_expression_result_points_and_precision():
    script = ocean.replay_script([], [VOUT], **PATHS)
    writer = script[script.index("procedure(icoptExportWave("):]
    for key in ("name", "expression", "result", "kind", "file", "columns", "units", "points", "separator", "precision"):
        assert f'\\"{key}\\": ' in writer
    assert '\\"precision\\": \\"%.16g\\"' in writer
    # the meta file is written after the CSV: one that exists names files that are complete
    single = writer[writer.index("(t\n        file = strcat(name \".csv\")"):]
    assert single.index("icoptWriteWave(value") < single.index('".meta.json"')


def test_a_family_is_one_file_per_member_with_an_index_of_the_sweep_values():
    script = ocean.replay_script([], [VOUT], **PATHS)
    writer = script[script.index("procedure(icoptExportWave("):]
    assert "(drIsParamWave(value)" in writer and 'file = sprintf(nil "%s__%d.csv" name i)' in writer
    assert 'strcat(dir "/" name ".families.json")' in writer and 'icoptJsonList(car(nth(i members)))' in writer
    assert "famGetSweepName(value d)" in writer
    family = script[script.index("procedure(icoptFamilyMembers("):]
    assert "foreach(v famGetSweepValues(f)" in family and "icoptFamilyMembers(m append(prefix list(v)))" in family  # nested


def test_nil_writes_nothing_and_a_non_waveform_says_what_it_is():
    script = ocean.replay_script([], [VOUT], **PATHS)
    writer = script[script.index("procedure(icoptExportWave("):]
    assert '(null(value) list("nil" 0))' in writer
    assert '(!drIsWaveform(value) list(sprintf(nil "not_a_waveform:%L" type(value)) 0))' in writer


def test_the_export_is_timed_apart_from_the_metrics():
    script = ocean.replay_script([], [VOUT], **PATHS)
    assert ('fprintf(icoptTiming "waveform:%s\\t%.6f\\t%s\\n" "vout" caddr(icoptWaveEval) + caddr(icoptWaveWrite) '
            "car(icoptWaveDone))") in script
    assert 'printf("ic-opt waveform export %s: %s, %d point(s), %.3f s (expression %.3f s, file %.3f s)\\n"' in script


def test_a_script_without_waveforms_has_no_writer_and_no_timing_file():
    from ic_opt.spec import Metric

    script = ocean.replay_script([Metric(name="G", unit="dB", expression="ymax(db(VF))", result="ac")], [], **PATHS)
    assert "icoptExportWave" not in script and "icoptTiming" not in script and "measureTime" not in script


def test_an_export_may_not_be_named_as_a_member_file_of_another():
    with pytest.raises(ValueError, match="member file of waveform vout"):
        ocean.replay_script([], [VOUT, WaveformExport(name="vout__0", expression="x")], **PATHS)
    ocean.replay_script([], [WaveformExport(name="vout__0", expression="x")], **PATHS)      # alone it is a name like any


# -- 2. the reader and the extract stage on CSV fixtures -----------------------------------------------------------------


def write_family(folder: Path, name: str) -> Path:
    """A two-member family as the script writes it: sweep ``k`` at 1 and 2.5, members of three and two points."""
    folder.mkdir(parents=True, exist_ok=True)
    members = [((1.0,), [(0.0, 0.5), (1e-9, 0.25), (2e-9, 0.125)]), ((2.5,), [(0.0, 1.0), (1e-9, 0.5)])]
    entries, index = [], []
    for i, (values, rows) in enumerate(members):
        file = f"{name}__{i}.csv"
        (folder / file).write_text("time_s,y\n" + "".join(f"{x:.16g},{y:.16g}\n" for x, y in rows))
        entries.append({"file": file, "columns": ["time_s", "y"], "units": ["s", "V"], "points": len(rows)})
        index.append({"index": i, "file": file, "values": list(values)})
    (folder / f"{name}.families.json").write_text(json.dumps({"name": name, "sweeps": ["k"], "members": index}))
    meta = {"name": name, "expression": "fam", "result": "tran", "kind": "family", "index": f"{name}.families.json",
            "members": entries, "separator": ",", "precision": "%.16g"}
    (folder / f"{name}.meta.json").write_text(json.dumps(meta))
    return folder / f"{name}.meta.json"


def test_the_reader_gives_real_complex_and_family_tables(tmp_path):
    t = 0.4e-12 * 750_000                           # a time that needs all sixteen digits: read back as it was written
    real = ocean.read_waveform(write_waveform(tmp_path, "vt", [(0.0, 0.0), (t, -8.3148e-06)], columns=("time_s", "y"),
                                              units=("s", "V")))
    assert real.kind == "waveform" and real.tables[0].columns == ["time_s", "y"] and real.tables[0].units == ["s", "V"]
    assert real.tables[0].rows == [(0.0, 0.0), (t, -8.3148e-06)]
    cplx = ocean.read_waveform(write_waveform(tmp_path, "h", [(2e10, 0.0151385171397347, -1e-3)],
                                              columns=("freq_Hz", "re", "im"), units=("Hz", "V", "V")))
    assert cplx.tables[0].rows == [(2e10, 0.0151385171397347, -1e-3)]
    family = ocean.read_waveform(write_family(tmp_path, "fam"))
    assert family.kind == "family" and family.sweeps == ["k"] and [tb.values for tb in family.tables] == [[1.0], [2.5]]
    assert [len(tb.rows) for tb in family.tables] == [3, 2] and family.tables[1].file.name == "fam__1.csv"


def test_a_file_that_is_not_what_its_meta_file_says_is_an_error(tmp_path):
    meta = write_waveform(tmp_path, "v", [(1.0, 2.0), (2.0, 3.0)])
    csv_path = tmp_path / "v.csv"
    csv_path.write_text("freq_Hz,y\n1,2\n")                                           # a row short
    with pytest.raises(ValueError, match="1 row"):
        ocean.read_waveform(meta)
    csv_path.write_text("freq_Hz           y\n1.00000e+00  2.00000e+00\n2.00000e+00  3.00000e+00\n")   # ocnPrint's table
    with pytest.raises(ValueError, match="header"):
        ocean.read_waveform(meta)
    csv_path.write_text("freq_Hz,y\n1,2\n2,x\n")
    with pytest.raises(ValueError, match="not a number"):
        ocean.read_waveform(meta)
    csv_path.write_text("freq_Hz,y\n1,2\n2,3,4\n")
    with pytest.raises(ValueError, match="3 field"):
        ocean.read_waveform(meta)
    (tmp_path / "fam.meta.json").write_text("{not json")
    with pytest.raises(ValueError, match="fam.meta.json"):
        ocean.read_waveform(tmp_path / "fam.meta.json")


def extract(tmp_path: Path, waveforms: dict[str, Path | None], outcomes: dict[str, str] | None = None):
    ctx = StageContext(spec=make_spec(), executor=LocalExecutor(tmp_path), store=RunStore(tmp_path), obs_id="obs_0001",
                       workdir=tmp_path, remote_dir=str(tmp_path), unit="tb")
    rows = {"NF": ocean.ScalarRow(8.0, "dB", "pass")}
    return Extract().run(Scalars(rows, waveforms, 1, outcomes=outcomes or {}), ctx)


def test_the_extract_stage_reads_real_complex_and_family_exports(tmp_path):
    waves = tmp_path / "waveforms"
    child = extract(tmp_path, {
        "vt": write_waveform(waves, "vt", [(0.0, 0.1), (1e-12, 0.2)], columns=("time_s", "y"), units=("s", "V")),
        "h": write_waveform(waves, "h", [(1e9, 0.5, -0.5)], columns=("freq_Hz", "re", "im"), units=("Hz", "V", "V")),
        "fam": write_family(waves, "fam"),
    })
    assert child.status == "ok" and child.issues == [] and child.metrics == {"NF": 8.0}


def test_the_extract_stage_names_a_nil_a_non_waveform_and_an_unreadable_file(tmp_path):
    waves = tmp_path / "waveforms"
    broken = write_waveform(waves, "broken", [(1.0, 2.0)])
    (waves / "broken.csv").write_text("1.00000e+00  2.00000e+00\n")
    child = extract(tmp_path, {"gone": None, "scalar": None, "broken": broken, "old": None},
                    {"waveform:gone": "nil", "waveform:scalar": "not_a_waveform:flonum"})
    assert child.status == "metric_failed" and child.metrics == {"NF": 8.0}
    assert child.issues[0] == "waveform gone returned nil"                       # as before N-100
    assert child.issues[1] == "waveform scalar not written: not_a_waveform:flonum"
    assert child.issues[2].startswith("waveform broken unreadable: broken.csv: header")
    assert child.issues[3] == "waveform old returned nil"            # no timing row (an older script): nil, as before


# -- 3. the timing in the OCEAN stage's trace ----------------------------------------------------------------------------


def test_the_timing_file_is_read_and_a_bad_row_is_left_out(tmp_path):
    path = tmp_path / "ocean_timing.tsv"
    assert ocean.parse_timing(path) == []
    path.write_text("waveform:v\t0.155\twritten\ngarbage\noppoints\t17.6\tread\n")
    assert ocean.parse_timing(path) == [ocean.TimingRow("waveform:v", 0.155, "written"),
                                        ocean.TimingRow("oppoints", 17.6, "read")]


def test_the_ocean_stage_puts_each_export_in_the_trace(tmp_path):
    from ic_opt.space import Point
    from ic_opt.stages.spectre_chain import Ocean, RawSim
    from tests.ic_opt.fakes import FakeSpectreExecutor

    spec = make_spec()
    work = tmp_path / "sims" / "obs_0001" / "tb" / "nominal"
    (work / "netlist").mkdir(parents=True)
    (work / "netlist" / "input.scs").write_text("parameters F=22 W=0.8u\n")
    executor = FakeSpectreExecutor(tmp_path / "remote", lambda p, tb, c: {"NF": 8.0}, nil_waveforms={"gone"})
    ctx = StageContext(spec=spec, executor=executor, store=RunStore(tmp_path), obs_id="obs_0001", workdir=work,
                       remote_dir=str(work), unit="tb", point=Point({"F": "22", "W": "0.8u"}, "user"))
    stage = Ocean(timeout_s=60, waveforms=[VOUT, WaveformExport(name="gone", expression="nil")])
    scalars = stage.run(RawSim(psf_dir=str(work / "psf"), returncode=0), ctx)
    labels = [r["label"] for r in ctx.trace]
    assert labels == ["ocean#1", "ocean:waveform:vout", "ocean:waveform:gone"]
    assert ctx.trace[1]["outcome"] == "written" and ctx.trace[2]["outcome"] == "nil"
    assert scalars.waveforms == {"vout": work / "metrics" / "waveforms" / "vout.meta.json", "gone": None}
    child = Extract().run(scalars, ctx)
    assert child.issues == ["waveform gone returned nil"] and child.metrics == {"NF": 8.0}
