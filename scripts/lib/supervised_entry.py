"""Run one validator/benchmark script under the batch supervisor (scripts/run_supervised_batch.py).

usage: python scripts/lib/supervised_entry.py <script.py> [script args...]

A watcher thread interrupts the script's main thread (the script's own SIGINT handling, or
KeyboardInterrupt, so its finally/cleanup code runs) when the supervisor asks it to stop (stop
file) or when the supervisor process disappears (a killed wrapper must never leave the batch
running). If the script has not exited GRACE_SECONDS after that, the watcher kills this process
tree and exits. Standard library only: it also runs under the browser-test interpreter.
"""

from __future__ import annotations

import _thread
import ctypes
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import threading
import time

GRACE_SECONDS = float(os.environ.get("AEGIS_SUPERVISOR_GRACE_S", "240"))


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _descendants_posix(pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                ppid = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            children.setdefault(ppid, []).append(int(entry.name))
    found, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def kill_own_tree() -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(os.getpid()), "/T", "/F"], capture_output=True, check=False)
    else:
        for pid in _descendants_posix(os.getpid()):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    os._exit(2)


def supervisor_gone(supervisor_pid: int) -> bool:
    """POSIX: the supervisor is this process's direct parent, so a killed supervisor shows up as reparenting
    (a killed but unreaped parent is a zombie, which kill(pid, 0) still reports as alive). Windows: a venv
    launcher may sit in between, so the supervisor pid itself is checked."""
    if os.name != "nt":
        return os.getppid() != supervisor_pid
    return not pid_alive(supervisor_pid)


def _watch(stop_file: Path | None, supervisor_pid: int | None) -> None:
    reason = None
    while reason is None:
        time.sleep(0.5)
        if stop_file is not None and stop_file.exists():
            reason = "stop requested by supervisor"
        elif supervisor_pid is not None and supervisor_gone(supervisor_pid):
            reason = f"supervisor pid {supervisor_pid} disappeared"
    print(f"[supervised_entry] {reason}: interrupting the script for its own cleanup", file=sys.stderr, flush=True)
    _thread.interrupt_main(signal.SIGINT)
    time.sleep(GRACE_SECONDS)
    print(f"[supervised_entry] script still running {GRACE_SECONDS:.0f} s after the interrupt: killing its process tree",
          file=sys.stderr, flush=True)
    kill_own_tree()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: supervised_entry.py <script.py> [args...]")
    script = Path(sys.argv[1]).resolve()
    stop_file = Path(os.environ["AEGIS_STOP_FILE"]) if os.environ.get("AEGIS_STOP_FILE") else None
    supervisor_pid = int(os.environ["AEGIS_SUPERVISOR_PID"]) if os.environ.get("AEGIS_SUPERVISOR_PID") else None
    threading.Thread(target=_watch, args=(stop_file, supervisor_pid), daemon=True, name="supervised-entry-watch").start()
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
