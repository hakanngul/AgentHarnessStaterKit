import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_engine_runner import project as base_project
from test_engine_runner import task
from typer.testing import CliRunner

from agent_harness import cli
from agent_harness.commit import changed_files, commit_done_tasks, owners
from agent_harness.filesystem import HarnessError
from agent_harness.models import Task

SHA = hashlib.sha256(b"ok\n").hexdigest()


def done(task_id: str, scope: list[str], **overrides) -> dict:
    evidence = [
        {
            "id": "E1",
            "command": "pytest",
            "exit_code": 0,
            "recorded_by": "harness",
            "output": f".harness/evidence/{task_id}/E1.txt",
            "output_sha256": SHA,
        }
    ]
    return task(
        task_id,
        status="DONE",
        scope=scope,
        evidence=evidence,
        acceptance=[{"id": "AC1", "text": "works", "verified_by": ["E1"]}],
        review={"status": "approved", "by": "reviewer"},
        **overrides,
    )


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def plain(tmp_path: Path, *tasks: dict) -> Path:
    """A valid project (evidence files included) that is not a git repository yet."""
    root = base_project(tmp_path, *tasks)
    for item in tasks:
        for evidence in item.get("evidence", []):
            out = root / evidence["output"]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"ok\n")
    return root


def untracked(root: Path) -> list[str]:
    return git(root, "status", "--porcelain", "--untracked-files=all").split()


def repo(tmp_path: Path, *tasks: dict) -> Path:
    """A git repository whose first commit holds the config and the task files."""
    root = plain(tmp_path, *tasks)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "baseline")
    return root


def write(root: Path, relative: str, text: str = "x\n") -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def subjects(root: Path) -> list[str]:
    return git(root, "log", "--format=%s").splitlines()


def files_of(root: Path, subject_prefix: str) -> list[str]:
    sha = git(root, "log", "-1", "--format=%H", f"--grep=^{subject_prefix}").strip()
    return git(root, "show", "--name-only", "--format=", sha).split()


def test_each_done_task_gets_its_own_commit_with_only_its_files(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]), done("T-2", ["src/b/"]))
    write(root, "src/a/one.py")
    write(root, "src/b/two.py")
    results = commit_done_tasks(root)
    assert [(r.task_id, r.status) for r in results] == [("T-1", "committed"), ("T-2", "committed")]
    assert subjects(root)[:2] == ["T-2: Login form", "T-1: Login form"]
    assert files_of(root, "T-1: ") == ["src/a/one.py"]
    assert files_of(root, "T-2: ") == ["src/b/two.py"]
    assert git(root, "status", "--porcelain").strip() == ""


def test_the_commit_message_names_review_and_evidence(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    commit_done_tasks(root)
    body = git(root, "log", "-1", "--format=%B")
    assert "Review: approved by reviewer" in body and "Evidence: E1" in body


def test_the_ledger_and_evidence_files_of_a_task_belong_to_its_commit(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, ".harness/evidence/T-1/E2.txt")
    path = root / ".harness/tasks/T-1.yaml"
    data = yaml.safe_load(path.read_text())
    data["log"] = ["2026-11-16 note"]
    path.write_text(yaml.safe_dump(data))
    commit_done_tasks(root)
    assert sorted(files_of(root, "T-1: ")) == [
        ".harness/evidence/T-1/E2.txt",
        ".harness/tasks/T-1.yaml",
        "src/a/one.py",
    ]


def test_a_change_no_task_accounts_for_blocks_every_commit_and_names_the_file(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]), done("T-2", ["src/b/"]))
    write(root, "src/a/one.py")
    write(root, "src/b/two.py")
    write(root, "elsewhere/stray.py")
    results = commit_done_tasks(root)
    assert {r.status for r in results} == {"blocked"}
    assert "elsewhere/stray.py" in results[0].note
    assert subjects(root) == ["baseline"]


def test_a_file_in_a_running_tasks_scope_is_not_stray_and_is_left_alone(tmp_path):
    running = task("T-2", status="IN_PROGRESS", scope=["src/b/"])
    root = repo(tmp_path, done("T-1", ["src/a/"]), running)
    write(root, "src/a/one.py")
    write(root, "src/b/wip.py")
    results = commit_done_tasks(root)
    assert [(r.task_id, r.status) for r in results] == [("T-1", "committed")]
    assert untracked(root) == ["??", "src/b/wip.py"]


def test_only_done_tasks_are_committed(tmp_path):
    root = repo(tmp_path, task("T-1", scope=["src/a/"]))
    write(root, "src/a/one.py")
    assert commit_done_tasks(root) == []
    assert subjects(root) == ["baseline"]


def test_generated_files_are_ignored(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, "src/a/__pycache__/one.cpython-313.pyc", "bin")
    write(root, "unrelated/__pycache__/x.pyc", "bin")
    write(root, ".harness/tasks/.T-1.yaml.lock", "")
    (result,) = commit_done_tasks(root)
    assert result.status == "committed" and result.files == ["src/a/one.py"]


def test_running_it_twice_commits_once(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    commit_done_tasks(root)
    write(root, "src/a/two.py")
    (second,) = commit_done_tasks(root)
    assert (second.status, second.note) == ("skipped", "already committed")
    assert untracked(root) == ["??", "src/a/two.py"]


def test_a_done_task_with_no_changes_is_skipped(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    (result,) = commit_done_tasks(root)
    assert (result.status, result.note) == ("skipped", "no changes to commit")


def test_staged_changes_are_refused_not_swept_into_a_task_commit(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, "mine.txt")
    git(root, "add", "mine.txt")
    with pytest.raises(HarnessError, match="staged changes"):
        commit_done_tasks(root)
    assert subjects(root) == ["baseline"]


def test_a_directory_that_is_not_a_git_repository_is_refused(tmp_path):
    root = plain(tmp_path, done("T-1", ["src/a/"]))
    with pytest.raises(HarnessError, match="git"):
        commit_done_tasks(root)


def test_the_project_must_be_the_repository_root(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    inner = plain(outer / "app", done("T-1", ["src/a/"]))
    with pytest.raises(HarnessError, match="repository root"):
        commit_done_tasks(inner)


def test_the_branch_option_creates_the_branch_and_commits_there(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    start = git(root, "branch", "--show-current").strip()
    write(root, "src/a/one.py")
    commit_done_tasks(root, branch="harness/group-1")
    assert git(root, "branch", "--show-current").strip() == "harness/group-1"
    assert git(root, "log", "-1", "--format=%s", start).strip() == "baseline"
    write(root, "src/a/more.py")
    commit_done_tasks(root, branch="harness/group-1")  # an existing branch is reused


def test_unknown_and_unrequested_tasks(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]), done("T-2", ["src/b/"]))
    write(root, "src/a/one.py")
    write(root, "src/b/two.py")
    with pytest.raises(HarnessError, match="unknown task: T-9"):
        commit_done_tasks(root, task_ids=["T-9"])
    results = commit_done_tasks(root, task_ids=["T-2"])
    assert [(r.task_id, r.status) for r in results] == [("T-2", "committed")]
    assert untracked(root) == ["??", "src/a/one.py"]


def test_a_broken_ledger_commits_nothing(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"], depends_on=["T-9"]))
    write(root, "src/a/one.py")
    with pytest.raises(HarnessError, match="ledger has errors"):
        commit_done_tasks(root)


def test_changed_files_handles_spaces_renames_and_deletions(tmp_path):
    root = repo(tmp_path, task("T-1", scope=["src/"]))
    write(root, "src/old name.py")
    write(root, "src/gone.py")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "files")
    git(root, "mv", "src/old name.py", "src/new name.py")
    (root / "src/gone.py").unlink()
    write(root, "src/fresh file.py")
    assert sorted(changed_files(root)) == ["src/fresh file.py", "src/gone.py", "src/new name.py"]


def test_a_task_without_a_scope_owns_nothing_but_its_ledger_files():
    model = Task.model_validate(task("T-1", scope=[]))
    assert owners("src/a.py", {"T-1": model}) == []
    assert owners(".harness/tasks/T-1.yaml", {"T-1": model}) == ["T-1"]
    assert owners(".harness/evidence/T-1/E1.txt", {"T-1": model}) == ["T-1"]


def test_a_file_is_owned_by_the_tasks_whose_scope_covers_it():
    one = Task.model_validate(task("T-1", scope=["src/a/"]))
    two = Task.model_validate(task("T-2", scope=["src/a/x.py", "docs/"]))
    tasks = {"T-1": one, "T-2": two}
    assert owners("src/a/x.py", tasks) == ["T-1", "T-2"]
    assert owners("src/a/y.py", tasks) == ["T-1"]
    assert owners("docs/readme.md", tasks) == ["T-2"]
    assert owners("src/b/z.py", tasks) == []


def test_commit_command_reports_and_exits_zero(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    out = CliRunner().invoke(cli.app, ["commit", str(root), "--json"])
    report = json.loads(out.output)
    assert out.exit_code == 0 and report["ok"] is True
    assert report["tasks"][0]["status"] == "committed" and len(report["tasks"][0]["sha"]) == 40


def test_commit_command_exits_one_when_blocked(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, "stray.txt")
    out = CliRunner().invoke(cli.app, ["commit", str(root), "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["tasks"][0]["status"] == "blocked"


def test_commit_command_reports_errors_as_json(tmp_path):
    root = plain(tmp_path, done("T-1", ["src/a/"]))
    out = CliRunner().invoke(cli.app, ["commit", str(root), "--json"])
    assert out.exit_code == 1 and "git" in out.output


def test_a_broad_scope_does_not_swallow_the_files_a_narrower_task_names(tmp_path):
    # The shape a real run produced: T-1 owns src/ and tests/, later tasks name single files.
    root = repo(
        tmp_path,
        done("T-1", ["src/", "tests/"]),
        done("T-2", ["src/farewell.py", "tests/test_farewell.py"]),
        done("T-3", ["src/shout.py"]),
    )
    for name in ("src/greet.py", "src/farewell.py", "src/shout.py", "tests/test_farewell.py"):
        write(root, name)
    results = commit_done_tasks(root)
    assert [(r.task_id, r.status) for r in results] == [
        (t, "committed") for t in ("T-1", "T-2", "T-3")
    ]
    assert files_of(root, "T-1: ") == ["src/greet.py"]  # not farewell, shout or the other test
    assert "src/farewell.py" in files_of(root, "T-2: ")
    assert "tests/test_farewell.py" in files_of(root, "T-2: ")
    assert "src/shout.py" in files_of(root, "T-3: ")


def test_a_file_a_running_task_names_more_specifically_is_left_for_that_task(tmp_path):
    running = task("T-2", status="IN_PROGRESS", scope=["src/wip.py"])
    root = repo(tmp_path, done("T-1", ["src/"]), running)
    write(root, "src/finished.py")
    write(root, "src/wip.py")
    commit_done_tasks(root)
    assert "src/finished.py" in files_of(root, "T-1: ")
    assert untracked(root) == ["??", "src/wip.py"]


def test_equal_scopes_go_to_the_lower_task_number():
    one = Task.model_validate(task("T-1", scope=["src/a/"]))
    ten = Task.model_validate(task("T-10", scope=["src/a/"]))
    two = Task.model_validate(task("T-2", scope=["src/a/"]))
    from agent_harness.commit import owner_of

    assert owner_of("src/a/x.py", {"T-10": ten, "T-2": two, "T-1": one}) == "T-1"
    assert owner_of("src/a/x.py", {"T-10": ten, "T-2": two}) == "T-2"
    assert owner_of("src/zzz.py", {"T-1": one}) is None
