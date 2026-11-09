"""Bounded local reads and conservative path checks for managed destinations."""

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

import yaml

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects repeated mapping keys instead of keeping the last one."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue  # `<<` merge keys are flattened by the base constructor
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:
                continue  # unhashable keys are rejected later by the base constructor
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_yaml(text: str):
    """Safe YAML load that rejects duplicate mapping keys."""
    return yaml.load(text, Loader=UniqueKeyLoader)


def atomic_write(path: Path, text: str) -> None:
    """Replace path with text via a unique temporary file, keeping the file mode."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
