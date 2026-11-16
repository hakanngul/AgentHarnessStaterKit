"""Work out which tasks can start next. Reads the ledger; runs and writes nothing.

`start` holds READY tasks that may begin now. Two tasks may run together only when
their declared scopes do not overlap; a task without a scope is assumed to touch
anything. Scope overlap is judged on path prefixes and errs on the side of holding a
task back: `src/api/*.py` and `src/api/v1/` are treated as overlapping. Entries are compared the
way `roles.py` compares paths (Unicode NFC, case folded); absolute, home, drive, URL and
variable forms and `..` overlap everything.
"""

from pathlib import Path

from agent_harness.check import inspect_ledger, reachable_phases
from agent_harness.models import (
    Decision,
    HarnessConfig,
    HeldTask,
    PendingDecision,
    PlanReport,
    PlanTask,
    Task,
    WaitingTask,
)
from agent_harness.roles import _fold

RUNNING = ("IN_PROGRESS", "IN_REVIEW")
# Whitespace plus the zero-width characters that copy and paste tends to leave behind.
BLANK = " \t\r\n\f\v\u00a0\ufeff\u200b\u200c\u200d\u2060"
FINAL = ("DONE", "CANCELLED")


def _order(item_id: str) -> tuple[str, int]:
    prefix, _, number = item_id.rpartition("-")
    return prefix, int(number)


def _prefix(entry: str) -> tuple[str, ...]:
    """Literal leading path segments of a scope entry; empty means the whole project."""
    text = _fold(entry.strip(BLANK)).replace("\\", "/")
    head = text.split("/", 1)[0]
    if text.startswith(("/", "~")) or any(mark in head for mark in ":$%"):
        # Absolute, home, drive, URL or variable form: assume it may touch anything.
        return ()
    parts: list[str] = []
    for raw in text.split("/"):
        segment = raw.strip(BLANK)
        if segment in ("", "."):
            continue
        if segment == ".." or "*" in segment or "?" in segment:
            return () if segment == ".." else tuple(parts)
        parts.append(segment)
    return tuple(parts)


def scopes_overlap(first: list[str], second: list[str]) -> bool:
    """True when two scopes may touch the same path, or either scope is undeclared."""
    if not first or not second:
        return True
    for left in map(_prefix, first):
        for right in map(_prefix, second):
            shorter = min(len(left), len(right))
            if left[:shorter] == right[:shorter]:
                return True
    return False


def _view(task: Task) -> dict:
    return task.model_dump(include={"id", "title", "owner", "status", "phase", "scope"})


def _unmet(
    task: Task,
    tasks: dict[str, Task],
    decisions: dict[str, Decision],
    config: HarnessConfig | None,
    reach: dict[str, frozenset[str]],
) -> list[str]:
    reasons: list[str] = []
    for ref in task.depends_on:
        target = decisions.get(ref) if ref.startswith("DEC-") else tasks.get(ref)
        wanted = "DECIDED" if ref.startswith("DEC-") else "DONE"
        if target is not None and target.status != wanted:
            reasons.append(f"{ref} is {target.status}")
    workflow = config.workflow if config else None
    if workflow and task.phase:
        for phase in workflow.requires(task.phase) or []:
            if phase not in reach.get(task.id, frozenset()):
                reasons.append(f"no {phase!r} task is reachable through depends_on")
    return reasons


def plan(path: Path) -> PlanReport:
    ledger, tasks, decisions, config = inspect_ledger(path)
    report = PlanReport(errors=list(ledger.errors), warnings=list(ledger.warnings))
    if report.errors:
        report.warnings.append("No plan was computed: fix the ledger errors first")
        return report

    workflow = config.workflow if config else None
    defined = {phase.name for phase in workflow.phases} if workflow else set()
    reach = reachable_phases(tasks, defined) if workflow else {}
    ordered = [tasks[task_id] for task_id in sorted(tasks, key=_order)]

    occupied = [task for task in ordered if task.status in RUNNING]
    report.running = [PlanTask(**_view(task)) for task in occupied]

    for task in ordered:
        if task.status in RUNNING or task.status in FINAL:
            continue
        reasons = _unmet(task, tasks, decisions, config, reach)
        if task.status == "BLOCKED":
            report.waiting.append(
                WaitingTask(**_view(task), reasons=[f"BLOCKED: {task.reason.strip()}", *reasons])
            )
        elif task.status == "BACKLOG":
            if reasons:
                report.waiting.append(WaitingTask(**_view(task), reasons=reasons))
            else:
                report.unblocked.append(PlanTask(**_view(task)))
        else:  # READY: `check` already guarantees its dependencies and phase gate
            conflicts = [other.id for other in occupied if scopes_overlap(task.scope, other.scope)]
            if conflicts:
                if not task.scope:
                    reason = "no scope declared, so it cannot run beside another task"
                elif all(not tasks[other].scope for other in conflicts):
                    reason = "a task without a declared scope is running or starting"
                else:
                    reason = "scope overlaps a task that is running or starting"
                report.held.append(HeldTask(**_view(task), conflicts_with=conflicts, reason=reason))
            else:
                report.start.append(PlanTask(**_view(task)))
                occupied.append(task)

    open_tasks = [task for task in ordered if task.status not in FINAL]
    for decision_id in sorted(decisions, key=_order):
        decision = decisions[decision_id]
        if decision.status == "PENDING":
            blocks = [task.id for task in open_tasks if decision_id in task.depends_on]
            report.decisions.append(
                PendingDecision(id=decision_id, question=decision.question, blocks=blocks)
            )
    report.finished = bool(tasks) and not open_tasks
    return report
