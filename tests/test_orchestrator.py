import asyncio
from pathlib import Path

import pytest
import yaml
from test_engine_runner import project, task

from agent_harness.engine.events import Finished, RateLimit, Started
from agent_harness.filesystem import HarnessError
from agent_harness.orchestrator import run_wave, set_status

EVIDENCE = [{"id": "E1", "command": "pytest", "exit_code": 0}]  # left by an earlier attempt


def ok(task_id: str = "T-1") -> list:
    return [Started(f"s-{task_id}"), Finished(True, f"s-{task_id}", result="done", turns=1)]


class ByTask:
    """Plays one scripted session per task id; like a real agent it records new evidence."""

    def __init__(
        self, scripts: dict[str, list], exit_code: int | None = 0, root: Path | None = None
    ):
        self.scripts = scripts
        self.exit_code = exit_code  # None: the agent records nothing
        self.root = root
        self.started: list[str] = []
        self.seen: dict[str, str] = {}  # status of each task, read from the ledger at run time

    async def run(self, request, root):
        task_id = request.prompt.split("Work on task ")[1].split(" ")[0].rstrip(".")
        self.started.append(task_id)
        path = root / f".harness/tasks/{task_id}.yaml"
        data = yaml.safe_load(path.read_text())
        self.seen[task_id] = data["status"]
        if self.exit_code is not None:
            items = data.get("evidence", [])
            items.append(
                {"id": f"E{len(items) + 1}", "command": "pytest", "exit_code": self.exit_code}
            )
            data["evidence"] = items
            path.write_text(yaml.safe_dump(data))
        for event in self.scripts[task_id]:
            yield event


def status_of(root: Path, task_id: str) -> dict:
    return yaml.safe_load((root / f".harness/tasks/{task_id}.yaml").read_text())


def wave(root: Path, engine, **options):
    return asyncio.run(run_wave(root, engine, **options))


def test_a_finished_task_with_evidence_goes_to_review(tmp_path):
    root = project(tmp_path, task())
    results = wave(root, ByTask({"T-1": ok()}))
    assert [(r.task_id, r.status) for r in results] == [("T-1", "IN_REVIEW")]
    saved = status_of(root, "T-1")
    assert saved["status"] == "IN_REVIEW" and "reason" not in saved
    assert saved["log"][0].endswith("orchestrator: started")
    assert "waiting for independent review" in saved["log"][-1]


def test_a_finished_task_without_evidence_is_blocked_not_reviewed(tmp_path):
    root = project(tmp_path, task())
    (result,) = wave(root, ByTask({"T-1": ok()}, exit_code=None))
    saved = status_of(root, "T-1")
    assert result.status == saved["status"] == "BLOCKED"
    assert "without recording new passing evidence" in saved["reason"]


def test_a_failed_run_blocks_the_task_with_the_agents_error(tmp_path):
    root = project(tmp_path, task())
    engine = ByTask({"T-1": [Started("s"), Finished(False, "s", error="tool crashed")]})
    (result,) = wave(root, engine)
    saved = status_of(root, "T-1")
    assert result.status == saved["status"] == "BLOCKED"
    assert "tool crashed" in saved["reason"]


def test_a_rate_limited_task_returns_to_ready_so_the_next_wave_retries_it(tmp_path):
    root = project(tmp_path, task())
    limited = [
        Started("s"),
        RateLimit("rejected", None),
        Finished(False, "s", error="limit", http_status=429),
    ]
    (result,) = wave(root, ByTask({"T-1": limited}), max_resumes=0)
    assert result.outcome.status == "rate_limited"
    saved = status_of(root, "T-1")
    assert saved["status"] == "READY" and "reason" not in saved


def test_an_engine_crash_blocks_only_that_task(tmp_path):
    class Crashing(ByTask):
        async def run(self, request, root):
            if "Work on task T-1 " in request.prompt:
                raise OSError("boom")
            async for event in super().run(request, root):
                yield event

    root = project(
        tmp_path,
        task("T-1", scope=["src/a/"]),
        task("T-2", scope=["src/b/"]),
    )
    results = wave(root, Crashing({"T-2": ok("T-2")}))
    assert {r.task_id: r.status for r in results} == {"T-1": "BLOCKED", "T-2": "IN_REVIEW"}


def test_only_tasks_the_planner_allows_start_and_max_parallel_caps_them(tmp_path):
    root = project(
        tmp_path,
        task("T-1", scope=["src/a/"]),
        task("T-2", scope=["src/a/x/"]),  # overlaps T-1: held
        task("T-3", scope=["src/b/"]),
        task("T-4", scope=["src/c/"]),
    )
    engine = ByTask({f"T-{n}": ok(f"T-{n}") for n in range(1, 5)})
    results = wave(root, engine, max_parallel=2)
    assert sorted(engine.started) == ["T-1", "T-3"]
    assert [r.task_id for r in results] == ["T-1", "T-3"]
    assert status_of(root, "T-2")["status"] == "READY"
    assert status_of(root, "T-4")["status"] == "READY"


def test_tasks_run_concurrently(tmp_path):
    gate = {"open": 0}

    class Meeting(ByTask):
        async def run(self, request, root):
            gate["open"] += 1
            for _ in range(50):  # both must be running before either may finish
                if gate["open"] == 2:
                    break
                await asyncio.sleep(0.01)
            assert gate["open"] == 2
            async for event in super().run(request, root):
                yield event

    root = project(
        tmp_path,
        task("T-1", scope=["src/a/"]),
        task("T-2", scope=["src/b/"]),
    )
    results = wave(root, Meeting({"T-1": ok("T-1"), "T-2": ok("T-2")}))
    assert {r.status for r in results} == {"IN_REVIEW"}


def test_an_empty_plan_runs_nothing(tmp_path):
    root = project(tmp_path, task(status="CANCELLED", reason="dropped"))
    assert wave(root, ByTask({})) == []


def test_a_broken_ledger_runs_nothing_and_changes_nothing(tmp_path):
    root = project(tmp_path, task(depends_on=["T-9"]))
    before = (root / ".harness/tasks/T-1.yaml").read_text()
    with pytest.raises(HarnessError, match="ledger has errors"):
        wave(root, ByTask({}))
    assert (root / ".harness/tasks/T-1.yaml").read_text() == before


def test_max_parallel_must_be_positive(tmp_path):
    with pytest.raises(HarnessError, match="at least 1"):
        wave(project(tmp_path, task()), ByTask({}), max_parallel=0)


def test_set_status_clears_the_reason_when_a_task_leaves_blocked(tmp_path):
    root = project(tmp_path, task(status="BLOCKED", reason="waiting"))
    set_status(root, "T-1", "READY", note="unblocked")
    saved = status_of(root, "T-1")
    assert saved["status"] == "READY" and "reason" not in saved


def _cli(monkeypatch, root, engine, *args):
    from typer.testing import CliRunner

    from agent_harness import cli

    monkeypatch.setattr(cli, "make_engine", lambda: engine)
    return CliRunner().invoke(cli.app, ["wave", str(root), *args])


def test_wave_command_reports_each_task_and_exits_zero_when_all_reach_review(tmp_path, monkeypatch):
    import json

    root = project(tmp_path, task())
    out = _cli(monkeypatch, root, ByTask({"T-1": ok()}), "--json")
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["ok"] is True and report["started"] == 1
    assert (report["tasks"][0]["task"], report["tasks"][0]["status"]) == ("T-1", "IN_REVIEW")


def test_wave_command_exits_one_when_a_task_is_blocked(tmp_path, monkeypatch):
    root = project(tmp_path, task())
    out = _cli(monkeypatch, root, ByTask({"T-1": ok()}, exit_code=None), "--json")
    assert out.exit_code == 1


def test_wave_command_refuses_a_broken_ledger(tmp_path, monkeypatch):
    root = project(tmp_path, task(depends_on=["T-9"]))
    out = _cli(monkeypatch, root, ByTask({}), "--json")
    assert out.exit_code == 1 and "ledger has errors" in out.output


def test_wave_command_prints_live_events_in_text_mode(tmp_path, monkeypatch):
    from agent_harness.engine.events import Text

    root = project(tmp_path, task())
    engine = ByTask({"T-1": [Started("s"), Text("working"), Finished(True, "s")]})
    out = _cli(monkeypatch, root, engine)
    assert "T-1: working" in out.output


def test_evidence_from_an_earlier_attempt_does_not_pass_review(tmp_path):
    root = project(tmp_path, task(evidence=EVIDENCE))
    (result,) = wave(root, ByTask({"T-1": ok()}, exit_code=None))
    assert result.status == "BLOCKED"


def test_failing_new_evidence_does_not_pass_review(tmp_path):
    root = project(tmp_path, task())
    (result,) = wave(root, ByTask({"T-1": ok()}, exit_code=1))
    assert result.status == "BLOCKED"


def test_a_task_without_acceptance_criteria_is_blocked(tmp_path):
    root = project(tmp_path, task(acceptance=[]))
    (result,) = wave(root, ByTask({"T-1": ok()}))
    assert result.status == "BLOCKED" and "no acceptance" in result.note


def test_the_task_is_in_progress_in_the_ledger_while_the_agent_runs(tmp_path):
    root = project(tmp_path, task())
    engine = ByTask({"T-1": ok()})
    wave(root, engine)
    assert engine.seen == {"T-1": "IN_PROGRESS"}


def test_a_settle_failure_does_not_strand_the_task_or_its_sibling(tmp_path, monkeypatch):
    import agent_harness.orchestrator as orchestrator

    real = orchestrator._settle

    def flaky(root, task_id, outcome, before):
        if task_id == "T-1":
            raise HarnessError("disk full")
        return real(root, task_id, outcome, before)

    monkeypatch.setattr(orchestrator, "_settle", flaky)
    root = project(tmp_path, task("T-1", scope=["src/a/"]), task("T-2", scope=["src/b/"]))
    results = wave(root, ByTask({"T-1": ok("T-1"), "T-2": ok("T-2")}))
    assert {r.task_id: r.status for r in results} == {"T-1": "BLOCKED", "T-2": "IN_REVIEW"}
    saved = status_of(root, "T-1")
    assert saved["status"] == "BLOCKED" and "disk full" in saved["reason"]


def test_a_failed_start_releases_the_tasks_already_claimed(tmp_path, monkeypatch):
    import agent_harness.orchestrator as orchestrator

    real = orchestrator.set_status

    def failing(root, task_id, status, **kw):
        if task_id == "T-2" and status == "IN_PROGRESS":
            raise HarnessError("lock timeout")
        return real(root, task_id, status, **kw)

    monkeypatch.setattr(orchestrator, "set_status", failing)
    root = project(tmp_path, task("T-1", scope=["src/a/"]), task("T-2", scope=["src/b/"]))
    engine = ByTask({})
    with pytest.raises(HarnessError, match="lock timeout"):
        wave(root, engine)
    assert engine.started == []
    assert status_of(root, "T-1")["status"] == status_of(root, "T-2")["status"] == "READY"


def test_set_status_refuses_a_task_that_is_no_longer_in_the_expected_status(tmp_path):
    root = project(tmp_path, task(status="CANCELLED", reason="dropped"))
    before = (root / ".harness/tasks/T-1.yaml").read_text()
    with pytest.raises(HarnessError, match="expected READY"):
        set_status(root, "T-1", "IN_PROGRESS", note="started", expect=("READY",))
    assert (root / ".harness/tasks/T-1.yaml").read_text() == before


def test_an_invalid_move_leaves_the_file_untouched(tmp_path):
    root = project(tmp_path, task())
    before = (root / ".harness/tasks/T-1.yaml").read_text()
    with pytest.raises(HarnessError):
        set_status(root, "T-1", "NOT_A_STATUS", note="x")
    assert (root / ".harness/tasks/T-1.yaml").read_text() == before


def test_the_log_keeps_only_the_latest_entries(tmp_path):
    from agent_harness.orchestrator import MAX_LOG

    root = project(tmp_path, task(log=[f"entry {n}" for n in range(MAX_LOG + 10)]))
    set_status(root, "T-1", "IN_PROGRESS", note="latest")
    log = status_of(root, "T-1")["log"]
    assert len(log) == MAX_LOG and log[-1].endswith("latest") and "entry 0" not in log


def test_a_rate_limit_then_a_resume_finishes_in_one_wave(tmp_path):
    class Resuming(ByTask):
        calls = 0

        async def run(self, request, root):
            Resuming.calls += 1
            if Resuming.calls == 1:
                async for event in super().run(request, root):
                    yield event
                return
            yield Started("s")
            yield Finished(True, "s", result="done", turns=1)

    limited = [Started("s"), Finished(False, "s", error="limit", http_status=429)]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    root = project(tmp_path, task())
    (result,) = wave(root, Resuming({"T-1": limited}), sleep=sleep)
    assert slept and result.outcome.status == "completed" and result.status == "IN_REVIEW"


def test_events_are_tagged_with_their_task(tmp_path):
    seen: list[tuple[str, object]] = []
    root = project(tmp_path, task("T-1", scope=["src/a/"]), task("T-2", scope=["src/b/"]))
    wave(
        root,
        ByTask({"T-1": ok("T-1"), "T-2": ok("T-2")}),
        on_event=lambda task_id, event: seen.append((task_id, event)),
    )
    assert {task_id for task_id, _ in seen} == {"T-1", "T-2"}


def test_cancelling_the_wave_releases_running_tasks(tmp_path):
    running = asyncio.Event()

    class Hanging(ByTask):
        async def run(self, request, root):
            yield Started("s")
            running.set()  # the engine is now running; only then is the wave cancelled
            await asyncio.sleep(60)

    root = project(tmp_path, task())

    async def go():
        runner = asyncio.create_task(run_wave(root, Hanging({"T-1": []})))
        await asyncio.wait_for(running.wait(), 5)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    asyncio.run(go())
    saved = status_of(root, "T-1")
    assert saved["status"] == "READY" and "interrupted" in saved["log"][-1]


def test_an_unrecorded_result_is_reported_and_fails_the_wave_command(tmp_path, monkeypatch):
    import json

    import agent_harness.orchestrator as orchestrator

    real = orchestrator.set_status

    def failing(root, task_id, status, **kw):
        if status != "IN_PROGRESS" or kw.get("expect") != ("READY",):
            raise HarnessError("disk full")  # nothing after the claim can be written
        return real(root, task_id, status, **kw)

    monkeypatch.setattr(orchestrator, "set_status", failing)
    root = project(tmp_path, task())
    out = _cli(monkeypatch, root, ByTask({"T-1": ok()}), "--json")
    assert out.exit_code == 1
    report = json.loads(out.output)
    assert report["tasks"][0]["recorded"] is False
    assert status_of(root, "T-1")["status"] == "IN_PROGRESS"  # reported, not hidden
