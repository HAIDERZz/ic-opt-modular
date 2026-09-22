"""Replay recorded sweep points through the current generators: does today's geometry still stand behind them?

A record directory holds ``<stratum>/<point>/geometry_manifest.json`` next to the
GDS the point was simulated with (em-opt's sweep layout, also what the pcell stage
writes). Each point is rebuilt from the manifest's config and classified:

* ``equal``      the rebuild is physically equal to the recorded GDS (``gds_compare``);
* ``different``  it is not — the recorded S-parameters describe geometry the current
                 code no longer draws;
* ``refused``    the current generator rejects the configuration.

CLI: ``python -m ic_opt.em.pcell.replay ROOT [--sample N] [--json OUT]``.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ic_opt.em.pcell.gds_compare import compare_gds
from ic_opt.em.pcell.registry import get_generator


@dataclass(frozen=True)
class Replay:
    stratum: str
    point: str
    status: str                     # equal | different | refused
    detail: str = ""                # refusal message, or the changed layers "66/0:1.2um2 ..."


def replay_point(point_dir: Path, *, plugin_module: str = "builtin:clean_port") -> Replay:
    manifest = json.loads((point_dir / "geometry_manifest.json").read_text(encoding="utf-8"))
    recorded = next(iter(sorted(point_dir.glob("*.gds"))))
    generator = get_generator(manifest["generator_id"], plugin_module=plugin_module)
    stratum, point = point_dir.parent.name, point_dir.name
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = generator.generate(generator.config_model.model_validate(manifest["geometry"]["config"]), outdir=Path(tmp), gds_name=recorded.name)
        except Exception as exc:            # every refusal class counts, not only PortError
            return Replay(stratum, point, "refused", f"{type(exc).__name__}: {exc}")
        cmp = compare_gds(recorded, result.gds_path)
    if cmp["physical_equal"]:
        return Replay(stratum, point, "equal")
    changed = " ".join(f"{c['layer_datatype']}:{c['xor_area_um2']:.3g}um2" for c in cmp["changed_layers"])
    labels = "" if cmp["labels_equal"] else f" labels-{len(cmp['removed_labels'])}+{len(cmp['added_labels'])}"
    return Replay(stratum, point, "different", changed + labels)


def points(root: Path, *, sample: int | None = None, seed: int = 7) -> list[Path]:
    """Every ``<stratum>/<point>`` under ``root`` that has a manifest and a GDS; ``sample`` per stratum when given."""
    out: list[Path] = []
    for stratum in sorted(p for p in root.iterdir() if p.is_dir()):
        found = sorted(p for p in stratum.iterdir() if (p / "geometry_manifest.json").exists() and any(p.glob("*.gds")))
        out += found if sample is None else random.Random(seed).sample(found, min(sample, len(found)))
    return out


def replay(root: Path, *, sample: int | None = None, seed: int = 7) -> list[Replay]:
    return [replay_point(p) for p in points(root, sample=sample, seed=seed)]


def summary(results: list[Replay]) -> dict[str, dict[str, int]]:
    per: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in results:
        per[r.stratum][r.status] += 1
    return {s: {k: per[s][k] for k in ("equal", "different", "refused")} for s in sorted(per)}


def refusal_reasons(results: list[Replay], top: int = 10) -> list[tuple[str, int]]:
    """The most common refusal messages with their numbers masked, so one reason counts once."""
    masked = collections.Counter(re.sub(r"-?\d+(\.\d+)?", "#", r.detail)[:120] for r in results if r.status == "refused")
    return masked.most_common(top)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--sample", type=int, default=None, help="points per stratum (default: all)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", type=Path, default=None, help="write every point's result here")
    args = parser.parse_args(argv)
    results = replay(args.root, sample=args.sample, seed=args.seed)
    for stratum, counts in summary(results).items():
        print(f"{stratum:<20} " + " ".join(f"{k}={v:<4}" for k, v in counts.items()))
    for reason, n in refusal_reasons(results):
        print(f"{n:>4}  {reason}")
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in results], indent=1), encoding="utf-8")
    return 0 if all(r.status == "equal" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
