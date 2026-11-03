"""One backend per runtime database: a second backend refuses before migrations or recovery."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.services.instance_lock import InstanceLock, InstanceLockError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

HOLD = """
import sys, time
sys.path.insert(0, sys.argv[2])
from backend.app.services.instance_lock import InstanceLock
lock = InstanceLock(sys.argv[1]); lock.acquire()
print("held", flush=True)
time.sleep(60)
"""


def _holder(database: Path) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", HOLD, str(database), str(PROJECT_ROOT)],
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None and process.stdout.readline().strip() == "held"
    return process


def test_second_owner_of_the_same_database_is_refused_and_a_crashed_owner_frees_it(tmp_path: Path) -> None:
    database = tmp_path / "cctv.db"
    holder = _holder(database)
    try:
        with pytest.raises(InstanceLockError, match="already running"):
            InstanceLock(database).acquire()
    finally:
        holder.kill()  # no clean release: the OS drops the lock with the process
        holder.wait(10)
    replacement = InstanceLock(database)
    replacement.acquire()
    replacement.release()


def test_separate_databases_do_not_contend(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path / "a.db"), InstanceLock(tmp_path / "b.db")
    first.acquire()
    second.acquire()
    first.release()
    second.release()


def test_two_handles_in_one_process_contend(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "cctv.db")
    first.acquire()
    try:
        with pytest.raises(InstanceLockError):
            InstanceLock(tmp_path / "cctv.db").acquire()
    finally:
        first.release()
    InstanceLock(tmp_path / "cctv.db").acquire()


def test_refused_backend_never_touches_the_database(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    database = tmp_path / "cctv.db"
    holder = _holder(database)
    try:
        application = create_app(database_path=database, evidence_root=tmp_path / "evidence")
        with pytest.raises(InstanceLockError):
            with TestClient(application):
                pass
        assert not database.exists()  # no migration, seeding or recovery ran
    finally:
        holder.kill()
        holder.wait(10)
