"""Migrate legacy 0.1 projects (``opt_requirement.md`` + ``config/*.yaml``) to ``spec.yaml``.

The legacy requirement is Markdown with one YAML block per ``## Section``; the
legacy config directory is the rendered form of the same content. Both map onto
the Spec fields; optimizer settings, fixed points, waveform exports and warm
start are HOW and come back as recipe hints instead of spec fields.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ic_opt.spec import Spec

_SECTION_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)
_YAML_BLOCK_RE = re.compile(r"```yaml\s*\n(?P<body>.*?)\n```", re.DOTALL)


def read_requirement_sections(text: str) -> dict[str, Any]:
    """``## Title`` + fenced yaml -> {title: parsed yaml}; sections without yaml are skipped."""
    sections: dict[str, Any] = {}
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = _YAML_BLOCK_RE.search(text[match.end():end])
        if block:
            sections[match.group("title")] = yaml.safe_load(block.group("body"))
    return sections


def spec_from_requirement(md_path: str | Path) -> tuple[Spec, dict[str, Any]]:
    """Returns (spec, hints); hints carry the HOW sections for the recipe author."""
    s = read_requirement_sections(Path(md_path).read_text(encoding="utf-8"))
    project = s["Project"]
    source = s["Maestro Source"]
    testbenches = source.get("testbenches") or [{"id": "tb", **source}]
    corners_section = s.get("Process Corners") or {}
    optimizer = s.get("Optimizer Settings") or {}
    spectre = s["Spectre Settings"]
    payload = {
        "project": project["project_name"],
        "description": project.get("description", ""),
        "testbenches": [_testbench(tb) for tb in testbenches],
        "corners": [_corner(c) for c in corners_section.get("corners", [])],
        "corner_policy": {
            "objective": corners_section.get("objective_policy", "worst_case"),
            "constraints": corners_section.get("constraint_policy", "all_corners"),
        },
        "variables": s["Design Variables"],
        "metrics": [_metric(m) for m in s.get("Metrics", [])],
        "constraints": s.get("Constraints", []),
        "objective": s.get("Objective"),
        "simulator": _simulator(spectre),
        "budget": {"max_simulations": _budget(optimizer, len(testbenches), len(corners_section.get("corners", [])))},
    }
    hints = {k: s[k] for k in ("Workflow", "Optimizer Settings", "Fixed Points", "Waveform Exports", "History Warm Start") if k in s}
    return Spec.model_validate(payload), hints


def spec_from_config_dir(config_dir: str | Path) -> Spec:
    """Legacy ``config/*.yaml`` (the rendered requirement) -> Spec."""
    cfg = Path(config_dir)
    load = lambda name: yaml.safe_load((cfg / name).read_text(encoding="utf-8")) if (cfg / name).exists() else None
    project = load("project_config.yaml")
    testbenches = load("testbenches.yaml")
    corners = load("process_corners.yaml") or {}
    variables = load("variables.yaml")
    metrics = load("metrics.yaml")
    spectre = load("spectre.yaml")["spectre"]
    optimizer = (load("optimizer.yaml") or {}).get("optimizer", {})
    tb_list = (
        [{"id": t["id"], "maestro_point_root": t["maestro_point_root"], **{k: t[k] for k in _TB_FIELDS if k in t}} for t in testbenches["testbenches"]]
        if testbenches
        else [{"id": "tb", "maestro_point_root": project["netlist"]["exported_input_scs"], **project["testbench"]}]
    )
    payload = {
        "project": project["project"]["name"],
        "description": project["project"].get("description", ""),
        "testbenches": tb_list,
        "corners": [_corner(c) for c in corners.get("corners", [])],
        "corner_policy": {
            "objective": corners.get("objective_policy", "worst_case"),
            "constraints": corners.get("constraint_policy", "all_corners"),
        },
        "variables": variables["variables"],
        "metrics": [
            {"name": m["name"], "unit": m["unit"], "expression": m["ocean"]["expression"], "testbench": m.get("testbench"),
             "result": m["ocean"].get("result"), "required_signals": m.get("required_signals", [])}
            for m in metrics["metrics"]
        ],
        "constraints": metrics.get("constraints", []),
        "objective": metrics.get("objective"),
        "simulator": _simulator(spectre),
        "budget": {"max_simulations": _budget(optimizer, len(tb_list), len(corners.get("corners", [])))},
    }
    return Spec.model_validate(payload)


_TB_FIELDS = ("virtuoso_library", "cell", "design_view", "maestro_view", "test_name", "corner")


def _testbench(tb: dict[str, Any]) -> dict[str, Any]:
    return {"id": tb["id"], "maestro_point_root": tb["maestro_point_root"], **{k: tb[k] for k in _TB_FIELDS if k in tb}}


def _corner(c: dict[str, Any]) -> dict[str, Any]:
    out = {"id": c["id"]}
    for key in ("model_section", "model_file", "variables", "description"):
        if c.get(key) is not None:
            out[key] = c[key]
    return out


def _metric(m: dict[str, Any]) -> dict[str, Any]:
    out = {"name": m["name"], "unit": m["unit"], "expression": m["ocean_expression"]}
    for key in ("testbench", "result", "required_signals"):
        if m.get(key) is not None:
            out[key] = m[key]
    return out


def _simulator(spectre: dict[str, Any]) -> dict[str, Any]:
    return {
        "preset": spectre.get("preset", "ax"),
        "threads_per_run": spectre.get("threads_per_run", 10),
        "parallel_jobs": spectre["parallel_jobs"],
        "timeout_s": spectre["timeout_s"],
        "license_check": spectre.get("require_license_check", True),
        "keep_failed_runs": spectre.get("keep_failed_runs", True),
        "keep_successful_runs": spectre.get("keep_successful_runs", True),
    }


def _budget(optimizer: dict[str, Any], n_tb: int, n_corners: int) -> int:
    evaluations = int(optimizer.get("max_evaluations", 100))
    return evaluations * n_tb * max(1, n_corners)


def write_spec(spec: Spec, path: str | Path) -> None:
    Path(path).write_text(yaml.safe_dump(spec.model_dump(mode="json", exclude_defaults=True), sort_keys=False, allow_unicode=True), encoding="utf-8")


def recipe_command(hints: dict[str, Any], new_project: Path) -> tuple[str, dict[str, Any]]:
    """The built-in recipe and parameters that reproduce the old mode; writes points.json / waveforms.json for fix_run."""
    workflow = (hints.get("Workflow") or {}).get("mode", "optimize")
    optimizer = hints.get("Optimizer Settings") or {}
    if workflow == "fix_run":
        points = (hints.get("Fixed Points") or {}).get("points", [])
        (new_project / "points.json").write_text(json.dumps([p["parameters"] for p in points], indent=2))
        params: dict[str, Any] = {"points": "points.json"}
        waves = (hints.get("Waveform Exports") or {}).get("exports", [])
        if waves:
            (new_project / "waveforms.json").write_text(json.dumps(
                [{"name": w["name"], "expression": w["expression"], **({"testbench": w["testbench"]} if w.get("testbench") else {})} for w in waves], indent=2))
            params["waveforms"] = "waveforms.json"
        return "fix_run", params
    strategy = optimizer.get("strategy") or {"turbo": "turbo", "openbox": "openbox_gp_eic", "random": "random"}.get(optimizer.get("algorithm"), "openbox_gp_eic")
    return "optimize", {"strategy": strategy, "budget": optimizer.get("max_evaluations", 30), "batch": optimizer.get("batch_size", 10),
                        "seed": optimizer.get("random_seed", 0)}


def recipe_note(hints: dict[str, Any], new_project: Path) -> str:
    """MIGRATION.md: which built-in recipe and parameters reproduce the old mode."""
    workflow = (hints.get("Workflow") or {}).get("mode", "optimize")
    recipe, params = recipe_command(hints, new_project)
    args = " ".join(f"{k}={v}" for k, v in params.items())
    lines = ["# Migrated from opt_requirement.md", "", f"spec.yaml holds the design problem. The old `{workflow}` mode maps to:", "",
             f"    ic-opt run {recipe} {new_project} {args}"]
    if (hints.get("History Warm Start") or {}).get("enabled"):
        lines += ["", "History warm start: pass those observations as `initial=` in a recipe (see coarse_to_fine.py)."]
    lines += ["", "Preview first: add `--plan`. Remote: add `--ssh-profile PROFILE`."]
    return "\n".join(lines) + "\n"
