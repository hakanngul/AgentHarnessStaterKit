import asyncio
from pathlib import Path

import pytest
import yaml
from test_engine_runner import project, task

from agent_harness.engine.events import Finished, RateLimit, Started
from agent_harness.filesystem import HarnessError
from agent_harness.orchestrator import run_wave, set_status

EVIDENCE = [{"id": "E1", "command": "pytest", "exit_code": 0}]


def ok(task_id: str = "T-1") -> list:
    return [Started(f"s-{task_id}"), Finished(True, f"s-{task_id}", result="done", turns=1)]


class ByTask:
    """Plays one scripted session per task id and records the order tasks started in."""

    def __init__(self, scripts: dict[str, list]):
        self.scripts = scripts
        self.started: list[str] = []

    async def run(self, request, root):
        task_id = request.prompt.split("Work on task ")[1].split(" ")[0].rstrip(".")
        self.started.append(task_id)
        for event in self.scripts[task_id]:
            yield event


def status_of(root: Path, task_id: str) -> dict:
    return yaml.safe_load((root / f".harness/tasks/{task_id}.yaml").read_text())


def wave(root: Path, engine, **options):
    return asyncio.run(run_wave(root, engine, **options))


def test_a_finished_task_with_evidence_goes_to_review(tmp_path):
    root = project(tmp_path, task(evidence=EVIDENCE))
    results = wave(root, ByTask({"T-1": ok()}))
    assert [(r.task_id, r.status) for r in results] == [("T-1", "IN_REVIEW")]
    saved = status_of(root, "T-1")
    assert saved["status"] == "IN_REVIEW" and "reason" not in saved
    assert saved["log"][0].endswith("orchestrator: started")
    assert "waiting for independent review" in saved["log"][-1]


def test_a_finished_task_without_evidence_is_blocked_not_reviewed(tmp_path):
    root = project(tmp_path, task())
    (result,) = wave(root, ByTask({"T-1": ok()}))
    saved = status_of(root, "T-1")
    assert result.status == saved["status"] == "BLOCKED"
    assert "no recorded evidence" in saved["reason"]


def test_a_failed_run_blocks_the_task_with_the_agents_error(tmp_path):
    root = project(tmp_path, task(evidence=EVIDENCE))
    engine = ByTask({"T-1": [Started("s"), Finished(False, "s", error="tool crashed")]})
    (result,) = wave(root, engine)
    saved = status_of(root, "T-1")
    assert result.status == saved["status"] == "BLOCKED"
    assert "tool crashed" in saved["reason"]


def test_a_rate_limited_task_returns_to_ready_so_the_next_wave_retries_it(tmp_path):
    root = project(tmp_path, task(evidence=EVIDENCE))
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
        task("T-1", scope=["src/a/"], evidence=EVIDENCE),
        task("T-2", scope=["src/b/"], evidence=EVIDENCE),
    )
    results = wave(root, Crashing({"T-2": ok("T-2")}))
    assert {r.task_id: r.status for r in results} == {"T-1": "BLOCKED", "T-2": "IN_REVIEW"}


def test_only_tasks_the_planner_allows_start_and_max_parallel_caps_them(tmp_path):
    root = project(
        tmp_path,
        task("T-1", scope=["src/a/"], evidence=EVIDENCE),
        task("T-2", scope=["src/a/x/"], evidence=EVIDENCE),  # overlaps T-1: held
        task("T-3", scope=["src/b/"], evidence=EVIDENCE),
        task("T-4", scope=["src/c/"], evidence=EVIDENCE),
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
        task("T-1", scope=["src/a/"], evidence=EVIDENCE),
        task("T-2", scope=["src/b/"], evidence=EVIDENCE),
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


def test_set_status_keeps_the_ledger_valid_and_clears_the_reason(tmp_path):
    root = project(tmp_path, task(status="BLOCKED", reason="waiting"))
    set_status(root, "T-1", "READY", note="unblocked")
    saved = status_of(root, "T-1")
    assert saved["status"] == "READY" and "reason" not in saved
    with pytest.raises(HarnessError):
        set_status(root, "T-1", "NOT_A_STATUS", note="x")


def _cli(monkeypatch, root, engine, *args):
    from typer.testing import CliRunner

    from agent_harness import cli

    monkeypatch.setattr(cli, "make_engine", lambda: engine)
    return CliRunner().invoke(cli.app, ["wave", str(root), *args])


def test_wave_command_reports_each_task_and_exits_zero_when_all_reach_review(tmp_path, monkeypatch):
    import json

    root = project(tmp_path, task(evidence=EVIDENCE))
    out = _cli(monkeypatch, root, ByTask({"T-1": ok()}), "--json")
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["ok"] is True and report["started"] == 1
    assert (report["tasks"][0]["task"], report["tasks"][0]["status"]) == ("T-1", "IN_REVIEW")


def test_wave_command_exits_one_when_a_task_is_blocked(tmp_path, monkeypatch):
    root = project(tmp_path, task())
    out = _cli(monkeypatch, root, ByTask({"T-1": ok()}), "--json")
    assert out.exit_code == 1


def test_wave_command_refuses_a_broken_ledger(tmp_path, monkeypatch):
    root = project(tmp_path, task(depends_on=["T-9"]))
    out = _cli(monkeypatch, root, ByTask({}), "--json")
    assert out.exit_code == 1 and "ledger has errors" in out.output


def test_wave_command_prints_live_events_in_text_mode(tmp_path, monkeypatch):
    from agent_harness.engine.events import Text

    root = project(tmp_path, task(evidence=EVIDENCE))
    engine = ByTask({"T-1": [Started("s"), Text("working"), Finished(True, "s")]})
    out = _cli(monkeypatch, root, engine)
    assert "T-1: working" in out.output
