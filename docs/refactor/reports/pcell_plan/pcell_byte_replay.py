"""Byte-identity harness for generator refactors (T13.11): regenerate devices and compare GDS + manifest bytes.

  library ROOT OUT_JSON [--sample N] [--jobs J]
      every ok point of every store under a library root (or N per store): regenerate from the recorded
      geometry_manifest.json config with the same GDS name, compare sha256 of the GDS and the manifest
  record OUT_DIR PROFILE [PROFILE ...]
      freeze a baseline per profile: the canonical smoke config of every family (profile_validation) for the top
      three stack conductors, spelled by name and by digit, with outer-diameter / width variants; the configs
      themselves are stored, so a later check regenerates exactly these configs (a refactor cannot move them)
  check OUT_DIR PROFILE [PROFILE ...]
      regenerate a recorded baseline and compare (a refusal must refuse with the same message)

Private profiles need IC_OPT_PROFILE_DIRS. The library root is outside the repository; nothing here is committed.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

FAMILIES = ("clean_port_ind_sym", "clean_port_xfm_bs", "clean_port_xfm_ms", "clean_port_xfm_balun", "clean_port_xfm_tw", "clean_port_xfm_il")
PAIRED = ("clean_port_xfm_bs", "clean_port_xfm_ms")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(generator_id: str, config: dict, gds_name: str) -> dict:
    """{'gds': sha, 'manifest': sha} or {'refused': 'Type: message'}."""
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS

    gen = PLUGIN_GENERATORS[generator_id]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = gen.generate(gen.config_model.model_validate(config), outdir=Path(tmp), gds_name=gds_name)
        except Exception as exc:  # noqa: BLE001 -- a refusal is an outcome to compare, not an error here
            return {"refused": f"{type(exc).__name__}: {exc}"}
        return {"gds": sha(result.gds_path), "manifest": sha(Path(tmp) / "geometry_manifest.json")}


# -- library replay -------------------------------------------------------------------------------------------------

def _replay_one(work: str) -> tuple[str, str, str]:
    work = Path(work)
    manifest = json.loads((work / "geometry_manifest.json").read_text())
    gds = next(work.glob("*.gds"))
    out = generate(manifest["generator_id"], manifest["geometry"]["config"], gds.name)
    if "refused" in out:
        return str(work), "refused", out["refused"][:160]
    same_gds, same_manifest = out["gds"] == sha(gds), out["manifest"] == sha(work / "geometry_manifest.json")
    return str(work), "equal" if same_gds and same_manifest else ("gds differs" if not same_gds else "manifest differs"), ""


def library(root: Path, out: Path, sample: int | None, jobs: int) -> None:
    works = []
    for store in sorted(p for p in root.iterdir() if (p / ".icopt" / "observations.jsonl").is_file()):
        rows = [json.loads(line) for line in (store / ".icopt" / "observations.jsonl").read_text().splitlines() if line.strip()]
        ok = [r["obs_id"] for r in rows if r["status"] == "ok"]
        if sample:
            ok = random.Random(11).sample(ok, min(sample, len(ok)))
        for obs in ok:
            for work in (store / ".icopt" / "sims" / obs / "em").iterdir():
                if (work / "geometry_manifest.json").is_file():
                    works.append(str(work))
    tally, per_store, examples = collections.Counter(), collections.defaultdict(collections.Counter), []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for work, outcome, detail in pool.map(_replay_one, works, chunksize=8):
            tally[outcome] += 1
            per_store[Path(work).parts[-6]][outcome] += 1
            if outcome != "equal" and len(examples) < 40:
                examples.append({"work": work, "outcome": outcome, "detail": detail})
    report = {"total": len(works), "tally": dict(tally), "per_store": {k: dict(v) for k, v in per_store.items()}, "examples": examples}
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("total", "tally", "per_store")}, indent=1))


# -- recorded baselines ---------------------------------------------------------------------------------------------

def _stack(profile) -> list[str]:
    widths = profile.layout_rules.metal_width_space
    return [name for name in profile.layer_catalog.conductors if name in widths]


def _spellings(name: str) -> list[str]:
    return [name, name[1:]] if name[:1] in "Mm" and name[1:].isdigit() else [name]


def _variants(config: dict) -> list[dict]:
    out = []
    for od_scale in (1.0, 0.8, 1.3):
        for w_scale in (1.0, 0.75):
            c = json.loads(json.dumps(config))
            for key in list(c):
                if key.endswith("outer_diameter_um") and isinstance(c[key], (int, float)):
                    c[key] = round(c[key] * od_scale, 1)
                if key.endswith("width_um") and isinstance(c[key], (int, float)):
                    c[key] = round(c[key] * w_scale, 2)
            out.append(c)
    return out


def configs_for(profile_id: str) -> list[dict]:
    from ic_opt.em.pcell._pcell_core import _metal_index, max_opening
    from ic_opt.em.pcell.process_rules import get_process_rule_profile
    from ic_opt.em.pcell.profile_validation import _canonical_config

    profile = get_process_rule_profile(profile_id)
    stack = _stack(profile)
    out = []
    for i in range(len(stack) - 1, max(len(stack) - 4, 1), -1):
        top, below = stack[i], stack[i - 1]
        for family in FAMILIES:
            try:
                base = _canonical_config(family, profile, profile_id, _metal_index(top), max_opening)
            except Exception as exc:  # noqa: BLE001
                out.append({"family": family, "config": None, "note": f"canonical config: {type(exc).__name__}: {exc}"})
                continue
            for spelled_top in _spellings(top):
                for spelled_below in (_spellings(below) if family in PAIRED else [None]):
                    c = dict(base)
                    if family in PAIRED:
                        c["primary_metal"], c["secondary_metal"] = spelled_top, spelled_below
                    else:
                        c["metal"] = spelled_top
                    for v in _variants(c):
                        out.append({"family": family, "config": v, "note": f"top {spelled_top}" + (f" / {spelled_below}" if spelled_below else "")})
    return out


def record(out_dir: Path, profiles: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for pid in profiles:
        cases = configs_for(pid)
        for case in cases:
            case["result"] = generate(case["family"], case["config"], "baseline.gds") if case["config"] else {"refused": case["note"]}
        (out_dir / f"{pid}.json").write_text(json.dumps(cases, indent=1))
        ok = sum("gds" in c["result"] for c in cases)
        print(f"{pid}: {len(cases)} cases, {ok} generated, {len(cases) - ok} refused")


def check(out_dir: Path, profiles: list[str]) -> None:
    bad = 0
    for pid in profiles:
        cases = json.loads((out_dir / f"{pid}.json").read_text())
        diffs = []
        for case in cases:
            if case["config"] is None:
                continue
            now = generate(case["family"], case["config"], "baseline.gds")
            if now != case["result"]:
                diffs.append({"family": case["family"], "note": case["note"], "before": case["result"], "after": now})
        bad += len(diffs)
        print(f"{pid}: {len(cases)} cases, {len(diffs)} differ")
        for d in diffs[:10]:
            print("   ", json.dumps(d)[:400])
    raise SystemExit(1 if bad else 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("library")
    a.add_argument("root", type=Path)
    a.add_argument("out", type=Path)
    a.add_argument("--sample", type=int, default=None)
    a.add_argument("--jobs", type=int, default=8)
    for mode in ("record", "check"):
        b = sub.add_parser(mode)
        b.add_argument("out_dir", type=Path)
        b.add_argument("profiles", nargs="+")
    args = ap.parse_args()
    if args.mode == "library":
        library(args.root, args.out, args.sample, args.jobs)
    elif args.mode == "record":
        record(args.out_dir, args.profiles)
    else:
        check(args.out_dir, args.profiles)


if __name__ == "__main__":
    main()
