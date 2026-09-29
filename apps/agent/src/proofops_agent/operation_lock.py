"""Nonblocking process lock for a receipt root on Windows and POSIX.

No platform fallback: an unavailable lock implementation must prevent dispatch.
The file is retained permanently; unlinking it could split competing lock owners.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def operation_lock(path: Path) -> Iterator[bool]:
    # Import before opening anything so unsupported hosts leave no receipt files.
    if os.name == "nt":
        import msvcrt

        def acquire(handle):
            # locking() starts at the current offset and can lock beyond EOF.
            # Always lock byte zero, including when reopening an existing file.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

        def release(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    else:
        import fcntl

        def acquire(handle):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release(handle):
            fcntl.flock(handle, fcntl.LOCK_UN)

    with path.open("a+b") as handle:
        try:
            acquire(handle)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        try:
            yield True
        finally:
            release(handle)
