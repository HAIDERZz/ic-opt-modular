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
    cshrc: Annotated[str | None, typer.Option("--cshrc", help="Cadence environment file on the simulation host: "
                                              "*.csh / *.cshrc / *.tcsh / *.tcshrc are sourced by csh, others by sh")] = None,
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

    try:
        executor = _ssh_executor(ssh_profile, project) if ssh_profile else None
        typer.echo(str(migrate_store_module.migrate(project, executor, dry_run=dry_run)))
    except (OSError, ValueError, RuntimeError, StageFailure) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _ssh_executor(ssh_profile: str, directory: Path):
    """The executor for ``--ssh-profile`` outside a run, built from that host's site.yaml entry as ``load_run`` builds it
    (scratch root, transfer timeout). A missing file or entry raises ``SiteError``."""
    from pathlib import PurePosixPath

    from ic_opt import site as site_module
    from ic_opt.executor import SshExecutor

    limits = site_module.load().host(ssh_profile)
    scratch = PurePosixPath(limits.scratch_root or "~/.ic-opt/scratch") / directory.resolve().name
    timeout = {} if limits.transfer_timeout_s is None else {"transfer_timeout_s": limits.transfer_timeout_s}
    return SshExecutor(ssh_profile, str(scratch), **timeout)


@app.command()
def call(
    name: Annotated[str, typer.Argument(help="block name")],
    project: Annotated[Path, typer.Argument(help="project directory")],
    params: Annotated[list[str] | None, typer.Argument(help="block parameters as key=value")] = None,
    ssh_profile: Annotated[str | None, typer.Option("--ssh-profile", help="the OpenSSH host the block works on (for a profile "
                                                    "directory: the host whose proc= path is read)")] = None,
    cshrc: Annotated[str | None, typer.Option("--cshrc")] = None,
) -> None:
    """Call one block; spec / executor / store / observations are filled in from the project (a library root fills in the library,
    a process profile directory -- one holding rule.yaml -- the profile, and with --ssh-profile that host's executor, through
    which em.validate_profile reads proc=). A report that failed (``ok`` false) exits 1."""
    if name not in blocks.REGISTRY:
        typer.echo(f"unknown block {name!r}; run `ic-opt blocks`", err=True)
        raise typer.Exit(code=2)
    fn = blocks.REGISTRY[name].fn
    kwargs = _params(params or [])
    if library_manifest.is_library(project):                        # a library root: library blocks get the library, not a run
        provided: dict[str, object] = {"library": library_query.Library(project)}
    elif (project / "rule.yaml").is_file():                          # a process profile directory
        provided = {"profile_dir": project}
        if ssh_profile:                                              # the site .proc lives on that host (ADR-0001)
            try:
                provided["executor"] = _ssh_executor(ssh_profile, project)
            except (OSError, ValueError) as exc:                     # SiteError is a ValueError
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=2) from exc
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


# -- the 0.1 command line ``ic-opt PROJECT --real|--doctor|--continue N``: 0.2 translated it, 0.3 refuses it ----------

_01_FLAGS = {"--real", "--doctor", "--continue", "--dry-orchestration", "--cadence-cshrc", "--ssh-profile"}
_01_REFUSED = """\
error: `ic-opt PROJECT --real|--doctor|--continue N` is the 0.1 command line; 0.3 no longer accepts it.
Convert a 0.1 project (opt_requirement.md) once, then run a recipe on it:
    ic-opt migrate PROJECT NEW_PROJECT                  writes spec.yaml, and MIGRATION.md with the recipe to use
    ic-opt run RECIPE NEW_PROJECT key=value ... --plan  preview; the same command without --plan runs it
A project that already has spec.yaml (0.2 converted it in place) skips the first step: NEW_PROJECT is PROJECT.
--doctor is now `ic-opt doctor NEW_PROJECT`, --continue N a re-run with budget=<points done + N>,
--dry-orchestration is --plan, and --cadence-cshrc F is --cshrc F."""


def is_01_command_line(argv: list[str]) -> bool:
    """``ic-opt PROJECT --real ...``: a first word that is neither a command nor an option, then a 0.1 flag."""
    if not argv or argv[0].startswith("-") or argv[0] in typer.main.get_command(app).commands:
        return False
    return any(arg.split("=", 1)[0] in _01_FLAGS for arg in argv[1:])


def main() -> None:
    import sys

    if is_01_command_line(sys.argv[1:]):              # refused before anything runs: the project is not touched
        typer.echo(_01_REFUSED, err=True)
        raise SystemExit(2)
    app()


if __name__ == "__main__":
    main()
