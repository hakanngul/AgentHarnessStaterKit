"""Bounded local reads and conservative path checks for managed destinations."""

import os
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 1_048_576


class HarnessError(ValueError):
    """An actionable user-facing setup error."""


def project_root(path: Path, *, required: bool = True) -> Path:
    root = Path(os.path.abspath(path.expanduser()))
    for item in (*reversed(root.parents), root):
        if item.is_symlink():
            raise HarnessError(f"Symlinked project path is not supported: {item}")
        if item.exists() and not item.is_dir():
            raise HarnessError(f"Project path is not a directory: {item}")
    if required and not root.is_dir():
        raise HarnessError(f"Project directory does not exist: {root}")
    return root


def managed_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or relative != PurePosixPath(relative).as_posix()
        or PurePosixPath(relative).is_absolute()
        or ".." in parts
        or "\\" in relative
        or ":" in relative
    ):
        raise HarnessError(f"Unsafe managed path: {relative}")
    target = root
    for part in parts:
        target /= part
        if target.is_symlink():
            raise HarnessError(f"Symlinked managed path is not supported: {relative}")
        if target != root / relative and target.exists() and not target.is_dir():
            raise HarnessError(f"Parent is not a directory: {target}")
    return target


def read_text(path: Path) -> str:
    if not path.is_file():
        raise HarnessError(f"Required file is missing or is not a regular file: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise HarnessError(f"File exceeds {MAX_FILE_BYTES} byte limit: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.read()
