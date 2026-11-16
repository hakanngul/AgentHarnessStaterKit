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
    assert second.status == "skipped"
    assert second.note == "already committed; 1 later change(s) left uncommitted"
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
    with pytest.raises(HarnessError, match="git rev-parse failed"):
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
    git(root, "switch", "-q", start)
    write(root, "src/a/more.py")
    results = commit_done_tasks(root, branch="harness/group-1")  # reuses the existing branch
    assert git(root, "branch", "--show-current").strip() == "harness/group-1"
    assert results[0].status == "skipped"  # T-1 was committed on that branch already


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
    assert out.exit_code == 1 and "git rev-parse failed" in out.output


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


# --- findings of the independent review ----------------------------------------------


def test_a_file_named_like_a_glob_is_staged_literally_and_leaks_nothing(tmp_path):
    running = task("T-2", status="IN_PROGRESS", scope=["src/wip.py"])
    root = repo(tmp_path, done("T-1", ["src/"]), running)
    write(root, "src/*.py", "literal")
    write(root, "src/wip.py", "work in progress")
    commit_done_tasks(root)
    assert files_of(root, "T-1: ") == ["src/*.py"]
    assert untracked(root) == ["??", "src/wip.py"]


@pytest.mark.parametrize("name", ["~notes.txt", "a:b.txt", "$HOME.txt", "100%.txt", "-dash.txt"])
def test_odd_root_file_names_are_stray_not_owned_by_everybody(tmp_path, name):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, name)
    (result,) = commit_done_tasks(root)
    assert result.status == "blocked" and name in result.note


def test_a_file_name_with_a_wildcard_does_not_widen_ownership(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, "src/other/a*b.py")
    (result,) = commit_done_tasks(root)
    assert result.status == "blocked" and "src/other/a*b.py" in result.note


def test_a_lookalike_prefix_is_not_the_same_directory():
    model = Task.model_validate(task("T-1", scope=["src/a"]))
    assert owners("src/a/x.py", {"T-1": model}) == ["T-1"]
    assert owners("src/ab.py", {"T-1": model}) == []
    assert owners("src/a.py", {"T-1": model}) == []


def test_a_whole_project_scope_owns_everything_but_loses_to_a_narrower_one():
    from agent_harness.commit import owner_of

    whole = Task.model_validate(task("T-1", scope=["."]))
    narrow = Task.model_validate(task("T-2", scope=["src/a.py"]))
    tasks = {"T-1": whole, "T-2": narrow}
    assert owner_of("README.md", tasks) == "T-1" and owner_of("src/a.py", tasks) == "T-2"


def test_a_changed_lock_file_of_the_project_is_not_silently_dropped(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    write(root, "uv.lock", "changed")
    (result,) = commit_done_tasks(root)
    assert result.status == "blocked" and "uv.lock" in result.note


def test_a_lock_file_listed_in_a_scope_is_committed(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/", "uv.lock"]))
    write(root, "src/a/one.py")
    write(root, "uv.lock", "changed")
    (result,) = commit_done_tasks(root)
    assert sorted(result.files) == ["src/a/one.py", "uv.lock"]


def test_a_failing_commit_leaves_the_index_clean_and_reports_what_happened(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]), done("T-2", ["src/b/"]), done("T-3", ["src/c/"]))
    for name in ("src/a/one.py", "src/b/two.py", "src/c/three.py"):
        write(root, name)
    hook = root / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nif git diff --cached --name-only | grep -q 'src/b/'; then exit 1; fi\nexit 0\n"
    )
    hook.chmod(0o755)
    results = commit_done_tasks(root)
    assert [(r.task_id, r.status) for r in results] == [
        ("T-1", "committed"),
        ("T-2", "blocked"),
        ("T-3", "skipped"),
    ]
    assert "commit failed" in results[1].note and "not attempted" in results[2].note
    assert git(root, "diff", "--cached", "--name-only").strip() == ""
    hook.unlink()
    again = commit_done_tasks(root)  # nothing is stuck: the rest can now be committed
    assert [(r.task_id, r.status) for r in again] == [
        ("T-1", "skipped"),
        ("T-2", "committed"),
        ("T-3", "committed"),
    ]


@pytest.mark.parametrize("name", ["-x", "--force", "bad name", "a..b", ""])
def test_unsafe_branch_names_are_refused_before_anything_changes(tmp_path, name):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    start = git(root, "branch", "--show-current").strip()
    with pytest.raises(HarnessError, match="not a valid branch name"):
        commit_done_tasks(root, branch=name)
    assert git(root, "branch", "--show-current").strip() == start
    assert subjects(root) == ["baseline"]


def test_a_glob_like_branch_name_does_not_match_an_existing_branch(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    git(root, "branch", "foo-1")
    write(root, "src/a/one.py")
    with pytest.raises(HarnessError, match="not a valid branch name"):
        commit_done_tasks(root, branch="foo*")


def test_the_branch_is_not_switched_when_there_is_nothing_to_commit(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    start = git(root, "branch", "--show-current").strip()
    commit_done_tasks(root, branch="harness/idle")
    assert git(root, "branch", "--show-current").strip() == start


def test_task_one_is_not_confused_with_task_ten_or_a_body_line(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]), done("T-10", ["src/b/"]))
    write(root, "src/b/ten.py")
    commit_done_tasks(root, task_ids=["T-10"])
    write(root, "src/a/one.py")
    git(root, "add", "src/a/one.py")
    git(root, "commit", "-qm", "unrelated subject", "-m", "T-1: appears only in the body")
    write(root, "src/a/two.py")
    by_task = {r.task_id: r for r in commit_done_tasks(root)}
    assert by_task["T-1"].status == "committed" and by_task["T-1"].files == ["src/a/two.py"]
    assert by_task["T-10"].status == "skipped"


def test_a_repository_without_commits_can_take_the_first_commit(tmp_path):
    root = plain(tmp_path, done("T-1", ["src/a/"]))
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    write(root, "src/a/one.py")
    (root / ".git/info/exclude").write_text(".harness/config.yaml\n")  # not a task's file
    (result,) = commit_done_tasks(root)
    assert result.status == "committed" and subjects(root) == ["T-1: Login form"]


def test_a_detached_head_is_refused_unless_a_branch_is_given(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    git(root, "checkout", "-q", "--detach")
    write(root, "src/a/one.py")
    with pytest.raises(HarnessError, match="HEAD is detached"):
        commit_done_tasks(root)
    (result,) = commit_done_tasks(root, branch="harness/rescue")
    assert result.status == "committed"


def test_unmerged_paths_and_nested_repositories_are_refused(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    nested = root / "src/a/inner"
    nested.mkdir(parents=True)
    git(nested, "init", "-q")
    write(nested, "f.txt")
    with pytest.raises(HarnessError, match="nested repository"):
        commit_done_tasks(root)


def test_the_commit_identity_falls_back_per_missing_setting(tmp_path, monkeypatch):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    git(root, "config", "--unset", "user.name")
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_COMMITTER_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    write(root, "src/a/one.py")
    commit_done_tasks(root)
    assert git(root, "log", "-1", "--format=%an <%ae>").strip() == "Agent Harness <t@example.com>"


def test_the_commit_identity_falls_back_when_nothing_is_configured(tmp_path, monkeypatch):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    git(root, "config", "--unset", "user.name")
    git(root, "config", "--unset", "user.email")
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_COMMITTER_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    write(root, "src/a/one.py")
    commit_done_tasks(root)
    assert (
        git(root, "log", "-1", "--format=%an <%ae>").strip() == "Agent Harness <harness@localhost>"
    )


def test_the_stray_check_applies_to_a_single_requested_task_too(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]), done("T-2", ["src/b/"]))
    write(root, "src/a/one.py")
    write(root, "stray.txt")
    (result,) = commit_done_tasks(root, task_ids=["T-1"])
    assert result.status == "blocked" and "stray.txt" in result.note


def test_quotes_newlines_and_unicode_in_file_names_survive_status_parsing(tmp_path):
    root = repo(tmp_path, task("T-1", scope=["src/"]))
    names = ['src/say "hi".py', "src/new\nline.py", "src/ünïcode ✓.py"]
    for name in names:
        write(root, name)
    assert sorted(changed_files(root)) == sorted(names)


def test_a_dotted_lock_file_outside_the_ledger_is_a_real_change(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/"]))
    write(root, "src/one.py")
    write(root, "src/.deps.lock", "pinned")
    (result,) = commit_done_tasks(root)
    assert sorted(result.files) == ["src/.deps.lock", "src/one.py"]


def test_a_cancelled_tasks_scope_owns_nothing_so_its_files_are_stray(tmp_path):
    cancelled = task("T-3", status="CANCELLED", reason="dropped", scope=["src/a.py"])
    root = repo(tmp_path, done("T-1", ["src/b/"]), cancelled)
    write(root, "src/b/one.py")
    write(root, "src/a.py")
    (result,) = commit_done_tasks(root)
    assert result.status == "blocked" and "src/a.py" in result.note
    assert subjects(root) == ["baseline"]


def test_a_cancelled_task_does_not_win_a_file_from_a_live_broader_scope(tmp_path):
    cancelled = task("T-3", status="CANCELLED", reason="dropped", scope=["src/a.py"])
    root = repo(tmp_path, done("T-1", ["src/"]), cancelled)
    write(root, "src/a.py")
    (result,) = commit_done_tasks(root)
    assert result.status == "committed" and files_of(root, "T-1: ") == ["src/a.py"]


def test_names_that_look_like_options_or_pathspec_magic_are_committed_literally(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/"]))
    for name in ("src/-dash.txt", "src/:(top)magic.txt", "src/[a].txt"):
        write(root, name)
    (result,) = commit_done_tasks(root)
    assert sorted(result.files) == ["src/-dash.txt", "src/:(top)magic.txt", "src/[a].txt"]
    assert untracked(root) == []


def test_a_name_in_other_letter_case_or_unicode_form_is_still_owned(tmp_path):
    model = Task.model_validate(task("T-1", scope=["src/Café/"]))
    assert owners("src/Cafe\u0301/x.py", {"T-1": model}) == ["T-1"]  # decomposed é
    assert owners("SRC/CAFÉ/x.py", {"T-1": model}) == ["T-1"]


def test_a_failed_commit_names_the_real_git_command(tmp_path):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    hooks = root / ".git/hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    (result,) = commit_done_tasks(root)
    assert result.status == "blocked" and "git commit failed" in result.note
    assert git(root, "diff", "--cached", "--name-only").strip() == ""


def test_a_git_log_failure_is_an_error_not_an_unborn_branch(tmp_path, monkeypatch):
    import agent_harness.commit as commit_module

    root = repo(tmp_path, done("T-1", ["src/a/"]))
    write(root, "src/a/one.py")
    real = commit_module._run

    def broken_log(root_, *args, check=True):
        if args[0] != "log":
            return real(root_, *args, check=check)
        failed = subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
        if check:  # what the real helper does for a failing command
            raise HarnessError("git log failed: fatal: bad object")
        return failed

    monkeypatch.setattr(commit_module, "_run", broken_log)
    with pytest.raises(HarnessError, match="git log failed"):
        commit_done_tasks(root)
    monkeypatch.undo()
    assert subjects(root) == ["baseline"]  # nothing was committed twice or at all


def test_a_cancelled_tasks_own_ledger_file_is_not_stray(tmp_path):
    cancelled = task("T-3", status="CANCELLED", reason="dropped", scope=["src/c/"])
    root = repo(tmp_path, done("T-1", ["src/a/"]), cancelled)
    write(root, "src/a/one.py")
    path = root / ".harness/tasks/T-3.yaml"
    data = yaml.safe_load(path.read_text())
    data["log"] = ["2026-11-16 cancelled later"]
    path.write_text(yaml.safe_dump(data))
    (result,) = commit_done_tasks(root)
    assert result.status == "committed"
    assert untracked(root) == ["M", ".harness/tasks/T-3.yaml"]


def _no_identity(root: Path, monkeypatch, *, keep: str | None = None) -> None:
    for key in ("user.name", "user.email"):
        if key != keep:
            git(root, "config", "--unset", key)
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_COMMITTER_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def test_a_missing_email_alone_falls_back_but_keeps_the_configured_name(tmp_path, monkeypatch):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    _no_identity(root, monkeypatch, keep="user.name")
    write(root, "src/a/one.py")
    commit_done_tasks(root)
    assert git(root, "log", "-1", "--format=%an <%ae>").strip() == "T <harness@localhost>"


def test_a_failed_commit_still_names_git_commit_when_the_identity_options_are_added(
    tmp_path, monkeypatch
):
    root = repo(tmp_path, done("T-1", ["src/a/"]))
    _no_identity(root, monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(root / ".git/hooks"))
    hook = root / ".git/hooks/pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    write(root, "src/a/one.py")
    (result,) = commit_done_tasks(root)
    assert result.status == "blocked" and "git commit failed" in result.note
    assert "git -c failed" not in result.note
