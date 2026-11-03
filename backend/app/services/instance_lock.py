"""One backend per runtime database.

Startup recovery adopts and releases every resource the database lists as owned, on the
assumption that the process that owned them is gone. A second backend on the same database
(for example on another port) would therefore stop the first backend's cameras. The lock is an
OS-level exclusive lock on ``<database>.lock``, taken before migrations and recovery and held for
the backend's lifetime. The OS drops it when the process exits, including a crash, so a stale
lock never blocks a replacement and PID reuse does not matter. Backends with separate databases
(isolated validators, benchmarks, tests) do not contend.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import IO


class InstanceLockError(RuntimeError):
    """Another live backend already owns this runtime database."""


class InstanceLock:
    def __init__(self, database_path: Path) -> None:
        self.path = Path(f"{Path(database_path).resolve()}.lock")
        self._handle: IO[bytes] | None = None

    def acquire(self, wait_seconds: float = 3.0) -> None:
        """Take the lock, retrying briefly: Windows releases a terminated process's locks asynchronously."""
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - held open for the backend's lifetime
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                _lock(handle)
                break
            except OSError as exc:
                if time.monotonic() < deadline:
                    time.sleep(0.1)
                    continue
                error = exc
                handle.close()
            raise InstanceLockError(
                f"Another backend is already running on database {self.path.with_suffix('')} "
                f"(lock {self.path} is held). Stop it first (scripts/a6000/stop-server.py), or give this "
                "instance its own CCTV_DATABASE_PATH."
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("ascii"))  # informational only; the OS lock is authoritative
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        except OSError:
            pass
        self._handle.close()
        self._handle = None


if os.name == "nt":
    import msvcrt

    def _lock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
