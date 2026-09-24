"""The library as a pipeline stage: optimize on predictions instead of EMX, then sign off the winners for real.

``surrogate_pipeline(spec, library)`` is ``[Pcell, Predict]``: the pcell still builds every point (an
unbuildable geometry fails as ``failed:pcell``, exactly as in the EM pipeline), then ``Predict`` fills each
device metric from the library -- the measured value at an exact library point, else the model's mean --
where the EM pipeline would run EMX and the measure kernel. A metric the library cannot vouch for (out of
domain, too uncertain, resonance above the sweep, no such column) fails the child as ``failed:predict``
with the reason, so optimizers treat it like any failed simulation. Every answer, bounds included, is
written to the child's ``predictions.json``.

The stage's identity names each device's stratum and dataset content key, so surrogate observations get
their own pipeline fingerprint: they never pass for EMX measurements, and a library that grows is a new
generation of predictions. Each column is asked with its confidence ceiling: the stage's ``rel_sigma_max`` when
given, else the quantity's in library.yaml, else ``domain.DEFAULT_SIGMA_REL_MAX``; the identity names the
manifest's ceilings when they apply, so changing one is a new generation too.

A device only maps onto a stratum built with the same generator and the same fixed fields (metal, fixture,
leads ...): ``match_stratum`` finds it, and ``Predict`` refuses a device whose fixed fields differ.

The engine runs points in parallel threads; the models they share are fitted once, before any of them is
queried: ``Predict.prefit`` (``lib_design`` calls it before the evaluation starts) hands every column the
stage will ask for to ``Library.models``, which fits the uncached ones in parallel processes within the
library's limits. A thread never fits a model by itself.
"""

from __future__ import annotations

import json
import threading

from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.library import dataset, domain, query
from ic_opt.observation import ChildResult
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import Geometry, Pcell


def _fixed(library: query.Library, stratum: str) -> tuple[str, dict]:
    part = library.manifest.strata[stratum].parts[0].store
    device = dataset._spec(library.root / part).devices[0]
    return device.generator, dict(device.fixed)


def match_stratum(library: query.Library, device) -> str:
    """The stratum built with this device's generator and fixed fields (every fixed field the library pins must agree)."""
    hits = []
    for name in library.strata():
        generator, fixed = _fixed(library, name)
        if generator == device.generator and all(device.fixed.get(k) == v for k, v in fixed.items()):
            hits.append(name)
    if len(hits) != 1:
        raise ValueError(f"device {device.id}: {len(hits)} strata match its generator and fixed fields ({hits}); name one with stratum=")
    return hits[0]


class Predict:
    """Device child: the spec's device metrics from the library instead of EMX + measure."""

    name = "predict"
    level = "child"
    unit = "device"
    simulates = False                                  # a prediction is not a simulation: it spends none of the spec's budget
    resources = Resources()

    def __init__(self, library: query.Library, strata: dict[str, str], *, k: float = 2.0, rel_sigma_max: float | None = None):
        self.library, self.strata, self.k, self.rel_sigma_max = library, dict(strata), k, rel_sigma_max
        identity = {"k": k, "rel_sigma_max": domain.DEFAULT_SIGMA_REL_MAX if rel_sigma_max is None else rel_sigma_max,
                    "calibrate": library.calibrate, "strata": {d: [s, library.dataset(s).key] for d, s in sorted(self.strata.items())}}
        if rel_sigma_max is None:                      # the manifest's ceilings apply: part of what the predictions are
            ceilings = {s: {q: rule.rel_sigma_max for q, rule in library.manifest.strata[s].quantities.items() if rule.rel_sigma_max is not None}
                        for s in sorted(set(self.strata.values()))}
            if any(ceilings.values()):                 # none set: the identity (and the fingerprint) of a library without them
                identity["rel_sigma_max_by_quantity"] = {s: c for s, c in ceilings.items() if c}
        self.identity = json.dumps(identity, separators=(",", ":"))
        self._lock = threading.Lock()
        self._ready: dict[str, list[str]] | None = None

    def fingerprint(self, geometry: Geometry, ctx: StageContext) -> str | None:
        return None

    def prefit(self, spec: Spec) -> dict[str, list[str]]:
        """Every model this stage will ask for -- per stratum, the columns of its devices' metrics -- fitted or loaded once,
        through ``Library.models`` (the uncached ones in parallel processes within the library's limits); returns them.
        Call it before the evaluation threads start (``lib_design`` does). ``run`` calls it too, so the models are ready
        before any query even when the caller did not: the first evaluation thread fits for all while the others wait."""
        with self._lock:
            if self._ready is None:
                wanted: dict[str, dict[str, None]] = {}
                for device, stratum in self.strata.items():
                    columns = _columns(spec, device, self.library.dataset(stratum))[0].values()
                    wanted.setdefault(stratum, {}).update(dict.fromkeys(columns))
                for stratum, columns in wanted.items():
                    if columns:
                        self.library.models(stratum, list(columns))
                self._ready = {stratum: list(columns) for stratum, columns in wanted.items()}
            return self._ready

    def run(self, geometry: Geometry, ctx: StageContext) -> ChildResult:
        stratum = self.strata.get(ctx.unit)
        if stratum is None:
            raise StageFailure(f"device {ctx.unit} has no library stratum")
        device = ctx.spec.device(ctx.unit)
        generator, fixed = _fixed(self.library, stratum)
        differ = {k: (device.fixed.get(k), v) for k, v in fixed.items() if device.fixed.get(k) != v}
        if generator != device.generator or differ:
            raise StageFailure(f"device {ctx.unit} is not stratum {stratum}'s device: generator {device.generator} vs {generator}, fixed {differ}")
        ds = self.library.dataset(stratum)
        config = geometry.devices[ctx.unit].config
        missing = [d for d in ds.dims if d not in config]
        if missing:
            raise StageFailure(f"device {ctx.unit}: the generator config lacks the library dims {missing}")
        wanted, issues = _columns(ctx.spec, ctx.unit, ds)
        if wanted:
            self.prefit(ctx.spec)                        # a no-op once done: the models are in memory before any query
        answer = query.query(self.library, stratum, {d: config[d] for d in ds.dims}, sorted(set(wanted.values())) or None,
                             k=self.k, rel_sigma_max=self.rel_sigma_max) if wanted else {"quantities": {}}
        metrics = {}
        for name, column in wanted.items():
            a = answer["quantities"][column]
            if a["status"] in ("measured", "predicted") and a.get("value") is not None:
                metrics[name] = float(a["value"])
            else:
                issues.append(f"metric {name}: {a['status']}" + (f" ({a['reason']})" if a.get("reason") else ""))
        (ctx.workdir / "predictions.json").write_text(json.dumps(answer, indent=1, default=float), encoding="utf-8")
        return ChildResult(unit=ctx.unit, corner=None, metrics=metrics, issues=issues, status="ok" if not issues else "failed:predict")


def _columns(spec: Spec, device: str, ds: dataset.Dataset) -> tuple[dict[str, str], list[str]]:
    """The device's metrics as stratum columns (``Lp`` at 28 GHz reads ``Lp@28``), and an issue per metric the stratum lacks."""
    wanted, issues = {}, []
    for metric in spec.metrics_for_device(device):
        column = metric.quantity if metric.frequency_hz is None else f"{metric.quantity}@{metric.frequency_hz / 1e9:g}"
        if column in ds.columns:
            wanted[metric.name] = column
        else:
            issues.append(f"metric {metric.name}: stratum {ds.stratum} has no {column} (columns {ds.columns})")
    return wanted, issues


def surrogate_pipeline(spec: Spec, library: query.Library, strata: dict[str, str] | None = None, **predict) -> list:
    """pcell -> library prediction per device (the em_only pipeline with EMX + measure replaced by the library); ``predict``
    goes to ``Predict`` (``k``, ``rel_sigma_max``: every column's ceiling, None for each quantity's own)."""
    strata = strata or {d.id: match_stratum(library, d) for d in spec.devices}
    return [Pcell(spec), Predict(library, strata, **predict)]
