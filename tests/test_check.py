import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_harness.check import DECISIONS_DIR, TASKS_DIR
from agent_harness.cli import app
from agent_harness.models import Decision, HarnessConfig, Manifest, Task

runner = CliRunner()
SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


def run_check(root: Path):
    result = runner.invoke(app, ["check", str(root), "--json"])
    return result.exit_code, json.loads(result.output)


def write(root: Path, directory: str, data: dict, name: str | None = None) -> Path:
    target = root / directory / f"{name or data['id']}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return target


def done_task(**overrides) -> dict:
    task = {
        "schema_version": 1,
        "id": "T-1",
        "title": "Login form",
        "owner": "developer",
        "status": "DONE",
        "acceptance": [{"id": "AC1", "text": "Valid user can sign in", "verified_by": ["E1"]}],
        "evidence": [
            {
                "id": "E1",
                "command": "pytest tests/test_login.py",
                "exit_code": 0,
                "summary": "4 passed",
                "commit": "abc1234",
                "recorded_at": "2026-11-09",
            }
        ],
        "review": {"status": "approved", "by": "reviewer"},
    }
    task.update(overrides)
    return task


def decision(**overrides) -> dict:
    data = {
        "id": "DEC-1",
        "question": "Which payment provider?",
        "options": [{"id": "A", "text": "Stripe"}, {"id": "B", "text": "Iyzico"}],
        "recommendation": "A",
        "status": "PENDING",
    }
    data.update(overrides)
    return data


def errors_for(tmp_path: Path, task: dict) -> list[str]:
    write(tmp_path, TASKS_DIR, task)
    code, report = run_check(tmp_path)
    assert code == (1 if report["errors"] else 0)
    return report["errors"]


def test_empty_project_passes_with_warning(tmp_path):
    code, report = run_check(tmp_path)
    assert code == 0
    assert report["ok"] is True
    assert report["warnings"]
    assert report["summary"]["tasks"] == 0


def test_valid_done_task_and_decision(tmp_path):
    write(tmp_path, TASKS_DIR, done_task(depends_on=["DEC-1"]))
    write(
        tmp_path,
        DECISIONS_DIR,
        decision(status="DECIDED", answer="A", decided_by="user", decided_at="2026-11-09"),
    )
    code, report = run_check(tmp_path)
    assert code == 0, report
    assert report["summary"] == {
        "tasks_done": 1,
        "tasks": 1,
        "decisions": 1,
        "decisions_pending": 0,
    }
    assert any("not rerun" in line for line in report["checks"])


def test_missing_project_fails(tmp_path):
    code, report = run_check(tmp_path / "missing")
    assert code == 1
    assert "does not exist" in report["errors"][0]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"acceptance": []}, "requires acceptance criteria"),
        (
            {"evidence": [], "acceptance": [{"id": "AC1", "text": "x"}]},
            "requires recorded evidence",
        ),
        ({"acceptance": [{"id": "AC1", "text": "x"}]}, "AC1 has no verified_by"),
        (
            {"acceptance": [{"id": "AC1", "text": "x", "verified_by": ["E9"]}]},
            "unknown evidence E9",
        ),
        ({"review": {"status": "pending"}}, "not pending"),
        ({"review": {"status": "changes_requested", "by": "r"}}, "not changes_requested"),
        ({"review": {"status": "approved"}}, "must name the reviewer"),
        ({"review": {"status": "approved", "by": "developer"}}, "cannot approve their own"),
        ({"review": {"status": "unavailable"}}, "requires notes"),
    ],
)
def test_done_requires_complete_evidence_and_review(tmp_path, overrides, expected):
    errors = errors_for(tmp_path, done_task(**overrides))
    assert any(expected in error for error in errors), errors


def test_review_unavailable_with_notes_is_allowed(tmp_path):
    task = done_task(review={"status": "unavailable", "notes": "No reviewer agent in session"})
    assert errors_for(tmp_path, task) == []


@pytest.mark.parametrize(
    "evidence",
    [
        {"id": "E1", "command": "pytest", "exit_code": 1},
        {"id": "E1", "kind": "manual", "result": "fail", "summary": "Button misaligned"},
    ],
)
def test_done_rejects_failing_evidence(tmp_path, evidence):
    errors = errors_for(tmp_path, done_task(evidence=[evidence]))
    assert any("failing evidence E1" in error for error in errors), errors


def test_manual_pass_evidence_counts(tmp_path):
    evidence = [{"id": "E1", "kind": "manual", "result": "pass", "summary": "Checked on device"}]
    assert errors_for(tmp_path, done_task(evidence=evidence)) == []


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ({"id": "E1", "command": "pytest"}, "requires command and exit_code"),
        ({"id": "E1", "exit_code": 0}, "requires command and exit_code"),
        ({"id": "E1", "command": "pytest", "exit_code": True}, "valid integer"),
        ({"id": "E1", "command": "x", "exit_code": 0, "result": "pass"}, "not result"),
        ({"id": "E1", "kind": "manual", "result": "pass"}, "requires result and summary"),
        (
            {"id": "E1", "kind": "manual", "result": "pass", "summary": "s", "exit_code": 0},
            "not exit_code",
        ),
        ({"id": "E1", "command": "x", "exit_code": 0, "commit": "HEAD"}, "pattern"),
        ({"id": "E1", "command": "x", "exit_code": 0, "verified": True}, "Extra inputs"),
    ],
)
def test_invalid_evidence_is_rejected(tmp_path, evidence, expected):
    errors = errors_for(tmp_path, done_task(evidence=[evidence]))
    assert errors and any(expected in error for error in errors), errors


def test_evidence_output_must_exist_inside_project(tmp_path):
    base = {"id": "E1", "command": "pytest", "exit_code": 0}
    errors = errors_for(tmp_path, done_task(evidence=[{**base, "output": "logs/E1.txt"}]))
    assert any("output not found" in error for error in errors)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/E1.txt").write_text("4 passed\n")
    assert errors_for(tmp_path, done_task(evidence=[{**base, "output": "logs/E1.txt"}])) == []
    errors = errors_for(tmp_path, done_task(evidence=[{**base, "output": "../outside.txt"}]))
    assert any("Unsafe managed path" in error for error in errors)


@pytest.mark.parametrize("status", ["READY", "IN_PROGRESS", "IN_REVIEW", "DONE"])
def test_active_task_cannot_proceed_on_pending_decision(tmp_path, status):
    write(tmp_path, DECISIONS_DIR, decision())
    errors = errors_for(tmp_path, done_task(status=status, depends_on=["DEC-1"]))
    assert any("dependency DEC-1 is PENDING" in error for error in errors), errors


def test_blocked_task_on_pending_decision_is_valid(tmp_path):
    write(tmp_path, DECISIONS_DIR, decision())
    task = done_task(status="BLOCKED", depends_on=["DEC-1"], reason="Waiting for DEC-1")
    write(tmp_path, TASKS_DIR, task)
    code, report = run_check(tmp_path)
    assert code == 0, report
    assert report["summary"]["decisions_pending"] == 1


@pytest.mark.parametrize("status", ["BLOCKED", "CANCELLED"])
def test_blocked_and_cancelled_need_reason(tmp_path, status):
    errors = errors_for(tmp_path, done_task(status=status))
    assert any(f"{status} requires a reason" in error for error in errors)


def test_dependency_on_unfinished_task(tmp_path):
    write(tmp_path, TASKS_DIR, done_task(id="T-2", status="IN_PROGRESS", review={}))
    errors = errors_for(tmp_path, done_task(depends_on=["T-2"]))
    assert any("dependency T-2 is IN_PROGRESS" in error for error in errors)


@pytest.mark.parametrize("ref", ["T-9", "DEC-9"])
def test_unknown_dependency(tmp_path, ref):
    errors = errors_for(tmp_path, done_task(status="BACKLOG", depends_on=[ref]))
    assert any(f"unknown {ref}" in error for error in errors)


def test_self_dependency_and_cycles(tmp_path):
    errors = errors_for(tmp_path, done_task(status="BACKLOG", depends_on=["T-1"]))
    assert any("depend on itself" in error for error in errors)
    write(tmp_path, TASKS_DIR, done_task(status="BACKLOG", depends_on=["T-2"]))
    write(tmp_path, TASKS_DIR, done_task(id="T-2", status="BACKLOG", depends_on=["T-3"]))
    write(tmp_path, TASKS_DIR, done_task(id="T-3", status="BACKLOG", depends_on=["T-1"]))
    code, report = run_check(tmp_path)
    assert code == 1
    assert any("T-1 -> T-2 -> T-3 -> T-1" in error for error in report["errors"])


def test_duplicate_criterion_and_evidence_ids(tmp_path):
    task = done_task()
    task["acceptance"] = task["acceptance"] * 2
    task["evidence"] = task["evidence"] * 2
    errors = errors_for(tmp_path, task)
    assert any("duplicate acceptance" in error for error in errors)
    assert any("duplicate evidence" in error for error in errors)


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("[broken", "while parsing"),
        ("null", "valid dictionary"),
        ("!!python/object:os.system {}", "could not determine a constructor"),
        (yaml.safe_dump(done_task(id="DEC-2")), "reserved for decisions"),
        (yaml.safe_dump(done_task(status="FINISHED")), "Input should be"),
        (yaml.safe_dump(done_task(schema_version=2)), "less than or equal to 1"),
        (yaml.safe_dump(done_task(schema_version=True)), "valid integer"),
        (yaml.safe_dump(done_task(owner="")), "at least 1 character"),
    ],
)
def test_malformed_task_files(tmp_path, contents, expected):
    target = tmp_path / TASKS_DIR / "T-1.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(contents)
    code, report = run_check(tmp_path)
    assert code == 1
    assert any(expected in error for error in report["errors"]), report["errors"]


def test_file_name_must_match_id(tmp_path):
    write(tmp_path, TASKS_DIR, done_task(), name="T-7")
    code, report = run_check(tmp_path)
    assert code == 1
    assert "must match id" in report["errors"][0]


def test_non_yaml_entries_warn_and_symlinks_fail(tmp_path):
    write(tmp_path, TASKS_DIR, done_task())
    (tmp_path / TASKS_DIR / "notes.md").write_text("draft\n")
    code, report = run_check(tmp_path)
    assert code == 0
    assert any("notes.md: ignored" in warning for warning in report["warnings"])
    (tmp_path / TASKS_DIR / "T-2.yaml").symlink_to(tmp_path / TASKS_DIR / "T-1.yaml")
    code, report = run_check(tmp_path)
    assert code == 1
    assert any("symlinks are not supported" in error for error in report["errors"])


def test_symlinked_ledger_directory_is_rejected(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / ".harness").mkdir()
    (tmp_path / TASKS_DIR).symlink_to(tmp_path / "elsewhere")
    code, report = run_check(tmp_path)
    assert code == 1
    assert "Symlinked managed path" in report["errors"][0]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"status": "DECIDED"}, "requires answer"),
        ({"answer": "A"}, "must not record an answer"),
        ({"options": [{"id": "A", "text": "only"}]}, "at least 2"),
        ({"options": [{"id": "A", "text": "x"}, {"id": "A", "text": "y"}]}, "unique"),
        ({"id": "D-1"}, "pattern"),
    ],
)
def test_invalid_decisions(tmp_path, overrides, expected):
    data = decision(**overrides)
    write(tmp_path, DECISIONS_DIR, data, name=data["id"])
    code, report = run_check(tmp_path)
    assert code == 1
    assert any(expected in error for error in report["errors"]), report["errors"]


def test_human_output_and_exit_code(tmp_path):
    write(tmp_path, TASKS_DIR, done_task(review={"status": "pending"}))
    result = runner.invoke(app, ["check", str(tmp_path)])
    assert result.exit_code == 1
    assert "errors:" in result.output


@pytest.mark.parametrize(
    ("model", "name"),
    [
        (HarnessConfig, "harness-config"),
        (Manifest, "manifest"),
        (Task, "task"),
        (Decision, "decision"),
    ],
)
def test_published_json_schemas_match_models(model, name):
    published = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    assert published == model.model_json_schema()


def test_generated_skill_example_matches_task_schema(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    skill = (tmp_path / ".claude/skills/development/SKILL.md").read_text()
    example = skill.split("```yaml\n", 1)[1].split("```", 1)[0]
    task = Task.model_validate(yaml.safe_load(example))
    assert task.depends_on == ["DEC-1"]
    code, report = run_check(tmp_path)
    assert code == 0 and report["summary"]["tasks"] == 0
