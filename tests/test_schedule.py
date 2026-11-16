import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_harness.check import DECISIONS_DIR, TASKS_DIR
from agent_harness.cli import app
from agent_harness.schedule import scopes_overlap

runner = CliRunner()

PHASES = [{"name": "spec"}, {"name": "implement", "requires": ["spec"]}]


def write_config(root: Path, **extra) -> None:
    target = root / ".harness/config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({"project_name": "Demo", **extra}))


def task(task_id: str, status: str = "READY", **overrides) -> dict:
    data = {"id": task_id, "title": f"Task {task_id}", "owner": "developer", "status": status}
    if status in {"BLOCKED", "CANCELLED"}:
        data["reason"] = "Waiting for the designer"
    if status in {"IN_REVIEW", "DONE"}:
        data["acceptance"] = [{"id": "AC1", "text": "Works", "verified_by": ["E1"]}]
        data["evidence"] = [{"id": "E1", "command": "pytest", "exit_code": 0}]
    if status == "DONE":
        data["review"] = {"status": "approved", "by": "reviewer"}
    data.update(overrides)
    return data


def decision(decision_id: str = "DEC-1", **overrides) -> dict:
    data = {
        "id": decision_id,
        "question": "Which payment provider?",
        "options": [{"id": "A", "text": "Stripe"}, {"id": "B", "text": "Iyzico"}],
    }
    data.update(overrides)
    return data


def write(root: Path, *items: dict) -> None:
    for item in items:
        directory = DECISIONS_DIR if item["id"].startswith("DEC-") else TASKS_DIR
        target = root / directory / f"{item['id']}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(item, sort_keys=False))


def plan(root: Path, *items: dict) -> dict:
    write(root, *items)
    result = runner.invoke(app, ["next", str(root), "--json"])
    report = json.loads(result.output)
    assert result.exit_code == (0 if report["ok"] else 1)
    return report


def ids(entries: list[dict]) -> list[str]:
    return [entry["id"] for entry in entries]


# --- scope overlap ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (["src/api/"], ["src/web/"], False),
        (["src/api/login.py"], ["src/api/logout.py"], False),
        (["src/api"], ["src/apiv2"], False),
        (["src/"], ["src/api/login.py"], True),
        (["src/api/login.py"], ["src/"], True),
        (["src/api/login.py"], ["src/api/login.py"], True),
        (["src/api/**"], ["src/api/v1/users.py"], True),
        (["src/api/*.py"], ["src/api/v1/"], True),
        (["**"], ["docs/"], True),
        (["docs/", "src/web/"], ["tests/", "src/web/app.py"], True),
        (["docs/", "src/web/"], ["tests/", "src/api/"], False),
        (["./src/api/"], ["src/API/login.py"], True),
        (["src\\api"], ["src/api/login.py"], True),
        (["../other"], ["docs/"], True),
        (["src/api/"], [], True),
        ([], [], True),
        (["docs/\u00f6zet.md"], ["docs/o\u0308zet.md"], True),
        (["/home/u/proj/src/api/"], ["src/web/"], True),
        (["~/proj/src/api"], ["docs/"], True),
        (["C:\\proj\\src\\api"], ["docs/"], True),
        (["src/api/ "], ["src/api/login.py"], True),
        ([" src / api "], ["src/api/login.py"], True),
        (["src/web/"], ["/"], True),
    ],
)
def test_scopes_overlap(first, second, expected):
    assert scopes_overlap(first, second) is expected
    assert scopes_overlap(second, first) is expected


# --- invalid ledger ---------------------------------------------------------------


def test_invalid_ledger_yields_errors_and_no_plan(tmp_path):
    report = plan(tmp_path, task("T-1", scope=["src/"]), task("T-2", depends_on=["T-9"]))
    assert report["ok"] is False
    assert any("unknown T-9" in error for error in report["errors"])
    for key in ("start", "running", "held", "unblocked", "waiting", "decisions"):
        assert report[key] == [], key
    assert "No plan was computed: fix the ledger errors first" in report["warnings"]


def test_errors_match_check(tmp_path):
    write(tmp_path, task("T-1", "DONE", review={"status": "pending"}))
    checked = json.loads(runner.invoke(app, ["check", str(tmp_path), "--json"]).output)
    planned = json.loads(runner.invoke(app, ["next", str(tmp_path), "--json"]).output)
    assert planned["errors"] == checked["errors"] != []


def test_empty_ledger_is_valid_and_not_finished(tmp_path):
    report = plan(tmp_path)
    assert report["ok"] is True
    assert report["finished"] is False
    assert report["start"] == []


def test_human_output_and_exit_code(tmp_path):
    write(tmp_path, task("T-1", scope=["src/"]))
    result = runner.invoke(app, ["next", str(tmp_path)])
    assert result.exit_code == 0
    assert "start:" in result.output and "T-1" in result.output


# --- classification ---------------------------------------------------------------


def test_tasks_are_sorted_into_lists_by_status(tmp_path):
    report = plan(
        tmp_path,
        task("T-1", "DONE"),
        task("T-2", "IN_PROGRESS", scope=["src/api/"]),
        task("T-3", "IN_REVIEW", scope=["src/web/"]),
        task("T-4", "READY", scope=["docs/"]),
        task("T-5", "BACKLOG"),
        task("T-6", "BLOCKED"),
        task("T-7", "CANCELLED"),
    )
    assert ids(report["running"]) == ["T-2", "T-3"]
    assert ids(report["start"]) == ["T-4"]
    assert ids(report["unblocked"]) == ["T-5"]
    assert ids(report["waiting"]) == ["T-6"]
    assert report["waiting"][0]["reasons"] == ["BLOCKED: Waiting for the designer"]
    assert report["held"] == []
    assert report["finished"] is False


def test_entries_carry_owner_phase_and_scope(tmp_path):
    write_config(tmp_path, workflow={"phases": PHASES})
    report = plan(tmp_path, task("T-1", phase="spec", owner="lead", scope=["docs/spec/"]))
    assert report["start"] == [
        {
            "id": "T-1",
            "title": "Task T-1",
            "owner": "lead",
            "status": "READY",
            "phase": "spec",
            "scope": ["docs/spec/"],
        }
    ]


def test_backlog_with_unfinished_dependency_waits_with_the_reason(tmp_path):
    report = plan(
        tmp_path,
        task("T-1", "IN_PROGRESS", scope=["src/"]),
        task("T-2", "BACKLOG", depends_on=["T-1"]),
    )
    assert report["unblocked"] == []
    assert report["waiting"][0]["id"] == "T-2"
    assert report["waiting"][0]["reasons"] == ["T-1 is IN_PROGRESS"]


def test_backlog_with_finished_dependencies_is_unblocked(tmp_path):
    report = plan(
        tmp_path,
        task("T-1", "DONE"),
        decision(status="DECIDED", answer="A", decided_by="user", decided_at="2026-11-17"),
        task("T-2", "BACKLOG", depends_on=["T-1", "DEC-1"]),
    )
    assert ids(report["unblocked"]) == ["T-2"]
    assert report["waiting"] == [] and report["decisions"] == []


def test_blocked_task_lists_its_reason_and_unmet_dependencies(tmp_path):
    report = plan(tmp_path, decision(), task("T-1", "BLOCKED", depends_on=["DEC-1"]))
    assert report["waiting"][0]["reasons"] == [
        "BLOCKED: Waiting for the designer",
        "DEC-1 is PENDING",
    ]


def test_cancelled_dependency_keeps_a_task_waiting(tmp_path):
    report = plan(tmp_path, task("T-1", "CANCELLED"), task("T-2", "BACKLOG", depends_on=["T-1"]))
    assert report["waiting"][0]["reasons"] == ["T-1 is CANCELLED"]


def test_pending_decisions_list_the_open_tasks_they_block(tmp_path):
    report = plan(
        tmp_path,
        decision("DEC-1"),
        decision("DEC-2", question="Dark mode?"),
        decision("DEC-3", status="WITHDRAWN"),
        task("T-1", "BACKLOG", depends_on=["DEC-1"]),
        task("T-2", "BLOCKED", depends_on=["DEC-1"]),
        task("T-3", "CANCELLED", depends_on=["DEC-1"]),
    )
    assert report["decisions"] == [
        {"id": "DEC-1", "question": "Which payment provider?", "blocks": ["T-1", "T-2"]},
        {"id": "DEC-2", "question": "Dark mode?", "blocks": []},
    ]


def test_finished_when_every_task_is_done_or_cancelled(tmp_path):
    assert plan(tmp_path, task("T-1", "DONE"), task("T-2", "CANCELLED"))["finished"] is True


# --- phase gate -------------------------------------------------------------------


def test_backlog_task_missing_a_required_phase_waits(tmp_path):
    write_config(tmp_path, workflow={"phases": PHASES})
    report = plan(tmp_path, task("T-1", "BACKLOG", phase="implement"))
    assert report["waiting"][0]["reasons"] == ["no 'spec' task is reachable through depends_on"]


def test_backlog_task_with_its_required_phase_done_is_unblocked(tmp_path):
    write_config(tmp_path, workflow={"phases": PHASES})
    report = plan(
        tmp_path,
        task("T-1", "DONE", phase="spec"),
        task("T-2", "BACKLOG", phase="implement", depends_on=["T-1"]),
    )
    assert ids(report["unblocked"]) == ["T-2"]


def test_backlog_task_whose_required_phase_is_unfinished_waits(tmp_path):
    write_config(tmp_path, workflow={"phases": PHASES})
    report = plan(
        tmp_path,
        task("T-1", "IN_PROGRESS", phase="spec", scope=["docs/"]),
        task("T-2", "BACKLOG", phase="implement", depends_on=["T-1"]),
    )
    assert report["waiting"][0]["reasons"] == ["T-1 is IN_PROGRESS"]


# --- parallel start ---------------------------------------------------------------


def test_ready_tasks_with_separate_scopes_start_together(tmp_path):
    report = plan(
        tmp_path,
        task("T-1", scope=["src/api/"]),
        task("T-2", scope=["src/web/"]),
        task("T-3", scope=["docs/"]),
    )
    assert ids(report["start"]) == ["T-1", "T-2", "T-3"]
    assert report["held"] == []


def test_ready_task_overlapping_a_running_task_is_held(tmp_path):
    report = plan(
        tmp_path,
        task("T-1", "IN_PROGRESS", scope=["src/api/"]),
        task("T-2", scope=["src/api/login.py"]),
        task("T-3", scope=["src/web/"]),
    )
    assert ids(report["start"]) == ["T-3"]
    assert report["held"] == [
        {
            "id": "T-2",
            "title": "Task T-2",
            "owner": "developer",
            "status": "READY",
            "phase": None,
            "scope": ["src/api/login.py"],
            "conflicts_with": ["T-1"],
            "reason": "scope overlaps a task that is running or starting",
        }
    ]


def test_task_in_review_still_occupies_its_scope(tmp_path):
    report = plan(tmp_path, task("T-1", "IN_REVIEW", scope=["src/"]), task("T-2", scope=["src/a"]))
    assert ids(report["held"]) == ["T-2"]


def test_ready_tasks_overlapping_each_other_start_one_at_a_time(tmp_path):
    report = plan(
        tmp_path,
        task("T-1", scope=["src/api/"]),
        task("T-2", scope=["src/api/v1/"]),
        task("T-3", scope=["src/"]),
    )
    assert ids(report["start"]) == ["T-1"]
    assert [(entry["id"], entry["conflicts_with"]) for entry in report["held"]] == [
        ("T-2", ["T-1"]),
        ("T-3", ["T-1"]),
    ]


def test_held_task_does_not_occupy_its_scope(tmp_path):
    # T-2 is held by T-1, so it must not in turn hold T-3 back.
    report = plan(
        tmp_path,
        task("T-1", scope=["src/api/"]),
        task("T-2", scope=["src/api/", "docs/"]),
        task("T-3", scope=["docs/"]),
    )
    assert ids(report["start"]) == ["T-1", "T-3"]
    assert ids(report["held"]) == ["T-2"]


def test_task_without_scope_starts_alone(tmp_path):
    report = plan(tmp_path, task("T-1"), task("T-2", scope=["docs/"]))
    assert ids(report["start"]) == ["T-1"]
    assert ids(report["held"]) == ["T-2"]


def test_task_without_scope_is_held_while_anything_runs(tmp_path):
    report = plan(tmp_path, task("T-1", "IN_PROGRESS", scope=["docs/"]), task("T-2"))
    assert report["start"] == []
    assert report["held"][0]["conflicts_with"] == ["T-1"]
    assert report["held"][0]["reason"] == "no scope declared, so it cannot run beside another task"


def test_backlog_tasks_do_not_occupy_scope(tmp_path):
    report = plan(tmp_path, task("T-1", "BACKLOG", scope=["src/"]), task("T-2", scope=["src/"]))
    assert ids(report["start"]) == ["T-2"]


# --- ordering and purity ----------------------------------------------------------


def test_tasks_are_ordered_by_number_not_by_text(tmp_path):
    report = plan(
        tmp_path,
        task("T-10", scope=["a/"]),
        task("T-2", scope=["b/"]),
        task("T-1", scope=["c/"]),
        task("API-3", scope=["d/"]),
    )
    assert ids(report["start"]) == ["API-3", "T-1", "T-2", "T-10"]


def test_lower_numbered_task_wins_a_scope_conflict(tmp_path):
    report = plan(tmp_path, task("T-10", scope=["src/"]), task("T-2", scope=["src/"]))
    assert ids(report["start"]) == ["T-2"]
    assert ids(report["held"]) == ["T-10"]


def test_next_is_repeatable_and_writes_nothing(tmp_path):
    write_config(tmp_path, workflow={"phases": PHASES})
    write(
        tmp_path,
        decision(),
        task("T-1", "DONE", phase="spec"),
        task("T-2", phase="implement", depends_on=["T-1"], scope=["src/"]),
        task("T-3", "BACKLOG", depends_on=["DEC-1"]),
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    first = runner.invoke(app, ["next", str(tmp_path), "--json"]).output
    second = runner.invoke(app, ["next", str(tmp_path), "--json"]).output
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert first == second
    assert before == after


def test_running_task_without_scope_holds_every_ready_task(tmp_path):
    report = plan(tmp_path, task("T-1", "IN_PROGRESS"), task("T-2", scope=["docs/"]))
    assert report["start"] == []
    assert report["held"][0]["conflicts_with"] == ["T-1"]


def test_blocked_task_also_lists_a_missing_phase(tmp_path):
    write_config(tmp_path, workflow={"phases": PHASES})
    report = plan(tmp_path, task("T-1", "BLOCKED", phase="implement"))
    assert report["waiting"][0]["reasons"] == [
        "BLOCKED: Waiting for the designer",
        "no 'spec' task is reachable through depends_on",
    ]
