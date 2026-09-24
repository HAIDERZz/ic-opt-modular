from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ic_opt.blocks import analyze, points
from ic_opt.blocks.doctor import doctor
from ic_opt.blocks.evaluate import evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.deck import Deck
from ic_opt.executor import LocalExecutor
from ic_opt.migrate import spec_from_config_dir
from ic_opt.observation import Observation
from ic_opt.site import HostLimits
from ic_opt.space import Point
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec, minimal_spec

RECORDED = Path(os.environ.get("IC_OPT_RECORDED_RUNS", "").split(":")[0] or "/nonexistent")   # first recorded 0.1.10 run


def maestro_export(root: Path, tb: str, params: str = "F=20 W=0.6u") -> Path:
    netlist = root / tb / "netlist"
    netlist.mkdir(parents=True)
    (netlist / "input.scs").write_text(
        f'simulator lang=spectre\ninclude "/pdk/models.scs" section=tt\nparameters temperature=27 {params}\ntran tran stop=10n\n'
    )
    (netlist / ".modelFiles").write_text("/pdk/models.scs\n")
    (root / tb / "shared.txt").write_text("shared")
    (netlist / "link").symlink_to(root / tb / "shared.txt")        # Maestro exports carry symlinks; import dereferences them
    return root / tb


def test_import_netlists_builds_a_deck_with_corners_and_support_files(tmp_path):
    export = maestro_export(tmp_path / "maestro", "tb")
    spec = make_spec(
        testbenches=[{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c", "test_name": "t"}],
        corners=[{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss", "variables": {"temperature": "125"}}],
    )
    store = RunStore(tmp_path / "proj")
    deck = import_netlists(spec, LocalExecutor(store.root / "sims"), store)

    assert set(deck.templates) == {("tb", "tt"), ("tb", "ss")}
    assert "F={{F}} W={{W}}" in deck.template("tb", "tt") and "section=ss" in deck.template("tb", "ss")
    assert "temperature=125" in deck.template("tb", "ss")
    bundle = deck.bundle("tb")
    assert (bundle / ".modelFiles").exists() and (bundle / "link").read_text() == "shared" and not (bundle / "link").is_symlink()
    assert Deck.load(store.root / "decks" / deck.fingerprint()).templates == deck.templates

    # the render stage carries the support files into every simulation directory
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    obs = evaluate(spec, [Point({"F": "22", "W": "0.8u"}, "user")], ex, store, deck=deck, limits=FAKE_HOST)[0]
    assert obs.status == "ok"
    assert (tmp_path / "proj" / obs.children["tb/tt"].sim_dir / "netlist" / ".modelFiles").exists()


def test_import_rejects_variable_not_in_top_level_parameters(tmp_path):
    export = maestro_export(tmp_path / "maestro", "tb", params="F=20")
    spec = make_spec(testbenches=[{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c", "test_name": "t"}])
    with pytest.raises(ValueError, match="W was not found"):
        import_netlists(spec, LocalExecutor(tmp_path / "scratch"), RunStore(tmp_path / "proj"))


def test_doctor_reports_each_check(tmp_path):
    export = maestro_export(tmp_path / "maestro", "tb")
    spec = make_spec(
        testbenches=[{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c", "test_name": "t"}],
        simulator={"parallel_jobs": 20, "threads_per_run": 10, "timeout_s": 10, "license_check": False},
    )
    store = RunStore(tmp_path / "proj")
    report = doctor(spec, LocalExecutor(store.root / "sims"), store=store, limits=HostLimits(max_threads=128, max_memory_gb=256))
    names = {c.name: c for c in report.checks}
    assert names["executor"].ok and names["export:tb"].ok and names["budget"].ok
    assert not names["envelope"].ok
    assert names["envelope"].detail.startswith("20 jobs × 10 threads / 0 GB per job → 200 threads / 0 GB of 128 / 256")
    assert not report.ok
    with pytest.raises(RuntimeError, match=r"envelope: 20 jobs"):
        report.require_pass()


def test_point_sources():
    spec = make_spec()
    assert [p.params for p in points.fixed(spec, [{"F": 24, "W": "0.8u"}])] == [{"F": "24", "W": "0.8u"}]
    with pytest.raises(ValueError):
        points.fixed(spec, [{"F": "21", "W": "0.8u"}])
    full = points.grid(spec)
    assert len(full) == 24 and len({p.key for p in full}) == 24
    assert [p.params["F"] for p in points.grid(spec, per_dim=2)][::2] == ["20", "30"]
    oat = points.one_at_a_time(spec, {"F": "20", "W": "0.8u"})
    assert [p.params for p in oat] == [{"F": "20", "W": "0.8u"}, {"F": "22", "W": "0.8u"}, {"F": "20", "W": "0.6u"}, {"F": "20", "W": "1u"}]
    sob = points.sobol(spec, 6, seed=3)
    assert 1 <= len(sob) <= 6 and all(p.origin == "points:sobol" for p in sob)


def test_score_model_parses_the_bottleneck_form_and_nothing_else():
    expr = "-(0.1*min(max(0,min(1,(9-NF)/0.7)),max(0,min(1,(IIP3-1)/2)))+0.8*(0.3*max(0,min(1,(9-NF)/0.7))+0.7*max(0,min(1,(IIP3-1)/2))))"
    model = analyze.score_model(expr)
    assert model["bottleneck_weight"] == 0.1 and model["sum_weight"] == 0.8
    assert model["weights"] == {"NF": 0.3, "IIP3": 0.7}
    assert analyze.score_model("NF_3G * 1e9 / (BW*MAX_GAIN)") is None


@pytest.mark.skipif(not RECORDED.exists(), reason="recorded run not available")
def test_report_from_recorded_run(tmp_path):
    spec = spec_from_config_dir(RECORDED / "config")
    rows = [json.loads(line) for line in (RECORDED / "reports" / "optimizer_evaluations.jsonl").read_text().splitlines() if line.strip()]
    obs = []
    for i, row in enumerate(rows, 1):
        metrics = row.get("metrics") or {}
        obs.append(Observation(
            obs_id=f"obs_{i:04d}", params=row["parameters"], origin="replay", metrics=metrics, fom=row.get("fom"),
            objective=row["objective"] if row["status"] == "feasible" else None, feasible=row["status"] == "feasible",
            constraint_penalty=row.get("constraint_penalty") or 0.0,
            status={"feasible": "ok", "constraint_failed": "constraint_failed"}.get(row["status"], "metric_failed"),
            spec_fingerprint=spec.fingerprint(), pipeline_fingerprint="replay", step="optimize", started_at="t", finished_at="t",
        ))
    store = RunStore(tmp_path)
    path = analyze.report(spec, obs, store)
    md = path.read_text()
    assert md.startswith("# IC-Opt report — ") and "## Best observed" in md and "## Constraint margins" in md
    assert "real_066" not in md and analyze.best(spec, obs)[0].params == {"F": "26", "L": "40n", "VB_LO": "310m", "W": "1u"}
    assert "- IIP3 gt 0 dBm: pass 64/64" in md
    assert (store.reports_dir() / "report.html").stat().st_size > 20_000
    figures = {p.name for p in store.reports_dir().glob("*.png")}
    assert {"feasible_convergence.png", "convergence.png", "constraint_margins.png"} <= figures
    assert "bottleneck_weighted_score.png" not in figures          # this objective is not the bottleneck form


def test_report_with_corners_and_bottleneck_objective(tmp_path):
    d = minimal_spec()
    d["corners"] = [{"id": "tt"}, {"id": "ss"}]
    d["metrics"] = [{"name": "NF", "unit": "dB", "expression": "nf()"}, {"name": "IIP3", "unit": "dBm", "expression": "iip3()"}]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "IIP3", "op": "gt", "value": "1"}]
    d["objective"] = {"direction": "minimize",
                      "expression": "-(0.1*min(max(0,min(1,(9-NF)/0.7)),max(0,min(1,(IIP3-1)/2)))+0.8*(0.3*max(0,min(1,(9-NF)/0.7))+0.7*max(0,min(1,(IIP3-1)/2))))"}
    d["budget"] = {"max_simulations": 100}
    spec = make_spec(**d)
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0 + (0.6 if c == "ss" else 0) + int(p["F"]) / 100, "IIP3": 2.5 - int(p["F"]) / 20})
    deck = Deck(templates={("tb", c): "parameters F={{F}} W={{W}}\n" for c in ("tt", "ss")})
    obs = evaluate(spec, points.grid(spec, per_dim=3), ex, store, deck=deck, limits=FAKE_HOST)
    md = analyze.report(spec, obs, store).read_text()
    assert "## Corners" in md and "- failures per corner: tt " in md and "ss " in md
    assert (store.reports_dir() / "bottleneck_weighted_score.png").exists()
