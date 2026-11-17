import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
import yaml
from fastapi import testclient
from pydantic import ValidationError
from test_engine_runner import project, task
from test_orchestrator import ByTask, ok

from agent_harness import jobs as jobs_module
from agent_harness.commit import CommitResult
from agent_harness.engine.events import Started
from agent_harness.filesystem import HarnessError
from agent_harness.jobs import MAX_EVENTS, MAX_JOBS, MAX_TEXT, Job, JobManager, JobParams
from agent_harness.server import create_app

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WAVE = {"kind": "wave"}


class Blocking:
    """An engine whose session never ends, so a job stays running until it is cancelled."""

    async def run(self, request, root):
        yield Started("s-blocked")
        await asyncio.Event().wait()


def make_client(root, factory=None):
    app = create_app(root, TOKEN, engine_factory=factory or (lambda: ByTask({"T-1": ok("T-1")})))
    return testclient.TestClient(app, base_url="http://localhost")


def start(api, body, **kw):
    return api.post("/api/jobs", headers=AUTH, json=body, **kw)


def events_of(api, job_id, **params):
    """Read the whole event stream; it closes when the job has ended."""
    frames = []
    with api.stream("GET", f"/api/jobs/{job_id}/events", headers=AUTH, **params) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        announced = None
        for line in response.iter_lines():
            if line.startswith("id: "):
                announced = int(line[4:])
            elif line.startswith("data: "):
                frames.append(json.loads(line[6:]))
                # the protocol id (what a client sends back to resume) must be the event id
                assert announced == frames[-1]["id"], (announced, frames[-1]["id"])
                announced = None
    return frames


def wait_for_status(root, task_id, wanted, seconds=10):
    """Poll until the task has the status; proves the orchestrator really claimed it."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if status_of(root, task_id) == wanted:
            return
        time.sleep(0.02)
    assert status_of(root, task_id) == wanted, f"{task_id} never became {wanted}"


def view(api, job_id):
    return api.get(f"/api/jobs/{job_id}", headers=AUTH).json()


def status_of(root, task_id):
    return yaml.safe_load((root / f".harness/tasks/{task_id}.yaml").read_text())["status"]


def fake_review(calls, rows=None):
    async def fake(root, engine, **kwargs):
        calls.append(kwargs)
        return rows or []

    return fake


def review_row(task_id="T-1", status="DONE"):
    outcome = SimpleNamespace(cost_usd=0.1)
    return SimpleNamespace(
        task_id=task_id, verdict="APPROVED", status=status, outcome=outcome, note="", recorded=True
    )


def test_a_wave_job_runs_the_orchestrator_and_streams_ordered_events(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        response = start(api, WAVE)
        assert response.status_code == 202
        job_id = response.json()["id"]
        frames = events_of(api, job_id)
        assert [f["id"] for f in frames] == list(range(1, len(frames) + 1))
        assert frames[0]["text"] == "wave started" and frames[-1]["text"] == "wave done"
        assert any(f["task"] == "T-1" and "session s-T-1" in f["text"] for f in frames)
        job = view(api, job_id)
        assert job["status"] == "done" and job["error"] is None and job["finished"]
        assert job["result"]["ok"] is True
        assert [(t["task"], t["status"]) for t in job["result"]["tasks"]] == [("T-1", "IN_REVIEW")]
        # the same row keys `harness wave --json` prints
        assert set(job["result"]["tasks"][0]) == {
            "task",
            "run",
            "status",
            "cost_usd",
            "note",
            "recorded",
        }
        assert job["params"] == {"kind": "wave"} and job["last_event"] == len(frames)
    assert status_of(root, "T-1") == "IN_REVIEW"


def test_a_wave_job_with_review_reviews_only_tasks_that_reached_review(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(jobs_module, "review_all", fake_review(calls, [review_row()]))
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "wave", "review": True, "reviewer": "qa"}).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
    assert calls[0]["task_ids"] == ["T-1"] and calls[0]["role"] == "qa"
    assert job["result"]["ok"] is True and job["result"]["reviews"][0]["verdict"] == "APPROVED"
    assert set(job["result"]["reviews"][0]) == {
        "task",
        "verdict",
        "status",
        "cost_usd",
        "note",
        "recorded",
    }


def test_a_failed_review_makes_the_wave_result_not_ok(monkeypatch, tmp_path):
    rows = [review_row(status="READY")]
    monkeypatch.setattr(jobs_module, "review_all", fake_review([], rows))
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "wave", "review": True}).json()["id"]
        events_of(api, job_id)
        assert view(api, job_id)["result"]["ok"] is False


def test_a_review_job_passes_its_options_to_review_all(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(jobs_module, "review_all", fake_review(calls, [review_row()]))
    root = project(tmp_path, task("T-1", "IN_REVIEW"))
    body = {"kind": "review", "tasks": ["T-1"], "reviewer": "qa", "max_parallel": 2}
    with make_client(root) as api:
        job_id = start(api, body).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
    assert calls[0]["task_ids"] == ["T-1"] and calls[0]["role"] == "qa"
    assert calls[0]["max_parallel"] == 2 and calls[0]["max_resumes"] == 3
    assert job["status"] == "done" and job["result"]["ok"] is True
    # the same shape `harness review --json` prints
    assert job["result"]["reviewed"] == 1 and job["result"]["tasks"][0]["task"] == "T-1"
    assert set(job["result"]["tasks"][0]) == {
        "task",
        "verdict",
        "status",
        "cost_usd",
        "note",
        "recorded",
    }
    assert "reviews" not in job["result"]


def test_a_commit_job_passes_tasks_and_branch_and_reports_blocked(monkeypatch, tmp_path):
    seen = {}

    def fake_commit(root, *, task_ids, branch):
        seen.update(task_ids=task_ids, branch=branch)
        return [
            CommitResult("T-1", "committed", sha="abc", files=["a.py"]),
            CommitResult("T-2", "blocked", note="stray file"),
        ]

    monkeypatch.setattr(jobs_module, "commit_done_tasks", fake_commit)
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "commit", "tasks": ["T-1"], "branch": "group"}).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
    assert seen == {"task_ids": ["T-1"], "branch": "group"}
    assert job["status"] == "done" and job["result"]["ok"] is False
    assert job["result"]["tasks"][0] == {
        "task": "T-1",
        "status": "committed",
        "sha": "abc",
        "files": ["a.py"],
        "note": "",
    }


def test_a_commit_in_a_directory_that_is_not_a_repository_fails_cleanly(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "commit"}).json()["id"]
        frames = events_of(api, job_id)
        job = view(api, job_id)
    assert job["status"] == "failed" and job["error"] and job["result"] is None
    assert frames[-1]["text"].startswith("commit failed: ")


def test_an_engine_that_cannot_be_built_fails_the_job_with_its_message(tmp_path):
    def broken():
        raise HarnessError("the engine needs its extra")

    root = project(tmp_path, task("T-1"))
    with make_client(root, broken) as api:
        job_id = start(api, WAVE).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
    assert job["status"] == "failed" and job["error"] == "the engine needs its extra"
    assert status_of(root, "T-1") == "READY"


def test_an_unexpected_error_fails_the_job_without_leaking_its_text(tmp_path):
    def boom():
        raise RuntimeError("secret /home/user/path")

    root = project(tmp_path, task("T-1"))
    with make_client(root, boom) as api:
        job_id = start(api, WAVE).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
        assert api.get("/api/plan", headers=AUTH).status_code == 200  # the server survived
    assert job["status"] == "failed" and job["error"] == "internal error: RuntimeError"
    assert "secret" not in json.dumps(job)


def test_a_ledger_error_fails_the_wave_job(tmp_path):
    root = project(tmp_path, task("T-1", "DONE"))  # DONE without evidence is a ledger error
    with make_client(root) as api:
        job_id = start(api, WAVE).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
    assert job["status"] == "failed" and "harness check" in job["error"]


def test_a_second_job_is_refused_while_one_runs_and_cancel_releases_the_task(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root, Blocking) as api:
        first = start(api, WAVE).json()["id"]
        wait_for_status(root, "T-1", "IN_PROGRESS")  # the wave really holds the task
        second = start(api, WAVE)
        assert second.status_code == 409 and first in second.json()["detail"]
        assert view(api, first)["status"] == "running"
        cancelled = api.post(f"/api/jobs/{first}/cancel", headers=AUTH)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert events_of(api, first)[-1]["text"] == "wave cancelled"
        assert status_of(root, "T-1") == "READY"  # released, not stranded IN_PROGRESS
        assert api.post(f"/api/jobs/{first}/cancel", headers=AUTH).status_code == 409
        again = start(api, WAVE)
        assert again.status_code == 202  # the slot is free again
        api.post(f"/api/jobs/{again.json()['id']}/cancel", headers=AUTH)


def test_a_commit_job_cannot_be_cancelled_and_runs_to_the_end(monkeypatch, tmp_path):
    gate = threading.Event()

    def slow_commit(root, *, task_ids, branch):
        gate.wait(10)
        return [CommitResult("T-1", "committed", sha="abc")]

    monkeypatch.setattr(jobs_module, "commit_done_tasks", slow_commit)
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "commit"}).json()["id"]
        refused = api.post(f"/api/jobs/{job_id}/cancel", headers=AUTH)
        assert refused.status_code == 409 and "interrupted" in refused.json()["detail"]
        assert view(api, job_id)["status"] == "running"
        gate.set()
        events_of(api, job_id)
        assert view(api, job_id)["status"] == "done"


def test_shutdown_cancels_a_running_wave_and_releases_its_task(tmp_path):
    root = project(tmp_path, task("T-1"))
    api = make_client(root, Blocking)
    with api:
        job_id = start(api, WAVE).json()["id"]
        wait_for_status(root, "T-1", "IN_PROGRESS")
        assert view(api, job_id)["status"] == "running"
        manager = api.app.state.jobs
    assert manager.jobs[job_id].status == "cancelled"
    assert status_of(root, "T-1") == "READY"


def test_events_resume_after_an_id_or_last_event_id(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, WAVE).json()["id"]
        everything = events_of(api, job_id)
        assert len(everything) >= 3
        assert events_of(api, job_id, params={"after": 2}) == everything[2:]
        header = {**AUTH, "Last-Event-ID": "2"}
        with api.stream("GET", f"/api/jobs/{job_id}/events", headers=header) as response:
            lines = list(response.iter_lines())
        ids = [json.loads(line[6:])["id"] for line in lines if line.startswith("data: ")]
        sse_ids = [int(line[4:]) for line in lines if line.startswith("id: ")]
        assert ids == [e["id"] for e in everything[2:]] and sse_ids == ids
        assert events_of(api, job_id, params={"after": 10**6}) == []
        junk = {**AUTH, "Last-Event-ID": "x1"}
        with api.stream("GET", f"/api/jobs/{job_id}/events", headers=junk) as response:
            assert response.status_code == 200  # a bad header is ignored, not an error
            assert len([ln for ln in response.iter_lines() if ln.startswith("id:")]) == len(
                everything
            )
        negative = api.get(f"/api/jobs/{job_id}/events?after=-1", headers=AUTH)
        assert negative.status_code == 422


def test_unknown_or_malformed_job_ids_are_404(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        for job_id in ("0" * 32, "nope", "A" * 32, "../x"):
            for url in (f"/api/jobs/{job_id}", f"/api/jobs/{job_id}/events"):
                assert api.get(url, headers=AUTH).status_code == 404, url
            assert api.post(f"/api/jobs/{job_id}/cancel", headers=AUTH).status_code == 404


def test_jobs_are_listed_newest_first_with_their_status(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_module, "commit_done_tasks", lambda *a, **k: [])
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        ids = []
        for _ in range(3):
            ids.append(start(api, {"kind": "commit"}).json()["id"])
            events_of(api, ids[-1])
        listed = api.get("/api/jobs", headers=AUTH).json()
        assert [j["id"] for j in listed] == ids[::-1]
        assert {j["status"] for j in listed} == {"done"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"kind": "deploy"},
        {"kind": "wave", "unknown": 1},
        {"kind": "wave", "max_parallel": 0},
        {"kind": "wave", "max_parallel": 9},
        {"kind": "wave", "max_resumes": -1},
        {"kind": "wave", "max_parallel": "3"},
        {"kind": "wave", "review": "yes"},
        {"kind": "wave", "review": 1},
        {"kind": "wave", "max_parallel": 2.5},
        {"kind": "wave", "max_parallel": True},
        {"kind": "review", "tasks": "T-1"},
        {"kind": "wave", "branch": "x"},
        {"kind": "wave", "tasks": ["T-1"]},
        {"kind": "commit", "max_parallel": 2},
        {"kind": "commit", "review": True},
        {"kind": "commit", "reviewer": "qa"},
        {"kind": "review", "branch": "x"},
        {"kind": "review", "review": True},
        {"kind": "review", "tasks": ["T-1\n"]},
        {"kind": "review", "tasks": ["T-١"]},
        {"kind": "review", "tasks": ["../T-1"]},
        {"kind": "review", "tasks": ["T-1"] * 51},
        {"kind": "review", "reviewer": "qa; rm -rf"},
        {"kind": "review", "reviewer": ""},
        {"kind": "commit", "branch": ""},
        {"kind": "commit", "branch": "x" * 101},
    ],
)
def test_invalid_job_requests_are_422_and_start_nothing(tmp_path, body):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        response = start(api, body)
        assert response.status_code == 422, body
        assert api.get("/api/jobs", headers=AUTH).json() == []


def test_job_requests_need_json_the_token_and_a_loopback_host(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        assert api.post("/api/jobs", headers=AUTH, content=b'{"kind":"wave"}').status_code == 415
        form = api.post("/api/jobs", headers=AUTH, data={"kind": "wave"})
        assert form.status_code == 415
        text = api.post(
            "/api/jobs", headers={**AUTH, "Content-Type": "text/plain"}, content=b'{"kind":"wave"}'
        )
        assert text.status_code == 415
        assert api.post("/api/jobs", json=WAVE).status_code == 401
        assert (
            api.post("/api/jobs", json=WAVE, headers={"Authorization": "Bearer x"}).status_code
            == 401
        )
        foreign = {**AUTH, "Host": "evil.example"}
        assert api.post("/api/jobs", json=WAVE, headers=foreign).status_code == 403
        bad_json = api.post(
            "/api/jobs", headers={**AUTH, "Content-Type": "application/json"}, content=b"{nope"
        )
        assert bad_json.status_code == 422
        huge = {"kind": "review", "tasks": ["T-1"], "pad": "x" * 20_000}
        assert start(api, huge).status_code == 413
        assert api.get("/api/jobs", headers=AUTH).json() == []
        for url in ("/api/jobs", "/api/jobs/" + "0" * 32, "/api/jobs/" + "0" * 32 + "/events"):
            assert api.get(url).status_code == 401, url
        assert api.post("/api/jobs/" + "0" * 32 + "/cancel").status_code == 401


def test_params_accept_the_defaults_and_remember_only_what_was_set():
    wave = JobParams.model_validate({"kind": "wave"})
    assert (wave.max_parallel, wave.max_resumes, wave.review, wave.reviewer) == (
        3,
        3,
        False,
        "reviewer",
    )
    assert wave.model_dump(exclude_unset=True) == {"kind": "wave"}
    both = JobParams.model_validate({"kind": "review", "tasks": ["T-1", "T-22"]})
    assert both.tasks == ["T-1", "T-22"]
    with pytest.raises(ValidationError, match="does not apply to a commit job"):
        JobParams.model_validate({"kind": "commit", "max_parallel": 3})  # even the default value


def make_job(kind="wave"):
    return Job("0" * 32, kind, {"kind": kind}, "now")


def test_the_event_buffer_and_text_are_bounded_and_ids_keep_counting():
    job = make_job()
    for number in range(MAX_EVENTS + 500):
        job.emit(None, f"event {number}")
    assert len(job.events) == MAX_EVENTS
    assert job.events[-1]["id"] == MAX_EVENTS + 500 and job.events[0]["id"] == 501
    job.emit("T-1", "x" * (MAX_TEXT * 4))
    assert len(job.events[-1]["text"]) == MAX_TEXT


def test_a_stream_resumes_from_the_oldest_kept_event_when_after_is_older():
    async def run():
        manager = JobManager(None, lambda: None)
        job = make_job()
        job.status = "done"
        for number in range(MAX_EVENTS + 5):
            job.emit(None, str(number))
        return [e["id"] async for e in manager.stream(job, after=1)]

    ids = asyncio.run(run())
    assert ids[0] == 6 and ids[-1] == MAX_EVENTS + 5 and len(ids) == MAX_EVENTS


def test_a_running_job_sends_keepalive_ticks_and_then_new_events(monkeypatch):
    monkeypatch.setattr(jobs_module, "KEEPALIVE", 0.01)

    async def run():
        manager = JobManager(None, lambda: None)
        job = make_job()
        seen = []

        async def reader():
            async for item in manager.stream(job):
                seen.append(item)
                if item is not None and item["text"] == "last":
                    return

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.1)
        job.emit(None, "last")
        await asyncio.wait_for(task, 2)
        assert job.waiters == []  # nothing is left registered
        return seen

    seen = asyncio.run(run())
    assert None in seen and seen[-1]["text"] == "last"


def test_a_disconnecting_reader_leaves_no_waiter_behind(monkeypatch):
    async def run():
        manager = JobManager(None, lambda: None)
        job = make_job()
        task = asyncio.create_task(anext(manager.stream(job)))
        await asyncio.sleep(0.05)
        assert len(job.waiters) == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return job.waiters

    assert asyncio.run(run()) == []


def test_only_a_few_finished_jobs_are_kept_and_a_running_one_never_dropped(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_module, "commit_done_tasks", lambda *a, **k: [])

    async def run():
        manager = JobManager(project(tmp_path, task("T-1")), lambda: None)
        ids = []
        for _ in range(MAX_JOBS + 7):
            job = manager.start(JobParams.model_validate({"kind": "commit"}))
            ids.append(job.id)
            await job.task
        manager._engine_factory = Blocking
        running = manager.start(JobParams.model_validate({"kind": "wave"}))
        await asyncio.sleep(0)
        kept = list(manager.jobs)
        await manager.shutdown()
        return ids, running.id, kept

    ids, running_id, kept = asyncio.run(run())
    assert len(kept) <= MAX_JOBS and kept[-1] == running_id
    assert ids[-1] in kept and ids[0] not in kept  # the oldest finished one went first


def test_cancelling_before_the_first_step_still_ends_the_job(tmp_path):
    async def run():
        manager = JobManager(project(tmp_path, task("T-1")), lambda: None)
        job = manager.start(JobParams.model_validate({"kind": "wave"}))
        await manager.cancel(job)  # the task has not run a single step yet
        return job

    job = asyncio.run(run())
    assert job.status == "cancelled" and job.finished and job.events[-1]["text"] == "wave cancelled"


def test_events_emitted_while_the_reader_is_busy_are_not_missed(monkeypatch):
    """The reader yields an event; meanwhile the job emits more and finishes. A reader that
    waits on a stale view would sit until the keepalive instead of ending at once."""
    monkeypatch.setattr(jobs_module, "KEEPALIVE", 60)

    async def run():
        manager = JobManager(None, lambda: None)
        job = make_job()
        job.emit(None, "first")
        seen = []
        async for event in manager.stream(job):
            seen.append(event["text"])
            if event["text"] == "first":
                job.emit(None, "second")  # the job moves on while the reader holds "first"
                job.status = "done"
                job.emit(None, "wave done")
        return seen

    seen = asyncio.run(asyncio.wait_for(run(), 3))
    assert seen == ["first", "second", "wave done"]


def test_an_error_thrown_into_the_lifespan_still_stops_the_running_job(tmp_path):
    """Starlette resumes the lifespan normally on shutdown, so the error has to be thrown into
    it by hand: that is what a cancelled or crashed server does."""
    root = project(tmp_path, task("T-1"))
    app = create_app(root, TOKEN, engine_factory=Blocking)

    async def run():
        with pytest.raises(RuntimeError, match="boom"):
            async with app.router.lifespan_context(app):
                app.state.jobs.start(JobParams.model_validate({"kind": "wave"}))
                await asyncio.sleep(0.2)
                raise RuntimeError("boom")
        job = next(iter(app.state.jobs.jobs.values()))
        # checked before asyncio.run tears the loop down and cancels leftovers by itself
        return job.status, status_of(root, "T-1")

    assert asyncio.run(run()) == ("cancelled", "READY")


def test_a_cancelled_job_task_ends_cancelled_after_its_bookkeeping(tmp_path):
    async def run():
        manager = JobManager(project(tmp_path, task("T-1")), lambda: Blocking())
        job = manager.start(JobParams.model_validate({"kind": "wave"}))
        await asyncio.sleep(0.2)
        assert job.status == "running"
        job.task.cancel()
        results = await asyncio.gather(job.task, return_exceptions=True)
        return job, results[0]

    job, outcome = asyncio.run(run())
    assert isinstance(outcome, asyncio.CancelledError)  # re-raised, not swallowed
    assert job.status == "cancelled" and job.events[-1]["text"] == "wave cancelled"


def run_real_server(app, port):
    import uvicorn

    from agent_harness.server import uvicorn_options

    server = uvicorn.Server(uvicorn.Config(app, **uvicorn_options("127.0.0.1", port)))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    return server, thread


def test_stopping_the_real_server_with_an_open_event_stream_does_not_hang(tmp_path):
    """uvicorn waits for open connections before it runs the lifespan shutdown; an event
    stream of a running job never ends by itself, so without a grace period it would wait
    forever and the job would never be cancelled."""
    import socket

    import httpx

    root = project(tmp_path, task("T-1"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(root, TOKEN, engine_factory=Blocking)
    server, thread = run_real_server(app, port)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=AUTH, timeout=30)
    job_id = client.post("/api/jobs", json=WAVE).json()["id"]
    got_first_event = threading.Event()

    def read_stream():
        try:
            with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        got_first_event.set()
        except httpx.HTTPError:
            pass  # the server closed the connection on shutdown

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    assert got_first_event.wait(10)
    wait_for_status(root, "T-1", "IN_PROGRESS")  # so READY below can only come from the cancel
    server.should_exit = True
    thread.join(20)
    assert not thread.is_alive(), "the server hung on shutdown with an open stream"
    assert status_of(root, "T-1") == "READY"  # the wave was cancelled and released its task


@pytest.mark.parametrize(
    "error",
    [OSError("disk full"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")],
)
def test_an_os_or_unicode_error_inside_a_job_fails_it_with_the_message(
    monkeypatch, tmp_path, error
):
    def broken_commit(root, *, task_ids, branch):
        raise error

    monkeypatch.setattr(jobs_module, "commit_done_tasks", broken_commit)
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "commit"}).json()["id"]
        events_of(api, job_id)
        job = view(api, job_id)
    assert job["status"] == "failed" and "internal error" not in job["error"]
    assert str(error)[:20] in job["error"]


def test_shutdown_lets_a_running_commit_finish_instead_of_cancelling_it(monkeypatch, tmp_path):
    gate = threading.Event()
    done = []

    def slow_commit(root, *, task_ids, branch):
        gate.wait(10)
        done.append(True)
        return [CommitResult("T-1", "committed", sha="abc")]

    monkeypatch.setattr(jobs_module, "commit_done_tasks", slow_commit)

    async def run():
        manager = JobManager(project(tmp_path, task("T-1")), lambda: None)
        job = manager.start(JobParams.model_validate({"kind": "commit"}))
        await asyncio.sleep(0.1)
        asyncio.get_running_loop().call_later(0.3, gate.set)
        await manager.shutdown()
        return job

    job = asyncio.run(asyncio.wait_for(run(), 10))
    assert job.status == "done" and done == [True]


class Halt(BaseException):
    """Not an Exception; asyncio itself would abort the loop on KeyboardInterrupt or SystemExit."""


def test_a_base_exception_does_not_leave_a_job_running(monkeypatch, tmp_path):
    def interrupted(root, *, task_ids, branch):
        raise Halt

    monkeypatch.setattr(jobs_module, "commit_done_tasks", interrupted)

    async def run():
        manager = JobManager(project(tmp_path, task("T-1")), lambda: None)
        job = manager.start(JobParams.model_validate({"kind": "commit"}))
        await asyncio.gather(job.task, return_exceptions=True)
        return job, manager.active()

    job, active = asyncio.run(run())
    assert job.status == "failed" and job.error == "interrupted" and active is None


def test_the_job_table_is_only_read_on_the_event_loop(tmp_path):
    import inspect

    root = project(tmp_path, task("T-1"))
    app = create_app(root, TOKEN)
    routes = {
        (r.path, tuple(sorted(r.methods))): r.endpoint for r in app.routes if hasattr(r, "methods")
    }
    for key in (("/api/jobs", ("GET",)), ("/api/jobs/{job_id}", ("GET",))):
        assert inspect.iscoroutinefunction(routes[key]), key  # a threadpool read could race


def test_the_content_type_must_be_exactly_json_and_a_declared_size_is_checked(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        for bad in ("application/jsonx", "application/json5", "text/application/json"):
            response = api.post(
                "/api/jobs", headers={**AUTH, "Content-Type": bad}, content=b'{"kind":"wave"}'
            )
            assert response.status_code == 415, bad
        charset = {**AUTH, "Content-Type": "Application/JSON; charset=utf-8"}
        assert (
            api.post("/api/jobs", headers=charset, content=b'{"kind":"commit"}').status_code == 202
        )
        big = {**AUTH, "Content-Type": "application/json", "Content-Length": "999999"}
        assert api.post("/api/jobs", headers=big, content=b"{}").status_code in (413, 400)


def test_a_huge_last_event_id_is_ignored_not_a_500(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, WAVE).json()["id"]
        everything = events_of(api, job_id)
        for value in ("9" * 5000, "9" * 19, "0" * 5000):
            header = {**AUTH, "Last-Event-ID": value}
            with api.stream("GET", f"/api/jobs/{job_id}/events", headers=header) as response:
                assert response.status_code == 200, len(value)
                got = [ln for ln in response.iter_lines() if ln.startswith("id: ")]
            assert len(got) == len(everything), len(value)  # the unusable header was ignored
        header = {**AUTH, "Last-Event-ID": "9" * 18}
        with api.stream("GET", f"/api/jobs/{job_id}/events", headers=header) as response:
            assert [ln for ln in response.iter_lines() if ln.startswith("id: ")] == []


def asgi_post(app, *, chunks=None, extra_headers=(), max_reads=500):
    """POST /api/jobs at the ASGI level; returns (status, bytes handed to the app, reads)."""
    state = {"reads": 0, "sent": 0}
    queue = list(chunks) if chunks is not None else None

    async def receive():
        state["reads"] += 1
        if state["reads"] > max_reads:  # a runaway reader would get here
            return {"type": "http.disconnect"}
        if queue is None:  # an endless body
            body, more = b"x" * 1000, True
        else:
            body, more = (queue.pop(0), bool(queue)) if queue else (b"", False)
        state["sent"] += len(body)
        return {"type": "http.request", "body": body, "more_body": more}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/jobs",
        "raw_path": b"/api/jobs",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("localhost", 80),
        "client": ("127.0.0.1", 1),
        "headers": [
            (b"host", b"localhost"),
            (b"authorization", f"Bearer {TOKEN}".encode()),
            (b"content-type", b"application/json"),
            *extra_headers,
        ],
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, state["sent"], state["reads"]


def test_the_body_cap_is_enforced_while_reading_not_after_the_upload(tmp_path):
    """An endless body must get a 413 after about the cap, not be buffered."""
    from agent_harness.server import MAX_BODY

    app = create_app(project(tmp_path, task("T-1")), TOKEN)
    status, handed, _ = asgi_post(app)
    assert status == 413
    assert handed <= MAX_BODY + 2000, handed  # read to the cap, then stopped


def test_body_size_boundaries(monkeypatch, tmp_path):
    from agent_harness.server import MAX_BODY

    monkeypatch.setattr(jobs_module, "commit_done_tasks", lambda *a, **k: [])
    root = project(tmp_path, task("T-1"))
    base = b'{"kind":"commit"}'
    exact = base + b" " * (MAX_BODY - len(base))
    assert len(exact) == MAX_BODY

    def post(chunks):  # a fresh app each time: an accepted body starts a job
        return asgi_post(create_app(root, TOKEN), chunks=chunks)[0]

    assert post([b""]) == 422  # empty: a clear error, not a 500
    assert post([exact]) == 202  # exactly the cap passes
    assert post([exact[:4000], exact[4000:]]) == 202  # also when it arrives in pieces
    assert post([exact + b" "]) == 413  # one byte over


def test_a_content_length_with_thousands_of_digits_is_413_not_a_crash(tmp_path):
    app = create_app(project(tmp_path, task("T-1")), TOKEN)
    huge = (b"content-length", b"9" * 5000)
    status, handed, _ = asgi_post(app, chunks=[b"{}"], extra_headers=(huge,))
    assert status == 413 and handed == 0  # refused before any body byte was read


def test_a_job_whose_task_cannot_be_created_leaves_no_running_job_and_no_lock(
    monkeypatch, tmp_path
):
    from agent_harness.runlock import acquire

    root = project(tmp_path, task("T-1"))

    async def scenario():
        manager = JobManager(root, lambda: None)

        def broken(coroutine, **kwargs):
            coroutine.close()
            raise RuntimeError("no task for you")

        monkeypatch.setattr(jobs_module.asyncio, "create_task", broken)
        with pytest.raises(RuntimeError):
            manager.start(JobParams(kind="commit"))
        monkeypatch.undo()
        assert manager.active() is None and manager.jobs == {}  # a later start is not refused
        acquire(root, "probe").release()  # and the project lock was given back

    asyncio.run(scenario())


def test_the_event_stream_carries_the_security_headers_too(tmp_path):
    from agent_harness.server import SECURITY_HEADERS

    root = project(tmp_path, task("T-1"))
    with make_client(root) as api:
        job_id = start(api, {"kind": "commit"}).json()["id"]
        with api.stream("GET", f"/api/jobs/{job_id}/events", headers=AUTH) as response:
            assert response.status_code == 200
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            for name, value in SECURITY_HEADERS.items():
                assert response.headers[name.lower()] == value, name
            assert response.headers["cache-control"] == "no-store"
            list(response.iter_lines())  # read to the end: the job finishes and the stream closes
