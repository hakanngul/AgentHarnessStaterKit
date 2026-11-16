import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_harness.check import TASKS_DIR
from agent_harness.cli import app
from agent_harness.models import HarnessConfig, WorkflowConfig

runner = CliRunner()

PHASES = [
    {"name": "spec"},
    {"name": "design", "requires": ["spec"]},
    {"name": "implement", "requires": ["spec", "design"]},
    {"name": "fix"},
]


def write_config(root: Path, phases: list | None = PHASES, **workflow) -> None:
    data: dict = {"project_name": "Demo"}
    if phases is not None:
        data["workflow"] = {"phases": phases, **workflow}
    target = root / ".harness/config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data))


def task(task_id: str, phase: str | None = None, status: str = "DONE", **overrides) -> dict:
    data = {
        "id": task_id,
        "title": f"Task {task_id}",
        "owner": "developer",
        "status": status,
        "acceptance": [{"id": "AC1", "text": "Works", "verified_by": ["E1"]}],
        "evidence": [{"id": "E1", "command": "pytest", "exit_code": 0}],
        "review": {"status": "approved", "by": "reviewer"},
    }
    if phase is not None:
        data["phase"] = phase
    if status in {"BLOCKED", "CANCELLED"}:
        data["reason"] = "Waiting"
    data.update(overrides)
    return data


def errors(root: Path, *tasks: dict) -> list[str]:
    for item in tasks:
        target = root / TASKS_DIR / f"{item['id']}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(item, sort_keys=False))
    result = runner.invoke(app, ["check", str(root), "--json"])
    report = json.loads(result.output)
    assert result.exit_code == (1 if report["errors"] else 0)
    return report["errors"]


# --- config model -----------------------------------------------------------------


def test_config_without_workflow_stays_valid():
    assert HarnessConfig.model_validate({"project_name": "Demo"}).workflow is None


def test_workflow_is_parsed_and_reports_requirements():
    workflow = HarnessConfig.model_validate(
        {"project_name": "Demo", "workflow": {"phases": PHASES}}
    ).workflow
    assert workflow.require_phase is False
    assert workflow.requires("implement") == ["spec", "design"]
    assert workflow.requires("spec") == []
    assert workflow.requires("unknown") is None


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        ({"phases": []}, "at least 1 item"),
        ({"phases": [{"name": "spec"}, {"name": "spec"}]}, "duplicate phase name"),
        ({"phases": [{"name": "a", "requires": ["b"]}, {"name": "b"}]}, "not defined before it"),
        ({"phases": [{"name": "a", "requires": ["a"]}]}, "not defined before it"),
        ({"phases": [{"name": "missing-dep", "requires": ["nope"]}]}, "not defined before it"),
        (
            {"phases": [{"name": "a"}, {"name": "b", "requires": ["a", "a"]}]},
            "lists a required phase twice",
        ),
        ({"phases": [{"name": "Spec"}]}, "should match pattern"),
        ({"phases": [{"name": "spec", "gate": "x"}]}, "Extra inputs are not permitted"),
        ({"phases": [{"name": "spec"}], "require_phase": "yes"}, "valid boolean"),
        ({"phases": [{"name": "spec"}], "strict": True}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_workflow_is_rejected(workflow, expected):
    with pytest.raises(ValidationError) as caught:
        WorkflowConfig.model_validate(workflow)
    assert expected in str(caught.value)


def test_invalid_workflow_in_config_is_one_check_error(tmp_path):
    write_config(tmp_path, [{"name": "a", "requires": ["b"]}])
    found = errors(tmp_path, task("T-1", "a"), task("T-2", "a"))
    assert len(found) == 1
    assert found[0].startswith(".harness/config.yaml:")
    assert "not defined before it" in found[0]


def test_init_does_not_write_a_workflow_key(tmp_path):
    # `workflow: null` would collide with a workflow block the user appends.
    assert runner.invoke(app, ["init", str(tmp_path), "--json"]).exit_code == 0
    config = (tmp_path / ".harness/config.yaml").read_text()
    assert "workflow" not in config
    assert "null" not in config


def test_validate_rejects_invalid_workflow(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path), "--json"]).exit_code == 0
    config = tmp_path / ".harness/config.yaml"
    data = yaml.safe_load(config.read_text())
    data["workflow"] = {"phases": [{"name": "spec"}, {"name": "spec"}]}
    config.write_text(yaml.safe_dump(data))
    result = runner.invoke(app, ["validate", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert "duplicate phase name" in result.output


def test_validate_accepts_workflow_on_an_existing_install(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path), "--json"]).exit_code == 0
    config = tmp_path / ".harness/config.yaml"
    data = yaml.safe_load(config.read_text())
    data["workflow"] = {"phases": PHASES, "require_phase": True}
    config.write_text(yaml.safe_dump(data))
    result = runner.invoke(app, ["validate", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output


# --- task phase -------------------------------------------------------------------


def test_tasks_without_phase_are_unaffected_by_a_workflow(tmp_path):
    write_config(tmp_path)
    assert errors(tmp_path, task("T-1")) == []


def test_phase_without_any_config_is_an_error(tmp_path):
    found = errors(tmp_path, task("T-1", "spec"))
    assert found == [
        ".harness/tasks/T-1.yaml: phase 'spec' is set but .harness/config.yaml defines no workflow"
    ]


def test_phase_without_workflow_in_config_is_an_error(tmp_path):
    write_config(tmp_path, phases=None)
    assert "defines no workflow" in errors(tmp_path, task("T-1", "spec", status="BACKLOG"))[0]


def test_unknown_phase_is_an_error_in_any_status(tmp_path):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "release", status="BACKLOG"))
    assert found == [
        ".harness/tasks/T-1.yaml: phase 'release' is not defined in .harness/config.yaml "
        "(spec, design, implement, fix)"
    ]


@pytest.mark.parametrize("phase", ["Spec", "", "a b", 3])
def test_malformed_phase_is_a_schema_error(tmp_path, phase):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", status="BACKLOG", phase=phase))
    assert len(found) == 1
    assert found[0].startswith(".harness/tasks/T-1.yaml: phase:")


# --- phase gate -------------------------------------------------------------------


def test_phase_without_requirements_needs_no_dependency(tmp_path):
    write_config(tmp_path)
    assert errors(tmp_path, task("T-1", "spec"), task("T-2", "fix")) == []


@pytest.mark.parametrize("status", ["READY", "IN_PROGRESS", "IN_REVIEW", "DONE"])
def test_active_task_without_required_phase_is_rejected(tmp_path, status):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "design", status=status))
    assert found == [
        f".harness/tasks/T-1.yaml: {status} in phase 'design' but no 'spec' task is reachable "
        "through depends_on; add the dependency or use BLOCKED or BACKLOG until that phase "
        "is done"
    ]


@pytest.mark.parametrize("status", ["BACKLOG", "BLOCKED", "CANCELLED"])
def test_inactive_task_is_not_gated(tmp_path, status):
    write_config(tmp_path)
    assert errors(tmp_path, task("T-1", "implement", status=status)) == []


def test_each_missing_required_phase_is_reported(tmp_path):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "implement"))
    assert len(found) == 2
    assert "no 'spec' task" in found[0]
    assert "no 'design' task" in found[1]


def test_direct_dependencies_satisfy_the_gate(tmp_path):
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "spec"),
        task("T-2", "design", depends_on=["T-1"]),
        task("T-3", "implement", depends_on=["T-1", "T-2"]),
    )
    assert found == []


def test_required_phase_is_found_transitively(tmp_path):
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "spec"),
        task("T-2", "design", depends_on=["T-1"]),
        task("T-3", "implement", status="IN_PROGRESS", depends_on=["T-2"]),
    )
    assert found == []


def test_required_phase_is_found_when_dependencies_sort_after_the_task(tmp_path):
    # Ids are visited in sorted order; here every dependency is discovered mid-walk.
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "implement", depends_on=["T-2"]),
        task("T-2", "design", depends_on=["T-3"]),
        task("T-3", depends_on=["T-4"]),
        task("T-4", "spec"),
    )
    assert found == []


def test_required_phase_is_found_through_a_task_without_phase(tmp_path):
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "spec"),
        task("T-2", depends_on=["T-1"]),
        task("T-3", "design", depends_on=["T-2"]),
    )
    assert found == []


def test_a_sibling_task_in_the_required_phase_does_not_count(tmp_path):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "spec"), task("T-2", "design"))
    assert len(found) == 1
    assert "T-2.yaml" in found[0]
    assert "no 'spec' task" in found[0]


def test_a_dependent_task_in_the_required_phase_does_not_count(tmp_path):
    # The spec depends on the design, not the other way round.
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "design"), task("T-2", "spec", depends_on=["T-1"]))
    assert len(found) == 1
    assert "T-1.yaml" in found[0]


def test_a_dependency_in_the_same_phase_does_not_satisfy_another_phase(tmp_path):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "design"), task("T-2", "design", depends_on=["T-1"]))
    assert len(found) == 2


def test_unfinished_required_phase_is_rejected_by_the_dependency_rule(tmp_path):
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "spec", status="IN_PROGRESS"),
        task("T-2", "design", status="IN_PROGRESS", depends_on=["T-1"]),
    )
    assert found == [
        ".harness/tasks/T-2.yaml: IN_PROGRESS but dependency T-1 is IN_PROGRESS; "
        "use BLOCKED or BACKLOG until it is satisfied"
    ]


def test_cancelled_required_phase_does_not_let_work_proceed(tmp_path):
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "spec", status="CANCELLED"),
        task("T-2", "design", depends_on=["T-1"]),
    )
    assert len(found) == 1
    assert "dependency T-1 is CANCELLED" in found[0]


def test_diamond_dependencies_are_resolved(tmp_path):
    phases = [
        {"name": "spec"},
        {"name": "design", "requires": ["spec"]},
        {"name": "qa", "requires": ["spec", "design"]},
    ]
    write_config(tmp_path, phases)
    found = errors(
        tmp_path,
        task("T-1", "spec"),
        task("T-2", "design", depends_on=["T-1"]),
        task("T-3", "design", depends_on=["T-1"]),
        task("T-4", "qa", depends_on=["T-2", "T-3"]),
    )
    assert found == []


def test_unknown_and_decision_dependencies_are_ignored_by_the_gate(tmp_path):
    write_config(tmp_path)
    found = errors(tmp_path, task("T-1", "design", depends_on=["T-9", "DEC-1"]))
    assert any("no 'spec' task" in error for error in found)
    assert any("unknown T-9" in error for error in found)


def test_dependency_cycle_does_not_hang_the_gate(tmp_path):
    write_config(tmp_path)
    found = errors(
        tmp_path,
        task("T-1", "design", depends_on=["T-2"]),
        task("T-2", "design", depends_on=["T-1"]),
    )
    assert any(error.startswith("dependency cycle:") for error in found)
    assert sum("no 'spec' task" in error for error in found) == 2


def test_long_dependency_chain_does_not_exhaust_the_stack(tmp_path):
    write_config(tmp_path)
    chain = [task("T-1", "spec")]
    chain += [task(f"T-{n}", depends_on=[f"T-{n - 1}"]) for n in range(2, 1500)]
    chain.append(task("T-1500", "design", depends_on=["T-1499"]))
    assert errors(tmp_path, *chain) == []


# --- require_phase ----------------------------------------------------------------


@pytest.mark.parametrize("status", ["BACKLOG", "READY", "IN_PROGRESS", "BLOCKED", "DONE"])
def test_require_phase_rejects_tasks_without_phase(tmp_path, status):
    write_config(tmp_path, require_phase=True)
    found = errors(tmp_path, task("T-1", status=status))
    assert found == [
        ".harness/tasks/T-1.yaml: .harness/config.yaml requires every task to name a phase"
    ]


def test_require_phase_exempts_cancelled_tasks(tmp_path):
    write_config(tmp_path, require_phase=True)
    assert errors(tmp_path, task("T-1", status="CANCELLED")) == []


def test_require_phase_accepts_the_explicit_short_path(tmp_path):
    write_config(tmp_path, require_phase=True)
    assert errors(tmp_path, task("T-1", "fix")) == []
