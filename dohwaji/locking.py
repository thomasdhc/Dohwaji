"""Process-level locking for Dohwaji's state directory."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_CONFLICT_MESSAGE = (
    "Dohwaji state is already in use. Stop Dohwaji before archiving its state, "
    "then try again."
)


class StateLock:
    """Hold a nonblocking shared or exclusive lock on a state directory."""

    def __init__(self, state_root: Path, *, shared: bool = False) -> None:
        self.path = state_root.expanduser().resolve() / "runtime.lock"
        self.shared = shared
        self._file: BinaryIO | None = None

    def __enter__(self) -> StateLock:
        if self._file is not None:
            raise RuntimeError("StateLock is already held")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        try:
            if os.name == "nt":
                if lock_file.seek(0, os.SEEK_END) == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                mode = msvcrt.LK_NBRLCK if self.shared else msvcrt.LK_NBLCK
                msvcrt.locking(lock_file.fileno(), mode, 1)
            else:
                mode = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
                fcntl.flock(lock_file.fileno(), mode | fcntl.LOCK_NB)
        except OSError as error:
            lock_file.close()
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise ValueError(_CONFLICT_MESSAGE) from error
            raise

        self._file = lock_file
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._file is None:
            return

        lock_file = self._file
        self._file = None
        try:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
