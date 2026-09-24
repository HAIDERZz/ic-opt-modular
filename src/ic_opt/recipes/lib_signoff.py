"""Built-in recipe: lib_signoff -- run library candidates through real EMX, compare with the predictions, optionally adopt them.

``ic-opt run lib_signoff PROJECT library=<root> candidates=<json> [stratum=...] [top=10] [adopt=false] [threads=N memory_gb=G] --plan``

``candidates`` is a ``lib_design`` report (``leaders``), a ``lib.suggest`` answer (``candidates``) or a list of
parameter dicts. Each candidate is simulated with the spec of the stratum part that holds its turns level
(generator, fixed fields, EMX physics, process file -- e.g. single turns with the single-turn part's wider
sweep), so a signed-off point is a measurement of the same generation as the library.
The predictions are taken before anything runs; after EMX the quantities are measured with the library's
own definitions (band, anchors, margin) and reported per candidate: predicted mu and calibrated bounds,
measured value, z in the model's space, inside or not. ``adopt=true`` copies every ok observation, sims
directory included, into the part store it was simulated for, under fresh obs ids and an ``origin`` naming
the sign-off run, so the next dataset build includes it. Real EMX: run with ``--plan`` first; it is the
approval point. ``threads`` / ``memory_gb`` replace the part's EMX thread count and memory cap (EMX's cap is
soft: give the candidates' real peak); they are not physics, so the generation stays the library's.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import yaml

from ic_opt import blocks as b
from ic_opt.library import dataset, query
from ic_opt.recipe import Run
from ic_opt.space import Point
from ic_opt.stages.em_chain import em_only_pipeline
from ic_opt.store import RunStore


def _candidates(path: Path, dims: list[str], top: int) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    items = (doc.get("leaders") or doc.get("candidates") or []) if isinstance(doc, dict) else doc
    out = []
    for item in items:
        p = item.get("params", item)
        out.append({d: float(p[d]) for d in dims})
    return out[:top]


def _part_for(ds: dataset.Dataset, parts: list[str], params: dict) -> str:
    """The part holding most rows at this candidate's turns level (the first part when the family has no turns dim)."""
    if not ds.nt_dim:
        return parts[0]
    level = round(float(params[ds.nt_dim]))
    counts = {p: sum(1 for r in ds.rows if r.part == p and round(r.coords[ds.nt_dim]) == level) for p in parts}
    return max(parts, key=lambda p: (counts[p], -parts.index(p)))


def _z(y: float, pred: dict, k: float) -> float | None:
    """z of a measurement against a predicted interval: in log space when the interval is log-symmetric, else linear."""
    mu, lo, hi, scale = pred["value"], pred["lo"], pred["hi"], k * pred["k_scale"]
    if lo > 0 and mu > 0 and y > 0 and abs(math.log(hi / mu) - math.log(mu / lo)) < 1e-6 * max(1.0, abs(math.log(hi / mu))):
        sigma = math.log(hi / mu) / scale
        return math.log(y / mu) / sigma if sigma > 0 else None
    sigma = (hi - mu) / scale
    return (y - mu) / sigma if sigma > 0 else None


def _point(params: dict) -> Point:
    return Point({d: (str(int(v)) if float(v).is_integer() else f"{v:g}") for d, v in params.items()}, "signoff")


def main(run: Run, *, library: str, candidates: str, stratum: str | None = None, top: int = 10, adopt: bool = False, k: float = 2.0,
         threads: int | None = None, memory_gb: float | None = None) -> None:
    lib = query.Library(library)
    name = stratum or (lib.strata()[0] if len(lib.strata()) == 1 else None)
    if name is None:
        raise ValueError(f"library has strata {lib.strata()}; name one with stratum=")
    ds = lib.dataset(name)
    stratum_def = lib.manifest.strata[name]
    parts = [p.store for p in stratum_def.parts]
    wanted = _candidates(Path(candidates), ds.dims, int(top))
    groups: dict[str, list[Point]] = {}
    for params in wanted:
        groups.setdefault(_part_for(ds, parts, params), []).append(_point(params))
    run.note(f"lib_signoff: {len(wanted)} candidates of {name} through em_only with the EMX settings of "
             + ", ".join(f"{part} ({len(pts)})" for part, pts in groups.items()))
    missing = [q for q in ds.columns if lib._model_file(name, q) is None]
    if missing:
        run.note(f"lib_signoff: fitting {len(missing)} of {name}'s {len(ds.columns)} models in parallel (first use; ~2-5 min per round)")
    lib.models(name, ds.columns)                          # every column's model; sequential fits took ~2 min each on a 28-column stratum
    before = {p.key: query.query(lib, name, {d: float(p.params[d]) for d in ds.dims}, None, k=float(k)) for pts in groups.values() for p in pts}
    done: list[tuple[str, object]] = []
    for part, pts in groups.items():
        spec = dataset._spec(lib.root / part)
        resources = {k2: v for k2, v in (("threads", threads), ("memory_gb", memory_gb)) if v is not None}
        spec = spec.model_copy(update={"project": f"{run.spec.project}_signoff_{part}",
                                       "em": spec.em.model_copy(update={k2: type(getattr(spec.em, k2))(v) for k2, v in resources.items()})})
        obs = b.evaluate(spec, pts, run.executor, run.store, pipeline=em_only_pipeline(spec), step=f"lib_signoff:{part}",
                         cshrc=run.cshrc, parallel_jobs=run.jobs, site=run.site)
        done += [(part, o) for o in obs]
    if run.plan:
        return
    report, zs, inside = [], [], []
    for part, o in done:
        entry = {"obs_id": o.obs_id, "part": part, "params": o.params, "status": o.status, "quantities": {}}
        if o.status == "ok":
            device = dataset._spec(lib.root / part).devices[0]
            row = dataset._row(run.store.project_dir, run.store.project_dir, part, device, o, stratum_def)
            for q, pred in before[o.key]["quantities"].items():
                y = row.values.get(q)
                e = {"measured": y, "predicted": pred.get("value"), "lo": pred.get("lo"), "hi": pred.get("hi"), "status": pred["status"]}
                if pred["status"] == "predicted" and y is not None:
                    e["z"] = _z(y, pred, float(k))
                    e["inside"] = pred["lo"] <= y <= pred["hi"]
                    if e["z"] is not None:
                        zs.append(abs(e["z"]))
                    inside.append(e["inside"])
                entry["quantities"][q] = e
        report.append(entry)
    summary = {"stratum": name, "signed_off": len(done), "ok": sum(o.status == "ok" for _, o in done),
               "coverage": sum(inside) / len(inside) if inside else None, "median_abs_z": sorted(zs)[len(zs) // 2] if zs else None,
               "points": report, "adopted": []}
    if adopt:
        for part in groups:
            summary["adopted"] += _adopt(lib.root / part, run.store, [o for p, o in done if p == part and o.status == "ok"])
    out = run.store.root / "reports" / "lib_signoff.json"
    out.write_text(json.dumps(summary, indent=1, default=float), encoding="utf-8")
    cov = f"{summary['coverage']:.0%}" if summary["coverage"] is not None else "-"
    run.note(f"lib_signoff: {summary['ok']}/{len(done)} ok, {cov} of the measured quantities inside the calibrated interval; report {out}")
    if summary["adopted"]:
        run.note(f"lib_signoff: adopted {len(summary['adopted'])} observations into {', '.join(groups)}")


def _adopt(part_dir: Path, source: RunStore, observations) -> list[str]:
    """Copy observations and their sims directories into the library part store under fresh obs ids."""
    target = RunStore(part_dir)
    adopted = []
    with target.lock():
        for o in observations:
            new_id = target.next_obs_id()
            shutil.copytree(source.root / "sims" / o.obs_id, target.root / "sims" / new_id)
            children = {k: c.model_copy(update={"sim_dir": c.sim_dir.replace(f"/sims/{o.obs_id}/", f"/sims/{new_id}/") if c.sim_dir else None})
                        for k, c in o.children.items()}
            target.append(o.model_copy(update={"obs_id": new_id, "origin": f"signoff:{source.project_dir.name}:{o.obs_id}", "children": children}))
            adopted.append(new_id)
    with (part_dir / ".icopt" / "adopted.yaml").open("a", encoding="utf-8") as log:
        log.write(yaml.safe_dump([{"from": str(source.project_dir), "ids": adopted}]))
    return adopted
