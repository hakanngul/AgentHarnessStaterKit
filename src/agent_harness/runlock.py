"""One run at a time per project.

A wave, a review and a commit all write the ledger and the working tree, and a commit uses
git's single index. Two actors (the CLI and the server, or two servers) working on one
project at once can release each other's tasks or mix files into each other's commits. The
lock sits at the entry points (CLI commands and server jobs), not inside the library
functions, so a wave and its review share one lock. `harness evidence run` never takes it:
an agent calls that from inside a running wave.

The lock is `flock` on `.harness/.run.lock`, so the kernel drops it when the process dies and
a crash leaves nothing to clean up. The file itself is harmless and may stay. Windows has no
`fcntl`; there `msvcrt.locking` is used, which is untested here.
"""

import contextlib
import datetime as dt
import errno
import json
import os
from collections.abc import Iterator
from pathlib import Path

from agent_harness.filesystem import HarnessError, managed_path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

LOCK_FILE = ".harness/.run.lock"
HOLDER_MAX = 400  # bytes of holder text read back for the error message


def _try_lock(descriptor: int) -> bool:
    """Take the lock without waiting; False if somebody else holds it."""
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:  # pragma: no cover - Windows
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK):
            return False
        raise HarnessError(f"could not take the project run lock: {exc}") from exc
    return True


def _unlock(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    else:  # pragma: no cover - Windows
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


def _holder(descriptor: int) -> str:
    """Who the lock file says holds the lock; never raises, the text is only a hint."""
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = json.loads(os.read(descriptor, HOLDER_MAX).decode("utf-8", "replace"))
        return f"{str(data['what'])[:40]}, pid {int(data['pid'])}, since {str(data['since'])[:25]}"
    except (OSError, ValueError, KeyError, TypeError):
        return "holder unknown"


class RunLock:
    """A held lock. `release` is safe to call twice."""

    def __init__(self, descriptor: int):
        self._descriptor: int | None = descriptor

    @property
    def held(self) -> bool:
        return self._descriptor is not None

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            _unlock(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def acquire(root: Path, what: str) -> RunLock:
    """Take the project run lock for `what` ('wave', 'server review', ...) or raise.

    Never waits: a run that cannot start now says who holds the project and stops.
    """
    path = managed_path(root, LOCK_FILE)
    if not path.parent.is_dir():
        raise HarnessError(f"{path.parent} does not exist; is this a harness project?")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise HarnessError(f"could not open the project run lock: {exc}") from exc
    try:
        if not _try_lock(descriptor):
            raise HarnessError(
                f"another harness run is active on this project ({_holder(descriptor)}); "
                "wait for it to finish or stop it"
            )
        note = {"what": what, "pid": os.getpid(), "since": dt.datetime.now(dt.UTC).isoformat()}
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, json.dumps(note).encode())
    except BaseException:
        os.close(descriptor)  # closing also drops a lock taken a moment ago
        raise
    return RunLock(descriptor)


@contextlib.contextmanager
def run_lock(root: Path, what: str) -> Iterator[RunLock]:
    lock = acquire(root, what)
    try:
        yield lock
    finally:
        lock.release()
