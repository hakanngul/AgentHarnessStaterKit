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


def test_generated_skill_example_passes_check_with_pending_decision(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    skill = (tmp_path / ".claude/skills/development/SKILL.md").read_text()
    example = yaml.safe_load(skill.split("```yaml\n", 1)[1].split("```", 1)[0])
    write(tmp_path, TASKS_DIR, example)
    write(tmp_path, DECISIONS_DIR, decision())
    code, report = run_check(tmp_path)
    assert code == 0, report
    assert report["summary"]["tasks_blocked"] == 1


def test_in_review_requires_acceptance_and_evidence(tmp_path):
    errors = errors_for(tmp_path, done_task(status="IN_REVIEW", acceptance=[], evidence=[]))
    assert any("IN_REVIEW requires acceptance" in error for error in errors)
    assert any("IN_REVIEW requires recorded evidence" in error for error in errors)


def test_in_review_does_not_require_review_yet(tmp_path):
    assert errors_for(tmp_path, done_task(status="IN_REVIEW", review={})) == []


def test_two_node_cycle(tmp_path):
    write(tmp_path, TASKS_DIR, done_task(status="BACKLOG", depends_on=["T-2"]))
    write(tmp_path, TASKS_DIR, done_task(id="T-2", status="BACKLOG", depends_on=["T-1"]))
    code, report = run_check(tmp_path)
    assert code == 1
    assert any("T-1 -> T-2 -> T-1" in error for error in report["errors"])


def test_long_dependency_chain_does_not_crash(tmp_path):
    count = 1500
    for number in range(1, count + 1):
        depends = [f"T-{number + 1}"] if number < count else ["T-1"]
        write(
            tmp_path, TASKS_DIR, done_task(id=f"T-{number}", status="BACKLOG", depends_on=depends)
        )
    code, report = run_check(tmp_path)
    assert code == 1
    assert report["summary"]["tasks"] == count
    assert sum("dependency cycle" in error for error in report["errors"]) == 1


def test_duplicate_dependencies_are_reported(tmp_path):
    write(tmp_path, DECISIONS_DIR, decision())
    task = done_task(status="BLOCKED", reason="x", depends_on=["DEC-1", "DEC-1"])
    assert any("duplicate depends_on" in error for error in errors_for(tmp_path, task))


def test_active_task_on_withdrawn_decision(tmp_path):
    write(tmp_path, DECISIONS_DIR, decision(status="WITHDRAWN"))
    errors = errors_for(tmp_path, done_task(depends_on=["DEC-1"]))
    assert any("dependency DEC-1 is WITHDRAWN" in error for error in errors)


@pytest.mark.parametrize("reviewer", ["Developer", " DEVELOPER ", "developer"])
def test_self_approval_ignores_case_and_spacing(tmp_path, reviewer):
    task = done_task(review={"status": "approved", "by": reviewer})
    assert any("cannot approve their own" in e for e in errors_for(tmp_path, task))


def test_ledger_path_that_is_a_file(tmp_path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / TASKS_DIR).write_text("not a directory\n")
    code, report = run_check(tmp_path)
    assert code == 1
    assert "is not a directory" in report["errors"][0]


def test_ledger_entry_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_harness.check.MAX_LEDGER_FILES", 2)
    for number in range(1, 4):
        write(tmp_path, TASKS_DIR, done_task(id=f"T-{number}"))
    code, report = run_check(tmp_path)
    assert code == 1
    assert "more than 2 entries" in report["errors"][0]


@pytest.mark.parametrize("output", ["/etc/passwd", "a" * 300, "logs/" + "b" * 300 + "/x.txt"])
def test_hostile_evidence_output_paths_are_reported(tmp_path, output):
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "output": output}]
    errors = errors_for(tmp_path, done_task(evidence=evidence))
    assert any("evidence E1" in error for error in errors), errors


def test_symlinked_evidence_output_is_rejected(tmp_path):
    (tmp_path / "real.txt").write_text("ok\n")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "output": "link.txt"}]
    errors = errors_for(tmp_path, done_task(evidence=evidence))
    assert any("Symlinked managed path" in error for error in errors)


def test_deeply_nested_yaml_is_reported_not_raised(tmp_path):
    target = tmp_path / TASKS_DIR / "T-1.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("[" * 20000)
    code, report = run_check(tmp_path)
    assert code == 1
    assert report["errors"] and report["errors"][0].startswith(f"{TASKS_DIR}/T-1.yaml")


def test_unreadable_ledger_directory_is_reported(tmp_path, monkeypatch):
    write(tmp_path, TASKS_DIR, done_task())

    def deny(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "iterdir", deny)
    code, report = run_check(tmp_path)
    assert code == 1
    assert "cannot be listed" in report["errors"][0]


def test_unquoted_yaml_date_is_accepted(tmp_path):
    text = yaml.safe_dump(done_task()).replace("'2026-11-09'", "2026-11-09")
    assert "recorded_at: 2026-11-09" in text
    target = tmp_path / TASKS_DIR / "T-1.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(text)
    assert run_check(tmp_path)[0] == 0


@pytest.mark.parametrize("value", [0, 1731110400, 1.5, True])
def test_numeric_dates_are_rejected(tmp_path, value):
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "recorded_at": value}]
    errors = errors_for(tmp_path, done_task(evidence=evidence))
    assert any("not a number" in error for error in errors), errors


@pytest.mark.parametrize(
    "line",
    ["title: 2026-02-30", "title: x\nlog: [" + "1" * 5000 + "]"],
)
def test_values_yaml_cannot_build_are_reported(tmp_path, line):
    target = tmp_path / TASKS_DIR / "T-1.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(f"id: T-1\nowner: d\nstatus: BACKLOG\n{line}\n")
    code, report = run_check(tmp_path)
    assert code == 1
    assert report["errors"][0].startswith(f"{TASKS_DIR}/T-1.yaml: ")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"owner": "   ", "review": {"status": "approved", "by": "someone"}}, "owner"),
        ({"acceptance": [{"id": "AC1", "text": "  ", "verified_by": ["E1"]}]}, "acceptance"),
        ({"title": "\t"}, "title"),
    ],
)
def test_whitespace_only_names_and_text_are_rejected(tmp_path, overrides, expected):
    errors = errors_for(tmp_path, done_task(**overrides))
    assert any(f": {expected}" in error and "pattern" in error for error in errors), errors


@pytest.mark.parametrize(
    ("cwd", "expected"),
    [
        ("/etc", "Unsafe managed path"),
        ("../elsewhere", "Unsafe managed path"),
        ("missing-dir", "cwd not found"),
    ],
)
def test_evidence_cwd_must_be_a_project_directory(tmp_path, cwd, expected):
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "cwd": cwd}]
    errors = errors_for(tmp_path, done_task(evidence=evidence))
    assert any(expected in error for error in errors), errors


def test_evidence_cwd_subdirectory_is_accepted(tmp_path):
    (tmp_path / "app").mkdir()
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "cwd": "app"}]
    assert errors_for(tmp_path, done_task(evidence=evidence)) == []


def test_symlinked_evidence_cwd_is_rejected(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "cwd": "link"}]
    errors = errors_for(tmp_path, done_task(evidence=evidence))
    assert any("Symlinked managed path" in error for error in errors)


def test_nul_byte_in_evidence_path_is_reported(tmp_path):
    evidence = [{"id": "E1", "command": "pytest", "exit_code": 0, "output": "a\x00b"}]
    errors = errors_for(tmp_path, done_task(evidence=evidence))
    assert any("evidence E1" in error for error in errors), errors


@pytest.mark.parametrize(
    "text",
    [
        "id: T-1\ntitle: x\nowner: d\nstatus: DONE\nstatus: BACKLOG\n",
        "id: T-1\ntitle: x\nowner: d\nstatus: BACKLOG\n"
        "review: {status: approved, status: pending}\n",
    ],
)
def test_duplicate_yaml_keys_are_rejected(tmp_path, text):
    target = tmp_path / TASKS_DIR / "T-1.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(text)
    code, report = run_check(tmp_path)
    assert code == 1
    assert "duplicate key 'status'" in report["errors"][0]


def test_ledger_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_harness.check.MAX_LEDGER_BYTES", 600)
    for number in range(1, 4):
        write(tmp_path, TASKS_DIR, done_task(id=f"T-{number}"))
    code, report = run_check(tmp_path)
    assert code == 1
    assert any("exceeds 600 bytes" in error for error in report["errors"])


def test_entry_cap_stops_enumeration_early(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_harness.check.MAX_LEDGER_FILES", 2)
    calls = []
    original = Path.iterdir

    def counting(self):
        for entry in original(self):
            calls.append(entry)
            yield entry

    for number in range(1, 10):
        write(tmp_path, TASKS_DIR, done_task(id=f"T-{number}"))
    monkeypatch.setattr(Path, "iterdir", counting)
    code, report = run_check(tmp_path)
    assert code == 1
    assert len(calls) == 3
