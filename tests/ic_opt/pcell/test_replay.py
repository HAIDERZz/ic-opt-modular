"""M0.3: replaying recorded sweep points through the current generators."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import replay
from tests.ic_opt.pcell.test_golden import build


def record(root: Path, stratum: str, point: str, case: str) -> Path:
    """A recorded point in the sweep layout: the generator's own outputs (manifest + GDS) under <stratum>/<point>/."""
    point_dir = root / stratum / point
    gds = build(case, point_dir)
    assert (point_dir / "geometry_manifest.json").exists()
    return gds


def test_replay_classifies_equal_different_and_refused(tmp_path):
    root = tmp_path / "sweep"
    record(root, "ind_sym", "p001", "ind_sym_nt1")
    stale = record(root, "ind_sym", "p002", "ind_sym_nt3")
    record(root, "xfm_bs", "p001", "xfm_bs")
    # p002's recorded GDS came from an older generation: swap it for a different golden's drawing
    stale.write_bytes((Path(__file__).parent / "golden" / "ind_sym_nt1.gds").read_bytes())
    # p003's configuration is one the current generator refuses (a two-port order with a tap)
    manifest_path = root / "xfm_bs" / "p001" / "geometry_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    refused_dir = root / "xfm_bs" / "p002"
    refused_dir.mkdir()
    manifest["geometry"]["config"]["primary_width_um"] = -1.0
    (refused_dir / "geometry_manifest.json").write_text(json.dumps(manifest))
    (refused_dir / "x.gds").write_bytes((root / "xfm_bs" / "p001" / "xfm_bs.gds").read_bytes())

    results = replay.replay(root)
    by_point = {(r.stratum, r.point): r for r in results}
    assert by_point[("ind_sym", "p001")].status == "equal" and by_point[("xfm_bs", "p001")].status == "equal"
    assert by_point[("ind_sym", "p002")].status == "different" and "66/0" in by_point[("ind_sym", "p002")].detail
    assert by_point[("xfm_bs", "p002")].status == "refused" and "ValidationError" in by_point[("xfm_bs", "p002")].detail
    assert replay.summary(results) == {"ind_sym": {"equal": 1, "different": 1, "refused": 0}, "xfm_bs": {"equal": 1, "different": 0, "refused": 1}}
    assert replay.refusal_reasons(results)[0][1] == 1 and "#" in replay.refusal_reasons(results)[0][0]
    assert len(replay.points(root, sample=1)) == 2


def test_cli_reports_and_exits_nonzero_on_drift(tmp_path, capsys):
    root = tmp_path / "sweep"
    record(root, "ind_sym", "p001", "ind_sym_nt1")
    assert replay.main([str(root), "--json", str(tmp_path / "out.json")]) == 0
    assert "ind_sym" in capsys.readouterr().out and json.loads((tmp_path / "out.json").read_text())[0]["status"] == "equal"
    stale = record(root, "ind_sym", "p002", "ind_sym_nt3")
    stale.write_bytes((Path(__file__).parent / "golden" / "ind_sym_nt1.gds").read_bytes())
    assert replay.main([str(root)]) == 1


SWEEPS = Path(os.environ.get("IC_OPT_EM_SWEEPS", "/nonexistent"))


@pytest.mark.skipif(not SWEEPS.exists(), reason="set IC_OPT_EM_SWEEPS to em-opt's recorded sweep root")
def test_recorded_library_replay_is_reported():
    """Not a gate: the recorded library spans older generations. Prints the drift table for the commit message."""
    results = replay.replay(SWEEPS, sample=10)
    for stratum, counts in replay.summary(results).items():
        print(stratum, counts)
    assert results
