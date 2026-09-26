"""N-21: ``localpath.literal``, the extended-length path on a Windows controller and the path itself everywhere else.

The Windows branch is forced with ``windows=True`` and checked as text. That Win32 then reaches a name ending in a dot or
a space through it cannot be verified on Linux; the Windows acceptance re-run does that."""

import ntpath
import os

from ic_opt.localpath import literal

DSPF = r"amap\__dspf_information__."          # in every Maestro export: a name that ends with a dot


def test_on_posix_a_path_is_its_own_string(tmp_path):
    for path in (tmp_path / "amap" / "__dspf_information__.", "netlist/trailing space ", "rel/../x"):
        assert literal(path, windows=False) == os.fspath(path)
    assert literal(tmp_path / "x.") == literal(tmp_path / "x.", windows=os.name == "nt")     # without windows=: os.name


def test_on_windows_an_absolute_path_gets_the_extended_length_prefix_once():
    staging = rf"D:\proj\.icopt\decks\.staging\cg_nf\{DSPF}"
    assert literal(staging, windows=True) == "\\\\?\\" + staging
    assert literal("D:/proj/netlist/trailing space ", windows=True) == r"\\?\D:\proj\netlist\trailing space "
    assert literal(literal(staging, windows=True), windows=True) == "\\\\?\\" + staging          # never twice
    assert literal(r"D:\proj\.\decks\tb\..\bundle", windows=True) == r"\\?\D:\proj\decks\bundle"  # behind \\?\ they'd be names


def test_on_windows_a_unc_path_gets_the_unc_form():
    assert literal(rf"\\nas\lab\proj\{DSPF}", windows=True) == rf"\\?\UNC\nas\lab\proj\{DSPF}"
    assert literal(r"\\?\UNC\nas\lab\proj", windows=True) == r"\\?\UNC\nas\lab\proj"


def test_on_windows_a_relative_path_is_made_absolute_before_the_prefix(tmp_path, monkeypatch):
    """Joined to the current directory as ntpath spells it (``C:\\...`` on Windows; off Windows, this directory in
    backslashes), and the path's own names keep their trailing dot. A drive-relative ``D:name`` takes that drive's
    current directory from Win32, which only Windows can show."""
    monkeypatch.chdir(tmp_path)
    here = ntpath.abspath(os.curdir)
    assert literal(rf"decks\{DSPF}", windows=True) == rf"\\?\{here}\decks\{DSPF}"
    assert literal("decks/../sims", windows=True) == rf"\\?\{here}\sims"
