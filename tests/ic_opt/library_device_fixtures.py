"""A synthetic transformer library whose every curve is declared, and circuit specs whose transformer comes from it (T18.2B):
the library tests' fixtures (``library_fixtures.build_xfm_library``: 224 rows, ports P1 N1 P2 N2, swept to 60 GHz) with the
curves Lp, Qp, Ls and Qs declared beside k, so that the index at a working frequency has every column a device maps."""

from __future__ import annotations

from pathlib import Path

import yaml

from ic_opt.spec import Spec
from tests.ic_opt.fakes import minimal_spec
from tests.ic_opt.library_fixtures import build_xfm_library

STRATUM = "xfm_demo"
F0 = 20e9                                                            # the working frequency of the specs below
GRID = {"xfmr.Lp": ("150p", "900p", "50p"), "xfmr.Ls": ("150p", "900p", "50p"), "xfmr.k": ("0.3", "0.8", "0.1")}
SI_GRID = {"Lp": (1.5e-10, 9e-10, 5e-11), "Ls": (1.5e-10, 9e-10, 5e-11), "k": (0.3, 0.8, 0.1)}     # the same grid in SI units
NETLIST = ('simulator lang=spectre\ninclude "/pdk/models.scs" section=tt\nparameters temperature=27 F=20\n'
           'NPORT0 ( p 0 n 0 s1 0 s2 0 ) nport file="/old/xfmr.s4p" interp=bbspice\ntran tran stop=10n\n')


def xfm_library(root: Path) -> Path:
    """``build_xfm_library`` with Lp, Qp, Ls and Qs declared at 10 GHz beside k: every curve of the pair."""
    build_xfm_library(root)
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"][STRATUM]["quantities"].update({c: {"anchors_ghz": [10]} for c in ("Lp", "Qp", "Ls", "Qs")})
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def library_device(root: Path, **library) -> dict:
    return {"id": "xfmr", "ports": ["P1", "N1", "P2", "N2"], "variables": {"Lp": "xfmr.Lp", "Ls": "xfmr.Ls", "k": "xfmr.k"},
            "library": {"root": str(root), "stratum": STRATUM, "frequency_hz": F0, **library}}


def library_spec_dict(root: Path, *, export: Path | None = None, terminals=("P1", "N1", "P2", "N2"), device: dict | None = None,
                      grid: dict | None = None, **overrides) -> dict:
    """A circuit spec (one testbench, circuit variable F) whose transformer ``xfmr`` comes from the library at ``root``:
    its variables Lp, Ls and k on ``grid`` (default ``GRID``), bound into NPORT0 with ``terminals``, device metrics Lp, k
    and Qp at 20 GHz. ``export``: the Maestro export root (default: a path nothing reads)."""
    grid = grid or GRID
    d = minimal_spec()
    d["project"] = "lib_xfmr"
    d["testbenches"] = [{"id": "tb", "maestro_point_root": str(export or "/x"), "virtuoso_library": "l", "cell": "c", "test_name": "t"}]
    d["devices"] = [device or library_device(root)]
    d["variables"] = [{"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"}] + [
        {"name": name, "kind": "continuous_step", "lower": lo, "upper": hi, "step": st} for name, (lo, hi, st) in grid.items()]
    d["bindings"] = [{"testbench": "tb", "instance": "NPORT0", "device": "xfmr", "terminals": list(terminals)}]
    d["metrics"] = [{"name": "NF", "unit": "dB", "expression": 'value(getData("NF"))'},
                    {"name": "Lp", "unit": "H", "device": "xfmr", "quantity": "Lp", "frequency_hz": F0},
                    {"name": "k", "unit": "1", "device": "xfmr", "quantity": "k", "frequency_hz": F0},
                    {"name": "Qp", "unit": "1", "device": "xfmr", "quantity": "Qp", "frequency_hz": F0}]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9 dB"}]
    d["objective"] = {"direction": "minimize", "expression": "NF"}
    d["budget"] = {"max_simulations": 200}
    d.update(overrides)
    return d


def library_spec(root: Path, **kwargs) -> Spec:
    return Spec.model_validate(library_spec_dict(root, **kwargs))
