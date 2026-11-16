"""Independent review of IN_REVIEW tasks by a reviewer role.

The reviewer reads and judges; it never records its own verdict. It ends its answer with a
verdict line and names the evidence for each acceptance criterion. This module parses that
answer and writes the review, `verified_by` and the new status, so the agent cannot mark a
task DONE by editing the ledger. An approval is accepted only when every criterion is
backed by recorded, passing evidence.
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_harness.check import TASKS_DIR
from agent_harness.engine.events import Event
from agent_harness.engine.prompt import review_prompt
from agent_harness.engine.runner import Engine, RunOutcome, run_task
from agent_harness.evidence import _load_task
from agent_harness.filesystem import HarnessError, managed_path, project_root
from agent_harness.models import Task
from agent_harness.orchestrator import MAX_REASON, _update, set_status
from agent_harness.schedule import plan

VERDICT = re.compile(
    r"^[ \t]*VERDICT:[ \t]*(APPROVED|CHANGES_REQUESTED)[ \t]*$", re.IGNORECASE | re.MULTILINE
)
NOTES_MAX = 1500


@dataclass
class ReviewResult:
    task_id: str
    outcome: RunOutcome
    status: str  # the ledger status the task was left in
    verdict: str | None = None
    note: str = ""
    recorded: bool = True


def parse_verdict(result: str, task: Task) -> tuple[str | None, dict[str, str]]:
    """The verdict and the evidence id named for each acceptance criterion.

    Ambiguity fails closed: two different verdict lines mean no verdict, and two different
    evidence ids for one criterion mean that criterion is not backed.
    """
    verdicts = {value.upper() for value in VERDICT.findall(result)}
    if len(verdicts) != 1:
        return None, {}
    mapping: dict[str, str] = {}
    for criterion in task.acceptance:
        named = re.findall(rf"^[ \t]*{re.escape(criterion.id)}:[ \t]*(\S+)[ \t]*$", result, re.M)
        if len(set(named)) == 1:
            mapping[criterion.id] = named[0]
    return verdicts.pop(), mapping


def _frozen(task: Task) -> str:
    """What a reviewer must not change: the whole task except its log."""
    data = task.model_dump(mode="json")
    data.pop("log", None)
    return json.dumps(data, sort_keys=True)


def _number(evidence_id: str) -> int:
    match = re.fullmatch(r"E(\d+)", evidence_id)
    return int(match.group(1)) if match else 0


def _unbacked(task: Task, mapping: dict[str, str]) -> list[str]:
    """Why an approval cannot stand; empty when every criterion has sound evidence."""
    evidence = {item.id: item for item in task.evidence}
    problems = []
    latest: dict[str, str] = {}
    for item in task.evidence:
        key = item.command or item.id
        if key not in latest or _number(item.id) > _number(latest[key]):
            latest[key] = item.id
    for key, evidence_id in latest.items():
        if not evidence[evidence_id].passed:
            problems.append(f"the latest run of {key!r} ({evidence_id}) failed")
    for criterion in task.acceptance:
        cited = evidence.get(mapping.get(criterion.id, ""))
        if cited is None:
            problems.append(f"{criterion.id} names no recorded evidence")
        elif not cited.passed:
            problems.append(f"{criterion.id} cites failing evidence {cited.id}")
        elif cited.recorded_by != "harness":
            problems.append(f"{criterion.id} cites {cited.id}, not recorded by harness")
    if not task.acceptance:
        problems.append("the task has no acceptance criteria")
    return problems


def _settle(root: Path, task_id: str, role: str, outcome: RunOutcome, before: str) -> ReviewResult:
    task = _load_task(managed_path(root, f"{TASKS_DIR}/{task_id}.yaml"))

    def keep(note: str) -> ReviewResult:
        _update(root, task_id, lambda data: None, note=note, expect=("IN_REVIEW",))
        return ReviewResult(task_id, outcome, "IN_REVIEW", None, note)

    if _frozen(task) != before:  # runs are read-only, so this is defence in depth
        reason = "the task changed during its review; reviews may not edit the ledger"
        set_status(root, task_id, "BLOCKED", reason=reason, note=reason)
        return ReviewResult(task_id, outcome, "BLOCKED", None, reason)
    if outcome.status != "completed":
        return keep(f"review run {outcome.status}: {outcome.error[:300]}")
    verdict, mapping = parse_verdict(outcome.result, task)
    if verdict is None:
        return keep("review ended without one clear verdict line")

    notes = outcome.result.strip()[-NOTES_MAX:]
    if verdict == "APPROVED":
        problems = _unbacked(task, mapping)
        if problems:
            verdict = "CHANGES_REQUESTED"
            notes = f"Approval was not accepted: {'; '.join(problems)}.\n{notes}"[-NOTES_MAX:]

    approved = verdict == "APPROVED"
    status = "DONE" if approved else "READY"

    def mutate(data: dict) -> None:
        data["review"] = {
            "status": "approved" if approved else "changes_requested",
            "by": role,
            "notes": notes,
        }
        if approved:
            for criterion in data["acceptance"]:
                kept = [*criterion.get("verified_by", []), mapping[criterion["id"]]]
                criterion["verified_by"] = list(dict.fromkeys(kept))
        data["status"] = status
        data.pop("reason", None)

    _update(
        root,
        task_id,
        mutate,
        note=f"review by {role}: {verdict.lower().replace('_', ' ')}",
        expect=("IN_REVIEW",),
    )
    return ReviewResult(task_id, outcome, status, verdict, notes[:MAX_REASON])


async def review_task(
    path: Path,
    task_id: str,
    engine: Engine,
    *,
    role: str = "reviewer",
    on_event: Callable[[Event], None] | None = None,
    **run_options,
) -> ReviewResult:
    """Run the reviewer on one IN_REVIEW task and record its verdict."""
    root = project_root(path)
    task = _load_task(managed_path(root, f"{TASKS_DIR}/{task_id}.yaml"))
    if task.status != "IN_REVIEW":
        raise HarnessError(f"{task_id} is {task.status}; only IN_REVIEW tasks are reviewed")
    if role.strip().casefold() == task.owner.strip().casefold():
        raise HarnessError(f"{task_id} cannot be reviewed by its owner {task.owner!r}")
    before = _frozen(task)
    try:
        outcome = await run_task(
            root,
            task_id,
            engine,
            role=role,
            runnable=("IN_REVIEW",),
            prompt_for=review_prompt,
            read_only=True,
            on_event=on_event,
            **run_options,
        )
    except Exception as exc:  # a broken review run must not strand or change the task
        outcome = RunOutcome(status="failed", task_id=task_id, role=role, error=str(exc))
    return await asyncio.to_thread(_settle, root, task_id, role, outcome, before)


def _status(root: Path, task_id: str) -> str:
    try:
        return _load_task(managed_path(root, f"{TASKS_DIR}/{task_id}.yaml")).status
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


async def review_all(
    path: Path,
    engine: Engine,
    *,
    task_ids: list[str] | None = None,
    role: str = "reviewer",
    max_parallel: int = 3,
    on_event: Callable[[str, Event], None] | None = None,
    **run_options,
) -> list[ReviewResult]:
    """Review every IN_REVIEW task (or just `task_ids`), a few at a time."""
    if max_parallel < 1:
        raise HarnessError("max_parallel must be at least 1")
    root = project_root(path)
    report = plan(root)
    if report.errors:
        raise HarnessError("the ledger has errors; run `harness check`: " + report.errors[0])
    waiting = [task.id for task in report.running if task.status == "IN_REVIEW"]
    if task_ids is not None:
        missing = [task_id for task_id in task_ids if task_id not in waiting]
        if missing:
            raise HarnessError(f"not IN_REVIEW, nothing to review: {', '.join(missing)}")
    chosen = [task_id for task_id in waiting if task_ids is None or task_id in task_ids]
    gate = asyncio.Semaphore(max_parallel)

    async def one(task_id: str) -> ReviewResult:
        def listener(event: Event) -> None:
            if on_event is not None:
                on_event(task_id, event)

        async with gate:
            try:
                return await review_task(
                    root, task_id, engine, role=role, on_event=listener, **run_options
                )
            except Exception as exc:  # e.g. the owner is the reviewer; the others still run
                failed = RunOutcome(status="failed", task_id=task_id, role=role, error=str(exc))
                return ReviewResult(task_id, failed, _status(root, task_id), None, str(exc), False)

    return list(await asyncio.gather(*(one(task_id) for task_id in chosen)))
