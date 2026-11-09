"""Validate the structured task and decision ledger without executing anything.

`check` verifies that recorded claims are complete and consistent. It does not
rerun commands, so it cannot prove that recorded evidence is truthful.
"""

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import ValidationError

from agent_harness.filesystem import HarnessError, managed_path, project_root, read_text
from agent_harness.models import Decision, LedgerReport, Task

TASKS_DIR = ".harness/tasks"
DECISIONS_DIR = ".harness/decisions"
MAX_LEDGER_FILES = 5000
ACTIVE = {"READY", "IN_PROGRESS", "IN_REVIEW", "DONE"}
M = TypeVar("M", Task, Decision)


def _load(root: Path, relative_dir: str, model: type[M], report: LedgerReport) -> dict[str, M]:
    items: dict[str, M] = {}
    directory = managed_path(root, relative_dir)
    try:
        if not directory.exists():
            return items
        if not directory.is_dir():
            report.errors.append(f"{relative_dir} is not a directory")
            return items
        entries = sorted(directory.iterdir())
    except OSError as exc:
        report.errors.append(f"{relative_dir}: cannot be listed: {exc}")
        return items
    if len(entries) > MAX_LEDGER_FILES:
        report.errors.append(f"{relative_dir} has more than {MAX_LEDGER_FILES} entries")
        return items
    for entry in entries:
        relative = f"{relative_dir}/{entry.name}"
        try:
            if entry.is_symlink():
                report.errors.append(f"{relative}: symlinks are not supported")
                continue
            if entry.suffix != ".yaml" or not entry.is_file():
                report.warnings.append(
                    f"{relative}: ignored (only *.yaml files are ledger entries)"
                )
                continue
            item = model.model_validate(yaml.safe_load(read_text(entry)))
        except ValidationError as exc:
            for error in exc.errors(include_input=False):
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                report.errors.append(f"{relative}: {location}: {error['msg']}")
            continue
        except RecursionError:
            report.errors.append(f"{relative}: YAML nesting is too deep")
            continue
        except (HarnessError, OSError, UnicodeError, yaml.YAMLError) as exc:
            report.errors.append(f"{relative}: {exc}")
            continue
        if entry.stem != item.id:
            report.errors.append(f"{relative}: file name must match id {item.id!r}")
            continue
        items[item.id] = item
    return items


def _check_task(
    task: Task,
    tasks: dict[str, Task],
    decisions: dict[str, Decision],
    root: Path,
    errors: list[str],
) -> None:
    where = f"{TASKS_DIR}/{task.id}.yaml"
    for kind, values in (
        ("acceptance", [c.id for c in task.acceptance]),
        ("evidence", [e.id for e in task.evidence]),
    ):
        if len(values) != len(set(values)):
            errors.append(f"{where}: duplicate {kind} ids")
    evidence = {item.id: item for item in task.evidence}

    if task.status in {"BLOCKED", "CANCELLED"} and not task.reason.strip():
        errors.append(f"{where}: {task.status} requires a reason")

    if len(task.depends_on) != len(set(task.depends_on)):
        errors.append(f"{where}: duplicate depends_on entries")
    for ref in task.depends_on:
        if ref == task.id:
            errors.append(f"{where}: task cannot depend on itself")
            continue
        if ref.startswith("DEC-"):
            target, done = decisions.get(ref), "DECIDED"
            state = target.status if target else None
        else:
            target, done = tasks.get(ref), "DONE"
            state = target.status if target else None
        if target is None:
            errors.append(f"{where}: depends_on references unknown {ref}")
        elif task.status in ACTIVE and state != done:
            errors.append(
                f"{where}: {task.status} but dependency {ref} is {state}; "
                "use BLOCKED or BACKLOG until it is satisfied"
            )

    for item in task.evidence:
        if item.output is None:
            continue
        try:
            if not managed_path(root, item.output).is_file():
                errors.append(f"{where}: evidence {item.id} output not found: {item.output}")
        except (HarnessError, OSError) as exc:
            errors.append(f"{where}: evidence {item.id}: {exc}")

    for criterion in task.acceptance:
        for ref in criterion.verified_by:
            if ref not in evidence:
                errors.append(f"{where}: {criterion.id} references unknown evidence {ref}")

    if task.status in {"IN_REVIEW", "DONE"}:
        if not task.acceptance:
            errors.append(f"{where}: {task.status} requires acceptance criteria")
        if not task.evidence:
            errors.append(f"{where}: {task.status} requires recorded evidence")

    if task.status != "DONE":
        return
    for criterion in task.acceptance:
        if not criterion.verified_by:
            errors.append(f"{where}: DONE but {criterion.id} has no verified_by evidence")
        for ref in criterion.verified_by:
            if ref in evidence and not evidence[ref].passed:
                errors.append(f"{where}: DONE but {criterion.id} relies on failing evidence {ref}")
    review = task.review
    if review.status == "approved":
        if not review.by.strip():
            errors.append(f"{where}: approved review must name the reviewer")
        elif review.by.strip().casefold() == task.owner.strip().casefold():
            errors.append(f"{where}: owner cannot approve their own task")
    elif review.status == "unavailable":
        if not review.notes.strip():
            errors.append(f"{where}: unavailable review requires notes explaining why")
    else:
        errors.append(f"{where}: DONE requires review approved or unavailable, not {review.status}")


def _cycles(tasks: dict[str, Task]) -> list[str]:
    """Return one representative path per dependency cycle among tasks.

    Iterative depth-first search, so long dependency chains cannot exhaust the stack.
    """
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished
    found: list[str] = []
    for start in sorted(tasks):
        if start in state:
            continue
        state[start] = 1
        path = [start]
        stack = [iter(tasks[start].depends_on)]
        while stack:
            ref = next(stack[-1], None)
            if ref is None:
                state[path.pop()] = 2
                stack.pop()
            elif ref not in tasks or ref == path[-1]:
                continue
            elif state.get(ref) == 1:
                found.append(" -> ".join([*path[path.index(ref) :], ref]))
            elif ref not in state:
                state[ref] = 1
                path.append(ref)
                stack.append(iter(tasks[ref].depends_on))
    return found


def check(path: Path) -> LedgerReport:
    report = LedgerReport()
    try:
        root = project_root(path)
        tasks = _load(root, TASKS_DIR, Task, report)
        decisions = _load(root, DECISIONS_DIR, Decision, report)
    except (HarnessError, OSError) as exc:
        report.errors.append(str(exc))
        return report

    for task_id in sorted(tasks):
        _check_task(tasks[task_id], tasks, decisions, root, report.errors)
    for cycle in _cycles(tasks):
        report.errors.append(f"dependency cycle: {cycle}")

    for status in sorted({t.status for t in tasks.values()}):
        report.summary[f"tasks_{status.lower()}"] = sum(t.status == status for t in tasks.values())
    report.summary["tasks"] = len(tasks)
    report.summary["decisions"] = len(decisions)
    report.summary["decisions_pending"] = sum(d.status == "PENDING" for d in decisions.values())

    if not tasks and not decisions:
        report.warnings.append(f"No ledger entries found under {TASKS_DIR} or {DECISIONS_DIR}")
    report.checks.append(f"{len(tasks)} task(s) and {len(decisions)} decision(s) checked")
    report.checks.append("Evidence is checked for consistency only; commands were not rerun")
    return report
