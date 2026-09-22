"""How much of the recorded library does the current geometry generation still stand behind?
For a sample of recorded sweep points per stratum: rebuild with the current generator, classify refused / physically-equal / different."""
import json, os, random, sys, tempfile, collections, re
from pathlib import Path
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.gds_compare import compare_gds
SWEEP = Path(os.environ["IC_OPT_EM_SWEEPS"])      # <em-opt>/experiments/device_db_sweep_n28/outputs/sweep
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
result = {}
reasons = collections.Counter()
for stratum in sorted(p.name for p in SWEEP.iterdir()):
    points = sorted(p for p in (SWEEP / stratum).iterdir() if (p / "geometry_manifest.json").exists() and list(p.glob("*.gds")))
    sample = random.Random(7).sample(points, min(N, len(points)))
    tally = collections.Counter()
    for point in sample:
        m = json.loads((point / "geometry_manifest.json").read_text())
        g = get_generator(m["generator_id"], plugin_module="builtin:clean_port")
        recorded = next(g_ for g_ in point.glob("*.gds"))
        with tempfile.TemporaryDirectory() as tmp:
            try:
                r = g.generate(g.config_model.model_validate(m["geometry"]["config"]), outdir=Path(tmp), gds_name=recorded.name)
            except Exception as exc:
                tally["refused"] += 1
                reasons[f"{stratum}: {type(exc).__name__}: {re.sub(r'[0-9.]+', '#', str(exc))[:90]}"] += 1
                continue
            cmp = compare_gds(str(r.gds_path), str(recorded))
            tally["equal" if cmp.get("equal") or cmp.get("physical_equal") or cmp.get("status") == "equal" else "different"] += 1
    result[stratum] = dict(tally, sampled=len(sample), total=len(points))
    print(stratum, dict(tally), flush=True)
json.dump({"per_stratum": result, "refusal_reasons": reasons.most_common(12)}, open(sys.argv[2], "w"), indent=1)
