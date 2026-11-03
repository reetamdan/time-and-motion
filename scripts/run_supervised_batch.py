"""Supervised sequential batch of long validators/benchmarks (one GPU workload at a time).

usage: .venv/Scripts/python.exe scripts/run_supervised_batch.py --batch <batch.json> [--label NAME]

batch.json: {"steps": [{"name": "ui_refactor", "python": ".install/browser-tests/venv/Scripts/python.exe",
                        "script": "scripts/validate_ui_refactor.py", "args": [], "stop_batch_on_failure": false,
                        "stop_batch_on_exit_codes": [2]}]}   # e.g. run_capacity exit 2 = interrupted

Guarantees:
- Every step runs through scripts/lib/supervised_entry.py, which interrupts the step for its own
  cleanup when the supervisor asks it to stop OR when the supervisor process disappears. A killed
  wrapper therefore never leaves the batch continuing; no further step is started by anyone.
- The supervisor stops (gracefully, then by process tree) when its own parent disappears, on
  SIGINT/SIGTERM/SIGBREAK, or when host memory falls below the batch floor; it then starts no
  further step and writes INTERRUPTED.json.
- After every step, containers created during that step (absent from the pre-step snapshot) that
  are still present are reported; those from the pinned DeepStream image are removed (single
  workload at a time, so they belong to that step). Nothing else is touched.
- status.json is rewritten after every event; memory samples (1 Hz) stream to memory.jsonl.
Exit: 0 all steps exited 0; 1 some step failed; 2 interrupted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT))
from backend.app.perception.service_maker import runtime_prefix  # noqa: E402
from evidence_dirs import utc_stamp  # noqa: E402

DISTRO = "Ubuntu-22.04"
FLOOR_INSTANT_MIB = 640
FLOOR_SUSTAINED_MIB = 1024
FLOOR_SUSTAINED_S = 15
STEP_STOP_WAIT_S = 300


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def available_mib() -> float:
    return psutil.virtual_memory().available / 2**20


def wrapper_pid() -> int:
    """The process that launched this supervisor. On Windows a venv python.exe is a launcher that runs
    the base interpreter as its child with the same arguments; skip such ancestors, or the watched
    "parent" would be the launcher, which outlives the real wrapper."""
    me = psutil.Process()
    own = me.cmdline()[1:]
    ancestor = me.parent()
    while ancestor is not None:
        try:
            if ancestor.cmdline()[1:] != own:
                return ancestor.pid
            ancestor = ancestor.parent()
        except psutil.Error:
            break
    return os.getppid()


def pinned_image() -> str:
    return (ROOT / "reports" / "environment" / "deepstream92" / "image-reference.txt").read_text(encoding="utf-8").strip()


def containers() -> dict[str, dict[str, str]] | None:
    try:
        out = subprocess.run([*runtime_prefix(DISTRO), "docker", "ps", "-a", "--no-trunc", "--format",
                              "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    rows = {}
    for line in out.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 4:
            rows[parts[0]] = {"name": parts[1], "image": parts[2], "status": parts[3]}
    return rows


class Batch:
    def __init__(self, spec: dict[str, Any], run_dir: Path, container_check: bool = True) -> None:
        self.spec, self.run_dir, self.container_check = spec, run_dir, container_check
        self.stop_file = run_dir / "STOP"
        self.parent_pid = wrapper_pid()
        self.stop_reason: str | None = None
        self.status: dict[str, Any] = {"started_utc": now(), "supervisor_pid": os.getpid(), "parent_pid": self.parent_pid,
                                       "policy": {"floor_instant_mib": FLOOR_INSTANT_MIB, "floor_sustained_mib": FLOOR_SUSTAINED_MIB,
                                                  "floor_sustained_s": FLOOR_SUSTAINED_S, "step_stop_wait_s": STEP_STOP_WAIT_S},
                                       "steps": [], "state": "running"}
        self.min_available = float("inf")
        self._monitor = threading.Thread(target=self._watch, daemon=True)
        self._done = threading.Event()

    def save(self) -> None:
        self.status["host_available_min_mib"] = round(self.min_available, 1)
        tmp = self.run_dir / "status.json.tmp"
        tmp.write_text(json.dumps(self.status, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.run_dir / "status.json")

    def wrapper_gone(self) -> bool:
        """POSIX: a killed wrapper reparents this process (and may linger as an unreaped zombie, which
        pid_exists still reports). Windows: the wrapper pid (venv launcher skipped) must still be running."""
        if os.name != "nt" and os.getppid() != self.parent_pid:
            return True
        try:
            return psutil.Process(self.parent_pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
        except psutil.Error:
            return False

    def request_stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
            self.stop_file.write_text(reason + "\n", encoding="utf-8")
            print(f"[supervisor] STOP: {reason}", flush=True)

    def _watch(self) -> None:
        low_since = None
        with (self.run_dir / "memory.jsonl").open("a", encoding="utf-8") as samples:
            while not self._done.is_set():
                available = available_mib()
                self.min_available = min(self.min_available, available)
                samples.write(json.dumps({"t": time.time(), "available_mib": round(available, 1),
                                          "commit_percent": psutil.swap_memory().percent}) + "\n")
                samples.flush()
                if self.wrapper_gone():
                    self.request_stop(f"supervisor parent pid {self.parent_pid} disappeared (wrapper killed)")
                if available < FLOOR_INSTANT_MIB:
                    self.request_stop(f"host available memory {available:.0f} MiB < {FLOOR_INSTANT_MIB} MiB")
                if available < FLOOR_SUSTAINED_MIB:
                    low_since = low_since or time.monotonic()
                    if time.monotonic() - low_since >= FLOOR_SUSTAINED_S:
                        self.request_stop(f"host available memory < {FLOOR_SUSTAINED_MIB} MiB for {FLOOR_SUSTAINED_S} s")
                else:
                    low_since = None
                self._done.wait(1)

    def run_step(self, step: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = {"name": step["name"], "started_utc": now(), "available_at_start_mib": round(available_mib(), 1)}
        self.status["steps"].append(record)
        self.save()
        before = containers() if self.container_check else None
        log = self.run_dir / f"{len(self.status['steps']):02d}-{step['name']}.log"
        python = str(ROOT / step.get("python", ".venv/Scripts/python.exe" if os.name == "nt" else ".venv-wsl/bin/python"))
        command = [python, str(ROOT / "scripts" / "lib" / "supervised_entry.py"), str(ROOT / step["script"]), *step.get("args", [])]
        record["command"] = command
        env = dict(os.environ, AEGIS_STOP_FILE=str(self.stop_file), AEGIS_SUPERVISOR_PID=str(os.getpid()), PYTHONUNBUFFERED="1")
        with log.open("w", encoding="utf-8") as handle:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT)
            record["pid"] = process.pid
            self.save()
            stop_seen_at = None
            while process.poll() is None:
                if self.stop_reason and stop_seen_at is None:
                    stop_seen_at = time.monotonic()
                if stop_seen_at is not None and time.monotonic() - stop_seen_at > STEP_STOP_WAIT_S:
                    record["killed_after_stop_wait"] = True
                    self._kill_tree(process.pid)
                    break
                time.sleep(1)
            try:
                record["exit_code"] = process.wait(30)
            except subprocess.TimeoutExpired:
                self._kill_tree(process.pid)
                record["exit_code"] = process.wait(30)
        record["ended_utc"] = now()
        text = log.read_text(encoding="utf-8", errors="replace")
        dirs = [part.split('"')[0] for part in text.split('"run_dir": "')[1:]]
        record["run_dir"] = dirs[-1].replace("\\\\", "\\") if dirs else None
        after = containers() if self.container_check else None
        if not self.container_check:
            record["leftover_containers"] = "NOT CHECKED (--no-container-check; tests only)"
        elif before is None or after is None:
            record["leftover_containers"] = "UNKNOWN (docker ps failed)"
        else:
            leftover = {cid: row for cid, row in after.items() if cid not in before}
            removed = []
            for cid, row in leftover.items():
                if row["image"] == pinned_image():
                    subprocess.run([*runtime_prefix(DISTRO), "docker", "rm", "-f", cid], capture_output=True, timeout=120, check=False)
                    removed.append(row["name"])
            record["leftover_containers"] = [row["name"] for row in leftover.values()]
            record["removed_containers"] = removed
            record["preexisting_containers_untouched"] = [row["name"] for row in before.values()]
        record["interrupted_by_supervisor"] = self.stop_reason
        self.save()
        print(f"[supervisor] {step['name']} exit={record['exit_code']} run_dir={record['run_dir']} "
              f"leftover={record['leftover_containers']}", flush=True)
        return record

    @staticmethod
    def _kill_tree(pid: int) -> None:
        try:
            parent = psutil.Process(pid)
            procs = [*parent.children(recursive=True), parent]
        except psutil.Error:
            return
        for proc in procs:
            try:
                proc.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=15)

    def run(self) -> int:
        self._monitor.start()
        failed = False
        try:
            for step in self.spec["steps"]:
                if self.stop_reason:
                    break
                record = self.run_step(step)
                if record["exit_code"] != 0:
                    failed = True
                    if step.get("stop_batch_on_failure") or record["exit_code"] in step.get("stop_batch_on_exit_codes", []):
                        self.request_stop(f"step {step['name']} exited {record['exit_code']} (batch stop rule)")
        finally:
            self._done.set()
            self._monitor.join(5)
            self.status["ended_utc"] = now()
            self.status["state"] = "INTERRUPTED" if self.stop_reason else ("COMPLETE_WITH_FAILURES" if failed else "COMPLETE")
            self.status["stop_reason"] = self.stop_reason
            self.status["not_started"] = [s["name"] for s in self.spec["steps"][len(self.status["steps"]):]]
            self.save()
            if self.stop_reason:
                (self.run_dir / "INTERRUPTED.json").write_text(json.dumps(
                    {"status": "INTERRUPTED - incomplete batch, not an acceptance result", "reason": self.stop_reason,
                     "completed_or_stopped_steps": [s["name"] for s in self.status["steps"]],
                     "not_started": self.status["not_started"], "at_utc": now()}, indent=2) + "\n", encoding="utf-8")
        return 2 if self.stop_reason else (1 if failed else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--label", default="batch")
    parser.add_argument("--no-container-check", action="store_true", help="tests only: skip the docker leftover check")
    parser.add_argument("--run-dir", type=Path, help="new batch directory (must not exist)")
    args = parser.parse_args()
    spec = json.loads(args.batch.read_text(encoding="utf-8"))
    run_dir = args.run_dir or ROOT / "reports" / "batches" / f"{args.label}-{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "batch.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    batch = Batch(spec, run_dir, container_check=not args.no_container_check)
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, lambda signum, _f: batch.request_stop(f"supervisor received signal {signum}"))
    print(json.dumps({"batch_dir": str(run_dir), "supervisor_pid": os.getpid()}), flush=True)
    return batch.run()


if __name__ == "__main__":
    raise SystemExit(main())
