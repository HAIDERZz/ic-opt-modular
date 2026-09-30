"""T18.2B section 2: ``ic_opt.library.link`` resolves a spec's library devices -- the index of the device's table at its
frequency, the index's rows on the device's variables' grids read in SI units, the space's table of the combinations they
occupy -- refuses what a user can fix, and keeps one resolution per process."""

from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal

import pytest
import yaml

from ic_opt import space
from ic_opt.library import cache, index, link
from ic_opt.library import query as q
from ic_opt.spec import Topology
from tests.ic_opt.library_device_fixtures import (
    F0,
    SI_GRID,
    STRATUM,
    library_device,
    library_spec,
    xfm_library,
)


@pytest.fixture(autouse=True)
def fresh():
    link.clear()
    yield
    link.clear()


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return xfm_library(tmp_path_factory.mktemp("link") / "lib")


def texts(spec, cell) -> dict[str, str]:
    """The device variables' grid texts of a combination (level indices over xfmr.Lp, xfmr.Ls, xfmr.k)."""
    out = {}
    for name, k in zip(("xfmr.Lp", "xfmr.Ls", "xfmr.k"), cell, strict=True):
        v = next(v for v in spec.variables if v.name == name)
        lower, unit = space.parse_scalar(v.lower)
        out[name] = space.format_value(lower + Decimal(k) * space.parse_scalar(v.step)[0], unit)
    return out


def test_the_device_s_table_is_the_index_s_rows_on_its_variables_grids(root):
    spec = library_spec(root)
    linked = link.resolve(spec)["xfmr"]
    reference = index.table(index.build(q.Library(root, calibrate=False), STRATUM, F0), SI_GRID)
    assert (linked.names, linked.coordinates, linked.ports) == (("xfmr.Lp", "xfmr.Ls", "xfmr.k"), ("Lp", "Ls", "k"),
                                                                 ("P1", "N1", "P2", "N2"))
    assert {cell: [(r.part, r.obs_id) for r in rows] for cell, rows in linked.table.cells.items()} == {
        cell: [(r.part, r.obs_id) for r in rows] for cell, rows in reference.cells.items()}
    assert 20 < len(reference.cells) < 200 and reference.size() == 16 * 16 * 6
    (table,) = space.tables(spec)
    assert table is linked.space_table and table.names == linked.names and table.levels == tuple(reference.levels())
    assert table.title == "device xfmr (library rows)" and space.grid_size(spec) == 6 * len(reference.cells)
    rows = sum(len(r) for r in reference.cells.values())
    assert linked.facts() == {"stratum": STRATUM, "frequency_hz": F0, "srf_margin": 1.25, "prefer": "max:Qmin", "rows": rows,
                              "combinations": len(reference.cells), "of": 1536}
    assert link.sentence(spec, "xfmr") == (f"xfm_demo at 20 GHz: {rows} rows on the grid in {len(reference.cells)} of 1536 "
                                           "combinations, prefer max:Qmin")
    assert link.summary(spec) == {"xfmr": linked.facts()}
    assert linked.topologies == {"xfm": Topology(drives=[("P1", "N1"), ("N2", "P2")])}     # the part's, as it measures rows


def test_a_point_takes_the_best_row_of_its_combination_and_none_off_the_table(root):
    spec = library_spec(root)
    linked = link.resolve(spec)["xfmr"]
    cell = max(linked.table.cells, key=lambda c: (len(linked.table.cells[c]), c))          # a combination of several rows
    candidates = linked.table.cells[cell]
    params = {"F": "20", **texts(spec, cell)}
    row = link.row_for(spec, "xfmr", params)
    assert len(candidates) > 1 and row == candidates[0]
    assert row.values["Qmin"] == max(r.values["Qmin"] for r in candidates)                 # prefer: max:Qmin, the default
    assert [linked.table.axes[j].level(row.values[c]) for j, c in enumerate(("Lp", "Ls", "k"))] == list(cell)
    empty = next((a, b, c) for a in range(16) for b in range(16) for c in range(6) if (a, b, c) not in linked.table.cells)
    assert link.row_for(spec, "xfmr", {"F": "20", **texts(spec, empty)}) is None
    for off in ({"xfmr.Lp": "155p"}, {"xfmr.Lp": "0.2n"}, {"xfmr.Lp": "950p"}, {"xfmr.k": "x"}):
        assert link.row_for(spec, "xfmr", {**params, **off}) is None, off
    message = link.missing(spec, "xfmr", {"F": "20", **texts(spec, empty)})
    near = space.snap(spec, [20.0, *(float(space.parse_scalar(t)[0]) for t in texts(spec, empty).values())])
    assert message.startswith("device xfmr: xfmr.Lp=") and "is not a combination of its library table (xfm_demo at 20 GHz" in message
    assert message.endswith("the nearest one is " + " ".join(f"{n}={near[n]}" for n in linked.names))
    assert link.row_for(spec, "xfmr", {n: near[n] for n in linked.names}) is not None


def test_the_margin_prefer_and_the_topology_go_to_the_index_the_table_and_the_rows_measurement(root, tmp_path):
    base = link.resolve(library_spec(root))["xfmr"]
    wide = link.resolve(library_spec(root, device=library_device(root, srf_margin=1.9, prefer="max:Qp")))["xfmr"]
    assert wide.index.srf_margin == 1.9 and len(wide.index.rows) < len(base.index.rows) and wide.table.prefer == "max:Qp"
    stated = library_device(root) | {"topology": {"drives": [["P1", "N1"], ["N2", "P2"]], "low_freq_max_hz": 2e9}}
    assert link.resolve(library_spec(root, device=stated))["xfmr"].topologies == {}         # the device's own is used
    relative = tmp_path / "relative"
    shutil.copytree(root, relative, ignore=shutil.ignore_patterns(".cache"))
    doc = yaml.safe_load((relative / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"][STRATUM]["low_freq_max_hz"] = "relative"
    (relative / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert link.resolve(library_spec(relative))["xfmr"].topologies["xfm"].low_freq_max_hz == "relative"


def test_the_identity_is_the_index_s_content_the_grid_and_prefer_not_where_the_library_sits(root, tmp_path):
    spec = library_spec(root)
    linked = link.resolve(spec)["xfmr"]
    assert json.loads(link.identity(spec)) == {"xfmr": {"stratum": STRATUM, "index": linked.index.key, "prefer": "max:Qmin",
                                                        "grid": {c: list(v) for c, v in SI_GRID.items()}}}
    moved = tmp_path / "moved"
    shutil.copytree(root, moved, ignore=shutil.ignore_patterns(".cache"))
    elsewhere = library_spec(moved)
    assert link.identity(elsewhere) == link.identity(spec) and elsewhere.fingerprint() == spec.fingerprint()
    finer = library_spec(root, grid={"xfmr.Lp": ("150p", "900p", "10p"), "xfmr.Ls": ("150p", "900p", "50p"),
                                     "xfmr.k": ("0.3", "0.8", "0.1")})
    ranked = library_spec(root, device=library_device(root, prefer="max:Qp"))
    assert len({link.identity(spec), link.identity(finer), link.identity(ranked)}) == 3
    assert link.identity(library_spec(root, grid={"xfmr.Lp": ("0.15n", "0.9n", "0.05n"), "xfmr.Ls": ("150p", "900p", "50p"),
                                                  "xfmr.k": ("0.3", "0.8", "0.1")})) == link.identity(spec)   # the same grid in SI


def test_each_refusal_says_what_to_change(root, tmp_path):
    with pytest.raises(ValueError, match=r"device xfmr: no library.yaml at .*nowhere \(library root: the directory holding "
                                         r"library.yaml, on the machine running ic-opt\)"):
        link.resolve(library_spec(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match=r"device xfmr: the library at .* has no stratum 'xfm_other'; its strata: xfm_demo"):
        link.resolve(library_spec(root, device=library_device(root, stratum="xfm_other")))
    renamed = library_device(root)
    renamed["variables"] = {"Lp": "xfmr.Lp", "Ls": "xfmr.Ls", "kk": "xfmr.k"}
    with pytest.raises(ValueError, match=r"device xfmr: kk is no column of the index of xfm_demo at 20 GHz; its columns: Lp, Qp, "
                                         r"Ls, Qs, k, Lp_lf, .*, Qmin, area"):
        link.resolve(library_spec(root, device=renamed))
    tapped = library_device(root) | {"ports": ["P1", "N1", "P2", "N2", "CTP"]}
    with pytest.raises(link.LinkError, match=r"device xfmr: its ports \['P1', 'N1', 'P2', 'N2', 'CTP'\] are not the table's "
                                         r"\['P1', 'N1', 'P2', 'N2'\] \(xfm_demo\)"):
        link.resolve(library_spec(root, device=tapped, terminals=("P1", "N1", "P2", "N2", "CTP")))
    far = {"xfmr.Lp": ("1n", "2n", "0.1n"), "xfmr.Ls": ("1n", "2n", "0.1n"), "xfmr.k": ("0.85", "0.95", "0.05")}
    with pytest.raises(ValueError, match=r"device xfmr: no row of xfm_demo at 20 GHz lies on the grid of its variables .* -- "
                                         r"Lp: the index holds 2.557e-10 to 1.107e-09, xfmr.Lp spans 1n to 2n; Ls: .*; "
                                         r"k: the index holds 0.437\d* to 0.77\d*, xfmr.k spans 0.85 to 0.95"):
        link.resolve(library_spec(root, grid=far))
    with pytest.raises(ValueError, match=r"device xfmr: prefer 'max:Qmn': expected max:<column> or min:<column>"):
        link.resolve(library_spec(root, device=library_device(root, prefer="max:Qmn")))
    with pytest.raises(ValueError, match=r"device xfmr: xfm_demo: no part is swept at 90 GHz"):
        link.resolve(library_spec(root, device=library_device(root, frequency_hz=90e9)))
    with pytest.raises(KeyError):
        link.row_for(library_spec(root), "nothing", {})


def test_a_process_resolves_a_device_once_and_clear_forgets_it(root, tmp_path, monkeypatch):
    builds = []
    real = index.build
    monkeypatch.setattr(index, "build", lambda *a, **k: builds.append(a[1]) or real(*a, **k))
    spec = library_spec(root)
    first = link.resolve(spec)["xfmr"]
    cell = first.table.levels()[0]
    for _ in range(3):
        space.snap(spec, [20.0, 300.0, 300.0, 0.5])
        link.row_for(spec, "xfmr", {"F": "20", **texts(spec, cell)})
        space.check(spec, {"F": "20", **texts(spec, cell)})
    assert link.resolve(library_spec(root))["xfmr"] is first and builds == [STRATUM]        # a spec loaded again: kept too
    grown = tmp_path / "grown"
    shutil.copytree(root, grown, ignore=shutil.ignore_patterns(".cache"))
    before = link.resolve(library_spec(grown))["xfmr"]
    doc = yaml.safe_load((grown / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"][STRATUM]["quantities"]["Lp"]["srf_margin"] = 1.9                  # the library changes under the run
    (grown / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert link.resolve(library_spec(grown))["xfmr"] is before and len(builds) == 2     # the run keeps the table it has
    link.clear()
    after = link.resolve(library_spec(grown))["xfmr"]
    assert after is not before and after.index.key != before.index.key and len(builds) == 3     # the next process sees it
    assert link.resolve(spec)["xfmr"] is not first and len(builds) == 4
    assert link.resolve(library_spec(root).model_copy(update={"devices": []})) == {} and link.tables(spec) != []


def test_a_library_that_cannot_be_written_caches_where_cache_locate_puts_it(root, tmp_path, monkeypatch):
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("read-only directories are not read-only here")
    shared = tmp_path / "shared"
    shutil.copytree(root, shared, ignore=shutil.ignore_patterns(".cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths = [shared, *shared.rglob("*")]
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        linked = link.resolve(library_spec(shared))["xfmr"]
        assert linked.table.cells and not (shared / ".cache").exists()
        assert linked.library.cache.note and "cannot be written" in linked.library.cache.note
        assert sorted(p.name.split("-")[0] for p in cache.fallback(shared).glob("*.json")) == ["anchors", "dataset"]
    finally:
        for path in paths:
            path.chmod(0o755 if path.is_dir() else 0o644)
