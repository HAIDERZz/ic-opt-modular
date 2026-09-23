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
generation of predictions.

A device only maps onto a stratum built with the same generator and the same fixed fields (metal, fixture,
leads ...): ``match_stratum`` finds it, and ``Predict`` refuses a device whose fixed fields differ.
"""

from __future__ import annotations

import json

from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.library import dataset, query
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

    def __init__(self, library: query.Library, strata: dict[str, str], *, k: float = 2.0, rel_sigma_max: float = 0.15):
        self.library, self.strata, self.k, self.rel_sigma_max = library, dict(strata), k, rel_sigma_max
        self.identity = json.dumps({"k": k, "rel_sigma_max": rel_sigma_max, "calibrate": library.calibrate,
                                    "strata": {d: [s, library.dataset(s).key] for d, s in sorted(self.strata.items())}}, separators=(",", ":"))

    def fingerprint(self, geometry: Geometry, ctx: StageContext) -> str | None:
        return None

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
        wanted = {}
        issues = []
        for metric in ctx.spec.metrics_for_device(ctx.unit):
            column = metric.quantity if metric.frequency_hz is None else f"{metric.quantity}@{metric.frequency_hz / 1e9:g}"
            if column in ds.columns:
                wanted[metric.name] = column
            else:
                issues.append(f"metric {metric.name}: stratum {stratum} has no {column} (columns {ds.columns})")
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


def surrogate_pipeline(spec: Spec, library: query.Library, strata: dict[str, str] | None = None, **predict) -> list:
    """pcell -> library prediction per device (the em_only pipeline with EMX + measure replaced by the library)."""
    strata = strata or {d.id: match_stratum(library, d) for d in spec.devices}
    return [Pcell(spec), Predict(library, strata, **predict)]
