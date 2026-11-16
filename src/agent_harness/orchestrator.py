"""One orchestration wave: start every task the planner allows, record how each ended.

The planner (`schedule.plan`) decides what may start; `run_task` runs one task. This
module connects them and is the only place that writes task status. Reviews, commits and
the phase gate stay outside it: a finished task goes to IN_REVIEW, never to DONE.
"""

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_harness.check import TASKS_DIR
from agent_harness.engine.events import Event
from agent_harness.engine.runner import Engine, RunOutcome, run_task
from agent_harness.evidence import MAX_FILE_BYTES, _load_task, _task_lock, _write_atomic
from agent_harness.filesystem import HarnessError, managed_path, project_root
from agent_harness.models import Task
from agent_harness.schedule import plan

MAX_REASON = 500


@dataclass
class WaveResult:
    task_id: str
    outcome: RunOutcome
    status: str  # the ledger status the task was left in
    note: str = ""


def set_status(root: Path, task_id: str, status: str, *, reason: str = "", note: str) -> None:
    """Move a task to `status` and append a log line, under the task's lock."""
    task_path = managed_path(root, f"{TASKS_DIR}/{task_id}.yaml")
    with _task_lock(task_path):
        data = _load_task(task_path).model_dump(mode="json", exclude_unset=True)
        data["status"] = status
        if status == "BLOCKED":
            data["reason"] = reason
        else:
            data.pop("reason", None)
        data["log"] = [*data.get("log", []), f"{dt.date.today()} orchestrator: {note}"[:2000]]
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
        if len(text.encode()) > MAX_FILE_BYTES:
            raise HarnessError(f"{task_id}.yaml would exceed {MAX_FILE_BYTES} bytes")
        try:
            Task.model_validate(yaml.safe_load(text))  # never write an invalid task
        except ValidationError as exc:
            raise HarnessError(f"refusing to write an invalid {task_id}: {exc}") from exc
        _write_atomic(task_path, text)


def _settle(root: Path, task_id: str, outcome: RunOutcome) -> tuple[str, str]:
    """The status and note a finished run earns."""
    if outcome.status == "completed":
        task = _load_task(managed_path(root, f"{TASKS_DIR}/{task_id}.yaml"))
        if not task.evidence or not task.acceptance:
            return "BLOCKED", "agent finished but the task has no recorded evidence or acceptance"
        return "IN_REVIEW", "agent finished; waiting for independent review"
    detail = (outcome.error or outcome.status)[:MAX_REASON]
    if outcome.status == "rate_limited":
        return "READY", f"usage limit reached, will be retried: {detail}"
    return "BLOCKED", f"agent run failed: {detail}"


async def run_wave(
    path: Path,
    engine: Engine,
    *,
    max_parallel: int = 3,
    on_event: Callable[[str, Event], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    **run_options,
) -> list[WaveResult]:
    """Run the tasks `harness next` would start, up to `max_parallel`, and settle each."""
    if max_parallel < 1:
        raise HarnessError("max_parallel must be at least 1")
    root = project_root(path)
    report = plan(root)
    if report.errors:
        raise HarnessError("the ledger has errors; run `harness check`: " + report.errors[0])
    chosen = [task.id for task in report.start[:max_parallel]]
    for task_id in chosen:
        set_status(root, task_id, "IN_PROGRESS", note="started")

    async def one(task_id: str) -> WaveResult:
        def listener(event: Event) -> None:
            if on_event is not None:
                on_event(task_id, event)

        try:
            extra = {"sleep": sleep} if sleep is not None else {}
            outcome = await run_task(
                root, task_id, engine, on_event=listener, **extra, **run_options
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one task must not stop its siblings
            outcome = RunOutcome(status="failed", task_id=task_id, role="", error=str(exc))
        status, note = _settle(root, task_id, outcome)
        set_status(root, task_id, status, reason=note, note=note)
        return WaveResult(task_id, outcome, status, note)

    return list(await asyncio.gather(*(one(task_id) for task_id in chosen)))
