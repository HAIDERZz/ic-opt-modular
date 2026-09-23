"""Process-profile block: ``ic-opt call em.validate_profile <profile dir> proc=<site.proc> generate=true`` (the directory holds rule.yaml)."""

from __future__ import annotations

from pathlib import Path


def validate_profile(profile_dir: str | Path, proc: str | None = None, generate: bool = False,
                     families: str | list[str] | None = None, out: str | None = None):
    """Check a process rule profile directory ``<id>/rule.yaml`` before anything is generated or simulated with it.

    Stages: schema (errors as ``<yaml.path>: <message>``) -> consistency (cross-references the loader does
    not check) -> the site EMX ``proc`` when given (every emx_name a proc token, conductor thicknesses equal,
    every drawing layer in the ``define`` of its emx_name) -> with ``generate=true`` one canonical device per
    family (``families``: comma list, default all six) built through the production generator and DRC-audited;
    ``out`` keeps those artifacts. The report prints one line per finding; ``call`` exits 1 when a stage fails.
    """
    from ic_opt.em.pcell.profile_validation import validate_profile as _validate

    directory = Path(profile_dir).expanduser().resolve()
    if not (directory / "rule.yaml").is_file():
        raise FileNotFoundError(f"{directory} holds no rule.yaml (a profile directory is <id>/rule.yaml)")
    names = [f.strip() for f in families.split(",") if f.strip()] if isinstance(families, str) else families
    return _validate(directory.name, extra_dirs=(directory.parent,), proc_path=Path(proc).expanduser() if proc else None,
                     generate=bool(generate), families=names or None, out_dir=Path(out).expanduser() if out else None)
