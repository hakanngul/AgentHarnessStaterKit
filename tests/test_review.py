import asyncio
import json
from pathlib import Path

import pytest
import yaml
from test_engine_runner import project, task
from test_orchestrator import ByTask
from typer.testing import CliRunner

from agent_harness import cli
from agent_harness.check import inspect_ledger
from agent_harness.engine.events import Finished, Started
from agent_harness.engine.prompt import review_prompt, task_prompt
from agent_harness.filesystem import HarnessError
from agent_harness.models import Task
from agent_harness.orchestrator import run_wave
from agent_harness.review import parse_verdict, review_all, review_task

EVIDENCE = [{"id": "E1", "command": "pytest", "exit_code": 0, "summary": "12 passed"}]
APPROVE = "Looks right.\nVERDICT: APPROVED\nAC1: E1\nNOTES: files read, scope respected"
REJECT = "VERDICT: CHANGES_REQUESTED\nNOTES: AC1 is not covered by a test"


def in_review(task_id: str = "T-1", **overrides) -> dict:
    return task(task_id, status="IN_REVIEW", evidence=EVIDENCE, **overrides)


class Reviewer:
    """Answers a review prompt with a scripted text; can tamper with the task file first."""

    def __init__(self, answers: dict[str, str] | str, tamper=None, fail: bool = False):
        self.answers = answers
        self.tamper = tamper
        self.fail = fail
        self.reviewed: list[str] = []

    async def run(self, request, root):
        task_id = request.prompt.split("Independently review task ")[1].split(".")[0]
        self.reviewed.append(task_id)
        if self.tamper:
            path = root / f".harness/tasks/{task_id}.yaml"
            data = yaml.safe_load(path.read_text())
            self.tamper(data)
            path.write_text(yaml.safe_dump(data))
        text = self.answers if isinstance(self.answers, str) else self.answers[task_id]
        yield Started("r")
        yield Finished(not self.fail, "r", result=text, error="boom" if self.fail else "")


def saved(root: Path, task_id: str = "T-1") -> dict:
    return yaml.safe_load((root / f".harness/tasks/{task_id}.yaml").read_text())


def review(root: Path, engine, task_id: str = "T-1", **options):
    return asyncio.run(review_task(root, task_id, engine, **options))


# --- parsing ----------------------------------------------------------------------


def test_the_last_verdict_line_wins_and_criteria_are_mapped():
    model = Task.model_validate(in_review(acceptance=[{"id": "AC1", "text": "x"}]))
    text = "VERDICT: APPROVED\nAC1: E9\nthen I changed my mind\nVERDICT: CHANGES_REQUESTED\nAC1: E1"
    assert parse_verdict(text, model) == ("CHANGES_REQUESTED", {"AC1": "E9"})


def test_no_verdict_line_means_no_verdict():
    model = Task.model_validate(in_review())
    assert parse_verdict("I think it is fine", model) == (None, {})
    assert parse_verdict("not a VERDICT: APPROVED line", model)[0] is None


# --- recording the verdict --------------------------------------------------------


def test_an_approval_with_backed_criteria_marks_the_task_done_and_the_ledger_stays_valid(tmp_path):
    root = project(tmp_path, in_review())
    result = review(root, Reviewer(APPROVE))
    data = saved(root)
    assert (result.status, result.verdict) == ("DONE", "APPROVED")
    assert data["status"] == "DONE" and data["review"]["status"] == "approved"
    assert data["review"]["by"] == "reviewer"
    assert data["acceptance"][0]["verified_by"] == ["E1"]
    report, *_ = inspect_ledger(root)
    assert report.errors == []


def test_changes_requested_send_the_task_back_with_the_notes(tmp_path):
    root = project(tmp_path, in_review())
    result = review(root, Reviewer(REJECT))
    data = saved(root)
    assert (result.status, data["status"]) == ("READY", "READY")
    assert data["review"]["status"] == "changes_requested"
    assert "not covered by a test" in data["review"]["notes"]
    prompt = task_prompt(Task.model_validate(data), "developer")
    assert "A reviewer requested changes" in prompt and "> VERDICT: CHANGES_REQUESTED" in prompt


@pytest.mark.parametrize(
    "answer",
    [
        "VERDICT: APPROVED\nNOTES: fine",  # names no evidence
        "VERDICT: APPROVED\nAC1: E7\nNOTES: fine",  # names evidence that does not exist
    ],
)
def test_an_approval_without_backing_evidence_is_downgraded(tmp_path, answer):
    root = project(tmp_path, in_review())
    result = review(root, Reviewer(answer))
    data = saved(root)
    assert (result.verdict, data["status"]) == ("CHANGES_REQUESTED", "READY")
    assert "Approval was incomplete" in data["review"]["notes"]
    assert "verified_by" not in data["acceptance"][0]


def test_an_approval_naming_failing_evidence_is_downgraded(tmp_path):
    failing = [{"id": "E1", "command": "pytest", "exit_code": 1, "summary": "1 failed"}]
    root = project(tmp_path, task(status="IN_REVIEW", evidence=failing))
    result = review(root, Reviewer(APPROVE))
    assert result.verdict == "CHANGES_REQUESTED" and saved(root)["status"] == "READY"


def test_no_verdict_leaves_the_task_in_review_and_says_so(tmp_path):
    root = project(tmp_path, in_review())
    result = review(root, Reviewer("Seems fine to me."))
    data = saved(root)
    assert (result.status, data["status"]) == ("IN_REVIEW", "IN_REVIEW")
    assert "without a verdict" in data["log"][-1]


def test_a_failed_review_run_changes_nothing_but_the_log(tmp_path):
    root = project(tmp_path, in_review())
    result = review(root, Reviewer(APPROVE, fail=True))
    data = saved(root)
    assert (result.status, data["status"]) == ("IN_REVIEW", "IN_REVIEW")
    assert (
        data.get("review", {}).get("status", "pending") == "pending" and "boom" in data["log"][-1]
    )


def test_a_reviewer_that_edits_the_evidence_blocks_the_task(tmp_path):
    def forge(data):
        data["evidence"][0]["exit_code"] = 0
        data["evidence"].append({"id": "E2", "command": "forged", "exit_code": 0})

    root = project(tmp_path, in_review())
    result = review(root, Reviewer(APPROVE, tamper=forge))
    data = saved(root)
    assert (result.status, data["status"]) == ("BLOCKED", "BLOCKED")
    assert "changed the task's evidence" in data["reason"]
    assert data.get("review", {}).get("status", "pending") == "pending"


# --- refusals ---------------------------------------------------------------------


def test_only_in_review_tasks_are_reviewed(tmp_path):
    root = project(tmp_path, task())
    engine = Reviewer(APPROVE)
    with pytest.raises(HarnessError, match="only IN_REVIEW"):
        review(root, engine)
    assert engine.reviewed == []


def test_an_owner_cannot_review_their_own_task(tmp_path):
    root = project(tmp_path, in_review())
    engine = Reviewer(APPROVE)
    with pytest.raises(HarnessError, match="cannot be reviewed by its owner"):
        review(root, engine, role="Developer")
    assert engine.reviewed == []


def test_an_undefined_reviewer_role_changes_nothing(tmp_path):
    root = project(tmp_path, in_review())
    result = review(root, Reviewer(APPROVE), role="auditor")
    assert result.status == "IN_REVIEW" and "not defined" in result.note


def test_review_all_takes_only_in_review_tasks_and_honours_the_filter(tmp_path):
    root = project(
        tmp_path,
        in_review("T-1", scope=["src/a/"]),
        in_review("T-2", scope=["src/b/"]),
        task("T-3", scope=["src/c/"]),
    )
    engine = Reviewer(APPROVE)
    results = asyncio.run(review_all(root, engine, task_ids=["T-2"]))
    assert [r.task_id for r in results] == ["T-2"] and engine.reviewed == ["T-2"]
    results = asyncio.run(review_all(root, engine))
    assert [r.task_id for r in results] == ["T-1"]  # T-2 is DONE now, T-3 is READY
    assert saved(root, "T-3")["status"] == "READY"


def test_review_all_refuses_a_broken_ledger(tmp_path):
    root = project(tmp_path, in_review(depends_on=["T-9"]))
    with pytest.raises(HarnessError, match="ledger has errors"):
        asyncio.run(review_all(root, Reviewer(APPROVE)))


def test_review_all_reports_an_owner_conflict_without_stopping_the_others(tmp_path):
    other = in_review("T-2", scope=["src/b/"], owner="reviewer")
    root = project(tmp_path, in_review("T-1", scope=["src/a/"]), other)
    results = asyncio.run(review_all(root, Reviewer(APPROVE)))
    by_task = {r.task_id: r for r in results}
    assert by_task["T-1"].status == "DONE"
    assert by_task["T-2"].status == "IN_REVIEW" and not by_task["T-2"].recorded


# --- prompt -----------------------------------------------------------------------


def test_the_review_prompt_lists_criteria_evidence_and_the_required_format():
    prompt = review_prompt(Task.model_validate(in_review()), "reviewer")
    assert "AC1: Valid user can sign in" in prompt
    assert "E1 [passed] pytest: 12 passed" in prompt
    assert "VERDICT: APPROVED" in prompt and "Do not edit any file" in prompt


# --- the whole loop ---------------------------------------------------------------


def test_changes_requested_then_a_second_wave_reaches_review_again(tmp_path):
    root = project(tmp_path, in_review())
    review(root, Reviewer(REJECT))
    results = asyncio.run(run_wave(root, ByTask({"T-1": [Started("s"), Finished(True, "s")]})))
    assert [(r.task_id, r.status) for r in results] == [("T-1", "IN_REVIEW")]
    assert saved(root)["review"]["status"] == "changes_requested"  # until the reviewer runs again
    review(root, Reviewer(APPROVE.replace("E1", "E2")))
    assert saved(root)["status"] == "DONE"


class Both:
    """Developer runs and review runs for the CLI tests."""

    def __init__(self, answer: str):
        self.dev = ByTask({"T-1": [Started("s"), Finished(True, "s")]})
        self.rev = Reviewer(answer)

    async def run(self, request, root):
        engine = self.rev if "Independently review" in request.prompt else self.dev
        async for event in engine.run(request, root):
            yield event


def invoke(monkeypatch, root, engine, *args):
    monkeypatch.setattr(cli, "make_engine", lambda: engine)
    return CliRunner().invoke(cli.app, [*args[:1], str(root), *args[1:]])


def test_review_command_marks_approved_tasks_done_and_exits_zero(tmp_path, monkeypatch):
    root = project(tmp_path, in_review())
    out = invoke(monkeypatch, root, Reviewer(APPROVE), "review", "--json")
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert (report["ok"], report["reviewed"]) == (True, 1)
    assert report["tasks"][0]["status"] == "DONE"


def test_review_command_exits_one_on_changes_requested(tmp_path, monkeypatch):
    root = project(tmp_path, in_review())
    out = invoke(monkeypatch, root, Reviewer(REJECT), "review", "--json")
    assert (
        out.exit_code == 1 and json.loads(out.output)["tasks"][0]["verdict"] == "CHANGES_REQUESTED"
    )


def test_review_command_with_nothing_to_review_succeeds(tmp_path, monkeypatch):
    root = project(tmp_path, task(status="CANCELLED", reason="dropped"))
    out = invoke(monkeypatch, root, Reviewer(APPROVE), "review", "--json")
    assert out.exit_code == 0 and json.loads(out.output)["reviewed"] == 0


def test_review_command_refuses_the_owner_as_reviewer(tmp_path, monkeypatch):
    root = project(tmp_path, in_review())
    out = invoke(monkeypatch, root, Reviewer(APPROVE), "review", "--json", "--role", "developer")
    assert out.exit_code == 1


def test_wave_with_review_runs_the_developer_then_the_reviewer(tmp_path, monkeypatch):
    root = project(tmp_path, task())
    engine = Both(APPROVE)
    out = invoke(monkeypatch, root, engine, "wave", "--json", "--review")
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["ok"] is True and report["reviews"][0]["status"] == "DONE"
    assert saved(root)["status"] == "DONE"


def test_wave_with_review_exits_one_when_the_reviewer_asks_for_changes(tmp_path, monkeypatch):
    root = project(tmp_path, task())
    out = invoke(monkeypatch, root, Both(REJECT), "wave", "--json", "--review")
    report = json.loads(out.output)
    assert out.exit_code == 1 and report["reviews"][0]["verdict"] == "CHANGES_REQUESTED"
    assert saved(root)["status"] == "READY"


def test_wave_without_review_does_not_call_the_reviewer(tmp_path, monkeypatch):
    root = project(tmp_path, task())
    engine = Both(APPROVE)
    out = invoke(monkeypatch, root, engine, "wave", "--json")
    assert out.exit_code == 0 and "reviews" not in json.loads(out.output)
    assert engine.rev.reviewed == [] and saved(root)["status"] == "IN_REVIEW"
