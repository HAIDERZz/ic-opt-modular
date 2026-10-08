"""netlist.import — Maestro export directories -> templated Deck under ``.icopt/decks/``."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from ic_opt.deck import Deck
from ic_opt.executor import Executor, ExecutorError
from ic_opt.localpath import literal
from ic_opt.sim import netlist as kernel
from ic_opt.spec import Spec
from ic_opt.store import RunStore


def _plan_check(spec, executor) -> Deck:
    """The preview's share of the import: fetch every testbench's export into a temporary directory and template it for
    every corner, so a variable the export lacks or a corner with no model include shows at the approval point, not at the
    run (the 2026-09-27 real-scenario acceptance, N-27, ISSUE-2, met it only after the plan). A parameter a corner sets
    that the export declares but never uses is a WARNING (N-35: `temperature` set per corner while `simulatorOptions`
    carried a literal `temp=27`, so every corner ran at 27 degrees in silence). Nothing reaches the store; like env.doctor's
    checks a failure is printed and the preview goes on. The fetched tree carries `amap/__dspf_information__.`, whose name
    Win32 would change: it is removed through `literal` (a `TemporaryDirectory` died with WinError 145 on the N-35 run).

    Returns the deck the run would import, as a snapshot (``Deck.snapshot``: its support files' digests, read before the
    temporary fetch is removed), so the plan names the deck and the pipeline the run will have (N-99); a testbench whose
    check failed is not in it."""
    names = spec.circuit_variables
    corners = [c or "nominal" for c in spec.corner_ids]
    set_by_corners = sorted({name for c in spec.corners for name in c.variables})
    deck = Deck()
    tmp = Path(tempfile.mkdtemp(prefix="ic_opt_plan_"))
    try:
        for tb in spec.testbenches:
            try:
                local = tmp / tb.id
                executor.get(f"{tb.maestro_point_root}/netlist", local, dereference=True)
                exported = local / "input.scs"
                if not exported.is_file():
                    raise FileNotFoundError(f"{tb.maestro_point_root}/netlist/input.scs not found on {executor.host}")
                text = exported.read_text(encoding="utf-8")
                template = kernel.template_deck(text, names)
                templates = {(tb.id, corner_id): kernel.apply_corner(template, spec.corner(corner_id)) if corner_id
                             else template for corner_id in spec.corner_ids}
            except (OSError, ValueError, ExecutorError) as exc:
                print(f"[plan] netlist.import {tb.id}: FAIL {exc}")
                continue
            deck.templates.update(templates)
            deck.bundles[tb.id] = local
            deck.source[tb.id] = f"{executor.host}:{tb.maestro_point_root}/netlist"
            print(f"[plan] netlist.import {tb.id}: {executor.host}:{tb.maestro_point_root}/netlist → deck, corners {corners}")
            unused = kernel.unreferenced_parameters(text, set_by_corners)
            if unused:
                print(f"[plan] netlist.import {tb.id}: WARNING the corners set {unused}, which this export declares in its "
                      "top-level parameters but never uses; the value changes nothing there (a literal such as "
                      "`simulatorOptions options temp=27` does not follow a parameter: set it with the corner's "
                      "`options`, e.g. options: {temp: \"125\"})")
        return deck.snapshot()
    finally:
        shutil.rmtree(literal(tmp))


def import_netlists(spec: Spec, executor: Executor, store: RunStore) -> Deck:
    """Fetch each testbench's exported ``netlist/`` through the executor, template it per corner, save the deck under
    ``decks/<fp>/`` (``Deck.fingerprint``: the templates and every support file's content, N-99), and return it as saved.

    Only the spec's variables may be templated; a variable missing from (or
    duplicated in) the top-level ``parameters`` statement is an error, and a
    variable assigned inside a subckt is refused — the legacy
    ``forbidden_setup_changes`` rule.
    """
    from ic_opt.recipe import PLAN_MODE

    if PLAN_MODE.get():
        return _plan_check(spec, executor)          # a snapshot: it names the deck and cannot run (Deck.snapshot)
    staging = store.root / "decks" / ".staging"
    if staging.exists():            # left by an import that stopped part-way: none of it may reach this deck
        shutil.rmtree(literal(staging))
    staging.mkdir(parents=True)     # always there, so the cleanup below is one plain delete (a devices-only spec fetches nothing)
    deck = Deck()
    names = spec.circuit_variables
    for tb in spec.testbenches:
        local = staging / tb.id
        executor.get(f"{tb.maestro_point_root}/netlist", local, dereference=True)
        exported = local / "input.scs"
        if not exported.is_file():
            raise FileNotFoundError(f"{tb.id}: {tb.maestro_point_root}/netlist/input.scs not found on {executor.host}")
        template = kernel.template_deck(exported.read_text(encoding="utf-8"), names)
        for corner_id in spec.corner_ids:
            deck.templates[(tb.id, corner_id)] = kernel.apply_corner(template, spec.corner(corner_id)) if corner_id else template
        deck.bundles[tb.id] = local
        deck.source[tb.id] = f"{executor.host}:{tb.maestro_point_root}/netlist"
    saved = deck.save(store.root / "decks")
    shutil.rmtree(literal(staging))     # a tree that cannot be removed is an error, not one left behind in silence
    imported = Deck.load(saved)         # decks/<fp>/: it fingerprints to <fp> (N-99), its bundles the saved copies
    store.log_step("netlist.import", "ok", deck=saved.name, testbenches=len(spec.testbenches), corners=len(spec.corner_ids),
                   support_files=sum(len(imported.support_files(tb)) for tb in imported.bundles))
    return imported


def last_imported(store: RunStore) -> Deck | None:
    """The deck the store's last ``netlist.import`` saved (``steps.jsonl``: its ``deck``, ``decks/<fp>/``), or None when
    there is none or its directory is gone. ``ic-opt doctor`` names it (N-99); every run imports the exports again."""
    path = store.root / "steps.jsonl"
    if not path.is_file():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("step") == "netlist.import" and row.get("status") == "ok" and row.get("deck"):
            saved = store.deck_dir(str(row["deck"]))
            return Deck.load(saved) if saved.is_dir() else None
    return None
