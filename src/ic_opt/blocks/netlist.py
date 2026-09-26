"""netlist.import — Maestro export directories -> templated Deck under ``.icopt/decks/``."""

from __future__ import annotations

import shutil

from ic_opt.deck import Deck
from ic_opt.executor import Executor
from ic_opt.localpath import literal
from ic_opt.sim import netlist as kernel
from ic_opt.spec import Spec
from ic_opt.store import RunStore


def import_netlists(spec: Spec, executor: Executor, store: RunStore) -> Deck:
    """Fetch each testbench's exported ``netlist/`` through the executor, template it per corner, save the deck.

    Only the spec's variables may be templated; a variable missing from (or
    duplicated in) the top-level ``parameters`` statement is an error, and a
    variable assigned inside a subckt is refused — the legacy
    ``forbidden_setup_changes`` rule.
    """
    from ic_opt.recipe import PLAN_MODE

    if PLAN_MODE.get():
        for tb in spec.testbenches:
            print(f"[plan] netlist.import {tb.id}: {executor.host}:{tb.maestro_point_root}/netlist → deck, "
                  f"corners {[c or 'nominal' for c in spec.corner_ids]}")
        return Deck()
    staging = store.root / "decks" / ".staging"
    if staging.exists():            # left by an import that stopped part-way: none of it may reach this deck
        shutil.rmtree(literal(staging))
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
    store.log_step("netlist.import", "ok", deck=deck.fingerprint(), testbenches=len(spec.testbenches), corners=len(spec.corner_ids))
    return Deck.load(saved)
