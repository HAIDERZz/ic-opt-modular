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

# What 0.1 / em-opt ran with when a requirement left a resource out. A spec states every resource field (T15), so a
# migration writes these out and MIGRATION.md lists each one it had to fill: they are the old tools' choices, not a
# statement about the machine the migrated project will run on.
LEGACY_DEFAULTS: dict[str, int | float] = {
    "simulator.threads_per_run": 10,
    "em.threads": 4,                    # em-opt max_cpu_per_job (EMX --parallel)
    "em.memory_gb": 32.0,               # EMX --max-memory
    "em.timeout_s": 3600,               # em-opt enforced no EMX timeout; 0.2.0 defaulted to 3600 s
}


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
    """Returns (spec, hints); hints carry the HOW sections for the recipe author, and under ``Resource Defaults``
    the resource fields the requirement left out and the 0.1 values written for them."""
    s = read_requirement_sections(Path(md_path).read_text(encoding="utf-8"))
    project = s["Project"]
    source = s["Maestro Source"]
    testbenches = source.get("testbenches") or [{"id": "tb", **source}]
    corners_section = s.get("Process Corners") or {}
    optimizer = s.get("Optimizer Settings") or {}
    spectre = s["Spectre Settings"]
    filled: dict[str, int | float] = {}
    devices, em, bindings = _em_sections(s, filled)
    simulator = _simulator(spectre, filled)
    if em is not None and "max_parallel_jobs" in s["EMX Settings"]:   # em-opt: candidate workers = min(batch, EMX jobs, Spectre jobs)
        simulator["parallel_jobs"] = min(simulator["parallel_jobs"], int(s["EMX Settings"]["max_parallel_jobs"]))
    payload = {
        "project": project["project_name"],
        "description": project.get("description", ""),
        "testbenches": [_testbench(tb) for tb in testbenches],
        "devices": devices,
        "em": em,
        "bindings": bindings,
        "corners": [_corner(c) for c in corners_section.get("corners", [])],
        "corner_policy": {
            "objective": corners_section.get("objective_policy", "worst_case"),
            "constraints": corners_section.get("constraint_policy", "all_corners"),
        },
        "variables": s["Design Variables"],
        "metrics": [_metric(m) for m in s.get("Metrics", [])],
        "constraints": s.get("Constraints", []),
        "objective": s.get("Objective"),
        "simulator": simulator,
        "budget": {"max_simulations": _budget(optimizer, len(testbenches) + len(devices), len(corners_section.get("corners", [])))},
    }
    hints = {k: s[k] for k in ("Workflow", "Optimizer Settings", "Fixed Points", "Waveform Exports", "History Warm Start",
                               "Passive Diagnostic Constraints") if k in s}
    if filled:
        hints["Resource Defaults"] = filled
    return Spec.model_validate(payload), hints


def _given_or_legacy(value: Any, field: str, filled: dict[str, int | float]) -> Any:
    """The requirement's value, else the 0.1 default for ``field`` -- remembered in ``filled`` so MIGRATION.md names it."""
    if value is None:
        value = filled[field] = LEGACY_DEFAULTS[field]
    return value


# -- em-opt sections: Geometry Generator / EM Devices / EMX Settings / Nport Bindings ---------------------

_RETIRED = {"clean_port_ind_sym_ct": ("clean_port_ind_sym", {"ct_metal": "7"}, ["P1", "N1", "CT"])}   # em-opt M13: CT is an option of ind_sym
_CT_LABELS = {"p01": "P1", "p02": "N1", "p03": "CT"}                                                   # the retired generator named its labels p01..


def _device(device_id: str, generator: dict[str, Any], prefix: str | None) -> dict[str, Any]:
    gen_id, extra_fixed, ports = _RETIRED.get(generator["id"], (generator["id"], {}, None))
    labels = ports or [_CT_LABELS.get(p, p) for p in generator["port_order"]]
    plugin = "builtin:clean_port" if generator["id"] in _RETIRED else generator.get("plugin_module", "builtin:clean_port")
    fixed = {**generator.get("fixed_parameters", {}), **extra_fixed}
    fields = list(generator.get("parameters", []))
    if plugin == "builtin:clean_port":                        # em-opt's field names -> today's vocabulary (M2.2)
        from ic_opt.em.pcell.generator_plugin import translate_config

        fixed = translate_config(gen_id, fixed)
        fields = [f for f in translate_config(gen_id, dict.fromkeys(fields))]
    return {
        "id": device_id,
        "generator": gen_id,
        "plugin": plugin,
        "profile": generator["process_profile"],
        "ports": labels,
        "fixed": fixed,
        "variables": {f: (f"{prefix}.{f}" if prefix else f) for f in fields},
    }


def _em_settings(emx: dict[str, Any], filled: dict[str, int | float]) -> dict[str, Any]:
    sweep = emx.get("sweep") or {}
    extra = list(emx.get("extra_args") or [])
    resource = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in extra if a.startswith(("--max-memory", "--simultaneous-frequencies", "--parallel"))}
    grid = {k: emx[k] for k in ("edge_width_um", "max_splits", "thickness_um") if emx.get(k) is not None}
    memory = emx.get("max_memory_gb") or (float(resource["--max-memory"].rstrip("G")) if "--max-memory" in resource else None)
    simultaneous = emx.get("simultaneous_frequencies")
    if simultaneous is None and "--simultaneous-frequencies" in resource:
        simultaneous = int(resource["--simultaneous-frequencies"])
    return {
        "binary": emx.get("binary", "emx"), "process_file": emx["process_file"], "mode": emx.get("mode", "quasistatic"),
        "frequencies": ({"start_hz": sweep.get("start_hz") or 0, "stop_hz": sweep["stop_hz"], "step_hz": sweep.get("step_hz"), "num_steps": sweep.get("num_steps")}
                        if sweep.get("enabled") else list(emx.get("frequency_hz") or [])),
        "accuracy": emx.get("accuracy") if emx.get("accuracy") else (grid or None),
        "three_d_metals": emx.get("three_d_metals") or [], "via_separation_um": emx.get("via_separation_um"),
        "via_inductance": emx.get("via_inductance") or [], "via_sidewalls": emx.get("via_sidewalls") or [], "modes": emx.get("modes") or [],
        "s_impedance": emx.get("s_impedance", 50.0), "threads": _given_or_legacy(emx.get("max_cpu_per_job"), "em.threads", filled),
        "memory_gb": _given_or_legacy(memory, "em.memory_gb", filled), "timeout_s": _given_or_legacy(emx.get("timeout_s"), "em.timeout_s", filled),
        "simultaneous_frequencies": simultaneous, "verbose": emx.get("verbose"),
        "extra_args": [a for a in extra if a.split("=", 1)[0] not in resource],
    }


def _em_sections(s: dict[str, Any], filled: dict[str, int | float]) -> tuple[list[dict], dict | None, list[dict]]:
    if "EM Devices" in s:
        devices = [_device(d["id"], d["geometry"]["generator"], d.get("parameter_prefix") or d["id"]) for d in s["EM Devices"]["devices"]]
    elif "Geometry Generator" in s:
        devices = [_device("device", s["Geometry Generator"]["generator"], None)]
    else:
        return [], None, []
    em = _em_settings(s["EMX Settings"], filled) if "EMX Settings" in s else None
    ports = {d["id"]: d["ports"] for d in devices}
    # em-opt ordered the sNp columns by the EMX port *names* (p01..); its bindings' terminal_order are circuit node names,
    # so the semantic terminal order is the device's port labels in that column order.
    bindings = [{"testbench": b["testbench"], "instance": b["instance"], "device": b.get("device_id") or "device",
                 "terminals": ports[b.get("device_id") or "device"]} for b in (s.get("Nport Bindings") or {}).get("nport_bindings", [])]
    return devices, em, bindings


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
        "simulator": _simulator(spectre, {}),
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


def _simulator(spectre: dict[str, Any], filled: dict[str, int | float]) -> dict[str, Any]:
    return {
        "preset": spectre.get("preset", "ax"),
        "threads_per_run": _given_or_legacy(spectre.get("threads_per_run"), "simulator.threads_per_run", filled),
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
    """MIGRATION.md: which built-in recipe and parameters reproduce the old mode, and which resource values to review."""
    workflow = (hints.get("Workflow") or {}).get("mode", "optimize")
    recipe, params = recipe_command(hints, new_project)
    args = " ".join(f"{k}={v}" for k, v in params.items())
    lines = ["# Migrated from opt_requirement.md", "", f"spec.yaml holds the design problem. The old `{workflow}` mode maps to:", "",
             f"    ic-opt run {recipe} {new_project} {args}"]
    if (hints.get("History Warm Start") or {}).get("enabled"):
        lines += ["", "History warm start: pass those observations as `initial=` in a recipe (see coarse_to_fine.py)."]
    if hints.get("Passive Diagnostic Constraints"):
        lines += ["", "Passive Diagnostic Constraints were dropped: em-opt never evaluated them; declare device metrics (quantity + frequency_hz) and constraints instead."]
    filled = hints.get("Resource Defaults") or {}
    if filled:
        lines += ["", ("The requirement did not set these resource fields; spec.yaml carries the 0.1 defaults for them. Review each one "
                       "for your machines (ic-opt itself has no resource defaults):"), ""]
        lines += [f"    {field}: {value}" for field, value in filled.items()]
    lines += ["", ("Resources (simulator.parallel_jobs, threads_per_run, timeout_s; em.threads, memory_gb, timeout_s) must fit the "
                   "simulation host's entry in ~/.ic-opt/site.yaml: hosts.local, or hosts.<profile> with --ssh-profile."),
              "", "Preview first: add `--plan`. Remote: add `--ssh-profile PROFILE`."]
    return "\n".join(lines) + "\n"
