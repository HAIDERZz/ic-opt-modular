from pathlib import Path

import pytest

from ic_opt import deck as deck_module
from ic_opt.deck import Deck
from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.localpath import literal
from ic_opt.space import Point
from ic_opt.stages import spectre_chain, spectre_pipeline
from ic_opt.stages.spectre_chain import Spectre
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, make_spec, minimal_spec

TEMPLATE = "simulator lang=spectre\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n"


def run_chain(tmp_path: Path, executor, corner=None, spec=None, deck=None):
    spec = spec or make_spec()
    store = RunStore(tmp_path)
    deck = deck if deck is not None else Deck(templates={("tb", corner): TEMPLATE})
    workdir = store.sim_dir("obs_0001", "tb", corner)
    ctx = StageContext(
        spec=spec, executor=executor, store=store, obs_id="obs_0001", workdir=workdir,
        remote_dir=executor.scratch(f"obs_0001/tb/{corner or 'nominal'}"), unit="tb", corner=corner, cshrc="/env.csh",
    )
    value: object = Point({"F": "24", "W": "0.8u"}, "user")
    for stage in spectre_pipeline(spec, deck):
        value = stage.run(value, ctx)
    return value, ctx


def test_chain_produces_child_result_from_point(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0 + int(p["F"]) / 100})
    child, ctx = run_chain(tmp_path, executor)

    assert child.status == "ok" and child.metrics == {"NF": 8.24} and child.unit == "tb"
    assert [c["label"] for c in ctx.trace] == ["spectre#1", "ocean#1"]
    assert (ctx.workdir / "netlist" / "input.scs").read_text().splitlines()[1] == "parameters temperature=27 F=24 W=0.8u"
    assert executor.commands[0].startswith("spectre -64 input.scs +escchars +preset=ax +mt=2")    # the fixture's threads_per_run
    assert (ctx.workdir / "metrics" / "probe.ocn").exists()


def test_a_bundle_with_names_windows_would_change_is_saved_and_rendered_through_literal_paths(tmp_path, monkeypatch):
    """N-21: every Maestro export carries amap/__dspf_information__. (a trailing dot, which ordinary Win32 paths strip),
    and a name may end in a space. Deck.save and render_netlist copy such a bundle through localpath.literal and keep
    every byte. On Linux these are ordinary names, so this guards the plumbing only; the Windows re-run checks Windows."""
    taken: list[Path] = []

    def spy(path, **kwargs):
        taken.append(Path(path))
        return literal(path, **kwargs)

    monkeypatch.setattr(deck_module, "literal", spy)
    monkeypatch.setattr(spectre_chain, "literal", spy)
    files = {".modelFiles": b"/pdk/models.scs\n", "amap/__dspf_information__.": bytes(range(38)), "trailing space ": b"x\n"}
    export = tmp_path / "export"
    for name, data in files.items():
        (export / name).parent.mkdir(parents=True, exist_ok=True)
        (export / name).write_bytes(data)
    saved = Deck(templates={("tb", None): TEMPLATE}, bundles={"tb": export}).save(RunStore(tmp_path).root / "decks")

    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0})
    child, ctx = run_chain(tmp_path, executor, deck=Deck.load(saved))
    assert child.status == "ok"
    for name, data in files.items():
        assert (saved / "tb" / "bundle" / name).read_bytes() == data and (ctx.workdir / "netlist" / name).read_bytes() == data
    assert {export, saved / "tb" / "bundle", ctx.workdir / "netlist"} <= set(taken)


def test_the_license_queue_wait_is_passed_only_when_the_spec_states_it(tmp_path):
    """R-17: ``+lqtimeout`` is simulator.license_queue_timeout_s. Unset, the flag stays off and Spectre waits in its license
    queue as it does by itself: no lab's wait is assumed. It is how the problem is run, not the problem (fingerprint)."""
    unset = spectre_pipeline(make_spec(), Deck())[1].argv()
    assert "+lqtimeout" not in unset and "900" not in unset
    stated_spec = make_spec(simulator={**minimal_spec()["simulator"], "license_queue_timeout_s": 600})
    stated = spectre_pipeline(stated_spec, Deck())[1].argv()
    at = stated.index("+lqtimeout")
    assert stated[at:at + 2] == ["+lqtimeout", "600"] and stated[:at] + stated[at + 2:] == unset
    assert Spectre(preset="ax", threads=1, timeout_s=1, license_queue_timeout_s=0).argv()[6:8] == ["+lqtimeout", "0"]
    assert stated_spec.fingerprint() == make_spec().fingerprint()

    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0})
    child, _ = run_chain(tmp_path, executor, spec=stated_spec)
    assert child.status == "ok" and " +mt=2 +lqtimeout 600 -maxw 5 " in executor.commands[0]


def test_spectre_failure_stops_the_chain(tmp_path):
    """A log without an ERROR entry (here none at all): the issues are what they were before N-119."""
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {}, fail_spectre=lambda tb, c: True)
    with pytest.raises(StageFailure, match="spectre exited 1") as failed:
        run_chain(tmp_path, executor)
    assert failed.value.issues == ["spectre exited 1", "spectre: license lost"]
    assert (tmp_path / ".icopt" / "sims" / "obs_0001" / "tb" / "nominal" / "spectre.stderr").read_text().startswith("spectre: license")


# -- N-119: the cause of a failed Spectre / OCEAN run in the child's issues ---------------------------------------------

# A Spectre log as N-118's check t4 left it (a corner whose model section the model file lacks), the path made generic:
# the log (+log psf/spectre.out) writes the entry on one line, the standard output wraps it at 80 columns.
SECTION_ERROR = ('ERROR: "input.scs" 12: The file `/pdk/models/spectre/toplevel.scs\' appears to be in Spectre syntax, but '
                 "Spectre cannot find the `section tt_no_such_section' library defined in it. Ensure that the "
                 "`tt_no_such_section' section is defined by `section/endsection' block and rerun the simulation.")
SPECTRE_LOG = f"""
Reading file:  /pdk/models/spectre/toplevel.scs

Error found by spectre during circuit read-in.
    {SECTION_ERROR}

Time for NDB Parsing: CPU = 102.152 ms, elapsed = 378.936 ms.
spectre completes with 1 error, 0 warnings, and 2 notices.
spectre terminated prematurely due to fatal error.
"""
SPECTRE_STDOUT = """
Error found by spectre during circuit read-in.
    ERROR: "input.scs" 12: The file
        `/pdk/models/spectre/toplevel.scs'
        appears to be in Spectre syntax, but Spectre cannot find the `section
        tt_no_such_section' library defined in it. Ensure that the
        `tt_no_such_section' section is defined by `section/endsection' block
        and rerun the simulation.

Reading file:
        /cad/spectre/etc/configs/preset.cfg
spectre completes with 1 error, 0 warnings, and 2 notices.
"""


def test_a_failed_spectre_run_puts_the_first_error_of_its_log_before_the_other_lines(tmp_path):
    """N-119: Spectre exited 2 and its log says why; the child's issues start with that entry, then the lines they always
    had. A longer entry is cut to ERROR_LIMIT characters (a real model path is longer than this one: the section's name
    stays inside the first 300 there too)."""
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {}, fail_spectre=lambda tb, c: SPECTRE_LOG)
    with pytest.raises(StageFailure) as failed:
        run_chain(tmp_path, executor)
    assert failed.value.issues == [SECTION_ERROR, "spectre exited 2", "spectre terminated prematurely due to fatal error."]
    assert not any(c.startswith("ocean") for c in executor.commands)
    longer = SPECTRE_LOG.replace("/pdk/models/", "/pdk/" + "a_long_directory_name/" * 6 + "models/")
    cut = spectre_chain.spectre_error(longer)
    assert len(cut) == spectre_chain.ERROR_LIMIT == 300 and cut.endswith("...") and "`section tt_no_such_section'" in cut


def test_the_error_entry_is_the_first_one_with_its_continuation_lines_on_one_line():
    """The wrapped form (standard output) collapses to the log's own line; the entry ends at a blank line or at a line
    indented no deeper than it; only the first entry counts; a text without one gives None."""
    assert spectre_chain.spectre_error(SPECTRE_STDOUT) == spectre_chain.spectre_error(SPECTRE_LOG)
    two = ("    ERROR (SFE-868): \"input.scs\" 3: Cannot open the input file\n        'models.scs'.\n"
           "    ERROR (SFE-1997): later\n    WARNING (SPECTRE-1): not part of it\n")
    assert spectre_chain.spectre_error(two) == "ERROR (SFE-868): \"input.scs\" 3: Cannot open the input file 'models.scs'."
    assert spectre_chain.spectre_error("ERROR (SPECTRE-25): The simulation is stopped either by the user or the\n"
                                       "        farm.\nAggregate audit\n") == ("ERROR (SPECTRE-25): The simulation is "
                                                                          "stopped either by the user or the farm.")
    for text in ("", "Error found by spectre during circuit read-in.\nspectre completes with 1 error.\n", "ERRORS: none\n"):
        assert spectre_chain.spectre_error(text) is None


def test_without_a_log_the_error_comes_from_spectres_output(tmp_path):
    """A run that stopped before it wrote psf/spectre.out: the same entry from its standard output."""
    from ic_opt.executor import CommandResult

    class NoLog(FakeSpectreExecutor):
        def run(self, command, *, cwd=None, timeout_s=None, cshrc=None):
            if command.startswith("spectre"):
                return CommandResult(2, SPECTRE_STDOUT, "spectre terminated prematurely due to fatal error.\n", ["spectre"], 0.01)
            return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)

    executor = NoLog(tmp_path / ".icopt" / "sims", lambda p, tb, c: {})
    with pytest.raises(StageFailure) as failed:
        run_chain(tmp_path, executor)
    assert not (tmp_path / ".icopt" / "sims" / "obs_0001" / "tb" / "nominal" / "psf" / "spectre.out").exists()
    assert failed.value.issues[0] == spectre_chain.spectre_error(SPECTRE_LOG) and failed.value.issues[1] == "spectre exited 2"


def test_ocean_retries_then_fails(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {}, fail_ocean=lambda tb, c: True)
    with pytest.raises(StageFailure, match="no scalars after 3 attempt") as failed:
        run_chain(tmp_path, executor)
    assert sum(c.startswith("ocean") for c in executor.commands) == 3
    assert failed.value.issues == ["ocean produced no scalars after 3 attempt(s)", "ocean: replay failed"]     # no log: as before


# OCEAN's log (-log metrics/ocean.log) as a license refusal leaves it (the N-100 acceptance, 2026-10-09): the error stream
# is the lines that start with \e, a framework message ERROR (<code>) or a SKILL error *Error*.
OCEAN_LOG = """\\o INFO (ELI-00333) License program mode is Virtuoso.
\\o Virtuoso Framework License (111) was checked out successfully. Total checkout time was 0.01s.
\\e ERROR (LMF-02005): License call failed for feature Virtuoso_Adv_Node_Framework, version 23.100 and quantity 1.
\\e ERROR (ELI-00111) Failed to check out license 'Virtuoso_Adv_Node_Framework'.
\\e ERROR (ELI-00133) Failed to launch the application because the required license is not available.
"""


def test_an_ocean_run_without_scalars_puts_the_first_error_of_its_log_first(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {}, fail_ocean=lambda tb, c: OCEAN_LOG)
    with pytest.raises(StageFailure) as failed:
        run_chain(tmp_path, executor)
    assert failed.value.issues == [
        "ERROR (LMF-02005): License call failed for feature Virtuoso_Adv_Node_Framework, version 23.100 and quantity 1.",
        "ocean produced no scalars after 3 attempt(s)", "ocean: replay failed"]
    skill = "\\i openResults(\"psf\")\n\\e *Error* openResults: no results in\n\\e     \"psf\"\n\\t nil\n\\e *Error* later\n"
    assert spectre_chain.ocean_error(skill) == '*Error* openResults: no results in "psf"'
    assert spectre_chain.ocean_error("\\o ERROR in the output stream is not the error stream\n\\e *WARNING* x\n") is None


def test_a_failed_metric_marks_the_child_metric_failed_and_keeps_the_others(tmp_path):
    """N-31 (2026-09-27): an expression that returns nil / a non-scalar costs that metric, not the child's other metrics."""
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": None})
    child, _ = run_chain(tmp_path, executor)
    assert child.status == "metric_failed" and child.metrics == {} and "non_scalar" in child.issues[0]

    spec = make_spec(metrics=[{"name": "NF", "unit": "dB", "expression": "nf()"}, {"name": "gain", "unit": "dB", "expression": "g()"}])
    executor = FakeSpectreExecutor(tmp_path / "two" / ".icopt" / "sims", lambda p, tb, c: {"NF": None, "gain": 3.0})
    child, _ = run_chain(tmp_path / "two", executor, spec=spec)
    assert child.status == "metric_failed" and child.metrics == {"gain": 3.0}
    assert child.issues == ["metric NF failed: non_scalar"]


def test_spectre_retries_once_on_a_transient_socket_failure(tmp_path):
    from ic_opt.executor import CommandResult
    from ic_opt.stages.spectre_chain import TRANSIENT_SOCKET_FAILURE

    class FlakySocket(FakeSpectreExecutor):
        calls = 0

        def run(self, command, *, cwd=None, timeout_s=None, cshrc=None):
            if command.startswith("spectre") and self.calls == 0:
                self.calls += 1
                return CommandResult(1, "", f"spectre: {TRANSIENT_SOCKET_FAILURE}", ["spectre"], 0.01)
            return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)

    executor = FlakySocket(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0})
    child, ctx = run_chain(tmp_path, executor)
    assert child.status == "ok" and [c["label"] for c in ctx.trace] == ["spectre#1", "spectre#2", "ocean#1"]
