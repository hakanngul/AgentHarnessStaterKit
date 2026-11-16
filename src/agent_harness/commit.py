"""One git commit per finished task, and a code check that no change is out of scope.

The orchestrator commits; the user merges (branch layout A: a parallel group shares one
branch, each task is its own commit). A change that no task's scope or ledger files
account for blocks every commit, because with parallel tasks nobody can say whose it is.
Only the files a task owns are staged, by literal path; nothing is pushed.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agent_harness.check import TASKS_DIR, inspect_ledger
from agent_harness.evidence import EVIDENCE_DIR
from agent_harness.filesystem import HarnessError, project_root
from agent_harness.models import Task
from agent_harness.schedule import _fold, _prefix

GENERATED_DIRS = ("__pycache__",)  # produced by allowed test commands, never a task's work
FALLBACK_NAME = "Agent Harness"
FALLBACK_EMAIL = "harness@localhost"
LEDGER_RANK = 10**6  # a task's own ledger files always belong to it
MAX_LISTED = 8


@dataclass
class CommitResult:
    task_id: str
    status: str  # committed | skipped | blocked
    note: str = ""
    sha: str | None = None
    files: list[str] = field(default_factory=list)


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """git with literal pathspecs: a file named `*.py` or `:(top)x` is just a file name."""
    try:
        done = subprocess.run(
            ["git", "--literal-pathspecs", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise HarnessError(f"git is not available: {exc}") from exc
    if check and done.returncode != 0:
        name = next((arg for arg in args if not arg.startswith("-") and "=" not in arg), args[0])
        raise HarnessError(f"git {name} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done


def _git(root: Path, *args: str) -> str:
    return _run(root, *args).stdout


def _identity(root: Path) -> list[str]:
    """`-c` options for whichever of user.name and user.email is not configured."""
    options = []
    for key, fallback in (("user.name", FALLBACK_NAME), ("user.email", FALLBACK_EMAIL)):
        if not _run(root, "config", key, check=False).stdout.strip():
            options += ["-c", f"{key}={fallback}"]
    return options


def _is_generated(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    ledger_lock = (
        PurePosixPath(path).parent.as_posix() == TASKS_DIR
        and name.startswith(".")
        and name.endswith(".lock")
    )  # .harness/tasks/.T-1.yaml.lock
    return path.endswith(".pyc") or ledger_lock or any(part in GENERATED_DIRS for part in parts)


def changed_files(root: Path) -> list[str]:
    """Modified, deleted and untracked files, generated ones left out.

    Refuses unmerged paths and nested repositories, which `git add` would mishandle.
    """
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
        if "U" in status or status in ("AA", "DD"):
            raise HarnessError(f"unmerged path, resolve it first: {path}")
        if path.endswith("/"):
            raise HarnessError(f"nested repository or directory entry, not a file: {path}")
        if "R" in status or "C" in status:
            index += 1  # the original name follows a rename or copy
        if not _is_generated(path):
            paths.append(path)
    return paths


def _covers(path: str, entry: str) -> int:
    """How many leading segments of scope `entry` match the concrete `path`; -1 if it does not.

    The path is matched literally (it is a real file name); only the scope entry is
    interpreted. A whole-project entry (`.`, `**`, absolute) covers everything with rank 0.
    """
    prefix = _prefix(entry)
    parts = [_fold(part) for part in path.split("/")]
    return len(prefix) if parts[: len(prefix)] == list(prefix) else -1


def _rank(path: str, task: Task) -> int:
    """How specifically `task` claims `path`: its own ledger files, else the deepest scope."""
    if path == f"{TASKS_DIR}/{task.id}.yaml" or path.startswith(f"{EVIDENCE_DIR}/{task.id}/"):
        return LEDGER_RANK
    return max((_covers(path, entry) for entry in task.scope), default=-1)


def owners(path: str, tasks: dict[str, Task]) -> list[str]:
    """Tasks whose scope or ledger files account for `path` (an empty scope owns nothing)."""
    return [
        task.id for task in tasks.values() if task.status != "CANCELLED" and _rank(path, task) >= 0
    ]


def _number(task_id: str) -> int:
    return int(task_id.rpartition("-")[2])


def owner_of(path: str, tasks: dict[str, Task]) -> str | None:
    """The one task that gets `path`: the most specific scope, then the lowest task number.

    A broad scope (`src/`) must not swallow files that a narrower task (`src/api.py`) names.
    """
    candidates = owners(path, tasks)
    if not candidates:
        return None
    return max(candidates, key=lambda task_id: (_rank(path, tasks[task_id]), -_number(task_id)))


def _committed(root: Path, task_id: str) -> bool:
    """True if a commit reachable from HEAD has a subject starting `T-n: `."""
    done = _run(root, "log", "--format=%s", "--fixed-strings", f"--grep={task_id}: ", check=False)
    if done.returncode != 0:  # an unborn branch has no history yet
        return False
    return any(line.startswith(f"{task_id}: ") for line in done.stdout.splitlines())


def _message(task: Task, files: list[str]) -> str:
    lines = [f"{task.id}: {task.title}", ""]
    if task.review.status == "approved":
        lines.append(f"Review: approved by {task.review.by}")
    lines.append(f"Evidence: {', '.join(item.id for item in task.evidence) or 'none'}")
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
    if branch is None and not _git(root, "branch", "--show-current").strip():
        raise HarnessError("HEAD is detached; check out a branch or pass --branch")

    if branch is not None:  # judge "already committed" on the branch the commits will go to
        if (
            branch.startswith("-")
            or _run(root, "check-ref-format", "--branch", branch, check=False).returncode
        ):
            raise HarnessError(f"not a valid branch name: {branch!r}")
        if (
            _run(
                root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
            ).returncode
            == 0
        ):
            _git(root, "switch", branch)

    wanted = sorted(
        (
            task
            for task in tasks.values()
            if task.status == "DONE" and (task_ids is None or task.id in task_ids)
        ),
        key=lambda task: _number(task.id),
    )
    changes = changed_files(root)
    stray = [item for item in changes if not owners(item, tasks)]
    if stray:
        shown = ", ".join(stray[:MAX_LISTED]) + (" ..." if len(stray) > MAX_LISTED else "")
        note = f"changes outside every task's scope, nothing was committed: {shown}"
        return [CommitResult(task.id, "blocked", note) for task in wanted]

    results: list[CommitResult] = []
    pending: list[tuple[Task, list[str]]] = []
    for task in wanted:
        mine = [item for item in changes if owner_of(item, tasks) == task.id]
        if _committed(root, task.id):
            later = f"; {len(mine)} later change(s) left uncommitted" if mine else ""
            results.append(CommitResult(task.id, "skipped", f"already committed{later}"))
        elif not mine:
            results.append(CommitResult(task.id, "skipped", "no changes to commit"))
        else:
            pending.append((task, mine))

    if branch is not None and pending and _git(root, "branch", "--show-current").strip() != branch:
        _git(root, "switch", "-c", branch)
    for position, (task, files) in enumerate(pending):
        try:
            _git(root, "add", "--", *files)
            _run(root, *_identity(root), "commit", "-m", _message(task, files))
            sha = _git(root, "rev-parse", "HEAD").strip()
        except HarnessError as exc:
            reset = _run(root, "reset", "-q", "--", *files, check=False)  # restore the index
            dirty = "" if reset.returncode == 0 else " (the index could not be restored)"
            results.append(CommitResult(task.id, "blocked", f"commit failed: {exc}{dirty}"))
            results += [
                CommitResult(rest.id, "skipped", "not attempted after an earlier failure")
                for rest, _ in pending[position + 1 :]
            ]
            break
        results.append(CommitResult(task.id, "committed", "", sha, files))
    return sorted(results, key=lambda result: _number(result.task_id))
