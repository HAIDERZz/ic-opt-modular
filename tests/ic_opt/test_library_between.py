"""N-127 (``docs/refactor/N127_BETWEEN_KNOTS_SPEC.md``): a curve between two knots answered from the knots' models, without a
fit of its own.

The fake: the synthetic inductor library (``build_library``: 39 single-turn rows resonating above their 60 GHz sweep, 66
two-turn rows resonating at 16-60 GHz; SRF_p and Lp anchored at 10 GHz) with the grid ``GRID`` -- 2, 4, 8, 16, 38 and 60
GHz, so Lp's knots are 2, 4, 8, 10, 16, 38, 60 GHz. Its curves are known: every row is an analytic RLC network
(``rlc_touchstone``), so a curve's true value at any frequency is measured from the network itself (``curve_at``). The knots'
models are fitted once per module (``prefitted``) and copied into each test's library with the rest of the cache, so a test
fits nothing unless it says so."""
from __future__ import annotations

import itertools
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from scipy.interpolate import PchipInterpolator
from typer.testing import CliRunner

from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.em import measure, touchstone
from ic_opt.library import between, composed, dataset, densify, domain, gp, region, stage, suggest
from ic_opt.library import query as q
from tests.ic_opt.library_fixtures import LOCAL, build_library, params, rlc_touchstone, use_site
from tests.ic_opt.test_library_grid import set_grid

STRATUM = "ind_demo"
GRID = {"from_ghz": 2, "ratio": 2, "until_ghz": 16, "step_ghz": 22}          # 2, 4, 8, then 16, 38, 60 over 0-60 GHz
KNOTS = [2.0, 4.0, 8.0, 10.0, 16.0, 38.0, 60.0]                             # with Lp's anchor at 10 GHz
FAR = params(150, 5, 2.5, 2)          # SRF_p about 26 GHz: Lp@6 interpolates over 2, 4, 8, 10; Lp@13 over 8, 10, 16 (38 past it)
NEAR = params(187, 5, 2.5, 2)         # SRF_p about 18 GHz: at 13 GHz above srf_margin x 13 = 16.25, at or below 1.25 x 16 = 20
SINGLE = params(155, 5.5, 2, 1)       # a single turn resonating above the sweep: Lp@13 over 8, 10, 16, 38


def curve_at(geometry: dict, curve: str, ghz: float) -> float:
    """The curve of the synthetic network ``geometry`` at ``ghz`` GHz, measured from the network itself (its own sweep)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.s2p"
        path.write_text(rlc_touchstone(geometry["outer_diameter_um"], geometry["width_um"], geometry["spacing_um"],
                                       int(geometry["turns"])), encoding="utf-8")
        ts = touchstone.read(path)
    quantities = measure.quantities(ts.freqs, ts.s, measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"]), z0=ts.z0)
    return quantities.at(curve, ghz * 1e9)


@pytest.fixture(scope="module")
def prefitted(tmp_path_factory) -> Path:
    """The synthetic library with the grid, every Lp knot's model (and SRF_p's) fitted and cached."""
    root = set_grid(build_library(tmp_path_factory.mktemp("between")), GRID)
    lib = q.Library(root, limits=LOCAL)
    assert lib.knots(STRATUM, "Lp") == KNOTS
    lib.models(STRATUM, [f"Lp@{k:g}" for k in KNOTS] + ["Qp_peak"], exact=True)
    return root


def copy(prefitted: Path, dest: Path, *, grid: dict | None = GRID) -> Path:
    """The prefitted library with its whole cache but no allowance table, its grid ``grid`` (None: none)."""
    root = shutil.copytree(prefitted, dest, ignore=shutil.ignore_patterns("between-*"))
    return set_grid(root, grid)


def counted_fits(monkeypatch) -> list[str]:
    """From now on, every column ``Library._fit`` fits in this process."""
    out: list[str] = []
    original = q.Library._fit

    def fit(self, stratum, quantity):
        out.append(quantity)
        return original(self, stratum, quantity)

    monkeypatch.setattr(q.Library, "_fit", fit)
    return out


def counted_measures(monkeypatch) -> list[str]:
    """From now on, every row sNp ``dataset._measure`` measures."""
    out: list[str] = []
    original = dataset._measure

    def measured(work, device, stratum):
        out.append(str(work))
        return original(work, device, stratum)

    monkeypatch.setattr(dataset, "_measure", measured)
    return out


def strip(answer: dict) -> dict:
    """An answer without its evidence rows (the column's own rows when interpolated, the model's otherwise)."""
    return {k: v for k, v in answer.items() if k != "nearest"}


# -- reading the rows' curves ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("freqs", [np.arange(0, 61) * 1e9, np.array([0, 1, 2, 3, 5, 8, 13, 21, 34, 55]) * 1e9])
def test_at_reads_a_curve_as_quantities_at_does_bit_for_bit(tmp_path, freqs):
    lines = rlc_touchstone(150, 5, 2.5, 2, freqs_ghz=freqs / 1e9)
    (tmp_path / "x.s2p").write_text(lines, encoding="utf-8")
    ts = touchstone.read(tmp_path / "x.s2p")
    quantities = measure.quantities(ts.freqs, ts.s, measure.Topology.from_labels([("P1", "N1")], [], ["P1", "N1"]), z0=ts.z0)
    rng = np.random.default_rng(0)
    x = np.concatenate([rng.uniform(freqs[0], freqs[-1], 400), freqs, freqs[1:] * (1 + 1e-12), freqs[1:] * (1 - 3e-10),
                        [0.3e9, 0.9e9, freqs[1] / 2]])
    for curve in quantities.curves:
        expected = np.array([quantities.at(curve, v) for v in x])
        assert np.array_equal(between.at(quantities, curve, x), expected, equal_nan=True), curve
    assert np.isnan(between.at(quantities, "Lp", [freqs[-1] * 1.01, -1.0])).all()           # outside the sweep: no value


def test_the_allowances_are_measured_once_per_dataset_curve_and_knots(prefitted, tmp_path, monkeypatch):
    """One pass over the rows for every curve, the file keyed by the dataset, the curve and its knots; a second Library reads
    it; an interval's allowance over all its rows is the p99 of PCHIP over the rows' own extension columns."""
    root = copy(prefitted, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    ds = lib.dataset(STRATUM)
    measured = counted_measures(monkeypatch)
    tables = lib.between_tables(STRATUM)
    assert len(measured) == len(ds.rows)                                     # one pass
    table = tables["Lp"]
    name = between.table_file(ds, "Lp", KNOTS, 1.25)
    assert (root / ".cache" / name).is_file() and json.loads((root / ".cache" / name).read_text()) == table
    assert [(e["from"], e["to"]) for e in table["intervals"]] == list(itertools.pairwise(KNOTS))
    assert table["edges"] == list(between.EDGES) and table["n_min"] == between.N_MIN
    assert q.Library(root, limits=LOCAL).between_tables(STRATUM) == tables and len(measured) == len(ds.rows)   # read, not measured
    other = between.table_file(ds, "Lp", KNOTS[:-1], 1.25)
    assert other != name                                                     # other knots: another file

    j = 1                                                                    # 4-8 GHz: stencil 2, 4, 8 and 10 where a row has it
    probes = [KNOTS[j] + p * (KNOTS[j + 1] - KNOTS[j]) for p in between.PROBES]
    at_probes = {f: dataset.anchors(root, ds, lib.manifest.strata[STRATUM], f, cache=None)["curves"]["Lp"] for f in probes}
    knots = {k: ds.values(f"Lp@{k:g}") for k in KNOTS}
    errs = []
    for i in range(len(ds.rows)):
        stencil = [k for k in KNOTS[0:4] if np.isfinite(knots[k][i])]
        if stencil[:3] != KNOTS[0:3] or any(at_probes[f][i] is None for f in probes):
            continue
        errs += [abs(PchipInterpolator(stencil, [knots[k][i] for k in stencil])(f) / at_probes[f][i] - 1) for f in probes]
    assert table["intervals"][j]["all"] == pytest.approx(np.percentile(errs, between.QUANTILE), rel=1e-9)
    assert table["intervals"][j]["rows"] == len(errs) // len(probes)


def test_composed_knots_share_their_parts_predictions_bit_for_bit():
    """Two composed knots (``model: resonance`` at 10 and 20 GHz) built on one base and one SRF model: within one call their
    copies (``between._sharing``) predict the shared models once per block of rows, and answer as the knots do, bit for bit."""
    rng = np.random.default_rng(0)
    dims, ranges = ["a", "b"], {"a": (0.0, 1.0), "b": (0.0, 1.0)}
    x = rng.uniform(0, 1, (40, 2))
    srf_y = 50 + 10 * x[:, 1]
    lf = gp.StratumGP(dims=dims, ranges=ranges, log_target=True).fit(x, np.exp(x[:, 0]))
    srf = gp.StratumGP(dims=dims, ranges=ranges, log_target=True).fit(x, srf_y)
    knots = []
    for f0 in (10.0, 20.0):
        c = composed.ComposedGP(kind="resonance", f0=f0, dims=dims, ranges=ranges, nt_dim=None,
                                part={"dims": dims, "ranges": ranges, "log_target": True}, names={"lf": "Lp_lf", "srf": "SRF"})
        c.attach(lf, srf).fit(x, np.exp(x[:, 0]) / (1 - (f0 / srf_y) ** 2) * (1 + 0.01 * x[:, 0]), lf_y=np.exp(x[:, 0]))
        knots.append(SimpleNamespace(gp=c))
    calls = {"lf": 0, "srf": 0}
    for name, model in (("lf", lf), ("srf", srf)):
        original = model.predict

        def counted(rows, *, floor=True, name=name, original=original):
            calls[name] += 1
            return original(rows, floor=floor)

        model.predict = counted
    xs = rng.uniform(0, 1, (30, 2))
    expected = [k.gp.predict(xs) for k in knots]
    assert calls == {"lf": 2, "srf": 2}
    gps = between._sharing(knots)
    got = [g.predict(xs) for g in gps]
    assert calls == {"lf": 3, "srf": 3}                                      # once for both copies
    for (mu, sigma), (mu_e, sigma_e) in zip(got, expected):
        assert np.array_equal(mu, mu_e) and np.array_equal(sigma, sigma_e)
    assert knots[0].gp.lf is lf and gps[0].lf is gps[1].lf and gps[0].part is knots[0].gp.part


# -- at a knot, between knots, exact ------------------------------------------------------------------------------------

def test_at_a_knot_the_answer_is_the_knot_columns_field_for_field(prefitted, tmp_path, monkeypatch):
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    fits = counted_fits(monkeypatch)
    for point in (FAR, SINGLE, NEAR):
        grid = q.query(lib, STRATUM, point, ["Lp@8", "Lp@16", "Lp@10"])
        assert grid == q.query(lib, STRATUM, point, ["Lp@8", "Lp@16", "Lp@10"], exact=True)
        assert not any("interpolated" in a for a in grid["quantities"].values())
    plain = q.Library(copy(prefitted, tmp_path / "plain", grid=None), limits=LOCAL)
    assert q.query(plain, STRATUM, FAR, ["Lp@8", "Lp@16"]) == q.query(lib, STRATUM, FAR, ["Lp@8", "Lp@16"])
    assert fits == [] and not list((tmp_path / "lib" / ".cache").glob("between-*"))       # no table either: nothing between knots


def test_between_knots_the_value_and_bounds_are_the_knots_interpolated_and_widened(prefitted, tmp_path, monkeypatch):
    """Lp@6 at FAR: PCHIP over 2, 4, 8, 10 GHz of the knots' own answers, the bounds widened by the allowance of FAR's
    proximity band; the value within its bounds of the network's own Lp at 6 GHz; nothing fitted for Lp@6."""
    root = copy(prefitted, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    fits = counted_fits(monkeypatch)
    answer = q.query(lib, STRATUM, FAR, ["Lp@6", "SRF_p"])["quantities"]
    a = answer["Lp@6"]
    assert a["status"] == "predicted" and a["interpolated"]["knots"] == [2.0, 4.0, 8.0, 10.0]
    knots = q.query(lib, STRATUM, FAR, ["Lp@2", "Lp@4", "Lp@8", "Lp@10"])["quantities"]
    f = [2.0, 4.0, 8.0, 10.0]
    pchip = {key: float(PchipInterpolator(f, [knots[f"Lp@{k:g}"][key] for k in f])(6.0)) for key in ("value", "lo", "hi")}
    allowance = a["interpolated"]["allowance"]
    table = lib.between_tables(STRATUM)["Lp"]["intervals"][1]
    u = 1.25 * 8e9 / answer["SRF_p"]["lo"]
    assert allowance == table["bands"][int(between.band(np.array([u]))[0])]["allowance"] and 0 < allowance < between.CAP
    assert a["value"] == pytest.approx(pchip["value"], rel=1e-12)
    assert a["lo"] == pytest.approx(pchip["lo"] / (1 + allowance), rel=1e-12) and a["hi"] == pytest.approx(pchip["hi"] / (1 - allowance), rel=1e-12)
    assert a["hi"] - a["lo"] > pchip["hi"] - pchip["lo"]                                      # the allowance widens
    assert a["lo"] <= curve_at(FAR, "Lp", 6.0) <= a["hi"]
    assert a["value"] == pytest.approx(curve_at(FAR, "Lp", 6.0), rel=2e-3)
    assert a["k_scale"] == max(lib.model(STRATUM, f"Lp@{k:g}").calibration["k_scale"] for k in f)
    assert fits == [] and not list((root / ".cache").glob("model-ind_demo-Lp_at_6-*"))
    assert len(list((root / ".cache").glob("between-ind_demo-Lp-*.json"))) == 1


def test_the_second_knot_above_joins_only_below_the_resonance(prefitted, tmp_path):
    """Lp@13: at FAR (resonance about 26 GHz, at or below 1.25 x 38 GHz) over 8, 10, 16; at SINGLE (above the sweep) over 8,
    10, 16, 38 -- each as PCHIP over those knots of their own answers."""
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    for point, used in ((FAR, [8.0, 10.0, 16.0]), (SINGLE, [8.0, 10.0, 16.0, 38.0])):
        a = q.query(lib, STRATUM, point, ["Lp@13"])["quantities"]["Lp@13"]
        assert a["status"] == "predicted" and a["interpolated"]["knots"] == used
        knots = q.query(lib, STRATUM, point, [f"Lp@{k:g}" for k in used])["quantities"]
        assert a["value"] == pytest.approx(float(PchipInterpolator(used, [knots[f"Lp@{k:g}"]["value"] for k in used])(13.0)), rel=1e-12)
        assert a["lo"] <= curve_at(point, "Lp", 13.0) <= a["hi"]


def test_between_the_last_two_knots_there_is_one_knot_above(prefitted, tmp_path):
    """Lp@50 lies between 38 and 60 GHz, the last knot: its stencil is 16, 38, 60 GHz. Here the single turns resonate just above
    their 60 GHz sweep, so their own curves bend too fast between 38 and 60 GHz for an interpolation (the allowance exceeds
    the cap): refused in lib.query and, without failing, in the batch path; the interpolation itself is PCHIP over the three."""
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    a = q.query(lib, STRATUM, SINGLE, ["Lp@50"])["quantities"]["Lp@50"]
    assert a["status"] == "out_of_domain" and a["reason"].startswith("between the knots 38 and 60 GHz and next to its resonance: interpolated")
    assert lib.between_tables(STRATUM)["Lp"]["intervals"][-1]["bands"][-1]["allowance"] > between.CAP
    models = lib.models(STRATUM, ["Lp@50"])
    dims = lib.dataset(STRATUM).dims
    x = np.array([[SINGLE[d] for d in dims], [params(101, 4.5, 2.5, 2)[d] for d in dims]])
    ok, pred = suggest.predict_all(x, models, srf_floor=suggest.srf_floors_for(lib, STRATUM, x, models))
    assert ok.tolist() == [False, False] and pred["Lp@50"]["interpolated"]["verdict"][0] == between.LARGE
    g = models["Lp@50"].gp
    assert g.second is None and g.used(True) == [16.0, 38.0, 60.0]
    knots = q.query(lib, STRATUM, SINGLE, ["Lp@16", "Lp@38", "Lp@60"])["quantities"]
    expected = float(PchipInterpolator([16.0, 38.0, 60.0], [knots[f"Lp@{k:g}"]["value"] for k in (16, 38, 60)])(50.0))
    assert g.answer(x[:1], 2.0)["value"][0] == pytest.approx(expected, rel=1e-12)


def test_exact_is_the_columns_own_model_as_without_a_grid(prefitted, tmp_path, monkeypatch):
    root = copy(prefitted, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    fits = counted_fits(monkeypatch)
    exact = q.query(lib, STRATUM, FAR, ["Lp@6", "Lp@13"], exact=True)
    assert fits == ["Lp@6", "Lp@13"] and not any("interpolated" in a for a in exact["quantities"].values())
    plain = q.Library(copy(prefitted, tmp_path / "plain", grid=None), limits=LOCAL)
    for point in (FAR, NEAR, SINGLE):
        assert q.query(lib, STRATUM, point, ["Lp@6", "Lp@13"], exact=True) == q.query(plain, STRATUM, point, ["Lp@6", "Lp@13"])
    assert lib.model(STRATUM, "Lp@6", exact=True).gp.__class__ is gp.StratumGP
    assert isinstance(lib.model(STRATUM, "Lp@6").gp, between.Between)
    blocked = library_blocks.query(lib, STRATUM, FAR, "Lp@6", exact="true")
    assert blocked["quantities"]["Lp@6"] == json.loads(json.dumps(exact["quantities"]["Lp@6"]))


def test_a_library_row_answers_its_measured_value(prefitted, tmp_path):
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    row = lib.dataset(STRATUM).find(params(150, 5, 3, 2))
    answer = q.query(lib, STRATUM, params(150, 5, 3, 2), ["Lp@6", "Lp@13"])
    assert answer["measured"] == {"part": row.part, "obs_id": row.obs_id}
    assert answer["quantities"]["Lp@6"] == {"status": "measured", "value": row.values["Lp@6"], "unit": "H"}
    assert answer["quantities"]["Lp@13"]["value"] == row.values["Lp@13"] == curve_at(params(150, 5, 3, 2), "Lp", 13.0)


# -- the domain and the resonance -----------------------------------------------------------------------------------------

def test_the_next_knot_above_past_the_resonance_margin_is_out_of_domain(prefitted, tmp_path):
    """NEAR at 13 GHz: its resonance (about 18 GHz) lies above srf_margin x 13 GHz but at or below srf_margin x 16 GHz, the
    knot above: out of the domain, naming exact=true -- which answers from the column's own model."""
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    answer = q.query(lib, STRATUM, NEAR, ["Lp@13", "SRF_p"])["quantities"]
    srf = answer["SRF_p"]["value"]
    assert 1.25 * 13e9 < srf <= 1.25 * 16e9
    a = answer["Lp@13"]
    assert a["status"] == "out_of_domain" and a["criterion"] == "interpolation"
    assert a["reason"] == (f"between the knots 10 and 16 GHz and next to its resonance ({srf / 1e9:.4g} GHz, at or below srf_margin x "
                           "16 GHz = 20 GHz); exact=true fits this frequency's own model")
    assert q.query(lib, STRATUM, NEAR, ["Lp@13"], exact=True)["quantities"]["Lp@13"]["status"] in ("predicted", "uncertain", "out_of_domain")
    above = q.query(lib, STRATUM, params(200, 5, 2.5, 2), ["Lp@14"])["quantities"]["Lp@14"]   # resonance about 16.5 GHz
    assert above["status"] == "above_resonance"                                              # N-113 at 14 GHz itself, as before


def test_a_stencil_knot_out_of_its_domain_refuses_the_point(prefitted, tmp_path, monkeypatch):
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    knot = lib.model(STRATUM, "Lp@16")
    original = knot.guard.check

    def refusing(params_, *, extrapolate=0.0):
        if params_["width_um"] == 5.2:
            raise domain.OutOfDomainError(criterion=3, reason="outside the convex hull at turns=2", nearest=[(0, 0.1)], fill_points=[])
        return original(params_, extrapolate=extrapolate)

    monkeypatch.setattr(knot.guard, "check", refusing)
    point = params(150, 5.2, 2.5, 2)
    a = q.query(lib, STRATUM, point, ["Lp@13"])["quantities"]["Lp@13"]
    assert a["status"] == "out_of_domain" and a["criterion"] == 3 and a["reason"] == "knot Lp@16: outside the convex hull at turns=2"
    assert all(n["value"] is not None for n in a["nearest"])                                  # the column's own rows as evidence
    assert q.query(lib, STRATUM, point, ["Lp@6"])["quantities"]["Lp@6"]["status"] == "predicted"     # 16 GHz is no knot of Lp@6's


def test_a_band_with_too_few_rows_or_too_large_an_allowance_is_refused(prefitted, tmp_path, monkeypatch):
    root = copy(prefitted, tmp_path / "lib")
    monkeypatch.setattr(between, "N_MIN", 10_000)
    a = q.query(q.Library(root, limits=LOCAL), STRATUM, FAR, ["Lp@6"])["quantities"]["Lp@6"]
    assert a["status"] == "out_of_domain" and a["criterion"] == "interpolation"
    assert a["reason"].startswith("between the knots 4 and 8 GHz and next to its resonance: fewer than 10000 of the table's rows lie as "
                                  "near their resonance in this interval (u = srf_margin x 8 GHz / resonance = ")
    assert a["reason"].endswith("too few to measure how far an interpolation errs there; exact=true fits this frequency's own model")
    monkeypatch.setattr(between, "N_MIN", 20)
    monkeypatch.setattr(between, "CAP", 1e-9)
    b = q.query(q.Library(root, limits=LOCAL), STRATUM, FAR, ["Lp@6"])["quantities"]["Lp@6"]
    assert b["status"] == "out_of_domain" and "more than the 1e-07 % an interpolated answer may add; exact=true fits" in b["reason"]


# -- the batch path, and every caller -------------------------------------------------------------------------------------

def test_predict_all_answers_as_query_does(prefitted, tmp_path):
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    points = [FAR, NEAR, SINGLE, params(120, 4.5, 2.2, 2), params(170, 5.5, 2.8, 2), params(95, 4.2, 2, 1)]
    x = np.array([[p[d] for d in lib.dataset(STRATUM).dims] for p in points])
    models = lib.models(STRATUM, ["Lp@6", "Lp@13", "SRF_p"])
    floors = suggest.srf_floors_for(lib, STRATUM, x, models)
    ok, pred = suggest.predict_all(x, models, srf_floor=floors)
    for i, p in enumerate(points):
        answers = q.query(lib, STRATUM, p, ["Lp@6", "Lp@13", "SRF_p"])["quantities"]
        good = all(answers[c]["status"] == "predicted" for c in ("Lp@6", "Lp@13")) and answers["SRF_p"]["status"] in ("predicted", "above_sweep")
        assert bool(ok[i]) == good, (p, answers)
        for c in ("Lp@6", "Lp@13"):
            if answers[c]["status"] == "predicted":
                for key in ("value", "lo", "hi"):
                    assert pred[c][key][i] == pytest.approx(answers[c][key], rel=1e-12)
                assert suggest.interpolated(pred[c], i) == answers[c]["interpolated"]
    assert not ok[1]                                                                          # NEAR: Lp@13 refused


def test_a_knot_without_a_model_at_a_points_turns_level_leaves_the_point_out(prefitted, tmp_path):
    """Lp@30 interpolates over 10, 16 and 38 GHz, and Lp@38 has no model for two turns (6 rows, too few for a turns level's
    own): a two-turn point is out of the domain -- in the batch path too, beside points that answer --, a single turn
    answers."""
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    assert 2 not in lib.model(STRATUM, "Lp@38").gp._sub and 1 in lib.model(STRATUM, "Lp@38").gp._sub
    two, one = params(101, 4.5, 2.5, 2), params(155, 5.5, 2, 1)       # two turns resonating at about 52 GHz
    answers = {name: q.query(lib, STRATUM, p, ["Lp@30"])["quantities"]["Lp@30"] for name, p in (("two", two), ("one", one))}
    assert answers["two"]["status"] == "out_of_domain" and answers["two"]["reason"].startswith("knot Lp@38: turns=2 has ")
    assert answers["one"]["status"] == "predicted" and answers["one"]["interpolated"]["knots"] == [10.0, 16.0, 38.0, 60.0]
    models = lib.models(STRATUM, ["Lp@30"])
    x = np.array([[p[d] for d in lib.dataset(STRATUM).dims] for p in (two, one)])
    ok, pred = suggest.predict_all(x, models, srf_floor=suggest.srf_floors_for(lib, STRATUM, x, models))
    assert ok.tolist() == [False, True] and np.isnan(pred["Lp@30"]["value"][0])
    assert pred["Lp@30"]["value"][1] == pytest.approx(answers["one"]["value"], rel=1e-12)


def test_suggest_region_and_densify_answer_through_an_interpolated_column(prefitted, tmp_path, monkeypatch):
    root = copy(prefitted, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    fits = counted_fits(monkeypatch)
    note = "Lp@6: answered between its knots 4 and 8 GHz from their models"
    s = suggest.suggest(lib, STRATUM, {"Lp@6": {"target": 2.0e-9, "tol": 0.05}}, "max:Qp_peak", n=3, verify_build=False, pool_size=1024)
    assert s["candidates"] and all(c["predicted"]["Lp@6"]["interpolated"]["knots"] == [2.0, 4.0, 8.0, 10.0] for c in s["candidates"])
    assert all(c["predicted"]["Lp@6"]["lo"] >= 1.9e-9 and c["predicted"]["Lp@6"]["hi"] <= 2.1e-9 for c in s["candidates"])
    assert any(n.startswith(note) for n in s["notes"])
    r = region.region(lib, STRATUM, {"Lp@6": {"min": 1.8e-9, "max": 2.2e-9}}, "max:Qp_peak", trend=("Lp@6", "width_um"), pool_size=2048,
                      max_points=20000)
    assert r["levels"]["robust"]["count"] > 0 and any(n.startswith(note) for n in r["notes"])
    assert all("interpolated" in c["predicted"]["Lp@6"] for c in r["candidates"])
    d = densify.densify(lib, STRATUM, ["Lp@6"], n=3, pool_size=2048, top=200)
    assert len(d["candidates"]) == 3 and all("interpolated" in c["predicted"]["Lp@6"] for c in d["candidates"] if c["predicted"]["Lp@6"]["value"])
    assert any(n.startswith(note) for n in d["notes"])
    assert fits == [] and not list((root / ".cache").glob("model-ind_demo-Lp_at_6-*"))
    exact = suggest.suggest(lib, STRATUM, {"Lp@6": {"target": 2.0e-9, "tol": 0.05}}, "max:Qp_peak", n=3, verify_build=False, pool_size=1024,
                            exact=True)
    assert fits == ["Lp@6"] and not any(n.startswith(note) for n in exact["notes"])
    assert all("interpolated" not in c["predicted"]["Lp@6"] for c in exact["candidates"])


def test_densify_takes_the_bracketing_knots_variance_and_covariance(prefitted, tmp_path):
    lib = q.Library(copy(prefitted, tmp_path / "lib"), limits=LOCAL)
    g = lib.model(STRATUM, "Lp@6").gp
    x = np.array([[150, 5, 2.5, 2], [151, 5.1, 2.5, 2], [140, 4.5, 2.2, 2]], dtype=float)
    mu, sigma = g.predict(x, floor=False)
    _mean, cov = g.posterior_cov(x)
    assert np.allclose(np.sqrt(np.diagonal(cov)), sigma / mu, rtol=1e-9)                      # log space: the diagonal is predict's
    a, b = (lib.model(STRATUM, f"Lp@{k:g}").gp for k in (4, 8))
    t = 0.5
    assert np.allclose(cov, (1 - t) * a.posterior_cov(x)[1] + t * b.posterior_cov(x)[1])
    assert g.log_target and g.available(x).all()


def test_lib_design_predicts_through_an_interpolated_column_and_names_it(prefitted, tmp_path, monkeypatch):
    from ic_opt.blocks.evaluate import evaluate
    from ic_opt.space import Point
    from ic_opt.spec import Spec
    from ic_opt.store import RunStore
    from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
    from tests.ic_opt.test_library_stage import design_spec

    pytest.importorskip("klayout.db")
    root = copy(prefitted, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    d = design_spec("between").model_dump(mode="json")
    d["metrics"].append({"name": "L6", "unit": "H", "device": "ind", "quantity": "Lp", "frequency_hz": 6e9})
    spec = Spec.model_validate(d)
    fits = counted_fits(monkeypatch)
    pipeline = stage.surrogate_pipeline(spec, lib)
    identity = json.loads(pipeline[-1].identity)
    assert identity["between"] == stage.between_identity(lib) and identity["between"]["grid"]["step_ghz"] == 22
    store = RunStore(tmp_path / "p")
    obs = evaluate(spec, [Point({"outer_diameter_um": "155", "width_um": "4.5", "spacing_um": "2.5", "turns": "2"}, "user")],
                   FakeSpectreExecutor(store.root / "sims"), store, pipeline=pipeline, limits=FAKE_HOST)
    assert obs[0].status == "ok" and obs[0].metrics["L6"] > 0
    answer = json.loads((store.project_dir / obs[0].children["ind/nominal"].sim_dir / "predictions.json").read_text())
    assert answer["quantities"]["Lp@6"]["interpolated"]["knots"] == [2.0, 4.0, 8.0, 10.0]
    assert "Lp@6" not in fits and not list((root / ".cache").glob("model-ind_demo-Lp_at_6-*"))
    exact = json.loads(stage.surrogate_pipeline(spec, lib, exact=True)[-1].identity)
    plain = q.Library(copy(prefitted, tmp_path / "plain", grid=None), limits=LOCAL)
    assert "between" not in exact and exact == json.loads(stage.surrogate_pipeline(spec, plain)[-1].identity)


def test_the_lib_design_recipe_says_which_metrics_it_interpolates(prefitted, tmp_path, monkeypatch, capsys):
    from ic_opt.recipe import Run, load_recipe
    from ic_opt.site import Site
    from ic_opt.spec import Spec, load_spec
    from ic_opt.store import RunStore
    from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
    from tests.ic_opt.test_library_stage import design_spec

    pytest.importorskip("klayout.db")
    root = copy(prefitted, tmp_path / "lib")
    d = design_spec("between_recipe").model_dump(mode="json")
    d["metrics"].append({"name": "L6", "unit": "H", "device": "ind", "quantity": "Lp", "frequency_hz": 6e9})
    for exact in (False, True):
        project = tmp_path / f"design_{exact}"
        project.mkdir()
        (project / "spec.yaml").write_text(yaml.safe_dump(Spec.model_validate(d).model_dump(mode="json")), encoding="utf-8")
        site = Site({"local": LOCAL, "lab": FAKE_HOST})
        store = RunStore(project)
        run = Run(project, load_spec(project / "spec.yaml"), store, FakeSpectreExecutor(store.root / "sims"), None, site, site.host("lab"))
        fits = counted_fits(monkeypatch)
        load_recipe("lib_design")(run, library=str(root), budget=4, batch=4, strategy="random", top=2, exact=exact)
        printed = capsys.readouterr().out
        said = "[run] lib_design: ind_demo: Lp@6: answered between its knots 4 and 8 GHz from their models"
        assert (said in printed) is not exact and ("Lp@6" in fits) is exact
        report = json.loads((store.root / "reports" / "lib_design.json").read_text())
        labelled = [("interpolated" in row["library"].get("Lp@6", {})) for row in report["leaders"] if "Lp@6" in row["library"]]
        assert labelled and all(lab is not exact for lab in labelled)


# -- without a grid -------------------------------------------------------------------------------------------------------

def test_without_a_grid_everything_is_as_before(prefitted, tmp_path, monkeypatch):
    root = copy(prefitted, tmp_path / "plain", grid=None)
    lib = q.Library(root, limits=LOCAL)
    fits = counted_fits(monkeypatch)
    assert lib._between(STRATUM, lib.column(STRATUM, "Lp@6")) is None
    models = lib.models(STRATUM, ["Lp@6"])
    assert fits == ["Lp@6"] and models["Lp@6"].gp.__class__ is gp.StratumGP and models["Lp@6"].resonance.knot_hz is None
    answer = q.query(lib, STRATUM, FAR, ["Lp@6"])["quantities"]["Lp@6"]
    assert "interpolated" not in answer and answer == q.query(lib, STRATUM, FAR, ["Lp@6"], exact=True)["quantities"]["Lp@6"]
    assert not list((root / ".cache").glob("between-*"))
    assert "between" not in json.loads(stage.Predict(lib, {"ind": STRATUM}).identity)


def test_the_blocks_take_exact_on_the_command_line(prefitted, tmp_path, monkeypatch):
    root = copy(prefitted, tmp_path / "lib")
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    for flag, interpolated in (("exact=false", True), ("exact=true", False)):
        done = CliRunner().invoke(app, ["call", "lib.query", str(root), f"stratum={STRATUM}", f"params={json.dumps(FAR)}",
                                        "quantities=Lp@6", flag])
        assert done.exit_code == 0, done.output
        assert ("interpolated" in json.loads(done.stdout)["quantities"]["Lp@6"]) is interpolated
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    assert doc["frequency_grid"] == GRID
