"""Public command-line interface."""

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from agent_harness import __version__
from agent_harness.analyze import analyze as inspect_project
from agent_harness.check import check as check_ledger
from agent_harness.doctor import doctor as diagnose
from agent_harness.evidence import run_evidence
from agent_harness.filesystem import HarnessError, project_root
from agent_harness.generate import initialize
from agent_harness.models import Report
from agent_harness.roles import decide, load_config
from agent_harness.validate import validate as check_project

app = typer.Typer(
    no_args_is_help=True, help="Install and inspect a local agent development harness."
)
console = Console(markup=False)
ProjectPath = Annotated[
    Path, typer.Argument(help="Project directory; defaults to the current directory.")
]
JsonFlag = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


def emit(value: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for key, item in value.items():
            console.print(f"{key}: {json.dumps(item, ensure_ascii=False)}")


def fail(exc: Exception, as_json: bool) -> None:
    message = str(exc.errors(include_input=False)) if isinstance(exc, ValidationError) else str(exc)
    emit({"ok": False, "errors": [message]}, as_json)
    raise typer.Exit(1)


def report_result(report: Report, as_json: bool) -> None:
    emit({"ok": report.ok, **report.model_dump()}, as_json)
    if not report.ok:
        raise typer.Exit(1)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True)
    ] = False,
) -> None:
    """A setup engine; Claude Code remains the agent runtime."""


@app.command()
def init(
    path: ProjectPath = Path("."),
    name: Annotated[str | None, typer.Option(help="Project name.")] = None,
    description: Annotated[str, typer.Option(help="Project description, stored as data.")] = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing files.")
    ] = False,
    output_json: JsonFlag = False,
) -> None:
    """Generate project memory, Claude roles, skills and configuration safely."""
    try:
        generated = initialize(path, name=name, description=description, dry_run=dry_run)
        emit({"ok": True, "dry_run": dry_run, "files": generated}, output_json)
    except (HarnessError, OSError, UnicodeError, ValidationError) as exc:
        fail(exc, output_json)


@app.command()
def analyze(path: ProjectPath = Path("."), output_json: JsonFlag = False) -> None:
    """Inspect root manifests; report commands as data without executing them."""
    try:
        emit(inspect_project(path).model_dump(), output_json)
    except (HarnessError, OSError, UnicodeError) as exc:
        fail(exc, output_json)


@app.command()
def validate(path: ProjectPath = Path("."), output_json: JsonFlag = False) -> None:
    """Check configuration, manifest, required files and local customizations."""
    report_result(check_project(path), output_json)


@app.command()
def check(path: ProjectPath = Path("."), output_json: JsonFlag = False) -> None:
    """Check the task and decision ledger for completeness; commands are not rerun."""
    report_result(check_ledger(path), output_json)


evidence_app = typer.Typer(no_args_is_help=True, help="Record task evidence.")
app.add_typer(evidence_app, name="evidence")


@evidence_app.command("run")
def evidence_run(
    task: Annotated[str, typer.Argument(help="Task id, e.g. T-1.")],
    command: Annotated[list[str], typer.Argument(help="Command to run, after `--`.")],
    path: Annotated[Path, typer.Option("--path", help="Project directory.")] = Path("."),
    evidence_id: Annotated[
        str | None, typer.Option("--id", help="Evidence id; default next E<n>.")
    ] = None,
    cwd: Annotated[str, typer.Option(help="Working directory inside the project.")] = ".",
    timeout: Annotated[float | None, typer.Option(help="Timeout in seconds.")] = None,
    summary: Annotated[str | None, typer.Option(help="Summary; default last output line.")] = None,
    output_json: JsonFlag = False,
) -> None:
    """Run a command without a shell and record its result as task evidence.

    Exits 1 when the command fails; the failure is still recorded.
    """
    try:
        item = run_evidence(
            path,
            task,
            command,
            evidence_id=evidence_id,
            cwd=cwd,
            timeout=timeout,
            summary=summary,
        )
    except (HarnessError, OSError, UnicodeError, ValidationError) as exc:
        fail(exc, output_json)
    emit(
        {"ok": item.passed, "evidence": item.model_dump(mode="json", exclude_none=True)},
        output_json,
    )
    if not item.passed:
        raise typer.Exit(1)


@app.command()
def scope(
    role: Annotated[str, typer.Argument(help="Role name from .harness/config.yaml.")],
    targets: Annotated[list[str], typer.Argument(help="Paths to check.")],
    path: Annotated[Path, typer.Option("--path", help="Project directory.")] = Path("."),
    output_json: JsonFlag = False,
) -> None:
    """Report whether a role may write each path; exits 1 if any is denied."""
    try:
        root = project_root(path)
        config = load_config(root)
    except (HarnessError, OSError, UnicodeError) as exc:
        fail(exc, output_json)
    if not config.roles:
        fail(HarnessError("No roles are defined in .harness/config.yaml"), output_json)
    decisions = [decide(config, role, root, target) for target in targets]
    allowed = all(item.allowed for item in decisions)
    emit(
        {
            "ok": allowed,
            "role": role,
            "decisions": [
                {"path": d.path, "allowed": d.allowed, "reason": d.reason} for d in decisions
            ],
        },
        output_json,
    )
    if not allowed:
        raise typer.Exit(1)


@app.command()
def doctor(path: ProjectPath = Path("."), output_json: JsonFlag = False) -> None:
    """Check harness health and local prerequisites; Claude authentication is not tested."""
    report_result(diagnose(path), output_json)
