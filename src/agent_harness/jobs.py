"""Background jobs for the server: one wave, review or commit at a time, with live events.

A job calls the same functions as the CLI (`run_wave`, `review_all`, `commit_done_tasks`).
It adds no decision logic: statuses are still written by those functions, never here. Only
one job runs at a time because two waves would fight over the same ledger and working tree.
Jobs live in memory; the ledger stays the source of truth if the server restarts.
"""

import asyncio
import datetime as dt
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_harness.commit import commit_done_tasks
from agent_harness.engine.describe import describe_event
from agent_harness.filesystem import HarnessError
from agent_harness.orchestrator import run_wave
from agent_harness.review import review_all

MAX_EVENTS = 2000  # events kept per job; older ones are dropped
MAX_TEXT = 500  # characters kept per event
MAX_JOBS = 20  # finished jobs kept in memory
KEEPALIVE = 15.0  # seconds of silence before a stream sends a comment line
TASK_ID = re.compile(r"T-[0-9]+")
ROLE = re.compile(r"[A-Za-z0-9_-]{1,40}")
JOB_ID = re.compile(r"[0-9a-f]{32}")
APPLIES = {
    "wave": {"kind", "max_parallel", "max_resumes", "review", "reviewer"},
    "review": {"kind", "max_parallel", "max_resumes", "reviewer", "tasks"},
    "commit": {"kind", "tasks", "branch"},
}


class Busy(HarnessError):
    """Another job is still running."""


class NotRunning(HarnessError):
    """The job cannot be cancelled: it has finished or cannot be interrupted."""


class JobParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["wave", "review", "commit"]
    max_parallel: int = Field(3, ge=1, le=8)
    max_resumes: int = Field(3, ge=0, le=10)
    review: bool = False
    reviewer: str = "reviewer"
    tasks: list[str] | None = Field(None, max_length=50)
    branch: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("tasks")
    @classmethod
    def _task_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not all(TASK_ID.fullmatch(item) for item in value):
            raise ValueError("task ids look like T-3")
        return value

    @field_validator("reviewer")
    @classmethod
    def _role(cls, value: str) -> str:
        if not ROLE.fullmatch(value):
            raise ValueError("a role name has letters, digits, - and _ only")
        return value

    @model_validator(mode="after")
    def _only_what_applies(self) -> "JobParams":
        extra = self.model_fields_set - APPLIES[self.kind]
        if extra:
            raise ValueError(f"{', '.join(sorted(extra))} does not apply to a {self.kind} job")
        return self


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    created: str
    status: str = "running"  # running | done | failed | cancelled
    finished: str | None = None
    error: str | None = None
    result: dict | None = None
    events: list[dict] = field(default_factory=list)
    next_id: int = 1
    task: asyncio.Task | None = None
    waiters: list[asyncio.Future] = field(default_factory=list)

    @property
    def cancellable(self) -> bool:
        return self.kind != "commit"  # a commit runs in a thread that cannot be interrupted

    def view(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "params": self.params,
            "created": self.created,
            "finished": self.finished,
            "error": self.error,
            "result": self.result,
            "last_event": self.next_id - 1,
        }

    def emit(self, task: str | None, text: str) -> None:
        self.events.append({"id": self.next_id, "task": task, "text": text[:MAX_TEXT]})
        self.next_id += 1
        del self.events[:-MAX_EVENTS]
        self.wake()

    def wake(self) -> None:
        for waiter in self.waiters:
            if not waiter.done():
                waiter.set_result(None)
        self.waiters.clear()


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


class JobManager:
    def __init__(self, root: Path, engine_factory: Callable[[], object]):
        self.root = root
        self._engine_factory = engine_factory
        self.jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id) if JOB_ID.fullmatch(job_id) else None

    def active(self) -> Job | None:
        return next((job for job in self.jobs.values() if job.status == "running"), None)

    def start(self, params: JobParams) -> Job:
        """Start a job; must be called from the event loop."""
        active = self.active()
        if active is not None:
            raise Busy(f"job {active.id} is still running")
        job = Job(
            uuid.uuid4().hex,
            params.kind,
            params.model_dump(exclude_unset=True),
            _now(),
        )
        self.jobs[job.id] = job
        finished = [key for key, item in self.jobs.items() if item.status != "running"]
        for key in finished[: max(0, len(self.jobs) - MAX_JOBS)]:
            del self.jobs[key]
        job.task = asyncio.create_task(self._run(job, params))
        return job

    async def _run(self, job: Job, params: JobParams) -> None:
        job.emit(None, f"{job.kind} started")
        try:
            job.result = await self._execute(job, params)
            status, error = "done", None
        except asyncio.CancelledError:
            status, error = "cancelled", None
        except (HarnessError, OSError, UnicodeError) as exc:
            status, error = "failed", str(exc)[:MAX_TEXT]
        except Exception as exc:  # noqa: BLE001 - a job must never take the server down
            status, error = "failed", f"internal error: {type(exc).__name__}"
        # no await from here on, so a stream that sees the status also sees the last event
        job.status, job.error, job.finished = status, error, _now()
        job.emit(None, f"{job.kind} {status}" + (f": {error}" if error else ""))

    async def _execute(self, job: Job, p: JobParams) -> dict:
        if p.kind == "commit":
            commits = await asyncio.to_thread(
                commit_done_tasks, self.root, task_ids=p.tasks, branch=p.branch
            )
            rows = [
                {
                    "task": r.task_id,
                    "status": r.status,
                    "sha": r.sha,
                    "files": r.files,
                    "note": r.note,
                }
                for r in commits
            ]
            return {"ok": all(r.status != "blocked" for r in commits), "tasks": rows}

        engine = self._engine_factory()

        def listener(task_id: str, event) -> None:
            text = describe_event(event)
            if text:
                job.emit(task_id, text)

        options = {
            "max_parallel": p.max_parallel,
            "on_event": listener,
            "max_resumes": p.max_resumes,
        }
        if p.kind == "review":
            reviews = await review_all(
                self.root, engine, task_ids=p.tasks, role=p.reviewer, **options
            )
            ok = all(r.status == "DONE" and r.recorded for r in reviews)
            return {"ok": ok, "reviews": _review_rows(reviews)}

        results = await run_wave(self.root, engine, **options)
        rows = [
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
        payload: dict = {"ok": ok, "started": len(results), "tasks": rows}
        if p.review:
            reviews = await review_all(
                self.root,
                engine,
                task_ids=[r.task_id for r in results if r.status == "IN_REVIEW"],
                role=p.reviewer,
                **options,
            )
            payload["reviews"] = _review_rows(reviews)
            payload["ok"] = ok and all(r.status == "DONE" and r.recorded for r in reviews)
        return payload

    async def cancel(self, job: Job) -> None:
        if job.status != "running" or job.task is None:
            raise NotRunning("the job is not running")
        if not job.cancellable:
            raise NotRunning("a commit job cannot be interrupted")
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        self._close_unfinished(job)

    def _close_unfinished(self, job: Job) -> None:
        """A task cancelled before its first step never reaches `_run`'s own bookkeeping."""
        if job.status == "running":
            job.status, job.finished = "cancelled", _now()
            job.emit(None, f"{job.kind} cancelled")

    async def stream(self, job: Job, after: int = 0) -> AsyncIterator[dict | None]:
        """Events after `after`, then new ones as they come; None is a keepalive tick."""
        cursor = after
        while True:
            fresh = [event for event in job.events if event["id"] > cursor]
            if fresh:
                for event in fresh:
                    cursor = event["id"]
                    yield event
                continue  # the job moved on while we yielded: look again before waiting
            if job.status != "running":
                return
            waiter = asyncio.get_running_loop().create_future()
            job.waiters.append(waiter)  # no await since the check above, so no event is missed
            try:
                await asyncio.wait_for(waiter, KEEPALIVE)
            except TimeoutError:
                yield None
            finally:
                if waiter in job.waiters:
                    job.waiters.remove(waiter)

    async def shutdown(self) -> None:
        """Stop what is running: cancel a wave or review, let a short commit finish."""
        tasks = []
        for job in self.jobs.values():
            if job.task is not None and not job.task.done():
                if job.cancellable:
                    job.task.cancel()
                tasks.append(job.task)
        await asyncio.gather(*tasks, return_exceptions=True)
        for job in self.jobs.values():
            self._close_unfinished(job)
