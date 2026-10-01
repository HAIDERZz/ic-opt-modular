"""N-48: ``touchstone.read`` takes Touchstone version 2 files -- the keywords, ``Lower`` / ``Upper`` triangles, the two-port
data order, ``[Reference]`` -- and gives the same full row-major matrix as a version 1 file of the same network;
``write`` stays version 1."""

from __future__ import annotations

import numpy as np
import pytest

from ic_opt.em import touchstone
from ic_opt.em.touchstone import TouchstoneError

FREQS_GHZ = [1.0, 2.5]


def network(n: int, seed: int = 0) -> np.ndarray:
    """A symmetric (reciprocal) [F, n, n] complex matrix with fixed numbers."""
    rng = np.random.default_rng(seed)
    a = rng.uniform(-0.9, 0.9, (len(FREQS_GHZ), n, n)) + 1j * rng.uniform(-0.9, 0.9, (len(FREQS_GHZ), n, n))
    return (a + a.transpose(0, 2, 1)) / 2


def pair(v: complex) -> str:
    return f"{v.real!r} {v.imag!r}"


def v2_text(s: np.ndarray, *, matrix_format: str = "Full", order: str | None = None, reference: str | None = None,
            unit: str = "GHz", extra: list[str] = (), end: bool = True, frequencies: int | None = None) -> str:
    """A version 2 file of ``s`` as the specification writes it: one keyword per line, rows of the chosen triangle (or the
    full matrix, in ``order`` for a 2-port), a line per matrix row."""
    n = s.shape[1]
    lines = ["! a version 2 file", "[Version] 2.0", f"# {unit} S RI R 50", f"[Number of Ports] {n}"]
    if order:
        lines.append(f"[Two-Port Data Order] {order}")
    lines.append(f"[Number of Frequencies] {frequencies if frequencies is not None else len(FREQS_GHZ)}")
    if reference is not None:
        lines.append(f"[Reference] {reference}")
    lines.append(f"[Matrix Format] {matrix_format}")
    lines.extend(extra)
    lines.append("[Network Data]")
    for f, m in zip(FREQS_GHZ, s, strict=True):
        rows = []
        for i in range(n):
            if matrix_format == "Lower":
                cols = range(i + 1)
            elif matrix_format == "Upper":
                cols = range(i, n)
            else:
                cols = range(n)
            rows.append(" ".join(pair(m[i, j]) for j in cols))
        if n == 2 and matrix_format == "Full" and order == "21_12":
            rows = [" ".join(pair(v) for v in (m[0, 0], m[1, 0], m[0, 1], m[1, 1]))]
        lines.append(f"{f!r} {rows[0]}")
        lines.extend("  " + r for r in rows[1:])
    if end:
        lines.append("[End]")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("matrix_format", ["Full", "Lower", "Upper"])
@pytest.mark.parametrize("n", [1, 3, 4])
def test_a_version_2_file_reads_as_the_full_matrix(tmp_path, n, matrix_format):
    s = network(n)
    path = tmp_path / f"dev.s{n}p"
    path.write_text(v2_text(s, matrix_format=matrix_format), encoding="utf-8")
    ts = touchstone.read(path)
    assert ts.n_ports == n and ts.unit == "GHz" and ts.fmt == "RI" and ts.z0 == 50.0
    np.testing.assert_allclose(ts.freqs, np.array(FREQS_GHZ) * 1e9)
    np.testing.assert_array_equal(ts.s, s)


def test_a_triangle_reads_the_same_as_the_version_1_file_the_writer_makes(tmp_path):
    s = network(3, seed=3)
    (tmp_path / "v2.s3p").write_text(v2_text(s, matrix_format="Lower"), encoding="utf-8")
    ts2 = touchstone.read(tmp_path / "v2.s3p")
    v1 = touchstone.write(tmp_path / "v1.s3p", ts2)              # version 1, full matrix
    text = v1.read_text(encoding="utf-8")
    assert "[" not in text and text.splitlines()[0] == "# GHz S RI R 50"
    ts1 = touchstone.read(v1)
    np.testing.assert_array_equal(ts1.s, ts2.s)
    np.testing.assert_array_equal(ts1.freqs, ts2.freqs)


@pytest.mark.parametrize("order", ["12_21", "21_12"])
def test_a_two_port_follows_its_data_order(tmp_path, order):
    s = network(2, seed=1)
    s[:, 0, 1] = s[:, 0, 1] + 0.05          # not reciprocal, so the order is visible
    path = tmp_path / "two.s2p"
    path.write_text(v2_text(s, order=order), encoding="utf-8")
    np.testing.assert_array_equal(touchstone.read(path).s, s)


def test_a_version_2_two_port_without_its_data_order_is_refused(tmp_path):
    path = tmp_path / "two.s2p"
    path.write_text(v2_text(network(2)), encoding="utf-8")
    with pytest.raises(TouchstoneError, match=r"\[Two-Port Data Order\]"):
        touchstone.read(path)


def test_a_version_1_two_port_keeps_the_s11_s21_s12_s22_order(tmp_path):
    s = network(2, seed=2)
    s[:, 1, 0] = s[:, 1, 0] - 0.1
    path = tmp_path / "one.s2p"
    lines = ["# GHz S RI R 50"] + [f"{f!r} " + " ".join(pair(v) for v in (m[0, 0], m[1, 0], m[0, 1], m[1, 1]))
                                   for f, m in zip(FREQS_GHZ, s, strict=True)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    np.testing.assert_array_equal(touchstone.read(path).s, s)


def test_one_common_reference_replaces_the_option_line_and_differing_ones_are_refused(tmp_path):
    s = network(3)
    path = tmp_path / "ref.s3p"
    path.write_text(v2_text(s, reference="75 75 75"), encoding="utf-8")
    assert touchstone.read(path).z0 == 75.0
    path.write_text(v2_text(s, reference="75\n75\n75"), encoding="utf-8")      # values over several lines
    assert touchstone.read(path).z0 == 75.0
    path.write_text(v2_text(s, reference="50 75 50"), encoding="utf-8")
    with pytest.raises(TouchstoneError, match="differ per port"):
        touchstone.read(path)
    path.write_text(v2_text(s, reference="50 50"), encoding="utf-8")
    with pytest.raises(TouchstoneError, match="lists 2 impedances for 3 ports"):
        touchstone.read(path)


def test_information_and_noise_data_are_passed_over(tmp_path):
    s = network(3)
    path = tmp_path / "info.s3p"
    body = v2_text(s, matrix_format="Lower", extra=["[Begin Information]", "made by a test", "[End Information]"], end=False)
    body += "[Noise Data]\n1 2 0.5 10 50\n2 3 0.6 20 50\n[End]\n"
    path.write_text(body, encoding="utf-8")
    np.testing.assert_array_equal(touchstone.read(path).s, s)


@pytest.mark.parametrize(("line", "message"), [
    ("[Mixed-Mode Order] D1,2 C1,2", r"\[Mixed-Mode Order\] is not supported"),
    ("[Something New] 1", r"\[Something New\] is not supported"),
    ("[Matrix Format] Diagonal", "not Full, Lower or Upper"),
])
def test_keywords_this_reader_does_not_take_are_refused(tmp_path, line, message):
    path = tmp_path / "bad.s3p"
    path.write_text(v2_text(network(3)).replace("[Matrix Format] Full", line), encoding="utf-8")
    with pytest.raises(TouchstoneError, match=message):
        touchstone.read(path)


def test_the_port_count_keyword_must_agree_with_the_suffix(tmp_path):
    path = tmp_path / "dev.s4p"
    path.write_text(v2_text(network(3)), encoding="utf-8")
    with pytest.raises(TouchstoneError, match=r"\[Number of Ports\] 3 but the suffix names 4"):
        touchstone.read(path)


def test_a_short_triangle_is_refused_by_the_token_count(tmp_path):
    s = network(3)
    path = tmp_path / "short.s3p"
    text = v2_text(s, matrix_format="Lower")
    path.write_text("\n".join(text.splitlines()[:-2]) + "\n[End]\n", encoding="utf-8")     # one matrix row short
    with pytest.raises(TouchstoneError, match="not a multiple of 13 .3-port, lower."):
        touchstone.read(path)


def test_the_emx_header_check_passes_over_the_version_keyword(tmp_path):
    path = tmp_path / "emx.s2p"
    path.write_text("! EMX was run on a host as: emx --format=touchstone\n[Version] 2.0\n# Hz S RI R 50\n[Number of Ports] 2\n"
                    "[Two-Port Data Order] 12_21\n[Matrix Format] Full\n[Network Data]\n1e9 0.1 0 0.9 0 0.9 0 0.1 0\n[End]\n",
                    encoding="utf-8")
    assert touchstone.header_issues(path, expected_ports=2, z0=50.0) == []
