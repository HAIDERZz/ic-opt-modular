"""One recommendation request across the tables of a device kind (N-114): ``lib.suggest`` and ``lib.region`` with
``family=`` or ``strata=`` instead of one ``stratum=``.

A table (stratum) is one device: one generator with one set of fixed fields -- the port opening, the metals, the ground
fixture, the taps. A target outside one table may lie inside another of the same kind (two tables that differ only in
their port opening cover different inductances), so asking one table answers "nothing" where the library has designs.
A family request asks each of its tables exactly as ``stratum=`` does and puts the answers side by side:

- ``strata``: each table's own answer, unchanged, or ``{"error": text}`` when the table cannot take the request;
- ``facts``: each table's generator, the fixed fields that tell the tables apart (``fixed``: the port openings, the
  metals, the ground fixture's metal, the centre-tap ports and the tap fields) and its rows;
- ``leaders``: for ``lib.suggest`` the measured designs, then the predicted ones, each merged across the tables by the
  objective's conservative bound (without one: by closeness to the targets) -- every table's own order kept -- at most
  ``n`` of each, labelled with the table and its ``fixed``; for ``lib.region`` per table the robust and mean counts and
  ranges and the measured designs, the tables with points first;
- ``unreachable``: for each table without a design, why (``unreachable``, the one place that words it);
- ``notes``: the library's notes, then which tables have designs and what the others reach.

Membership is read off the tables, never off their names: a table is of family ``F`` when its generator is
``clean_port_<F>`` (the built-in families; a generator without that prefix is a family of its own name), and it is
tapped when its parts' device has a centre-tap port -- a port whose name starts with ``CT`` (``CT``, ``CTP``, ``CTS``),
the rule by which ``Device.default_topology`` grounds the taps. The ports, not the ``ct_*`` fields, decide: they are
what EMX measured and the sNp holds, the rule is the same for every family (the fields are named per family:
``ct_metal``, ``ct_primary_metal``, ...), and every built-in generator refuses a tap port without its tap metal and the
other way round, so on a part that was built the two agree.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from ic_opt import site
from ic_opt.library import combinations, dataset, query
from ic_opt.library import region as _region
from ic_opt.library import suggest as _suggest

PREFIX = "clean_port_"                               # the built-in generators' names: clean_port_<family>
TAPS = ("none", "any", "only")                      # the tables without centre taps (the default), all, the tapped ones
KINDS = ("outside_range", "not_met_together", "cannot_take")   # unreachable: the three reasons
LEVELS = ("robust", "mean")


# -- which tables ---------------------------------------------------------------------------------------------------------

def family_of(generator: str) -> str:
    """A generator's family: its name without ``clean_port_`` (``clean_port_xfm_bs`` -> ``xfm_bs``); a name without that
    prefix is its own family."""
    return generator.removeprefix(PREFIX)


def tap_ports(device) -> list[str]:
    """A device's centre-tap ports: the ports whose name starts with CT (``Device.default_topology`` grounds them)."""
    return [p for p in device.ports if p.upper().startswith("CT")]


def tapped(library: query.Library, stratum: str) -> bool:
    """Whether a table's parts carry centre taps (any part's device has a tap port)."""
    return any(tap_ports(d) for d in dataset.part_devices(library.root, library.manifest.strata[stratum]).values())


def members(library: query.Library, family: str, taps: str = "none") -> list[str]:
    """The tables of ``family`` (its name, or a generator's full name) kept by ``taps``, in the library's order."""
    if taps not in TAPS:
        raise ValueError(f"taps is none (the tables without centre taps, the default), any or only; got {taps!r}")
    wanted = family_of(str(family))
    kinds = {name: family_of(library.manifest.strata[name].generator) for name in library.strata()}
    names = [name for name, kind in kinds.items() if kind == wanted]
    if not names:
        known = ", ".join(f"{kind} ({sum(k == kind for k in kinds.values())} tables)" for kind in sorted(set(kinds.values())))
        raise ValueError(f"no table of family {family!r} in {library.root}: a table's family is its generator's name without "
                         f"{PREFIX}; the library's families: {known}")
    marked = {name: tapped(library, name) for name in names}
    keep = [name for name in names if taps == "any" or marked[name] == (taps == "only")]
    if not keep:
        raise ValueError(f"family {wanted} has no {'tapped' if taps == 'only' else 'untapped'} table (taps={taps}); its tables: "
                         + ", ".join(f"{name} ({'tapped' if marked[name] else 'untapped'})" for name in names))
    return keep


def request(library: query.Library, *, stratum: str | None = None, family: str | None = None, taps: str | None = None,
            strata: list[str] | None = None) -> tuple[list[str], str | None, str | None]:
    """The tables a call asks: exactly one of ``stratum`` (one table, answered as before), ``family`` (``members``, with
    ``taps``, ``none`` unless given) and ``strata`` (a list, no tap filter). Returns (tables, family, taps)."""
    given = [name for name, value in (("stratum", stratum), ("family", family), ("strata", strata)) if value not in (None, "", [])]
    if len(given) != 1:
        raise ValueError("give exactly one of stratum= (one table), family= (every table of a device kind, e.g. ind_sym, "
                         f"xfm_bs, xfm_ms; taps= none, any or only) or strata= (a comma list of tables); got {', '.join(given) or 'none'}")
    if taps is not None and not family:
        raise ValueError("taps= filters the tables of a family= (none, any or only); stratum= names one table, strata= a list")
    if stratum:
        return [str(stratum)], None, None
    if family:
        taps = "none" if taps is None else str(taps)
        return members(library, str(family), taps), family_of(str(family)), taps
    unknown = [name for name in strata if name not in library.manifest.strata]
    if unknown:
        raise ValueError(f"no table {unknown} in {library.root}; its tables: {library.strata()}")
    return list(dict.fromkeys(strata)), None, None


def facts(library: query.Library, stratum: str) -> dict:
    """A table's generator, the fixed fields that tell it from the others of its kind (``fixed``: a value the parts share,
    else ``{part: value}``) and its rows (None, like an empty ``fixed``, for a table whose parts or dataset cannot be
    read: its answer is the error that says why)."""
    rule = library.manifest.strata[stratum]
    try:
        per_part = {store: _fixed(device) for store, device in dataset.part_devices(library.root, rule).items()}
    except ValueError:
        per_part = {}
    keys = list(dict.fromkeys(key for fields in per_part.values() for key in fields))
    fixed = {}
    for key in keys:
        values = {part: fields.get(key) for part, fields in per_part.items()}
        first = next(iter(values.values()))
        fixed[key] = first if all(v == first for v in values.values()) else values
    try:
        rows = len(library.dataset(stratum).rows)
    except ValueError:
        rows = None
    return {"generator": rule.generator, "fixed": fixed, "rows": rows}


def _fixed(device) -> dict:
    """The port openings, the metals, the ground fixture's metal, the tap ports and the tap fields of a part's device."""
    fields = device.fixed
    out = {k: v for k, v in fields.items() if k.endswith("opening_um")}
    out.update({k: v for k, v in fields.items() if k.endswith("metal") and not k.startswith("ct_")})
    fixture = fields.get("ground_fixture")
    if isinstance(fixture, dict) and "metal" in fixture:
        out["ground_fixture_metal"] = fixture["metal"]
    out["taps"] = tap_ports(device)
    out.update({k: v for k, v in fields.items() if k.startswith("ct_")})
    return out


# -- asking every table -----------------------------------------------------------------------------------------------------

def _ask(library: query.Library, stratum: str, names: list[str], call: Callable[[], dict]) -> dict:
    """``call``'s answer, or ``{"error": text}`` when the table cannot take the request: a column it does not have (or no
    row has a value of), a frequency outside its sweeps, any refusal or failure of its own (ValueError, RuntimeError). A
    machine without limits (SiteError) is no table's matter: it refuses the whole request."""
    try:
        _answerable(library, stratum, names)
        return call()
    except site.SiteError:
        raise
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}


def _answerable(library: query.Library, stratum: str, names: list[str]) -> None:
    """Refuse a request naming a column no row of the table has a value of (``Dataset.usable``): no model is fitted on
    nothing. The names go through ``Library.columns`` first, which refuses what is no column of the table at all."""
    ds = library.dataset(stratum)
    for column in library.columns(stratum, names):
        if not ds.usable(column):
            curve = ("; a row has a curve's value only inside its sweep and while its system SRF lies above the curve's "
                     "srf_margin x f" if "@" in column else "")
            raise ValueError(f"{stratum}: no row has a value of {column}{curve}")


def _requested(targets: dict, objective: str | None, extra: tuple[str, ...] = ()) -> tuple[list[_suggest.Target], list[str]]:
    """The stated targets (a malformed request is refused here, before any table is asked) and every column named."""
    stated = _suggest.parse_targets(targets)
    obj = _suggest.parse_objective(objective)
    return stated, [t.quantity for t in stated] + ([obj[1]] if obj else []) + list(extra)


def suggest(library: query.Library, names: list[str], targets: dict, objective: str | None = None, *, family: str | None = None,
            taps: str | None = None, n: int = 5, **options) -> dict:
    """``suggest.suggest`` on every table of ``names`` and the answers together (module docstring); ``options`` go to it."""
    stated, columns = _requested(targets, objective)
    answers = {name: _ask(library, name, columns,
                          lambda name=name: _suggest.suggest(library, name, targets, objective, n=n, **options)) for name in names}
    known = {name: facts(library, name) for name in names}
    reached = [name for name, a in answers.items() if "error" not in a and (a["satisfying"] or a.get("region_grid", {}).get("satisfying"))]
    why = {name: unreachable(library, name, a, len(stated), kind="suggest") for name, a in answers.items() if name not in reached}
    return {"family": family, "taps": taps, "strata": answers, "facts": known,
            "leaders": _suggest_leaders(answers, known, len(stated), objective, n), "unreachable": {k: v[0] for k, v in why.items()},
            "notes": library.notes + _summary(answers, stated, reached, why)}


def region(library: query.Library, names: list[str], targets: dict, objective: str | None = None, *, family: str | None = None,
           taps: str | None = None, out: str | None = None, level: str = "robust", device: str | None = None, **options) -> dict:
    """``region.region`` on every table of ``names`` and the answers together (module docstring); ``options`` go to it.
    ``out`` writes one table's combinations (N-106): refused unless exactly one table has grid points at ``level``,
    and then that table's, the answer gaining ``combinations``, ``device_block`` and ``variables_block``."""
    if level not in LEVELS:
        raise ValueError(f"level is robust or mean, got {level!r}")
    trend = options.get("trend")
    stated, columns = _requested(targets, objective, (trend[0],) if trend else ())
    answers = {name: _ask(library, name, columns,
                          lambda name=name: _region.region(library, name, targets, objective, level_points=level if out else None,
                                                           **options)) for name in names}
    points = {name: a.pop("level_points") for name, a in answers.items() if "level_points" in a}
    known = {name: facts(library, name) for name in names}
    reached = [name for name, a in answers.items() if "error" not in a and (a["levels"]["robust"]["count"] or a["measured"])]
    why = {name: unreachable(library, name, a, len(stated), kind="region") for name, a in answers.items() if name not in reached}
    answer = {"family": family, "taps": taps, "strata": answers, "facts": known, "leaders": _region_leaders(answers, known),
              "unreachable": {k: v[0] for k, v in why.items()}, "notes": library.notes + _summary(answers, stated, reached, why)}
    if out:
        holding = {name: len(p["rows"]) for name, p in points.items() if p["rows"]}
        if len(holding) != 1:
            listed = ", ".join(f"{name} ({count})" for name, count in holding.items())
            if not holding:
                raise ValueError(f"lib.region out=: no table of the request holds a {level} grid point, so there is no combination "
                                 f"to write ({'level=mean, or ' if level == 'robust' else ''}other targets)")
            raise ValueError(f"lib.region out= writes the combinations of one table, and {len(holding)} tables hold {level} grid "
                             f"points: {listed}; rerun with stratum=<one of them> and out=")
        (name,) = holding
        answer.update(combinations.from_region(library, name, {**answers[name], "level_points": points[name]}, out, targets=targets,
                                               device=device))
        answer["notes"].append(f"out= wrote the {level} grid points of {name}, the only table that holds any")
    return answer


# -- leaders ----------------------------------------------------------------------------------------------------------------

def _canonical(name: str) -> str:
    """A column name as a table names it (``dataset.resolve``: ``Qp@60.0`` is ``Qp@60``)."""
    base, at, text = name.partition("@")
    try:
        return f"{base}@{float(text):g}" if at else name
    except ValueError:
        return name


def _order(stated: list[_suggest.Target], objective: tuple[str, str] | None) -> Callable[[dict], tuple[float, float]]:
    """The key ``suggest.rank`` ranks a table's designs by, for one design of any table: the objective's conservative bound
    then its relative sigma, else the distance to the targets' centres then the targets' relative sigma."""
    def key(entry: dict) -> tuple[float, float]:
        p = entry["predicted"]
        if objective:
            sense, q = objective
            if q not in p:
                return math.inf, math.inf
            out = (-p[q]["lo"] if sense == "max" else p[q]["hi"], p[q]["rel_sigma"])
        else:
            centres = [(t.quantity, t.value if t.kind == "target" else (t.value + t.upper) / 2) for t in stated
                       if t.kind in ("target", "window")]
            out = (sum(abs(p[q]["value"] / c - 1) for q, c in centres if q in p),
                   sum(p[t.quantity]["rel_sigma"] for t in stated if t.quantity in p))
        return tuple(v if math.isfinite(v) else math.inf for v in out)
    return key


def _merge(lists: dict[str, list[dict]], keys: dict[str, Callable[[dict], tuple]], n: int) -> list[tuple[str, dict]]:
    """At most ``n`` entries of the tables' lists, the best head first by its table's key (a tie: the earlier table), so
    each table's own order is kept."""
    heads = dict.fromkeys(lists, 0)
    out: list[tuple[str, dict]] = []
    while len(out) < n:
        live = [(keys[name](lists[name][i]), order, name) for order, (name, i) in enumerate(heads.items()) if i < len(lists[name])]
        if not live:
            break
        name = min(live)[2]
        out.append((name, lists[name][heads[name]]))
        heads[name] += 1
    return out


def _suggest_leaders(answers: dict, known: dict, stated: int, objective: str | None, n: int) -> list[dict]:
    obj = _suggest.parse_objective(objective)
    obj = (obj[0], _canonical(obj[1])) if obj else None
    keys = {name: _order([_suggest.Target(**t) for t in a["targets"][:stated]], obj) for name, a in answers.items() if "error" not in a}
    out = []
    for field in ("measured", "candidates"):                 # the measured designs first, then the predicted ones
        lists = {name: answers[name][field] for name in keys if answers[name][field]}
        out += [{"stratum": name, "fixed": known[name]["fixed"], **entry} for name, entry in _merge(lists, keys, n)]
    return out


def _region_leaders(answers: dict, known: dict) -> list[dict]:
    rows = [{"stratum": name, "fixed": known[name]["fixed"], "robust": a["levels"]["robust"], "mean": a["levels"]["mean"],
             "measured": len(a["measured"])} for name, a in answers.items() if "error" not in a]

    def rank(row: dict) -> tuple[int, int, int]:
        group = 0 if row["robust"]["count"] else 1 if row["measured"] else 2 if row["mean"]["count"] else 3
        return group, -row["robust"]["count"], -row["mean"]["count"]
    return sorted(rows, key=rank)


# -- why a table has no design -------------------------------------------------------------------------------------------

def unreachable(library: query.Library, stratum: str, answer: dict, stated: int, *, kind: str) -> tuple[dict, str]:
    """Why ``stratum`` has no design for the request -- the one place that words it (N-116, answers beyond the measured
    range, would change it here). ``answer`` is the table's answer (``kind`` ``suggest`` or ``region``) or its error, and
    its ``targets`` the goals as the table resolved them, the first ``stated`` the request's own (the rest the resonance the
    anchored ones imply). The measured range of a quantity is that of the rows that have it under the library's own rules
    (``Dataset.usable``: an anchored column only below its resonance margin). ``kind`` is one of three (``KINDS``):

    - ``outside_range`` (a): a target's window lies wholly outside the table's measured range of its quantity: ``outside``
      lists each such target -- ``quantity``, ``window`` (SI units), ``range``, ``side`` (below or above);
    - ``not_met_together`` (b): every target inside its range, but no design meets them together: ``closest`` is the
      measured row nearest to meeting every goal (the distance outside each window scaled by the window's width, a
      one-sided bound's by its own size; a row without a resonance in its sweep counts its sweep's end for an SRF
      minimum, as ``suggest`` does), with its values;
    - ``cannot_take`` (c): the table refused the request (a quantity it does not have, a frequency outside its sweeps):
      the error text.

    Returns the entry -- ``kind``, ``reason`` (the sentence that says it in SI units), ``ranges`` (every stated quantity's
    measured range), ``outside`` and ``closest`` -- and a short phrase for the answer's notes."""
    if "error" in answer:
        return ({"kind": "cannot_take", "reason": f"{stratum} cannot take the request: {answer['error']}", "ranges": {}, "outside": [],
                 "closest": None}, f"{stratum} cannot take the request")
    ds = library.dataset(stratum)
    goals = [_suggest.Target(**t) for t in answer["targets"]]
    ranges, outside = {}, []
    for t in goals[:stated]:
        values = ds.values(t.quantity, ds.usable(t.quantity))
        if not len(values):
            continue
        low, high = float(values.min()), float(values.max())
        ranges[t.quantity] = [low, high]
        a, b = t.window()
        reach = max((v for v in (_beyond_sweep(r, t) for r in ds.rows) if math.isfinite(v)), default=-math.inf)
        if b < low or a > max(high, reach):                   # a sweep's end above an SRF minimum settles it (_beyond_sweep)
            outside.append({"quantity": t.quantity, "window": [a, b], "range": [low, high], "side": "below" if b < low else "above"})
    if outside:
        texts = []
        for o in outside:
            shown = _si(o["quantity"], [v for v in (*o["window"], *o["range"]) if math.isfinite(v)])
            texts.append(f"{o['quantity']} {_window(o['quantity'], *o['window'], shown)} lies {o['side']} this table's measured "
                         f"{shown(o['range'][0])}-{shown(o['range'][1])}{shown.unit}")
        first = outside[0]
        end = first["range"][0] if first["side"] == "below" else first["range"][1]
        shown = _si(first["quantity"], [end])
        return ({"kind": "outside_range", "reason": "; ".join(texts), "ranges": ranges, "outside": outside, "closest": None},
                f"{stratum} reaches {first['quantity']} {shown(end)}{shown.unit} at the {'least' if first['side'] == 'below' else 'most'}")
    closest = _closest(ds, goals)
    implied = [f"{t.quantity} >= {_value(t.quantity, t.value)}" for t in goals[stated:]]
    sentence = ("every target lies inside this table's measured range, but no measured row meets them together"
                + (f" with {', '.join(implied)} (the resonance the anchored targets imply)" if implied else "")
                + ", nor does a predicted design with its whole calibrated interval inside every window")
    if kind == "region" and answer["levels"]["mean"]["count"]:
        sentence += (f" ({answer['levels']['mean']['count']} grid points do at the mean level, their predicted values inside "
                     "every window)")
    if closest is not None:
        values = "; ".join(f"{q} {_value(q, v)}" for q, v in closest["values"].items())
        sentence += f"; the closest measured row is {closest['part']}/{closest['obs_id']}: {values}"
    return ({"kind": "not_met_together", "reason": sentence, "ranges": ranges, "outside": [], "closest": closest},
            f"{stratum} has every target inside its measured range but no design meeting them together")


def _beyond_sweep(row: dataset.Row, t: _suggest.Target) -> float:
    """What a row without a value of ``t``'s quantity counts as for goal ``t``: for an SRF goal without an upper end (a
    minimum) on a row with no resonance in its sweep, the sweep's end -- its resonance lies above it (``suggest``'s floor,
    ``region._settled_by_sweep_end``); else +inf, no value to compare."""
    if t.quantity.startswith("SRF") and t.window()[1] == math.inf and row.values.get(t.quantity) is None:
        return row.stop_hz
    return math.inf


def _closest(ds: dataset.Dataset, goals: list[_suggest.Target]) -> dict | None:
    """The row nearest to meeting every goal (``unreachable``, reason b), among the rows that have a value of every goal's
    quantity (``_beyond_sweep`` for an SRF minimum)."""
    best, best_d = None, math.inf
    for row in ds.rows:
        total = 0.0
        for t in goals:
            v = row.values.get(t.quantity)
            if v is None or not math.isfinite(v):
                v = _beyond_sweep(row, t)             # an SRF minimum: the sweep's end; anything else: not comparable
                if not math.isfinite(v):
                    total = math.nan
                    break
            a, b = t.window()
            scale = (b - a) if math.isfinite(a) and math.isfinite(b) else abs(a if math.isfinite(a) else b) or 1.0
            total += (max(a - v, 0.0, v - b) / scale) ** 2
        if math.isfinite(total) and total < best_d:
            best, best_d = row, total
    if best is None:
        return None
    return {"part": best.part, "obs_id": best.obs_id, "params": {d: float(best.coords[d]) for d in ds.dims},
            "values": {t.quantity: best.values.get(t.quantity) for t in goals}, "distance": round(math.sqrt(best_d), 6)}


# -- wording ------------------------------------------------------------------------------------------------------------------

class _Shown:
    """Numbers of one quantity in one sentence, in one SI unit (pH or nH, GHz, or none)."""

    def __init__(self, scale: float, unit: str):
        self.scale, self.unit = scale, f" {unit}" if unit else ""

    def __call__(self, value: float) -> str:
        return f"{value / self.scale:.4g}"


def _si(quantity: str, values: list[float]) -> _Shown:
    unit = query.unit(quantity)
    if unit == "H":
        small = min((abs(v) for v in values if v and math.isfinite(v)), default=1e-12)
        return _Shown(1e-12, "pH") if small < 1e-9 else _Shown(1e-9, "nH")
    if unit == "Hz":
        return _Shown(1e9, "GHz")
    return _Shown(1.0, "")


def _window(quantity: str, a: float, b: float, shown: _Shown) -> str:
    if math.isfinite(a) and math.isfinite(b):
        return f"window {shown(a)}-{shown(b)}{shown.unit}"
    return f"minimum {shown(a)}{shown.unit}" if math.isfinite(a) else f"maximum {shown(b)}{shown.unit}"


def _value(quantity: str, value: float | None) -> str:
    if value is None:
        return "none (no resonance in its sweep)" if quantity.startswith("SRF") else "none"
    shown = _si(quantity, [value])
    return f"{shown(value)}{shown.unit}"


def _summary(answers: dict, stated: list[_suggest.Target], reached: list[str], why: dict[str, tuple[dict, str]]) -> list[str]:
    """When some tables have no design: one line naming the targets, the tables that have designs and what the others reach."""
    if not why:
        return []
    shown = next((a["targets"][:len(stated)] for a in answers.values() if "error" not in a), [t.__dict__ for t in stated])
    head = ", ".join(_target_text(_suggest.Target(**t)) for t in shown)
    found = f"designs in {', '.join(reached)}" if reached else "no table has a design"
    return [f"{head}: {found}; " + "; ".join(phrase for _, phrase in why.values())]


def _target_text(t: _suggest.Target) -> str:
    a, b = t.window()
    return f"{t.quantity} {_window(t.quantity, a, b, _si(t.quantity, [v for v in (a, b) if np.isfinite(v)]))}"
