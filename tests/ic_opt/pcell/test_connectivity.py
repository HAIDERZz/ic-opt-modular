"""The connectivity check reads a written GDS and reports which port labels share a net."""

from __future__ import annotations

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell.connectivity import nets
from tests.ic_opt.pcell.test_golden import CASES, build


def expected_topology(name: str, found: dict[str, int]) -> None:
    ground = {found[g] for g in found if g.startswith("G")}
    assert len(ground) == 1, found                                             # every stub sits on the one M1 ring
    primary = {found["P1"], found["N1"]}
    assert len(primary) == 1 and primary != ground, found                      # P1 and N1 are one winding, not ground
    if "P2" in found:
        secondary = {found["P2"], found["N2"]}
        assert len(secondary) == 1 and secondary != primary and secondary != ground, found
        if "CTP" in found:
            assert found["CTP"] in primary and found["CTS"] in secondary, found
    if "CT" in found:
        assert found["CT"] in primary, found
    assert 0 not in found.values(), found                                      # every label lands on metal


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_golden_has_the_expected_nets(name, tmp_path):
    expected_topology(name, nets(build(name, tmp_path), "demo_6m"))
