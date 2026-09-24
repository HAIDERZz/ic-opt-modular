"""ic-opt — run recipes, list and describe blocks, migrate 0.1 projects.

    ic-opt run optimize PROJECT --plan            preview: blocks, simulation count, concurrency, host
    ic-opt run optimize PROJECT budget=60          run a built-in recipe with parameters
    ic-opt run my_recipe.py PROJECT --ssh-profile lab
    ic-opt blocks | ic-opt describe sim.evaluate
    ic-opt doctor PROJECT
    ic-opt migrate OLD_PROJECT NEW_PROJECT
    ic-opt migrate-store PROJECT [--dry-run]       restamp observations with identities free of machine facts
    ic-opt call points.sobol PROJECT n=12 seed=3   call one block by name (JSON-ish key=value args)
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from ic_opt import __version__, blocks
from ic_opt import migrate as migrate_module
from ic_opt import recipe as recipe_module
from ic_opt.blocks.doctor import plan_line
from ic_opt.library import manifest as library_manifest
from ic_opt.library import query as library_query
from ic_opt.site import EnvelopeError, SiteError

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
_RESOURCE_FIELDS = ("parallel_jobs", "threads_per_run", "timeout_s", "threads", "memory_gb")


def _params(pairs: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise typer.BadParameter(f"expected key=value, got {pair!r}")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def _run(project: Path, ssh_profile: str | None, cshrc: str | None) -> recipe_module.Run:
    """The project's Run. An invalid spec.yaml, a missing site.yaml or host entry exits 2 with the reason, no traceback."""
    try:
        return recipe_module.load_run(project, ssh_profile=ssh_profile, cshrc=cshrc)
    except ValidationError as exc:
        typer.echo(f"error: {_spec_problems(project / 'spec.yaml', exc)}", err=True)
        raise typer.Exit(code=2) from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:              # SiteError is a ValueError
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _spec_problems(path: Path, exc: ValidationError) -> str:
    """Pydantic's findings as one paragraph (``field.path: message``); a missing resource field also says why."""
    errors = exc.errors()
    text = f"{path} is not a valid spec: " + "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'spec'}: {e['msg']}" for e in errors) + "."
    if any(e["type"] == "missing" and e["loc"] and e["loc"][-1] in _RESOURCE_FIELDS for e in errors):
        text += (" Resource fields have no defaults: simulator.parallel_jobs / threads_per_run / timeout_s and em.threads /"
                 " memory_gb / timeout_s are yours to set for your machines, within their ~/.ic-opt/site.yaml entries.")
    return text


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"ic-opt {__version__}")
        raise typer.Exit()


@app.callback()
def _main(version: Annotated[bool, typer.Option("--version", callback=_print_version, is_eager=True)] = False) -> None:
    """IC-Opt: composable blocks for Spectre/OCEAN design optimization."""


@app.command()
def run(
    recipe: Annotated[str, typer.Argument(help="built-in name or path to a recipe .py")],
    project: Annotated[Path, typer.Argument(help="project directory containing spec.yaml")],
    params: Annotated[list[str] | None, typer.Argument(help="recipe parameters as key=value")] = None,
    plan: Annotated[bool, typer.Option("--plan", help="print what would run; start no simulation")] = False,
    ssh_profile: Annotated[str | None, typer.Option("--ssh-profile", help="run simulations on this OpenSSH host alias")] = None,
    cshrc: Annotated[str | None, typer.Option("--cshrc", help="Cadence environment csh file")] = None,
) -> None:
    """Run a recipe (``--plan`` first: it is the only approval point)."""
    main = recipe_module.load_recipe(recipe)
    ctx = _run(project, ssh_profile, cshrc)
    kwargs = _params(params or [])
    typer.echo(f"recipe {recipe}  spec {ctx.spec.project} ({ctx.spec.fingerprint()})  {plan_line(ctx.spec, ctx.executor, ctx.limits)}")
    typer.echo(f"params {kwargs}  cshrc {ctx.cshrc or '-'}  observations {len(ctx.store.observations())}")
    token = recipe_module.PLAN_MODE.set(plan)
    try:
        main(ctx, **kwargs)
    except (SiteError, EnvelopeError) as exc:           # a job too big for its host is refused before it starts, --plan too
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        recipe_module.PLAN_MODE.reset(token)


@app.command("blocks")
def list_blocks() -> None:
    """List the blocks a recipe can compose."""
    width = max(len(n) for n in blocks.REGISTRY)
    for name, item in sorted(blocks.REGISTRY.items()):
        typer.echo(f"{name.ljust(width)}  {item.summary}")


@app.command()
def describe(name: Annotated[str, typer.Argument(help="block name, e.g. sim.evaluate")]) -> None:
    """Signature and documentation of one block."""
    if name not in blocks.REGISTRY:
        typer.echo(f"unknown block {name!r}; run `ic-opt blocks`", err=True)
        raise typer.Exit(code=2)
    typer.echo(blocks.describe(name))


@app.command()
def doctor(
    project: Path,
    ssh_profile: Annotated[str | None, typer.Option("--ssh-profile")] = None,
    cshrc: Annotated[str | None, typer.Option("--cshrc")] = None,
) -> None:
    """Check that the spec can run here (tools, license, exports, envelope, budget)."""
    ctx = _run(project, ssh_profile, cshrc)
    report = blocks.doctor(ctx.spec, ctx.executor, cshrc=ctx.cshrc, store=ctx.store, limits=ctx.limits)
    typer.echo(str(report))
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def migrate(old_project: Path, new_project: Path) -> None:
    """opt_requirement.md (0.1) -> spec.yaml (0.2) plus a note with the recipe to use."""
    spec, hints = migrate_module.spec_from_requirement(old_project / "opt_requirement.md")
    new_project.mkdir(parents=True, exist_ok=True)
    migrate_module.write_spec(spec, new_project / "spec.yaml")
    note = migrate_module.recipe_note(hints, new_project)
    (new_project / "MIGRATION.md").write_text(note, encoding="utf-8")
    typer.echo(f"wrote {new_project / 'spec.yaml'} and MIGRATION.md")
    typer.echo(note)


@app.command("migrate-store")
def migrate_store(
    project: Annotated[Path, typer.Argument(help="project or library part directory (holds .icopt/observations.jsonl)")],
    ssh_profile: Annotated[str | None, typer.Option("--ssh-profile", help="hash the EMX process file on this OpenSSH host")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="report what would change; write nothing")] = False,
) -> None:
    """Restamp a store's observations with identities free of machine facts: the spec fingerprint without resources and
    budget, the EMX process file by content. Backs up observations.jsonl first; a second run changes nothing."""
    from ic_opt import migrate_store as migrate_store_module
    from ic_opt.eval.stage import StageFailure
    from ic_opt.executor import SshExecutor

    try:
        executor = SshExecutor(ssh_profile, f"~/.ic-opt/scratch/{project.resolve().name}") if ssh_profile else None
        typer.echo(str(migrate_store_module.migrate(project, executor, dry_run=dry_run)))
    except (OSError, ValueError, RuntimeError, StageFailure) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def call(
    name: Annotated[str, typer.Argument(help="block name")],
    project: Annotated[Path, typer.Argument(help="project directory")],
    params: Annotated[list[str] | None, typer.Argument(help="block parameters as key=value")] = None,
    ssh_profile: Annotated[str | None, typer.Option("--ssh-profile")] = None,
    cshrc: Annotated[str | None, typer.Option("--cshrc")] = None,
) -> None:
    """Call one block; spec / executor / store / observations are filled in from the project (a library root fills in the library,
    a process profile directory -- one holding rule.yaml -- the profile). A report that failed (``ok`` false) exits 1."""
    if name not in blocks.REGISTRY:
        typer.echo(f"unknown block {name!r}; run `ic-opt blocks`", err=True)
        raise typer.Exit(code=2)
    fn = blocks.REGISTRY[name].fn
    kwargs = _params(params or [])
    if library_manifest.is_library(project):                        # a library root: library blocks get the library, not a run
        provided: dict[str, object] = {"library": library_query.Library(project)}
    elif (project / "rule.yaml").is_file():                          # a process profile directory
        provided = {"profile_dir": project}
    else:
        ctx = _run(project, ssh_profile, cshrc)
        provided = {"spec": ctx.spec, "executor": ctx.executor, "store": ctx.store, "observations": ctx.store.observations(), "cshrc": ctx.cshrc,
                    "limits": ctx.limits}
    args = {p: provided[p] for p in inspect.signature(fn).parameters if p in provided and p not in kwargs}
    try:
        result = fn(**args, **kwargs)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(_render(result))
    if getattr(result, "ok", True) is False:                        # env.doctor, em.validate_profile: a failed check fails the command
        raise typer.Exit(code=1)


def _render(result: object) -> str:
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json(indent=2)
    if isinstance(result, dict):
        return json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if isinstance(result, list):
        return json.dumps([_row(item) for item in result], indent=2, default=str)
    return str(result)


def _row(item: object) -> object:
    """Points and observations render as their parameters; models as dicts."""
    if hasattr(item, "params"):
        return item.params
    return item.model_dump() if hasattr(item, "model_dump") else str(item)


# -- 0.1 shim: ``ic-opt PROJECT_DIR --real|--doctor|--continue N`` (kept for one release) ----------

_LEGACY_FLAGS = {"--real", "--doctor", "--continue", "--dry-orchestration", "--cadence-cshrc", "--ssh-profile"}


def legacy_argv(argv: list[str]) -> list[str] | None:
    """Translate a 0.1 command line into the 0.2 one, migrating the project in place when needed; None if not legacy."""
    if not argv or argv[0] in {c.name or c.callback.__name__ for c in app.registered_commands} or not Path(argv[0]).is_dir():
        return None
    project = Path(argv[0])
    flags = {a for a in argv[1:] if a.startswith("--")}
    if not flags & _LEGACY_FLAGS:
        return None
    value = lambda flag: argv[argv.index(flag) + 1] if flag in argv else None
    options = [*(["--ssh-profile", value("--ssh-profile")] if value("--ssh-profile") else []),
               *(["--cshrc", value("--cadence-cshrc")] if value("--cadence-cshrc") else [])]
    if not (project / "spec.yaml").exists():
        spec, hints = migrate_module.spec_from_requirement(project / "opt_requirement.md")
        migrate_module.write_spec(spec, project / "spec.yaml")
        (project / "MIGRATION.md").write_text(migrate_module.recipe_note(hints, project), encoding="utf-8")
        typer.echo(f"migrated {project / 'opt_requirement.md'} -> {project / 'spec.yaml'} (see MIGRATION.md)", err=True)
    if "--doctor" in flags:
        return ["doctor", str(project), *options]
    _, hints = migrate_module.spec_from_requirement(project / "opt_requirement.md")
    recipe, params = migrate_module.recipe_command(hints, project)
    if value("--continue"):
        from ic_opt.store import RunStore

        done = len(RunStore(project).observations().by_step("optimize"))
        recipe, params["budget"] = "optimize", done + int(value("--continue"))
    plan = ["--plan"] if "--dry-orchestration" in flags else []
    return ["run", recipe, str(project), *[f"{k}={v}" for k, v in params.items()], *plan, *options]


def main() -> None:
    import sys

    translated = legacy_argv(sys.argv[1:])
    if translated is not None:
        typer.echo("0.1 command line; running: ic-opt " + " ".join(translated), err=True)
        sys.argv[1:] = translated
    app()


if __name__ == "__main__":
    main()
