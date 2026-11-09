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
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from agent_harness.check import TASKS_DIR, _UniqueKeyLoader
from agent_harness.filesystem import (
    MAX_FILE_BYTES,
    HarnessError,
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


def _load_task(path: Path) -> Task:
    try:
        return Task.model_validate(yaml.load(read_text(path), Loader=_UniqueKeyLoader))
    except ValidationError as exc:
        raise HarnessError(f"Task file is invalid; run `harness check`: {exc}") from exc
    except (ValueError, yaml.YAMLError) as exc:
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
    lines = [line.strip() for line in output.decode("utf-8", "replace").splitlines()]
    last = next((line for line in reversed(lines) if line), "")
    return last[:MAX_SUMMARY]


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
    existing = {item.id for item in task.evidence}
    if evidence_id is None:
        numbers = [int(item[1:]) for item in existing if EVIDENCE_ID.fullmatch(item)]
        evidence_id = f"E{max(numbers, default=0) + 1}"
    elif evidence_id in existing:
        raise HarnessError(f"{task_id} already has evidence {evidence_id}")

    cwd = PurePosixPath(cwd).as_posix()
    workdir = root if cwd == "." else managed_path(root, cwd)
    if not workdir.is_dir():
        raise HarnessError(f"Working directory not found: {cwd}")
    output_rel = f"{EVIDENCE_DIR}/{task_id}/{evidence_id}.txt"
    output_path = managed_path(root, output_rel)
    if output_path.exists():
        raise HarnessError(f"Refusing to overwrite existing evidence output: {output_rel}")

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

    evidence = Evidence(
        id=evidence_id,
        kind="command",
        command=shlex.join(argv)[:2000],
        cwd=cwd,
        exit_code=exit_code,
        summary=(summary if summary is not None else default_summary)[:4000],
        commit=_commit(root),
        output=output_rel,
        output_sha256="0" * 64,  # replaced after the file is written
        recorded_at=dt.date.today(),
        recorded_by="harness",
    )
    data = task.model_dump(mode="json", exclude_unset=True)

    created_dirs = [p for p in (output_path.parent, output_path.parent.parent) if not p.exists()]
    for directory in reversed(created_dirs):
        directory.mkdir()
    try:
        with output_path.open("xb") as stream:
            stream.write(_bounded(raw))
        evidence = evidence.model_copy(update={"output_sha256": sha256_file(output_path)})
        data["evidence"] = [
            *data.get("evidence", []),
            evidence.model_dump(mode="json", exclude_none=True),
        ]
        Task.model_validate(yaml.safe_load(yaml.safe_dump(data)))  # never write an invalid task
        _write_atomic(
            task_path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
        )
    except BaseException:
        output_path.unlink(missing_ok=True)
        for directory in created_dirs:
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise
    return evidence
