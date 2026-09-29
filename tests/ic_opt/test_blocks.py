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
from ic_opt.observation import ChildResult, Observation, Observations
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
    (netlist / "amap").mkdir()
    (netlist / "amap" / "__dspf_information__.").write_text("dspf\n")     # in every export: the name ends with a dot (N-21)
    (root / tb / "shared.txt").write_text("shared")
    try:
        (netlist / "link").symlink_to(root / tb / "shared.txt")    # Maestro exports carry symlinks; import dereferences them
    except OSError as exc:                                        # Windows without the symlink privilege (N-35, 2026-09-27)
        pytest.skip(f"this account cannot create symlinks: {exc}")
    return root / tb


def test_import_netlists_builds_a_deck_with_corners_and_support_files(tmp_path):
    export = maestro_export(tmp_path / "maestro", "tb")
    spec = make_spec(
        testbenches=[{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c", "test_name": "t"}],
        corners=[{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss", "variables": {"temperature": "125"}}],
    )
    store = RunStore(tmp_path / "proj")
    staging = store.root / "decks" / ".staging"
    (staging / "tb").mkdir(parents=True)
    (staging / "tb" / "stale.scs").write_text("left by an import that stopped part-way")
    deck = import_netlists(spec, LocalExecutor(store.root / "sims"), store)

    assert set(deck.templates) == {("tb", "tt"), ("tb", "ss")}
    assert "F={{F}} W={{W}}" in deck.template("tb", "tt") and "section=ss" in deck.template("tb", "ss")
    assert "temperature=125" in deck.template("tb", "ss")
    bundle = deck.bundle("tb")
    assert (bundle / ".modelFiles").exists() and (bundle / "link").read_text(encoding="utf-8") == "shared" and not (bundle / "link").is_symlink()
    assert (bundle / "amap" / "__dspf_information__.").read_text(encoding="utf-8") == "dspf\n" and not (bundle / "stale.scs").exists()
    assert not staging.exists()                              # removed after the import, not left behind in silence (N-21)
    assert Deck.load(store.root / "decks" / deck.fingerprint()).templates == deck.templates

    # the render stage carries the support files into every simulation directory
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    obs = evaluate(spec, [Point({"F": "22", "W": "0.8u"}, "user")], ex, store, deck=deck, limits=FAKE_HOST)[0]
    assert obs.status == "ok"
    assert (tmp_path / "proj" / obs.children["tb/tt"].sim_dir / "netlist" / ".modelFiles").exists()
    assert (tmp_path / "proj" / obs.children["tb/tt"].sim_dir / "netlist" / "amap" / "__dspf_information__.").exists()


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
    assert names["envelope"].detail.startswith("20 jobs × (10 + 1) threads / 0 GB per job → 220 threads / 0 GB of 128 / 256")
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
    rows = [json.loads(line) for line in (RECORDED / "reports" / "optimizer_evaluations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
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
    md = path.read_text(encoding="utf-8")
    assert md.startswith("# IC-Opt report — ") and "## Summary" in md and "## Best observed" in md and "## Constraint margins" in md
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
    md = analyze.report(spec, obs, store).read_text(encoding="utf-8")
    assert "## Corners" in md and "- failures per corner (a point counts at every corner it fails at): tt " in md and "ss " in md
    assert (store.reports_dir() / "bottleneck_weighted_score.png").exists()


def test_corners_section_scores_each_corner_with_the_device_metrics(tmp_path):
    """EM devices measure once per point (a corner-less child) while testbenches run per corner. The 2026-09-27 real-scenario
    acceptance (N-27, ISSUE-7) found the section filing the device metrics under a corner of their own, so every corner
    read metric_failed and a `nominal` row appeared beside tt / ss / ff. Each corner now counts the corner-less metrics."""
    d = minimal_spec()
    d["corners"] = [{"id": "tt"}, {"id": "ss"}]
    d["metrics"] = [{"name": "NF", "unit": "dB", "expression": "nf()"}, {"name": "Qp", "unit": "ratio", "expression": "qp()"}]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "Qp", "op": "gt", "value": "10"}]
    d["objective"] = {"direction": "minimize", "expression": "NF"}
    spec = make_spec(**d)
    stamp = {"spec_fingerprint": spec.fingerprint(), "pipeline_fingerprint": "p", "started_at": "t", "finished_at": "t"}

    def observation(i, nf_ss, device_status="ok"):
        children = {"tb/tt": ChildResult(unit="tb", corner="tt", status="ok", metrics={"NF": 8.0}),
                    "tb/ss": ChildResult(unit="tb", corner="ss", status="ok", metrics={"NF": nf_ss}),
                    "xfmr/nominal": ChildResult(unit="xfmr", corner=None, status=device_status, metrics={"Qp": 12.0})}
        worst = max(8.0, nf_ss)
        status = device_status if device_status != "ok" else ("ok" if worst < 9 else "constraint_failed")
        return Observation(obs_id=f"obs_{i}", params={"F": str(20 + 2 * i), "W": "0.6u"}, origin="user", children=children,
                           metrics={} if status.startswith("failed") else {"NF": worst, "Qp": 12.0}, fom=worst, objective=worst,
                           feasible=status == "ok", status=status, **stamp)

    rows = Observations([observation(0, 8.5), observation(1, 9.5), observation(2, 8.2, "failed:emx:xfmr")])
    md = analyze._corners_section(spec, rows)
    assert "- best observation obs_0 per corner:" in md
    assert "| corner | status | objective | NF | Qp |" in md
    assert "| tt | ok | 8 | 8 dB | 12 |" in md and "| ss | ok | 8.5 | 8.5 dB | 12 |" in md
    assert "nominal" not in md and "metric_failed" not in md
    assert "- failures per corner (a point counts at every corner it fails at): tt 1/3, ss 2/3" in md   # obs_1 fails at ss; obs_2's device failed for both
    assert "- NF < 9 dB violated at: tt 0/3, ss 1/3" in md and "- Qp > 10 violated at: tt 0/3, ss 0/3" in md
    # Constraint margins are judged on every corner (all_corners): obs_1's own metrics hold ss's NF 9.5, obs_0's tt 8
    margins = analyze._margins_section(spec, rows)
    assert "- NF < 9 dB: pass 2/3, best margin 0.8 dB (obs_2), worst -0.5 dB (obs_1)" in margins      # each point's worst corner
    d["corner_policy"] = {"objective": "worst_case", "constraints": "nominal"}
    nominal = analyze._margins_section(make_spec(**d), rows)
    assert "- NF < 9 dB: pass 3/3" in nominal and "the nominal corner" in nominal


def test_margins_take_the_worst_corner_even_when_the_point_selected_another(tmp_path):
    """N-35 (2026-09-27, ISSUE-8): a point's metrics are the corner with the largest total penalty; a constraint another corner
    violates alone read as passed in the margins. Two constraints, two corners: NF fails at ss only, gain fails at tt only
    and by more, so the point selects tt -- the margins must still count NF as violated."""
    d = minimal_spec()
    d["corners"] = [{"id": "tt"}, {"id": "ss"}]
    d["metrics"] = [{"name": "NF", "unit": "dB", "expression": "nf()"}, {"name": "gain", "unit": "dB", "expression": "g()"}]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "gain", "op": "gt", "value": "10"}]
    d["objective"] = {"direction": "minimize", "expression": "NF"}
    spec = make_spec(**d)
    stamp = {"spec_fingerprint": spec.fingerprint(), "pipeline_fingerprint": "p", "started_at": "t", "finished_at": "t"}
    children = {"tb/tt": ChildResult(unit="tb", corner="tt", status="ok", metrics={"NF": 8.0, "gain": 4.0}),
                "tb/ss": ChildResult(unit="tb", corner="ss", status="ok", metrics={"NF": 9.5, "gain": 12.0})}
    o = Observation(obs_id="obs_0", params={"F": "20", "W": "0.6u"}, origin="user", children=children, metrics={"NF": 8.0, "gain": 4.0},
                    fom=8.0, objective=None, feasible=False, status="constraint_failed", constraint_penalty=0.36, **stamp)
    md = analyze._margins_section(spec, Observations([o]))
    assert "- NF < 9 dB: pass 0/1, best margin -0.5 dB (obs_0), worst -0.5 dB (obs_0)" in md
    assert "- gain > 10 dB: pass 0/1, best margin -6 dB (obs_0), worst -6 dB (obs_0)" in md


def test_an_empty_deck_saves_and_loads(tmp_path):
    """A deck without templates (a devices-only spec) saves its directory and source.txt and loads back empty (N-22)."""
    saved = Deck().save(tmp_path / "decks")
    assert (saved / "source.txt").exists()
    loaded = Deck.load(saved)
    assert loaded.templates == {} and loaded.bundles == {} and loaded.source == {}


def test_a_devices_only_spec_imports_an_empty_deck(tmp_path):
    """netlist.import on a spec with devices and no testbenches (the em_only pipeline, run by the same recipes) fetches
    nothing and returns an empty deck; it used to crash in Deck.save (N-22)."""
    from tests.ic_opt.test_em_engine import em_spec

    spec = em_spec(testbenches=False)
    store = RunStore(tmp_path)
    deck = import_netlists(spec, LocalExecutor(store.root / "sims"), store)
    assert deck.templates == {} and deck.bundles == {}
    assert (store.root / "decks" / deck.fingerprint() / "source.txt").exists()
    assert not (store.root / "decks" / ".staging").exists()


def test_report_values_carry_units_and_the_summary_names_what_binds(tmp_path):
    """The N-42 report review (2026-09-27): raw floats without units (BW=3.2e+10), constraints as `gt 26e9 Hz`, advisory
    ranges without their suffix, no summary. Values now print with their unit and an SI prefix, constraints read
    `BW > 26 GHz`, and the summary says how many points are feasible and which constraint binds."""
    d = minimal_spec()
    d["metrics"] = [{"name": "BW", "unit": "Hz", "expression": "bw()"}, {"name": "NF", "unit": "dB", "expression": "nf()"}]
    d["constraints"] = [{"metric": "BW", "op": "gt", "value": "26e9 Hz"}, {"metric": "NF", "op": "lt", "value": "9"}]
    d["objective"] = {"direction": "minimize", "expression": "NF"}
    spec = make_spec(**d)
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"BW": 3.2e10 if p["F"] != "20" else 2.4e10, "NF": 8.0 + int(p["F"]) / 100})
    obs = evaluate(spec, points.grid(spec, per_dim=2), ex, store, deck=Deck(templates={("tb", None): "parameters F={{F}} W={{W}}\n"}), limits=FAKE_HOST)
    md = analyze.report(spec, obs, store).read_text(encoding="utf-8")
    assert "## Summary" in md and "- feasible: 2 of 4 observations (50%)" in md
    assert "- binding constraints: BW > 26 GHz (2 of 4)" in md
    assert "- BW > 26 GHz: pass 2/4, best margin 6 GHz" in md and "BW=32 GHz" in md and "NF=8.3 dB" in md
    html_text = (store.reports_dir() / "report.html").read_text(encoding="utf-8")
    assert "<div class='table'>" in html_text and "name='viewport'" in html_text and "<figcaption>" in html_text
    assert "<h2>Figures</h2>" not in html_text and html_text.index("<h2>Constraint margins</h2>") < html_text.index("above the line passes")
