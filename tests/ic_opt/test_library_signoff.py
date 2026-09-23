"""T13.6: lib_signoff with a fake EMX that answers with the synthetic library's own physics (so measured = truth)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ic_opt.recipe import PLAN_MODE, Run, load_recipe
from ic_opt.site import Site
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor
from tests.ic_opt.library_fixtures import build_library, rlc_touchstone, truth
from tests.ic_opt.test_library import part_spec

pytest.importorskip("klayout.db")


def library_physics(argv, n_ports, z0, cwd):
    """The synthetic library's RLC for the built geometry, with the header EMX writes (the emx stage validates it)."""
    cfg = json.loads((Path(cwd) / "geometry_manifest.json").read_text())["geometry"]["config"]
    body = rlc_touchstone(cfg["outer_diameter_um"], cfg["width_um"], cfg["spacing_um"], int(cfg["turns"]))
    return "! Touchstone simulation data from EMX version 2024.1.0 (fake)\n! EMX was run on fake as:\n! " + " ".join(argv[:3]) + "\n" + body


def make_run(tmp_path: Path, name: str) -> tuple[Run, FakeSpectreExecutor]:
    project = tmp_path / name
    project.mkdir()
    spec = part_spec(name, 60)
    (project / "spec.yaml").write_text(yaml.safe_dump(spec.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    ex = FakeSpectreExecutor(store.root / "sims", snp_fn=library_physics)
    return Run(project, spec, store, ex, None, Site(max_threads=16, max_memory_gb=64)), ex


def test_lib_signoff_compares_real_measurements_with_predictions_and_adopts(tmp_path):
    lib_root = build_library(tmp_path / "lib")
    wanted = [{"outer_diameter_um": 155, "width_um": 4.5, "spacing_um": 2.5, "turns": 2}, {"outer_diameter_um": 135, "width_um": 5.5, "spacing_um": 2, "turns": 1}]
    (tmp_path / "cand.json").write_text(json.dumps({"candidates": [{"params": p} for p in wanted]}), encoding="utf-8")
    run, ex = make_run(tmp_path, "signoff")
    main = load_recipe("lib_signoff")
    token = PLAN_MODE.set(True)
    try:
        main(run, library=str(lib_root), candidates=str(tmp_path / "cand.json"), stratum="ind_demo")
    finally:
        PLAN_MODE.reset(token)
    assert ex.emx_runs == 0 and run.store.observations() == []                  # --plan runs nothing
    before = len((lib_root / "nt2" / ".icopt" / "observations.jsonl").read_text().splitlines())
    main(run, library=str(lib_root), candidates=str(tmp_path / "cand.json"), stratum="ind_demo", adopt=True)
    assert ex.emx_runs == 2
    report = json.loads((run.store.root / "reports" / "lib_signoff.json").read_text())
    assert report["ok"] == 2 and {p["part"] for p in report["points"]} == {"nt2", "nt1"}       # each turns level uses its own part's spec
    flags = []
    for point in report["points"]:
        g = {k: float(v) for k, v in point["params"].items()}
        lp = point["quantities"]["Lp_lf"]
        assert lp["measured"] == pytest.approx(truth(g["outer_diameter_um"], g["width_um"], g["spacing_um"], int(g["turns"]))["Lp_lf"], rel=1e-9)
        assert lp["status"] == "predicted" and lp["predicted"] == pytest.approx(lp["measured"], rel=2e-2)
        assert (lp["z"] > 0) == (lp["measured"] > lp["predicted"]) and lp["inside"] == (lp["lo"] <= lp["measured"] <= lp["hi"])
        flags += [q["inside"] for q in point["quantities"].values() if "inside" in q]
    assert report["coverage"] == pytest.approx(sum(flags) / len(flags)) and len(report["adopted"]) == 2
    rows = (lib_root / "nt2" / ".icopt" / "observations.jsonl").read_text().splitlines()
    adopted = json.loads(rows[-1])
    assert len(rows) == before + 1 and adopted["origin"].startswith("signoff:signoff:") and adopted["obs_id"] == f"obs_{before + 1:04d}"
    assert (lib_root / "nt2" / ".icopt" / "sims" / adopted["obs_id"] / "em" / "ind" / "ind.s2p").is_file()
    assert "adopted.yaml" in {p.name for p in (lib_root / "nt2" / ".icopt").iterdir()}
