"""One git commit per finished task, and a code check that no change is out of scope.

The orchestrator commits; the user merges (branch layout A: a parallel group shares one
branch, each task is its own commit). A change that no task's scope or ledger files
account for blocks every commit, because with parallel tasks nobody can say whose it is.
Only the files a task owns are staged, by explicit path; nothing is pushed.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agent_harness.check import TASKS_DIR, inspect_ledger
from agent_harness.evidence import EVIDENCE_DIR
from agent_harness.filesystem import HarnessError, project_root
from agent_harness.models import Task
from agent_harness.schedule import _prefix, scopes_overlap

GENERATED = ("__pycache__",)  # produced by allowed test commands, never a task's work
GENERATED_SUFFIXES = (".pyc", ".lock")
FALLBACK_IDENTITY = ("Agent Harness", "harness@localhost")
MAX_LISTED = 8


@dataclass
class CommitResult:
    task_id: str
    status: str  # committed | skipped | blocked
    note: str = ""
    sha: str | None = None
    files: list[str] = field(default_factory=list)


def _git(root: Path, *args: str, identity: bool = False) -> str:
    command = ["git"]
    if identity:
        name = subprocess.run(
            ["git", "config", "user.name"], cwd=root, capture_output=True, text=True
        ).stdout.strip()
        email = subprocess.run(
            ["git", "config", "user.email"], cwd=root, capture_output=True, text=True
        ).stdout.strip()
        if not (name and email):
            command += ["-c", f"user.name={FALLBACK_IDENTITY[0]}"]
            command += ["-c", f"user.email={FALLBACK_IDENTITY[1]}"]
    try:
        done = subprocess.run(
            [*command, *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise HarnessError(f"git is not available: {exc}") from exc
    if done.returncode != 0:
        raise HarnessError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout


def _is_generated(path: str) -> bool:
    return path.endswith(GENERATED_SUFFIXES) or any(
        part in GENERATED for part in PurePosixPath(path).parts
    )


def changed_files(root: Path) -> list[str]:
    """Modified, deleted and untracked paths (files, not directories), generated ones left out."""
    raw = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    entries = raw.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1  # the original name follows a rename or copy
        if not _is_generated(path):
            paths.append(path)
    return paths


def owners(path: str, tasks: dict[str, Task]) -> list[str]:
    """Tasks whose scope or ledger files account for `path` (an empty scope owns nothing)."""
    found = []
    for task in tasks.values():
        own = (
            path == f"{TASKS_DIR}/{task.id}.yaml"
            or path.startswith(f"{EVIDENCE_DIR}/{task.id}/")
            or (task.scope and scopes_overlap([path], task.scope))
        )
        if own:
            found.append(task.id)
    return found


LEDGER_RANK = 10**6  # a task's own ledger files always belong to it


def _rank(path: str, task: Task) -> int:
    """How specifically `task` claims `path`: the deepest matching scope entry wins."""
    if path == f"{TASKS_DIR}/{task.id}.yaml" or path.startswith(f"{EVIDENCE_DIR}/{task.id}/"):
        return LEDGER_RANK
    matching = [len(_prefix(entry)) for entry in task.scope if scopes_overlap([path], [entry])]
    return max(matching, default=-1)


def owner_of(path: str, tasks: dict[str, Task]) -> str | None:
    """The one task that gets `path`: the most specific scope, then the lowest task number.

    A broad scope (`src/`) must not swallow files that a narrower task (`src/api.py`) names.
    """
    candidates = owners(path, tasks)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda task_id: (_rank(path, tasks[task_id]), -int(task_id.rpartition("-")[2])),
    )


def _already_committed(root: Path, task_id: str) -> bool:
    return bool(_git(root, "log", "-1", "--format=%H", f"--grep=^{task_id}: ").strip())


def _message(task: Task, files: list[str]) -> str:
    lines = [f"{task.id}: {task.title}", ""]
    if task.review.status == "approved":
        lines.append(f"Review: approved by {task.review.by}")
    ids = ", ".join(item.id for item in task.evidence)
    lines.append(f"Evidence: {ids or 'none'}")
    lines.append(f"Files: {len(files)}")
    return "\n".join(lines)


def commit_done_tasks(
    path: Path, *, task_ids: list[str] | None = None, branch: str | None = None
) -> list[CommitResult]:
    """Commit each DONE task that has no commit yet, one commit per task."""
    root = project_root(path)
    report, tasks, _, _ = inspect_ledger(root)
    if report.errors:
        raise HarnessError("the ledger has errors; run `harness check`: " + report.errors[0])
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root.resolve():
        raise HarnessError(f"the project must be the git repository root ({top})")
    if _git(root, "diff", "--cached", "--name-only").strip():
        raise HarnessError("the index has staged changes; commit or unstage them first")
    unknown = [task_id for task_id in task_ids or [] if task_id not in tasks]
    if unknown:
        raise HarnessError(f"unknown task: {', '.join(unknown)}")

    wanted = [
        task
        for task in tasks.values()
        if task.status == "DONE" and (task_ids is None or task.id in task_ids)
    ]
    changes = changed_files(root)
    stray = [item for item in changes if not owners(item, tasks)]
    if stray:
        shown = ", ".join(stray[:MAX_LISTED]) + (" ..." if len(stray) > MAX_LISTED else "")
        note = f"changes outside every task's scope, nothing was committed: {shown}"
        return [CommitResult(task.id, "blocked", note) for task in wanted]

    if branch is not None and wanted:
        exists = _git(root, "branch", "--list", branch).strip()
        _git(root, "switch", branch) if exists else _git(root, "switch", "-c", branch)

    results = []
    for task in sorted(wanted, key=lambda item: int(item.id.rpartition("-")[2])):
        if _already_committed(root, task.id):
            results.append(CommitResult(task.id, "skipped", "already committed"))
            continue
        files = [item for item in changes if owner_of(item, tasks) == task.id]
        if not files:
            results.append(CommitResult(task.id, "skipped", "no changes to commit"))
            continue
        _git(root, "add", "--", *files)
        _git(root, "commit", "-m", _message(task, files), identity=True)
        sha = _git(root, "rev-parse", "HEAD").strip()
        changes = [item for item in changes if item not in files]
        results.append(CommitResult(task.id, "committed", "", sha, files))
    return results
