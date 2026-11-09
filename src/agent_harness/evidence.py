"""Record task evidence by running a command, instead of trusting a typed claim.

The command is the argv the caller passes explicitly; it runs without a shell. Commands
found in project metadata are never executed. The result is still produced on the
caller's machine: the stored hash detects later edits to the output, not fabrication.
"""

import datetime as dt
import os
import re
import shlex
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from agent_harness.check import TASKS_DIR
from agent_harness.filesystem import (
    MAX_FILE_BYTES,
    HarnessError,
    atomic_write,
    load_yaml,
    managed_path,
    project_root,
    read_text,
    sha256_file,
)
from agent_harness.models import Evidence, Task

EVIDENCE_DIR = ".harness/evidence"
TASK_ID = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
EVIDENCE_ID = re.compile(r"^E[1-9][0-9]*$")
TIMEOUT_EXIT_CODE = 124
MAX_SUMMARY = 200
LOCK_WAIT_SECONDS = 30.0


def _load_task(path: Path) -> Task:
    try:
        return Task.model_validate(load_yaml(read_text(path)))
    except ValidationError as exc:
        raise HarnessError(f"Task file is invalid; run `harness check`: {exc}") from exc
    except (ValueError, RecursionError, yaml.YAMLError) as exc:
        raise HarnessError(f"Task file is invalid; run `harness check`: {exc}") from exc


def _commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _bounded(output: bytes) -> bytes:
    if len(output) <= MAX_FILE_BYTES:
        return output
    keep = MAX_FILE_BYTES - 200
    header = f"[harness: output truncated to the last {keep} bytes]\n".encode()
    return header + output[-keep:]


def _summary(output: bytes) -> str:
    """Last line with a letter or digit, so JSON output does not summarize as `}`."""
    lines = [line.strip() for line in output.decode("utf-8", "replace").splitlines()]
    last = next((line for line in reversed(lines) if any(c.isalnum() for c in line)), "")
    return last[:MAX_SUMMARY]


def _write_atomic(path: Path, text: str) -> None:
    atomic_write(path, text)


def _reserve(root: Path, task_id: str, task: Task, wanted: str | None) -> tuple[str, Path]:
    """Claim an evidence id by creating its output file exclusively."""
    taken = {item.id for item in task.evidence}
    if wanted is not None:
        candidates = [wanted]
        if wanted in taken:
            raise HarnessError(f"{task_id} already has evidence {wanted}")
    else:
        start = max((int(i[1:]) for i in taken if EVIDENCE_ID.fullmatch(i)), default=0) + 1
        candidates = [f"E{n}" for n in range(start, start + 1000) if f"E{n}" not in taken]
    for candidate in candidates:
        relative = f"{EVIDENCE_DIR}/{task_id}/{candidate}.txt"
        path = managed_path(root, relative)
        try:
            with path.open("xb"):
                return candidate, path
        except FileExistsError:
            if wanted is not None:
                raise HarnessError(
                    f"Refusing to overwrite existing evidence output: {relative}"
                ) from None
    raise HarnessError(f"No free evidence id for {task_id}")


@contextmanager
def _task_lock(task_path: Path):
    lock = task_path.with_name(f".{task_path.name}.lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise HarnessError(
                    f"{lock.name} is held by another writer; remove it if no "
                    "`harness evidence run` is active"
                ) from None
            time.sleep(0.05)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def run_evidence(
    path: Path,
    task_id: str,
    argv: list[str],
    *,
    evidence_id: str | None = None,
    cwd: str = ".",
    timeout: float | None = None,
    summary: str | None = None,
) -> Evidence:
    """Run argv, store its combined output and append harness-recorded evidence."""
    if not argv:
        raise HarnessError("No command given; pass it after `--`")
    if not TASK_ID.fullmatch(task_id) or task_id.startswith("DEC-"):
        raise HarnessError(f"Invalid task id: {task_id!r}")
    if evidence_id is not None and not EVIDENCE_ID.fullmatch(evidence_id):
        raise HarnessError(f"Invalid evidence id: {evidence_id!r}")
    if timeout is not None and timeout <= 0:
        raise HarnessError("Timeout must be positive")

    root = project_root(path)
    task_path = managed_path(root, f"{TASKS_DIR}/{task_id}.yaml")
    task = _load_task(task_path)
    if task.id != task_id:
        raise HarnessError(f"{task_path.name} declares id {task.id!r}")
    cwd = PurePosixPath(cwd).as_posix()
    workdir = root if cwd == "." else managed_path(root, cwd)
    if not workdir.is_dir():
        raise HarnessError(f"Working directory not found: {cwd}")

    output_dir = managed_path(root, f"{EVIDENCE_DIR}/{task_id}")
    created_dirs = [p for p in (output_dir, output_dir.parent) if not p.exists()]
    for directory in reversed(created_dirs):
        directory.mkdir()
    reserved: Path | None = None  # only this file is ours to delete on failure
    try:
        evidence_id, reserved = _reserve(root, task_id, task, evidence_id)
        output_rel = f"{EVIDENCE_DIR}/{task_id}/{evidence_id}.txt"
        try:
            completed = subprocess.run(
                argv,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            exit_code, raw = completed.returncode, completed.stdout or b""
            default_summary = _summary(raw)
        except subprocess.TimeoutExpired as exc:
            exit_code, raw = TIMEOUT_EXIT_CODE, exc.output or b""
            default_summary = f"timed out after {timeout:g}s"
        except OSError as exc:
            raise HarnessError(f"Could not start command {argv[0]!r}: {exc}") from exc
        with reserved.open("wb") as stream:
            stream.write(_bounded(raw))
        evidence = Evidence(
            id=evidence_id,
            kind="command",
            command=shlex.join(argv)[:2000],
            cwd=cwd,
            exit_code=exit_code,
            summary=(summary if summary is not None else default_summary)[:4000],
            commit=_commit(root),
            output=output_rel,
            output_sha256=sha256_file(reserved),
            recorded_at=dt.date.today(),
            recorded_by="harness",
        )
        # The command may have run for minutes: merge into the task as it is now, not
        # the copy read before, so concurrent edits are kept.
        with _task_lock(task_path):
            current = _load_task(task_path)
            if evidence_id in {item.id for item in current.evidence}:
                raise HarnessError(f"{task_id} gained evidence {evidence_id} while running")
            data = current.model_dump(mode="json", exclude_unset=True)
            data["evidence"] = [
                *data.get("evidence", []),
                evidence.model_dump(mode="json", exclude_none=True),
            ]
            text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
            Task.model_validate(yaml.safe_load(text))  # never write an invalid task
            _write_atomic(task_path, text)
    except BaseException:
        if reserved is not None:
            reserved.unlink(missing_ok=True)
        for directory in created_dirs:
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise
    return evidence
