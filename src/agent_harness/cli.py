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
from agent_harness.hooks import install as install_hook
from agent_harness.hooks import run_pre_tool_use
from agent_harness.models import PlanReport, Report
from agent_harness.roles import decide, load_config
from agent_harness.schedule import plan as plan_next
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


def report_result(report: Report | PlanReport, as_json: bool) -> None:
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
    timeout: Annotated[
        float, typer.Option(help="Timeout in seconds; the run is recorded as exit 124.")
    ] = 3600.0,
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
    warnings = [] if item.commit else ["No git commit recorded (not a Git checkout?)"]
    emit(
        {
            "ok": item.passed,
            "evidence": item.model_dump(mode="json", exclude_none=True),
            "warnings": warnings,
        },
        output_json,
    )
    if not item.passed:
        raise typer.Exit(1)


@app.command("next")
def next_tasks(path: ProjectPath = Path("."), output_json: JsonFlag = False) -> None:
    """Show which tasks can start now and what the others wait for; runs nothing."""
    report_result(plan_next(path), output_json)


def make_engine():
    """The engine `harness run` uses; tests replace it."""
    from agent_harness.engine.claude import ClaudeEngine, load_sdk

    return ClaudeEngine(load_sdk())


def _describe(event) -> str | None:
    from agent_harness.engine import events

    if isinstance(event, events.Started):
        return f"session {event.session_id}"
    if isinstance(event, events.Text):
        return event.text.strip()
    if isinstance(event, events.ToolUse):
        detail = event.input.get("command") or event.input.get("file_path") or ""
        return f"[{event.name}] {detail}".rstrip()
    if isinstance(event, events.ToolDenied):
        return f"[denied {event.name}] {event.reason}"
    if isinstance(event, events.Waiting):
        return f"[usage limit] waiting {int(event.seconds)}s, then resuming the session"
    if isinstance(event, events.RateLimit) and event.status != "allowed":
        return f"[usage limit {event.status}] resets at {event.resets_at}"
    return None


@app.command("run")
def run_command(
    task: Annotated[str, typer.Argument(help="Task id from .harness/tasks, e.g. T-3.")],
    path: ProjectPath = Path("."),
    role: Annotated[
        str | None, typer.Option(help="Role to act as; defaults to the task owner.")
    ] = None,
    max_resumes: Annotated[
        int, typer.Option(min=0, help="How often to resume after a usage limit.")
    ] = 3,
    output_json: JsonFlag = False,
) -> None:
    """Run one READY or IN_PROGRESS task through the agent engine; the ledger is not changed."""
    import asyncio

    from agent_harness.engine.runner import run_task

    try:
        engine = make_engine()
        printer = (
            None if output_json else (lambda e: (line := _describe(e)) and console.print(line))
        )
        outcome = asyncio.run(
            run_task(path, task, engine, role=role, max_resumes=max_resumes, on_event=printer)
        )
    except (HarnessError, OSError, UnicodeError) as exc:
        fail(exc, output_json)
    summary = {
        "ok": outcome.status == "completed",
        "status": outcome.status,
        "task": outcome.task_id,
        "role": outcome.role,
        "session_id": outcome.session_id,
        "sessions": outcome.sessions,
        "turns": outcome.turns,
        "cost_usd": outcome.cost_usd,
        "waited_seconds": outcome.waited_seconds,
        "result": outcome.result,
        "errors": [outcome.error] if outcome.error else [],
    }
    emit(summary, output_json)
    if outcome.status != "completed":
        raise typer.Exit(1)


@app.command("wave")
def wave_command(
    path: ProjectPath = Path("."),
    max_parallel: Annotated[int, typer.Option(min=1, help="Most tasks to run at once.")] = 3,
    max_resumes: Annotated[
        int, typer.Option(min=0, help="How often to resume after a usage limit.")
    ] = 3,
    review: Annotated[
        bool, typer.Option("--review", help="Run the reviewer on tasks that reach IN_REVIEW.")
    ] = False,
    reviewer: Annotated[str, typer.Option(help="Role that reviews with --review.")] = "reviewer",
    output_json: JsonFlag = False,
) -> None:
    """Run every task `harness next` would start, in parallel, and record how each ended.

    Finished tasks go to IN_REVIEW, failed ones to BLOCKED, rate-limited ones back to READY.
    """
    import asyncio

    from agent_harness.orchestrator import run_wave
    from agent_harness.review import review_all

    try:
        engine = make_engine()
        printer = (
            None
            if output_json
            else (lambda t, e: (line := _describe(e)) and console.print(f"{t}: {line}"))
        )
        results = asyncio.run(
            run_wave(
                path,
                engine,
                max_parallel=max_parallel,
                on_event=printer,
                max_resumes=max_resumes,
            )
        )
        reviews = []
        if review:
            reviews = asyncio.run(
                review_all(
                    path,
                    engine,
                    task_ids=[r.task_id for r in results if r.status == "IN_REVIEW"],
                    role=reviewer,
                    max_parallel=max_parallel,
                    on_event=printer,
                    max_resumes=max_resumes,
                )
            )
    except (HarnessError, OSError, UnicodeError) as exc:
        fail(exc, output_json)
    tasks = [
        {
            "task": r.task_id,
            "run": r.outcome.status,
            "status": r.status,
            "cost_usd": r.outcome.cost_usd,
            "note": r.note,
            "recorded": r.recorded,
        }
        for r in results
    ]
    ok = all(r.status == "IN_REVIEW" and r.recorded for r in results)
    payload = {"ok": ok, "started": len(results), "tasks": tasks}
    if review:
        payload["reviews"] = _review_rows(reviews)
        ok = ok and all(r.status == "DONE" and r.recorded for r in reviews)
        payload["ok"] = ok
    emit(payload, output_json)
    if not ok:
        raise typer.Exit(1)


def _review_rows(reviews) -> list[dict]:
    return [
        {
            "task": r.task_id,
            "verdict": r.verdict,
            "status": r.status,
            "cost_usd": r.outcome.cost_usd,
            "note": r.note,
            "recorded": r.recorded,
        }
        for r in reviews
    ]


@app.command("review")
def review_command(
    path: ProjectPath = Path("."),
    task: Annotated[
        list[str] | None, typer.Option("--task", help="Review only this task; repeatable.")
    ] = None,
    role: Annotated[
        str, typer.Option(help="Role that reviews; never the task owner.")
    ] = "reviewer",
    max_parallel: Annotated[int, typer.Option(min=1, help="Most reviews at once.")] = 3,
    max_resumes: Annotated[
        int, typer.Option(min=0, help="How often to resume after a usage limit.")
    ] = 3,
    output_json: JsonFlag = False,
) -> None:
    """Have the reviewer role judge IN_REVIEW tasks: approved ones become DONE.

    Changes requested send the task back to READY with the reviewer's notes. An approval
    counts only if every acceptance criterion is backed by passing evidence.
    """
    import asyncio

    from agent_harness.review import review_all

    try:
        engine = make_engine()
        printer = (
            None
            if output_json
            else (lambda t, e: (line := _describe(e)) and console.print(f"{t}: {line}"))
        )
        reviews = asyncio.run(
            review_all(
                path,
                engine,
                task_ids=task,
                role=role,
                max_parallel=max_parallel,
                on_event=printer,
                max_resumes=max_resumes,
            )
        )
    except (HarnessError, OSError, UnicodeError) as exc:
        fail(exc, output_json)
    ok = all(r.status == "DONE" and r.recorded for r in reviews)
    emit({"ok": ok, "reviewed": len(reviews), "tasks": _review_rows(reviews)}, output_json)
    if not ok:
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


hook_app = typer.Typer(no_args_is_help=True, help="Claude Code hook integration.")
app.add_typer(hook_app, name="hook")


@hook_app.command("pre-tool-use")
def hook_pre_tool_use() -> None:
    """Read a Claude Code PreToolUse event on stdin; deny writes outside the role scope."""
    import sys

    run_pre_tool_use(sys.stdin, sys.stdout, sys.stderr)


@hook_app.command("install")
def hook_install(
    path: ProjectPath = Path("."),
    command: Annotated[
        str, typer.Option(help="How Claude Code should invoke harness, e.g. 'uv run harness'.")
    ] = "harness",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the result without writing.")
    ] = False,
    output_json: JsonFlag = False,
) -> None:
    """Register the PreToolUse hook in .claude/settings.json, keeping existing settings."""
    try:
        emit({"ok": True, **install_hook(path, command=command, dry_run=dry_run)}, output_json)
    except (HarnessError, OSError, UnicodeError) as exc:
        fail(exc, output_json)


@app.command()
def doctor(path: ProjectPath = Path("."), output_json: JsonFlag = False) -> None:
    """Check harness health and local prerequisites; Claude authentication is not tested."""
    report_result(diagnose(path), output_json)
