"""T16.5 R-20: where a library keeps its cache files -- its own .cache, a cache_dir= of the caller's, or, when its own
cannot be written, ~/.cache/ic-opt/<key>/ with a note in every answer; the files in its own .cache are read in every case.
N-4: two processes that need the same uncached model at once compute it once."""
from __future__ import annotations

import contextlib
import hashlib
import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ic_opt import _lock
from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.library import gp
from ic_opt.library import query as q
from tests.ic_opt.fakes import FAKE_HOST
from tests.ic_opt.library_fixtures import LOCAL, build_library, params, use_site
from tests.ic_opt.test_library_query import count_fits, fresh_copy

TARGETS = {"Lp_lf": {"min": 1.0e-9, "max": 1.3e-9}}
HOLD_S = 1.5                                         # what each hold-out takes at least in the N-4 test: the other process arrives meanwhile


@pytest.fixture(scope="module")
def owned(tmp_path_factory) -> Path:
    """The synthetic library as its owner leaves it: Lp_lf's dataset, calibration and model in its own .cache."""
    root = build_library(tmp_path_factory.mktemp("owned"))
    q.Library(root, limits=LOCAL).model("ind_demo", "Lp_lf")
    return root


@pytest.fixture
def shared(owned, tmp_path, monkeypatch):
    """A copy of ``owned`` that this user cannot write to -- every directory under it read-only, its .cache included --
    and a home directory of the test's own."""
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("read-only directories by POSIX permissions, which the superuser ignores")
    root = shutil.copytree(owned, tmp_path / "shared")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    dirs = [root, *sorted(p for p in root.rglob("*") if p.is_dir())]
    for d in dirs:
        d.chmod(0o555)
    yield root
    for d in dirs:
        d.chmod(0o755)


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def stems(directory: Path) -> list[str]:
    """The cache files of ind_demo in ``directory`` by kind and column; the fits' lock files (N-4) aside."""
    return sorted(p.name.rsplit("-", 1)[0] for p in directory.glob("*-ind_demo-*") if p.suffix != ".lock")


def test_a_library_that_cannot_be_written_caches_under_home_and_says_so(shared, tmp_path, monkeypatch):
    """The owner's files still answer (Lp_lf's model loads, nothing refits it); what has to be computed -- Qp_peak's
    calibration and model -- goes to ~/.cache/ic-opt/<the first 16 hex digits of sha256(resolved root)>/, and the answer
    says so. Nothing under the library root changes."""
    before = tree(shared)
    home = tmp_path / "home" / ".cache" / "ic-opt" / hashlib.sha256(str(shared.resolve()).encode()).hexdigest()[:16]
    lib = q.Library(shared, limits=LOCAL)
    assert lib.cache.directory == home and lib.cache.also == (shared / ".cache",)
    (note,) = lib.notes
    assert f"{shared / '.cache'} cannot be written" in note and str(home) in note
    fits = count_fits(monkeypatch)
    answer = q.query(lib, "ind_demo", params(155, 4.5, 2.5, 2), ["Lp_lf", "Qp_peak"])
    assert answer["notes"] == [note] and {a["status"] for a in answer["quantities"].values()} == {"predicted"}
    assert lib.dataset("ind_demo").cache == "hit" and fits == ["per_nt"] * 6       # Qp_peak: five hold-out fits, then the model
    assert stems(home) == ["calibration-ind_demo-Qp_peak", "model-ind_demo-Qp_peak"]
    assert tree(shared) == before
    fits.clear()
    again = q.Library(shared, limits=LOCAL)                                          # a new process: both models load
    assert again.model("ind_demo", "Qp_peak").calibration == lib.model("ind_demo", "Qp_peak").calibration and fits == []


def test_every_library_answer_carries_the_note(shared, tmp_path, monkeypatch):
    """lib.load (per stratum), lib.coverage, lib.query, lib.suggest, lib.region and lib.densify: the note comes first in
    ``notes``, the command line prints it too. Only Lp_lf is asked for, so every model is the owner's: nothing is fitted."""
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    note = q.Library(shared).notes[0]
    fits = count_fits(monkeypatch)
    answers = {
        "lib.load": library_blocks.load(shared)["ind_demo"],
        "lib.coverage": library_blocks.coverage(shared, "ind_demo"),
        "lib.query": library_blocks.query(shared, "ind_demo", params(155, 4.5, 2.5, 2), "Lp_lf"),
        "lib.suggest": library_blocks.suggest(shared, "ind_demo", TARGETS, n=1, pool_size=256, verify_build=False),
        "lib.region": library_blocks.region(shared, "ind_demo", TARGETS, pool_size=256, n=1, workers=1),
        "lib.densify": library_blocks.densify(shared, "ind_demo", 1, quantities="Lp_lf", pool_size=256, top=16, workers=1),
    }
    assert {name: a["notes"][0] for name, a in answers.items()} == dict.fromkeys(answers, note) and fits == []
    printed = CliRunner().invoke(app, ["call", "lib.coverage", str(shared), "stratum=ind_demo"])
    assert printed.exit_code == 0, printed.output
    assert json.loads(printed.stdout)["notes"] == [note]


def test_cache_dir_takes_every_cache_file_the_fit_workers_too(owned, tmp_path, monkeypatch):
    """cache_dir= puts the dataset, calibrations and models there -- those the spawned fit workers write as well -- and
    nothing into the library's own .cache; a writable library says nothing. The command line takes it as a parameter
    of any lib block, and a Library opened with another cache directory is refused rather than silently switched."""
    root = fresh_copy(owned, tmp_path / "lib")                                       # no .cache
    elsewhere = tmp_path / "elsewhere"
    lib = q.Library(root, limits=FAKE_HOST, cache_dir=elsewhere)
    assert lib.cache.directory == elsewhere.resolve() and lib.notes == []
    lib.models("ind_demo", ["Qp_peak", "SRF_p"], workers=2, threads=2)
    assert stems(elsewhere) == ["calibration-ind_demo-Qp_peak", "calibration-ind_demo-SRF_p", "dataset-ind_demo",
                                "model-ind_demo-Qp_peak", "model-ind_demo-SRF_p"]
    assert not (root / ".cache").exists()
    use_site(monkeypatch, tmp_path / "absent.yaml")                                  # loading needs no site.yaml
    fits = count_fits(monkeypatch)
    printed = CliRunner().invoke(app, ["call", "lib.query", str(root), "stratum=ind_demo", f"params={json.dumps(params(155, 4.5, 2.5, 2))}",
                                       "quantities=Qp_peak,SRF_p", f"cache_dir={elsewhere}"])
    assert printed.exit_code == 0, printed.output
    body = json.loads(printed.stdout)
    assert fits == [] and body["notes"] == [] and {a["status"] for a in body["quantities"].values()} <= {"predicted", "above_sweep"}
    assert library_blocks.coverage(lib, "ind_demo", cache_dir=str(elsewhere))["rows"] == 105
    with pytest.raises(ValueError, match=r"is not the cache directory of the library given"):
        library_blocks.coverage(lib, "ind_demo", cache_dir=str(tmp_path / "third"))
    assert not (root / ".cache").exists()


# -- N-4: one process computes a model, the others wait for it ------------------------------------------------------------

def calibrate_marked(root: str, marker: str, start, guarded: bool) -> dict:
    """In a spawned process: Qp_peak's model of the library at ``root``, every hold-out noting this process in ``marker``
    and taking HOLD_S longer; the processes set off together from ``start``. Unguarded: N-4's lock is replaced by nothing,
    as it was before N-4."""
    original = gp.holdout

    def holdout(*args, **kwargs):
        with open(marker, "a", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n")
        time.sleep(HOLD_S)
        return original(*args, **kwargs)

    gp.holdout = holdout
    if not guarded:
        _lock.waiting_lock = lambda path: contextlib.nullcontext(False)
    lib = q.Library(root, limits=LOCAL)                                                # one fit worker: the fit runs here
    lib.dataset("ind_demo")                                                            # read from its cache before the start
    start.wait(timeout=120)
    return lib.model("ind_demo", "Qp_peak").calibration


@pytest.mark.parametrize("guarded", [True, False], ids=["locked", "unlocked"])
def test_two_processes_that_need_the_same_model_compute_it_once(owned, tmp_path, guarded):
    """Two spawned processes ask for the same uncached model at the same moment. The first to take the lock next to the
    calibration file computes the hold-out and the model; the other waits for it and loads them: one hold-out between the
    two, both with the same calibration. The control without the lock computes it in both."""
    root = fresh_copy(owned, tmp_path / "lib")
    q.Library(root, limits=LOCAL).dataset("ind_demo")                                   # cached: neither process builds it
    marker = tmp_path / "holdouts.txt"
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        start = manager.Barrier(2)
        jobs = [pool.submit(calibrate_marked, str(root), str(marker), start, guarded) for _ in range(2)]
        first, second = (job.result(timeout=300) for job in jobs)
    computed = marker.read_text(encoding="utf-8").split()
    assert first == second and first["source"] == "holdout 5x20%"
    if guarded:
        (calibration,) = (root / ".cache").glob("calibration-ind_demo-Qp_peak-*.json")
        assert len(computed) == 1 and (root / ".cache" / f"{calibration.name}.lock").is_file()
    else:
        assert len(computed) == 2 and len(set(computed)) == 2                         # each process computed it
