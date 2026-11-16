"""Run one ledger task through an engine, waiting out usage limits.

The runner does not change the ledger: picking tasks, updating their status and
committing are the orchestrator's job. It refuses tasks that should not run and keeps
one session alive across a usage-limit pause by resuming it.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol

from agent_harness.check import inspect_ledger
from agent_harness.engine.events import Event, Finished, RateLimit, RunRequest, Started, Waiting
from agent_harness.engine.prompt import RESUME_PROMPT, task_prompt
from agent_harness.filesystem import HarnessError, project_root
from agent_harness.models import Task

RUNNABLE = ("READY", "IN_PROGRESS")
LIMIT_FALLBACK_WAIT = 300.0  # seconds, when the engine reports no reset time
LIMIT_MARGIN = 30.0  # seconds added after the reported reset time


class Engine(Protocol):
    def run(self, request: RunRequest, root: Path) -> AsyncIterator[Event]: ...


@dataclass
class RunOutcome:
    status: Literal["completed", "failed", "rate_limited"]
    task_id: str
    role: str
    session_id: str | None = None
    result: str = ""
    error: str = ""
    sessions: int = 0
    turns: int = 0
    cost_usd: float | None = None
    waited_seconds: float = 0.0
    events: list[Event] = field(default_factory=list, repr=False)


def prepare(
    path: Path,
    task_id: str,
    role: str | None = None,
    *,
    runnable: tuple[str, ...] = RUNNABLE,
    prompt_for: Callable[[Task, str], str] = task_prompt,
) -> tuple[Path, RunRequest]:
    """Validate that a task may run and build its first request; raise HarnessError if not."""
    root = project_root(path)
    report, tasks, _, config = inspect_ledger(root)
    if report.errors:
        raise HarnessError("the ledger has errors; run `harness check`: " + report.errors[0])
    task = tasks.get(task_id)
    if task is None:
        raise HarnessError(f"unknown task {task_id!r}")
    if task.status not in runnable:
        raise HarnessError(f"{task_id} is {task.status}; only {' or '.join(runnable)} tasks run")
    role = role or task.owner.strip()
    role_config = config.roles.get(role) if config else None
    if role_config is None:
        raise HarnessError(f"role {role!r} is not defined in .harness/config.yaml")
    request = RunRequest(
        cwd=root,
        role=role,
        prompt=prompt_for(task, role),
        model=role_config.model,
        effort=role_config.effort,
    )
    return root, request


def _limit_wait(limit: RateLimit | None, now: float) -> float:
    if limit is None or limit.resets_at is None:
        return LIMIT_FALLBACK_WAIT
    return max(0.0, limit.resets_at - now) + LIMIT_MARGIN


def _hit_limit(finished: Finished, limit: RateLimit | None) -> bool:
    return finished.http_status == 429 or (limit is not None and limit.status == "rejected")


def _record(outcome: "RunOutcome", event: Event, on_event: Callable[[Event], None] | None) -> None:
    outcome.events.append(event)
    if on_event is not None:
        on_event(event)


async def run_task(
    path: Path,
    task_id: str,
    engine: Engine,
    *,
    role: str | None = None,
    runnable: tuple[str, ...] = RUNNABLE,
    prompt_for: Callable[[Task, str], str] = task_prompt,
    max_resumes: int = 3,
    max_wait: float = 6 * 3600,
    on_event: Callable[[Event], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
) -> RunOutcome:
    root, request = prepare(path, task_id, role, runnable=runnable, prompt_for=prompt_for)
    outcome = RunOutcome(status="failed", task_id=task_id, role=request.role)
    while True:
        outcome.sessions += 1
        limit: RateLimit | None = None
        finished: Finished | None = None
        crashed: Exception | None = None
        stream = engine.run(request, root).__aiter__()
        try:
            while True:
                try:
                    event = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:  # engine and process errors are not HarnessError
                    crashed = exc
                    break
                _record(outcome, event, on_event)  # a failing listener is not an engine error
                if isinstance(event, Started):
                    outcome.session_id = event.session_id
                elif isinstance(event, RateLimit):
                    limit = event
                elif isinstance(event, Finished):
                    finished = event
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()
        if finished is None:
            if crashed is not None:
                outcome.error = f"{type(crashed).__name__}: {crashed}"
            else:
                outcome.error = "the engine stopped without a result"
            return outcome
        outcome.session_id = finished.session_id or outcome.session_id
        outcome.turns += finished.turns
        if finished.cost_usd is not None:
            outcome.cost_usd = (outcome.cost_usd or 0.0) + finished.cost_usd
        if finished.ok:
            outcome.status, outcome.result, outcome.error = "completed", finished.result, ""
            return outcome
        outcome.error = finished.error or "the agent reported an error"
        if not _hit_limit(finished, limit):
            return outcome
        wait = _limit_wait(limit, clock())
        if outcome.sessions > max_resumes or outcome.session_id is None or wait > max_wait:
            outcome.status = "rate_limited"
            return outcome
        _record(outcome, Waiting(wait), on_event)
        await sleep(wait)
        outcome.waited_seconds += wait
        request = replace(request, resume=outcome.session_id, prompt=RESUME_PROMPT)
