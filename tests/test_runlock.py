import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest
import yaml
from fastapi import testclient
from test_engine_runner import project, task
from test_orchestrator import ByTask, ok
from typer.testing import CliRunner

from agent_harness import cli
from agent_harness import jobs as jobs_module
from agent_harness.commit import _is_generated, changed_files
from agent_harness.filesystem import HarnessError
from agent_harness.jobs import JobManager, JobParams
from agent_harness.runlock import LOCK_FILE, acquire, run_lock
from agent_harness.server import create_app

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
runner = CliRunner()
HOLD = (
    "import pathlib, sys\n"
    "from agent_harness.runlock import acquire\n"
    "lock = acquire(pathlib.Path(sys.argv[1]), 'child wave')\n"
    "print('ready', flush=True)\n"
    "sys.stdin.read()\n"
)


def status_of(root, task_id):
    return yaml.safe_load((root / f".harness/tasks/{task_id}.yaml").read_text())["status"]


def is_free(root) -> bool:
    try:
        acquire(root, "probe").release()
    except HarnessError:
        return False
    return True


# --- AC1: the lock itself ---------------------------------------------------------------


def test_a_second_acquire_is_refused_and_names_the_holder(tmp_path):
    root = project(tmp_path, task("T-1"))
    first = acquire(root, "wave")
    with pytest.raises(HarnessError) as error:
        acquire(root, "review")  # even in the same process: a different open file
    text = str(error.value)
    assert "another harness run is active" in text
    assert f"wave, pid {os.getpid()}" in text and "since 20" in text
    first.release()
    acquire(root, "review").release()


def test_release_is_idempotent_and_the_context_manager_releases_on_error(tmp_path):
    root = project(tmp_path, task("T-1"))
    lock = acquire(root, "wave")
    assert lock.held
    lock.release()
    lock.release()  # a second call does nothing
    assert not lock.held and is_free(root)
    with pytest.raises(RuntimeError, match="boom"):
        with run_lock(root, "wave") as held:
            assert held.held and not is_free(root)
            raise RuntimeError("boom")
    assert is_free(root)


def test_a_stale_lock_file_does_not_block(tmp_path):
    root = project(tmp_path, task("T-1"))
    (root / LOCK_FILE).write_text(json.dumps({"what": "old", "pid": 1, "since": "2000-01-01"}))
    acquire(root, "wave").release()
    assert (root / LOCK_FILE).exists()  # it is left behind, harmlessly


def test_an_unreadable_holder_note_still_gives_a_clear_error(tmp_path):
    import fcntl

    root = project(tmp_path, task("T-1"))
    descriptor = os.open(root / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    os.write(descriptor, b"\xff not json \x00")
    try:
        with pytest.raises(HarnessError, match="holder unknown"):
            acquire(root, "wave")
    finally:
        os.close(descriptor)


def test_a_project_without_a_harness_directory_is_refused(tmp_path):
    with pytest.raises(HarnessError, match="harness project"):
        acquire(tmp_path, "wave")


def test_a_symlinked_lock_file_is_refused_and_its_target_untouched(tmp_path):
    root = project(tmp_path, task("T-1"))
    target = tmp_path / "elsewhere"
    target.write_text("keep me")
    (root / LOCK_FILE).symlink_to(target)
    with pytest.raises(HarnessError):
        acquire(root, "wave")
    assert target.read_text() == "keep me"


def test_the_lock_file_is_not_a_stray_change_for_commits(tmp_path):
    assert _is_generated(LOCK_FILE)
    assert not _is_generated(".harness/other.lock")  # only this exact file
    root = project(tmp_path, task("T-1"))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    acquire(root, "commit").release()
    assert LOCK_FILE not in changed_files(root)


def start_holder(root):
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLD, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "ready"
    return child


def test_another_process_holds_the_lock_and_a_crash_releases_it(tmp_path):
    root = project(tmp_path, task("T-1"))
    child = start_holder(root)
    try:
        with pytest.raises(HarnessError) as error:
            acquire(root, "wave")
        assert f"child wave, pid {child.pid}" in str(error.value)
        os.kill(child.pid, signal.SIGKILL)  # no cleanup code runs
        child.wait(10)
        acquire(root, "wave").release()  # the kernel dropped the lock with the process
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)


# --- AC2: the CLI -------------------------------------------------------------------------


class Probe(ByTask):
    """A ByTask that also records whether the project lock was held while it ran."""

    def __init__(self, scripts, root):
        super().__init__(scripts, root=root)
        self.held = []

    async def run(self, request, root):
        self.held.append(not is_free(root))
        async for event in super().run(request, root):
            yield event


def with_engine(monkeypatch, engine):
    monkeypatch.setattr(cli, "make_engine", lambda: engine)


@pytest.mark.parametrize("command", ["wave", "run", "review", "commit"])
def test_cli_commands_that_change_the_project_refuse_while_it_is_locked(
    monkeypatch, tmp_path, command
):
    root = project(tmp_path, task("T-1"))
    engine = ByTask({"T-1": ok("T-1")})
    with_engine(monkeypatch, engine)
    args = ["run", "T-1", str(root)] if command == "run" else [command, str(root)]
    before = (root / ".harness/tasks/T-1.yaml").read_text()
    with acquire(root, "server wave"):
        result = runner.invoke(cli.app, args)
    assert result.exit_code == 1, result.output
    assert "another harness run is active" in result.output and "server wave" in result.output
    assert engine.started == []
    assert (root / ".harness/tasks/T-1.yaml").read_text() == before


def test_a_cli_wave_holds_the_lock_while_agents_run_and_frees_it_after(monkeypatch, tmp_path):
    root = project(tmp_path, task("T-1"))
    engine = Probe({"T-1": ok("T-1")}, root)
    with_engine(monkeypatch, engine)
    result = runner.invoke(cli.app, ["wave", str(root), "--json"])
    assert result.exit_code == 0, result.output
    assert engine.held == [True]
    assert is_free(root)


def test_a_wave_with_review_uses_one_lock_for_both_phases(monkeypatch, tmp_path):
    entered, review_held = [], []
    real = cli.run_lock

    def counting(root, what):
        entered.append(what)
        return real(root, what)

    async def fake_review(root, engine, **kwargs):
        review_held.append((not is_free(root), kwargs["task_ids"]))
        return []

    monkeypatch.setattr(cli, "run_lock", counting)
    monkeypatch.setattr("agent_harness.review.review_all", fake_review)
    root = project(tmp_path, task("T-1"))
    engine = Probe({"T-1": ok("T-1")}, root)
    with_engine(monkeypatch, engine)
    result = runner.invoke(cli.app, ["wave", str(root), "--review", "--json"])
    assert result.exit_code == 0, result.output
    assert entered == ["wave"]  # not one lock for the wave and another for the review
    assert engine.held == [True]
    assert review_held == [(True, ["T-1"])]  # the review phase really ran, still locked
    assert is_free(root)


def test_evidence_and_read_only_commands_ignore_the_lock(tmp_path):
    root = project(tmp_path, task("T-1", "IN_PROGRESS"))
    with acquire(root, "wave"):  # an agent inside a wave records evidence like this
        for args in (["check", str(root)], ["next", str(root)], ["validate", str(root)]):
            assert runner.invoke(cli.app, args).exit_code in (0, 1), args
            assert "another harness run" not in runner.invoke(cli.app, args).output
        done = runner.invoke(
            cli.app,
            ["evidence", "run", "T-1", "--path", str(root), "--", sys.executable, "-c", "pass"],
        )
        assert "another harness run" not in done.output
        assert done.exit_code == 0, done.output


# --- AC3: server jobs ---------------------------------------------------------------------


class Blocking:
    async def run(self, request, root):
        from agent_harness.engine.events import Started

        yield Started("s-blocked")
        import asyncio

        await asyncio.Event().wait()


def make_client(root, factory):
    app = create_app(root, TOKEN, engine_factory=factory)
    return testclient.TestClient(app, base_url="http://localhost")


def test_a_job_is_refused_with_409_while_another_actor_holds_the_project(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root, lambda: ByTask({"T-1": ok("T-1")})) as api:
        with acquire(root, "cli wave"):
            response = api.post("/api/jobs", headers=AUTH, json={"kind": "wave"})
            assert response.status_code == 409
            assert "cli wave" in response.json()["detail"]
            assert api.get("/api/jobs", headers=AUTH).json() == []  # no job was created
        assert api.post("/api/jobs", headers=AUTH, json={"kind": "wave"}).status_code == 202


def wait_until(check, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


def test_a_running_job_holds_the_lock_and_a_finished_one_frees_it(tmp_path):
    root = project(tmp_path, task("T-1"))
    with make_client(root, Blocking) as api:
        assert is_free(root)  # an idle server holds nothing
        job_id = api.post("/api/jobs", headers=AUTH, json={"kind": "wave"}).json()["id"]
        wait_until(lambda: status_of(root, "T-1") == "IN_PROGRESS")
        with pytest.raises(HarnessError, match="server wave"):
            acquire(root, "cli wave")
        assert api.post(f"/api/jobs/{job_id}/cancel", headers=AUTH).status_code == 200
        assert is_free(root)  # freed by the time the cancel answered
    assert is_free(root)


def test_every_way_a_job_can_end_frees_the_lock(monkeypatch, tmp_path):
    root = project(tmp_path, task("T-1"))

    def broken():
        raise HarnessError("no engine")

    with make_client(root, lambda: ByTask({"T-1": ok("T-1")})) as api:  # done
        job_id = api.post("/api/jobs", headers=AUTH, json={"kind": "wave"}).json()["id"]
        wait_until(lambda: api.get(f"/api/jobs/{job_id}", headers=AUTH).json()["status"] == "done")
        assert is_free(root)
    root2 = project(tmp_path / "b", task("T-1"))
    with make_client(root2, broken) as api:  # failed
        job_id = api.post("/api/jobs", headers=AUTH, json={"kind": "wave"}).json()["id"]
        wait_until(
            lambda: api.get(f"/api/jobs/{job_id}", headers=AUTH).json()["status"] == "failed"
        )
        assert is_free(root2)
    monkeypatch.setattr(jobs_module, "commit_done_tasks", lambda *a, **k: [])
    root3 = project(tmp_path / "c", task("T-1"))
    with make_client(root3, broken) as api:  # a commit job
        job_id = api.post("/api/jobs", headers=AUTH, json={"kind": "commit"}).json()["id"]
        wait_until(lambda: api.get(f"/api/jobs/{job_id}", headers=AUTH).json()["status"] == "done")
        assert is_free(root3)


def test_cancel_before_the_first_step_and_shutdown_free_the_lock(tmp_path):
    import asyncio

    root = project(tmp_path, task("T-1"))

    async def early_cancel():
        manager = JobManager(root, lambda: Blocking())
        job = manager.start(JobParams.model_validate({"kind": "wave"}))
        assert not is_free(root)
        await manager.cancel(job)  # the task never ran a step
        return is_free(root)

    assert asyncio.run(early_cancel())
    root2 = project(tmp_path / "b", task("T-1"))

    async def shutdown():
        manager = JobManager(root2, lambda: Blocking())
        manager.start(JobParams.model_validate({"kind": "wave"}))
        await asyncio.sleep(0.2)
        assert not is_free(root2)
        await manager.shutdown()
        return is_free(root2)

    assert asyncio.run(shutdown())


def test_the_lock_is_held_in_a_real_thread_free_way_by_a_commit_job(monkeypatch, tmp_path):
    """A commit job runs in a worker thread; the lock must stay held until it returns."""
    gate = threading.Event()
    seen = {}

    def slow_commit(root, *, task_ids, branch):
        seen["free_during"] = is_free(root)
        gate.wait(10)
        return []

    monkeypatch.setattr(jobs_module, "commit_done_tasks", slow_commit)
    root = project(tmp_path, task("T-1"))
    with make_client(root, lambda: None) as api:
        job_id = api.post("/api/jobs", headers=AUTH, json={"kind": "commit"}).json()["id"]
        wait_until(lambda: "free_during" in seen)
        assert seen["free_during"] is False and not is_free(root)
        gate.set()
        wait_until(lambda: api.get(f"/api/jobs/{job_id}", headers=AUTH).json()["status"] == "done")
        assert is_free(root)
